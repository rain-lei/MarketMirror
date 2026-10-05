"""Build an expanded, source-bound policy calendar without equity assumptions."""

from __future__ import annotations

import argparse
import copy
import json
import tempfile
from collections import Counter
from pathlib import Path

import pdfplumber

from . import fetch_pbc_omo_2019, fetch_pre_wuhan_policy_sources_2019, monetary_operations, policy_calendar
from . import prepare_pre_wuhan_monetary_context_2019 as legacy_context
from .fetch_pre_wuhan_policy_sources_2019 import CONFIG as ACQUISITION_CONFIG, select_sources
from .monetary_operations import annotate_rate_changes, parse_bulletin
from .policy_calendar import build_calendar, parse_lpr, parse_rrr, parse_rrr_legal_text
from .provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/pre_wuhan_policy_calendar_2019.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_policy_calendar_2019_v1"


def compute() -> tuple[dict, dict, dict, dict]:
    settings = json.loads(CONFIG.read_text(encoding="utf-8"))
    raw_dir = ROOT / settings["raw_directory"]
    legacy_manifest = ROOT / settings["legacy_context_manifest_path"]
    review_path = ROOT / settings["legal_review_path"]
    required = {raw_dir / "manifest.json": settings["raw_manifest_sha256"],
                ACQUISITION_CONFIG: settings["acquisition_config_sha256"],
                legacy_manifest: settings["legacy_context_manifest_sha256"], review_path: settings["legal_review_sha256"]}
    if any(file_sha256(path) != value for path, value in required.items()):
        raise ValueError("expanded policy raw manifest, selection, legacy context or legal review changed")
    old_facts, old_clock, bindings, old_code = legacy_context.compute()
    bindings.update({str(path): value for path, value in required.items()})
    bindings[str(CONFIG)] = file_sha256(CONFIG)
    original_manifest = json.loads(legacy_manifest.read_text(encoding="utf-8"))
    for name, saved in original_manifest["artifacts"].items():
        path = legacy_manifest.parent / name
        if file_sha256(path) != saved["sha256"]:
            raise ValueError("legacy policy source artifacts changed")
        bindings[str(path)] = saved["sha256"]
    if old_code != original_manifest["code_sha256"]:
        raise ValueError("legacy monetary producer code differs from the frozen version")
    records = copy.deepcopy(old_facts["records"])
    for record in records:
        record.update({"source_id": Path(record["source"]["archive_path"]).stem, "kind": "omo",
                       "publication_precision": "second", "available_at": record["publication_timestamp"],
                       "availability_rule": "historical_page_second_clock", "policy_measures": []})
    acquisition = json.loads(ACQUISITION_CONFIG.read_text(encoding="utf-8"))
    selected, selection_bindings = select_sources(acquisition)
    bindings.update(selection_bindings)
    raw = json.loads((raw_dir / "manifest.json").read_text(encoding="utf-8"))
    bindings.update(raw["inputs"])
    if any(file_sha256(Path(name)) != value for name, value in {**bindings, **raw["code_sha256"]}.items()):
        raise ValueError("expanded policy input or acquisition code differs")
    selected_by_id = {row["source_id"]: row for row in selected}
    if len(raw["records"]) != len(selected) or {row["source_id"] for row in raw["records"]} != set(selected_by_id):
        raise ValueError("expanded raw responses omit or duplicate selected sources")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if review["reviewer_kind"] != "single_assistant" or review["visually_inspected_pages"] != [1, 2] or review["publication_timestamp"] is not None:
        raise ValueError("RRR legal review must be an explicit source-only visual review")
    for image in review["rendered_pages"]:
        path = ROOT / image["path"]
        if file_sha256(path) != image["sha256"]:
            raise ValueError("RRR legal visual source evidence changed")
        bindings[str(path)] = image["sha256"]
    legal = []
    for row in raw["records"]:
        if row["status"] != "FETCHED" or any(row.get(key) != value for key, value in selected_by_id[row["source_id"]].items()):
            raise ValueError("expanded policy source failed or changed identity")
        path = raw_dir / row["archive_name"]
        if path.parent != raw_dir or file_sha256(path) != row["sha256"]:
            raise ValueError("expanded original policy response changed")
        bindings[str(path)] = row["sha256"]
        source = {"url": row["final_url"], "discovery_url": row["url"], "request_url": row.get("request_url", row["url"]),
                  "archive_path": str(path.relative_to(ROOT)).replace("\\", "/"), "sha256": row["sha256"],
                  "retrieved_at_utc": row["retrieved_at_utc"], "source_kind": "official_republication" if row["kind"] == "rrr_republication" else "official_primary",
                  "publication_time_note": "Chinese page time interpreted as +08:00; not proof of the first publication across all channels. Current response is not a certified unrevised historical snapshot."}
        if row["kind"] == "rrr_legal_pdf":
            if review["source_path"] != source["archive_path"] or review["source_sha256"] != source["sha256"]:
                raise ValueError("RRR visual decimal review belongs to different legal source bytes")
            with pdfplumber.open(path) as document:
                pages = [page.extract_text() for page in document.pages]
            parsed = parse_rrr_legal_text(pages, review["approved_decimal_aliases"])
            legal.append({**parsed, "source_id": row["source_id"], "source": source, "extracted_text_pages": pages,
                          "text_extractor": {"library": "pdfplumber", "version": pdfplumber.__version__},
                          "visual_review_sha256": settings["legal_review_sha256"]})
            continue
        if row["kind"] == "omo":
            parsed = parse_bulletin(path.read_bytes(), row["publication_date"], row["title"])
            parsed.update({"publication_precision": "second", "available_at": parsed["publication_timestamp"],
                           "availability_rule": "historical_page_second_clock", "policy_measures": []})
        elif row["kind"] == "lpr":
            parsed = parse_lpr(path.read_bytes(), row["publication_date"], row["title"])
        elif row["kind"] == "rrr_republication":
            parsed = parse_rrr(path.read_bytes(), row["publication_date"], row["title"])
        else:
            raise ValueError("expanded policy source kind is unsupported")
        records.append({**parsed, "kind": row["kind"], "source_id": row["source_id"], "source": source})
    expected = settings["expected_counts"]
    kinds = Counter(record["kind"] for record in records)
    if kinds != {"omo": expected["legacy_omo"] + expected["earlier_omo"], "lpr": expected["lpr_announcements"], "rrr_republication": expected["rrr_announcements"]} or len(legal) != expected["legal_documents"]:
        raise ValueError("expanded policy coverage differs from the frozen selection")
    months = [record["signed_date"][:7] for record in records if record["kind"] == "lpr"]
    if sorted(months) != [f"2019-{month:02d}" for month in range(8, 13)]:
        raise ValueError("reform-era monthly LPR source coverage has a gap or duplicate")
    rrr = next(record for record in records if record["kind"] == "rrr_republication")
    projection = [{key: measure[key] for key in ("measure_key", "effective_date", "change_percentage_points")} for measure in rrr["policy_measures"]]
    if projection != legal[0]["schedule"]:
        raise ValueError("RRR published announcement contradicts its official legal schedule")
    for measure in rrr["policy_measures"]:
        measure["corroborating_document_id"] = legal[0]["document_id"]
    records.sort(key=lambda row: (row["available_at"], row["source_id"]))
    annotate_rate_changes(records)
    clocks = [{key: row[key] for key in ("trade_date", "signal_cutoff_date")} for row in old_clock["calendar"]]
    if len(clocks) != expected["calendar_steps"]:
        raise ValueError("expanded policy experiment clock changed")
    calendar = build_calendar(records, clocks)
    changes = [{"trade_date": day["trade_date"], "signal_cutoff_date": day["signal_cutoff_date"],
                "time_split": "first_30_development_dates" if index < 30 else "last_13_development_dates", **change}
               for index, day in enumerate(calendar) for change in day["new_observed_rate_changes"]]
    facts = {"pipeline_version": "pre-wuhan-expanded-official-policy-facts-v1", "records": records, "legal_documents": legal,
             "coverage": {"announcement_sources": len(records), "corroborating_legal_documents": len(legal), "by_kind": dict(kinds),
                          "rate_observations_by_instrument": dict(Counter(operation["instrument"] for row in records for operation in row["operations"])),
                          "explicit_no_reverse_repo_notices": sum(record["explicit_no_reverse_repo"] is True for record in records),
                          "rrr_scheduled_measures": len(rrr["policy_measures"]), "calendar_steps": len(calendar),
                          "new_visible_nonzero_observed_rate_changes": changes}, "interpretation": settings["interpretation"]}
    context = {"pipeline_version": "pre-wuhan-policy-first-visible-and-scheduled-context-v1", "calendar": calendar,
               "use_policy": {"mode": "source_verified_context", "agent_signal_enabled": False},
               "availability_rule": settings["availability_rule"], "event_rule": settings["event_rule"], "interpretation": settings["interpretation"]}
    code = {str(Path(module.__file__).resolve()): file_sha256(Path(module.__file__)) for module in
            (fetch_pbc_omo_2019, fetch_pre_wuhan_policy_sources_2019, monetary_operations, policy_calendar, legacy_context)}
    code[str(Path(__file__).resolve())] = file_sha256(Path(__file__))
    return facts, context, bindings, code


def run(output: Path, audit_existing: bool) -> dict:
    output = output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve() or not audit_existing and output.exists():
        raise ValueError("expanded policy calendar requires a fresh direct research_outputs directory")
    facts, context, bindings, code = compute()
    with tempfile.TemporaryDirectory(prefix="expanded-policy-stage-", dir=output.parent) as temporary:
        stage = Path(temporary)
        for name, value in (("policy_facts.json", facts), ("lagged_policy_calendar.json", context)):
            (stage / name).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        manifest = {"pipeline_version": "pre-wuhan-expanded-policy-manifest-v1", "inputs": bindings, "code_sha256": code,
                    "artifacts": {name: {"sha256": file_sha256(stage / name)} for name in ("policy_facts.json", "lagged_policy_calendar.json")}}
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        if any(file_sha256(Path(name)) != value for name, value in {**bindings, **code}.items()):
            raise ValueError("expanded policy sources or code changed during computation")
        if audit_existing:
            if any((stage / name).read_bytes() != (output / name).read_bytes() for name in ("policy_facts.json", "lagged_policy_calendar.json", "manifest.json")):
                raise ValueError("expanded policy artifact differs byte-for-byte")
        else:
            stage.replace(output)
    return facts["coverage"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.output_dir, args.audit_existing), ensure_ascii=False))


if __name__ == "__main__":
    main()
