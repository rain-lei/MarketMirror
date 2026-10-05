"""Compare current default auction paths value-for-value with the old Wuhan ledger."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .portfolio_market import simulate_portfolio
from .wuhan_portfolio_baseline import CODE_PATHS, _load_inputs


ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "research_outputs/wuhan_pit_portfolio_no_text_2020_v2"
CONFIG = ROOT / "research/configs/wuhan_pre_event_pit_portfolio_no_text_2020.json"
OUTPUT = ROOT / "research_outputs/wuhan_default_tie_compatibility_v1.json"
FILES = ("portfolio_no_text_summary.json", "portfolio_no_text_paths.json",
         "portfolio_no_text_ledger.jsonl.gz", "portfolio_no_text_report.md")


def compute(archive: Path = ARCHIVE, config_path: Path = CONFIG) -> dict:
    archive, config_path = archive.resolve(), config_path.resolve()
    manifest = json.loads((archive / "portfolio_no_text_manifest.json").read_text(encoding="utf-8"))
    for name in FILES:
        if manifest.get("artifacts", {}).get(name, {}).get("sha256") != file_sha256(archive / name):
            raise ValueError(f"archived artifact hash differs: {name}")
    cfg, _, market, agents, joined, baskets, inputs = _load_inputs(config_path)
    if manifest.get("inputs") != inputs:
        raise ValueError("archived input files differ from current source preparation")
    summary = json.loads((archive / FILES[0]).read_text(encoding="utf-8"))
    paths = json.loads((archive / FILES[1]).read_text(encoding="utf-8"))
    by_id = {row["path_id"]: row for row in paths}
    expected = {f"{index}:{case['case_id']}" for index in range(len(baskets)) for case in cfg["cases"]}
    if (len(by_id) != len(paths) or set(by_id) != expected or summary["paths"] != len(paths)
            or summary["market_dataset_id"] != market["market_dataset_id"]):
        raise ValueError("archived path identities or market source differ")
    rows = asset_calls = 0
    with gzip.open(archive / FILES[2], "rt", encoding="utf-8") as stream:
        for index, assets in enumerate(baskets):
            for case in cfg["cases"]:
                identity = f"{index}:{case['case_id']}"
                current = simulate_portfolio({asset: joined[asset] for asset in assets}, agents,
                                             cfg["core"], cfg["background"], cfg["venue"],
                                             cfg["feedback_parameters"], case, False)
                if {"path_id": identity, "basket_index": index, "case_id": case["case_id"],
                        **current["summary"]} != by_id[identity]:
                    raise ValueError(f"default final path differs from archive: {identity}")
                for day in current["trace"]:
                    line = stream.readline()
                    if not line or json.loads(line) != {"path_id": identity, **day}:
                        raise ValueError(f"default daily ledger differs from archive: {identity}")
                    rows += 1
                    asset_calls += len(day["portfolio_auction"]["asset_calls"])
        if stream.readline():
            raise ValueError("archived ledger contains extra rows")
    if rows != summary["ledger_rows"] or asset_calls != summary["asset_call_rows"]:
        raise ValueError("default replay coverage differs from archive")
    old_code = manifest["code_sha256"]
    code_differences = [name for name, original in old_code.items()
                        if file_sha256(CODE_PATHS[name]) != original]
    return {"pipeline_version": "wuhan-default-tie-numerical-compatibility-v1",
            "experiment_id": summary["experiment_id"], "paths": len(paths),
            "ledger_rows": rows, "asset_calls": asset_calls,
            "all_archived_days_and_paths_equal": True,
            "archived_code_hash_differences": code_differences,
            "interpretation": "Current default rules numerically reproduce the saved ledger. Archived source hashes are historical and differ from current code; this is not a claim of unchanged source identity.",
            "input_sha256": {str(path): file_sha256(path) for path in
                             (archive / "portfolio_no_text_manifest.json", *(archive / name for name in FILES),
                              config_path)},
            "current_code_sha256": {name: file_sha256(path) for name, path in CODE_PATHS.items()},
            "audit_code_sha256": file_sha256(Path(__file__))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute()
    destination = args.output.resolve()
    if args.audit_existing:
        if json.loads(destination.read_text(encoding="utf-8")) != result:
            raise ValueError("compatibility archive differs from current recomputation")
    else:
        if destination.exists() or destination.parent != (ROOT / "research_outputs").resolve():
            raise ValueError("compatibility output must be a new research_outputs file")
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("paths", "ledger_rows", "asset_calls",
                                                 "all_archived_days_and_paths_equal",
                                                 "archived_code_hash_differences")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
