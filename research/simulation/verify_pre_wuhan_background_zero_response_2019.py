"""Compare full legacy-source and current zero-response portfolio objects."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .agents import AgentParameters
from .portfolio_market import simulate_portfolio
from .run_pre_wuhan_background_response_2019 import ROOT, load_inputs, verify_hashes
from .scenario_shocks import build_scenario_shocks
from .semantic_memory_sensitivity import canonical_hash

SNAPSHOT = ROOT / "research_outputs/pre_wuhan_background_reference_source_2019_v1/portfolio_market.py"
OUTPUT = ROOT / "research_outputs/pre_wuhan_background_zero_response_compatibility_2019_v1.json"


def compute() -> dict:
    config, base, replay, _, codes, dates, _, joined, _, inputs, code_hashes, upgrades = load_inputs()
    source_path = (ROOT / "research/configs" / config["reference_archive"]).resolve()
    source = json.loads(source_path.read_text(encoding="utf-8"))
    original_path = str((ROOT / "research/simulation/portfolio_market.py").resolve())
    if file_sha256(SNAPSHOT) != source["code_sha256"][original_path]:
        raise ValueError("legacy source snapshot differs from original archive")
    spec = importlib.util.spec_from_file_location("research.simulation._verified_portfolio_source_2019", SNAPSHOT)
    if spec is None or spec.loader is None:
        raise ValueError("cannot load the verified historical module")
    legacy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy)
    agents = [AgentParameters(**row) for row in replay["agents"]]
    case = next(row for row in base["cases"] if row["case_id"] == "separated_institution35")
    paths = []
    for index in range(0, len(codes), 3):
        basket = codes[index:index + 3]
        shocks = build_scenario_shocks(basket, len(dates), "common_only", config["pulse_sessions"],
                                       config["pulse_polarities"], config["common_amplitude"], 0.0, config["issuer_seed"])
        args = ({code: joined[code] for code in basket}, agents, base["core"], base["background"],
                base["venue"], base["feedback_parameters"], case, False)
        previous = legacy.simulate_portfolio(*args, scenario_shocks=shocks)
        current = simulate_portfolio(*args, scenario_shocks=shocks,
                                     background_response={"valuation_response_bps": 0, "pulse_participation_bps": 10000})
        if previous != current:
            raise ValueError("current zero-response full simulation differs from original source")
        paths.append({"basket_index": index // 3, "assets": basket,
                      "full_simulation_sha256": canonical_hash(current), "sessions": len(current["trace"])})
    verify_hashes(inputs)
    verify_hashes(code_hashes)
    return {"pipeline_version": "pre-wuhan-full-legacy-source-zero-response-comparison-v1",
            "equal_full_simulation_objects": len(paths), "portfolio_sessions": sum(row["sessions"] for row in paths),
            "asset_sessions": sum(len(row["assets"]) * row["sessions"] for row in paths),
            "compared_fields": "entire nested objects: summaries, participant specs, observations, covariance, decisions, demands, orders, trades, escrows and daily wallets",
            "source_archive_sha256": file_sha256(source_path), "historical_source_sha256": file_sha256(SNAPSHOT),
            "reviewed_code_upgrades": upgrades, "current_code_sha256": code_hashes, "paths": paths}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute()
    payload = {"result": result, "code_sha256": file_sha256(Path(__file__))}
    output = args.output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("compatibility audit output must be under research_outputs")
    if args.audit_existing:
        if json.loads(output.read_text(encoding="utf-8")) != payload:
            raise ValueError("archived zero-response compatibility audit differs")
    else:
        if output.exists():
            raise ValueError("choose a fresh compatibility output")
        output.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("equal_full_simulation_objects", "portfolio_sessions", "asset_sessions")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
