"""Independently check raw HTML facts, rate history and the 43 cutoff contexts."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "research_outputs/pre_wuhan_monetary_context_2019_v1"
OUTPUT = ROOT / "research_outputs/pre_wuhan_monetary_context_audit_2019_v1.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plain(value: str) -> str:
    value = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", value, flags=re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def raw_facts(record: dict) -> tuple[str, list[dict], bool]:
    raw = (ROOT / record["source"]["archive_path"]).read_text(encoding="utf-8")
    title = re.findall(r"<title>(.*?)</title>", raw, re.S | re.I)
    stamps = re.findall(r'<[^>]+\bid=["\x27]shijian["\x27][^>]*>([^<]+)', raw, re.I)
    body_start = re.search(r'<div\b[^>]+\bid=["\x27]zoom["\x27][^>]*>', raw, re.I)
    if len(title) != 1 or len(stamps) != 1 or body_start is None or plain(title[0]) != record["title"]:
        raise ValueError("independent HTML title, body or historical timestamp differs")
    stamp = stamps[0].strip().replace(" ", "T") + "+08:00"
    if stamp != record["publication_timestamp"]:
        raise ValueError("independent historical page clock differs")
    suffix = re.sub(r"<(script|style)\b[^>]*>.*?</\1>|<!--.*?-->", "", raw[body_start.end():], flags=re.S | re.I)
    depth, end = 1, None
    for tag in re.finditer(r"</?div\b[^>]*>", suffix, re.I):
        depth += -1 if tag[0].startswith("</") else 1
        if depth == 0:
            end = tag.start()
            break
    if end is None:
        raise ValueError("independent audit cannot bound the source body div")
    markup = suffix[:end]
    text = plain(markup)
    if re.sub(r"\s+", "", text) != re.sub(r"\s+", "", record["body_text"]):
        raise ValueError("independent flat HTML body differs")
    absent = "不开展逆回购" in text
    hk = "香港金融管理局" in text and "央行票据" in text
    mlf = "MLF" in text or "中期借贷便利" in text
    repo = "逆回购操作情况" in text
    operations = []
    for line in re.findall(r"<tr\b[^>]*>(.*?)</tr>", markup, re.S | re.I):
        cells = [re.sub(r"\s+", "", plain(cell)) for cell in re.findall(r"<(?:td|th)\b[^>]*>(.*?)</(?:td|th)>", line, re.S | re.I)]
        if hk and len(cells) == 4 and "央行票据（香港）" in cells[0]:
            amount_text, tenor_text, rate_text = cells[1:]
            instrument, kind, market = "hk_central_bank_bill", "bill_yield", "offshore_hk"
        elif len(cells) == 3:
            tenor_text, amount_text, rate_text = cells
            instrument, kind, market = ("mlf" if mlf and "年" in tenor_text else "reverse_repo" if repo else None), "tender_interest", "mainland"
        else:
            continue
        tenor = re.fullmatch(r"(\d+)(天|年|个月)(?:[（(]\d+天[）)])?", tenor_text)
        amount = re.fullmatch(r"(\d+(?:\.\d+)?)亿元", amount_text)
        rate = re.fullmatch(r"(\d+(?:\.\d+)?)%", rate_text)
        if not (tenor and amount and rate):
            continue
        if instrument is None:
            raise ValueError("independent raw operation instrument is unknown")
        operations.append({"instrument": instrument, "tenor_value": int(tenor[1]), "tenor_unit": tenor[2],
                           "gross_amount_100m_yuan": float(Decimal(amount[1])), "rate_pct": float(Decimal(rate[1])),
                           "rate_kind": kind, "market": market})
    published = [{key: row[key] for key in ("instrument", "tenor_value", "tenor_unit", "gross_amount_100m_yuan", "rate_pct", "rate_kind", "market")}
                 for row in record["operations"]]
    if operations != published or absent != record["explicit_no_reverse_repo"]:
        raise ValueError("independent tender facts or no-operation declaration differs")
    gross = 0.0 if absent else sum(row["gross_amount_100m_yuan"] for row in operations if row["instrument"] == "reverse_repo") if repo else None
    if gross != record["gross_reverse_repo_amount_100m_yuan"] or record["net_liquidity_100m_yuan"] is not None:
        raise ValueError("gross quantity was confused with unobserved liquidity or filled zero")
    return stamp, operations, absent


def compute() -> dict:
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    bindings = {str(SOURCE / "manifest.json"): digest(SOURCE / "manifest.json")}
    for field in ("inputs", "code_sha256"):
        bindings.update(manifest[field])
    for name, value in manifest["artifacts"].items():
        bindings[str(SOURCE / name)] = value["sha256"]
    if any(digest(Path(name)) != value for name, value in bindings.items()):
        raise ValueError("monetary audit source, code or artifact binding differs")
    facts = json.loads((SOURCE / "operations.json").read_text(encoding="utf-8"))
    context = json.loads((SOURCE / "lagged_context.json").read_text(encoding="utf-8"))
    records, calendar = facts["records"], context["calendar"]
    if len(records) != 57 or len(calendar) != 43 or len({row["title"] for row in records}) != 57:
        raise ValueError("independent monetary panel coverage differs")
    raw_rows, noops = 0, 0
    previous = {}
    for record in records:
        _, operations, absent = raw_facts(record)
        raw_rows += len(operations)
        noops += int(absent)
        if record["use_policy"]["agent_signal_enabled"] is not False:
            raise ValueError("unvalidated policy source enabled an Agent signal")
        for row in record["operations"]:
            key = (row["instrument"], row["tenor_value"], row["tenor_unit"])
            before = previous.get(key)
            delta = float((Decimal(str(row["rate_pct"])) - Decimal(str(before["rate_pct"]))) * 100) if before else None
            if row["previous_observation"] != before or row["change_from_previous_observed_bps"] != delta:
                raise ValueError("rate difference uses a missing or different-tenor predecessor")
            previous[key] = {"title": record["title"], "publication_timestamp": record["publication_timestamp"],
                             "source_sha256": record["source"]["sha256"], "rate_pct": row["rate_pct"]}
    observation_checks, cutoff_checks = 0, 0
    for row in calendar:
        cutoff = datetime.fromisoformat(row["signal_cutoff_date"] + "T23:59:59+08:00")
        if row["cutoff_timestamp"] != cutoff.isoformat() or row["signal_cutoff_date"] >= row["trade_date"]:
            raise ValueError("monetary context clock did not censor future information")
        available = [record for record in records if datetime.fromisoformat(record["publication_timestamp"]) <= cutoff]
        expected = {}
        for record in available:
            for operation in record["operations"]:
                key = operation["instrument"] + ":" + str(operation["tenor_value"]) + operation["tenor_unit"]
                expected[key] = {"rate_pct": operation["rate_pct"], "rate_kind": operation["rate_kind"],
                                 "change_from_previous_observed_bps": operation["change_from_previous_observed_bps"],
                                 "publication_timestamp": record["publication_timestamp"], "source_sha256": record["source"]["sha256"], "title": record["title"]}
        if expected != row["latest_observed_instrument_rates"]:
            raise ValueError("independent latest rate observation differs from archived context")
        on_date = [record for record in available if record["signed_date"] == row["signal_cutoff_date"]]
        values = [record["gross_reverse_repo_amount_100m_yuan"] for record in on_date if record["gross_reverse_repo_amount_100m_yuan"] is not None]
        if (row["cutoff_date_bulletins"] != [record["title"] for record in on_date]
                or row["cutoff_date_gross_reverse_repo_100m_yuan"] != (sum(values) if values else None)
                or row["cutoff_date_net_liquidity_100m_yuan"] is not None
                or row["agent_signal_enabled"] is not False):
            raise ValueError("monetary cutoff quantity, missingness or signal gate differs")
        observation_checks += len(expected)
        cutoff_checks += 1
    changes = []
    for record in records:
        for operation in record["operations"]:
            if operation["change_from_previous_observed_bps"] in {None, 0.0}:
                continue
            first = next((row for row in calendar if row["cutoff_timestamp"] >= record["publication_timestamp"]), None)
            changes.append({"instrument": operation["instrument"], "title": record["title"],
                            "publication_timestamp": record["publication_timestamp"], "change_bps": operation["change_from_previous_observed_bps"],
                            "first_visible_synthetic_step": first["trade_date"] if first else None,
                            "first_visible_signal_cutoff": first["signal_cutoff_date"] if first else None,
                            "time_split": "first_30_development_dates" if first and calendar.index(first) < 30 else "last_13_development_dates" if first else "outside_calendar"})
    undefined_predecessors = [{"title": record["title"], "instrument": row["instrument"], "tenor_value": row["tenor_value"], "tenor_unit": row["tenor_unit"]}
                             for record in records for row in record["operations"] if row["previous_observation"] is None]
    return {"pipeline_version": "pre-wuhan-monetary-independent-raw-html-clock-audit-v1", "raw_bulletins_checked": len(records),
            "independent_operation_rows_checked": raw_rows, "explicit_no_reverse_repo_notices": noops,
            "cutoff_contexts_checked": cutoff_checks, "latest_observed_rate_cells_checked": observation_checks,
            "observed_rate_changes": changes, "first_observation_without_previous_rate": undefined_predecessors,
            "rate_changes_first_visible_in_last_13_dates": sum(row["time_split"] == "last_13_development_dates" for row in changes),
            "interpretation": "Stdlib regular-expression extraction from original response HTML, independent of the production HTMLParser. Observed rate differences are instrument/tenor matched. First-observation differences remain null. Only two nonzero predecessor-backed changes exist in this bounded catalog and both are in the first 30 development dates; this is insufficient evidence of policy-effect prediction or a new shock holdout.",
            "inputs": dict(sorted(bindings.items())), "code_sha256": digest(Path(__file__))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute()
    output = args.output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("monetary audit output must be directly under research_outputs")
    payload = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if args.audit_existing:
        if output.read_bytes() != payload:
            raise ValueError("monetary independent audit differs byte-for-byte")
    else:
        with output.open("xb") as handle:
            handle.write(payload)
    print(json.dumps({key: result[key] for key in ("raw_bulletins_checked", "independent_operation_rows_checked", "cutoff_contexts_checked", "latest_observed_rate_cells_checked", "observed_rate_changes", "rate_changes_first_visible_in_last_13_dates")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
