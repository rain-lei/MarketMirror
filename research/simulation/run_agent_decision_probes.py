"""Run bounded synthetic decision probes with full traces and independent settlement."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from ..data_pipeline.policy_events import audit_sources
from .agent_decision_trace import explain_path, audit_explanations
from .agents import AgentParameters
from .portfolio_audit import audit_portfolio_day
from .portfolio_market import initial_portfolio, simulate_portfolio

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "research/configs/agent_decision_probes_2026_v1.json"
VERSION = "three-role-synthetic-decision-probes-v1"
CASES = ("neutral", "positive_public", "negative_public", "positive_issuer",
         "high_uncertainty", "high_risk")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_plan(path=DEFAULT_CONFIG):
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    if (plan.get("schema_version") != VERSION or plan.get("data_kind") != "synthetic"
            or plan.get("empirical_calibration") is not False
            or plan.get("historical_event_signal_applied") is not False
            or plan.get("llm_outputs_used") is not False):
        raise ValueError("explicit synthetic mechanism-only scope required")
    if tuple(c["case_id"] for c in plan["cases"]) != CASES or plan["seeds"] != [7, 11, 23, 47, 89]:
        raise ValueError("all registered cases and seeds required")
    if plan["assets"] != ["A", "B", "C"] or plan["sessions"] != 18 or plan["text_modes"] != [False, True]:
        raise ValueError("registered full probe dimensions required")
    for name, expected in plan["bindings"].items():
        if sha(ROOT / name) != expected:
            raise ValueError("frozen probe input changed: " + name)
    if len(plan["bindings"]) < 10:
        raise ValueError("complete source and code bindings required")
    return plan


def make_inputs(plan, case):
    """Synthetic dates order steps only; no historical price or event is inserted."""
    sessions = plan["sessions"]
    calendar = []
    day = date(2000, 1, 3)
    while len(calendar) < sessions + 2:
        if day.weekday() < 5:
            calendar.append(day.isoformat())
        day += timedelta(days=1)
    joined = {}
    for asset in plan["assets"]:
        steps = []
        for session in range(sessions):
            active = plan["pulse_start"] <= session < plan["pulse_stop"]
            exposed = case["scope"] == "public" or asset == "A"
            steps.append({
                "trade_date": calendar[session + 2], "signal_cutoff_date": calendar[session],
                "execution_reference_date": calendar[session + 1], "execution_available": True,
                "observed_return": 0.0, "market_signal": 0.0, "estimated_volatility": case["volatility_floor"],
                "text_signal": case["signal"] if active and exposed else 0.0,
                "text_uncertainty": case["uncertainty"] if active and exposed else 0.0,
                "text_evidence": f"synthetic-assumption:{case['case_id']}:{asset}:{session}",
            })
        joined[asset] = steps
    return joined


def run_one(plan, case, seed, use_text):
    agents = [AgentParameters(**p) for p in plan["agent_parameters"]]
    core = {**plan["core"], "seed": seed}
    background = {**plan["background"], "seed": seed}
    feedback = {**plan["feedback"], "volatility_floor": case["volatility_floor"]}
    portfolio_case = plan["portfolio_case"]
    venue = plan["venue"]
    inputs = make_inputs(plan, case)
    result = simulate_portfolio(inputs, agents, core, background, venue, feedback, portfolio_case, use_text)
    _, accounts, _, _ = initial_portfolio(agents, core, background, plan["assets"], portfolio_case, venue)
    explanations = explain_path(result, accounts, portfolio_case, venue, use_text)
    explanation_audit = audit_explanations(explanations, result)
    previous = {
        "accounts": {n: asdict(a) for n, a in accounts.items()},
        "prices": dict.fromkeys(plan["assets"], venue["price_start_minor"]), "fee_pool_minor": 0,
        "initial_cash_minor": sum(sum(a.wallets.values()) for a in accounts.values()),
        "initial_shares": {a: sum(account.shares[a] for account in accounts.values()) for a in plan["assets"]},
    }
    for session, record in enumerate(result["trace"]):
        previous = audit_portfolio_day(record, previous, venue, session, dense=(session == 0))
    if previous["prices"] != result["summary"]["final_prices_minor"]:
        raise ValueError("independent closing prices differ")
    if previous["fee_pool_minor"] != result["summary"]["fee_pool_minor"]:
        raise ValueError("independent closing fee pool differs")
    for owner, account in previous["accounts"].items():
        saved = result["summary"]["accounts"][owner]
        for field in ("wallets", "shares", "sellable"):
            if saved[field] != account[field]:
                raise ValueError("independent closing resources differ")
    return {"case_id": case["case_id"], "seed": seed, "use_text": use_text,
            "model_result": result, "decision_explanations": explanations,
            "audit": {**explanation_audit, "independent_portfolio_days": len(result["trace"]),
                      "independent_asset_calls": len(result["trace"]) * len(plan["assets"]),
                      "dense_first_day_asset_calls": len(plan["assets"])}}


def behavioral_signature(run):
    result = run["model_result"]
    return [{"decisions": day["decisions"], "auction": day["portfolio_auction"]}
            for day in result["trace"]]


def summarize_pair(no_text, with_text):
    nrows = {(r["session"], r["actor"]): r for r in no_text["decision_explanations"]}
    wrows = {(r["session"], r["actor"]): r for r in with_text["decision_explanations"]}
    def request_signature(row):
        return {a: (v["action"], v["whole_lot_quantity"]) for a, v in row["requested_orders"].items()}
    def fill_signature(row):
        return {a: v["filled_quantity"] * (1 if v["side"] == "buy" else -1)
                for a, v in row["execution"].items()}
    role_counts = {}
    for role in ("aggressive", "conservative", "institutional"):
        keys = [k for k in nrows if nrows[k]["role"] == role]
        role_counts[role] = {
            "decisions": len(keys),
            "target_changed": sum(nrows[k]["desired_weights"] != wrows[k]["desired_weights"] for k in keys),
            "request_changed": sum(request_signature(nrows[k]) != request_signature(wrows[k]) for k in keys),
            "fill_changed": sum(fill_signature(nrows[k]) != fill_signature(wrows[k]) for k in keys),
            "with_text_waits": sum(wrows[k]["gates"]["waiting_applied"] for k in keys),
            "with_text_liquidations": sum(wrows[k]["gates"]["risk_liquidation_priority"] for k in keys),
        }
    return {"case_id": with_text["case_id"], "seed": with_text["seed"], "role_counts": role_counts,
            "behavior_changed": behavioral_signature(no_text) != behavioral_signature(with_text),
            "final_prices_no_text": no_text["model_result"]["summary"]["final_prices_minor"],
            "final_prices_with_text": with_text["model_result"]["summary"]["final_prices_minor"]}


def write_json(path, value):
    with path.open("xb") as stream:
        stream.write(canonical(value) + b"\n")


def run_study(config, output):
    initial_protocol_sha = sha(Path(config))
    plan = load_plan(config)
    output.mkdir(parents=True, exist_ok=False)
    # Context is audited separately and remains disabled in all simulations.
    source_audit = audit_sources(ROOT / plan["context_catalog"])
    catalog = json.loads((ROOT / plan["context_catalog"]).read_text(encoding="utf-8"))
    context = {"catalog_sha256": sha(ROOT / plan["context_catalog"]), "source_audit": source_audit,
               "historical_event_signal_applied": False,
               "events": [{"event_id": e["event_id"], "publication": e["publication"],
                           "scope": e["scope"], "use_policy": e["use_policy"]}
                          for e in catalog["events"]]}
    write_json(output / "historical_context_readiness.json", context)
    write_json(output / "frozen_plan.json", plan)
    pairs, artifacts, counters = [], {}, Counter()
    for case in plan["cases"]:
        for seed in plan["seeds"]:
            runs = {}
            for mode in plan["text_modes"]:
                run = run_one(plan, case, seed, mode)
                name = f"{case['case_id']}_seed{seed}_text{int(mode)}.json.gz"
                with (output / name).open("xb") as stream:
                    with gzip.GzipFile(fileobj=stream, mode="wb", mtime=0, filename="") as packed:
                        packed.write(canonical(run) + b"\n")
                # Reopen the saved payload and recheck the explanation layer.
                with gzip.open(output / name, "rt", encoding="utf-8") as stream:
                    reopened = json.load(stream)
                if reopened != run:
                    raise ValueError("saved full probe path changed")
                audit_explanations(reopened["decision_explanations"], reopened["model_result"])
                runs[mode] = run
                artifacts[name] = sha(output / name)
                counters.update(paths=1, portfolio_days=run["audit"]["independent_portfolio_days"],
                    asset_calls=run["audit"]["independent_asset_calls"],
                    decision_explanations=run["audit"]["decisions"],
                    asset_explanations=run["audit"]["asset_explanations"],
                    dense_asset_calls=run["audit"]["dense_first_day_asset_calls"])
            pair = summarize_pair(runs[False], runs[True])
            if case["case_id"] == "neutral" and pair["behavior_changed"]:
                raise ValueError("neutral no-information control changed behavior")
            pairs.append(pair)
        print(json.dumps({"sealed_case": case["case_id"], "paths_completed": counters["paths"]}), flush=True)
    # Validate the same frozen inputs again after all cases have run.
    load_plan(config)
    if sha(Path(config)) != initial_protocol_sha:
        raise ValueError("protocol changed during execution")
    expected_counts = {**dict(counters), "paired_conditions": len(pairs)}
    if expected_counts != plan["expected_counts"]:
        raise ValueError("complete registered probe scope was not executed")
    summary = {"status": "COMPLETE_SYNTHETIC_THREE_ROLE_DECISION_TRACES_AND_SETTLEMENT_PROBES",
        "version": VERSION, "protocol_sha256": initial_protocol_sha, "counts": dict(counters),
        "pairs": pairs, "data_kind": "synthetic", "llm_outputs_used": False,
        "historical_event_signal_applied": False, "empirical_calibration": False,
        "scope": "Decision explanation and model checks; not a historical event reproduction or LLM evaluation."}
    write_json(output / "summary.json", summary)
    artifacts.update({name: sha(output / name) for name in (
        "summary.json", "frozen_plan.json", "historical_context_readiness.json")})
    write_json(output / "manifest.json", {"version": VERSION, "protocol_sha256": sha(Path(config)),
        "artifacts": artifacts, "bindings": plan["bindings"], "counts": dict(counters)})
    print(json.dumps({"status": summary["status"], "counts": dict(counters)}), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run_study(args.config, args.output_dir)


if __name__ == "__main__":
    main()
