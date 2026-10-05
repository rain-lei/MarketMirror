"""Reconstruct all twelve transport paths without importing their producer.

Raw numeric sources are separately rebuilt by audit_numeric_contract. This
module independently hashes innovations/ranks and integrates declared scales.
"""
from .audit_fixed_marginal_risk import expected_budget_path
from .audit_temporal_residual_coupling import independent_windows, independent_selection, independent_row
from .audit_public_information_risk import verify_budget as verify_public
from .audit_temporal_whole_information import verify_budget as verify_whole
from .temporal_information_study import require, read_gzip, ROOT


def verify_prepared(cfg, saved, risk, features, seed, sealed_windows=None):
    params = {**cfg["issuer_parameters"], "innovation_seed": seed["innovation_seed"], "information_seed": seed["information_seed"]}
    legacy = {"scenario_id": "temporal_candidate_2022", "parameters": params, "by_stock": risk}
    panel = {"parameters": {"innovation_seed": "marketmirror-public-market-factor-v1:" + seed["innovation_seed"]}, "by_stock": features}
    windows = independent_windows(cfg, features)
    source = read_gzip(ROOT / cfg["residual_rank_windows_path"])["windows"] if sealed_windows is None else sealed_windows
    require(source == windows, "Transport past ranks differ from independent reconstruction")
    date_seed = cfg["residual_common_seed_namespace"] + seed["innovation_seed"]
    choices = [independent_selection(window, date_seed) for window in windows]
    settings = {"version": "past-only-global-residual-rank-coupling-v1", "common_date_seed": date_seed,
        "universe_stocks": sorted(features), "windows": choices}
    counts = {"independently_rebuilt_residual_rank_windows": len(cfg["stocks"]) * len(cfg["dates"]),
        "independently_verified_common_date_draws": len(cfg["dates"]), "independently_verified_coupled_innovations": 0,
        "independently_rebuilt_budget_rows": 0, "independently_integrated_public_budget_rows": 0,
        "independently_integrated_whole_public_budget_rows": 0}
    keys = {dep + "_" + mode + "_" + delivery for dep in ("independent", "historical_rank")
        for mode in ("market_independent", "market_shared") for delivery in ("masked", "public", "whole_public")}
    require(set(saved) == keys, "Transport omitted or added prepared risk paths")
    for mode in ("market_independent", "market_shared"):
        baseline = expected_budget_path(legacy, panel, mode)
        for dep in ("independent", "historical_rank"):
            expected = baseline
            if dep == "historical_rank":
                expected = {**baseline, "residual_coupling_parameters": settings,
                    "by_stock": {stock: [independent_row(row, window, choice, stock, params)
                        for row, window, choice in zip(rows, windows, choices, strict=True)]
                        for stock, rows in baseline["by_stock"].items()}}
            for delivery in ("masked", "public", "whole_public"):
                actual = saved[dep + "_" + mode + "_" + delivery]
                header, budget, verifier = ({"masked": (None, None, None),
                    "public": ("information_coverage_parameters", "public_information_budget", verify_public),
                    "whole_public": ("whole_information_coverage_parameters", "whole_information_budget", verify_whole)})[delivery]
                if header is None:
                    plain = actual
                else:
                    version = "public-information-matched-received-risk-v1" if delivery == "public" else "whole-information-matched-received-risk-v1"
                    require(actual.get(header) == {"version": version, "delivery": delivery}, "Transport coverage header differs")
                    plain = {key: value for key, value in actual.items() if key != header}
                    plain["by_stock"] = {stock: [{key: value for key, value in row.items() if key != budget} for row in rows]
                        for stock, rows in actual["by_stock"].items()}
                require(plain == expected, "Transport altered original raw source, amplitudes, clocks or innovations")
                for rows in actual["by_stock"].values():
                    for row in rows:
                        if verifier is not None:
                            verifier(row, params)
                            name = "independently_integrated_public_budget_rows" if delivery == "public" else "independently_integrated_whole_public_budget_rows"
                            counts[name] += 1
                        counts["independently_rebuilt_budget_rows"] += 1
                        counts["independently_verified_coupled_innovations"] += int(dep == "historical_rank")
    return counts
