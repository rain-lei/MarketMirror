"""Explicit prompt versions; v1 offsets and v2 quotes are separate protocols."""

from pathlib import Path

DEFAULT_PROMPT_VERSION = "semantic-prompt-v1"
QUOTE_PROMPT_VERSION = "semantic-prompt-v2"
WUHAN_V4_PROMPT_VERSION = "wuhan-company-confirmed-v4"
PROMPTS = {
    DEFAULT_PROMPT_VERSION: Path(__file__).with_name("PROMPT_V1.md"),
    QUOTE_PROMPT_VERSION: Path(__file__).with_name("PROMPT_V2.md"),
    WUHAN_V4_PROMPT_VERSION: Path(__file__).with_name("PROMPT_WUHAN_V4.md"),
}


def prompt_path(version: str) -> Path:
    if version not in PROMPTS:
        raise ValueError("unsupported semantic prompt version")
    return PROMPTS[version]
