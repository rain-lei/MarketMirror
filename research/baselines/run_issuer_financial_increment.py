"""Build the frozen financial-factor development check from existing sources."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

from . import issuer_financial_increment as diagnostic
from .run_experiments import _load_market
from ..data_pipeline.market_activity import DOCUMENTATION, REQUIRED_RAW, parse_activity_row
from ..data_pipeline.provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/issuer_financial_increment_2019.json"
OUTPUT = ROOT / "research_outputs/issuer_financial_increment_2019_v1"


def serialize(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def compute() -> dict[str, dict]:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    if (cfg["version"] != "issuer-financial-common-panel-risk-diagnostic-v1" or
            cfg["market_features"] != diagnostic.MARKET_FEATURES or
            cfg["financial_features"] != diagnostic.FINANCIAL_FEATURES or
            cfg["target"] != "absolute_stock_return" or cfg["agent_signal_enabled"] is not False):
        raise ValueError("financial increment protocol differs")
    bindings = {}

    def bind(path, expected):
        path = Path(path).resolve()
        key = str(path)
        if key in bindings and bindings[key] != expected:
            raise ValueError("conflicting source bindings")
        bindings[key] = expected

    bind(CONFIG, file_sha256(CONFIG))
    paths, source = {}, {}
    for name, record in cfg["inputs"].items():
        path = (CONFIG.parent / record["path"]).resolve()
        bind(path, record["sha256"])
        paths[name], source[name] = path, json.loads(path.read_text(encoding="utf-8"))
    financial, industry, provenance = (source[name] for name in ("financial_states", "industry_memberships", "industry_provenance"))
    for values in (financial["inputs"], financial["code_sha256"], provenance["inputs"], provenance["code_sha256"]):
        for path, expected in values.items():
            bind(path, expected)
    market, download = source["market_manifest"], source["download_manifest"]
    if (financial["agent_signal_enabled"] is not False or market["pipeline_version"] != "market-import-v1" or
            market["data_kind"] != "observed" or market["settings"]["stock_return_basis"] != "adjusted_price_return" or
            market["settings"]["benchmark_price_basis"] != "price_index" or
            download["provider"] != "BaoStock" or download["data_kind"] != "observed" or
            download["documentation"] != DOCUMENTATION or download["benchmark_symbol"] != market["settings"]["benchmark_id"]):
        raise ValueError("market or source-state identity differs")
    bind(market["config_path"], market["config_sha256"])
    for value in market["inputs"].values():
        bind(value["path"], value["sha256"])
    csv_path = paths["market_manifest"].parent / "market_daily.csv"
    bind(csv_path, market["artifacts"]["market_daily.csv"]["sha256"])
    download_dir = paths["download_manifest"].parent
    for name, value in download["artifacts"].items():
        path = (download_dir / name).resolve()
        if path.parent != download_dir:
            raise ValueError("raw market artifact escapes its archive directory")
        bind(path, value["sha256"])
    for key, name in (("stock", "stock_returns.csv"), ("benchmark", "benchmark_prices.csv"), ("calendar", "calendar.csv")):
        if market["inputs"][key]["sha256"] != download["artifacts"][name]["sha256"]:
            raise ValueError("prepared market and download vintage differ")
    code_paths = [Path(__file__).resolve(), Path(diagnostic.__file__).resolve(),
                  ROOT / "research/baselines/run_experiments.py", ROOT / "research/data_pipeline/market_activity.py",
                  ROOT / "research/data_pipeline/market_data.py", ROOT / "research/data_pipeline/provenance.py",
                  ROOT / "research/data_pipeline/build_dataset.py"]
    code = {str(path): file_sha256(path) for path in code_paths}

    def verify():
        if any(file_sha256(Path(path)) != expected for path, expected in {**bindings, **code}.items()):
            raise ValueError("financial increment input or producer changed")

    verify()
    parent = source["cohort_archive"]["result"]
    codes = sorted(parent["sample"]["selected_stock_codes"])
    states = {r["stock_code"]: r for r in financial["companies"]}
    industries = {r["stock_code"]: r for r in industry["cohort_memberships"]}
    if (len(codes) != cfg["expected_parent_companies"] or len(codes) != len(set(codes)) or
            len(states) != len(financial["companies"]) or len(industries) != len(industry["cohort_memberships"]) or
            parent["market_dataset_id"] != market["market_dataset_id"] or
            set(states) != set(codes) or set(industries) != set(codes)):
        raise ValueError("frozen parent cohort or market identity differs")
    calendar = market["session_dates"]
    with (download_dir / "calendar.csv").open(encoding="utf-8-sig", newline="") as handle:
        if [r["trade_date"] for r in csv.DictReader(handle)] != calendar:
            raise ValueError("raw exchange calendar differs from prepared calendar")
    groups = _load_market(csv_path, market)
    observations = {(code, r.trade_date.isoformat()): (r.stock_return, r.market_return)
                    for code in codes for r in groups[code]}
    queries = [q for q in download["queries"] if q["function"] == "query_history_k_data_plus"]
    by_code = {q["symbol"].split(".")[1]: q for q in queries}
    if len(by_code) != len(queries) or not set(codes) <= set(by_code):
        raise ValueError("duplicate or missing raw market queries")
    amounts = {}
    for stock in codes:
        query = by_code[stock]
        symbol = query["symbol"]
        if (query["frequency"] != "d" or query["adjustflag"] != "1" or
                set(query["fields"].split(",")) != REQUIRED_RAW or
                query["raw_file"] != symbol.replace(".", "_") + "_raw.csv"):
            raise ValueError("raw stock activity query differs")
        with (download_dir / query["raw_file"]).open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != query["fields"].split(","):
                raise ValueError("raw market columns differ from their provider query")
            for raw in reader:
                row = parse_activity_row(raw, symbol)
                key = (stock, row["trade_date"])
                if key in amounts:
                    raise ValueError("raw stock activity has a duplicate company date")
                amounts[key] = float(row["amount_cny"])
    panel, split = diagnostic.build_panel(calendar, codes, observations, amounts, states, industries, cfg)
    result = diagnostic.fit_models(panel, split)
    result.update(pipeline_version=cfg["version"], market_dataset_id=market["market_dataset_id"],
                  parent_companies=len(codes), limitations=cfg["limitations"],
                  source_vintage={"market_retrieved_at": download["retrieved_at"],
                                  "market_and_PDF_bytes_certified_historical_point_in_time": False},
                  coverage_by_date=[{"trade_date": d,
                                     "paired_companies": sum(r["trade_date"] == d and r["paired_exclusion_reason"] is None for r in panel),
                                     "exclusion_counts": dict(Counter(r["paired_exclusion_reason"] for r in panel if r["trade_date"] == d and r["paired_exclusion_reason"]))}
                                    for d in split["dates"]],
                  interpretation="Source-dated, paired, industry-controlled absolute-return development diagnostic; no direction, causal effect, blind-validation or endogenous-market-calibration claim. No Agent signal is adopted.")
    artifacts = {"panel.json": {"pipeline_version": cfg["version"], "parent_stock_codes": codes,
                                "split": split, "rows": panel, "agent_signal_enabled": False},
                 "results.json": result}
    artifacts["manifest.json"] = {"pipeline_version": cfg["version"], "inputs": dict(sorted(bindings.items())),
                                  "code_sha256": code,
                                  "artifacts": {name: {"sha256": hashlib.sha256(serialize(value)).hexdigest()}
                                                for name, value in artifacts.items()}}
    verify()
    return artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("financial increment artifacts require a direct research_outputs directory")
    artifacts = compute()
    if args.audit_existing:
        if any((output / name).read_bytes() != serialize(value) for name, value in artifacts.items()):
            raise ValueError("financial increment differs from the frozen byte reconstruction")
        print("All three financial increment artifacts rebuilt byte-identically.")
    else:
        output.mkdir(exist_ok=False)
        for name, value in artifacts.items():
            with (output / name).open("xb") as handle:
                handle.write(serialize(value))
    print(json.dumps({"training_rows": artifacts["results.json"]["training_rows"],
                      "evaluation_rows": artifacts["results.json"]["evaluation_rows"],
                      "comparisons": {k: v["relative_error_improvement"] for k, v in artifacts["results.json"]["comparisons"].items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
