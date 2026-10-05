"""Read-only full-quarter transmission study over both completed allocations."""
from collections import Counter
import gzip
import json
from pathlib import Path

from .temporal_information_study import ROOT, CELLS, read, read_gzip, encoded, require, checkpoint as base_checkpoint
from .temporal_initial_allocation_study import checkpoint as allocation_checkpoint
from .audit_pre_wuhan_allocation_attribution_2019 import initial_from_specs
from .audit_initial_resource_allocation import initial_allocated_state
from .signal_transmission_diagnostics import diagnose, panel_statistics
from .verify_pre_wuhan_order_price_diagnostics_2019 import compare
from ..data_pipeline.provenance import file_sha256

CONFIG = ROOT / "research/configs/temporal_signal_transmission_2021_v1.json"
OUTPUT = ROOT / "research_outputs/temporal_signal_transmission_2021_v1"
SOURCES = {
    "baseline": (ROOT / "research/configs/temporal_public_information_risk_2021_v1.json", ROOT / "research_outputs/temporal_public_information_risk_2021_v1"),
    "candidate": (ROOT / "research/configs/temporal_initial_allocation_2021_v1.json", ROOT / "research_outputs/temporal_initial_allocation_2021_v1"),
}


def inputs():
    cfg = read(SOURCES["candidate"][0])
    original = read(SOURCES["baseline"][0])
    for key in ("stocks", "dates", "seeds", "baskets", "variants", "model_design", "issuer_parameters", "evaluation_mask_path"):
        require(cfg[key] == original[key], "transmission source scope differs")
    return cfg, read_gzip(ROOT / cfg["evaluation_mask_path"])


def load():
    protocol = read(CONFIG)
    require(protocol["version"] == "full-temporal-signal-transmission-2021-v1"
        and protocol["is_full_path_counterfactual"] is False and protocol["model_parameters_changed"] is False,
        "transmission protocol differs")
    for name, digest in protocol["bindings"].items():
        require(file_sha256(ROOT / name) == digest, "transmission frozen evidence changed: " + name)
    cfg, mask = inputs()
    require(protocol["seeds"] == cfg["seeds"] and protocol["variants"] == CELLS, "complete transmission grid differs")
    return protocol, cfg, mask


def source(si, arm, ci, cfg):
    root = SOURCES[arm][1]
    seed_id, cell = cfg["seeds"][si]["seed_id"], CELLS[ci]
    folder = root / f"seed_{si}/cell_{ci}"
    check = base_checkpoint if arm == "baseline" else allocation_checkpoint
    check(folder, [seed_id, cell["name"]])
    check(root / f"seed_{si}/prepared", [seed_id, "input_paths"])
    return folder, read(folder / "condition.json")


def conditions(si, cfg):
    # These paths were already independently reconstructed in both source studies.
    old = read_gzip(SOURCES["baseline"][1] / f"seed_{si}/prepared/issuer_paths.json.gz")
    new = read_gzip(SOURCES["candidate"][1] / f"seed_{si}/prepared/issuer_paths.json.gz")
    require(old == new, "allocation altered generated information paths")
    return old


def process_condition(si, arm, ci, cfg, mask, prepared, output, analyser=diagnose, statistics_function=panel_statistics):
    folder, saved = source(si, arm, ci, cfg)
    seed_id, cell = cfg["seeds"][si]["seed_id"], CELLS[ci]
    issuer = prepared[cell["risk_mode"] + "_" + cell["delivery"]]
    source_rows = {(r["stock_code"], r["trade_date"]): r for r in saved["variant"]["daily_asset_rows"]}
    counts, role_counts, rows = Counter(), {r: Counter() for r in ("aggressive", "conservative", "institutional", "background")}, []
    design = cfg["model_design"]
    with gzip.open(folder / "ledger.jsonl.gz", "rt", encoding="utf-8") as stream:
        for bi, path in enumerate(saved["paths"]):
            basket, specs = cfg["baskets"][bi], path["participant_specs"]
            require((path["basket_index"], path["seed_id"], path["variant"]) == (bi, seed_id, cell["name"]), "archived path identity differs")
            previous = (initial_allocated_state if arm == "candidate" else initial_from_specs)(specs, basket, design, design["case"])["accounts"]
            for session, date in enumerate(cfg["dates"]):
                line = next(stream, None)
                require(line is not None, "source ledger ended early")
                day = json.loads(line)
                require((day["seed_id"], day["variant"], day["basket_index"], day["trade_date"], day["portfolio_auction"]["session"])
                    == (seed_id, cell["name"], bi, date, session), "source ledger clock differs")
                require(sorted(day["observations"]) == sorted(day["portfolio_auction"]["asset_calls"]) == basket, "complete source basket required")
                for stock in basket:
                    observation = day["observations"][stock]
                    require(observation["common_shock"] == 0
                        and ("industry_shock" not in observation or observation["industry_shock"] == 0)
                        and observation["text_signal"] == 0, "unregistered policy, industry or text signal")
                    result = analyser(day, stock, specs, issuer["by_stock"][stock][session], issuer["parameters"],
                        design["venue"], design["background"], previous)
                    reference = source_rows[stock, date]
                    call = day["portfolio_auction"]["asset_calls"][stock]
                    require((reference["price_before_minor"], reference["price_after_minor"]) == (call["price_before_minor"], call["price_after_minor"]), "saved return price differs")
                    record = {"arm": arm, "seed_id": seed_id, "name": cell["name"], "basket_index": bi,
                        "stock_code": stock, "trade_date": date, "signal_cutoff_date": day["signal_cutoff_date"],
                        "observed_return": reference["observed_return"], "stages": result["stages"],
                        "accepted_book_probes": result["accepted_book_probes"]}
                    output(record)
                    rows.append(record)
                    counts.update(result["counts"])
                    counts["unknown_source_targets_preserved"] += int(reference["observed_return"] is None)
                    counts["accepted_book_price_checks"] += 3
                    for role in role_counts:
                        role_counts[role].update(result["roles"][role])
                previous = day["portfolio_auction"]["accounts"]
                counts["ledger_records"] += 1
            counts["paths"] += 1
        require(next(stream, None) is None, "source ledger has extra records")
    require(counts["books"] == 7134 and counts["owner_positions"] == 171216 and counts["ledger_records"] == 2378
        and counts["paths"] == 41 and counts["unknown_source_targets_preserved"] == 15, "full transmission condition coverage differs")
    metrics = statistics_function(rows, mask)
    common = saved["variant"]["common_factor_metrics"]["synthetic"]
    compare(metrics["actual_return_bps"]["mean_stock_correlation"], common["mean_pairwise_stock_return_correlation"])
    compare(metrics["actual_return_bps"]["equal_weight_common_std_bps"], common["equal_weight_daily_return_std"] * 10000)
    return {"arm": arm, "seed_id": seed_id, **cell, "checks": dict(counts),
        "role_checks": {r: dict(v) for r, v in role_counts.items()}, "stage_statistics": metrics}
