"""Correct the QC summary while byte-checking the immutable v1 source read."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import extract_issuer_half_balance_sheets as original
from .fetch_issuer_disclosures import ROOT, digest, serialize, validate_bindings

CONFIG = ROOT / "research/configs/issuer_half_balance_sheets_2019_v2.json"
OUTPUT = ROOT / "research_outputs/issuer_half_balance_sheet_candidates_2019_v2.json"


def source_qc(companies: list[dict]) -> list[dict]:
    return [{"stock_code": c["stock_code"], "literal_reader_status": c["literal_reader_status"]}
            for c in companies if c["source_pdf_path"] is not None
            and c["literal_reader_status"] != "PASS_LITERAL_PAGE_MAPS"]


def compute() -> dict:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    if cfg["agent_signal_enabled"] is not False:
        raise ValueError("half balance v2 gate differs")
    validate_bindings(cfg["inputs"])
    value = original.compute()
    if (ROOT / cfg["original_output"]).read_bytes() != serialize(value):
        raise ValueError("v1 source read differs before QC-summary correction")
    qc = source_qc(value["companies"])
    value["summary"]["source_reader_qc_companies"] = len(qc)
    value.update(pipeline_version=cfg["version"], source_reader_qc_issuers=qc,
                 inputs={**value["inputs"], **cfg["inputs"], CONFIG.relative_to(ROOT).as_posix(): digest(CONFIG)},
                 code_sha256={**value["code_sha256"], Path(__file__).resolve().relative_to(ROOT).as_posix(): digest(Path(__file__))},
                 interpretation=cfg["interpretation"])
    validate_bindings({**value["inputs"], **value["code_sha256"]})
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("half balance v2 output must be directly under research_outputs")
    value = compute()
    raw = serialize(value)
    if args.audit_existing:
        if output.read_bytes() != raw:
            raise ValueError("half balance v2 reconstruction differs")
        print("Half-year balance v2 rebuilt byte-identically.")
    else:
        with output.open("xb") as handle:
            handle.write(raw)
    print(json.dumps(value["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
