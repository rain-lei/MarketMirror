"""Build source-bound OMO facts and strictly lagged context for the frozen clock."""

from __future__ import annotations

import argparse
import json
import tempfile
from collections import Counter
from pathlib import Path

from . import monetary_operations
from .fetch_pbc_omo_2019 import CONFIG as ACQUISITION_CONFIG, discover
from .monetary_operations import annotate_rate_changes, lagged_context, parse_bulletin
from .provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/pre_wuhan_monetary_context_2019.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_monetary_context_2019_v1"


def compute() -> tuple[dict, dict, dict, dict]:
    settings = json.loads(CONFIG.read_text(encoding="utf-8"))
    raw_dir = ROOT / settings["raw_directory"]
    if file_sha256(raw_dir / "manifest.json") != settings["raw_manifest_sha256"] or file_sha256(ACQUISITION_CONFIG) != settings["acquisition_config_sha256"]:
        raise ValueError("frozen original-response manifest or acquisition selection differs")
    config = json.loads(ACQUISITION_CONFIG.read_text(encoding="utf-8"))
    selected, bindings = discover(config)
    raw_manifest = json.loads((raw_dir / "manifest.json").read_text(encoding="utf-8"))
    bindings.update(raw_manifest["inputs"])
    bindings[str(raw_dir / "manifest.json")] = settings["raw_manifest_sha256"]
    bindings[str(CONFIG)] = file_sha256(CONFIG)
    if any(file_sha256(Path(name)) != value for name, value in bindings.items()):
        raise ValueError("monetary context frozen input hash differs")
    records = []
    selected_by_title = {row["title"]: row for row in selected}
    if len(raw_manifest["records"]) != len(selected):
        raise ValueError("raw bulletin count differs from full frozen discovery selection")
    for row in raw_manifest["records"]:
        if row["status"] != "FETCHED" or {key: row[key] for key in selected_by_title[row["title"]]} != selected_by_title[row["title"]]:
            raise ValueError("monetary context source missing or changed from discovery")
        path = raw_dir / row["archive_name"]
        if path.parent != raw_dir or file_sha256(path) != row["sha256"]:
            raise ValueError("original bulletin archive hash differs")
        bindings[str(path)] = row["sha256"]
        parsed = parse_bulletin(path.read_bytes(), row["publication_date"], row["title"])
        parsed["source"] = {"publisher": "中国人民银行公开市场业务操作室", "source_kind": "official_primary",
                            "url": row["url"], "archive_path": str(path.relative_to(ROOT)).replace("\\", "/"),
                            "sha256": row["sha256"], "retrieved_at_utc": row["retrieved_at_utc"],
                            "publication_time_note": "Historical page timestamp interpreted as Asia/Shanghai local time; not proof of the first publication across all channels. Current response is not a certified historical unrevised snapshot."}
        records.append(parsed)
    if {row["title"] for row in records} != set(selected_by_title):
        raise ValueError("monetary context duplicates or omits selected official bulletins")
    records.sort(key=lambda row: (row["publication_timestamp"], row["title"]))
    annotate_rate_changes(records)
    reference = ROOT / config["reference_path"]
    if file_sha256(reference) != config["reference_sha256"]:
        raise ValueError("monetary reference market clock changed")
    bindings[str(reference)] = config["reference_sha256"]
    original = json.loads(reference.read_text(encoding="utf-8"))["result"]
    rows = original["variants"][0]["daily_asset_rows"]
    pairs = Counter((row["trade_date"], row["signal_cutoff_date"]) for row in rows)
    if len(pairs) != 43 or set(pairs.values()) != {123}:
        raise ValueError("monetary reference clock is not a complete 123-stock, 43-day panel")
    calendar = [{"trade_date": day, "signal_cutoff_date": cutoff} for day, cutoff in sorted(pairs)]
    day_counts = Counter(row["signed_date"] for row in records)
    for day in {row["trade_date"] for row in calendar} | {row["signal_cutoff_date"] for row in calendar}:
        notices = [row for row in records if row["signed_date"] == day and row["gross_reverse_repo_amount_100m_yuan"] is not None]
        if len(notices) != 1:
            raise ValueError("monetary development or cutoff date lacks one explicit domestic reverse-repo declaration")
    context = lagged_context(records, calendar)
    counts = Counter(operation["instrument"] for row in records for operation in row["operations"])
    changes = [{"title": row["title"], "publication_timestamp": row["publication_timestamp"], **operation}
               for row in records for operation in row["operations"]
               if operation["change_from_previous_observed_bps"] not in {None, 0.0}]
    operations = {"pipeline_version": "pre-wuhan-pbc-omo-source-facts-v1", "records": records,
                  "coverage": {"bulletins": len(records), "publication_dates": len(day_counts), "development_dates": len(calendar),
                               "development_dates_with_complete_domestic_declaration": 43,
                               "explicit_no_reverse_repo_notices": sum(row["explicit_no_reverse_repo"] for row in records),
                               "operation_rows_by_instrument": dict(counts), "nonzero_rate_changes_from_previous_observation": changes},
                  "interpretation": settings["interpretation"]}
    lagged = {"pipeline_version": "pre-wuhan-pbc-omo-t-minus-two-context-v1", "calendar": context,
              "use_policy": {"mode": "source_verified_context", "agent_signal_enabled": False},
              "interpretation": "Every instrument observation is censored at its exact page timestamp against the archived t-2 end-of-day cutoff. Carrying the latest observed rate does not certify the standing policy rate; instrument absence and net liquidity remain unknown. No target-day return, stock-price direction or response coefficient is included."}
    code = {str(Path(__file__).resolve()): file_sha256(Path(__file__)),
            str(Path(monetary_operations.__file__).resolve()): file_sha256(Path(monetary_operations.__file__)),
            str(ROOT / "research/data_pipeline/fetch_pbc_omo_2019.py"): file_sha256(ROOT / "research/data_pipeline/fetch_pbc_omo_2019.py")}
    return operations, lagged, bindings, code


def run(output: Path, audit_existing: bool) -> dict:
    output = output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve() or not audit_existing and output.exists():
        raise ValueError("monetary context requires a fresh direct research_outputs directory")
    operations, lagged, inputs, code = compute()
    with tempfile.TemporaryDirectory(prefix="pbc-context-stage-", dir=output.parent) as temporary:
        stage = Path(temporary)
        for name, value in (("operations.json", operations), ("lagged_context.json", lagged)):
            (stage / name).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        manifest = {"pipeline_version": "pre-wuhan-pbc-omo-context-manifest-v1", "inputs": inputs, "code_sha256": code,
                    "artifacts": {name: {"sha256": file_sha256(stage / name)} for name in ("operations.json", "lagged_context.json")}}
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        if any(file_sha256(Path(name)) != value for name, value in {**inputs, **code}.items()):
            raise ValueError("monetary context input or code changed during computation")
        if audit_existing:
            if any((stage / name).read_bytes() != (output / name).read_bytes() for name in ("operations.json", "lagged_context.json", "manifest.json")):
                raise ValueError("monetary context artifact differs byte-for-byte")
        else:
            stage.replace(output)
    return operations["coverage"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.output_dir, args.audit_existing), ensure_ascii=False))


if __name__ == "__main__":
    main()
