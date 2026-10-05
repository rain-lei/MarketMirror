"""Matched marginal message risk with unchanged private information coverage.

The independent and shared market components have the same one-stock mixture
distribution. Only their cross-stock coupling differs. This is a synthetic
received-message experiment, not universally delivered public news.
"""
from __future__ import annotations
import hashlib
import math
from fractions import Fraction

from .issuer_valuation import (RISK_FIELDS, MESSAGE_FIELDS, validate_parameters,
    validate_risk_row, validate_issuer_valuation as validate_legacy,
    message_receipt, strategy_quote_terms, respond_issuer_background)
from .public_market_factor import FEATURE_KEYS, feature

VERSION = "fixed-marginal-received-market-risk-v1"
MODES = ("market_independent", "market_shared")
EXTRA_FIELDS = {"budget_factor_feature", "risk_budget"}

def draw_uniform(token):
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    value = math.sqrt(3) * (2 * ((int(digest[:16], 16) + .5) / 2 ** 64) - 1)
    return digest, value

def budget_coefficients(row, factor, parameters):
    validate_parameters(parameters)
    validate_risk_row(row, row["trade_date"], row["signal_cutoff_date"], parameters["history_window"])
    if set(factor) != FEATURE_KEYS:
        raise ValueError("risk budget factor contains undeclared fields")
    rebuilt = feature(factor["history"], row["trade_date"], row["signal_cutoff_date"], parameters["history_window"], "sh.000300")
    if factor != rebuilt or [{"trade_date": r["trade_date"], "stock_return": r["stock_return"]} for r in factor["history"]] != row["history"]:
        raise ValueError("risk budget stock/index history differs or is not lagged")
    market = Fraction(*factor["market_variance_fraction"])
    stock = Fraction(*factor["stock_variance_fraction"])
    beta = Fraction(*factor["beta_fraction"])
    component = beta * beta * market
    residual = stock - component
    if stock < 0 or residual < 0 or (stock == 0 and component != 0):
        raise ValueError("invalid source variance decomposition")
    share = component / stock if stock else Fraction(0)
    remainder = 1 - share
    total = row["lagged_stock_volatility"] * parameters["risk_multiplier"] * 10000
    signed = (1 if beta > 0 else -1 if beta < 0 else 0) * total * math.sqrt(float(share))
    idiosyncratic = total * math.sqrt(float(remainder))
    if not math.isclose(signed * signed + idiosyncratic * idiosyncratic, total * total, rel_tol=2e-15, abs_tol=1e-12):
        raise ValueError("risk budget does not retain the original issuer amplitude")
    return {"market_variance_share_fraction": [share.numerator, share.denominator],
        "residual_variance_share_fraction": [remainder.numerator, remainder.denominator],
        "total_amplitude_bps": total, "signed_market_amplitude_bps": signed,
        "residual_amplitude_bps": idiosyncratic, "pre_cap_variance_bps2": total * total}

def budget_message(row, factor, stock, parameters, settings):
    if not isinstance(settings, dict) or set(settings) != {"version", "mode", "common_innovation_seed"} or settings["version"] != VERSION or settings["mode"] not in MODES:
        raise ValueError("explicit fixed marginal risk settings required")
    if not isinstance(settings["common_innovation_seed"], str) or not settings["common_innovation_seed"] or not isinstance(stock, str) or not stock:
        raise ValueError("risk budget seed and stock must be explicit")
    day = row["trade_date"]
    private_hash, private_z = draw_uniform(f"issuer-innovation-v1:{parameters['innovation_seed']}:{stock}:{day}")
    common_hash, common_z = draw_uniform(f"public-factor-common-v1:{settings['common_innovation_seed']}:{day}")
    independent_hash, independent_z = draw_uniform(f"risk-budget-market-independent-v1:{settings['common_innovation_seed']}:{stock}:{day}")
    coefficients = budget_coefficients(row, factor, parameters)
    used_hash, used_z = (common_hash, common_z) if settings["mode"] == "market_shared" else (independent_hash, independent_z)
    private_shift = coefficients["residual_amplitude_bps"] * private_z
    market_shift = coefficients["signed_market_amplitude_bps"] * used_z
    raw = private_shift + market_shift
    shift = max(-parameters["max_shift_bps"], min(parameters["max_shift_bps"], raw))
    return {**row, "innovation_sha256": private_hash, "standardized_innovation": private_z,
        "raw_shift_bps": raw, "valuation_shift_bps": shift, "was_capped": raw != shift,
        "budget_factor_feature": factor, "risk_budget": {**coefficients,
            "common_innovation_sha256": common_hash, "common_standardized_innovation": common_z,
            "independent_market_innovation_sha256": independent_hash, "independent_market_standardized_innovation": independent_z,
            "used_market_innovation_sha256": used_hash, "used_market_standardized_innovation": used_z,
            "residual_raw_shift_bps": private_shift, "market_raw_shift_bps": market_shift}}

def build_path(risk, features, scenario_id, parameters, mode, common_innovation_seed):
    if not isinstance(risk, dict) or not risk or set(risk) != set(features) or not isinstance(scenario_id, str) or not scenario_id:
        raise ValueError("risk budget panel coverage differs")
    settings = {"version": VERSION, "mode": mode, "common_innovation_seed": common_innovation_seed}
    result = {}
    for stock, rows in sorted(risk.items()):
        if not isinstance(rows, list) or not rows or len(rows) != len(features[stock]) or any(set(r) != RISK_FIELDS for r in rows):
            raise ValueError("risk budget requires complete explicit issuer risk rows")
        result[stock] = [budget_message(row, factor, stock, parameters, settings)
            for row, factor in zip(rows, features[stock], strict=True)]
    return {"scenario_id": scenario_id, "parameters": dict(parameters), "risk_budget_parameters": settings, "by_stock": result}

def validate_issuer_valuation(assets, calendar, path):
    if isinstance(path, dict) and "risk_budget_parameters" not in path:
        return validate_legacy(assets, calendar, path)
    if (not isinstance(path, dict) or set(path) != {"scenario_id", "parameters", "risk_budget_parameters", "by_stock"}
        or not isinstance(path["by_stock"], dict) or set(path["by_stock"]) != set(assets)):
        raise ValueError("risk budget path contract differs")
    risk, features = {}, {}
    for stock in assets:
        rows = path["by_stock"][stock]
        if not isinstance(rows, list) or len(rows) != len(calendar):
            raise ValueError("risk budget calendar differs")
        risk[stock], features[stock] = [], []
        for row, (day, cutoff, _) in zip(rows, calendar, strict=True):
            if not isinstance(row, dict) or set(row) != MESSAGE_FIELDS | EXTRA_FIELDS or type(row["was_capped"]) is not bool:
                raise ValueError("risk budget message schema differs")
            validate_risk_row(row, day, cutoff, path["parameters"]["history_window"])
            risk[stock].append({key: row[key] for key in RISK_FIELDS})
            features[stock].append(row["budget_factor_feature"])
    settings = path["risk_budget_parameters"]
    if not isinstance(settings, dict) or set(settings) != {"version", "mode", "common_innovation_seed"} or settings["version"] != VERSION:
        raise ValueError("risk budget settings differ")
    rebuilt = build_path(risk, features, path["scenario_id"], path["parameters"], settings["mode"], settings["common_innovation_seed"])
    if rebuilt != path:
        raise ValueError("risk budget message differs from its complete lagged source and raw innovations")
    return path

def subset(path, assets):
    return {**{k: v for k, v in path.items() if k != "by_stock"}, "by_stock": {a: path["by_stock"][a] for a in assets}}
