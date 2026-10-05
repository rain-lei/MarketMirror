"""Validate a pack's frozen model request before calling or scoring it."""

from __future__ import annotations

from typing import Any

from ..data_pipeline.provenance import file_sha256
from .prompt_contract import prompt_path


FROZEN_GATEWAY = "http://aigw.dlut.edu.cn/v1"
LEGACY_FIELDS = ("frozen_model_id", "frozen_prompt_version", "frozen_prompt_sha256")


def frozen_model_protocol(pack_manifest: dict[str, Any], *, required: bool = False) -> dict[str, Any] | None:
    """Return a checked protocol, or None for an unfrozen development pack."""
    protocol = pack_manifest.get("frozen_model_protocol")
    if protocol is None:
        config = pack_manifest.get("config", {})
        if not isinstance(config, dict):
            raise ValueError("annotation pack config is invalid")
        if not any(field in config for field in LEGACY_FIELDS):
            if required:
                raise ValueError("annotation pack has no frozen model protocol")
            return None
        protocol = {"provider_base_url": FROZEN_GATEWAY,
                    "model_id": config.get("frozen_model_id"),
                    "prompt_version": config.get("frozen_prompt_version"),
                    "prompt_sha256": config.get("frozen_prompt_sha256"),
                    "temperature": 0}
    fields = {"provider_base_url", "model_id", "prompt_version", "prompt_sha256", "temperature"}
    if (not isinstance(protocol, dict) or set(protocol) != fields
            or protocol["provider_base_url"] != FROZEN_GATEWAY
            or not all(isinstance(protocol[name], str) and protocol[name].strip()
                       for name in ("model_id", "prompt_version", "prompt_sha256"))
            or len(protocol["prompt_sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in protocol["prompt_sha256"])
            or type(protocol["temperature"]) not in (int, float)
            or protocol["temperature"] != 0):
        raise ValueError("annotation pack does not carry a valid frozen model protocol")
    try:
        prompt_file = prompt_path(protocol["prompt_version"])
    except ValueError as error:
        raise ValueError("annotation pack freezes an unsupported prompt version") from error
    if file_sha256(prompt_file) != protocol["prompt_sha256"]:
        raise ValueError("frozen prompt differs from the current prompt file")
    return protocol
