"""Independently check industry shock allocation, beliefs and background orders."""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from fractions import Fraction

from .audit_background_response import expected_demand


def audit_industry_grid(cells: list[dict], membership: dict, sessions: int, config: dict) -> dict:
    if [cell["name"] for cell in cells] != config["variants"]:
        raise ValueError("industry grid scenario coverage differs")
    codes = sorted(membership)
    donors = sorted(codes, key=lambda code: (hashlib.sha256(f"{config['permutation_seed']}:{code}".encode()).digest(), code))
    permuted = dict(zip(codes, (membership[code] for code in donors), strict=True))
    amplitude = config["total_cross_stock_rms"]
    expected_by_cell = {}
    pulses = dict(zip(config["pulse_sessions"], config["pulse_polarities"], strict=True))
    ordinal = {session: i for i, session in enumerate(config["pulse_sessions"])}
    checks = 0
    for cell in cells:
        name = cell["name"]
        mixed, permute = name.startswith("common_plus"), name.endswith("permuted")
        assignment = permuted if permute else membership
        path = cell["industry"]
        if name == "uniform_common":
            if path is not None:
                raise ValueError("uniform comparison contains an industry component")
        elif (path["membership"] != membership or path["path_groups"] != assignment
              or path["scenario_id"] != name or path["assignment_mode"] != ("permuted_control" if permute else "industry")
              or set(path["by_group"]) != set(membership.values())):
            raise ValueError("industry membership or permutation differs from independent allocation")
        if Counter(assignment.values()) != Counter(membership.values()):
            raise ValueError("permutation changed global group sizes")
        totals = {code: [] for code in codes}
        for session in range(sessions):
            polarity = pulses.get(session, 0)
            common = amplitude * polarity if name == "uniform_common" else (amplitude * polarity / math.sqrt(2) if mixed else 0.0)
            signs = {group: (1 if hashlib.sha256(f"{config['industry_seed']}:{group}:{ordinal[session] // 2}".encode()).digest()[0] & 1 else -1)
                     for group in set(membership.values())} if polarity else {}
            center = sum(Fraction(signs[membership[code]]) for code in codes) / len(codes) if polarity else Fraction(0)
            variance = sum((Fraction(signs[membership[code]]) - center) ** 2 for code in codes) / len(codes) if polarity else Fraction(0)
            if abs(cell["common"][session] - common) > 1e-12:
                raise ValueError("common component differs from independent dose reconstruction")
            for code in codes:
                expected = 0.0
                if polarity and name != "uniform_common":
                    expected = (amplitude * polarity / math.sqrt(2) * float(Fraction(signs[assignment[code]]) - center) / math.sqrt(float(variance))
                                if mixed else amplitude * polarity * signs[assignment[code]])
                actual = path["by_group"][path["path_groups"][code]][session] if path else 0.0
                if not math.isfinite(actual) or abs(actual - expected) > 1e-12:
                    raise ValueError("industry component differs from independent group/dose reconstruction")
                totals[code].append(common + actual)
                checks += 1
            values = [totals[code][-1] for code in codes]
            mean_square = sum(Fraction(str(value)) ** 2 for value in values) / len(codes)
            if abs(float(mean_square) - (amplitude * amplitude if polarity else 0)) > 1e-12:
                raise ValueError("industry grid total RMS is not matched")
        expected_by_cell[name] = totals
    for mixed in (False, True):
        base = "common_plus_industry" if mixed else "industry_only"
        for session in range(sessions):
            if sorted(expected_by_cell[base + "_grouped"][code][session] for code in codes) != sorted(expected_by_cell[base + "_permuted"][code][session] for code in codes):
                raise ValueError("permutation does not preserve the exact daily shock multiset")
    return {"independent_asset_session_dose_checks": checks, "matched_pulse_rms": amplitude,
            "permuted_global_group_sizes_preserved": True, "paired_daily_shock_multisets_equal": True}


def verify_industry_delivery(day: dict, specs: dict, shocks: dict, industry: dict | None, session: int) -> None:
    for stock, observation in day["observations"].items():
        common, issuer = shocks["common"][session], shocks["asset_specific"][stock][session]
        shared = 0.0
        if industry is not None:
            group = industry["path_groups"][stock]
            shared = industry["by_group"][group][session]
            expected = {"industry_scenario_id": industry["scenario_id"], "industry_assignment_mode": industry["assignment_mode"],
                        "industry_code": industry["membership"][stock], "industry_path_group": group, "industry_shock": shared,
                        "industry_evidence": f"industry-scenario:{industry['scenario_id']}:{group}:{session}"}
            if any(observation.get(key) != value for key, value in expected.items()):
                raise ValueError("declared industry shock was not delivered exactly")
        elif any(key.startswith("industry_") for key in observation):
            raise ValueError("industry input appeared in the uniform control")
        if (observation["common_shock"] != common or observation["issuer_specific_shock"] != issuer
                or observation["scenario_shock"] != common + issuer + shared
                or observation["scenario_id"] != shocks["scenario_id"]
                or observation["scenario_evidence"] != f"scenario:{shocks['scenario_id']}:{stock}:{session}"
                or observation["text_signal"] != 0.0 or observation["text_uncertainty"] != 0.0 or observation["text_evidence"] != "text:disabled"):
            raise ValueError("combined industry observation differs from the declared no-text scenario")
        for name, spec in specs.items():
            if spec["kind"] != "strategy":
                continue
            profile, parameters = spec["profile"], spec["parameters"]
            signal = max(-1.0, min(1.0, observation["market_signal"] * profile["momentum_loading"]
                                  + profile["market_bias"] + common + issuer + shared))
            belief = parameters["market_sensitivity"] * signal
            if not math.isclose(day["decisions"][name]["beliefs"][stock], belief, rel_tol=0.0, abs_tol=1e-12):
                raise ValueError("industry strategy belief differs from independent reconstruction")


def verify_shared_background(day: dict, previous: dict, stock: str, session: int, venue: dict,
                             background: dict, common: float, industry: float, industry_code: str,
                             path_group: str, response: dict, specs: dict) -> None:
    names = {name for name, spec in specs.items() if spec["kind"] == "background" and spec["asset"] == stock}
    if set(day["background_demands"][stock]) != names:
        raise ValueError("shared background demand coverage differs")
    call = day["portfolio_auction"]["asset_calls"][stock]
    orders = {order["owner"]: order for order in call["orders"]}
    for name in names:
        expected = expected_demand(stock, session, name, previous["accounts"][name]["shares"][stock], previous["prices"][stock],
                                   call["price_bounds_minor"], venue, background, common + industry, response)
        if "common_news_response" in expected:
            detail = expected.pop("common_news_response")
            detail["shared_shock"] = detail.pop("common_shock")
            detail.update(common_shock=common, industry_shock=industry, industry_code=industry_code, industry_path_group=path_group)
            expected["shared_news_response"] = detail
        if day["background_demands"][stock][name] != expected:
            raise ValueError("shared background order differs from independent rational reconstruction")
        if expected["requested_quantity"]:
            actual = orders.get(name)
            if actual is None or any(actual[field] != expected[key] for field, key in (
                    ("side", "side"), ("quantity", "requested_quantity"), ("limit_price_minor", "limit_price_minor"))):
                raise ValueError("submitted industry order differs from background demand")
        elif name in orders:
            raise ValueError("zero shared background demand submitted an order")
