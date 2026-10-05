"""Predeclared exogenous shock paths for controlled portfolio stress scenarios."""

from __future__ import annotations

import hashlib

from .agents import finite_range


def validate_scenario_shocks(assets: list[str], sessions: int, shocks: dict) -> dict:
    if (not isinstance(assets, list) or not assets or len(set(assets)) != len(assets)
            or type(sessions) is not int or sessions <= 0
            or not isinstance(shocks, dict) or set(shocks) != {"scenario_id", "common", "asset_specific"}
            or not isinstance(shocks["scenario_id"], str) or not shocks["scenario_id"]
            or not isinstance(shocks["common"], list) or len(shocks["common"]) != sessions
            or not isinstance(shocks["asset_specific"], dict)
            or set(shocks["asset_specific"]) != set(assets)):
        raise ValueError("scenario shocks require an ID and complete session/asset paths")
    common = list(shocks["common"])
    specific = {}
    for value in common:
        finite_range(value, -1.0, 1.0, "common scenario shock")
    for asset in assets:
        values = shocks["asset_specific"][asset]
        if not isinstance(values, list) or len(values) != sessions:
            raise ValueError("issuer-specific shock path does not cover every session")
        specific[asset] = list(values)
        for value in values:
            finite_range(value, -1.0, 1.0, "issuer-specific scenario shock")
        if any(not -1.0 <= common[index] + values[index] <= 1.0 for index in range(sessions)):
            raise ValueError("combined scenario shock must stay between -1 and 1")
    return {"scenario_id": shocks["scenario_id"], "common": common, "asset_specific": specific}


def build_scenario_shocks(assets: list[str], sessions: int, scenario_id: str,
                          pulse_sessions: list[int], pulse_polarities: list[int],
                          common_amplitude: float, issuer_amplitude: float,
                          issuer_seed: str) -> dict:
    if (not isinstance(scenario_id, str) or not scenario_id or not isinstance(issuer_seed, str)
            or not issuer_seed or not isinstance(pulse_sessions, list) or not pulse_sessions
            or not isinstance(pulse_polarities, list) or len(pulse_sessions) != len(pulse_polarities)
            or len(set(pulse_sessions)) != len(pulse_sessions)):
        raise ValueError("scenario pulse schedule or identifier is invalid")
    finite_range(common_amplitude, 0.0, 1.0, "common shock amplitude")
    finite_range(issuer_amplitude, 0.0, 1.0, "issuer shock amplitude")
    if any(type(session) is not int or not 0 <= session < sessions for session in pulse_sessions):
        raise ValueError("scenario pulse session is outside the simulation calendar")
    if any(type(sign) is not int or sign not in {-1, 1} for sign in pulse_polarities):
        raise ValueError("scenario pulse polarity must be -1 or 1")
    common = [0.0] * sessions
    specific = {asset: [0.0] * sessions for asset in assets}
    for session, polarity in zip(pulse_sessions, pulse_polarities, strict=True):
        common[session] = common_amplitude * polarity
        for asset in assets:
            digest = hashlib.sha256(f"{issuer_seed}:{asset}".encode()).digest()
            exposure = 1 if digest[0] % 2 else -1
            specific[asset][session] = issuer_amplitude * polarity * exposure
    return validate_scenario_shocks(assets, sessions,
                                    {"scenario_id": scenario_id, "common": common,
                                     "asset_specific": specific})
