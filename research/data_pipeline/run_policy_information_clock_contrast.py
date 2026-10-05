"""Run the frozen four-way lag-availability diagnostic on identical dates."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from . import policy_calendar, policy_information_clock, policy_rate_panel, provenance
from .provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/policy_information_clock_contrast_2019_2020.json"
OUTPUT = ROOT / "research_outputs/policy_information_clock_contrast_2019_2020_v1"


def serialize(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def compute() -> dict[str, dict]:
    settings = json.loads(CONFIG.read_text(encoding="utf-8"))
    bindings = {str(CONFIG): file_sha256(CONFIG), **{str(ROOT / name): value for name, value in settings["inputs"].items()}}
    source = ROOT / settings["source_directory"]
    parent = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    audit_path = ROOT / "research_outputs/extended_policy_rate_diagnostic_audit_2019_2020_v2.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    bindings.update(parent["inputs"] | parent["code_sha256"] | audit["inputs"])
    bindings[str(ROOT / "research/data_pipeline/audit_extended_policy_rate_diagnostic.py")] = audit["code_sha256"]
    if any(file_sha256(Path(name)) != value for name, value in bindings.items()):
        raise ValueError("clock contrast prior source, producer or independent audit changed")
    records = json.loads((source / "policy_facts.json").read_text(encoding="utf-8"))["records"]
    protocol = json.loads((ROOT / "research/configs/extended_policy_market_diagnostic_2019_2020.json").read_text(encoding="utf-8"))
    channels = [(row["instrument"], row["tenor_value"], row["tenor_unit"]) for row in protocol["additional_policy_features"]]
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
            raise ValueError("clock contrast source/calendar scope differs")
        calendars[name] = policy_calendar.build_calendar(records, clocks)
        vectors[name] = policy_rate_panel.policy_vectors(records, calendars[name], channels)
        panels[name] = policy_information_clock.clock_model_rows(days, closes, calendars[name], vectors[name], variant["market_lag_sessions"], variant["policy_lag_sessions"])
    old_calendar = json.loads((source / "rate_calendar.json").read_text(encoding="utf-8"))["calendar"]
    old_panel = json.loads((source / "diagnostic_panel.json").read_text(encoding="utf-8"))
    if calendars["market2_policy2"] != old_calendar or vectors["market2_policy2"] != old_panel["policy_panel"]:
        raise ValueError("clock contrast does not recover the prior two-session policy objects")
    for current, old in zip(panels["market2_policy2"], old_panel["model_rows"]):
        for key in ("trade_date", "signal_cutoff_date", "execution_reference_date", "baseline_features", "policy_features",
                    "target_return", "target_absolute_return", "paired_exclusion_reason"):
            if current[key] != old[key]:
                raise ValueError("clock contrast does not recover prior market features/targets")
    exclusions = policy_information_clock.shared_pairing(panels)
    fitted = {}
    for name, rows in panels.items():
        fitted[name] = policy_rate_panel.fit_diagnostic(rows, settings["training_end_date"], settings["evaluation_start_date"])
        fitted[name]["training_policy_dates"] = [row["trade_date"] for row in vectors[name] if row["observed_rate_changes"] and row["trade_date"] <= settings["training_end_date"]]
        fitted[name]["evaluation_policy_dates"] = [row["trade_date"] for row in vectors[name] if row["observed_rate_changes"] and row["trade_date"] >= settings["evaluation_start_date"]]
    gate = all(result["coefficients_fitted"] for result in fitted.values())
    for fixed_market in (1, 2):
        left, right = fitted[f"market{fixed_market}_policy2"], fitted[f"market{fixed_market}_policy1"]
        if gate:
            for target in left["targets"]:
                if left["targets"][target]["models"]["market_state"] != right["targets"][target]["models"]["market_state"]:
                    raise ValueError("policy-only timing contrast changed its paired market baseline")
    pulse_dates = [{"source_id": change["source_id"], "channel": change["channel"], "change_bps": change["change_bps"],
                    "trade_date": row["trade_date"], "variant": name}
                   for name, panel in vectors.items() for row in panel for change in row["observed_rate_changes"]]
    results = {"pipeline_version": settings["version"], "variants": fitted, "all_variants_full_rank": gate,
               "shared_exclusions": exclusions, "contrasts": policy_information_clock.summarize_contrasts(fitted),
               "policy_step_mapping": pulse_dates, "old_two_session_objects_recovered": True,
               "agent_signal_enabled": False, "limitations": settings["limitations"],
               "interpretation": "Four prespecified crossed timing variants reported, on an identical union-excluded target-date sample. Lower error in a variant is not an adopted rule or an independent forecast proof."}
    code = {str(Path(module.__file__).resolve()): file_sha256(Path(module.__file__))
            for module in (policy_calendar, policy_information_clock, policy_rate_panel, provenance)}
    code[str(Path(__file__).resolve())] = file_sha256(Path(__file__))
    output = {"clock_panels.json": {"calendars": calendars, "policy_vectors": vectors, "model_rows": panels}, "results.json": results}
    output["manifest.json"] = {"pipeline_version": settings["version"], "inputs": dict(sorted(bindings.items())),
                               "code_sha256": code,
                               "artifacts": {name: {"sha256": hashlib.sha256(serialize(value)).hexdigest()} for name, value in output.items()}}
    if any(file_sha256(Path(name)) != value for name, value in {**bindings, **code}.items()):
        raise ValueError("clock contrast input/producer changed during calculation")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("clock contrast must use a direct research_outputs directory")
    artifacts = compute()
    if args.audit_existing:
        if any((output / name).read_bytes() != serialize(value) for name, value in artifacts.items()):
            raise ValueError("clock contrast differs from frozen artifacts")
        print("All three clock contrast artifacts rebuilt byte-identically.")
    else:
        if output.exists():
            raise ValueError("clock contrast output exists; use a fresh directory")
        output.mkdir()
        for name, value in artifacts.items():
            with (output / name).open("xb") as handle:
                handle.write(serialize(value))
    result = artifacts["results.json"]
    print(json.dumps({"shared_exclusions": result["shared_exclusions"], "all_variants_full_rank": result["all_variants_full_rank"]}, ensure_ascii=False))
    for name, fitted in result["variants"].items():
        print(json.dumps({"name": name, "train": fitted["training_rows"], "check": fitted["evaluation_rows"],
                          "policy_check_dates": fitted["evaluation_policy_dates"],
                          "metrics": {target: {model: value["metrics"] for model, value in data["models"].items()} for target, data in fitted["targets"].items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
