"""Rerun deterministic research jobs and compare all declared artifact bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .verify_catalog import audit_run, load_catalog
from ..baselines.placebo_dates import run_placebo
from ..baselines.activity_event_study import run_activity_event
from ..baselines.observed_diagnostics import run_diagnostic
from ..baselines.run_experiments import run_experiments
from ..baselines.run_prediction import run_prediction
from ..data_pipeline.market_activity import import_activity
from ..data_pipeline.market_data import import_market
from ..data_pipeline.build_dataset import build_dataset
from ..data_pipeline.provenance import file_sha256
from ..semantic.annotation_pack import build_annotation_pack
from ..semantic.keyword_baseline import run_baseline
from ..simulation.historical_replay import run_replay
from ..simulation.capacity_diagnostic import run_diagnostic as run_capacity_diagnostic
from ..simulation.participation_replay import run_participation_replay
from ..simulation.stress_market import run_stress

VERSION = "research-reexecution-v1"


def rerun_unified_dataset(manifest_path: Path, output_dir: Path) -> Any:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sources = [Path(item["path"]) for item in manifest["sources"]]
    financial = [Path(item["path"]) for item in manifest["financial_inputs"]]
    return build_dataset(sources, output_dir, financial_databases=financial)


RUNNERS: dict[str, Callable[[Path, Path], Any]] = {
    "unified_dataset": rerun_unified_dataset,
    "observed_market": import_market,
    "observed_market_2018": import_market,
    "observed_activity": import_activity,
    "observed_activity_2018": import_activity,
    "activity_event": run_activity_event,
    "activity_event_2018": lambda source, output: run_diagnostic("activity", source, output),
    "synthetic_stress": run_stress,
    "observed_event": run_experiments,
    "observed_event_2018": run_experiments,
    "text_prediction": run_prediction,
    "event_date_diagnostic": run_placebo,
    "event_date_diagnostic_2018": lambda source, output: run_diagnostic("placebo", source, output),
    "historical_replay_q1": run_replay,
    "historical_replay_later": run_replay,
    "historical_replay_2018": run_replay,
    "historical_capacity_2018_h1": run_capacity_diagnostic,
    "historical_capacity_2020_q1": run_capacity_diagnostic,
    "historical_capacity_2020_later": run_capacity_diagnostic,
    "historical_participation_2018_h1": run_participation_replay,
    "historical_participation_2020_q1": run_participation_replay,
    "historical_participation_2020_later": run_participation_replay,
    "semantic_annotation": build_annotation_pack,
    "keyword_baseline": run_baseline,
}
METADATA_ONLY_DIFFERENCES = {"observed_event": {"event_results.json"},
                             "observed_event_2018": {"event_results.json"}}
SQLITE_METADATA_ONLY_DIFFERENCES = {"unified_dataset": {"dataset.sqlite"}}


def sqlite_logical_digest(path: Path) -> tuple[str, dict[str, int]]:
    """Hash schema and every PK-ordered row, except the build timestamp metadata row."""
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    digest = hashlib.sha256()
    counts = {}
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed")
        schema = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name, tbl_name"
        ).fetchall()
        digest.update(json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        tables = [row[1] for row in schema if row[0] == "table" and not row[1].startswith("sqlite_")]
        generated_rows = 0
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            columns = connection.execute(f"PRAGMA table_info({quoted})").fetchall()
            pk = [row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5] > 0]
            ordering = ", ".join('"' + name.replace('"', '""') + '"' for name in pk) if pk else "rowid"
            count = 0
            for row in connection.execute(f"SELECT * FROM {quoted} ORDER BY {ordering}"):
                if table == "dataset_metadata" and row[0] == "generated_at":
                    if len(row) != 2 or not isinstance(json.loads(row[1]), str):
                        raise ValueError("dataset generated_at metadata is malformed")
                    generated_rows += 1
                    continue
                digest.update(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                digest.update(b"\n")
                count += 1
            counts[table] = count
            digest.update(f"{table}:{count}\n".encode("utf-8"))
        if generated_rows != 1:
            raise ValueError("expected exactly one generated_at metadata row")
        return digest.hexdigest(), counts
    finally:
        connection.close()


def load_config(path: Path) -> tuple[Path, list[dict[str, str]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or set(data) != {"integrity_catalog", "runs"}
            or not isinstance(data["integrity_catalog"], str) or not data["integrity_catalog"]
            or not isinstance(data["runs"], list) or not data["runs"]):
        raise ValueError("reexecution config requires integrity catalog and runs")
    ids = []
    for run in data["runs"]:
        if (not isinstance(run, dict) or set(run) != {"run_id", "input"}
                or run["run_id"] not in RUNNERS or not isinstance(run["input"], str) or not run["input"]):
            raise ValueError("reexecution run or input is unsupported")
        ids.append(run["run_id"])
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate reexecution run")
    return (path.parent / data["integrity_catalog"]).resolve(), data["runs"]


def compare_artifacts(expected: dict[str, Any], regenerated_dir: Path, reference_dir: Path | None = None,
                      ignore_generated_at: set[str] | None = None,
                      compare_sqlite_logically: set[str] | None = None) -> list[dict[str, str | None]]:
    if not isinstance(expected, dict) or not expected:
        raise ValueError("pinned run has no artifact hash map")
    comparisons = []
    for name, info in expected.items():
        path = (regenerated_dir / name).resolve()
        if path.parent != regenerated_dir.resolve():
            raise ValueError("artifact path escapes regenerated output directory")
        actual = file_sha256(path) if path.is_file() else None
        reference = info["sha256"]
        status = "identical" if actual == reference else "different_or_missing"
        if (status != "identical" and name in (ignore_generated_at or set())
                and reference_dir is not None and path.is_file()):
            original = json.loads((reference_dir / name).read_text(encoding="utf-8"))
            replayed = json.loads(path.read_text(encoding="utf-8"))
            if (isinstance(original, dict) and isinstance(replayed, dict)
                    and isinstance(original.get("generated_at"), str)
                    and isinstance(replayed.get("generated_at"), str)):
                first, second = dict(original), dict(replayed)
                del first["generated_at"]
                del second["generated_at"]
                if first == second:
                    status = "equivalent_except_generated_at"
        if (status != "identical" and name in (compare_sqlite_logically or set())
                and reference_dir is not None and path.is_file()):
            old_digest, old_counts = sqlite_logical_digest(reference_dir / name)
            new_digest, new_counts = sqlite_logical_digest(path)
            if old_digest == new_digest and old_counts == new_counts:
                status = "equivalent_except_sqlite_generated_at_metadata"
        comparisons.append({"artifact": name, "expected_sha256": reference,
                            "regenerated_sha256": actual,
                            "status": status})
    return comparisons


def reexecute(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    integrity_path, selected = load_config(config_path)
    config_digest = file_sha256(config_path)
    integrity_digest = file_sha256(integrity_path)
    pinned = {run["run_id"]: run for run in load_catalog(integrity_path)}
    if any(run["run_id"] not in pinned for run in selected):
        raise ValueError("reexecution run is absent from the integrity catalog")
    repo_root = Path(__file__).resolve().parents[2]
    hash_cache: dict[Path, str] = {}
    results = []
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty reexecution output directory")
    if any(output_dir == p or output_dir in p.parents for p in (config_path, integrity_path)):
        raise ValueError("reexecution output cannot contain catalog inputs")
    output_dir.mkdir(parents=True, exist_ok=True)
    for item in selected:
        run_id = item["run_id"]
        pinned_run = pinned[run_id]
        integrity = audit_run(pinned_run, integrity_path.parent, repo_root / "research",
                              repo_root / "research_outputs", hash_cache)
        entry: dict[str, Any] = {"run_id": run_id, "integrity_status": integrity["status"],
                                 "integrity_failures": integrity["failures"], "artifacts": []}
        if integrity["status"] != "passed":
            entry["status"] = "failed_preflight"
            results.append(entry)
            continue
        source_input = (config_path.parent / item["input"]).resolve()
        if not source_input.is_file() and not (run_id == "keyword_baseline" and source_input.is_dir()):
            entry.update(status="failed", error="run input is missing or has the wrong type")
            results.append(entry)
            continue
        manifest_path = (integrity_path.parent / pinned_run["manifest"]).resolve()
        reference_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(dir=output_dir) as temp:
            fresh = Path(temp) / "rerun"
            try:
                RUNNERS[run_id](source_input, fresh)
                entry["artifacts"] = compare_artifacts(reference_manifest["artifacts"], fresh,
                                                        manifest_path.parent, METADATA_ONLY_DIFFERENCES.get(run_id),
                                                        SQLITE_METADATA_ONLY_DIFFERENCES.get(run_id))
                entry["status"] = ("equivalent" if all(r["status"] in {"identical", "equivalent_except_generated_at",
                                                                      "equivalent_except_sqlite_generated_at_metadata"}
                                                       for r in entry["artifacts"]) else "different")
            except (OSError, ValueError, RuntimeError, AssertionError, KeyError) as exc:
                entry.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        results.append(entry)
    unstable = [path for path, digest in hash_cache.items() if not path.is_file() or file_sha256(path) != digest]
    if file_sha256(config_path) != config_digest:
        unstable.append(config_path)
    if file_sha256(integrity_path) != integrity_digest:
        unstable.append(integrity_path)
    if unstable:
        for entry in results:
            entry["status"] = "failed"
            entry["error"] = "one or more pinned files changed during reexecution"
    result = {"pipeline_version": VERSION, "config_sha256": config_digest,
              "integrity_catalog_sha256": integrity_digest,
              "passed_runs": sum(entry["status"] == "equivalent" for entry in results),
              "runs": results, "unstable_files": [str(path) for path in unstable],
              "scope": "Deterministic artifact byte comparison after a fresh local rerun; source meaning and empirical validity are not assessed."}
    (output_dir / "reexecution_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "reexecution_report.md").write_text(render_report(result), encoding="utf-8")
    manifest = {"pipeline_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
                "config_sha256": result["config_sha256"], "integrity_catalog_sha256": result["integrity_catalog_sha256"],
                "auditor_code_sha256": file_sha256(Path(__file__)),
                "artifacts": {name: {"sha256": file_sha256(output_dir / name)} for name in
                              ("reexecution_results.json", "reexecution_report.md")}}
    (output_dir / "reexecution_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def render_report(result: dict[str, Any]) -> str:
    lines = ["# 确定性实验独立重跑核验", "", "每项先通过固定清单的输入、代码和原产物完整性检查，然后在新空目录执行同一输入；产物逐字节比较，明确列出的时间戳例外采用限定字段比较。", "",
             "| 运行 | 重跑结果 | 一致产物/总产物 |", "|---|---|---:|"]
    for run in result["runs"]:
        count = sum(a["status"] in {"identical", "equivalent_except_generated_at",
                                    "equivalent_except_sqlite_generated_at_metadata"} for a in run["artifacts"])
        lines.append(f"| {run['run_id']} | {run['status']} | {count}/{len(run['artifacts'])} |")
    for run in result["runs"]:
        if run["status"] != "equivalent":
            lines += ["", f"## {run['run_id']} 差异", ""]
            lines.extend(f"- {issue}" for issue in run["integrity_failures"])
            if run.get("error"):
                lines.append(f"- {run['error']}")
            lines.extend(f"- {a['artifact']}: {a['status']}" for a in run["artifacts"] if a["status"] != "identical")
    normalized = [(run["run_id"], a["artifact"]) for run in result["runs"] for a in run["artifacts"]
                  if a["status"] == "equivalent_except_generated_at"]
    normalized_sqlite = [(run["run_id"], a["artifact"]) for run in result["runs"] for a in run["artifacts"]
                         if a["status"] == "equivalent_except_sqlite_generated_at_metadata"]
    byte_identical = sum(a["status"] == "identical" for run in result["runs"] for a in run["artifacts"])
    lines += ["", f"通过 {result['passed_runs']}/{len(result['runs'])} 项重跑。{byte_identical} 份产物逐字节一致；其余按下列严格限定的规则比较：", ""]
    lines.extend(f"- {run_id}/{name}：仅忽略 JSON 顶层 `generated_at`；其余所有字段作精确比较。" for run_id, name in normalized)
    lines.extend(f"- {run_id}/{name}：原始字节不同；核对完整 SQLite 结构与按主键排序的全部表行，仅忽略 `dataset_metadata.generated_at` 一行。" for run_id, name in normalized_sqlite)
    lines += ["", "目录外的其他实验尚未纳入此重跑门槛。", "",
              "通过表示当前机器、当前源文件和代码能够再生相同研究内容，不证明事件解释、可交易性、统计显著性或 Agent 行为校准。", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = reexecute(args.config, args.output_dir)
    print(json.dumps({"passed_runs": result["passed_runs"], "total_runs": len(result["runs"])}, ensure_ascii=False))
    if result["passed_runs"] != len(result["runs"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
