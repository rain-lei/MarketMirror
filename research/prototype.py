"""Read, inspect and verify the small MarketMirror mechanism research prototype."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "research/configs/mechanism_prototype_evidence_2026_v2.json"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def local(name):
    path = (ROOT / name).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError("Evidence path is outside the project")
    return path


def load_inventory():
    inventory = read(EVIDENCE)
    if inventory["schema_version"] != "mechanism-prototype-offline-evidence-v1":
        raise ValueError("Unsupported prototype evidence inventory")
    for name, expected in inventory["bindings"].items():
        if sha(local(name)) != expected:
            raise ValueError("Recorded protocol, receipt or verification changed: " + name)
    return inventory


def verify(study_names):
    inventory = load_inventory()
    checks = {}
    unique_paths = {}
    for name in study_names:
        study = inventory["studies"][name]
        cfg = read(local(study["config"]))
        output = local(study["output"])
        manifest = read(output / "manifest.json")
        verification = read(output / "independent_verification.json")
        if (manifest["protocol_sha256"] != sha(local(study["config"]))
                or verification["protocol_sha256"] != sha(local(study["config"]))
                or verification["status"] != study["verification_status"]
                or read(output / "frozen_plan.json") != cfg):
            raise ValueError("Protocol or independent verification differs: " + name)
        if manifest["bindings"] != cfg["bindings"]:
            raise ValueError("Frozen artifact bindings differ: " + name)
        paths = {**cfg["bindings"], **manifest.get("references", {}),
                 **{(output / p).relative_to(ROOT).as_posix(): v for p, v in manifest["artifacts"].items()}}
        for path, expected in paths.items():
            if path in unique_paths and unique_paths[path] != expected:
                raise ValueError("Conflicting recorded source hashes: " + path)
            if path not in unique_paths:
                if sha(local(path)) != expected:
                    raise ValueError("Source, implementation or raw artifact changed: " + path)
                unique_paths[path] = expected
        checks[name] = {"status": "ARCHIVED_EVIDENCE_HASHES_VERIFIED", "verified_references": len(paths),
                        "independent_verification": verification["status"]}
    return {"studies": checks, "unique_files_verified": len(unique_paths),
            "online_model_calls": 0, "new_market_simulations": 0,
            "historical_market_fidelity_or_prediction_proven": False}


def status():
    inventory = load_inventory()
    rows = []
    for name, study in inventory["studies"].items():
        out = local(study["output"])
        summary = read(out / "summary.json")
        verification = read(out / "independent_verification.json")
        rows.append({"study": name, "counts": summary["counts"], "recorded_audit": verification["status"],
                     "all_raw_file_hashes_checked_in_this_command": False})
    return {"scope": "可解释、可复现的三类 Agent 机制研究原型", "studies": rows,
        "facts": {"distinct_source_cases": 6, "role_parameters_are_assumptions": True,
                  "cases_are_development_examples": True, "original_v6_semantic_gate_passed": False,
                  "resource_batch_requires_separate_final_audit": True},
        "verify_command": "python -B -m research.prototype verify",
        "risk_command": "python -B -m research.prototype risk --case high_risk"}


def show_risk(case_name, seed_summary):
    verify(["risk"])
    inventory = load_inventory()
    study = inventory["studies"]["risk"]
    summary = read(local(study["output"]) / "summary.json")
    source_cfg = read(local(inventory["studies"]["source_cases"]["config"]))
    aliases = {"pbc": 0, "wuhan": 1, "calendar": 2, "qa1": 3, "qa2": 4, "qa3": 5}
    case_id = source_cfg["case_ids"][aliases[case_name]] if case_name in aliases else case_name
    rows = [r for r in summary["role_aggregates"] if case_name == "all" or r["case_id"] == case_id]
    markets = [r for r in summary["market_aggregates"] if case_name == "all" or r["case_id"] == case_id]
    if not rows:
        raise ValueError("Use a source-case alias, a registered probe such as high_risk, or all")
    if seed_summary == "median":
        rows = [{**{k: r[k] for k in ("study", "case_id", "mode", "duration_steps", "role")},
                 "metric_medians": {k: v["median"] for k, v in r["metrics"].items()},
                 "maxima_medians": {k: v["median"] for k, v in r["maxima"].items()},
                 "counts_over_five_seeds": r["counts"]} for r in rows]
    return {"case_id": case_id, "counts": summary["counts"], "role_metrics": rows, "market_metrics": markets,
            "meaning": "模型账本中的收益、收盘回撤、决策步波动、持仓风险及实际成交；没有真实预测能力结论。",
            "seed_ranges_are_not_confidence_intervals": True,
            "risk_request_is_not_completed_liquidation": True,
            "high_risk_probe_uses_assumed_covariance_floor_not_a_large_price_drop": True}


def show_case(case_name, seed, mode, kind, strength, context):
    inventory = load_inventory()
    verify(["fixed_state" if kind == "fixed" else "source_cases"])
    study = inventory["studies"]["source_cases"]
    cfg = read(local(study["config"]))
    aliases = {"pbc": 0, "wuhan": 1, "calendar": 2, "qa1": 3, "qa2": 4, "qa3": 5}
    index = aliases.get(case_name)
    if index is None:
        try:
            index = cfg["case_ids"].index(case_name)
        except ValueError as exc:
            raise ValueError("Use pbc, wuhan, calendar, qa1, qa2, qa3 or a registered case ID") from exc
    if seed not in cfg["seeds"]:
        raise ValueError("Use one of the five registered seeds: 7, 11, 23, 47, 89")
    if kind == "fixed":
        out = local(inventory["studies"]["fixed_state"]["output"])
        with gzip.open(out / f"case{index}_seed{seed}_fixed_state.json.gz", "rt", encoding="utf-8") as stream:
            raw = json.load(stream)
        groups = [g for g in raw["groups"] if g["context"] == context and g["checkpoint"] == "first_visible"
                  and (context == "common_role_state" or g["actor"].endswith("_00"))]
        variant = f"{mode}:{strength:g}"
        rows = []
        for group in groups:
            matches = [r for r in group["rows"] if r["variant"] == variant]
            if len(matches) != 1:
                raise ValueError("Keywords and no-text use strength 1; reviewed facts use 0.5, 1 or 1.5")
            row = matches[0]
            rows.append({"role": group["role"], "actor": group["actor"], "state_sha256": group["state_sha256"],
                "desired_total_weight": sum(row["decision"]["desired_weights"].values()),
                "belief_terms": row["explanation"]["belief_terms"],
                "requests": row["explanation"]["requested_orders"], "gates": row["explanation"]["gates"],
                "actual_fills": None})
        return {"case_id": raw["case_id"], "seed": seed, "kind": kind, "context": context, "variant": variant,
                "cutoff_date": groups[0]["state"]["signal_cutoff_date"], "trade_date": groups[0]["state"]["trade_date"],
                "rows": rows, "meaning": "固定状态下未执行的交易请求；没有模拟或推断成交。"}
    if strength != 1:
        raise ValueError("Full archived paths use the registered strength 1")
    with gzip.open(local(study["output"]) / f"case{index}_seed{seed}_{mode}.json.gz", "rt", encoding="utf-8") as stream:
        raw = json.load(stream)
    first = min(r["step"] for r in raw["source_links"] if r["source_visible"])
    rows = [{"actor": r["actor"], "role": r["role"], "desired_total_weight": sum(r["desired_weights"].values()),
             "requests": r["requested_orders"], "actual_execution": r["execution"], "gates": r["gates"]}
            for r in raw["decision_explanations"] if r["session"] == first and r["actor"].endswith("_00")]
    return {"case_id": raw["case_id"], "seed": seed, "kind": kind, "mode": mode,
            "source_link": raw["source_links"][first], "rows": rows,
            "meaning": "已归档合成市场中的真实账本成交；角色可能有不同的前期状态。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="查看已归档研究状态，原始文件全量哈希另用 verify")
    verification = commands.add_parser("verify", help="复核全部协议、来源与原始产物哈希，无在线调用")
    verification.add_argument("--study", choices=("all", "source_cases", "fixed_state", "duration", "risk"), default="all")
    risk = commands.add_parser("risk", help="查看已归档模型收益、风险超限和实际成交，不新增仿真")
    risk.add_argument("--case", default="high_risk")
    risk.add_argument("--seed-summary", choices=("median", "ranges"), default="median")
    case = commands.add_parser("case", help="解释一个固定来源案例的三类决策")
    case.add_argument("--case", default="wuhan")
    case.add_argument("--seed", type=int, default=7)
    case.add_argument("--mode", choices=("no_text", "keywords", "reviewed_llm", "llm_asset_placebo"), default="reviewed_llm")
    case.add_argument("--kind", choices=("fixed", "path"), default="fixed")
    case.add_argument("--strength", type=float, choices=(0.5, 1.0, 1.5), default=1.0)
    case.add_argument("--context", choices=("common_role_state", "within_actor"), default="common_role_state")
    args = parser.parse_args()
    if args.command == "status":
        result = status()
    elif args.command == "verify":
        result = verify(["source_cases", "fixed_state", "duration", "risk"] if args.study == "all" else [args.study])
    elif args.command == "risk":
        result = show_risk(args.case, args.seed_summary)
    else:
        result = show_case(args.case, args.seed, args.mode, args.kind, args.strength, args.context)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
