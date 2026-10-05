"""Independently reconstruct risk budgets from an independently read raw panel."""
import hashlib
import math
from fractions import Fraction

from .public_market_factor import FEATURE_KEYS
from .audit_issuer_valuation import expected_receipt

def uniform(token):
    h = hashlib.sha256(token.encode()).hexdigest()
    return h, math.sqrt(3) * (2 * ((int(h[:16], 16) + .5) / 2 ** 64) - 1)

def expected_budget_path(legacy, independently_read_panel, mode):
    if mode not in ("market_independent", "market_shared"):
        raise ValueError("unknown audit risk mode")
    params = legacy["parameters"]
    common_seed = independently_read_panel["parameters"]["innovation_seed"]
    settings = {"version": "fixed-marginal-received-market-risk-v1", "mode": mode, "common_innovation_seed": common_seed}
    output = {}
    for stock, source_rows in legacy["by_stock"].items():
        rows = []
        for source, public in zip(source_rows, independently_read_panel["by_stock"][stock], strict=True):
            f = {k: public[k] for k in FEATURE_KEYS}
            h = f["history"]
            if source["history"] != [{"trade_date": r["trade_date"], "stock_return": r["stock_return"]} for r in h]:
                raise ValueError("independent stock histories disagree")
            x, y = [Fraction(str(r["market_return"])) for r in h], [Fraction(str(r["stock_return"])) for r in h]
            n = len(h)
            vx = (sum(a * a for a in x) - sum(x) ** 2 / n) / (n - 1)
            vy = (sum(a * a for a in y) - sum(y) ** 2 / n) / (n - 1)
            cross = (sum(a * b for a, b in zip(x, y, strict=True)) - sum(x) * sum(y) / n) / (n - 1)
            if vx <= 0 or vy < 0:
                raise ValueError("independent source variance invalid")
            q = cross * cross / (vx * vy) if vy else Fraction(0)
            if not 0 <= q <= 1:
                raise ValueError("independent source risk fraction invalid")
            total = source["lagged_stock_volatility"] * params["risk_multiplier"] * 10000
            loading = (1 if cross > 0 else -1 if cross < 0 else 0) * total * math.sqrt(float(q))
            remaining = total * math.sqrt(float(1 - q))
            if not math.isclose(total * total, loading * loading + remaining * remaining, abs_tol=1e-12, rel_tol=2e-15):
                raise ValueError("independent fixed variance identity failed")
            day = source["trade_date"]
            ph, pz = uniform(f"issuer-innovation-v1:{params['innovation_seed']}:{stock}:{day}")
            ch, cz = uniform(f"public-factor-common-v1:{common_seed}:{day}")
            ih, iz = uniform(f"risk-budget-market-independent-v1:{common_seed}:{stock}:{day}")
            used_h, used_z = (ch, cz) if mode == "market_shared" else (ih, iz)
            private, market = remaining * pz, loading * used_z
            raw = private + market
            shift = min(params["max_shift_bps"], max(-params["max_shift_bps"], raw))
            feature = {k: source[k] for k in ("trade_date", "signal_cutoff_date", "history", "history_sha256", "lagged_stock_volatility")}
            rows.append({**feature, "innovation_sha256": ph, "standardized_innovation": pz,
                "raw_shift_bps": raw, "valuation_shift_bps": shift, "was_capped": raw != shift,
                "budget_factor_feature": f, "risk_budget": {
                    "market_variance_share_fraction": [q.numerator, q.denominator],
                    "residual_variance_share_fraction": [(1 - q).numerator, (1 - q).denominator],
                    "total_amplitude_bps": total, "signed_market_amplitude_bps": loading,
                    "residual_amplitude_bps": remaining, "pre_cap_variance_bps2": total * total,
                    "common_innovation_sha256": ch, "common_standardized_innovation": cz,
                    "independent_market_innovation_sha256": ih, "independent_market_standardized_innovation": iz,
                    "used_market_innovation_sha256": used_h, "used_market_standardized_innovation": used_z,
                    "residual_raw_shift_bps": private, "market_raw_shift_bps": market}})
        output[stock] = rows
    return {"scenario_id": legacy["scenario_id"], "parameters": dict(params), "risk_budget_parameters": settings, "by_stock": output}

def verify_path(actual, legacy, independently_read_panel, mode):
    expected = expected_budget_path(legacy, independently_read_panel, mode)
    if actual != expected:
        raise ValueError("independent complete risk-budget path differs")
    return sum(len(rows) for rows in actual["by_stock"].values())

def verify_mask(receipt, legacy_row, params, stock, actor):
    original = expected_receipt(legacy_row, params, stock, actor)
    if (receipt["received"], receipt["receipt_sha256"], receipt["information_probability_bps"]) != (
        original["received"], original["receipt_sha256"], original["information_probability_bps"]):
        raise ValueError("information delivery changed with shared risk")
    if not receipt["received"] and (receipt["valuation_shift_bps"] is not None or receipt["applied_valuation_shift_bps"] != 0):
        raise ValueError("unreceived private message was exposed or missingness erased")
