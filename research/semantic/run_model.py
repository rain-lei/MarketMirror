"""Call an OpenAI-compatible chat endpoint and archive source-bound raw outputs.

The API key is read only from ``MARKETMIRROR_LLM_API_KEY``.  This command does
not parse or score model output; ``parse_model_outputs`` remains the fail-closed
normalization step.  Raw responses stay in the ignored research output folder.
"""

from __future__ import annotations

import argparse
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


VERSION = "semantic-model-runner-v2"
DEFAULT_BASE_URL = "http://aigw.dlut.edu.cn/v1"
DEFAULT_MODEL = "DeepSeek-V4-Flash-0731-W8A8"
PROMPT_PATH = Path(__file__).with_name("PROMPT_V1.md")


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


def run_model(pack_dir: Path, output_dir: Path, api_key: str, base_url: str = DEFAULT_BASE_URL,
              model: str = DEFAULT_MODEL, limit: int | None = None, timeout: float = 90.0,
              retries: int = 2, temperature: float = 0.0) -> dict[str, Any]:
    pack_dir, output_dir = pack_dir.resolve(), output_dir.resolve()
    items, pack_manifest = load_pack(pack_dir)
    if not api_key.strip():
        raise ValueError("MARKETMIRROR_LLM_API_KEY is empty")
    if output_dir == pack_dir or pack_dir in output_dir.parents:
        raise ValueError("model output must be separate from annotation pack")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty model output directory")
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    selected = list(items.values())[:limit] if limit is not None else list(items.values())
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    failures = 0
    url = endpoint_url(base_url)
    for index, item in enumerate(selected, start=1):
        user_message = _json_text({"segments": item["segments"]})
        try:
            response = request_completion(
                url, api_key, model,
                [{"role": "system", "content": prompt}, {"role": "user", "content": user_message}],
                timeout, retries, temperature)
        except RuntimeError as error:
            failures += 1
            response = _json_text({"runner_error": str(error)})
        rows.append(_raw_row(item, model, "semantic-prompt-v1", response))
        print(f"Processed {index}/{len(selected)}", flush=True)
    raw_path = output_dir / "model_raw_outputs.jsonl"
    with raw_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    manifest = {
        "pipeline_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider_base_url": normalize_base_url(base_url), "model_id": model,
        "prompt_version": "semantic-prompt-v1", "temperature": temperature,
        "pack_experiment_id": pack_manifest["experiment_id"],
        "input_sha256": {"annotation_manifest": file_sha256(pack_dir / "annotation_manifest.json"),
                         "annotation_items": file_sha256(pack_dir / "annotation_items.jsonl"),
                         "prompt": file_sha256(PROMPT_PATH)},
        "requested_rows": len(selected), "model_rows": len(rows), "request_failures": failures,
        "artifacts": {"model_raw_outputs.jsonl": {"sha256": file_sha256(raw_path)}}}
    (output_dir / "model_run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
    args = parser.parse_args()
    key = os.getenv("MARKETMIRROR_LLM_API_KEY", "")
    if args.check:
        models = list_models(args.base_url, key, args.timeout)
        result = {"base_url": normalize_base_url(args.base_url), "requested_model": args.model,
                  "available": args.model in models, "model_count": len(models), "models": models}
        print(json.dumps(result, ensure_ascii=False))
        return
    if args.pack_dir is None or args.output_dir is None:
        parser.error("pack_dir and --output-dir are required unless --check is used")
    result = run_model(args.pack_dir, args.output_dir, key, args.base_url, args.model,
                       args.limit, args.timeout, args.retries, args.temperature)
    print(json.dumps({key: result[key] for key in ("model_id", "requested_rows", "request_failures")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
