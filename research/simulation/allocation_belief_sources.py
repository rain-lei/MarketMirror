"""Ordered sources of the factual belief-to-total-exposure allocation stage."""

from __future__ import annotations

import math

SOURCES = ["profile_bias", "own_price_momentum_given_bias", "shared_shocks_given_prior", "private_issuer_given_prior"]


def clipped(value):
    if not math.isfinite(value):
        raise ValueError("belief provenance is nonfinite")
    return max(-1.0, min(1.0, value))


def decompose_sources(parameters, profile, observations, receipts, decision):
    assets = sorted(observations)
    if not assets or set(receipts) != set(assets) or set(decision["beliefs"]) != set(assets):
        raise ValueError("belief provenance asset/receipt identity differs")
    stages = [dict.fromkeys(assets, 0.0)]
    for step in range(4):
        values = {}
        for a in assets:
            o = observations[a]
            if o["text_signal"] != 0 or o["text_uncertainty"] != 0:
                raise ValueError("frozen belief provenance requires disabled text")
            signal = profile["market_bias"]
            if step >= 1:
                signal += o["market_signal"] * profile["momentum_loading"]
            if step >= 2:
                signal += o["scenario_shock"]
            if step >= 3:
                signal += receipts[a]["applied_belief_signal"]
            values[a] = parameters["market_sensitivity"] * clipped(signal)
        stages.append(values)
    for a in assets:
        if (not math.isclose(stages[-1][a], decision["beliefs"][a], rel_tol=0, abs_tol=1e-12)
                or not math.isclose(stages[-2][a], decision["issuer_base_beliefs"][a], rel_tol=0, abs_tol=1e-12)
                or not math.isclose(stages[-1][a] - stages[-2][a], decision["issuer_belief_contributions"][a], rel_tol=0, abs_tol=1e-12)):
            raise ValueError("factual belief/base/private contribution differs from sources")
    means = [sum(s.values()) / len(assets) for s in stages]
    totals = [max(0.0, parameters["base_weight"] + .4 * mean) for mean in means]
    components = {name: (totals[i + 1] - totals[i]) / len(assets) for i, name in enumerate(SOURCES)}
    factual = (totals[-1] - parameters["base_weight"]) / len(assets)
    if not math.isclose(sum(components.values()), factual, rel_tol=0, abs_tol=1e-12):
        raise ValueError("belief exposure components do not telescope")
    return {"component_equal_asset_weight_changes": components, "factual_equal_asset_weight_change": factual,
            "stage_mean_beliefs": means, "stage_total_exposures_before_risk_cap": totals,
            "order_defined_bookkeeping_not_causal_effects": True}
