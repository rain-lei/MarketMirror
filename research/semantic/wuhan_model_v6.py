"""Run frozen v6 on the sixth cohort only after source-first review."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .local_credential import load_api_key
from .run_model import _is_runner_failure, _raw_row, _read_raw_rows, request_completion
from .prepare_wuhan_v6_holdout import POLICY, PROMPT, ROOT
from .wuhan_v6_snapshot import (
    BASE_URL, MODEL, OUTPUT_DIR as PACK_DIR, PROMPT_VERSION,
    verify_snapshot,
)
from .wuhan_v6_review import MANIFEST_NAME as REVIEW_MANIFEST, REVIEW_DIR, load_review


VERSION_RUN = "wuhan-company-confirmed-model-run-v6"
MODULE = Path(__file__).resolve()
RAW_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v6_holdout_model_v6_v1"
RAW_NAME = "model_raw_outputs.jsonl"
MANIFEST_NAME = "model_run_manifest.json"


def _code_hashes() -> dict[str, str]:
    return {name: file_sha256(MODULE.with_name(name)) for name in (
        "wuhan_model_v6.py", "wuhan_v6_snapshot.py", "wuhan_v6_review.py",
        "prepare_wuhan_v6_holdout.py", "wuhan_v6_gate.py",
        "run_model.py", "local_credential.py")}


def preflight(pack_dir: Path = PACK_DIR, review_dir: Path = REVIEW_DIR) -> tuple[dict, dict, dict]:
    items, pack = verify_snapshot(output_dir=pack_dir)
    labels, review = load_review(pack_dir, review_dir)
    if (len(items) != 250 or len(labels) != len(items)
            or review.get("audit", {}).get("reviewed_items") != len(items)
            or review.get("model_outputs_previously_seen") is not False
            or review.get("review_before_model_outputs") is not True):
        raise ValueError("sixth cohort source-first review is incomplete")
    protocol = pack["frozen_model_protocol"]
    if protocol != {"provider_base_url": BASE_URL, "model_id": MODEL,
                    "prompt_version": PROMPT_VERSION, "prompt_sha256": file_sha256(PROMPT),
                    "temperature": 0.0}:
        raise ValueError("v6 model settings differ from frozen pack")
    return items, pack, review


def _inputs(pack_dir: Path, review_dir: Path) -> dict[str, str]:
    return {name: file_sha256(path) for name, path in (
        ("annotation_manifest", pack_dir / "annotation_manifest.json"),
        ("annotation_items", pack_dir / "annotation_items.jsonl"),
        ("prompt", PROMPT),
        ("evaluation_policy", POLICY),
        ("source_first_review_manifest", review_dir / REVIEW_MANIFEST),
        ("reference_labels", review_dir / "reference_labels.jsonl"))}


def _output_root(output_dir: Path, pack_dir: Path, review_dir: Path, *, resume: bool) -> Path:
    output_dir = output_dir.resolve()
    outputs = (ROOT / "research_outputs").resolve()
    if (output_dir == outputs or outputs not in output_dir.parents
            or any(output_dir == path or output_dir in path.parents or path in output_dir.parents
                   for path in (pack_dir.resolve(), review_dir.resolve()))):
        raise ValueError("v6 raw output must be a separate research_outputs child")
    if output_dir.exists() and any(output_dir.iterdir()) and not resume:
        raise ValueError("v6 raw output must be new or explicitly resumed")
    return output_dir


def run(pack_dir: Path = PACK_DIR, output_dir: Path = RAW_DIR, *, api_key: str,
        review_dir: Path = REVIEW_DIR, resume: bool = False,
        timeout: float = 90.0, retries: int = 2) -> dict:
    pack_dir, review_dir = pack_dir.resolve(), review_dir.resolve()
    items, pack, review = preflight(pack_dir, review_dir)
    output_dir = _output_root(output_dir, pack_dir, review_dir, resume=resume)
    if not isinstance(api_key, str) or not api_key.strip() or timeout <= 0 or retries < 0:
        raise ValueError("v6 model credential or request settings are invalid")
    if datetime.now(timezone.utc) <= datetime.fromisoformat(review["generated_at"]):
        raise ValueError("v6 model execution must follow source-first review")
    inputs, code = _inputs(pack_dir, review_dir), _code_hashes()
    raw_path, manifest_path = output_dir / RAW_NAME, output_dir / MANIFEST_NAME
    rows: list[dict] = []
    if resume:
        if (not output_dir.is_dir()
                or {p.name for p in output_dir.iterdir()} - {
                    RAW_NAME, MANIFEST_NAME, "model_run_manifest.json.tmp"}
                or not raw_path.is_file() or not manifest_path.is_file()):
            raise ValueError("v6 resume requires only its raw checkpoint")
        prior = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (prior.get("pipeline_version") != VERSION_RUN
                or prior.get("pack_experiment_id") != pack["experiment_id"]
                or prior.get("provider_base_url") != BASE_URL
                or prior.get("model_id") != MODEL
                or prior.get("prompt_version") != PROMPT_VERSION
                or prior.get("temperature") != 0.0
                or prior.get("input_sha256") != inputs
                or prior.get("code_sha256") != code
                or prior.get("reviewed_items") != len(items)):
            raise ValueError("v6 resume checkpoint identity differs")
        data = raw_path.read_bytes()
        committed = prior.get("raw_bytes")
        if (type(committed) is not int or not 0 <= committed <= len(data)
                or hashlib.sha256(data[:committed]).hexdigest()
                != prior.get("artifacts", {}).get(RAW_NAME, {}).get("sha256")):
            raise ValueError("v6 raw checkpoint bytes or hash differ")
        rows = _read_raw_rows(raw_path, items, MODEL, data[:committed].decode("utf-8"), PROMPT_VERSION)
        if len(rows) != prior.get("model_rows"):
            raise ValueError("v6 raw checkpoint row count differs")
        rows = [row for row in rows if not _is_runner_failure(row)]
    output_dir.mkdir(parents=True, exist_ok=True)
    with raw_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def checkpoint() -> dict:
        manifest = {
            "pipeline_version": VERSION_RUN,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "provider_base_url": BASE_URL, "model_id": MODEL,
            "prompt_version": PROMPT_VERSION, "temperature": 0.0,
            "pack_experiment_id": pack["experiment_id"],
            "input_sha256": inputs, "code_sha256": code,
            "reviewed_items": len(items), "review_before_model": True,
            "requested_rows": len(items), "model_rows": len(rows),
            "remaining_rows": len(items) - len(rows),
            "request_failures": sum(_is_runner_failure(row) for row in rows),
            "raw_bytes": raw_path.stat().st_size,
            "artifacts": {RAW_NAME: {"sha256": file_sha256(raw_path)}},
        }
        temporary = manifest_path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(manifest_path)
        return manifest

    result = checkpoint()
    completed = {row["item_id"] for row in rows}
    pending = [item for item in items.values() if item["item_id"] not in completed]
    prompt = PROMPT.read_text(encoding="utf-8")
    with raw_path.open("a", encoding="utf-8") as handle:
        for index, item in enumerate(pending, start=len(rows) + 1):
            # Deliberately exclude company identifiers, financial fields and labels.
            user_message = json.dumps({"segments": item["segments"]}, ensure_ascii=False,
                                      separators=(",", ":"))
            try:
                response = request_completion(
                    BASE_URL + "/chat/completions", api_key, MODEL,
                    [{"role": "system", "content": prompt},
                     {"role": "user", "content": user_message}],
                    timeout, retries, 0.0)
            except RuntimeError as error:
                response = json.dumps({"runner_error": str(error)}, ensure_ascii=False)
            row = _raw_row(item, MODEL, PROMPT_VERSION, response)
            rows.append(row)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            result = checkpoint()
            print(f"Processed {index}/{len(items)}", flush=True)
    if (_inputs(pack_dir, review_dir) != inputs or _code_hashes() != code
            or preflight(pack_dir, review_dir)[1]["experiment_id"] != pack["experiment_id"]):
        raise RuntimeError("v6 model inputs or code changed during requests")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()
    if args.preflight:
        items, pack, review = preflight()
        print(json.dumps({"items": len(items), "pack_experiment_id": pack["experiment_id"],
                          "reviewed_items": review["audit"]["reviewed_items"],
                          "request_user_fields": ["segments"],
                          "prompt_sha256": file_sha256(PROMPT)}, ensure_ascii=False))
        return
    key = load_api_key()
    try:
        result = run(api_key=key, resume=args.resume, timeout=args.timeout,
                     retries=args.retries)
    finally:
        key = None
    print(json.dumps({"pack_experiment_id": result["pack_experiment_id"],
                      "model_rows": result["model_rows"],
                      "request_failures": result["request_failures"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
