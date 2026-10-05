"""Independently audit every archived Wuhan keyword Agent text observation.

This checks source identity, reply-only keyword provenance and every recorded
text observation against its own t-2 cutoff. Full current-code decision and
settlement validation is handled by audit_wuhan_portfolio_keyword.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from datetime import date, datetime
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .audit_wuhan_snapshot_source import PACK, ROOT, audit as audit_source
from .keyword_baseline import predict_item
from .signal_validation import load_pack, read_jsonl, validate_predictions


KEYWORDS = ROOT / "research_outputs/wuhan_pre_event_pit_keywords_2020_v3"
PORTFOLIO = ROOT / "research_outputs/wuhan_pit_portfolio_keyword_attention_2020_v2"
OUTPUT = ROOT / "research_outputs/wuhan_agent_input_isolation_audit_v2.json"
PORTFOLIO_ARTIFACTS = {"keyword_portfolio_ledger.jsonl.gz", "keyword_portfolio_paths.json",
                       "keyword_portfolio_report.md", "keyword_portfolio_summary.json"}


def expected_text(code: str, cutoff: str, reply_hits: dict[str, dict],
                  level: float) -> tuple[float, str]:
    day = date.fromisoformat(cutoff)
    item = reply_hits.get(code)
    if item is None or datetime.fromisoformat(item["available_at"]).date() > day:
        return 0.0, f"semantic:none-through:{cutoff}"
    evidence = hashlib.sha256(item["item_id"].encode("utf-8")).hexdigest()
    return float(level), f"semantic:{evidence}"


def audit() -> dict:
    source = audit_source()
    archived_source = ROOT / "research_outputs/wuhan_snapshot_source_audit_v1.json"
    if json.loads(archived_source.read_text(encoding="utf-8")) != source:
        raise ValueError("source-to-pack audit differs from its saved result")
    items, pack = load_pack(PACK)
    keyword_manifest_path = KEYWORDS / "keyword_manifest.json"
    keyword_manifest = json.loads(keyword_manifest_path.read_text(encoding="utf-8"))
    prediction_path = KEYWORDS / "keyword_predictions.jsonl"
    if (keyword_manifest.get("pack_experiment_id") != pack["experiment_id"]
            or keyword_manifest.get("input_sha256") != {
                "annotation_manifest.json": file_sha256(PACK / "annotation_manifest.json"),
                "annotation_items.jsonl": file_sha256(PACK / "annotation_items.jsonl")}
            or keyword_manifest.get("artifacts") != {
                "keyword_predictions.jsonl": {"sha256": file_sha256(prediction_path)}}
            or keyword_manifest.get("code_sha256") != {
                name: file_sha256(ROOT / "research/semantic" / name)
                for name in ("keyword_baseline.py", "signal_validation.py")}):
        raise ValueError("archived keyword prediction identity differs")
    predictions = read_jsonl(prediction_path)
    if (validate_predictions(items, predictions) != keyword_manifest["validation"]
            or len(predictions) != len(items)
            or predictions != [predict_item(item) for item in items.values()]):
        raise ValueError("keyword predictions cannot be reproduced from frozen items")
    reply_hits: dict[str, dict] = {}
    question_only = 0
    for prediction in predictions:
        if not prediction["events"]:
            continue
        event = prediction["events"][0]
        source_name = event["evidence_spans"][0]["source"]
        if source_name == "reply":
            item = items[prediction["item_id"]]
            if item["stock_code"] in reply_hits or item["stage"] != "reply":
                raise ValueError("reply keyword is repeated or lacks reply-stage evidence")
            reply_hits[item["stock_code"]] = item
        elif source_name == "question":
            question_only += 1
        else:
            raise ValueError("keyword hit has an unknown source")
    if len(reply_hits) + question_only != keyword_manifest["candidate_events"]:
        raise ValueError("keyword event provenance is incomplete")
    manifest_path = PORTFOLIO / "keyword_portfolio_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    summary = json.loads((PORTFOLIO / "keyword_portfolio_summary.json").read_text(encoding="utf-8"))
    paths = json.loads((PORTFOLIO / "keyword_portfolio_paths.json").read_text(encoding="utf-8"))
    if (set(manifest.get("artifacts", {})) != PORTFOLIO_ARTIFACTS
            or any(file_sha256(PORTFOLIO / name) != info["sha256"]
                   for name, info in manifest["artifacts"].items())
            or any(file_sha256(Path(name)) != digest for name, digest in manifest["inputs"].items())
            or summary.get("experiment_id") != manifest.get("experiment_id")
            or summary.get("reply_items") != len(items)
            or summary.get("reply_keyword_companies") != len(reply_hits)
            or summary.get("question_only_hits_excluded") != question_only
            or summary.get("paths") != len(paths)):
        raise ValueError("keyword portfolio archive, sources or counts differ")
    if len({path["path_id"] for path in paths}) != len(paths):
        raise ValueError("keyword portfolio path identities repeat")
    company_codes = set(json.loads((ROOT / "research/configs/wuhan_qna_active_universe_2020.json")
                                   .read_text(encoding="utf-8"))["stock_codes"])
    if not set(reply_hits) <= company_codes or len(company_codes) != 126:
        raise ValueError("keyword reply hits escape the frozen company cohort")
    days = observations = active_observations = 0
    with gzip.open(PORTFOLIO / "keyword_portfolio_ledger.jsonl.gz", "rt", encoding="utf-8") as stream:
        for path in paths:
            assets = path["assets"]
            level = path["attention_level"]
            if (len(assets) != 3 or not set(assets) <= company_codes or level not in (0.25, 1.0)
                    or path["use_text"] is not True or path["sessions"] != 35):
                raise ValueError("keyword portfolio path is outside the fixed design")
            previous_trade = None
            for _ in range(path["sessions"]):
                line = stream.readline()
                if not line:
                    raise ValueError("keyword portfolio ledger ended early")
                record = json.loads(line)
                trade, cutoff, reference = (record[name] for name in
                                            ("trade_date", "signal_cutoff_date", "execution_reference_date"))
                if (record["path_id"] != path["path_id"] or set(record["observations"]) != set(assets)
                        or not date.fromisoformat(cutoff) < date.fromisoformat(reference) < date.fromisoformat(trade)
                        or previous_trade is not None and trade <= previous_trade):
                    raise ValueError("keyword portfolio ledger time or path identity differs")
                previous_trade = trade
                for code in assets:
                    observation = record["observations"][code]
                    uncertainty, evidence = expected_text(code, cutoff, reply_hits, level)
                    if (observation["text_signal"] != 0.0
                            or observation["text_uncertainty"] != uncertainty
                            or observation["text_evidence"] != evidence):
                        raise ValueError("Agent text observation differs from visible reply keywords")
                    observations += 1
                    active_observations += int(uncertainty > 0)
                days += 1
        if stream.readline():
            raise ValueError("keyword portfolio ledger contains undeclared rows")
    if days != summary["ledger_rows"] or observations != summary["asset_call_rows"]:
        raise ValueError("keyword Agent observation coverage differs from archive")
    code_drift = [name for name, digest in manifest["code_sha256"].items()
                  if file_sha256(ROOT / "research" / name) != digest]
    return {"pipeline_version": "wuhan-agent-input-isolation-audit-v2",
            "snapshot_experiment_id": pack["experiment_id"],
            "keyword_experiment_id": summary["experiment_id"],
            "source_audit_sha256": file_sha256(archived_source),
            "portfolio_manifest_sha256": file_sha256(manifest_path),
            "audit_code_sha256": file_sha256(Path(__file__)),
            "cohort_companies": len(company_codes), "reply_items": len(items),
            "reply_keyword_companies": len(reply_hits), "question_only_hits_excluded": question_only,
            "paths": len(paths), "portfolio_days": days, "agent_asset_observations": observations,
            "active_reply_keyword_observations": active_observations,
            "archived_portfolio_code_drift": code_drift,
            "scope": "Checks every current-code archived text observation against reply-only source and the step cutoff. Source and artifacts are hash-verified; a separate full portfolio audit covers decisions and settlement."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = audit()
    if args.audit_existing:
        if json.loads(OUTPUT.read_text(encoding="utf-8")) != result:
            raise ValueError("saved Agent input audit differs from recomputation")
    else:
        if OUTPUT.exists():
            raise ValueError("Agent input audit output already exists")
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "cohort_companies", "reply_items", "reply_keyword_companies",
        "question_only_hits_excluded", "paths", "portfolio_days", "agent_asset_observations",
        "active_reply_keyword_observations", "archived_portfolio_code_drift")},
        ensure_ascii=False))


if __name__ == "__main__":
    main()
