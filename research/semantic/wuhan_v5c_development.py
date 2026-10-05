"""Run the minimally changed v5c prompt on the seen development set.

The archived v5a runner remains fixed; this wrapper records its own code hash
and a distinct prompt/version in every checkpoint. It never emits Agent signals.
"""

from __future__ import annotations

from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from . import wuhan_v5_development as runner


MODULE = Path(__file__).resolve()
ORIGINAL_CODE_HASHES = runner._code_hashes


def _code_hashes() -> dict[str, str]:
    return {**ORIGINAL_CODE_HASHES(), MODULE.name: file_sha256(MODULE)}


def main() -> None:
    runner.VERSION = "wuhan-v5c-seen-development-v1"
    runner.PROMPT_VERSION = "wuhan-company-confirmed-v5c-development"
    runner.PROMPT = MODULE.with_name("PROMPT_WUHAN_V5C_CANDIDATE.md")
    runner.DEFAULT_OUTPUT = runner.OUTPUTS / "wuhan_v5c_development_candidate_v1"
    runner._code_hashes = _code_hashes
    runner.main()


if __name__ == "__main__":
    main()
