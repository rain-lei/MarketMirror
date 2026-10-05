"""Paired factorial changes; no parameter selection or empirical causal claim."""
from __future__ import annotations

FIELDS = ("mean", "volatility", "zero_fraction", "stock_correlation", "portfolio_volatility")


def metric_values(row):
    own = row["comparison"]["synthetic"]
    common = row["common_factor_metrics"]["synthetic"]
    return {"mean": own["mean"], "volatility": own["standard_deviation"],
            "zero_fraction": own["zero_return_fraction"],
            "stock_correlation": common["mean_pairwise_stock_return_correlation"],
            "portfolio_volatility": common["equal_weight_daily_return_std"]}


def factorial_summary(variants):
    index = {(row["seed_id"], row["background_anchor"], row["target_scale"], row["quote_scale"]): row for row in variants}
    seeds = list(dict.fromkeys(row["seed_id"] for row in variants))
    anchors = list(dict.fromkeys(row["background_anchor"] for row in variants))
    grid = {(seed, anchor, target, quote) for seed in seeds for anchor in anchors
            for target in (1.0, 0.0) for quote in (1.0, 0.0)}
    if len(variants) != len(grid) or set(index) != grid:
        raise ValueError("factorial summary requires the complete unique channel grid")
    result = {}
    for anchor in anchors:
        paired = []
        for seed in seeds:
            base, target, quote, both = [metric_values(index[seed, anchor, t, q])
                                        for t, q in ((1.0, 1.0), (0.0, 1.0), (1.0, 0.0), (0.0, 0.0))]
            changes = {"target_off_minus_baseline": {}, "quote_off_minus_baseline": {},
                       "both_off_minus_baseline": {}, "interaction": {}}
            for field in FIELDS:
                values = [row[field] for row in (base, target, quote, both)]
                if any(value is None for value in values):
                    for row in changes.values():
                        row[field] = None
                    continue
                changes["target_off_minus_baseline"][field] = target[field] - base[field]
                changes["quote_off_minus_baseline"][field] = quote[field] - base[field]
                changes["both_off_minus_baseline"][field] = both[field] - base[field]
                changes["interaction"][field] = both[field] - target[field] - quote[field] + base[field]
            paired.append({"seed_id": seed, **changes})
        result[anchor] = paired
    return result


def expected_coverage(seeds, cells, baskets, sessions, strategies, assets, backgrounds):
    for value in (seeds, baskets, sessions, strategies, assets, backgrounds):
        if type(value) is not int or value <= 0:
            raise ValueError("channel coverage dimensions must be positive integers")
    expected_cells = {(anchor, target, quote) for anchor in ("initial_inventory", "current_inventory")
                      for target in (1.0, 0.0) for quote in (1.0, 0.0)}
    if (len(cells) != 8 or len({row["name"] for row in cells}) != 8
            or any(set(row) != {"name", "background_anchor", "target_scale", "quote_scale"} for row in cells)
            or {(row["background_anchor"], row["target_scale"], row["quote_scale"]) for row in cells} != expected_cells
            or any(type(row[key]) not in (int, float) for row in cells for key in ("target_scale", "quote_scale"))):
        raise ValueError("channel coverage requires both anchors and the full binary factorial")
    days = seeds * 2 * baskets * sessions
    return {"paths": seeds * 8 * baskets, "full_path_ledger_records": seeds * 8 * baskets * sessions,
            "legacy_diagonal_daily_records_matched": days * 2,
            "legacy_same_state_records_matched": days,
            "same_state_records": days * 3,
            "same_state_strategy_allocations_audited": days * 4 * strategies,
            "same_state_original_decision_order_matches": days * strategies,
            "quote_only_quantity_identity_checks": days * strategies,
            "target_only_quote_base_identity_checks": days * strategies * assets,
            "paired_private_receipt_values": seeds * 7 * baskets * sessions * (strategies * assets + backgrounds * assets)}
