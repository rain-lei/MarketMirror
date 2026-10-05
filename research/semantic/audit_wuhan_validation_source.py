"""Verify that the next Wuhan company sample was fixed without label leakage."""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .event_snapshot import select_point_in_time_universe
from .signal_validation import load_pack


ROOT = Path(__file__).resolve().parents[2]
OLD_CONFIG = ROOT / "research/configs/wuhan_qna_active_universe_2020.json"
NEW_CONFIG = ROOT / "research/configs/wuhan_qna_independent_validation_universe_2020.json"
OLD_PACK = ROOT / "research_outputs/wuhan_pre_event_pit_snapshot_2020_v3"
NEW_PACK = ROOT / "research_outputs/wuhan_pre_event_validation_source_2020_v1"
DATABASE = ROOT / "research_outputs/unified/dataset.sqlite"
SEED_PREFIX = "marketmirror-wuhan-pit-independent-validation-v1-"


def audit() -> tuple[dict, dict[str, str]]:
    old = json.loads(OLD_CONFIG.read_text(encoding="utf-8"))
    new = json.loads(NEW_CONFIG.read_text(encoding="utf-8"))
    old_codes, new_codes = set(old["stock_codes"]), set(new["stock_codes"])
    old_sel, new_sel = old["selection"], new["selection"]
    if (len(old_codes) != 126 or len(new_codes) != 126 or old_codes & new_codes
            or any(new_sel[field] != old_sel[field] for field in
                   ("selection_rule", "sample_size", "qa_source_sha256", "question_window_start", "snapshot_as_of"))):
        raise ValueError("validation cohort size, cutoff, source or disjointness differs")
    seed = new_sel["seed"]
    if not seed.startswith(SEED_PREFIX) or not seed[len(SEED_PREFIX):].isdigit():
        raise ValueError("validation seed does not follow the first-disjoint search")
    number = int(seed[len(SEED_PREFIX):])
    with sqlite3.connect(DATABASE.as_uri() + "?mode=ro", uri=True) as connection:
        eligible = [row[0] for row in connection.execute(
            "SELECT DISTINCT stock_code FROM qa_record WHERE source_file_hash=? AND question_eligible=1 "
            "AND stock_code IS NOT NULL AND question_available_at>=? AND question_available_at<=?",
            (old_sel["qa_source_sha256"], "2020-01-01T00:00:00.000000+08:00",
             old_sel["snapshot_as_of"]))]
    if old["stock_codes"] != select_point_in_time_universe(eligible, old_sel["seed"], 126):
        raise ValueError("development cohort differs from source-derived selection")
    for candidate in range(number + 1):
        candidate_codes = select_point_in_time_universe(
            eligible, f"{SEED_PREFIX}{candidate:06d}", 126)
        if candidate == number:
            if candidate_codes != new["stock_codes"] or old_codes & set(candidate_codes):
                raise ValueError("validation cohort differs from first disjoint selection")
        elif not old_codes & set(candidate_codes):
            raise ValueError("an earlier seed already yielded a disjoint cohort")
    first_items, first_pack = load_pack(OLD_PACK)
    second_items, second_pack = load_pack(NEW_PACK)
    for pack in (first_pack, second_pack):
        for path, digest in pack["input_sha256"].items():
            if file_sha256(Path(path)) != digest:
                raise ValueError("snapshot source differs from archived hash")
        for name, digest in pack["code_sha256"].items():
            if file_sha256(ROOT / "research/semantic" / name) != digest:
                raise ValueError("snapshot builder differs from archived hash")
    if (first_pack["universe_size"] != 126 or second_pack["universe_size"] != 126
            or first_pack["snapshot_as_of"] != second_pack["snapshot_as_of"]
            or first_pack["source"]["sha256"] != second_pack["source"]["sha256"]
            or first_pack["counts"]["items"] != len(first_items)
            or second_pack["counts"]["items"] != len(second_items)):
        raise ValueError("old and new snapshot frames differ")
    if ({item["stock_code"] for item in first_items.values()}
            & {item["stock_code"] for item in second_items.values()}
            or {item["qa_id"] for item in first_items.values()}
            & {item["qa_id"] for item in second_items.values()}
            or {item["source_text_sha256"] for item in first_items.values()}
            & {item["source_text_sha256"] for item in second_items.values()}):
        raise ValueError("validation items overlap with development items")
    inputs = {str(path): file_sha256(path) for path in (
        OLD_CONFIG, NEW_CONFIG, DATABASE,
        OLD_PACK / "annotation_manifest.json", OLD_PACK / "annotation_items.jsonl",
        NEW_PACK / "annotation_manifest.json", NEW_PACK / "annotation_items.jsonl")}
    result = {"development_experiment_id": first_pack["experiment_id"],
              "validation_experiment_id": second_pack["experiment_id"],
              "source_question_companies": len(eligible), "first_disjoint_seed": seed,
              "first_disjoint_iteration": number, "development_companies": len(old_codes),
              "validation_companies": len(new_codes), "development_reply_items": len(first_items),
              "validation_reply_items": len(second_items),
              "validation_without_reply": len(new_codes) - len(second_items),
              "stock_overlap": 0, "qa_overlap": 0, "visible_text_overlap": 0,
              "checks": {"source_and_code_hashes": True, "source_derived_old_and_new_selection": True,
                         "first_disjoint_seed": True, "identity_and_text_disjointness": True}}
    return result, inputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("audit output must be a new empty directory")
    result, inputs = audit()
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        staging = Path(temporary)
        saved = staging / "wuhan_validation_source_audit.json"
        saved.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report = staging / "wuhan_validation_source_audit.md"
        report.write_text("# 武汉事前独立公司验证来源审计\n\n"
                          f"同一事前提问公司框 {result['source_question_companies']} 家，首个零重叠种子 "
                          f"`{result['first_disjoint_seed']}`。新旧各 126 家；新批次 "
                          f"{result['validation_reply_items']} 家有可见回复。公司、问答身份与可见文本哈希均零重叠。\n\n"
                          "仅验证来源选择；尚未证明新语义模型的质量。\n", encoding="utf-8")
        manifest = {"generated_at": datetime.now(timezone.utc).isoformat(), "inputs": inputs,
                    "audit_code_sha256": file_sha256(Path(__file__)),
                    "artifacts": {path.name: {"sha256": file_sha256(path)} for path in (saved, report)}}
        (staging / "wuhan_validation_source_audit_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
