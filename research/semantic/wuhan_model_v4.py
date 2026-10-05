"""Run the frozen Wuhan v4 prompt after source-first review, with checkpoints."""

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
from .wuhan_protocol_v4 import (
    BASE_URL,
    MODEL,
    MODULE as PROTOCOL_MODULE,
    PACK_DIR,
    PROMPT,
    ROOT,
    RAW_DIR,
    RAW_MANIFEST,
    RAW_NAME,
    VERSION,
    verify_pack,
)
from .wuhan_v4_review import REVIEW_DIR, load_review


VERSION_RUN = "wuhan-company-confirmed-model-run-v4"
MODULE = Path(__file__).resolve()


def _code_hashes() -> dict[str, str]:
    return {
        "wuhan_protocol_v4.py": file_sha256(PROTOCOL_MODULE),
        "wuhan_model_v4.py": file_sha256(MODULE),
        "wuhan_v4_review.py": file_sha256(Path(__file__).with_name("wuhan_v4_review.py")),
        "run_model.py": file_sha256(Path(__file__).with_name("run_model.py")),
    }


def run(pack_dir: Path, output_dir: Path, *, api_key: str, timeout: float = 90.0,
        retries: int = 2, resume: bool = False,
        review_dir: Path = REVIEW_DIR) -> dict:
    pack_dir, output_dir, review_dir = (path.resolve() for path in (pack_dir, output_dir, review_dir))
    items, pack = verify_pack(pack_dir)
    _, review = load_review(pack_dir, review_dir)
    if review.get("audit", {}).get("reviewed_items") != len(items):
        raise ValueError("every item must be reviewed before v4 model execution")
    if (not isinstance(api_key, str) or not api_key.strip()
            or timeout <= 0 or retries < 0):
        raise ValueError("v4 model credential or request settings are invalid")
    if (output_dir == pack_dir or output_dir in pack_dir.parents or pack_dir in output_dir.parents
            or output_dir == review_dir or output_dir in review_dir.parents or review_dir in output_dir.parents):
        raise ValueError("v4 model output must be separate from source and review inputs")
    outputs_root = (ROOT / "research_outputs").resolve()
    if output_dir == outputs_root or outputs_root not in output_dir.parents:
        raise ValueError("v4 model output must stay under the ignored research_outputs folder")
    if output_dir.exists() and any(output_dir.iterdir()) and not resume:
        raise ValueError("use a new empty model output directory")

    protocol = pack["frozen_model_protocol"]
    review_manifest_path = review_dir / "source_first_review_manifest.json"
    inputs = {"annotation_manifest": file_sha256(pack_dir / "annotation_manifest.json"),
              "annotation_items": file_sha256(pack_dir / "annotation_items.jsonl"),
              "prompt": file_sha256(PROMPT),
              "source_first_review_manifest": file_sha256(review_manifest_path)}
    code = _code_hashes()
    if (protocol.get("provider_base_url") != BASE_URL or protocol.get("model_id") != MODEL
            or protocol.get("prompt_version") != VERSION
            or protocol.get("prompt_sha256") != inputs["prompt"]
            or protocol.get("temperature") != 0.0):
        raise ValueError("v4 model request differs from the frozen annotation protocol")

    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path, manifest_path = output_dir / RAW_NAME, output_dir / RAW_MANIFEST
    rows = []
    if resume and any(output_dir.iterdir()):
        allowed = {RAW_NAME, RAW_MANIFEST, "model_run_manifest.json.tmp"}
        if {path.name for path in output_dir.iterdir()} - allowed or not raw_path.exists() or not manifest_path.exists():
            raise ValueError("resume requires only v4 raw outputs and their checkpoint manifest")
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (previous.get("pipeline_version") != VERSION_RUN
                or previous.get("pack_experiment_id") != pack["experiment_id"]
                or previous.get("provider_base_url") != BASE_URL
                or previous.get("model_id") != MODEL
                or previous.get("prompt_version") != VERSION
                or previous.get("temperature") != 0.0
                or previous.get("input_sha256") != inputs
                or previous.get("code_sha256") != code
                or previous.get("reviewed_items") != len(items)):
            raise ValueError("v4 resume checkpoint differs from the frozen model/review protocol")
        data = raw_path.read_bytes()
        committed = previous.get("raw_bytes")
        declared = previous.get("artifacts", {}).get(RAW_NAME, {}).get("sha256")
        if (type(committed) is not int or not 0 <= committed <= len(data)
                or hashlib.sha256(data[:committed]).hexdigest() != declared):
            raise ValueError("v4 resume checkpoint raw output hash differs")
        rows = _read_raw_rows(raw_path, items, MODEL, data[:committed].decode("utf-8"), VERSION)
        if len(rows) != previous.get("model_rows"):
            raise ValueError("v4 resume checkpoint row count differs")
        rows = [row for row in rows if not _is_runner_failure(row)]
    completed = {row["item_id"] for row in rows}
    pending = [item for item in items.values() if item["item_id"] not in completed]

    def checkpoint() -> dict:
        manifest = {"pipeline_version": VERSION_RUN,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "provider_base_url": BASE_URL, "model_id": MODEL,
                    "prompt_version": VERSION, "temperature": 0.0,
                    "pack_experiment_id": pack["experiment_id"],
                    "input_sha256": inputs, "code_sha256": code,
                    "reviewed_items": len(items), "review_before_model": True,
                    "requested_rows": len(items), "model_rows": len(rows),
                    "remaining_rows": len(items) - len(rows),
                    "request_failures": sum(_is_runner_failure(row) for row in rows),
                    "raw_bytes": raw_path.stat().st_size,
                    "artifacts": {RAW_NAME: {"sha256": file_sha256(raw_path)}}}
        temporary = manifest_path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(manifest_path)
        return manifest

    with raw_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    checkpoint()
    prompt = PROMPT.read_text(encoding="utf-8")
    with raw_path.open("a", encoding="utf-8") as handle:
        for index, item in enumerate(pending, start=len(rows) + 1):
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
            row = _raw_row(item, MODEL, VERSION, response)
            rows.append(row)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            result = checkpoint()
            print(f"Processed {index}/{len(items)}", flush=True)

    if (inputs != {"annotation_manifest": file_sha256(pack_dir / "annotation_manifest.json"),
                   "annotation_items": file_sha256(pack_dir / "annotation_items.jsonl"),
                   "prompt": file_sha256(PROMPT),
                   "source_first_review_manifest": file_sha256(review_manifest_path)}
            or code != _code_hashes()):
        raise RuntimeError("v4 model inputs or code changed while requests were running")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_dir", type=Path, nargs="?", default=PACK_DIR)
    parser.add_argument("--output-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--review-dir", type=Path, default=REVIEW_DIR)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    try:
        key = load_api_key()
        result = run(args.pack_dir, args.output_dir, api_key=key,
                     timeout=args.timeout, retries=args.retries,
                     resume=args.resume, review_dir=args.review_dir)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(2, f"error: {type(error).__name__}: {error}\n")
    finally:
        if "key" in locals():
            key = None
    print(json.dumps({"pack_experiment_id": result["pack_experiment_id"],
                      "model_rows": result["model_rows"],
                      "request_failures": result["request_failures"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
