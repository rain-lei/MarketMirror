"""Independent regex and PyMuPDF check of expanded policy facts and clocks."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import fitz

from .audit_pre_wuhan_monetary_context_2019 import digest, plain, raw_facts

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "research_outputs/pre_wuhan_policy_calendar_2019_v1"
OUTPUT = ROOT / "research_outputs/pre_wuhan_policy_calendar_audit_2019_v1.json"
CHINA = timezone(timedelta(hours=8))


def div_region(raw: str, attribute: str, value: str) -> str:
    starts = list(re.finditer(r'<div\b[^>]*\b' + attribute + r'=["\x27]' + re.escape(value) + r'["\x27][^>]*>', raw, re.I))
    if len(starts) != 1:
        raise ValueError("independent policy HTML region is missing or ambiguous")
    suffix = re.sub(r"<(script|style)\b[^>]*>.*?</\1>|<!--.*?-->", "", raw[starts[0].end():], flags=re.S | re.I)
    depth = 1
    for match in re.finditer(r"</?div\b[^>]*>", suffix, re.I):
        depth += -1 if match[0].startswith("</") else 1
        if depth == 0:
            return suffix[:match.start()]
    raise ValueError("independent policy HTML region is unterminated")


def additional_facts(record: dict) -> tuple[list[dict], list[dict]]:
    raw = (ROOT / record["source"]["archive_path"]).read_text(encoding="utf-8")
    suffix = "_中国货币网" if record["kind"] == "lpr" else "_中共广东省委金融委员会办公室"
    titles = re.findall(r"<title\b[^>]*>(.*?)</title>", raw, re.S | re.I)
    if len(titles) != 1 or plain(titles[0]) != record["title"] + suffix:
        raise ValueError("independent policy page title differs")
    if record["kind"] == "lpr":
        clock = plain(div_region(raw, "class", "article-a-toolbar"))
        heading = plain(div_region(raw, "class", "title-heading"))
        body = plain(div_region(raw, "id", "ewebeditor_content"))
        if heading != record["title"]:
            raise ValueError("independent original LPR heading differs")
    else:
        clocks = re.findall(r'<li\b[^>]*\bclass=["\x27]c_time["\x27][^>]*>(.*?)</li>', raw, re.S | re.I)
        attributions = re.findall(r'<span\b[^>]*\bid=["\x27]ly["\x27][^>]*>(.*?)</span>', raw, re.S | re.I)
        if len(clocks) != 1 or len(attributions) != 1 or plain(attributions[0]) != "中国人民银行网站":
            raise ValueError("independent RRR republication clock or source attribution differs")
        clock, body = plain(clocks[0]), plain(div_region(raw, "id", "zoom"))
    stamps = re.findall(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?![:\d])", clock)
    if len(stamps) != 1:
        raise ValueError("independent minute publication clock differs")
    stamp = datetime.strptime(stamps[0], "%Y-%m-%d %H:%M").replace(tzinfo=CHINA)
    if (record["publication_timestamp"] != stamp.isoformat(timespec="minutes")
            or record["available_at"] != (stamp + timedelta(seconds=59)).isoformat()
            or record["signed_date"] != stamp.date().isoformat() or record["publication_precision"] != "minute"):
        raise ValueError("policy minute clock was given invented second precision")
    compact = re.sub(r"\s+", "", body)
    if compact != re.sub(r"\s+", "", record["body_text"]):
        raise ValueError("independent original policy body differs")
    if record["kind"] == "lpr":
        one = re.findall(r"1年期LPR为(\d+(?:\.\d+)?)%", compact)
        five = re.findall(r"5年期以上LPR为(\d+(?:\.\d+)?)%", compact)
        dates = re.findall(r"(\d{4})年(\d+)月(\d+)日贷款市场报价利率", compact)
        if len(one) != 1 or len(five) != 1 or len(dates) != 1 or "以上LPR在下一次发布LPR之前有效" not in compact:
            raise ValueError("independent LPR source lacks complete explicit rates and validity")
        if "-".join(f"{int(n):0{4 if i == 0 else 2}d}" for i, n in enumerate(dates[0])) != record["signed_date"]:
            raise ValueError("independent LPR body date differs")
        operations = [{"instrument": "lpr", "tenor_value": tenor, "tenor_unit": unit, "rate_pct": float(Decimal(value)),
                       "rate_kind": "lending_benchmark", "market": "mainland", "gross_amount_100m_yuan": None}
                      for tenor, unit, value in ((1, "年", one[0]), (5, "年以上", five[0]))]
        measures = []
    else:
        general = re.findall(r"决定于(\d{4})年(\d+)月(\d+)日全面下调金融机构存款准备金率(\d+(?:\.\d+)?)个百分点（不含([^）]+)）", compact)
        targeted = re.findall(r"仅在省级行政区域内经营的城市商业银行定向下调存款准备金率(\d+(?:\.\d+)?)个百分点，于(\d+)月(\d+)日和(\d+)月(\d+)日分两次实施到位，每次下调(\d+(?:\.\d+)?)个百分点", compact)
        if len(general) != 1 or len(targeted) != 1:
            raise ValueError("independent RRR source lacks one clear phased policy")
        year, month, day, rate, exclusions = general[0]
        total, month1, day1, month2, day2, per_phase = targeted[0]
        if Decimal(total) != 2 * Decimal(per_phase):
            raise ValueError("independent RRR targeted total differs from its phases")
        measures = [{"measure_key": "general", "effective_date": f"{int(year):04d}-{int(month):02d}-{int(day):02d}", "change_percentage_points": float(-Decimal(rate)),
                     "scope_key": "financial_institutions_except_named_exclusions", "excluded_institutions": exclusions.replace("和", "、").split("、")},
                    *[{"measure_key": key, "effective_date": f"{int(year):04d}-{int(m):02d}-{int(d):02d}", "change_percentage_points": float(-Decimal(per_phase)),
                       "scope_key": "province_only_city_commercial_banks", "excluded_institutions": []}
                      for key, m, d in (("targeted_phase_1", month1, day1), ("targeted_phase_2", month2, day2))]]
        operations = []
    if record["explicit_no_reverse_repo"] is not None or record["gross_reverse_repo_amount_100m_yuan"] is not None:
        raise ValueError("non-OMO source was treated as a zero reverse-repo declaration")
    fields = ("instrument", "tenor_value", "tenor_unit", "rate_pct", "rate_kind", "market", "gross_amount_100m_yuan")
    if [{key: row[key] for key in fields} for row in record["operations"]] != operations:
        raise ValueError("independent policy rate observations differ")
    for measure, wanted in zip(record["policy_measures"], measures):
        if any(measure[key] != value for key, value in wanted.items()) or measure["issuer_exposure"] != "unmapped" or any(measure[key] is not None for key in ("absolute_reserve_requirement_pct", "equity_direction", "response_coefficient", "net_liquidity_100m_yuan")):
            raise ValueError("independent RRR scope, schedule or unknown equity exposure differs")
    if len(record["policy_measures"]) != len(measures):
        raise ValueError("independent RRR scheduled-measure count differs")
    return operations, measures


def compute() -> dict:
    manifest_path = SOURCE / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bindings = {str(manifest_path): digest(manifest_path), **manifest["inputs"], **manifest["code_sha256"]}
    for name, value in manifest["artifacts"].items():
        bindings[str(SOURCE / name)] = value["sha256"]
    if any(digest(Path(name)) != value for name, value in bindings.items()):
        raise ValueError("expanded policy source, producer or artifact binding differs")
    facts = json.loads((SOURCE / "policy_facts.json").read_text(encoding="utf-8"))
    rows = json.loads((SOURCE / "lagged_policy_calendar.json").read_text(encoding="utf-8"))["calendar"]
    records = facts["records"]
    if len(records) != 83 or len({record["source_id"] for record in records}) != 83 or len(rows) != 43 or len(facts["legal_documents"]) != 1:
        raise ValueError("expanded policy source coverage differs")
    reference_config = json.loads((ROOT / "research/configs/pre_wuhan_monetary_operations_2019.json").read_text(encoding="utf-8"))
    reference = json.loads((ROOT / reference_config["reference_path"]).read_text(encoding="utf-8"))["result"]["variants"][0]["daily_asset_rows"]
    pairs = Counter((row["trade_date"], row["signal_cutoff_date"]) for row in reference)
    if len(pairs) != 43 or set(pairs.values()) != {123} or [(row["trade_date"], row["signal_cutoff_date"]) for row in rows] != sorted(pairs):
        raise ValueError("expanded policy calendar does not match all archived experiment clocks")
    independent, previous, measure_rows, rate_count, noops = {}, {}, [], 0, 0
    for record in sorted(records, key=lambda record: (datetime.fromisoformat(record["available_at"]), record["source_id"])):
        if record["kind"] == "omo":
            stamp, operations, absent = raw_facts(record)
            if record["available_at"] != stamp or record["publication_precision"] != "second":
                raise ValueError("OMO exact page clock was altered")
            measures = []
            noops += int(absent)
        else:
            operations, measures = additional_facts(record)
        if record["net_liquidity_100m_yuan"] is not None or record["use_policy"]["agent_signal_enabled"] is not False:
            raise ValueError("policy context inferred net liquidity or enabled economic signals")
        audited_operations = []
        for observed, saved in zip(operations, record["operations"]):
            key = (observed["instrument"], observed["tenor_value"], observed["tenor_unit"])
            before = previous.get(key)
            delta = float((Decimal(str(observed["rate_pct"])) - Decimal(str(before["rate_pct"]))) * 100) if before else None
            if saved["previous_observation"] != before or saved["change_from_previous_observed_bps"] != delta:
                raise ValueError("policy rate comparison has the wrong instrument/tenor predecessor")
            previous[key] = {"title": record["title"], "publication_timestamp": record["publication_timestamp"], "source_sha256": record["source"]["sha256"], "rate_pct": observed["rate_pct"]}
            audited_operations.append({**observed, "previous_observation": before, "change_from_previous_observed_bps": delta})
            rate_count += 1
        independent[record["source_id"]] = {"available": datetime.fromisoformat(record["available_at"]), "record": record, "operations": audited_operations, "measures": measures}
        measure_rows.extend(measures)
    legal = facts["legal_documents"][0]
    with fitz.open(ROOT / legal["source"]["archive_path"]) as document:
        text = re.sub(r"\s+", "", "".join(page.get_text() for page in document))
        pages = len(document)
    if pages != 4 or "银发(2019)223号" not in text or "中国人民银行办公厅2019年9月10日印发" not in text or "在该行政区域外没有设立分支机构的城市商业银行" not in text:
        raise ValueError("independent legal PDF identity or province-only scope differs")
    phases = re.findall(r"自(\d{4})年(\d+)月(\d+)日起,下调.*?存款准备金率([O\d]+\.\d+)个百分点", text)
    visual_review = json.loads((ROOT / "research/configs/rrr_legal_review_2019_223.json").read_text(encoding="utf-8"))
    if visual_review["source_sha256"] != digest(ROOT / legal["source"]["archive_path"]) or visual_review["approved_decimal_aliases"] != {"O.5": "0.5"}:
        raise ValueError("independent PDF decimal review is not bound to this source")
    legal_schedule = [{"effective_date": f"{int(y):04d}-{int(m):02d}-{int(d):02d}", "change_percentage_points": float(-Decimal(token if "O" not in token else visual_review["approved_decimal_aliases"][token]))}
                      for y, m, d, token in phases]
    if (legal_schedule != [{key: m[key] for key in ("effective_date", "change_percentage_points")} for m in measure_rows]
            or legal_schedule != [{key: m[key] for key in ("effective_date", "change_percentage_points")} for m in legal["schedule"]]
            or legal["publication_timestamp"] is not None or "财务公司、汽车金融公司、金融租赁公司存款准备金率保持不变" not in text):
        raise ValueError("independent legal PDF schedule, exclusions or publication-time meaning differs")
    last_cutoff, rate_cells, schedule_cells, new_changes, known_implementations, seen = None, 0, 0, [], [], set()
    ordered = sorted(independent.values(), key=lambda item: (item["available"], item["record"]["source_id"]))
    for index, row in enumerate(rows):
        cutoff = datetime.fromisoformat(row["signal_cutoff_date"] + "T23:59:59+08:00")
        available = [item for item in ordered if item["available"] <= cutoff]
        fresh = [] if last_cutoff is None else [item for item in available if last_cutoff < item["available"]]
        if row["cutoff_timestamp"] != cutoff.isoformat() or row["baseline_state_only"] != (last_cutoff is None):
            raise ValueError("independent baseline/cutoff rule differs")
        if row["baseline_source_ids"] != ([item["record"]["source_id"] for item in available] if last_cutoff is None else []) or row["newly_visible_source_ids"] != [item["record"]["source_id"] for item in fresh]:
            raise ValueError("inherited policy state was treated as new information")
        expected_rates, expected_schedule, expected_changes, expected_implementations = {}, [], [], []
        for item in available:
            record = item["record"]
            for operation in item["operations"]:
                key = operation["instrument"] + ":" + str(operation["tenor_value"]) + operation["tenor_unit"]
                expected_rates[key] = {key: operation[key] for key in ("rate_pct", "rate_kind", "market", "change_from_previous_observed_bps")}
                expected_rates[key].update({"source_id": record["source_id"], "source_sha256": record["source"]["sha256"], "publication_timestamp": record["publication_timestamp"], "available_at": record["available_at"]})
            for measure in item["measures"]:
                identity = record["source_id"] + ":" + measure["measure_key"]
                expected_schedule.append({**measure, "measure_id": identity, "status": "effective_by_cutoff" if measure["effective_date"] <= row["signal_cutoff_date"] else "scheduled_after_cutoff"})
                if last_cutoff is not None and item["available"] <= last_cutoff and last_cutoff.date().isoformat() < measure["effective_date"] <= row["signal_cutoff_date"]:
                    expected_implementations.append(identity)
        for item in fresh:
            record = item["record"]
            for operation in item["operations"]:
                if operation["change_from_previous_observed_bps"] in {None, 0.0}:
                    continue
                change = {key: operation[key] for key in ("instrument", "tenor_value", "tenor_unit", "market", "rate_kind", "rate_pct", "change_from_previous_observed_bps", "previous_observation")}
                change.update({"source_id": record["source_id"], "available_at": record["available_at"], "source_sha256": record["source"]["sha256"], "equity_direction": None, "response_coefficient": None, "agent_signal_enabled": False})
                identity = (record["source_id"], operation["instrument"], operation["tenor_value"], operation["tenor_unit"])
                if identity in seen:
                    raise ValueError("policy change was applied again on a subsequent day")
                seen.add(identity)
                expected_changes.append(change)
                new_changes.append({"trade_date": row["trade_date"], "signal_cutoff_date": row["signal_cutoff_date"], "time_split": "first_30_development_dates" if index < 30 else "last_13_development_dates", **change})
        if row["latest_observed_instrument_rates"] != expected_rates or row["new_observed_rate_changes"] != expected_changes or row["newly_effective_known_rrr_measure_ids"] != expected_implementations:
            raise ValueError("independent latest rates, fresh changes or anticipated implementation differs")
        if len(row["known_rrr_schedule"]) != len(expected_schedule):
            raise ValueError("independent known RRR schedule coverage differs")
        for saved, expected in zip(row["known_rrr_schedule"], expected_schedule):
            if (any(saved[key] != value for key, value in expected.items()) or saved["anticipated_from_prior_announcement"] is not True
                    or saved["announcement_available_at"] != independent[saved["source_id"]]["record"]["available_at"]):
                raise ValueError("independent RRR schedule status or information provenance differs")
        quantities = [item["record"]["gross_reverse_repo_amount_100m_yuan"] for item in available if item["record"]["signed_date"] == row["signal_cutoff_date"] and item["record"]["gross_reverse_repo_amount_100m_yuan"] is not None]
        if row["cutoff_date_gross_reverse_repo_100m_yuan"] != (sum(quantities) if quantities else None) or row["cutoff_date_net_liquidity_100m_yuan"] is not None or row["agent_signal_enabled"] is not False:
            raise ValueError("policy cutoff quantity, missingness or economic-signal gate differs")
        known_implementations.extend({"trade_date": row["trade_date"], "signal_cutoff_date": row["signal_cutoff_date"], "measure_id": identity} for identity in expected_implementations)
        rate_cells += len(expected_rates)
        schedule_cells += len(expected_schedule)
        last_cutoff = cutoff
    if facts["coverage"]["new_visible_nonzero_observed_rate_changes"] != new_changes:
        raise ValueError("policy change coverage summary differs from independent first-visible events")
    return {"pipeline_version": "pre-wuhan-expanded-policy-independent-audit-v1", "raw_html_sources_checked": len(records),
            "legal_pdf_pages_checked": pages, "rate_observations_checked": rate_count, "explicit_no_reverse_repo_notices": noops,
            "calendar_steps_checked": len(rows), "latest_rate_cells_checked": rate_cells, "rrr_schedule_cells_checked": schedule_cells,
            "new_observed_rate_changes": new_changes, "anticipated_implementation_transitions": known_implementations,
            "interpretation": "Independent original-HTML regex, raw operation tables, PyMuPDF legal-text extraction and direct clock reconstruction. Embedded PDF decimal OCR errors retain a byte-bound single-assistant visual review. This audit does not establish stock-price effects, net liquidity, complete policy coverage or new holdout performance.",
            "inputs": dict(sorted(bindings.items())), "code_sha256": {str(Path(__file__).resolve()): digest(Path(__file__)),
                str(ROOT / "research/data_pipeline/audit_pre_wuhan_monetary_context_2019.py"): digest(ROOT / "research/data_pipeline/audit_pre_wuhan_monetary_context_2019.py")}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute()
    output = args.output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("expanded policy audit must be directly under research_outputs")
    payload = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    if args.audit_existing:
        if output.read_bytes() != payload:
            raise ValueError("expanded independent policy audit differs byte-for-byte")
    else:
        with output.open("xb") as handle:
            handle.write(payload)
    print(json.dumps({key: result[key] for key in ("raw_html_sources_checked", "legal_pdf_pages_checked", "rate_observations_checked", "calendar_steps_checked", "latest_rate_cells_checked", "rrr_schedule_cells_checked", "anticipated_implementation_transitions")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
