"""Run the revised v5b prompt on the same seen development set.

The v5a runner is intentionally kept byte-for-byte fixed for its archived run.
This wrapper binds a new prompt/version and adds its own hash to the run identity.
Neither variant may generate an Agent signal from the seen development sample.
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
    runner.VERSION = "wuhan-v5b-seen-development-v1"
    runner.PROMPT_VERSION = "wuhan-company-confirmed-v5b-development"
    runner.PROMPT = MODULE.with_name("PROMPT_WUHAN_V5B_CANDIDATE.md")
    runner.DEFAULT_OUTPUT = runner.OUTPUTS / "wuhan_v5b_development_candidate_v1"
    runner._code_hashes = _code_hashes
    runner.main()


if __name__ == "__main__":
    main()
