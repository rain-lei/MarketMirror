"""Complete raw rate predecessors, preserving the earlier rank-failed contrast."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from . import monetary_operations, policy_calendar, policy_information_clock, policy_rate_panel, provenance, sectioned_policy_rates
from .provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/policy_information_clock_completion_2019_2020.json"
OUTPUT = ROOT / "research_outputs/policy_information_clock_completed_2019_2020_v1"


def serialize(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def parse_predecessor(raw: bytes, day: str, title: str) -> dict:
    probe = monetary_operations.BulletinHTML()
    probe.feed(raw.decode("utf-8"))
    compact = re.sub(r"\s+", "", "".join(probe.values["body"]))
    if "TMLF操作情况" in compact and re.search(r"(?<!T)MLF操作情况", compact):
        return sectioned_policy_rates.parse_sectioned_bulletin(raw, day, title)
    return policy_rate_panel.parse_rate_bulletin(raw, day, title)


def compute() -> dict[str, dict]:
    settings = json.loads(CONFIG.read_text(encoding="utf-8"))
    bindings = {str(CONFIG): file_sha256(CONFIG), **{str(ROOT / name): value for name, value in settings["inputs"].items()}}
    source = ROOT / settings["source_directory"]
    parent = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    prior_audit = json.loads((ROOT / "research_outputs/extended_policy_rate_diagnostic_audit_2019_2020_v2.json").read_text(encoding="utf-8"))
    contrast = ROOT / "research_outputs/policy_information_clock_contrast_2019_2020_v1"
    contrast_manifest = json.loads((contrast / "manifest.json").read_text(encoding="utf-8"))
    raw_root = ROOT / settings["predecessor_directory"]
    acquisition = json.loads((raw_root / "manifest.json").read_text(encoding="utf-8"))
    bindings.update(parent["inputs"] | parent["code_sha256"] | prior_audit["inputs"] | contrast_manifest["code_sha256"] | acquisition["inputs"] | acquisition["code_sha256"])
    bindings[str(ROOT / "research/data_pipeline/audit_extended_policy_rate_diagnostic.py")] = prior_audit["code_sha256"]
    if any(file_sha256(Path(name)) != value for name, value in bindings.items()):
        raise ValueError("completed clock source, prior producer or independent review changed")
    old_facts = json.loads((source / "policy_facts.json").read_text(encoding="utf-8"))["records"]
    old_records = json.loads((source / "policy_facts.json").read_text(encoding="utf-8"))["records"]
    if len(acquisition["records"]) != settings["expected_additional_sources"] or any(row["kind"] != "omo" for row in acquisition["records"]):
        raise ValueError("completed clock predecessor source coverage differs")
    records = old_records
    for row in acquisition["records"]:
        path = raw_root / row["archive_name"]
        if row["status"] != "FETCHED" or path.parent != raw_root or file_sha256(path) != row["sha256"]:
            raise ValueError("completed clock predecessor response failed, moved or changed")
        bindings[str(path)] = row["sha256"]
        record = parse_predecessor(path.read_bytes(), row["publication_date"], row["title"])
        record.update({"source_id": row["source_id"], "source": {"archive_path": str(path.relative_to(ROOT)).replace("\\", "/"),
                       "sha256": row["sha256"], "url": row["url"], "retrieved_at_utc": row["retrieved_at_utc"]}})
        records.append(record)
    if len(records) != settings["expected_total_sources"] or len({row["source_id"] for row in records}) != len(records):
        raise ValueError("completed clock source identities are missing or duplicated")
    records.sort(key=lambda row: (row["available_at"], row["source_id"]))
    monetary_operations.annotate_rate_changes(records)
    by_id = {row["source_id"]: row for row in records}
    changes = []
    for old in old_facts:
        current = by_id[old["source_id"]]
        for key in old:
            if key != "operations" and current[key] != old[key]:
                raise ValueError("source completion changed an existing original article fact")
        if len(current["operations"]) != len(old["operations"]):
            raise ValueError("source completion changed original operation count")
        for before, after in zip(old["operations"], current["operations"]):
            for key in before:
                if key not in {"previous_observation", "change_from_previous_observed_bps"} and before[key] != after[key]:
                    raise ValueError("source completion changed an existing tender fact")
            if before["previous_observation"] != after["previous_observation"] or before["change_from_previous_observed_bps"] != after["change_from_previous_observed_bps"]:
                changes.append({"source_id": old["source_id"], "instrument": after["instrument"], "tenor_value": after["tenor_value"], "tenor_unit": after["tenor_unit"],
                                "old_previous_observation": before["previous_observation"], "completed_previous_observation": after["previous_observation"],
                                "old_change_bps": before["change_from_previous_observed_bps"], "completed_change_bps": after["change_from_previous_observed_bps"]})
    prior_protocol = json.loads((ROOT / "research/configs/extended_policy_market_diagnostic_2019_2020.json").read_text(encoding="utf-8"))
    channels = [(row["instrument"], row["tenor_value"], row["tenor_unit"]) for row in prior_protocol["additional_policy_features"]]
    market_path = next(ROOT / name for name in settings["inputs"] if name.endswith("/benchmark_prices.csv"))
    with (market_path.parent / "calendar.csv").open(encoding="utf-8-sig", newline="") as handle:
        days = [row["trade_date"] for row in csv.DictReader(handle)]
    with market_path.open(encoding="utf-8-sig", newline="") as handle:
        closes = {row["trade_date"]: float(row["close"]) for row in csv.DictReader(handle)}
    calendars, vectors, panels = {}, {}, {}
    for variant in settings["variants"]:
        name = variant["name"]
        clocks = policy_information_clock.prior_clocks(days, settings["study_start_date"], settings["study_end_date"], variant["policy_lag_sessions"])
        if (len(clocks) != settings["expected_raw_calendar_steps"]
                or sum(row["trade_date"] <= settings["training_end_date"] for row in clocks) != settings["expected_raw_training_steps"]
                or sum(row["trade_date"] >= settings["evaluation_start_date"] for row in clocks) != settings["expected_raw_evaluation_steps"]):
            raise ValueError("completed contrast calendar/train/check coverage differs")
        calendars[name] = policy_calendar.build_calendar(records, clocks)
        vectors[name] = policy_rate_panel.policy_vectors(records, calendars[name], channels)
        panels[name] = policy_information_clock.clock_model_rows(days, closes, calendars[name], vectors[name], variant["market_lag_sessions"], variant["policy_lag_sessions"])
    initial_panels = json.loads((contrast / "clock_panels.json").read_text(encoding="utf-8"))
    for name, model_rows in panels.items():
        for current, old in zip(model_rows, initial_panels["model_rows"][name]):
            for key in ("trade_date", "signal_cutoff_date", "market_feature_cutoff_date", "baseline_features", "target_return", "target_absolute_return"):
                if current[key] != old[key]:
                    raise ValueError("source completion changed benchmark features, targets or timing rules")
    exclusions = policy_information_clock.shared_pairing(panels)
    fitted = {}
    for name, model_rows in panels.items():
        fitted[name] = policy_rate_panel.fit_diagnostic(model_rows, settings["training_end_date"], settings["evaluation_start_date"])
        fitted[name]["training_policy_dates"] = [row["trade_date"] for row in vectors[name] if row["observed_rate_changes"] and row["trade_date"] <= settings["training_end_date"]]
        fitted[name]["evaluation_policy_dates"] = [row["trade_date"] for row in vectors[name] if row["observed_rate_changes"] and row["trade_date"] >= settings["evaluation_start_date"]]
    gate = all(result["coefficients_fitted"] for result in fitted.values())
    if gate:
        for market_lag in (1, 2):
            left, right = fitted[f"market{market_lag}_policy2"], fitted[f"market{market_lag}_policy1"]
            for target in left["targets"]:
                if left["targets"][target]["models"]["market_state"] != right["targets"][target]["models"]["market_state"]:
                    raise ValueError("completed policy-only timing contrast changed its market baseline")
    pulse_mapping = [{"source_id": event["source_id"], "channel": event["channel"], "change_bps": event["change_bps"],
                      "trade_date": row["trade_date"], "variant": name}
                     for name, panel in vectors.items() for row in panel for event in row["observed_rate_changes"]]
    result = {"pipeline_version": settings["version"], "amendment_reason": settings["amendment_reason"],
              "original_source_facts_unchanged": True, "benchmark_features_targets_unchanged": True,
              "completed_predecessor_changes": changes, "shared_exclusions": exclusions,
              "all_variants_full_rank": gate, "variants": fitted, "policy_step_mapping": pulse_mapping,
              "contrasts": policy_information_clock.summarize_contrasts(fitted), "agent_signal_enabled": False,
              "limitations": settings["limitations"], "source_counts": dict(Counter(row["kind"] for row in records)),
              "interpretation": "Disclosed source-completion amendment after a rank failure. Identical calendar targets and original operation facts, new verified predecessor evidence only. All four clock variants reported; no automatic optimum or Agent adoption."}
    code = {str(Path(module.__file__).resolve()): file_sha256(Path(module.__file__))
            for module in (monetary_operations, policy_calendar, policy_information_clock, policy_rate_panel, provenance, sectioned_policy_rates)}
    code[str(Path(__file__).resolve())] = file_sha256(Path(__file__))
    output = {"policy_facts.json": {"records": records, "agent_signal_enabled": False, "amendment_reason": settings["amendment_reason"]},
              "clock_panels.json": {"calendars": calendars, "policy_vectors": vectors, "model_rows": panels}, "results.json": result}
    output["manifest.json"] = {"pipeline_version": settings["version"], "inputs": dict(sorted(bindings.items())), "code_sha256": code,
                               "artifacts": {name: {"sha256": hashlib.sha256(serialize(value)).hexdigest()} for name, value in output.items()}}
    if any(file_sha256(Path(name)) != value for name, value in {**bindings, **code}.items()):
        raise ValueError("completed clock source or producer changed during calculation")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("completed clock output must be a direct research_outputs directory")
    artifacts = compute()
    if args.audit_existing:
        if any((output / name).read_bytes() != serialize(value) for name, value in artifacts.items()):
            raise ValueError("completed clock artifacts differ from frozen output")
        print("All four completed clock artifacts rebuilt byte-identically.")
    else:
        if output.exists():
            raise ValueError("completed clock output exists; preserve it and use a fresh directory")
        output.mkdir()
        for name, value in artifacts.items():
            with (output / name).open("xb") as handle:
                handle.write(serialize(value))
    result = artifacts["results.json"]
    print(json.dumps({"sources": result["source_counts"], "shared_exclusions": result["shared_exclusions"], "all_variants_full_rank": result["all_variants_full_rank"]}, ensure_ascii=False))
    for name, fitted in result["variants"].items():
        print(json.dumps({"name": name, "train": fitted["training_rows"], "check": fitted["evaluation_rows"],
                          "metrics": {target: {model: row["metrics"] for model, row in data["models"].items()} for target, data in fitted["targets"].items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
