"""Paired reply-keyword attention sensitivity on the fixed Wuhan portfolio cohort.

Keyword hits are literal, directionless topic proxies. The two uncertainty
levels are explicit mechanism assumptions, not measured semantic confidence.
"""

from __future__ import annotations

import argparse
import gzip
import json
import platform
import statistics
import tempfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from ..semantic.keyword_baseline import VERSION as KEYWORD_VERSION, predict_item
from ..semantic.signal_validation import load_pack, read_jsonl, validate_predictions
from .portfolio_audit import audit_portfolio_day
from .portfolio_market import simulate_portfolio
from .semantic_memory_sensitivity import canonical_hash
from .semantic_signal_join import join_steps
from .wuhan_portfolio_baseline import (
    CODE_PATHS as BASE_CODE_PATHS,
    ROOT, _initial_audit_state, _load_inputs, load_summary,
)

VERSION = "wuhan-reply-keyword-attention-portfolio-v1"
CODE_PATHS = {**BASE_CODE_PATHS, **{name: ROOT / name for name in (
    "simulation/wuhan_portfolio_keyword.py", "semantic/keyword_baseline.py",
    "semantic/signal_validation.py", "semantic/annotation_pack.py",
    "data_pipeline/aggregate_qa_features.py")}}
ARTIFACTS = {"keyword_portfolio_summary.json", "keyword_portfolio_paths.json",
             "keyword_portfolio_ledger.jsonl.gz", "keyword_portfolio_report.md"}


def reply_attention_row(item: dict, prediction: dict, level: float) -> dict | None:
    """Exclude question-only hits from the company-reply channel."""
    if type(level) not in (int, float) or not 0 < level <= 1:
        raise ValueError("attention level must be in (0,1]")
    if prediction["item_id"] != item["item_id"] or prediction["source_text_sha256"] != item["source_text_sha256"]:
        raise ValueError("keyword prediction does not match the source item")
    events = prediction["events"]
    if not events:
        return None
    if len(events) != 1 or events[0]["direction"] != "unknown" or len(events[0]["evidence_spans"]) != 1:
        raise ValueError("keyword prediction is not a directionless single literal hit")
    if events[0]["evidence_spans"][0]["source"] != "reply":
        return None
    if item["stage"] != "reply":
        raise ValueError("reply attention requires a visible company reply")
    return {"stock_code": item["stock_code"], "item_id": item["item_id"],
            "available_at": item["available_at"], "text_signal": 0.0,
            "uncertainty": float(level)}


def _load(config_path: Path):
    config_path = config_path.resolve()
    settings = json.loads(config_path.read_text(encoding="utf-8"))
    required = {"run_id", "baseline_config", "baseline_directory", "annotation_pack",
                "keyword_directory", "attention_levels"}
    if (set(settings) != required or not all(isinstance(settings[k], str) and settings[k]
                                            for k in required - {"attention_levels"})
            or settings["attention_levels"] != [0.25, 1.0]):
        raise ValueError("keyword portfolio requires its fixed two-level configuration")
    paths = {key: (config_path.parent / settings[key]).resolve() for key in
             ("baseline_config", "baseline_directory", "annotation_pack", "keyword_directory")}
    cfg, replay, market, agents, joined, baskets, market_inputs = _load_inputs(paths["baseline_config"])
    baseline = load_summary(paths["baseline_directory"])
    base_manifest_path = paths["baseline_directory"] / "portfolio_no_text_manifest.json"
    base_manifest = json.loads(base_manifest_path.read_text(encoding="utf-8"))
    base_paths_path = paths["baseline_directory"] / "portfolio_no_text_paths.json"
    base_paths = json.loads(base_paths_path.read_text(encoding="utf-8"))
    if (base_manifest["inputs"] != market_inputs or baseline["market_dataset_id"] != market["market_dataset_id"]
            or baseline["baskets"] != baskets or baseline["cases"] != cfg["cases"]
            or baseline["dates"] != {"start": replay["start_date"], "end": replay["end_date"]}
            or len(base_paths) != len(baskets) * len(cfg["cases"])):
        raise ValueError("archived no-text baseline does not match this portfolio cohort")
    by_path = {row["path_id"]: row for row in base_paths}
    if len(by_path) != len(base_paths):
        raise ValueError("archived no-text path identities repeat")

    items, pack = load_pack(paths["annotation_pack"])
    pack_dir = paths["annotation_pack"]
    if (pack.get("universe_size") != len(joined)
            or pack.get("counts", {}).get("items") != len(items)
            or pack.get("counts", {}).get("companies_without_reply_snapshot") != len(joined) - len(items)
            or pack.get("snapshot_as_of") != "2020-01-22T23:59:59.999999+08:00"
            or {item["stock_code"] for item in items.values()} - set(joined)
            or len({item["stock_code"] for item in items.values()}) != len(items)):
        raise ValueError("annotation pack is not the complete fixed pre-event company snapshot")
    for name, digest in pack.get("input_sha256", {}).items():
        if file_sha256(Path(name)) != digest:
            raise ValueError("annotation pack source changed")
    for name, digest in pack.get("code_sha256", {}).items():
        if file_sha256(ROOT / "semantic" / name) != digest:
            raise ValueError("annotation pack source code changed")
    if pack["input_sha256"].get(str((config_path.parent / "wuhan_qna_active_universe_2020.json").resolve())) != market_inputs.get(str((config_path.parent / "wuhan_qna_active_universe_2020.json").resolve())):
        raise ValueError("annotation and market universes differ")

    keyword_dir = paths["keyword_directory"]
    keyword_manifest_path = keyword_dir / "keyword_manifest.json"
    keyword_manifest = json.loads(keyword_manifest_path.read_text(encoding="utf-8"))
    prediction_path = keyword_dir / "keyword_predictions.jsonl"
    if (keyword_manifest.get("pipeline_version") != KEYWORD_VERSION
            or keyword_manifest.get("pack_experiment_id") != pack["experiment_id"]
            or keyword_manifest.get("input_sha256") != {
                "annotation_manifest.json": file_sha256(pack_dir / "annotation_manifest.json"),
                "annotation_items.jsonl": file_sha256(pack_dir / "annotation_items.jsonl")}
            or keyword_manifest.get("code_sha256") != {
                name: file_sha256(ROOT / "semantic" / name)
                for name in ("keyword_baseline.py", "signal_validation.py")}
            or keyword_manifest.get("artifacts") != {
                "keyword_predictions.jsonl": {"sha256": file_sha256(prediction_path)}}):
        raise ValueError("keyword baseline source or artifact changed")
    predictions = read_jsonl(prediction_path)
    prediction_audit = validate_predictions(items, predictions)
    if (prediction_audit != keyword_manifest["validation"]
            or len(predictions) != len(items)
            or predictions != [predict_item(item) for item in items.values()]
            or sum(bool(row["events"]) for row in predictions) != keyword_manifest["candidate_events"]):
        raise ValueError("keyword baseline cannot be reproduced from visible items")
    source = {str(config_path): file_sha256(config_path), **market_inputs}
    for path in (base_manifest_path, base_paths_path,
                 paths["baseline_directory"] / "portfolio_no_text_summary.json",
                 pack_dir / "annotation_manifest.json", pack_dir / "annotation_items.jsonl",
                 keyword_manifest_path, prediction_path):
        source[str(path)] = file_sha256(path)
    rows = {str(level): [row for item, prediction in zip(items.values(), predictions, strict=True)
                          if (row := reply_attention_row(item, prediction, level)) is not None]
            for level in settings["attention_levels"]}
    question_only = sum(bool(p["events"]) and p["events"][0]["evidence_spans"][0]["source"] == "question"
                        for p in predictions)
    if len(rows["0.25"]) + question_only != keyword_manifest["candidate_events"]:
        raise ValueError("keyword candidate provenance is incomplete")
    return settings, cfg, replay, market, agents, joined, baskets, by_path, rows, question_only, len(items), source, paths


def run_experiment(config_path: Path, output_dir: Path, progress=None) -> dict:
    output_dir = output_dir.resolve()
    (settings, cfg, replay, market, agents, joined, baskets, base_paths, rows,
     question_only, reply_items, inputs, paths) = _load(config_path)
    if (output_dir.exists() and any(output_dir.iterdir())
            or any(output_dir == Path(path) or output_dir in Path(path).parents for path in inputs)):
        raise ValueError("keyword portfolio output must be a new directory separate from inputs")
    code = {name: file_sha256(path) for name, path in CODE_PATHS.items()}
    runtime = {"python": platform.python_version(), "zlib": zlib.ZLIB_RUNTIME_VERSION}
    identity = {"inputs": inputs, "code_sha256": code, "runtime": runtime}
    experiment_id = canonical_hash(identity)
    output_dir.mkdir(parents=True, exist_ok=True)
    results, day_count = [], 0
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        staging = Path(temporary)
        with (staging / "keyword_portfolio_ledger.jsonl.gz").open("wb") as binary, gzip.GzipFile(
                filename="", mode="wb", fileobj=binary, mtime=0) as ledger:
            for basket_index, assets in enumerate(baskets):
                for case in cfg["cases"]:
                    baseline_id = f"{basket_index}:{case['case_id']}"
                    control = simulate_portfolio({a: joined[a] for a in assets}, agents, cfg["core"],
                                                 cfg["background"], cfg["venue"],
                                                 cfg["feedback_parameters"], case, False)["summary"]
                    archived = base_paths[baseline_id]
                    if {k: v for k, v in archived.items() if k not in {"path_id", "basket_index", "case_id"}} != control:
                        raise AssertionError("reexecuted no-text control differs from archived baseline")
                    for level in settings["attention_levels"]:
                        tag = str(level)
                        active = {a: join_steps(joined[a], a, rows[tag]) for a in assets}
                        result = simulate_portfolio(active, agents, cfg["core"], cfg["background"],
                                                    cfg["venue"], cfg["feedback_parameters"], case, True)
                        path_id = f"{baseline_id}:attention_{tag}"
                        prior = _initial_audit_state(agents, cfg, assets, case)
                        for session, day in enumerate(result["trace"]):
                            for asset in assets:
                                observation = day["observations"][asset]
                                step = active[asset][session]
                                if (observation["text_signal"] != 0
                                        or observation["text_uncertainty"] != step["text_uncertainty"]
                                        or observation["text_evidence"] != step["text_evidence"]):
                                    raise AssertionError("keyword attention observation differs from visible input")
                            prior = audit_portfolio_day(day, prior, cfg["venue"], session)
                            ledger.write((json.dumps({"path_id": path_id, **day}, ensure_ascii=False,
                                                     separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8"))
                            day_count += 1
                        saved = result["summary"]
                        if (saved["final_prices_minor"] != prior["prices"]
                                or saved["fee_pool_minor"] != prior["fee_pool_minor"]
                                or any(any(saved["accounts"][name][field] != account[field]
                                           for field in ("wallets", "shares", "sellable"))
                                       for name, account in prior["accounts"].items())):
                            raise AssertionError("keyword portfolio summary differs from audited wallets")
                        results.append({"path_id": path_id, "baseline_path_id": baseline_id,
                                        "basket_index": basket_index, "case_id": case["case_id"],
                                        "attention_level": level, "final_price_delta_minor": {
                                            a: saved["final_prices_minor"][a] - control["final_prices_minor"][a]
                                            for a in assets},
                                        "role_wealth_multiple_delta": {
                                            role: saved["role_wealth_multiple"][role] - control["role_wealth_multiple"][role]
                                            for role in ("aggressive", "conservative", "institutional")},
                                        "strategy_filled_delta": saved["strategy_filled"] - control["strategy_filled"],
                                        **saved})
                if progress:
                    progress(basket_index + 1, len(baskets))
        grouped = []
        for level in settings["attention_levels"]:
            for case in cfg["cases"]:
                selected = [r for r in results if r["attention_level"] == level and r["case_id"] == case["case_id"]]
                accepted = sum(r["strategy_accepted"] for r in selected)
                filled = sum(r["strategy_filled"] for r in selected)
                grouped.append({"attention_level": level, "case_id": case["case_id"],
                                "baskets": len(selected), "strategy_requested": sum(r["strategy_requested"] for r in selected),
                                "strategy_accepted": accepted, "strategy_filled": filled,
                                "strategy_fill_fraction": filled / accepted if accepted else 0.0,
                                "strategy_filled_delta_vs_no_text": sum(r["strategy_filled_delta"] for r in selected),
                                "cash_clipped_orders": sum(r["cash_clipped_orders"] for r in selected),
                                "mean_final_price_delta_multiple": statistics.mean(
                                    delta / cfg["venue"]["price_start_minor"] for r in selected
                                    for delta in r["final_price_delta_minor"].values()),
                                "mean_role_wealth_multiple_delta": {role: statistics.mean(
                                    r["role_wealth_multiple_delta"][role] for r in selected)
                                    for role in ("aggressive", "conservative", "institutional")}})
        if (any(file_sha256(Path(path)) != digest for path, digest in inputs.items())
                or code != {name: file_sha256(path) for name, path in CODE_PATHS.items()}):
            raise RuntimeError("keyword portfolio source or code changed during execution")
        summary = {"pipeline_version": VERSION, "experiment_id": experiment_id,
                   "run_id": settings["run_id"], "baseline_experiment_id": load_summary(paths["baseline_directory"])["experiment_id"],
                   "market_dataset_id": market["market_dataset_id"], "pack_experiment_id":
                   json.loads((paths["annotation_pack"] / "annotation_manifest.json").read_text(encoding="utf-8"))["experiment_id"],
                   "stocks": len(joined), "reply_items": reply_items,
                   "reply_keyword_companies": len(rows["0.25"]), "question_only_hits_excluded": question_only,
                   "companies_without_reply": len(joined) - reply_items, "baskets": baskets, "cases": cfg["cases"],
                   "dates": {"start": replay["start_date"], "end": replay["end_date"]},
                   "paths": len(results), "ledger_rows": day_count,
                   "asset_call_rows": day_count * cfg["basket_size"],
                   "audited_portfolio_days": day_count, "grouped": grouped,
                   "interpretation": "Literal reply keyword attention proxy with assumed uncertainty levels; neither semantic prediction nor historical price fit."}
        (staging / "keyword_portfolio_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        (staging / "keyword_portfolio_paths.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        report = ["# 武汉事前回复关键词：组合关注度机制敏感性", "",
                  f"{len(joined)} 家固定公司、{len(baskets)} 个三股篮子；105 家有事前回复。",
                  f"仅 {len(rows['0.25'])} 家公司回复里的字面命中激活无方向关注度；"
                  f"{question_only} 条仅提问命中排除。两档关注度惩罚 0.25/1.0 是手设机制参数。", "",
                  "| 关注度参数 | 资金条件 | 成交/接受 | 成交股数相对无文本 | 平均终价差/初价 |",
                  "|---:|---|---:|---:|---:|"]
        for group in grouped:
            report.append(f"| {group['attention_level']} | {group['case_id']} | "
                          f"{group['strategy_fill_fraction']:.2%} | {group['strategy_filled_delta_vs_no_text']:+,} | "
                          f"{group['mean_final_price_delta_multiple']:+.6f} |")
        report += ["", "每条无文本路径重执行后与归档摘要精确配对；每个有文本组合日重建资金与股份。",
                   "行情只提供固定样本、交易日、事前截止及停牌代理；模拟价格由内生撮合产生。",
                   "关键词命中不证明事实、方向、事件时点或预测能力。参数与背景订单未用历史盘口校准；"
                   "本结果仅展示假设参数如何改变机制路径。", ""]
        (staging / "keyword_portfolio_report.md").write_text("\n".join(report), encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "experiment_id": experiment_id,
                    "generated_at": datetime.now(timezone.utc).isoformat(), **identity,
                    "artifacts": {name: {"sha256": file_sha256(staging / name)} for name in sorted(ARTIFACTS)}}
        (staging / "keyword_portfolio_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run_experiment(args.config, args.output_dir,
                            lambda done, total: print(f"Processed {done}/{total} baskets", flush=True)
                            if done % 10 == 0 or done == total else None)
    print(json.dumps({k: result[k] for k in ("experiment_id", "paths", "ledger_rows", "grouped")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
