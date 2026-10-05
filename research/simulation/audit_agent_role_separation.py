"""Audit observed-return replay role separation without claiming calibration."""

from __future__ import annotations

import argparse
import itertools
import json
import math
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ..data_pipeline.provenance import file_sha256


VERSION = "observed-return-agent-role-separation-audit-v1"
ROOT = Path(__file__).resolve().parents[2]
MODULE = Path(__file__).resolve()
RUNS = {
    "2018_h1": ROOT / "research_outputs/observed_2018/historical_replay",
    "2020_q1": ROOT / "research_outputs/observed_2020/historical_replay",
    "2020_later": ROOT / "research_outputs/observed_2020/historical_replay_later",
}
OUTPUT_DIR = ROOT / "research_outputs/agent_role_separation_2018_2020_v1"
RESULT_NAME = "role_separation.json"
REPORT_NAME = "role_separation_report.md"
MANIFEST_NAME = "role_separation_manifest.json"
ROLES = ("aggressive", "conservative", "institutional")


def load_run(directory: Path) -> tuple[dict, dict]:
    manifest_path = directory / "historical_replay_manifest.json"
    result_path = directory / "historical_replay.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("pipeline_version") != "historical-price-taking-replay-v1"
            or manifest.get("artifacts", {}).get(result_path.name, {}).get("sha256")
            != file_sha256(result_path)):
        raise ValueError(f"historical replay archive hash differs: {directory.name}")
    for filename, expected in manifest.get("artifacts", {}).items():
        if file_sha256(directory / filename) != expected["sha256"]:
            raise ValueError(f"historical replay artifact differs: {filename}")
    for filename, expected in manifest.get("inputs", {}).items():
        if file_sha256(Path(filename)) != expected:
            raise ValueError(f"historical replay input differs: {Path(filename).name}")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if (result.get("replay_id") != manifest.get("replay_id")
            or result.get("pipeline_version") != manifest.get("pipeline_version")
            or set(result.get("agents", {})) != set(ROLES)):
        raise ValueError("historical replay identity or three roles differ")
    return result, manifest


def analyze_run(result: dict) -> dict:
    roles = {name: params["role"] for name, params in result["agents"].items()}
    if set(roles.values()) != set(ROLES) or len(roles) != 3:
        raise ValueError("three distinct role identities are required")
    by_role = {role: {"actions": Counter(), "reason_codes": Counter(),
                      "signal_action_changes": 0, "signal_target_changes": 0,
                      "signal_fill_changes": 0, "risk_budget_breach_days": 0,
                      "market_days": 0} for role in ROLES}
    pairwise = {"|".join(pair): 0 for pair in itertools.combinations(ROLES, 2)}
    company_days = all_same_actions = signal_nonzero_days = 0
    per_company = {}
    for code, paths in result["paths"].items():
        active, control = paths["market_signal"], paths["zero_signal"]
        active_trace, control_trace = active["trace"], control["trace"]
        if not active_trace or len(active_trace) != len(control_trace):
            raise ValueError("market and zero-signal traces have different coverage")
        seen_dates: set[str] = set()
        company_same = company_signal_changes = 0
        for market_day, zero_day in zip(active_trace, control_trace):
            date = market_day["trade_date"]
            if (date != zero_day["trade_date"] or date in seen_dates
                    or set(market_day["agents"]) != set(roles)
                    or set(zero_day["agents"]) != set(roles)):
                raise ValueError("paired replay day, role or date differs")
            seen_dates.add(date)
            company_days += 1
            signal_nonzero_days += int(abs(market_day["market_signal"]) > 1e-12)
            actions = {}
            any_signal_action_change = False
            for name, role in roles.items():
                market_row, zero_row = market_day["agents"][name], zero_day["agents"][name]
                if market_row["role"] != role or zero_row["role"] != role:
                    raise ValueError("role label differs from configured agent")
                market_decision, zero_decision = market_row["decision"], zero_row["decision"]
                action = market_decision["action"]
                if action not in {"buy", "sell", "hold"}:
                    raise ValueError("unsupported replay action")
                actions[role] = action
                bucket = by_role[role]
                bucket["market_days"] += 1
                bucket["actions"][action] += 1
                bucket["reason_codes"].update(market_decision["reason_codes"])
                action_changed = action != zero_decision["action"]
                bucket["signal_action_changes"] += int(action_changed)
                bucket["signal_target_changes"] += int(
                    not math.isclose(market_decision["target_weight"],
                                     zero_decision["target_weight"],
                                     rel_tol=0, abs_tol=1e-10))
                bucket["signal_fill_changes"] += int(
                    not math.isclose(market_row["filled_shares"],
                                     zero_row["filled_shares"],
                                     rel_tol=0, abs_tol=1e-8))
                any_signal_action_change |= action_changed
            company_signal_changes += int(any_signal_action_change)
            same = len(set(actions.values())) == 1
            all_same_actions += int(same)
            company_same += int(same)
            for left, right in itertools.combinations(ROLES, 2):
                pairwise[f"{left}|{right}"] += int(actions[left] != actions[right])
        for name, role in roles.items():
            by_role[role]["risk_budget_breach_days"] += active["summary"][name][
                "risk_budget_breach_days"]
        per_company[code] = {"days": len(active_trace),
                             "all_same_action_days": company_same,
                             "at_least_one_signal_action_change_days": company_signal_changes}
    if company_days == 0:
        raise ValueError("replay contains no company-days")
    return {
        "company_days": company_days,
        "signal_nonzero_company_days": signal_nonzero_days,
        "all_same_action_company_days": all_same_actions,
        "at_least_two_actions_differ_company_days": company_days - all_same_actions,
        "at_least_two_actions_differ_fraction": (company_days - all_same_actions) / company_days,
        "pairwise_action_disagreement_company_days": pairwise,
        "by_role": {role: {**row, "actions": dict(row["actions"]),
                           "reason_codes": dict(row["reason_codes"])}
                    for role, row in by_role.items()},
        "per_company": per_company,
    }


def compute(runs: dict[str, Path] = RUNS) -> tuple[dict, dict[str, str]]:
    inputs: dict[str, str] = {}
    periods = {}
    for name, directory in runs.items():
        result, _ = load_run(directory)
        periods[name] = analyze_run(result)
        for filename in ("historical_replay_manifest.json", "historical_replay.json"):
            path = directory / filename
            inputs[str(path.resolve())] = file_sha256(path)
    return {"pipeline_version": VERSION,
            "interpretation": "Preset three-role action separation on observed-return, price-taking replay; no behavioral calibration or text-event reproduction.",
            "periods": periods}, inputs


def render_report(result: dict) -> str:
    lines = ["# 2018/2020 三类 Agent 行动区分度审计", "",
             "本审计只观察现有历史收益路径回放中的预设主体行动。价格由真实已实现收益外生推进，"
             "Agent 不改变价格；这些回放没有政策文本信号、真实投资者类别、持仓或盘口。", "",
             "| 时段 | 公司×交易日 | 三类行动至少两种不同 | 全部行动相同 | 非零市场动量信号日 |",
             "|---|---:|---:|---:|---:|"]
    for name, period in result["periods"].items():
        lines.append(f"| {name} | {period['company_days']} | "
                     f"{period['at_least_two_actions_differ_company_days']} "
                     f"({period['at_least_two_actions_differ_fraction']:.1%}) | "
                     f"{period['all_same_action_company_days']} | "
                     f"{period['signal_nonzero_company_days']} |")
    lines.extend(["", "下表的‘信号改行动’逐角色比较同股票、同交易日的市场动量与零信号路径；"
                  "它是模型内机制差异，不是政策冲击识别或收益预测能力。", "",
                  "| 时段 | 角色 | 买/卖/持有日 | 信号改行动日 | 信号改目标权重日 | 信号改成交股数日 |",
                  "|---|---|---:|---:|---:|---:|"])
    for name, period in result["periods"].items():
        for role in ROLES:
            row = period["by_role"][role]
            actions = row["actions"]
            lines.append(f"| {name} | {role} | "
                         f"{actions.get('buy', 0)}/{actions.get('sell', 0)}/"
                         f"{actions.get('hold', 0)} | "
                         f"{row['signal_action_changes']} | {row['signal_target_changes']} | "
                         f"{row['signal_fill_changes']} |")
    lines.extend(["", "三类主体的差异来自固定参数和账户状态；即使行动不同，也不能说已复现"
                  "2018 去杠杆或 2020 疫情下真实交易主体的决策路径。要检验那项主张，仍需可见"
                  "事件信号、与主体类别关联的交易/持仓或订单流，以及独立参数校准和留出检验。", ""])
    return "\n".join(lines)


def write_report(output_dir: Path = OUTPUT_DIR, runs: dict[str, Path] = RUNS) -> dict:
    output_dir = output_dir.resolve()
    outputs = (ROOT / "research_outputs").resolve()
    if (output_dir == outputs or outputs not in output_dir.parents
            or any(output_dir == path or output_dir in path.parents or path in output_dir.parents
                   for path in (run.resolve() for run in runs.values()))
            or output_dir.exists() and any(output_dir.iterdir())):
        raise ValueError("role audit output must be a new separate research_outputs child")
    result, inputs = compute(runs)
    code = {MODULE.name: file_sha256(MODULE)}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        stage = Path(temporary)
        (stage / RESULT_NAME).write_text(
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8")
        (stage / REPORT_NAME).write_text(render_report(result), encoding="utf-8")
        manifest = {"pipeline_version": VERSION,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "input_sha256": inputs, "code_sha256": code,
                    "artifacts": {path.name: {"sha256": file_sha256(path)}
                                  for path in stage.iterdir()}}
        (stage / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if (file_sha256(MODULE) != code[MODULE.name]
                or any(file_sha256(Path(name)) != expected for name, expected in inputs.items())):
            raise RuntimeError("role audit sources or code changed during writing")
        for path in stage.iterdir():
            path.replace(output_dir / path.name)
    return result


def audit_existing(output_dir: Path = OUTPUT_DIR, runs: dict[str, Path] = RUNS) -> dict:
    output_dir = output_dir.resolve()
    manifest = json.loads((output_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    result, inputs = compute(runs)
    code = {MODULE.name: file_sha256(MODULE)}
    if (manifest.get("pipeline_version") != VERSION
            or manifest.get("input_sha256") != inputs
            or manifest.get("code_sha256") != code
            or set(manifest.get("artifacts", {})) != {RESULT_NAME, REPORT_NAME}):
        raise ValueError("role audit manifest differs from current sources")
    expected = {RESULT_NAME: json.dumps(result, ensure_ascii=False, indent=2,
                                        allow_nan=False) + "\n",
                REPORT_NAME: render_report(result)}
    for name, content in expected.items():
        path = output_dir / name
        if (file_sha256(path) != manifest["artifacts"][name]["sha256"]
                or path.read_text(encoding="utf-8") != content):
            raise ValueError(f"role audit artifact differs from recomputation: {name}")
    return {"company_days": {name: period["company_days"]
                             for name, period in result["periods"].items()},
            "source_bound_recomputation": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = (audit_existing(args.output_dir) if args.audit_existing else
              write_report(args.output_dir))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
