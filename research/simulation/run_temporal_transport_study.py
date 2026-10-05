"""Produce the registered full 2022 factorial with complete raw ledgers."""
from __future__ import annotations
import argparse
from collections import Counter
import gzip
from pathlib import Path
import tempfile

from .temporal_transport_study import (ROOT, CONFIG, OUTPUT, CELLS, encoded, read, require, load_study,
    seed_paths, path_key, controls_for, shocks_for, read_gzip, write_gzip, seal, checkpoint, bindings_ok, summarize)
from .agents import AgentParameters
from .temporal_public_information_risk import fixed
from .portfolio_temporal_whole_information import simulate_portfolio
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
        print(f"Transport producer seed {si + 1}/5 condition {ci + 1}/48 complete.", flush=True)
    bindings_ok(cfg)
    record = {"status": "COMPLETE_FULL_TRANSPORT_SEED", "seed_id": seed["seed_id"],
        "protocol_sha256": file_sha256(protocol_path), "checkpoints": {str(path.relative_to(folder)).replace("\\", "/"): file_sha256(path)
            for path in [prepared / "checkpoint.json"] + [folder / f"cell_{i}/checkpoint.json" for i in range(len(CELLS))]}}
    with (folder / "production_complete.json").open("xb") as stream:
        stream.write(encoded(record))
    # Function completion is separate from the wrapper's observed child exit.
    with (folder / "producer_function_complete.json").open("xb") as stream:
        stream.write(encoded({"status": "FULL_FUNCTION_SCOPE_COMPLETE_EXIT_NOT_YET_OBSERVED",
            "claim_sha256": file_sha256(claim), "production_complete_sha256": file_sha256(folder / "production_complete.json")}))


def finish(protocol_path=CONFIG):
    cfg, _, _, _, mask = load_study(protocol_path)
    target = OUTPUT / "results.json"
    require(not target.exists(), "Preserve complete transport results")
    jobs_path = ROOT / cfg["jobs_receipt_path"]
    jobs = read(jobs_path)
    require(jobs["status"] == "COMPLETE_FULL_TRANSPORT_CHILD_PROCESSES" and jobs["protocol_sha256"] == file_sha256(protocol_path)
        and len(jobs["jobs"]) == 10 and all(type(j["observed_process_exit_code"]) is int and j["observed_process_exit_code"] == 0 for j in jobs["jobs"])
        and {(j["seed_index"], j["phase"]) for j in jobs["jobs"]} == {(i, p) for i in range(5) for p in ("producer", "audit")},
        "All ten distinct actual producer/audit exits required")
    for job in jobs["jobs"]:
        require(file_sha256(ROOT / job["log_path"]) == job["log_sha256"]
            and file_sha256(ROOT / job["receipt_path"]) == job["receipt_sha256"], "Actual transport process evidence changed")
    variants, counts, producer_counts, bindings = [], Counter(), Counter(), {}
    for si, seed in enumerate(cfg["seeds"]):
        folder = OUTPUT / f"seed_{si}"
        production, audit = read(folder / "production_complete.json"), read(folder / "independent.json")
        require(production["status"] == "COMPLETE_FULL_TRANSPORT_SEED" and production["protocol_sha256"] == file_sha256(protocol_path)
            and audit["status"] == "PASS_FULL_TRANSPORT_SEED_RAW_LEDGER_AND_STATISTICS"
            and audit["protocol_sha256"] == file_sha256(protocol_path) and audit["checks"] == cfg["per_seed_audit_scope"],
            "Complete transport production/audit scope differs")
        for name, digest in production["checkpoints"].items():
            require(file_sha256(folder / name) == digest, "Completed transport checkpoint changed")
            bindings[(folder / name).relative_to(ROOT).as_posix()] = digest
        checkpoint(folder / "prepared", [seed["seed_id"], "input_paths"], protocol_path)
        for ci, cell in enumerate(CELLS):
            checkpoint(folder / f"cell_{ci}", [seed["seed_id"], cell["name"]], protocol_path)
            saved = read(folder / f"cell_{ci}/condition.json")
            # Full daily rows and every account remain in each bound condition.
            variants.append({k: v for k, v in saved["variant"].items() if k != "daily_asset_rows"})
            producer_counts.update(saved["checks"])
        for name in ("production_complete.json", "independent.json", "producer_claim.json", "producer_function_complete.json"):
            bindings[(folder / name).relative_to(ROOT).as_posix()] = file_sha256(folder / name)
        counts.update(audit["checks"])
    require(dict(counts) == cfg["derived_expected_coverage"] and producer_counts["first_basket_complete_replays"] == 240,
        "Complete 240-condition transport coverage differs")
    result = {"status": "COMPLETE_FULL_TRANSPORT_STUDY_2022_V1", "protocol_sha256": file_sha256(protocol_path),
        "sample": {"companies": len(cfg["stocks"]), "sessions": len(cfg["dates"]), "baskets": len(cfg["baskets"])},
        "seeds": cfg["seeds"], "variants": variants, "checks": dict(counts), "producer_checks": dict(producer_counts),
        **summarize(variants, cfg["seeds"]), "artifacts": {**bindings, jobs_path.relative_to(ROOT).as_posix(): file_sha256(jobs_path)},
        "evaluation_mask": {k: v for k, v in mask.items() if k not in ("targets_by_stock", "correlation_pairs", "known_dates_by_stock")},
        "semantic_gate_enabled": False, "no_new_parameter_default": True, "candidate_selected_as_default": False,
        "point_in_time_feed_certified": False, "economic_total_return_certified": False, "interpretation": cfg["interpretation"]}
    with target.open("xb") as stream:
        stream.write(encoded(result))
    print("All 240 transport conditions and complete independent seed audits finished.", flush=True)


def execute_cell(cfg, joined, mask, seed, cell, paths, core, background, stage):
    design, dates, stocks = cfg["model_design"], cfg["dates"], cfg["stocks"]
    agents = [AgentParameters(**r) for r in cfg["agents"]]
    daily, saved_paths, counts, flows = [], [], Counter(), Counter()
    with (stage / "ledger.jsonl.gz").open("xb") as raw, gzip.GzipFile(fileobj=raw, filename="", mode="wb", mtime=0) as ledger:
        for bi, basket in enumerate(cfg["baskets"]):
            issuer = fixed.subset(paths[path_key(cell)], basket)
            kwargs = dict(scenario_shocks=shocks_for(basket, dates), background_response=cfg["background_response"],
                issuer_valuation=issuer, quantity_controls=controls_for(cell["background_anchor"]),
                target_feedback_scale=cell["feedback_scale"], quote_feedback_scale=cell["feedback_scale"])
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
    parser.add_argument("--seed", type=int)
    parser.add_argument("--finish", action="store_true")
    args = parser.parse_args()
    if args.finish:
        require(args.seed is None, "Finish scope must be explicit")
        finish()
    else:
        run_seed(args.seed)
