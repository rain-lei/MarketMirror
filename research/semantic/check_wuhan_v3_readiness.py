"""Check the local prerequisites for a real Wuhan v3 model run.

This command is deliberately offline.  It validates the frozen input pack,
the process environment, and the requested output directories; it never
contacts the gateway and never prints the API key or its value.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .wuhan_protocol_v3 import BASE_URL, MODEL, VERSION, verify_pack


def _directory_state(path: Path) -> str:
    if not path.exists():
        return "missing"
    if not path.is_dir():
        return "not_directory"
    return "empty" if not any(path.iterdir()) else "non_empty"


def check(pack_dir: Path, output_dirs: list[Path]) -> dict:
    """Return a non-secret readiness report without making network requests."""
    pack_dir = pack_dir.resolve()
    checks: dict[str, bool] = {}
    details: dict[str, object] = {}
    try:
        items, manifest = verify_pack(pack_dir)
        checks["frozen_pack"] = True
        details["experiment_id"] = manifest["experiment_id"]
        details["items"] = len(items)
        details["protocol"] = manifest["frozen_model_protocol"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        checks["frozen_pack"] = False
        details["pack_error"] = type(error).__name__

    key = os.getenv("MARKETMIRROR_LLM_API_KEY", "")
    checks["api_key_present"] = bool(key.strip())
    # Only report presence.  Length, prefix, and repr can all aid secret
    # recovery when logs are retained, so they are intentionally omitted.
    details["api_key_source"] = "process_environment"
    details["gateway_base_url"] = BASE_URL
    details["model_id"] = MODEL
    details["prompt_version"] = VERSION

    states = {str(path.resolve()): _directory_state(path) for path in output_dirs}
    checks["output_directories_ready"] = all(state in ("missing", "empty")
                                             for state in states.values())
    checks["distinct_output_directories"] = len({str(path.resolve()) for path in output_dirs}) == len(output_dirs)
    checks["output_directories_outside_pack"] = all(
        path.resolve() != pack_dir and pack_dir not in path.resolve().parents
        for path in output_dirs
    )
    details["output_directories"] = states
    details["network_contacted"] = False
    details["ready"] = all(checks.values())
    return {"checks": checks, "details": details}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_dir", type=Path)
    parser.add_argument("--output-dir", action="append", type=Path, required=True,
                        dest="output_dirs",
                        help="a future raw/normalized/score directory; repeat three times")
    args = parser.parse_args()
    report = check(args.pack_dir, args.output_dirs)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["details"]["ready"] else 2)


if __name__ == "__main__":
    main()
