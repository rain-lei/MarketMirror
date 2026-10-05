"""Independently recompute issuer experiment return and industry statistics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from . import verify_pre_wuhan_industry_response_2019 as independent_statistics

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "research_outputs/pre_wuhan_issuer_valuation_2019_v1"
OUTPUT = ROOT / "research_outputs/pre_wuhan_issuer_valuation_statistics_2019_v1.json"


def compute() -> dict:
    # Reuse the existing independent stdlib verifier without editing its source
    # or changing its archived industry output. Its fixed panel dimensions are
    # identical here (123 stocks, 43 sessions, 5 variants).
    previous = independent_statistics.SOURCE
    independent_statistics.SOURCE = SOURCE
    try:
        result = independent_statistics.compute()
    finally:
        independent_statistics.SOURCE = previous
    result["pipeline_version"] = "pre-wuhan-issuer-valuation-stdlib-statistics-v1"
    result["code_sha256"] = {str(Path(__file__).resolve()): file_sha256(Path(__file__)),
                              str(Path(independent_statistics.__file__).resolve()): file_sha256(Path(independent_statistics.__file__))}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("issuer statistics output must be in research_outputs")
    result = compute()
    payload = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if args.audit_existing:
        if output.read_bytes() != payload:
            raise ValueError("issuer statistics archive differs byte-for-byte")
    else:
        with output.open("xb") as handle:
            handle.write(payload)
    print(json.dumps({key: value for key, value in result.items() if key not in {"inputs", "code_sha256"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
