"""Call an OpenAI-compatible chat endpoint and archive source-bound raw outputs.

The API key is read only from ``MARKETMIRROR_LLM_API_KEY``.  This command does
not parse or score model output; ``parse_model_outputs`` remains the fail-closed
normalization step.  Raw responses stay in the ignored research output folder.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .signal_validation import load_pack
from ..data_pipeline.provenance import file_sha256


VERSION = "semantic-model-runner-v3"
DEFAULT_BASE_URL = "http://aigw.dlut.edu.cn/v1"
DEFAULT_MODEL = "DeepSeek-V4-Flash-0731-W8A8"
PROMPT_PATH = Path(__file__).with_name("PROMPT_V1.md")
PROMPT_VERSION = "semantic-prompt-v1"
RAW_FIELDS = {"item_id", "source_text_sha256", "model_id", "prompt_version", "raw_response"}


def normalize_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
    if not value.endswith("/v1"):
        value += "/v1"
    return value


def endpoint_url(base_url: str) -> str:
    return normalize_base_url(base_url) + "/chat/completions"


def list_models(base_url: str, api_key: str, timeout: float = 20.0) -> list[str]:
    """Read model IDs from the gateway without sending any source text."""
    if not api_key.strip():
        raise ValueError("MARKETMIRROR_LLM_API_KEY is empty")
    request = urllib.request.Request(
        normalize_base_url(base_url) + "/models",
        headers={"Authorization": f"Bearer {api_key}"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        status = f" HTTP {error.code}" if isinstance(error, urllib.error.HTTPError) else ""
        raise RuntimeError(f"model listing failed{status}: {type(error).__name__}") from error
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise RuntimeError("model listing response has no data array")
    return sorted({str(row["id"]) for row in data if isinstance(row, dict) and row.get("id")})


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _content(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("API response has no choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("API response choice has no message")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [part.get("text", "") for part in content if isinstance(part, dict)]
        if all(isinstance(part, str) for part in parts):
            return "".join(parts)
    raise ValueError("API response message content is not text")


def request_completion(url: str, api_key: str, model: str, messages: list[dict[str, str]],
                       timeout: float, retries: int, temperature: float) -> str:
    body = {"model": model, "messages": messages, "temperature": temperature}
    request = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST")
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return _content(payload)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
            last_error = error
            if isinstance(error, urllib.error.HTTPError) and error.code not in (408, 409, 425, 429) and error.code < 500:
                break
            if attempt < retries:
                time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"model request failed after {retries + 1} attempts: {type(last_error).__name__}") from last_error


def _raw_row(item: dict[str, Any], model: str, prompt_version: str, raw_response: str) -> dict[str, Any]:
    return {"item_id": item["item_id"], "source_text_sha256": item["source_text_sha256"],
            "model_id": model, "prompt_version": prompt_version, "raw_response": raw_response}


def _read_raw_rows(path: Path, items: dict[str, dict[str, Any]], model: str,
                   contents: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not path.exists():
        return rows
    for line in (path.read_text(encoding="utf-8") if contents is None else contents).splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or set(row) != RAW_FIELDS:
            raise ValueError("existing model output has invalid fields")
        item_id = row.get("item_id")
        if item_id not in items or item_id in seen:
            raise ValueError("existing model output has unknown or duplicate item")
        if row["source_text_sha256"] != items[item_id]["source_text_sha256"]:
            raise ValueError("existing model output source hash differs from annotation pack")
        if row["model_id"] != model or row["prompt_version"] != PROMPT_VERSION:
            raise ValueError("existing model output uses a different model or prompt")
        if not isinstance(row["raw_response"], str):
            raise ValueError("existing model output raw_response must be text")
        rows.append(row)
        seen.add(item_id)
    return rows


def _is_runner_failure(row: dict[str, Any]) -> bool:
    try:
        value = json.loads(row["raw_response"])
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(value, dict) and "runner_error" in value


def run_model(pack_dir: Path, output_dir: Path, api_key: str, base_url: str = DEFAULT_BASE_URL,
              model: str = DEFAULT_MODEL, limit: int | None = None, timeout: float = 90.0,
              retries: int = 2, temperature: float = 0.0, resume: bool = False) -> dict[str, Any]:
    pack_dir, output_dir = pack_dir.resolve(), output_dir.resolve()
    items, pack_manifest = load_pack(pack_dir)
    if not api_key.strip():
        raise ValueError("MARKETMIRROR_LLM_API_KEY is empty")
    if output_dir == pack_dir or pack_dir in output_dir.parents:
        raise ValueError("model output must be separate from annotation pack")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    if output_dir.exists() and any(output_dir.iterdir()) and not resume:
        raise ValueError("use a new empty model output directory")
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    selected = list(items.values())[:limit] if limit is not None else list(items.values())
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / "model_raw_outputs.jsonl"
    manifest_path = output_dir / "model_run_manifest.json"
    if resume and output_dir.exists() and any(output_dir.iterdir()):
        allowed = {raw_path.name, manifest_path.name, manifest_path.with_suffix(".json.tmp").name}
        unexpected = {path.name for path in output_dir.iterdir()} - allowed
        if unexpected or not raw_path.exists() or not manifest_path.exists():
            raise ValueError("resume output must contain only model_raw_outputs.jsonl and model_run_manifest.json")
    input_sha256 = {"annotation_manifest": file_sha256(pack_dir / "annotation_manifest.json"),
                    "annotation_items": file_sha256(pack_dir / "annotation_items.jsonl"),
                    "prompt": file_sha256(PROMPT_PATH)}
    rows = []
    selected_ids = {item["item_id"] for item in selected}
    if resume and manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (previous.get("pack_experiment_id") != pack_manifest["experiment_id"]
                or previous.get("input_sha256") != input_sha256
                or previous.get("provider_base_url") != normalize_base_url(base_url)
                or previous.get("model_id") != model
                or previous.get("prompt_version") != PROMPT_VERSION
                or previous.get("temperature") != temperature):
            raise ValueError("resume output provenance does not match current request")
        raw_bytes = raw_path.read_bytes()
        committed_bytes = previous.get("raw_bytes", len(raw_bytes))
        if (type(committed_bytes) is not int or committed_bytes < 0
                or committed_bytes > len(raw_bytes)):
            raise ValueError("resume checkpoint has invalid raw byte count")
        committed = raw_bytes[:committed_bytes]
        declared = previous.get("artifacts", {}).get(raw_path.name, {}).get("sha256")
        if hashlib.sha256(committed).hexdigest() != declared:
            raise ValueError("resume checkpoint raw output hash mismatch")
        # Bytes appended after the last committed manifest are uncommitted. They
        # are discarded and that item is requested again, including a torn line.
        rows = _read_raw_rows(raw_path, items, model, committed.decode("utf-8"))
        if len(rows) != previous.get("model_rows"):
            raise ValueError("resume checkpoint row count mismatch")
        if any(row["item_id"] not in selected_ids for row in rows):
            raise ValueError("resume limit excludes already completed items; omit --limit")
        # Failed requests are replaced on resume so the normalized file remains unique by item_id.
        rows = [row for row in rows if not _is_runner_failure(row)]
    existing_ids = {row["item_id"] for row in rows}
    pending = [item for item in selected if item["item_id"] not in existing_ids]
    # Rewrite retained rows before starting. Every subsequent response is flushed immediately.
    with raw_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    def checkpoint() -> dict[str, Any]:
        manifest = {
            "pipeline_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
            "provider_base_url": normalize_base_url(base_url), "model_id": model,
            "prompt_version": PROMPT_VERSION, "temperature": temperature, "resumed": resume,
            "pack_experiment_id": pack_manifest["experiment_id"],
            "input_sha256": input_sha256,
            "code_sha256": {"run_model.py": file_sha256(Path(__file__))},
            "requested_rows": len(selected), "model_rows": len(rows),
            "remaining_rows": len(selected) - len(rows),
            "request_failures": sum(_is_runner_failure(row) for row in rows),
            "raw_bytes": raw_path.stat().st_size,
            "artifacts": {raw_path.name: {"sha256": file_sha256(raw_path)}}}
        temporary = manifest_path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(manifest_path)
        return manifest

    manifest = checkpoint()
    url = endpoint_url(base_url)
    with raw_path.open("a", encoding="utf-8") as handle:
        for index, item in enumerate(pending, start=len(rows) + 1):
            user_message = _json_text({"segments": item["segments"]})
            try:
                response = request_completion(
                    url, api_key, model,
                    [{"role": "system", "content": prompt}, {"role": "user", "content": user_message}],
                    timeout, retries, temperature)
            except RuntimeError as error:
                response = _json_text({"runner_error": str(error)})
            row = _raw_row(item, model, PROMPT_VERSION, response)
            rows.append(row)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            manifest = checkpoint()
            print(f"Processed {index}/{len(selected)}", flush=True)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_dir", type=Path, nargs="?")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--check", action="store_true", help="only list gateway models; do not upload annotation text")
    parser.add_argument("--base-url", default=os.getenv("MARKETMIRROR_LLM_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--model", default=os.getenv("MARKETMIRROR_LLM_MODEL", DEFAULT_MODEL))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--resume", action="store_true", help="resume an existing output directory")
    args = parser.parse_args()
    key = os.getenv("MARKETMIRROR_LLM_API_KEY", "")
    try:
        if args.check:
            models = list_models(args.base_url, key, args.timeout)
            result = {"base_url": normalize_base_url(args.base_url), "requested_model": args.model,
                      "available": args.model in models, "model_count": len(models), "models": models}
            print(json.dumps(result, ensure_ascii=False))
            return
        if args.pack_dir is None or args.output_dir is None:
            parser.error("pack_dir and --output-dir are required unless --check is used")
        result = run_model(args.pack_dir, args.output_dir, key, args.base_url, args.model,
                           args.limit, args.timeout, args.retries, args.temperature, args.resume)
        print(json.dumps({key: result[key] for key in ("model_id", "requested_rows", "request_failures")}, ensure_ascii=False))
    except (ValueError, RuntimeError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
