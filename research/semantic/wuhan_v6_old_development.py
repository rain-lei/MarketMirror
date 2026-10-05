"""Run the v6 candidate on the already consumed 105-item development cohort.

This adapter reuses the v5 development pipeline's checkpoint and scoring code,
but binds its own v6 prompt, version and source hash into every result. It never
produces an Agent signal or an independent validation score.
"""

from __future__ import annotations

import argparse
import json
from contextlib import contextmanager
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from . import wuhan_v5_development as runner
from .annotation_pack import canonical_hash
from .local_credential import load_api_key


MODULE = Path(__file__).resolve()
VERSION = "wuhan-v6-old-seen-development-v1"
PROMPT_VERSION = "wuhan-company-confirmed-v6-old-development-candidate"
PROMPT = MODULE.with_name("PROMPT_WUHAN_V6_CANDIDATE.md")
DEFAULT_OUTPUT = runner.OUTPUTS / "wuhan_v6_old_seen_development_candidate_v1"


@contextmanager
def configured_runner():
    """Temporarily bind the legacy 105-item runner to a distinct v6 identity."""
    original = {name: getattr(runner, name) for name in
                ("VERSION", "PROMPT_VERSION", "PROMPT", "DEFAULT_OUTPUT", "_code_hashes", "preflight")}

    def code_hashes() -> dict[str, str]:
        return {**original["_code_hashes"](), MODULE.name: file_sha256(MODULE)}

    def preflight():
        items, labels, identity = original["preflight"]()
        inputs = {"v6_candidate_prompt" if name == "v5_candidate_prompt" else name: digest
                  for name, digest in identity["inputs"].items()}
        identity = {**identity, "inputs": inputs}
        identity["experiment_id"] = canonical_hash({
            "version": VERSION,
            "source_experiment_id": identity["source_experiment_id"],
            "v3_pack_experiment_id": runner.verify_v3_pack(runner.V3_PACK)[1]["experiment_id"],
            "inputs": inputs, "code": identity["code"],
        })
        return items, labels, identity

    try:
        runner.VERSION = VERSION
        runner.PROMPT_VERSION = PROMPT_VERSION
        runner.PROMPT = PROMPT
        runner.DEFAULT_OUTPUT = DEFAULT_OUTPUT
        runner._code_hashes = code_hashes
        runner.preflight = preflight
        yield runner
    finally:
        for name, value in original.items():
            setattr(runner, name, value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--preflight", action="store_true",
                        help="verify existing 105-item source and prompt without model requests")
    args = parser.parse_args()
    with configured_runner() as active:
        items, _, identity = active.preflight()
        if args.preflight:
            print(json.dumps({"items": len(items), "experiment_id": identity["experiment_id"],
                              "prompt_sha256": identity["inputs"]["v6_candidate_prompt"],
                              "sample_role": "seen_development_only", "agent_signal_eligible": False},
                             ensure_ascii=False))
            return
        key = load_api_key()
        try:
            active.run_raw(args.output_root, api_key=key, resume=args.resume)
        finally:
            key = None
        result = active.normalize_and_score(args.output_root)
        print(json.dumps({"items": len(items),
                          "model_f1": result["model"]["event_detection"]["f1"],
                          "type_macro_f1": result["model"]["event_type_macro_f1_supported"],
                          "keyword_f1": result["keyword"]["event_detection"]["f1"],
                          "development_targets_met": result["development_targets"]["met"],
                          "agent_signal_eligible": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
