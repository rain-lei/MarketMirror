"""Complete matched allocation effects and allocation-by-anchor interactions."""
from .public_information_study import CELLS
from .public_factor_metrics import values, difference


def gaps(row):
    v = row["joint_checks"]["values"]
    return {"mean": v["absolute_mean_gap"],
        "volatility": None if v["volatility_ratio"] is None else abs(v["volatility_ratio"] - 1),
        "zero_fraction": v["zero_fraction_gap"], "stock_correlation": v["stock_correlation_gap"],
        "portfolio_volatility": None if v["portfolio_volatility_ratio"] is None else abs(v["portfolio_volatility_ratio"] - 1)}


def paired_effects(candidate, baseline, seeds):
    if len(seeds) != 5 or len({s["seed_id"] for s in seeds}) != 5:
        raise ValueError("five distinct original seeds required")
    expected = {(s["seed_id"], c["name"]) for s in seeds for c in CELLS}
    indexes = []
    for rows in (candidate, baseline):
        index = {(r["seed_id"], r["name"]): r for r in rows}
        if len(rows) != 80 or set(index) != expected:
            raise ValueError("all eighty candidate and baseline conditions required")
        for seed in seeds:
            for cell in CELLS:
                row = index[seed["seed_id"], cell["name"]]
                if any(row[k] != v for k, v in cell.items()):
                    raise ValueError("condition metadata differs from original design")
        indexes.append(index)
    treated, control = indexes
    paired, lookup = [], {}
    for seed in seeds:
        for cell in CELLS:
            key = seed["seed_id"], cell["name"]
            delta = difference(gaps(treated[key]), gaps(control[key]))
            row = {"seed_id": seed["seed_id"], **cell,
                "metric_changes": difference(values(treated[key]), values(control[key])), "gap_changes": delta,
                "all_five_gaps_nonworse": all(v is not None and v <= 1e-12 for v in delta.values())}
            paired.append(row)
            lookup[(seed["seed_id"], cell["background_anchor"], cell["feedback_scale"], cell["risk_mode"], cell["delivery"])] = row
    interactions = []
    for seed in seeds:
        for scale in (1.0, 0.0):
            for risk in ("market_independent", "market_shared"):
                for delivery in ("masked", "public"):
                    fixed = lookup[seed["seed_id"], "initial_inventory", scale, risk, delivery]
                    current = lookup[seed["seed_id"], "current_inventory", scale, risk, delivery]
                    interactions.append({"seed_id": seed["seed_id"], "feedback_scale": scale, "risk_mode": risk, "delivery": delivery,
                        "metric_interaction": difference(current["metric_changes"], fixed["metric_changes"]),
                        "gap_interaction": difference(current["gap_changes"], fixed["gap_changes"])})
    return {"paired_allocation_effects": paired, "allocation_by_anchor_interactions": interactions,
        "interaction_direction": "allocation effect under current-inventory anchor minus allocation effect under original fixed anchor"}
