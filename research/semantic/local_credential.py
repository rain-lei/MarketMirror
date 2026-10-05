"""Read and update the demo API key in the git-ignored local env file."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CREDENTIAL_PATH = REPO_ROOT / ".env.local"
KEY_NAME = "MARKETMIRROR_LLM_API_KEY"


def _key_from_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def save_api_key(api_key: str, path: Path = DEFAULT_CREDENTIAL_PATH) -> None:
    """Save the demo key to a git-ignored local environment file."""
    if not isinstance(api_key, str) or not api_key.strip() or "\n" in api_key or "\r" in api_key:
        raise ValueError("API key is empty or contains a line break")
    path = path.resolve()
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    updated: list[str] = []
    replaced = False
    for line in existing:
        candidate = line.strip()
        if candidate.startswith("export "):
            candidate = candidate[7:].lstrip()
        name = candidate.split("=", 1)[0].strip() if "=" in candidate else ""
        if name == KEY_NAME:
            if not replaced:
                updated.append(f"{KEY_NAME}={api_key}")
                replaced = True
            continue
        updated.append(line)
    if not replaced:
        updated.append(f"{KEY_NAME}={api_key}")

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".env-local-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(updated) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def load_api_key(path: Path = DEFAULT_CREDENTIAL_PATH) -> str:
    """Load the demo key without echoing or logging its value."""
    for line in path.read_text(encoding="utf-8").splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith("#"):
            continue
        if candidate.startswith("export "):
            candidate = candidate[7:].lstrip()
        if "=" not in candidate:
            continue
        name, value = candidate.split("=", 1)
        if name.strip() == KEY_NAME:
            api_key = _key_from_value(value)
            if not api_key:
                raise ValueError("local API key is empty")
            return api_key
    raise FileNotFoundError(f"{KEY_NAME} is not set in the local environment file")
