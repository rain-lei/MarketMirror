"""Independent rational audits of lagged risk, private beliefs and physical quotes."""

from __future__ import annotations

import hashlib
import json
import math
from fractions import Fraction

from .audit_background_response import expected_demand


def verify_risk_panel(risk: dict, groups: dict, joined: dict, window: int) -> dict:
    checks = 0
    for stock, rows in risk.items():
        source = {row.trade_date.isoformat(): row.stock_return for row in groups[stock]}
        calendar = sorted(source)
        if len(source) != len(groups[stock]) or len(rows) != len(joined[stock]):
            raise ValueError("independent issuer risk coverage differs")
        for row, step in zip(rows, joined[stock], strict=True):
            target = calendar.index(step["trade_date"])
            if target < window + 1 or calendar[target - 2] != step["signal_cutoff_date"]:
                raise ValueError("issuer risk cutoff is not the frozen t-2 market date")
            dates = calendar[target - window - 1:target - 1]
            history = [{"trade_date": day, "stock_return": source[day]} for day in dates]
            digest = hashlib.sha256(json.dumps(history, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
            values = [Fraction(str(source[day])) for day in dates]
            mean = sum(values) / len(values)
            sigma = math.sqrt(float(sum((value - mean) ** 2 for value in values) / (len(values) - 1)))
            if (set(row) != {"trade_date", "signal_cutoff_date", "history", "history_sha256", "lagged_stock_volatility"}
                    or row["trade_date"] != step["trade_date"] or row["signal_cutoff_date"] != dates[-1]
                    or row["history"] != history or row["history_sha256"] != digest
                    or not math.isclose(row["lagged_stock_volatility"], sigma, rel_tol=0.0, abs_tol=1e-14)):
                raise ValueError("issuer lagged feature differs from independent source/rational variance")
            checks += 1
    return {"independent_t_minus_two_risk_rows": checks, "history_observations_per_row": window,
            "target_returns_excluded_from_risk_contract": True}


def expected_receipt(row: dict, parameters: dict, stock: str, name: str) -> dict:
    digest = hashlib.sha256(f"issuer-information-v1:{parameters['information_seed']}:{stock}:{row['trade_date']}:{name}".encode()).hexdigest()
    received = int(digest[:16], 16) * 10000 < parameters["information_probability_bps"] * 2**64
    shift = row["valuation_shift_bps"] if received else 0.0
    return {"received": received, "receipt_sha256": digest,
            "information_probability_bps": parameters["information_probability_bps"],
            "valuation_shift_bps": shift if received else None,
            "applied_valuation_shift_bps": shift, "applied_belief_signal": shift / parameters["belief_scale_bps"]}


def verify_message_design(cells: list[dict], risk: dict, config: dict) -> dict:
    if [cell["name"] for cell in cells] != [cell["name"] for cell in config["variants"]] or cells[0]["issuer"] is not None:
        raise ValueError("issuer sensitivity grid coverage differs")
    checks, capped = 0, {}
    for cell, declaration in zip(cells[1:], config["variants"][1:], strict=True):
        parameters = {**config["parameters"], "risk_multiplier": declaration["risk_multiplier"],
                      "information_probability_bps": declaration["information_probability_bps"]}
        path = cell["issuer"]
        if path["parameters"] != parameters or path["scenario_id"] != cell["name"] or set(path["by_stock"]) != set(risk):
            raise ValueError("issuer scenario parameters or coverage differ")
        clipped = 0
        for stock, features in risk.items():
            for feature, row in zip(features, path["by_stock"][stock], strict=True):
                digest = hashlib.sha256(f"issuer-innovation-v1:{parameters['innovation_seed']}:{stock}:{feature['trade_date']}".encode()).hexdigest()
                innovation = math.sqrt(3) * (2 * ((int(digest[:16], 16) + 0.5) / 2**64) - 1)
                raw = innovation * parameters["risk_multiplier"] * feature["lagged_stock_volatility"] * 10000
                shift = min(parameters["max_shift_bps"], max(-parameters["max_shift_bps"], raw))
                expected = {**feature, "innovation_sha256": digest, "standardized_innovation": innovation,
                            "raw_shift_bps": raw, "valuation_shift_bps": shift, "was_capped": shift != raw}
                if row != expected:
                    raise ValueError("issuer message differs from independent fixed-seed reconstruction")
                clipped += int(shift != raw)
                checks += 1
        capped[cell["name"]] = clipped
    # Strength and coverage never enter the innovation hash. Scenario identity
    # and risk multiplier never enter the receipt hash.
    for stock, features in risk.items():
        for index, feature in enumerate(features):
            rows = [cell["issuer"]["by_stock"][stock][index] for cell in cells[1:]]
            if len({row["innovation_sha256"] for row in rows}) != 1:
                raise ValueError("paired issuer scenarios received different innovations")
            for name in ("aggressive_000", f"background_{stock}_000"):
                halves = [expected_receipt(row, cell["issuer"]["parameters"], stock, name)
                          for row, cell in zip(rows, cells[1:], strict=True)
                          if cell["issuer"]["parameters"]["information_probability_bps"] == 5000]
                if len({(receipt["received"], receipt["receipt_sha256"]) for receipt in halves}) != 1:
                    raise ValueError("paired receipt masks changed with message strength")
    return {"independent_issuer_message_rows": checks, "capped_company_days": capped,
            "innovation_and_receipt_draws_coupled_across_scenarios": True}


def verify_issuer_day(day: dict, previous: dict, specs: dict, venue: dict, background: dict,
                      response: dict | None, shocks: dict, industry: dict | None, issuer: dict, session: int) -> dict:
    counts = {"strategy_receipt_checks": 0, "background_receipt_checks": 0,
              "strategy_received": 0, "background_received": 0, "background_demand_checks": 0}
    assets, parameters = sorted(day["observations"]), issuer["parameters"]
    expected_all = {}
    for name, spec in specs.items():
        stocks = assets if spec["kind"] == "strategy" else [spec["asset"]]
        expected_all[name] = {stock: expected_receipt(issuer["by_stock"][stock][session], parameters, stock, name) for stock in stocks}
        kind = spec["kind"]
        counts[f"{kind}_receipt_checks"] += len(stocks)
        counts[f"{kind}_received"] += sum(receipt["received"] for receipt in expected_all[name].values())
    if day.get("issuer_information_receipts") != expected_all:
        raise ValueError("issuer receipts differ from independent private information masks")
    for stock in assets:
        observation = day["observations"][stock]
        shared_industry = industry["by_group"][industry["path_groups"][stock]][session] if industry else 0.0
        common, legacy_issuer = shocks["common"][session], shocks["asset_specific"][stock][session]
        if (observation.get("common_shock") != common or observation.get("issuer_specific_shock") != legacy_issuer
                or observation.get("scenario_shock") != common + legacy_issuer + shared_industry
                or observation["text_signal"] != 0.0 or observation["text_uncertainty"] != 0.0
                or observation["text_evidence"] != "text:disabled" or any(key.startswith("private_") or key.startswith("issuer_valuation") for key in observation)):
            raise ValueError("shared observation leaked a private issuer value or differs from declared inputs")
        if industry:
            group = industry["path_groups"][stock]
            expected_industry = {"industry_scenario_id": industry["scenario_id"], "industry_assignment_mode": industry["assignment_mode"],
                                 "industry_code": industry["membership"][stock], "industry_path_group": group,
                                 "industry_shock": shared_industry,
                                 "industry_evidence": f"industry-scenario:{industry['scenario_id']}:{group}:{session}"}
            if any(observation.get(key) != value for key, value in expected_industry.items()):
                raise ValueError("issuer experiment altered official industry delivery")
        call = day["portfolio_auction"]["asset_calls"][stock]
        orders = {order["owner"]: order for order in call["orders"]}
        price, tick, lot = previous["prices"][stock], venue["tick_minor"], venue["lot_size"]
        lower, upper = call["price_bounds_minor"]
        for name, spec in specs.items():
            if spec["kind"] != "strategy":
                continue
            receipt = expected_all[name][stock]
            profile, p = spec["profile"], spec["parameters"]
            base_signal = observation["market_signal"] * profile["momentum_loading"] + profile["market_bias"] + observation["scenario_shock"]
            private_signal = observation["market_signal"] * profile["momentum_loading"] + profile["market_bias"] + (observation["scenario_shock"] + receipt["applied_belief_signal"])
            base_belief = p["market_sensitivity"] * min(1.0, max(-1.0, base_signal))
            belief = p["market_sensitivity"] * min(1.0, max(-1.0, private_signal))
            shift = p["market_sensitivity"] * receipt["applied_valuation_shift_bps"]
            decision = day["decisions"][name]
            for field, expected in (("beliefs", belief), ("issuer_base_beliefs", base_belief),
                                    ("issuer_belief_contributions", belief - base_belief), ("issuer_quote_shift_bps", shift)):
                if not math.isclose(decision[field][stock], expected, rel_tol=0.0, abs_tol=1e-12):
                    raise ValueError("private issuer belief or physical quote contribution differs")
            delta = decision["order_weight_changes"][stock]
            quantity = math.floor(abs(delta) * decision["nav_minor"] / (price * lot)) * lot
            if delta < 0:
                quantity = min(quantity, previous["accounts"][name]["shares"][stock])
            if not quantity:
                if name in orders:
                    raise ValueError("zero issuer strategy demand submitted an order")
                continue
            side = "buy" if delta > 0 else "sell"
            sign = -1 if side == "buy" else 1
            units = Fraction(price, tick) * (1 + (Fraction(str(base_belief)) * venue["quote_response_bps"] + Fraction(str(shift))) / 10000
                                             + Fraction(sign * venue["quote_spread_bps"], 20000))
            rounded = units.numerator // units.denominator if side == "buy" else -(-units.numerator // units.denominator)
            quote = max(lower, min(upper, rounded * tick))
            order = orders.get(name)
            if order is None or (order["side"], order["quantity"], order["limit_price_minor"]) != (side, quantity, quote):
                raise ValueError("issuer strategy order differs from independent rational quote")
        names = {name for name, spec in specs.items() if spec["kind"] == "background" and spec["asset"] == stock}
        if set(day["background_demands"][stock]) != names:
            raise ValueError("issuer background demand coverage differs")
        for name in names:
            receipt = expected_all[name][stock]
            shared = common + shared_industry
            expected = expected_demand(stock, session, name, previous["accounts"][name]["shares"][stock], price,
                                       call["price_bounds_minor"], venue, background, shared if response else 0.0,
                                       response or {"valuation_response_bps": 0, "pulse_participation_bps": 10000})
            if industry and "common_news_response" in expected:
                detail = expected.pop("common_news_response")
                detail["shared_shock"] = detail.pop("common_shock")
                detail.update(common_shock=common, industry_shock=shared_industry,
                              industry_code=industry["membership"][stock], industry_path_group=industry["path_groups"][stock])
                expected["shared_news_response"] = detail
            expected["issuer_valuation_response"] = {"receipt": receipt, "base_limit_price_minor": expected["limit_price_minor"]}
            if receipt["received"] and expected["requested_quantity"]:
                shift = Fraction(str(shared)) * response["valuation_response_bps"] if response else Fraction(0)
                shift += Fraction(str(receipt["applied_valuation_shift_bps"]))
                side = expected["side"]
                urgency = background["urgency_bps"] if side == "buy" else -background["urgency_bps"]
                units = Fraction(price, tick) * (1 + (shift + urgency) / 10000)
                rounded = units.numerator // units.denominator if side == "buy" else -(-units.numerator // units.denominator)
                expected["limit_price_minor"] = max(lower, min(upper, rounded * tick))
            if day["background_demands"][stock][name] != expected:
                raise ValueError("issuer background demand differs from independent rational reconstruction")
            if expected["requested_quantity"]:
                order = orders.get(name)
                if order is None or any(order[field] != expected[key] for field, key in (
                        ("side", "side"), ("quantity", "requested_quantity"), ("limit_price_minor", "limit_price_minor"))):
                    raise ValueError("issuer background submitted order differs")
            elif name in orders:
                raise ValueError("zero issuer background demand submitted an order")
            counts["background_demand_checks"] += 1
    return counts
