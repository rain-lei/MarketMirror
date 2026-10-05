"""Build the frozen cross-year policy versus market-state diagnostic."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from . import monetary_operations, policy_calendar, policy_rate_panel, provenance
from .provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/extended_policy_rate_bindings_2019_2020.json"
OUTPUT = ROOT / "research_outputs/extended_policy_rate_diagnostic_2019_2020_v2"


def load_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def serialized(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def compute() -> dict[str, dict]:
    settings = json.loads(CONFIG.read_text(encoding="utf-8"))
    bindings = {str(ROOT / name): value for name, value in settings["inputs"].items()}
    bindings[str(CONFIG)] = file_sha256(CONFIG)
    protocol_path = ROOT / settings["diagnostic_protocol"]
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    bindings.update({str(ROOT / name): value for name, value in protocol["market_inputs"].items()})
    raw_root = ROOT / settings["raw_directory"]
    acquisition = json.loads((raw_root / "manifest.json").read_text(encoding="utf-8"))
    bindings.update(acquisition["inputs"] | acquisition["code_sha256"])
    if any(file_sha256(Path(name)) != value for name, value in bindings.items()):
        raise ValueError("extended diagnostic frozen input or acquisition producer changed")
    if Counter(row["kind"] for row in acquisition["records"]) != {key: settings["expected_counts"][key] for key in ("omo", "lpr")}:
        raise ValueError("extended diagnostic source coverage differs")
    records = []
    for row in acquisition["records"]:
        path = raw_root / row["archive_name"]
        if row["status"] != "FETCHED" or path.parent != raw_root or file_sha256(path) != row["sha256"]:
            raise ValueError("extended diagnostic original source is missing, failed or changed")
        bindings[str(path)] = row["sha256"]
        raw = path.read_bytes()
        function = policy_rate_panel.parse_rate_bulletin if row["kind"] == "omo" else policy_calendar.parse_lpr
        record = function(raw, row["publication_date"], row["title"])
        record.update({"source_id": row["source_id"], "source": {"archive_path": str(path.relative_to(ROOT)).replace("\\", "/"),
                       "sha256": row["sha256"], "url": row["url"], "retrieved_at_utc": row["retrieved_at_utc"]}})
        records.append(record)
    records.sort(key=lambda row: (row["available_at"], row["source_id"]))
    lpr_months = [row["signed_date"][:7] for row in records if row["kind"] == "lpr"]
    if lpr_months != ["2019-08", "2019-09", "2019-10", "2019-11", "2019-12", "2020-01", "2020-02", "2020-03"]:
        raise ValueError("extended diagnostic LPR monthly coverage differs")
    monetary_operations.annotate_rate_changes(records)
    benchmark_path = next(ROOT / name for name in protocol["market_inputs"] if name.endswith("/benchmark_prices.csv"))
    market_root = benchmark_path.parent
    primary = json.loads((market_root / "baostock_download_manifest.json").read_text(encoding="utf-8"))
    for name in ("benchmark_prices.csv", "calendar.csv", "sh_000300_raw.csv", "calendar_raw.csv"):
        path = market_root / name
        wanted = primary["artifacts"][name]["sha256"]
        if file_sha256(path) != wanted:
            raise ValueError("extended benchmark/calendar differs from original provider artifact")
        bindings[str(path)] = wanted
    queries = [row for row in primary["queries"] if row.get("symbol") == protocol["benchmark_id"]]
    if len(queries) != 1 or queries[0]["adjustflag"] != "3" or queries[0]["frequency"] != "d":
        raise ValueError("extended benchmark does not establish unadjusted daily index source")
    days = [row["trade_date"] for row in load_csv(market_root / "calendar.csv")]
    raw_days = [row["calendar_date"] for row in load_csv(market_root / "calendar_raw.csv") if row["is_trading_day"] == "1"]
    if days != raw_days:
        raise ValueError("extended calendar differs from provider open-day flags")
    benchmark_rows = load_csv(benchmark_path)
    raw_prices = load_csv(market_root / "sh_000300_raw.csv")
    if ([row["trade_date"] for row in benchmark_rows] != days or [row["date"] for row in raw_prices] != days
            or any(row["benchmark_id"] != protocol["benchmark_id"] for row in benchmark_rows)
            or any(row["code"] != protocol["benchmark_id"] for row in raw_prices)):
        raise ValueError("extended benchmark lacks unique ordered calendar/identifier coverage")
    quote_records = [row for row in primary["quote_checks"] if row["symbol"] == protocol["benchmark_id"]]
    if len(quote_records) != 1 or quote_records[0]["same_day_return_gate"] != "passed" or quote_records[0]["adjacent_price_gate"] != "passed":
        raise ValueError("extended benchmark lacks the original provider return/level gates")
    # The original provider validator records a fractional-return tolerance;
    # this report explicitly uses percentage points for the same differences.
    tolerance_pct = quote_records[0]["return_comparison_tolerance"] * 100
    same_day_differences, adjacent_differences, preclose_differences = [], [], []
    for index, (saved, raw) in enumerate(zip(benchmark_rows, raw_prices)):
        if Decimal(saved["close"]) != Decimal(raw["close"]):
            raise ValueError("extended benchmark price changed from original provider row")
        same = abs((float(raw["close"]) / float(raw["preclose"]) - 1) * 100 - float(raw["pctChg"]))
        adjacent = abs((float(raw["close"]) / float(raw_prices[index - 1]["close"]) - 1) * 100 - float(raw["pctChg"])) if index else 0.0
        if same > tolerance_pct or adjacent > tolerance_pct:
            raise ValueError("extended benchmark return basis is inconsistent")
        same_day_differences.append(same)
        adjacent_differences.append(adjacent)
        if index and Decimal(raw["preclose"]) != Decimal(raw_prices[index - 1]["close"]):
            preclose_differences.append({"trade_date": raw["date"], "source_preclose": raw["preclose"],
                                        "previous_source_close": raw_prices[index - 1]["close"],
                                        "difference_index_points": float(Decimal(raw["preclose"]) - Decimal(raw_prices[index - 1]["close"])),
                                        "adjacent_return_difference_percentage_points": adjacent})
    closes = {row["trade_date"]: float(row["close"]) for row in benchmark_rows}
    clocks = policy_rate_panel.make_clocks(days, protocol["study_start_date"], protocol["study_end_date"])
    if (len(clocks) != settings["expected_counts"]["calendar_steps"]
            or sum(row["trade_date"] <= protocol["training_end_date"] for row in clocks) != settings["expected_counts"]["training_steps"]
            or sum(row["trade_date"] >= protocol["evaluation_start_date"] for row in clocks) != settings["expected_counts"]["evaluation_steps"]):
        raise ValueError("extended diagnostic clock/split coverage differs")
    calendar = policy_calendar.build_calendar(records, clocks)
    channels = [(row["instrument"], row["tenor_value"], row["tenor_unit"]) for row in protocol["additional_policy_features"]]
    panel = policy_rate_panel.policy_vectors(records, calendar, channels)
    model_rows = policy_rate_panel.diagnostic_rows(days, closes, calendar, panel)
    results = policy_rate_panel.fit_diagnostic(model_rows, protocol["training_end_date"], protocol["evaluation_start_date"])
    results.update({"pipeline_version": protocol["version"], "benchmark_id": protocol["benchmark_id"],
                    "producer_version": "extended-rate-diagnostic-serialization-v2",
                    "benchmark_quote_checks": {"provider_rows": len(raw_prices), "tolerance_percentage_points": tolerance_pct,
                                               "maximum_same_day_difference_percentage_points": max(same_day_differences),
                                               "maximum_adjacent_difference_percentage_points": max(adjacent_differences),
                                               "exact_preclose_level_differences": preclose_differences,
                                               "price_levels_repaired": False},
                    "baseline_feature_order": protocol["baseline_features"], "policy_feature_order": protocol["additional_policy_features"],
                    "training_policy_dates": [row["trade_date"] for row in panel if row["observed_rate_changes"] and row["trade_date"] <= protocol["training_end_date"]],
                    "evaluation_policy_dates": [row["trade_date"] for row in panel if row["observed_rate_changes"] and row["trade_date"] >= protocol["evaluation_start_date"]],
                    "limitations": protocol["limitations"], "adoption_rule": protocol["adoption_rule"]})
    code = {str(Path(module.__file__).resolve()): file_sha256(Path(module.__file__))
            for module in (monetary_operations, policy_calendar, policy_rate_panel, provenance)}
    code[str(Path(__file__).resolve())] = file_sha256(Path(__file__))
    artifacts = {"policy_facts.json": {"records": records, "agent_signal_enabled": False, "scope": acquisition["interpretation"]},
                 "rate_calendar.json": {"calendar": calendar, "clock_rule": protocol["clock_rule"]},
                 "diagnostic_panel.json": {"policy_panel": panel, "model_rows": model_rows}, "results.json": results}
    artifacts["manifest.json"] = {"pipeline_version": protocol["version"], "producer_version": "extended-rate-diagnostic-serialization-v2", "inputs": dict(sorted(bindings.items())),
                                  "code_sha256": code,
                                  "artifacts": {name: {"sha256": hashlib.sha256(serialized(value)).hexdigest()} for name, value in artifacts.items()}}
    if any(file_sha256(Path(name)) != value for name, value in {**bindings, **code}.items()):
        raise ValueError("extended diagnostic input/producer changed during calculation")
    return artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("extended diagnostic needs a direct research_outputs directory")
    artifacts = compute()
    if args.audit_existing:
        for name, value in artifacts.items():
            if (output / name).read_bytes() != serialized(value):
                raise ValueError(f"extended diagnostic artifact differs: {name}")
        print("All five extended diagnostic artifacts rebuilt byte-identically.")
    else:
        if output.exists():
            raise ValueError("extended diagnostic output exists; use an unused directory")
        output.mkdir()
        for name, value in artifacts.items():
            with (output / name).open("xb") as handle:
                handle.write(serialized(value))
    result = artifacts["results.json"]
    print(json.dumps({key: result[key] for key in ("training_rows", "evaluation_rows", "excluded_rows", "training_designs", "coefficients_fitted", "training_policy_dates", "evaluation_policy_dates")}, ensure_ascii=False))
    print(json.dumps({target: {name: model["metrics"] for name, model in values["models"].items()} for target, values in result["targets"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
