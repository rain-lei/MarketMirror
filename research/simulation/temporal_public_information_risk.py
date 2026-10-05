"""Frozen received-message formula with explicit available-history validation."""
from . import temporal_fixed_marginal_risk as fixed
from .public_information_risk import (with_public_delivery, message_receipt,
    strategy_quote_terms, respond_issuer_background)


def build_path(risk, features, scenario_id, parameters, mode, common_innovation_seed, delivery):
    if delivery not in ("masked", "public"):
        raise ValueError("explicit message delivery required")
    path = fixed.build_path(risk, features, scenario_id, parameters, mode, common_innovation_seed)
    return with_public_delivery(path) if delivery == "public" else path


def validate_issuer_valuation(assets, calendar, path):
    if not isinstance(path, dict) or "information_coverage_parameters" not in path:
        return fixed.validate_issuer_valuation(assets, calendar, path)
    plain = {k: v for k, v in path.items() if k != "information_coverage_parameters"}
    plain["by_stock"] = {s: [{k: v for k, v in r.items() if k != "public_information_budget"}
        for r in rows] for s, rows in path["by_stock"].items()}
    fixed.validate_issuer_valuation(assets, calendar, plain)
    if path != with_public_delivery(plain):
        raise ValueError("temporal public path differs from frozen received-risk budget")
    return path
