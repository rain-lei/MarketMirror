"""Small archived-state counterfactuals, without new market paths or model calls."""
from __future__ import annotations

import copy
import hashlib
import math

from ..semantic.source_case_facts import canonical
from .agent_decision_trace import explain_decision
from .agents import AgentParameters
from .portfolio_auction import PortfolioAccount
from .source_case_decision_link import case_inputs

VERSION = "source-case-fixed-state-counterfactual-v1"
ROLES = ("aggressive", "conservative", "institutional")
STRENGTHS = (0.5, 1.0, 1.5)
CONTEXTS = ("within_actor", "common_role_state")
PAIRS = (
    ("no_text:1", "keywords:1"),
    ("no_text:1", "reviewed_llm:1"),
    ("keywords:1", "reviewed_llm:1"),
    ("reviewed_llm:1", "llm_asset_placebo:1"),
    ("reviewed_llm:1", "reviewed_llm:0.5"),
    ("reviewed_llm:1", "reviewed_llm:1.5"),
)


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def select_checkpoints(links):
    visible = [x["step"] for x in links if x["source_visible"]]
    active = [x["step"] for x in links if x["information_active"]]
    if not visible or not active or min(visible) == 0:
        raise ValueError("both a pre-publication and an active checkpoint are required")
    inactive = [x["step"] for x in links
                if x["step"] > max(active) and not x["information_active"]]
    if not inactive:
        raise ValueError("a post-duration checkpoint is required")
    return {"before_publication": min(visible) - 1, "first_visible": min(visible),
            "first_inactive": min(inactive)}


def scaled_observations(base, joined, link, step, mode, strength):
    if type(strength) not in (int, float) or not math.isfinite(strength) or strength <= 0:
        raise ValueError("positive finite declared mapping strength required")
    if mode not in ("reviewed_llm", "llm_asset_placebo") and strength != 1:
        raise ValueError("only reviewed-fact mapping strengths may vary")
    observations = copy.deepcopy(base)
    for asset, row in observations.items():
        source = joined[asset][step]
        if mode == "no_text":
            if row["text_signal"] != 0 or row["text_uncertainty"] != 0:
                raise ValueError("fixed reference must have disabled text")
            continue
        row["text_signal"] = max(-1.0, min(1.0, source["text_signal"] * strength))
        row["text_uncertainty"] = max(0.0, min(1.0, source["text_uncertainty"] * strength))
        row["text_evidence"] = source["text_evidence"] + f":assumed-strength:{strength:g}"
    used_link = copy.deepcopy(link)
    used_link.update(mapping_strength=strength, strength_is_assumed=True,
                     applied_by_asset={a: {"signal": r["text_signal"],
                                          "uncertainty": r["text_uncertainty"]}
                                       for a, r in observations.items()})
    return observations, used_link


def request_signature(explanation):
    return [(a, v["action"], v["whole_lot_quantity"])
            for a, v in sorted(explanation["requested_orders"].items())]


def compare_rows(reference, treatment):
    if reference["fixed_state_sha256"] != treatment["fixed_state_sha256"]:
        raise ValueError("counterfactual shared-state fingerprints differ")
    for name in ("account_before", "prices_before_minor", "memory_before", "parameters", "profile", "session"):
        if reference["explanation"][name] != treatment["explanation"][name]:
            raise ValueError("counterfactual state or role parameters differ")
    text_fields = {"text_signal", "text_uncertainty", "text_evidence"}
    def market_inputs(row):
        return {a: {k: v for k, v in o.items() if k not in text_fields}
                for a, o in row["observations"].items()}
    if market_inputs(reference) != market_inputs(treatment):
        raise ValueError("counterfactual non-text observations differ")
    r, t = reference["decision"], treatment["decision"]
    return {"reference": reference["variant"], "treatment": treatment["variant"],
            "beliefs_changed": r["beliefs"] != t["beliefs"],
            "targets_changed": r["desired_weights"] != t["desired_weights"],
            "requests_changed": request_signature(reference["explanation"]) != request_signature(treatment["explanation"]),
            "gates_changed": reference["explanation"]["gates"] != treatment["explanation"]["gates"],
            "actual_fills_computed": False}


def compare_archived_state(raw, case, record, mapping, portfolio_case, lot_size):
    """Compare modes at three registered dates with each input state held fixed.

    The shared-role comparison borrows aggressive_00's no-text account AND
    confirmation memory for every role. It isolates the preset rule bundle;
    it does not identify which individual parameter explains a difference.
    No counterfactual order is submitted and no counterfactual fill is inferred.
    """
    if (raw["mode"] != "no_text" or raw["case_id"] != case["case_id"]
            or raw["case_text_sha256"] != case["case_text_sha256"]):
        raise ValueError("matching archived no-text case required")
    inputs = {m: case_inputs(case, record, mapping, m)
              for m in ("no_text", "keywords", "reviewed_llm", "llm_asset_placebo")}
    if raw["source_links"] != inputs["no_text"][1]:
        raise ValueError("archived source links differ from the fixed source clock")
    checkpoints = select_checkpoints(raw["source_links"])
    specs = raw["model_result"]["participant_specs"]
    owners = sorted(n for n, v in specs.items() if v["kind"] == "strategy")
    if len(owners) != 12 or any(f"{r}_00" not in owners for r in ROLES):
        raise ValueError("all twelve registered strategy accounts required")
    if any(specs[n]["profile"] != specs["aggressive_00"]["profile"] for n in owners):
        raise ValueError("common-role comparison requires a shared homogeneous profile")
    saved = {(r["session"], r["actor"]): r for r in raw["decision_explanations"]}
    variants = [("no_text", 1.0), ("keywords", 1.0)] + [
        (mode, strength) for mode in ("reviewed_llm", "llm_asset_placebo") for strength in STRENGTHS]
    groups = []
    for checkpoint, step in checkpoints.items():
        day = raw["model_result"]["trace"][step]
        for actor in owners:
            archived = saved[step, actor]
            if (archived["parameters"] != specs[actor]["parameters"]
                    or archived["profile"] != specs[actor]["profile"]
                    or archived["prices_before_minor"] != day["portfolio_auction"]["prices_before_minor"]):
                raise ValueError("archived decision input differs from original specifications")
            explain_decision(AgentParameters(**archived["parameters"]), archived["profile"],
                PortfolioAccount(**copy.deepcopy(archived["account_before"])), archived["prices_before_minor"],
                day["observations"], day["covariance"], portfolio_case, archived["memory_before"],
                step, False, day["decisions"][actor], lot_size)
        units = [("within_actor", n, n) for n in owners] + [
            ("common_role_state", f"{role}_00", "aggressive_00") for role in ROLES]
        for context, actor, source_actor in units:
            source = saved[step, source_actor]
            state = {"account": copy.deepcopy(source["account_before"]),
                     "prices": copy.deepcopy(source["prices_before_minor"]),
                     "observations": copy.deepcopy(day["observations"]),
                     "covariance": copy.deepcopy(day["covariance"]),
                     "memory": copy.deepcopy(source["memory_before"]), "session": step,
                     "profile": copy.deepcopy(source["profile"]),
                     "portfolio_case": copy.deepcopy(portfolio_case), "lot_size": lot_size,
                     "signal_cutoff_date": day["signal_cutoff_date"], "trade_date": day["trade_date"]}
            before = copy.deepcopy(state)
            rows = []
            agent = AgentParameters(**specs[actor]["parameters"])
            for mode, strength in variants:
                joined, links = inputs[mode]
                observations, link = scaled_observations(state["observations"], joined, links[step], step, mode, strength)
                explained = explain_decision(agent, state["profile"], PortfolioAccount(**copy.deepcopy(state["account"])),
                    state["prices"], observations, state["covariance"], portfolio_case,
                    state["memory"], step, mode != "no_text", lot_size=lot_size)
                rows.append({"variant": f"{mode}:{strength:g}", "mode": mode, "strength": strength,
                             "source_link": link, "observations": observations,
                             "fixed_state_sha256": digest(state), **explained})
            if state != before:
                raise ValueError("counterfactual changed the reference state")
            by_variant = {r["variant"]: r for r in rows}
            groups.append({"context": context, "checkpoint": checkpoint, "actor": actor, "role": agent.role,
                "state_source_actor": source_actor, "state": state, "state_sha256": digest(state),
                "rows": rows, "comparisons": [compare_rows(by_variant[r], by_variant[t]) for r, t in PAIRS],
                "actual_fills_computed": False})
    return {"version": VERSION, "case_id": case["case_id"], "seed": raw["seed"],
            "case_text_sha256": case["case_text_sha256"], "checkpoints": checkpoints, "groups": groups,
            "independent_event_count": 1, "historical_market_effect_identified": False,
            "no_new_market_paths_or_model_calls": True}
