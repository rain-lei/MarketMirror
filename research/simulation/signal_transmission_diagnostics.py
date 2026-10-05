"""Descriptive signal-to-book diagnostics; accepted-book probes are not paths."""
from collections import Counter
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import math
import statistics

from .audit_background import interval_price
from .audit_public_information_risk import verify_receipt
from .audit_temporal_statistics import correlation

STAGES = (
    "generated_market_before_cap_bps", "mean_received_market_before_cap_bps",
    "mean_received_total_after_cap_bps", "mean_strategy_belief_effect_bps",
    "mean_emitted_market_quote_effect_bps", "mean_accepted_market_quote_effect_bps",
    "mean_filled_market_quote_effect_bps", "mean_emitted_total_quote_effect_bps", "actual_return_bps",
)


def require(value, message):
    if not value:
        raise ValueError(message)


def parts(row, receipt, parameters):
    """An unreceived private component is a known absence, not a missing target."""
    detail = receipt.get("public_information_receipt")
    got_private = detail["private_received"] if detail else receipt["received"]
    private = row["risk_budget"]["residual_raw_shift_bps"] if got_private else 0.0
    market = (row["public_information_budget"]["public_market_multiplier"] * row["risk_budget"]["market_raw_shift_bps"]
        if detail else row["risk_budget"]["market_raw_shift_bps"] if got_private else 0.0)
    cap = parameters["max_shift_bps"]
    private_only = max(-cap, min(cap, private))
    require(math.isclose(receipt["applied_valuation_shift_bps"], max(-cap, min(cap, private + market)), rel_tol=1e-12, abs_tol=1e-12),
        "received components do not reconstruct the applied signal")
    return private, market, private_only


def quote(price, side, base_bps, shift_bps, spread_bps, tick, bounds):
    require(side in ("buy", "sell") and tick > 0, "explicit physical quote side and tick required")
    units = Decimal(price) * (1 + (Decimal(str(base_bps)) + Decimal(str(shift_bps))) / 10000
        + Decimal((-1 if side == "buy" else 1) * spread_bps) / 20000) / tick
    result = int(units.to_integral_value(rounding=ROUND_FLOOR if side == "buy" else ROUND_CEILING)) * tick
    return min(bounds[1], max(bounds[0], result))


def diagnose(day, stock, specs, row, parameters, venue, background, previous):
    call = day["portfolio_auction"]["asset_calls"][stock]
    prior, bounds = call["price_before_minor"], call["price_bounds_minor"]
    orders = {r["owner"]: r for r in call["orders"]}
    require(len(orders) == len(call["orders"]), "duplicate book owner")
    owners = [n for n, p in specs.items() if p["kind"] == "strategy" or p["asset"] == stock]
    strategies = [n for n in owners if specs[n]["kind"] == "strategy"]
    require(len(owners) == 24 and len(strategies) == 12 and set(orders) <= set(owners), "complete frozen stock-owner scope required")
    counts, roles, terms, changes = Counter(), {r: Counter() for r in ("aggressive", "conservative", "institutional", "background")}, {k: [] for k in STAGES}, {}
    for name in owners:
        spec = specs[name]
        role = spec["parameters"]["role"] if spec["kind"] == "strategy" else "background"
        counters = roles[role]
        receipt = day["issuer_information_receipts"][name][stock]
        verify_receipt(receipt, row, parameters, stock, name)
        private, market, private_only = parts(row, receipt, parameters)
        applied = receipt["applied_valuation_shift_bps"]
        detail = receipt.get("public_information_receipt")
        got_private = detail["private_received"] if detail else receipt["received"]
        for c in (counts, counters):
            c["owner_positions"] += 1
            c["private_received"] += int(got_private)
            c["nonzero_market_component_received"] += int(market != 0)
            c["nonzero_applied_total_received"] += int(applied != 0)
            c["received_total_capped"] += int(private + market != applied)
        terms["mean_received_market_before_cap_bps"].append(market / 24)
        terms["mean_received_total_after_cap_bps"].append(applied / 24)
        order = orders.get(name)
        if role != "background":
            d, p = day["decisions"][name], spec["parameters"]
            delta = d["order_weight_changes"][stock]
            raw_lots = abs(delta) * d["nav_minor"] / (prior * venue["lot_size"])
            quantity = math.floor(raw_lots) * venue["lot_size"]
            if delta < 0:
                quantity = min(quantity, previous[name]["shares"][stock])
            require((order["quantity"] if order else 0) == quantity, "submitted strategy quantity differs from factual target and whole lots")
            require(not order or order["side"] == ("buy" if delta > 0 else "sell"), "strategy side differs from actual target")
            terms["mean_strategy_belief_effect_bps"].append(d["issuer_belief_contributions"][stock] * parameters["belief_scale_bps"] / 12)
            for c in (counts, counters):
                c["strategy_positions"] += 1
                c["strategy_zero_delta"] += int(delta == 0)
                c["strategy_nonzero_delta_no_order"] += int(delta != 0 and quantity == 0)
                c["strategy_sub_lot_delta"] += int(0 < raw_lots < 1)
                c["strategy_wait_positions"] += int("confirmation_or_rebalance_wait" in d["reasons"])
                c["strategy_minimum_trade_positions"] += int("minimum_portfolio_trade" in d["reasons"])
                c["strategy_risk_liquidation_positions"] += int(d["risk_liquidation"])
            if not order:
                continue
            base = Decimal(str(d.get("quote_base_beliefs", d["issuer_base_beliefs"])[stock])) * venue["quote_response_bps"]
            require(math.isclose(d["issuer_quote_shift_bps"][stock], p["market_sensitivity"] * applied, rel_tol=1e-12, abs_tol=1e-12), "issuer quote coefficient differs")
            q = quote(prior, order["side"], base, d["issuer_quote_shift_bps"][stock], venue["quote_spread_bps"], venue["tick_minor"], bounds)
            no_market = quote(prior, order["side"], base, p["market_sensitivity"] * private_only, venue["quote_spread_bps"], venue["tick_minor"], bounds)
            no_issuer = quote(prior, order["side"], base, 0, venue["quote_spread_bps"], venue["tick_minor"], bounds)
        else:
            demand = day["background_demands"][stock][name]
            require((order["quantity"] if order else 0) == demand["requested_quantity"], "background factual demand differs")
            for c in (counts, counters):
                c["background_positions"] += 1
            if not order:
                continue
            require(order["side"] == demand["side"], "background side differs")
            base_quote = demand["issuer_valuation_response"]["base_limit_price_minor"]
            untouched = not receipt["received"] or (detail and not got_private and market == 0)
            q = base_quote if untouched else quote(prior, order["side"], 0, applied, -2 * background["urgency_bps"], venue["tick_minor"], bounds)
            no_market = (quote(prior, order["side"], 0, private_only, -2 * background["urgency_bps"], venue["tick_minor"], bounds)
                if got_private else base_quote)
            no_issuer = base_quote
        require(q == order["limit_price_minor"], "reconstructed physical quote differs")
        changes[name] = (no_market, no_issuer)
        market_effect = (q - no_market) * 10000 / prior
        total_effect = (q - no_issuer) * 10000 / prior
        terms["mean_emitted_market_quote_effect_bps"].append(market_effect / 24)
        terms["mean_accepted_market_quote_effect_bps"].append(market_effect * order["accepted_quantity"] / order["quantity"] / 24)
        terms["mean_filled_market_quote_effect_bps"].append(market_effect * order["filled_quantity"] / order["quantity"] / 24)
        terms["mean_emitted_total_quote_effect_bps"].append(total_effect / 24)
        for c in (counts, counters):
            c["submitted_orders"] += 1
            c["submitted_orders_market_quote_changed"] += int(q != no_market)
            c["submitted_orders_total_quote_changed"] += int(q != no_issuer)
            c["accepted_orders"] += int(order["accepted_quantity"] > 0)
            c["filled_orders"] += int(order["filled_quantity"] > 0)
            for stage, field in (("requested", "quantity"), ("accepted", "accepted_quantity"), ("filled", "filled_quantity")):
                c[stage + "_" + order["side"]] += order[field]
            c["cash_clipped_quantity"] += (order["quantity"] - order["accepted_quantity"]) if "cash_and_fee_reservation" in order["reasons"] else 0
            c["inventory_clipped_quantity"] += (order["quantity"] - order["accepted_quantity"]) if "sellable_inventory" in order["reasons"] else 0
    stages = {k: math.fsum(v) for k, v in terms.items()}
    stages["generated_market_before_cap_bps"] = row["risk_budget"]["market_raw_shift_bps"]
    stages["actual_return_bps"] = (call["price_after_minor"] / prior - 1) * 10000
    factual = interval_price(call, venue)
    require(factual == (call["price_after_minor"], call["matched_volume"], call["clearing_imbalance"]), "original accepted book does not reconstruct")
    probes = {}
    for index, label in enumerate(("remove_market_quote_component", "remove_all_issuer_quote_component")):
        changed = {**call, "orders": [{**o, "limit_price_minor": changes[o["owner"]][index]} for o in call["orders"]]}
        price, volume, imbalance = interval_price(changed, venue)
        probes[label] = {"price_minor": price, "matched_volume": volume, "imbalance": imbalance}
        counts[label + "_same_price"] += int(price == factual[0])
        counts[label + "_same_volume"] += int(volume == factual[1])
    counts["books"] += 1
    counts["traded_flat_books"] += int(call["matched_volume"] > 0 and call["price_after_minor"] == prior)
    counts["no_trade_books"] += int(call["matched_volume"] == 0)
    return {"stages": {k: stages[k] for k in STAGES}, "counts": dict(counts), "roles": {k: dict(v) for k, v in roles.items()}, "accepted_book_probes": probes}


def panel_statistics(rows, mask):
    """Same stocks, pair dates and common dates for every signal stage."""
    panel = {(r["stock_code"], r["trade_date"]): r for r in rows}
    require(len(panel) == len(rows) == len(mask["stocks"]) * len(mask["dates"])
        and set(panel) == {(s, d) for s in mask["stocks"] for d in mask["dates"]}, "entire stage panel required")
    output = {}
    pairs = [p for p in mask["correlation_pairs"] if p["primary_eligible"]]
    for stage in STAGES:
        values = [r["stages"][stage] for r in rows]
        require(all(type(v) in (int, float) and math.isfinite(v) for v in values), "all stages require known finite model observables")
        correlations, undefined = [], []
        for p in pairs:
            ds, a, b = p["known_dates"], p["left"], p["right"]
            value = correlation([panel[a, d]["stages"][stage] for d in ds], [panel[b, d]["stages"][stage] for d in ds])
            if value is None:
                undefined.append([a, b])
            else:
                correlations.append(value)
        common = [statistics.fmean(panel[s, d]["stages"][stage] for s in mask["stocks"])
            for d in mask["full_cohort_portfolio_common_dates"]]
        output[stage] = {"pooled_mean_bps": statistics.fmean(values), "pooled_std_bps": statistics.pstdev(values),
            "zero_fraction": sum(v == 0 for v in values) / len(values),
            "mean_stock_correlation": statistics.fmean(correlations) if len(correlations) == len(pairs) and pairs else None,
            "fixed_primary_pairs": len(pairs), "defined_primary_pairs": len(correlations), "undefined_primary_pairs": undefined,
            "common_sessions": len(common), "equal_weight_common_std_bps": statistics.pstdev(common) if len(common) >= 2 else None}
    return output
