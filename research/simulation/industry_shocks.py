"""Declared industry stress paths and an explicitly labelled permutation control."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

from .agents import finite_range
from .background_response import respond_background_demand


def validate_industry_shocks(assets: list[str], sessions: int, path: dict) -> dict:
    fields = {"scenario_id", "assignment_mode", "membership", "path_groups", "by_group"}
    if (not isinstance(assets, list) or not assets or len(set(assets)) != len(assets)
            or type(sessions) is not int or sessions <= 0
            or not isinstance(path, dict) or set(path) != fields or not isinstance(path["scenario_id"], str)
            or not path["scenario_id"] or path["assignment_mode"] not in {"industry", "permuted_control"}
            or not isinstance(path["membership"], dict) or set(path["membership"]) != set(assets)
            or not isinstance(path["path_groups"], dict) or set(path["path_groups"]) != set(assets)
            or not isinstance(path["by_group"], dict) or not path["by_group"]):
        raise ValueError("industry shocks need explicit assignment and complete asset coverage")
    for group in [*path["membership"].values(), *path["path_groups"].values(), *path["by_group"]]:
        if not isinstance(group, str) or not re.fullmatch(r"[A-S]\d{2}", group):
            raise ValueError("industry shock group requires an explicit nonnull official code")
    if set(path["by_group"]) != set(path["path_groups"].values()):
        raise ValueError("industry shock group paths must match assigned groups")
    if path["assignment_mode"] == "industry" and path["membership"] != path["path_groups"]:
        raise ValueError("true industry assignment cannot be relabelled as a permutation")
    for values in path["by_group"].values():
        if not isinstance(values, list) or len(values) != sessions:
            raise ValueError("industry paths must cover every session")
        for value in values:
            finite_range(value, -1.0, 1.0, "industry shock")
    return {**path, "membership": dict(path["membership"]), "path_groups": dict(path["path_groups"]),
            "by_group": {group: list(values) for group, values in path["by_group"].items()}}


def subset_industry_shocks(path: dict, assets: list[str]) -> dict:
    groups = {path["path_groups"][asset] for asset in assets}
    return {**path, "membership": {asset: path["membership"][asset] for asset in assets},
            "path_groups": {asset: path["path_groups"][asset] for asset in assets},
            "by_group": {group: path["by_group"][group] for group in sorted(groups)}}


def build_industry_grid(membership: dict[str, str], sessions: int, pulse_sessions: list[int],
                        polarities: list[int], amplitude: float, seed: str, permutation_seed: str) -> list[dict]:
    """Match total cross-stock RMS at each pulse without using realized outcomes.

    Pure industry/permuted cells also match each stock's absolute dose. Mixed
    cells split squared dose equally between common and zero-mean industry
    components; permutation preserves their exact daily shock multiset.
    """
    if (not isinstance(membership, dict) or not membership or type(sessions) is not int or sessions < 1
            or any(not isinstance(code, str) or not re.fullmatch(r"\d{6}", code) for code in membership)
            or any(not isinstance(group, str) or not re.fullmatch(r"[A-S]\d{2}", group) for group in membership.values())
            or len(set(membership.values())) < 2 or not isinstance(seed, str) or not seed
            or not isinstance(permutation_seed, str) or not permutation_seed
            or not isinstance(pulse_sessions, list) or not pulse_sessions or len(set(pulse_sessions)) != len(pulse_sessions)
            or not isinstance(polarities, list) or len(polarities) != len(pulse_sessions)
            or any(type(i) is not int or not 0 <= i < sessions for i in pulse_sessions)
            or any(type(sign) is not int or sign not in {-1, 1} for sign in polarities)):
        raise ValueError("industry grid membership or declared pulse schedule is invalid")
    finite_range(amplitude, 0.0, 1.0, "industry total RMS amplitude")
    if amplitude == 0:
        raise ValueError("industry dose comparison requires positive declared amplitude")
    codes, groups = sorted(membership), sorted(set(membership.values()))
    donors = sorted(codes, key=lambda code: (hashlib.sha256(f"{permutation_seed}:{code}".encode()).hexdigest(), code))
    permuted = {code: membership[donor] for code, donor in zip(codes, donors, strict=True)}
    if Counter(permuted.values()) != Counter(membership.values()):
        raise ValueError("industry permutation must preserve all group sizes")
    raw = {group: [0.0] * sessions for group in groups}
    centered = {group: [0.0] * sessions for group in groups}
    uniform, mixed_common = [0.0] * sessions, [0.0] * sessions
    for ordinal, (session, polarity) in enumerate(zip(pulse_sessions, polarities, strict=True)):
        signs = {group: 1 if hashlib.sha256(f"{seed}:{group}:{ordinal // 2}".encode()).digest()[0] % 2 else -1 for group in groups}
        mean = math.fsum(signs[membership[code]] for code in codes) / len(codes)
        scale = math.sqrt(math.fsum((signs[membership[code]] - mean) ** 2 for code in codes) / len(codes))
        if scale == 0:
            raise ValueError("declared industry seed has no cross-stock variation at a pulse")
        uniform[session] = amplitude * polarity
        mixed_common[session] = amplitude * polarity / math.sqrt(2)
        for group in groups:
            raw[group][session] = amplitude * polarity * signs[group]
            centered[group][session] = amplitude * polarity / math.sqrt(2) * (signs[group] - mean) / scale
    cells = [{"name": "uniform_common", "common": uniform, "industry": None}]
    for mixed in (False, True):
        for permute in (False, True):
            name = ("common_plus_industry" if mixed else "industry_only") + ("_permuted" if permute else "_grouped")
            path = {"scenario_id": name, "assignment_mode": "permuted_control" if permute else "industry",
                    "membership": dict(membership), "path_groups": permuted if permute else dict(membership),
                    "by_group": centered if mixed else raw}
            path = validate_industry_shocks(codes, sessions, path)
            cells.append({"name": name, "common": list(mixed_common) if mixed else [0.0] * sessions, "industry": path})
    for cell in cells:
        doses = []
        for session in range(sessions):
            values = [cell["common"][session] + (cell["industry"]["by_group"][cell["industry"]["path_groups"][code]][session]
                      if cell["industry"] else 0.0) for code in codes]
            for value in values:
                finite_range(value, -1.0, 1.0, "combined industry scenario shock")
            rms = math.sqrt(math.fsum(value * value for value in values) / len(codes))
            target = amplitude if session in pulse_sessions else 0.0
            if not math.isclose(rms, target, rel_tol=0.0, abs_tol=1e-12):
                raise ValueError("total pulse RMS differs between industry cells")
            doses.append({"session": session, "rms": rms, "cross_stock_mean": math.fsum(values) / len(values),
                          "mean_absolute": math.fsum(abs(value) for value in values) / len(values),
                          "minimum": min(values), "maximum": max(values)})
        cell["dose_by_session"] = doses
    return cells


def respond_shared_background(demand: dict, stock: str, session: int, name: str, price: int,
                              bounds: tuple[int, int], venue: dict, background: dict, common: float,
                              industry: float, industry_code: str, path_group: str, response: dict) -> dict:
    shared = common + industry
    result = respond_background_demand(demand, stock, session, name, price, bounds, venue, background, shared, response)
    if result is demand or "common_news_response" not in result:
        return result
    detail = dict(result.pop("common_news_response"))
    detail.update(shared_shock=detail.pop("common_shock"), common_shock=common, industry_shock=industry,
                  industry_code=industry_code, industry_path_group=path_group)
    result["shared_news_response"] = detail
    return result
