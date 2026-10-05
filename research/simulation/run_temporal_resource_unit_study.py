# Derived full producer from original SHA256 379deeae09389959ab4dd6ff53d3515a1d5ded784108d19a7b1663c216aa88dc
"""Produce the registered full 2022 factorial with complete raw ledgers."""
from __future__ import annotations
import argparse
from collections import Counter
import gzip
from pathlib import Path
import tempfile

from .temporal_resource_unit_study import (ROOT, CONFIG, OUTPUT, CELLS, encoded, read, require, load_study,
    seed_paths, path_key, controls_for, shocks_for, read_gzip, write_gzip, seal, checkpoint, bindings_ok, summarize)
from .agents import AgentParameters
from .temporal_public_information_risk import fixed
from .portfolio_temporal_resource_units import simulate_portfolio
from .temporal_return_metrics import metrics
from ..data_pipeline.provenance import file_sha256


def run_seed(si, protocol_path=CONFIG):
    require(type(si) is int and 0 <= si < 5, "Seed outside registered transport grid")
    cfg, risk, factors, joined, mask = load_study(protocol_path)
    seed = cfg["seeds"][si]
    folder = OUTPUT / f"seed_{si}"
    folder.mkdir(parents=True, exist_ok=True)
    claim = folder / "producer_claim.json"
    require(not claim.exists(), "Preserve prior transport owner; inspect actual process before any recovery")
    import os
    from ..data_pipeline.temporal_transport_sources import windows_process_identity
    with claim.open("xb") as stream:
        stream.write(encoded({"status": "RUNNING", "owner": windows_process_identity(os.getpid()),
            "seed_id": seed["seed_id"], "protocol_sha256": file_sha256(protocol_path)}))
    core, background, paths = seed_paths(cfg, risk, factors, seed)
    prepared = folder / "prepared"
    if prepared.exists():
        checkpoint(prepared, [seed["seed_id"], "input_paths"], protocol_path)
        require(read_gzip(prepared / "issuer_paths.json.gz") == paths, "Resumed transport paths differ")
    else:
        stage = Path(tempfile.mkdtemp(prefix="prepare-", dir=folder))
        write_gzip(stage / "issuer_paths.json.gz", paths)
        seal(stage, ["issuer_paths.json.gz"], [seed["seed_id"], "input_paths"], protocol_path)
        stage.replace(prepared)
    for ci, cell in enumerate(CELLS):
        target = folder / f"cell_{ci}"
        if target.exists():
            checkpoint(target, [seed["seed_id"], cell["name"]], protocol_path)
        else:
            stage = Path(tempfile.mkdtemp(prefix=f"pending_{ci}_", dir=folder))
            execute_cell(cfg, joined, mask, seed, cell, paths, core, background, stage)
            seal(stage, ["ledger.jsonl.gz", "condition.json"], [seed["seed_id"], cell["name"]], protocol_path)
            stage.replace(target)
        print(f"Transport producer seed {si + 1}/5 condition {ci + 1}/96 complete.", flush=True)
    bindings_ok(cfg)
    record = {"status": "COMPLETE_FULL_RESOURCE_UNIT_SEED", "seed_id": seed["seed_id"],
        "protocol_sha256": file_sha256(protocol_path), "checkpoints": {str(path.relative_to(folder)).replace("\\", "/"): file_sha256(path)
            for path in [prepared / "checkpoint.json"] + [folder / f"cell_{i}/checkpoint.json" for i in range(len(CELLS))]}}
    with (folder / "production_complete.json").open("xb") as stream:
        stream.write(encoded(record))
    # Function completion is separate from the wrapper's observed child exit.
    with (folder / "producer_function_complete.json").open("xb") as stream:
        stream.write(encoded({"status": "FULL_FUNCTION_SCOPE_COMPLETE_EXIT_NOT_YET_OBSERVED",
            "claim_sha256": file_sha256(claim), "production_complete_sha256": file_sha256(folder / "production_complete.json")}))


def execute_cell(cfg, joined, mask, seed, cell, paths, core, background, stage):
    design, dates, stocks = cfg["model_design"], cfg["dates"], cfg["stocks"]
    agents = [AgentParameters(**r) for r in cfg["agents"]]
    daily, saved_paths, counts, flows = [], [], Counter(), Counter()
    with (stage / "ledger.jsonl.gz").open("xb") as raw, gzip.GzipFile(fileobj=raw, filename="", mode="wb", mtime=0) as ledger:
        for bi, basket in enumerate(cfg["baskets"]):
            issuer = fixed.subset(paths[path_key(cell)], basket)
            kwargs = dict(scenario_shocks=shocks_for(basket, dates), background_response=cfg["background_response"],
                issuer_valuation=issuer, quantity_controls=controls_for(cell["background_anchor"]),
                target_feedback_scale=cell["feedback_scale"], quote_feedback_scale=cell["feedback_scale"],
                resource_arm=cell["resource_arm"], initial_prices_minor={s: cfg["initial_raw_prices_minor"][s] for s in basket})
            args = ({s: joined[s] for s in basket}, agents, core, background, design["venue"],
                design["feedback_parameters"], design["case"], False)
            sim = simulate_portfolio(*args, **kwargs)
            if bi == 0:
                again = simulate_portfolio(*args, **kwargs)
                require(encoded(sim) == encoded(again), "first-basket entire simulation replay differs")
                counts["first_basket_complete_replays"] += 1
            require(len(sim["trace"]) == len(dates), "temporal path length differs")
            specs = sim["participant_specs"]
            for session, day in enumerate(sim["trace"]):
                require(day["trade_date"] == dates[session]
                    and day["signal_cutoff_date"] == joined[basket[0]][session]["signal_cutoff_date"]
                    and day["execution_reference_date"] == joined[basket[0]][session]["execution_reference_date"], "model clock differs")
                for stock, call in day["portfolio_auction"]["asset_calls"].items():
                    daily.append({"stock_code": stock, "trade_date": day["trade_date"], "signal_cutoff_date": day["signal_cutoff_date"],
                        "price_before_minor": call["price_before_minor"], "price_after_minor": call["price_after_minor"],
                        "matched_volume": call["matched_volume"], "observed_return": joined[stock][session]["observed_return"]})
                    require(call["execution_available"] is joined[stock][session]["execution_available"], "retrospective venue condition differs")
                    for order in call["orders"]:
                        role = "background" if specs[order["owner"]]["kind"] == "background" else specs[order["owner"]]["parameters"]["role"]
                        for label, field in (("requested", "quantity"), ("accepted", "accepted_quantity"), ("filled", "filled_quantity")):
                            flows[role + "|" + order["side"] + "|" + label] += order[field]
                        if "cash_and_fee_reservation" in order["reasons"]:
                            flows[role + "|" + order["side"] + "|cash_clipped"] += order["quantity"] - order["accepted_quantity"]
                ledger.write(encoded({"seed_id": seed["seed_id"], "variant": cell["name"], "basket_index": bi, **day}))
                counts["full_path_ledger_records"] += 1
                counts["asset_calls"] += len(basket)
            saved_paths.append({"seed_id": seed["seed_id"], "variant": cell["name"], "basket_index": bi,
                "participant_specs": specs, "summary": sim["summary"]})
            counts["paths"] += 1
            if (bi + 1) % 10 == 0:
                print(f"{seed['seed_id']} {cell['name']}: {bi + 1}/{len(cfg['baskets'])} baskets saved.", flush=True)
    require(dict(counts) == cfg["per_condition_producer_scope"], "temporal complete producer scope differs")
    variant = {"seed_id": seed["seed_id"], **cell, **metrics(daily, mask, cfg["joint_limits"]),
        "daily_asset_rows": daily, "role_order_quantities": dict(flows), "total_matched_volume": sum(r["matched_volume"] for r in daily)}
    with (stage / "condition.json").open("xb") as stream:
        stream.write(encoded({"variant": variant, "paths": saved_paths, "checks": dict(counts)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, required=True)
    run_seed(parser.parse_args().seed)
