"""Run the isolated Wuhan company-confirmed v3 prompt with resumable checkpoints."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .run_model import _is_runner_failure, _raw_row, _read_raw_rows, request_completion
from .wuhan_protocol_v3 import (BASE_URL, MODEL, MODULE, PROMPT, RAW_MANIFEST, RAW_NAME,
                                 RUN_VERSION, VERSION, verify_pack)


def run(pack_dir: Path, output_dir: Path, *, api_key: str,
        timeout: float = 90.0, retries: int = 2, resume: bool = False) -> dict:
    pack_dir, output_dir = pack_dir.resolve(), output_dir.resolve()
    items, pack = verify_pack(pack_dir)
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("MARKETMIRROR_LLM_API_KEY is empty")
    if (output_dir == pack_dir or output_dir in pack_dir.parents or pack_dir in output_dir.parents
            or timeout <= 0 or retries < 0):
        raise ValueError("v3 model output path or request settings are invalid")
    if output_dir.exists() and any(output_dir.iterdir()) and not resume:
        raise ValueError("use a new empty v3 model output directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path, manifest_path = output_dir / RAW_NAME, output_dir / RAW_MANIFEST
    inputs = {"annotation_manifest": file_sha256(pack_dir / "annotation_manifest.json"),
              "annotation_items": file_sha256(pack_dir / "annotation_items.jsonl"),
              "prompt": file_sha256(PROMPT)}
    code = {"wuhan_protocol_v3.py": file_sha256(MODULE),
            "wuhan_model_v3.py": file_sha256(Path(__file__)),
            "run_model.py": file_sha256(Path(__file__).with_name("run_model.py"))}
    rows = []
    if resume and any(output_dir.iterdir()):
        allowed = {RAW_NAME, RAW_MANIFEST, "model_run_manifest.json.tmp"}
        if {path.name for path in output_dir.iterdir()} - allowed or not raw_path.exists() or not manifest_path.exists():
            raise ValueError("resume requires only raw outputs and their committed manifest")
        prior = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (prior.get("pipeline_version") != RUN_VERSION
                or prior.get("pack_experiment_id") != pack["experiment_id"]
                or prior.get("provider_base_url") != BASE_URL or prior.get("model_id") != MODEL
                or prior.get("prompt_version") != VERSION or prior.get("temperature") != 0.0
                or prior.get("input_sha256") != inputs or prior.get("code_sha256") != code
                or prior.get("requested_rows") != len(items)):
            raise ValueError("resume checkpoint differs from frozen v3 request")
        data = raw_path.read_bytes()
        committed = prior.get("raw_bytes")
        if type(committed) is not int or not 0 <= committed <= len(data):
            raise ValueError("resume checkpoint has invalid committed byte count")
        payload = data[:committed]
        if hashlib.sha256(payload).hexdigest() != prior.get("artifacts", {}).get(RAW_NAME, {}).get("sha256"):
            raise ValueError("resume checkpoint raw output hash mismatch")
        rows = _read_raw_rows(raw_path, items, MODEL, payload.decode("utf-8"), VERSION)
        if len(rows) != prior.get("model_rows"):
            raise ValueError("resume checkpoint row count mismatch")
        rows = [row for row in rows if not _is_runner_failure(row)]
    completed = {row["item_id"] for row in rows}
    pending = [item for item in items.values() if item["item_id"] not in completed]
    with raw_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def checkpoint() -> dict:
        manifest = {"pipeline_version": RUN_VERSION,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "provider_base_url": BASE_URL, "model_id": MODEL,
                    "prompt_version": VERSION, "temperature": 0.0, "resumed": resume,
                    "pack_experiment_id": pack["experiment_id"], "input_sha256": inputs,
                    "code_sha256": code, "requested_rows": len(items), "model_rows": len(rows),
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

    manifest = checkpoint()
    prompt = PROMPT.read_text(encoding="utf-8")
    with raw_path.open("a", encoding="utf-8") as handle:
        for index, item in enumerate(pending, start=len(rows) + 1):
            message = json.dumps({"segments": item["segments"]}, ensure_ascii=False,
                                 separators=(",", ":"))
            try:
                response = request_completion(
                    BASE_URL + "/chat/completions", api_key, MODEL,
                    [{"role": "system", "content": prompt},
                     {"role": "user", "content": message}], timeout, retries, 0.0)
            except RuntimeError as error:
                response = json.dumps({"runner_error": str(error)}, ensure_ascii=False)
            row = _raw_row(item, MODEL, VERSION, response)
            rows.append(row)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            manifest = checkpoint()
            print(f"Processed {index}/{len(items)}", flush=True)
    if any(file_sha256(Path(name)) != digest for name, digest in {
            str(pack_dir / "annotation_manifest.json"): inputs["annotation_manifest"],
            str(pack_dir / "annotation_items.jsonl"): inputs["annotation_items"],
            str(PROMPT): inputs["prompt"]}.items()) or code != {
                "wuhan_protocol_v3.py": file_sha256(MODULE),
                "wuhan_model_v3.py": file_sha256(Path(__file__)),
                "run_model.py": file_sha256(Path(__file__).with_name("run_model.py"))}:
        raise RuntimeError("v3 source or code changed during model run")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    try:
        result = run(args.pack_dir, args.output_dir,
                     api_key=os.getenv("MARKETMIRROR_LLM_API_KEY", ""),
                     timeout=args.timeout, retries=args.retries, resume=args.resume)
        print(json.dumps({"experiment_id": result["pack_experiment_id"],
                          "model_rows": result["model_rows"],
                          "request_failures": result["request_failures"]}, ensure_ascii=False))
    except (ValueError, RuntimeError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
