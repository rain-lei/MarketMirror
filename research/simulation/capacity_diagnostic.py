"""Compare illustrative replay orders with observed daily traded amount at fixed AUM scales."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..data_pipeline.market_activity import VERSION as ACTIVITY_VERSION
from ..data_pipeline.provenance import file_sha256
from .historical_replay import VERSION as REPLAY_VERSION


VERSION = "historical-capacity-diagnostic-v1"
THRESHOLDS = (0.01, 0.05)


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {"run_id", "data_kind", "replay_manifest", "activity_manifest",
                "aum_cny_per_agent"}
    if not isinstance(config, dict) or set(config) != required:
        raise ValueError("capacity config has missing or unknown fields")
    if config["data_kind"] != "observed_posthoc" or not isinstance(config["run_id"], str) or not config["run_id"]:
        raise ValueError("capacity diagnostic requires an observed post-hoc run")
    for key in ("replay_manifest", "activity_manifest"):
        if not isinstance(config[key], str) or not config[key]:
            raise ValueError(f"{key} must point to a manifest")
    scales = config["aum_cny_per_agent"]
    if (not isinstance(scales, list) or not scales
            or any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0 or value > 1e13
                   for value in scales)
            or len(set(scales)) != len(scales) or scales != sorted(scales)):
        raise ValueError("AUM scales must be unique increasing positive finite CNY amounts")
    return config


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[min(lower + 1, len(ordered) - 1)] * weight


def compare_capacity(replay: dict[str, Any], activity: dict[tuple[str, str], dict[str, Any]],
                     scales: list[int | float]) -> dict[str, Any]:
    """Scale each agent's traded fraction of initial capital to hypothetical CNY AUM."""
    agents = replay["agents"]
    if not agents or any(not math.isfinite(float(row["initial_cash"])) or row["initial_cash"] <= 0
                         for row in agents.values()):
        raise ValueError("replay requires positive finite illustrative initial capital")
    results = []
    for code, paths in sorted(replay["paths"].items()):
        if set(paths) != {"market_signal", "zero_signal"}:
            raise ValueError("capacity diagnostic requires both replay controls")
        for path_name, path in sorted(paths.items()):
            if not path["trace"]:
                raise ValueError("replay path has no trading sessions")
            for aum in scales:
                daily = []
                seen_dates = set()
                for step in path["trace"]:
                    day = step["trade_date"]
                    if day in seen_dates:
                        raise ValueError("replay contains a duplicate trade date")
                    seen_dates.add(day)
                    observed = activity.get((code, day))
                    if observed is None:
                        raise ValueError(f"observed activity is missing for {code} {day}")
                    reference_price = float(step["price_index_before"])
                    if not math.isfinite(reference_price) or reference_price <= 0:
                        raise ValueError("replay reference price must be positive and finite")
                    if set(step["agents"]) != set(agents):
                        raise ValueError("replay trace agent identities differ from config")
                    gross_fraction = sum(abs(float(row["filled_shares"])) * reference_price
                                         / float(agents[name]["initial_cash"])
                                         for name, row in step["agents"].items())
                    if not math.isfinite(gross_fraction):
                        raise ValueError("replay fill implies nonfinite traded fraction")
                    gross_cny = gross_fraction * float(aum)
                    amount = float(observed["amount_cny"])
                    trading = observed["trading_status"] == "trading"
                    if (not math.isfinite(amount) or amount < 0
                            or (trading and amount <= 0) or (not trading and amount != 0)):
                        raise ValueError("observed daily amount and trading status disagree")
                    ratio = gross_cny / amount if trading else None
                    daily.append({"trade_date": day, "observed_amount_cny": amount,
                                  "hypothetical_gross_cny": gross_cny,
                                  "fraction_of_observed_amount": ratio,
                                  "trading_status": observed["trading_status"],
                                  "suspended_with_model_fill": not trading and gross_cny > 1e-8})
                ratios = [row["fraction_of_observed_amount"] for row in daily
                          if row["fraction_of_observed_amount"] is not None]
                results.append({"stock_code": code, "path": path_name,
                                "aum_cny_per_agent": aum, "sessions": len(daily),
                                "suspended_with_model_fill_days": sum(row["suspended_with_model_fill"] for row in daily),
                                "days_above_fraction": {str(threshold): sum(value > threshold for value in ratios)
                                                        for threshold in THRESHOLDS},
                                "median_fraction": percentile(ratios, 0.5),
                                "p95_fraction": percentile(ratios, 0.95),
                                "max_fraction": max(ratios) if ratios else None,
                                "daily": daily})
    return {"pipeline_version": VERSION,
            "interpretation": "Retrospective gross-order participation sensitivity under hypothetical CNY AUM per agent and per stock.",
            "results": results,
            "limitations": [
                "Observed same-day amount is used only after the fact; it was not known at the replay decision time.",
                "Total executed amount is not order-book depth, available liquidity, net order flow or investor-type activity.",
                "The replay price is a normalized return index; fills are converted via fraction of each agent's illustrative initial capital.",
                "Gross orders are summed without internal crossing between agents, so this is a conservative external-capacity proxy.",
                "Each stock is a separate scenario with the stated AUM per agent; do not add them as one funded portfolio.",
                "No price impact, queue position, bid-ask spread or slippage is inferred from these fractions."]}


def render_report(result: dict[str, Any]) -> str:
    lines = ["# 历史回放的成交容量诊断", "",
             "按每类 Agent 在**单只股票**上的假设资金规模，把原规则回放的成交比例换算成人民币，再与同日实际总成交额作事后比较。当天成交额未进入决策；比例不是可执行深度。", "",
             "| 股票 | 路径 | 每类 Agent 假设资金 | 交易日 | 参与比例中位数 | 95 分位 | 最大值 | 超过 1% 的天数 | 超过 5% 的天数 | 停牌仍成交 |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    def percent(value: float | None) -> str:
        return "—" if value is None else f"{value:.2%}"
    for row in result["results"]:
        lines.append(f"| {row['stock_code']} | {row['path']} | {row['aum_cny_per_agent']:,.0f} | {row['sessions']} | "
                     f"{percent(row['median_fraction'])} | {percent(row['p95_fraction'])} | {percent(row['max_fraction'])} | "
                     f"{row['days_above_fraction']['0.01']} | {row['days_above_fraction']['0.05']} | "
                     f"{row['suspended_with_model_fill_days']} |")
    lines += ["", "这只诊断假设资金规模下的容量敏感性，不是历史真实订单、成交保证或价格冲击校准。", ""]
    lines.extend(f"- {limitation}" for limitation in result["limitations"])
    return "\n".join(lines) + "\n"


def run_diagnostic(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    config = load_config(config_path)
    replay_manifest_path = (config_path.parent / config["replay_manifest"]).resolve()
    activity_manifest_path = (config_path.parent / config["activity_manifest"]).resolve()
    replay_path = replay_manifest_path.parent / "historical_replay.json"
    activity_path = activity_manifest_path.parent / "market_activity.csv"
    inputs = {path: file_sha256(path) for path in
              (config_path, replay_manifest_path, replay_path, activity_manifest_path, activity_path)}
    replay_manifest = json.loads(replay_manifest_path.read_text(encoding="utf-8"))
    activity_manifest = json.loads(activity_manifest_path.read_text(encoding="utf-8"))
    if (replay_manifest["pipeline_version"] != REPLAY_VERSION
            or activity_manifest["pipeline_version"] != ACTIVITY_VERSION
            or inputs[replay_path] != replay_manifest["artifacts"][replay_path.name]["sha256"]
            or inputs[activity_path] != activity_manifest["artifacts"][activity_path.name]["sha256"]
            or activity_manifest["units"]["amount_cny"] != "CNY"):
        raise ValueError("observed replay or activity source differs from its manifest")
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    if (replay["data_kind"] != "observed" or activity_manifest["data_kind"] != "observed"
            or replay["market_dataset_id"] != activity_manifest["market_dataset_id"]
            or replay["replay_id"] != replay_manifest["replay_id"]):
        raise ValueError("replay and activity must describe the same observed market dataset")
    activity = {}
    with activity_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if set(reader.fieldnames or []) != {"trade_date", "stock_code", "provider_symbol",
                                            "volume_shares", "amount_cny", "trading_status"}:
            raise ValueError("activity CSV schema differs from the expected observed source")
        for row in reader:
            key = (row["stock_code"], row["trade_date"])
            if key in activity:
                raise ValueError("activity contains a duplicate stock-date")
            activity[key] = row
    result = compare_capacity(replay, activity, config["aum_cny_per_agent"])
    if any(file_sha256(path) != digest for path, digest in inputs.items()):
        raise RuntimeError("capacity input changed during calculation")
    if any(output_dir == path or output_dir in path.parents or path.parent in output_dir.parents
           for path in inputs):
        raise ValueError("capacity output must be separate from inputs")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty capacity output directory")
    code_hashes = {"capacity_diagnostic.py": file_sha256(Path(__file__)),
                   "historical_replay.py": file_sha256(Path(__file__).with_name("historical_replay.py")),
                   "market_activity.py": file_sha256(Path(__file__).parents[1] / "data_pipeline/market_activity.py")}
    identity = {"input_sha256": {str(path): digest for path, digest in inputs.items()},
                "code_sha256": code_hashes}
    result["run_id"] = config["run_id"]
    result["market_dataset_id"] = replay["market_dataset_id"]
    result["diagnostic_id"] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        staging = Path(temporary)
        (staging / "capacity_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (staging / "capacity_report.md").write_text(render_report(result), encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
                    "diagnostic_id": result["diagnostic_id"], **identity,
                    "artifacts": {path.name: {"sha256": file_sha256(path)} for path in staging.iterdir()}}
        (staging / "capacity_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run_diagnostic(args.config, args.output_dir)
    print(json.dumps({"diagnostic_id": result["diagnostic_id"],
                      "scenarios": len(result["results"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
