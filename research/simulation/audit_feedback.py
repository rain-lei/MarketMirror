"""Reconstruct feedback experiments from bilateral trades and check causal inputs."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from collections import Counter
from decimal import Decimal
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .agents import AgentParameters
from .audit_auction import audit_day
from .feedback_auction import participants
from .feedback_experiment import load_summary, verified_inputs

VERSION = "feedback-trade-and-information-audit-v1"


def _read_ledger(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def check_feedback(record: dict, history: list[dict], settings: dict) -> None:
    """Recompute a fixed-window sample variance without the simulation helper."""
    available = [r for r in history if r["trade_date"] <= record["signal_cutoff_date"]]
    returns = [r["price_after_minor"] / r["price_before_minor"] - 1 for r in available]
    window = settings["volatility_sessions"]
    values = ([0.0] * window + returns)[-window:]
    mean = sum(values) / window
    variance = sum((value - mean)**2 for value in values) / (window - 1)
    volatility = max(settings["volatility_floor"], min(1.0, math.sqrt(variance)))
    signal = math.tanh(sum(values[-settings["momentum_sessions"]:]) / (volatility * math.sqrt(settings["momentum_sessions"])))
    claimed = record["feedback"]
    digest = hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    if (not math.isclose(claimed["market_signal"], signal, rel_tol=1e-12, abs_tol=1e-12)
            or not math.isclose(claimed["estimated_volatility"], volatility, rel_tol=1e-12, abs_tol=1e-12)
            or claimed["visible_closes"] != len(available)
            or claimed["latest_close_date"] != (available[-1]["trade_date"] if available else None)
            or claimed["window_return_sha256"] != digest or claimed["warmup_missing"] != max(0, window - len(available))):
        raise ValueError("feedback differs from already reconstructed own-price history")


def audit_directory(directory: Path, config_path: Path, output_dir: Path, progress=None) -> dict:
    directory, config_path, output_dir = [p.resolve() for p in (directory, config_path, output_dir)]
    summary = load_summary(directory)
    manifest_path = directory / "feedback_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    initial_bindings = {p: file_sha256(p) for p in (manifest_path, config_path, Path(__file__))}
    cfg, parent, base, _, _, inputs, steps = verified_inputs(config_path)
    if manifest["inputs"] != inputs or summary["run_id"] != cfg["run_id"]:
        raise ValueError("feedback audit config or source bindings differ")
    if (output_dir == directory or output_dir in directory.parents or directory in output_dir.parents
            or any(output_dir == Path(p) or output_dir in Path(p).parents for p in inputs)):
        raise ValueError("audit output must be separate from inputs")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty feedback audit directory")
    results = json.loads((directory / "feedback_results.json").read_text(encoding="utf-8"))
    scenarios = {s["scenario_id"]: s for s in cfg["scenarios"]}
    expected = {(stock, s, response, enabled) for stock in steps for s in scenarios for response in cfg["quote_response_bps"] for enabled in (False, True)}
    paths = {(p["stock_code"], p["scenario_id"], p["quote_response_bps"], p["use_text"]): p for p in results["path_summaries"]}
    if len(paths) != len(results["path_summaries"]) or set(paths) != expected or summary["paths"] != len(expected):
        raise ValueError("feedback path coverage is invalid")
    role_agents = [AgentParameters(**p) for p in base["agents"]]
    specs = {sid: {a.name: {"parameters": a.__dict__, "profile": profile} for a, profile in participants(role_agents, s)} for sid, s in scenarios.items()}
    if results["participant_specs"] != specs:
        raise ValueError("participant parameters differ from the declared cohort")
    states, histories, hashers, counts, aggregates, participant_totals = {}, {}, {}, Counter(), {}, {}
    count, traded, halted, endogenous = 0, 0, 0, 0
    for record in _read_ledger(directory / "feedback_ledger.jsonl.gz"):
        key = tuple(record.pop(field) for field in ("stock_code", "scenario_id", "quote_response_bps", "use_text"))
        if key not in paths:
            raise ValueError("feedback ledger has an undeclared path")
        stock, sid, response, enabled = key
        scenario = scenarios[sid]
        if key not in states:
            accounts = {name: {"cash_minor": int(Decimal(str(spec["parameters"]["initial_cash"])) * 100),
                              "shares": scenario["inventory"][int(name.rsplit("_", 1)[1])],
                              "sellable_shares": scenario["inventory"][int(name.rsplit("_", 1)[1])]}
                        for name, spec in specs[sid].items()}
            states[key] = {"accounts": accounts, "price_minor": parent["venue"]["price_start_minor"], "fee_pool_minor": 0,
                           "initial_cash_minor": sum(a["cash_minor"] for a in accounts.values()), "initial_shares": sum(a["shares"] for a in accounts.values())}
            histories[key], hashers[key], aggregates[key] = [], hashlib.sha256(b"["), Counter()
            participant_totals[key] = {name: {"initial_wealth": a["cash_minor"] + a["shares"] * parent["venue"]["price_start_minor"],
                                             "peak": a["cash_minor"] + a["shares"] * parent["venue"]["price_start_minor"],
                                             "max_drawdown": 0.0, "trades": 0, "fees_minor": 0, "risk_breach_sessions": 0}
                                       for name, a in accounts.items()}
        session = counts[key]
        if session >= len(steps[stock]):
            raise ValueError("feedback ledger contains too many sessions")
        source = steps[stock][session]
        if any(record[field] != source[field] for field in ("trade_date", "signal_cutoff_date", "execution_reference_date")):
            raise ValueError("feedback dates differ from the verified source calendar")
        text, uncertainty = (source["text_signal"], source["text_uncertainty"]) if enabled else (0.0, 0.0)
        if (record["text_signal_used"] != text or record["text_uncertainty_used"] != uncertainty
                or record["text_evidence"] != (source["text_evidence"] if enabled else "text:disabled")
                or record["auction"]["execution_available"] != source["execution_available"]):
            raise ValueError("text channel or suspension differs from the verified source")
        if scenario["feedback_mode"] == "endogenous":
            check_feedback(record, histories[key], cfg["feedback_parameters"])
            endogenous += 1
        elif record["feedback"] != {"market_signal": source["market_signal"], "estimated_volatility": source["estimated_volatility"]}:
            raise ValueError("conditioned observation differs from its verified source")
        if any(record[field] != record["feedback"][field] for field in ("market_signal", "estimated_volatility")):
            raise ValueError("top-level observation differs from the declared feedback")
        names = list(specs[sid])
        order = (names if scenario["order_mode"] == "forward" else list(reversed(names)) if scenario["order_mode"] == "reverse" else
                 sorted(names, key=lambda name: (hashlib.sha256(f"{scenario['seed']}:{session}:{name}".encode()).hexdigest(), name)))
        if record["arrival_order"] != order or set(record["observations"]) != set(names):
            raise ValueError("arrival order or observation coverage differs")
        for item in record["auction"]["orders"]:
            if item["sequence"] != order.index(item["owner"]):
                raise ValueError("submitted order sequence differs from declared arrival")
        for name, spec in specs[sid].items():
            observation = record["observations"][name]
            profile = spec["profile"]
            signal = max(-1.0, min(1.0, record["market_signal"] * profile["momentum_loading"] + profile["market_bias"]))
            if (observation["step"] != session or observation["price"] != states[key]["price_minor"] / 100
                    or observation["market_signal"] != signal or observation["estimated_volatility"] != record["estimated_volatility"]
                    or observation["text_signal"] != text or observation["uncertainty"] != uncertainty
                    or observation["text_evidence"] != (source["text_evidence"] if enabled else "text:disabled")):
                raise ValueError("participant observation differs from the causal input and declared profile")
        states[key] = audit_day(record, states[key], parent["venue"], session)
        for item in record["auction"]["orders"]:
            participant_totals[key][item["owner"]]["trades"] += int(item["filled_quantity"] > 0)
            participant_totals[key][item["owner"]]["fees_minor"] += item["fee_minor"]
        for name, account in states[key]["accounts"].items():
            totals, parameters = participant_totals[key][name], specs[sid][name]["parameters"]
            wealth = account["cash_minor"] + account["shares"] * states[key]["price_minor"]
            totals["peak"] = max(totals["peak"], wealth)
            totals["max_drawdown"] = max(totals["max_drawdown"], 1 - wealth / totals["peak"])
            risk_cap = min(parameters["max_weight"], parameters["risk_budget"] / record["estimated_volatility"])
            if record["decisions"][name]["risk_weight_cap"] != risk_cap:
                raise ValueError("decision risk cap differs from the recorded causal volatility")
            totals["risk_breach_sessions"] += int(account["shares"] * states[key]["price_minor"] / wealth > risk_cap + 1e-9)
        histories[key].append({"trade_date": record["trade_date"], "price_before_minor": record["auction"]["price_before_minor"], "price_after_minor": states[key]["price_minor"]})
        if counts[key]:
            hashers[key].update(b", ")
        hashers[key].update(json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False).encode())
        counts[key] += 1
        count += 1
        auction = record["auction"]
        traded += int(auction["matched_volume"] > 0)
        halted += int(not auction["execution_available"])
        aggregates[key].update({"matched_volume": auction["matched_volume"], "trade_sessions": int(auction["matched_volume"] > 0),
                                "price_change_sessions": int(auction["price_before_minor"] != auction["price_after_minor"]),
                                "accepted_quantity": sum(o["accepted_quantity"] for o in auction["orders"]),
                                "requested_quantity": sum(o["quantity"] for o in auction["orders"]), "blocked_sessions": int(not auction["execution_available"])})
        if progress is not None and count % 20000 == 0:
            progress(count, summary["ledger_rows"])
    if set(states) != expected or count != summary["ledger_rows"]:
        raise ValueError("feedback ledger coverage is incomplete")
    for key, state in states.items():
        path = paths[key]
        hashers[key].update(b"]")
        if (counts[key] != len(steps[key[0]]) or counts[key] != path["sessions"] or hashers[key].hexdigest() != path["trace_sha256"]
                or state["price_minor"] != path["final_price_minor"] or state["fee_pool_minor"] != path["fee_pool_minor"]
                or any(path[field] != value for field, value in aggregates[key].items())):
            raise ValueError("reconstructed full path differs from the saved summary")
        for name, account in state["accounts"].items():
            saved = path["agent_summary"][name]
            totals = participant_totals[key][name]
            final_wealth = account["cash_minor"] + account["shares"] * state["price_minor"]
            if (any(saved[field] != value for field, value in account.items())
                    or saved["final_wealth_minor"] != final_wealth or saved["initial_wealth_minor"] != totals["initial_wealth"]
                    or saved["wealth_multiple"] != final_wealth / totals["initial_wealth"]
                    or any(saved[field] != totals[field] for field in ("max_drawdown", "trades", "fees_minor", "risk_breach_sessions"))):
                raise ValueError("final participant differs from trade reconstruction")
        for role in ("aggressive", "conservative", "institutional"):
            members = [name for name in state["accounts"] if specs[key[1]][name]["parameters"]["role"] == role]
            final = sum(state["accounts"][name]["cash_minor"] + state["accounts"][name]["shares"] * state["price_minor"] for name in members)
            initial = sum(participant_totals[key][name]["initial_wealth"] for name in members)
            if path["role_wealth_multiple"][role] != final / initial:
                raise ValueError("role wealth summary differs from reconstructed aggregate wealth")
    if (any(file_sha256(directory / name) != info["sha256"] for name, info in manifest["artifacts"].items())
            or any(file_sha256(p) != digest for p, digest in initial_bindings.items())
            or any(file_sha256(Path(p)) != digest for p, digest in inputs.items())):
        raise ValueError("feedback source, artifact or auditor changed during audit")
    result = {"pipeline_version": VERSION, "status": "passed", "experiment_id": summary["experiment_id"], "paths": len(states),
              "ledger_rows": count, "traded_sessions": traded, "halted_sessions": halted, "endogenous_input_rows": endogenous,
              "dense_tick_sweeps": traded, "checks": {"trade_reconstruction": True, "dense_price_scan": True,
              "own_price_feedback_causality": True, "source_text_and_halts": True, "cohort_and_arrival_order": True, "full_trace_and_summary": True}}
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "feedback_audit.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    binding = {"pipeline_version": VERSION, "run_manifest": str(manifest_path), "run_manifest_sha256": file_sha256(manifest_path),
               "config_path": str(config_path), "config_sha256": file_sha256(config_path), "code_sha256": file_sha256(Path(__file__)),
               "result_sha256": file_sha256(result_path)}
    (output_dir / "feedback_audit_manifest.json").write_text(json.dumps(binding, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def load_audit(directory: Path, audit_dir: Path) -> dict:
    manifest = json.loads((audit_dir / "feedback_audit_manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("pipeline_version") != VERSION or manifest["code_sha256"] != file_sha256(Path(__file__))
            or Path(manifest["run_manifest"]).resolve() != (directory / "feedback_manifest.json").resolve()
            or manifest["run_manifest_sha256"] != file_sha256(directory / "feedback_manifest.json")
            or manifest["config_sha256"] != file_sha256(Path(manifest["config_path"]))
            or manifest["result_sha256"] != file_sha256(audit_dir / "feedback_audit.json")):
        raise ValueError("feedback audit binding changed")
    result = json.loads((audit_dir / "feedback_audit.json").read_text(encoding="utf-8"))
    if result.get("status") != "passed" or not result.get("checks") or any(value is not True for value in result["checks"].values()):
        raise ValueError("feedback audit did not pass every check")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = audit_directory(args.directory, args.config, args.output_dir, lambda done, total: print(f"Audited {done}/{total} ledger rows", flush=True))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
