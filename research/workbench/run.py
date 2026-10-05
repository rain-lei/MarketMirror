"""Execute one pinned research run in a separately recorded local directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..data_pipeline.provenance import file_sha256
from ..registry.reexecute import load_config, reexecute
from ..registry.verify_catalog import load_catalog

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/reexecution_catalog_2020.json"
DEFAULT_OUTPUT_ROOT = ROOT / "research_outputs/workbench_runs"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def available_runs(config_path: Path = CONFIG) -> list[str]:
    _, runs = load_config(config_path.resolve())
    return [item["run_id"] for item in runs]


def version_for_run(config_path: Path, run_id: str) -> dict[str, str]:
    """Return opaque, hash-derived data and execution versions for one pinned run."""
    config_path = config_path.resolve()
    integrity_path, selected = load_config(config_path)
    item = next((row for row in selected if row["run_id"] == run_id), None)
    if item is None:
        raise ValueError("run_id is absent from the pinned reexecution catalog")
    pinned = next((row for row in load_catalog(integrity_path) if row["run_id"] == run_id), None)
    if pinned is None:
        raise ValueError("run_id is absent from the pinned integrity catalog")
    manifest_path = (integrity_path.parent / pinned["manifest"]).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    data_sections = ("inputs", "input_sha256", "market_source_inputs", "evidence_inputs",
                     "sources", "financial_inputs", "config_sha256", "data_kind")
    data_payload = {key: manifest[key] for key in data_sections if key in manifest}
    data_digest = hashlib.sha256(json.dumps(data_payload, ensure_ascii=False,
                                            sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    code_digest = hashlib.sha256(json.dumps(manifest.get("code_sha256", {}), ensure_ascii=False,
                                            sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    pipeline = str(manifest.get("pipeline_version", "unknown"))
    return {"data_version": f"inputs-{data_digest[:12]}",
            "model_version": f"{pipeline}-{code_digest[:12]}"}


def version_catalog(config_path: Path = CONFIG) -> dict[str, dict[str, str]]:
    return {run_id: version_for_run(config_path, run_id) for run_id in available_runs(config_path)}


def selected_config(config_path: Path, run_id: str) -> dict[str, Any]:
    config_path = config_path.resolve()
    integrity_path, runs = load_config(config_path)
    selected = next((item for item in runs if item["run_id"] == run_id), None)
    if selected is None:
        raise ValueError("run_id is absent from the pinned reexecution catalog")
    return {"integrity_catalog": str(integrity_path),
            "runs": [{"run_id": run_id, "input": str((config_path.parent / selected["input"]).resolve())}]}


def write_status(job_dir: Path, record: dict[str, Any]) -> None:
    staged = job_dir / "job_status.tmp"
    staged.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    staged.replace(job_dir / "job_status.json")


def run_selected(run_id: str, output_root: Path = DEFAULT_OUTPUT_ROOT,
                 config_path: Path = CONFIG, job_id: str | None = None,
                 data_version: str | None = None, model_version: str | None = None) -> dict[str, Any]:
    """Create an immutable selection record, then call the existing audited runner."""
    config_path = config_path.resolve()
    selection = selected_config(config_path, run_id)
    versions = version_for_run(config_path, run_id)
    if data_version is not None and data_version != versions["data_version"]:
        raise ValueError("data_version is not the pinned version for this run")
    if model_version is not None and model_version != versions["model_version"]:
        raise ValueError("model_version is not the pinned version for this run")
    data_version, model_version = versions["data_version"], versions["model_version"]
    job_id = job_id or uuid4().hex
    if len(job_id) != 32 or any(char not in "0123456789abcdef" for char in job_id):
        raise ValueError("job_id must be a 32-character lowercase hexadecimal identifier")
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    job_dir = output_root / job_id
    job_dir.mkdir(exist_ok=False)
    selection_path = job_dir / "selected_config.json"
    selection_path.write_text(json.dumps(selection, ensure_ascii=False, indent=2), encoding="utf-8")
    version_path = job_dir / "version_selection.json"
    version_path.write_text(json.dumps({"run_id": run_id, "data_version": data_version,
                                        "model_version": model_version}, ensure_ascii=False, indent=2), encoding="utf-8")
    record: dict[str, Any] = {
        "job_id": job_id, "run_id": run_id, "status": "running", "started_at": now_utc(),
        "master_config_sha256": file_sha256(config_path),
        "integrity_catalog_sha256": file_sha256(Path(selection["integrity_catalog"])),
        "selected_config_sha256": file_sha256(selection_path),
        "version_selection_sha256": file_sha256(version_path),
        "data_version": data_version, "model_version": model_version,
        "runner_code_sha256": file_sha256(Path(__file__)),
    }
    write_status(job_dir, record)
    try:
        result = reexecute(selection_path, job_dir / "results")
        run = result["runs"][0]
        record.update(status="passed" if run["status"] == "equivalent" else run["status"],
                      finished_at=now_utc(), compared_artifacts=len(run["artifacts"]),
                      artifact_statuses={item["artifact"]: item["status"] for item in run["artifacts"]},
                      result_sha256=file_sha256(job_dir / "results/reexecution_results.json"),
                      result_manifest_sha256=file_sha256(job_dir / "results/reexecution_manifest.json"))
    except Exception as exc:
        (job_dir / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        record.update(status="failed", finished_at=now_utc(), error_type=type(exc).__name__)
    write_status(job_dir, record)
    return record


def read_public_job(output_root: Path, job_id: str) -> dict[str, Any] | None:
    if len(job_id) != 32 or any(char not in "0123456789abcdef" for char in job_id):
        return None
    path = output_root.resolve() / job_id / "job_status.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("job_id") != job_id:
            return None
        job_dir = path.parent
        result_dir = job_dir / "results"
        recorded_hashes = {"selected_config_sha256": job_dir / "selected_config.json",
                           "version_selection_sha256": job_dir / "version_selection.json",
                           "result_sha256": result_dir / "reexecution_results.json",
                           "result_manifest_sha256": result_dir / "reexecution_manifest.json"}
        changed = (raw.get("status") == "passed" and not set(recorded_hashes).issubset(raw))
        changed = changed or any(not target.is_file() or file_sha256(target) != raw[key]
                      for key, target in recorded_hashes.items() if key in raw)
        if raw.get("status") == "passed" and not changed:
            selection = json.loads((job_dir / "selected_config.json").read_text(encoding="utf-8"))
            versions = json.loads((job_dir / "version_selection.json").read_text(encoding="utf-8"))
            result = json.loads((result_dir / "reexecution_results.json").read_text(encoding="utf-8"))
            manifest = json.loads((result_dir / "reexecution_manifest.json").read_text(encoding="utf-8"))
            artifacts = manifest.get("artifacts")
            run = result["runs"][0]
            compared = {item["artifact"]: item["status"] for item in run["artifacts"]}
            changed = (len(selection.get("runs", [])) != 1 or selection["runs"][0]["run_id"] != raw.get("run_id")
                       or versions.get("run_id") != raw.get("run_id")
                       or versions.get("data_version") != raw.get("data_version")
                       or versions.get("model_version") != raw.get("model_version")
                       or version_for_run(CONFIG, raw["run_id"]) != {"data_version": raw.get("data_version"),
                                                                        "model_version": raw.get("model_version")}
                       or len(result["runs"]) != 1 or run["run_id"] != raw.get("run_id")
                       or run["status"] != "equivalent" or raw.get("compared_artifacts") != len(compared)
                       or raw.get("artifact_statuses") != compared
                       or not isinstance(artifacts, dict) or not artifacts)
            for name, info in (artifacts or {}).items():
                target = (result_dir / name).resolve()
                if (target.parent != result_dir.resolve() or not target.is_file()
                        or file_sha256(target) != info["sha256"]):
                    changed = True
        if changed:
            raw["status"] = "record_changed"
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        raw = {"job_id": job_id, "status": "record_changed"}
    fields = ("job_id", "run_id", "status", "started_at", "finished_at", "data_version", "model_version",
              "compared_artifacts", "artifact_statuses", "error_type")
    return {key: raw[key] for key in fields if key in raw}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id", choices=available_runs())
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    record = run_selected(args.run_id, args.output_root)
    print(json.dumps({"job_id": record["job_id"], "run_id": record["run_id"],
                      "status": record["status"]}, ensure_ascii=False))
    if record["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
