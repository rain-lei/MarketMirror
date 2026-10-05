"""Join reviewed case facts and explicit hypothetical exposure to unchanged Agent rules."""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path

from ..semantic.source_case_facts import ROOT, asof_facts, validate_fact_record

MAPPING = ROOT / "research/configs/source_case_scenario_mapping_2026_v1.json"
MODES = ("no_text", "keywords", "reviewed_llm", "llm_asset_placebo")


def load_mapping(path=MAPPING):
    mapping = json.loads(Path(path).read_text(encoding="utf-8"))
    if (mapping["schema_version"] != "source-case-assumed-mechanism-mapping-v1"
            or tuple(mapping["modes"]) != MODES or mapping["sessions"] != 18
            or mapping["historical_returns_or_prices_applied"] is not False
            or mapping["empirical_investor_calibration"] is not False
            or mapping["actual_issuer_exposure_identified"] is not False):
        raise ValueError("explicit development scenario assumptions required")
    return mapping


def case_calendar(case, mapping):
    manifest_name = mapping["case_calendars"].get(case["case_id"],
        mapping["case_calendars"].get(case["source_kind"]))
    if not manifest_name:
        raise ValueError("registered source calendar missing")
    manifest = json.loads((ROOT/manifest_name).read_text(encoding="utf-8"))
    info = manifest["inputs"]["calendar"]
    path = Path(info["path"])
    if not path.is_relative_to(ROOT) or hashlib.sha256(path.read_bytes()).hexdigest() != info["sha256"]:
        raise ValueError("source calendar changed or outside repository")
    with path.open(encoding="utf-8-sig",newline="") as stream:
        rows = list(csv.DictReader(stream))
    dates = [r["trade_date"] for r in rows]
    if dates != sorted(set(dates)) or len(dates) < mapping["sessions"]+2:
        raise ValueError("source calendar incomplete or duplicate dates")
    available = datetime.fromisoformat(case["available_at"])
    eligible = [i for i,d in enumerate(dates)
                if datetime.fromisoformat(d+"T23:59:59.999999+08:00") >= available]
    if not eligible:
        raise ValueError("source publication after entire calendar")
    first_cutoff = max(0,eligible[0]-mapping["pre_information_trade_steps"])
    selected = dates[first_cutoff:first_cutoff+mapping["sessions"]+2]
    if len(selected) != mapping["sessions"]+2:
        raise ValueError("not enough post-publication source dates")
    return selected, {"manifest_path":manifest_name,"calendar_path":path.relative_to(ROOT).as_posix(),
                      "calendar_sha256":info["sha256"],"source_version_is_current_retrieval":True}


def mapped_information(case, record, mapping, mode, cutoff):
    if mode not in MODES:
        raise ValueError("registered text comparison mode required")
    validate_fact_record(case,record)
    available = asof_facts(case,record,cutoff)
    if not available["available"] or mode == "no_text":
        return {"available":available["available"],"signal":0.0,"uncertainty":0.0,
                "used_fact_indices":[],"unresolved_fact_indices":[],"evidence":[],
                "keyword_matches":[],"numeric_effect_is_assumed":True}
    if mode == "keywords":
        texts = [s["text"] for s in case["segments"]
                 if case["source_kind"] != "company_reply_workbook" or s["source"] == "reply"]
        matching = [rule for rule in mapping["keyword_rules"] if any(rule["token"] in t for t in texts)]
        return {"available":True,"signal":max(-1.0,min(1.0,sum(r["signal"] for r in matching))),
                "uncertainty":max([r["uncertainty"] for r in matching],default=0.0),
                "used_fact_indices":[],"unresolved_fact_indices":[],
                "evidence":[],"keyword_matches":[r["token"] for r in matching],
                "numeric_effect_is_assumed":True}
    seen,used,unresolved,evidence = set(),[],[],[]
    contributions = []
    for index,fact in enumerate(record["facts"]):
        rule = mapping["fact_mapping"][fact["kind"]]
        if rule["signal"] is None or rule["uncertainty"] is None:
            unresolved.append(index)
            continue
        evidence.extend(fact["evidence"])
        used.append(index)
        if fact["kind"] not in seen:
            contributions.append(rule)
            seen.add(fact["kind"])
    return {"available":True,"signal":max(-1.0,min(1.0,sum(c["signal"] for c in contributions))),
            "uncertainty":max([c["uncertainty"] for c in contributions],default=0.0),
            "used_fact_indices":used,"unresolved_fact_indices":unresolved,
            "evidence":evidence,"keyword_matches":[],"numeric_effect_is_assumed":True}


def case_inputs(case, record, mapping, mode):
    dates,calendar_info = case_calendar(case,mapping)
    joined = {a:[] for a in ("A","B","C")}
    links = []
    visible_count = 0
    exposure = mapping["placebo_exposed_asset"] if mode == "llm_asset_placebo" else mapping["hypothetical_exposed_asset"]
    for step in range(mapping["sessions"]):
        cutoff = dates[step]+"T23:59:59.999999+08:00"
        info = mapped_information(case,record,mapping,mode,cutoff)
        if info["available"]:
            visible_count += 1
        active = info["available"] and visible_count <= mapping["information_duration_steps"]
        identifier = f"source-case:{case['case_text_sha256']}:{mode}:cutoff:{dates[step]}"
        link = {"case_id":case["case_id"],"step":step,"signal_cutoff_at":cutoff,
                "source_available_at":case["available_at"],"source_visible":info["available"],
                "information_active":active,"exposed_asset":exposure,
                "exposure_is_assumed_not_real_issuer_mapping":True,
                "mapping":info if active else {**info,"signal":0.0,"uncertainty":0.0,
                    "used_fact_indices":[],"evidence":[],"keyword_matches":[]},
                "text_evidence_identifier":identifier,"calendar":calendar_info}
        links.append(link)
        for asset in joined:
            signal = info["signal"] if active and asset == exposure else 0.0
            uncertainty = info["uncertainty"] if active and asset == exposure else 0.0
            joined[asset].append({
                "trade_date":dates[step+2],"signal_cutoff_date":dates[step],
                "execution_reference_date":dates[step+1],"execution_available":True,
                "observed_return":0.0,"market_signal":0.0,"estimated_volatility":0.01,
                "text_signal":signal,"text_uncertainty":uncertainty,
                "text_evidence":identifier,
            })
    return joined,links
