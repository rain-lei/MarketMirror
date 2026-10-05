"""Produce all five seeds and sixteen conditions with complete raw ledgers."""
from __future__ import annotations
import argparse
from collections import Counter
import gzip
from pathlib import Path
import tempfile

from .temporal_whole_information_study import (ROOT, CONFIG, OUTPUT, CELLS, encoded, read, require, load_study,
    seed_paths, path_key, controls_for, shocks_for, read_gzip, write_gzip, seal, checkpoint, bindings_ok, summarize)
from .agents import AgentParameters
from .temporal_public_information_risk import fixed
from .portfolio_temporal_whole_information import simulate_portfolio
from .temporal_return_metrics import metrics
from ..data_pipeline.provenance import file_sha256


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


def run_seed(si):
    require(type(si) is int and 0 <= si < 5, "seed outside frozen temporal grid")
    cfg, risk, factors, joined, mask = load_study()
    seed = cfg["seeds"][si]
    folder = OUTPUT / f"seed_{si}"
    folder.mkdir(parents=True, exist_ok=True)
    core, background, paths = seed_paths(cfg, risk, factors, seed)
    prepared = folder / "prepared"
    if prepared.exists():
        checkpoint(prepared, [seed["seed_id"], "input_paths"])
        require(read_gzip(prepared / "issuer_paths.json.gz") == paths, "resumed input paths differ")
    else:
        stage = Path(tempfile.mkdtemp(prefix="prepare-", dir=folder))
        write_gzip(stage / "issuer_paths.json.gz", paths)
        seal(stage, ["issuer_paths.json.gz"], [seed["seed_id"], "input_paths"])
        stage.replace(prepared)
    for ci, cell in enumerate(CELLS):
        target = folder / f"cell_{ci}"
        if target.exists():
            checkpoint(target, [seed["seed_id"], cell["name"]])
        else:
            stage = Path(tempfile.mkdtemp(prefix=f"pending_{ci}_", dir=folder))
            execute_cell(cfg, joined, mask, seed, cell, paths, core, background, stage)
            seal(stage, ["ledger.jsonl.gz", "condition.json"], [seed["seed_id"], cell["name"]])
            stage.replace(target)
        print(f"Temporal producer seed {si + 1}/5 condition {ci + 1}/16 complete.", flush=True)
    bindings_ok(cfg)
    record = {"status": "COMPLETE_FULL_TEMPORAL_WHOLE_INFORMATION_SEED", "seed_id": seed["seed_id"],
        "protocol_sha256": file_sha256(CONFIG), "checkpoints": {str(p.relative_to(folder)): file_sha256(p)
            for p in [prepared / "checkpoint.json"] + [folder / f"cell_{i}/checkpoint.json" for i in range(16)]}}
    target = folder / "production_complete.json"
    if target.exists():
        require(target.read_bytes() == encoded(record), "completed seed receipt changed")
    else:
        with target.open("xb") as stream:
            stream.write(encoded(record))


def finish():
    cfg, _, _, _, mask = load_study()
    target = OUTPUT / "results.json"
    require(not target.exists(), "preserve completed temporal results")
    jobs = read(ROOT / cfg["jobs_receipt_path"])
    require(len(jobs["jobs"]) == 10 and all(j["observed_process_exit_code"] == 0 for j in jobs["jobs"])
        and {(j["seed_index"], j["phase"]) for j in jobs["jobs"]} == {(i, p) for i in range(5) for p in ("producer", "audit")},
        "all distinct actual producer/audit exits required")
    variants, counts, producer_counts, bindings = [], Counter(), Counter(), {}
    for si, seed in enumerate(cfg["seeds"]):
        folder = OUTPUT / f"seed_{si}"
        production, audit = read(folder / "production_complete.json"), read(folder / "independent.json")
        require(production["status"] == "COMPLETE_FULL_TEMPORAL_WHOLE_INFORMATION_SEED"
            and production["protocol_sha256"] == file_sha256(CONFIG)
            and audit["status"] == "PASS_FULL_TEMPORAL_WHOLE_INFORMATION_SEED_RAW_LEDGER_AND_STATISTICS"
            and audit["checks"] == cfg["per_seed_audit_scope"], "complete production/audit scope differs")
        for name, digest in production["checkpoints"].items():
            require(file_sha256(folder / name) == digest, "completed checkpoint changed")
            bindings[str((folder / name).relative_to(ROOT))] = digest
        checkpoint(folder / "prepared", [seed["seed_id"], "input_paths"])
        for ci, cell in enumerate(CELLS):
            checkpoint(folder / f"cell_{ci}", [seed["seed_id"], cell["name"]])
            saved = read(folder / f"cell_{ci}/condition.json")
            variants.append(saved["variant"])
            producer_counts.update(saved["checks"])
        for name in ("production_complete.json", "independent.json"):
            bindings[str((folder / name).relative_to(ROOT))] = file_sha256(folder / name)
        counts.update(audit["checks"])
    require(dict(counts) == cfg["derived_expected_coverage"] and producer_counts["first_basket_complete_replays"] == 80,
        "complete eighty-condition coverage differs")
    result = {"status": "COMPLETE_FULL_TEMPORAL_WHOLE_INFORMATION_STUDY_2021_V1",
        "protocol_sha256": file_sha256(CONFIG), "sample": {"companies": len(cfg["stocks"]), "sessions": len(cfg["dates"])},
        "seeds": cfg["seeds"], "variants": variants, "checks": dict(counts), "producer_checks": dict(producer_counts),
        **summarize(variants, cfg["seeds"]), "artifacts": bindings, "evaluation_mask": {k: v for k, v in mask.items() if k not in
            ("targets_by_stock", "correlation_pairs", "known_dates_by_stock")},
        "semantic_gate_enabled": False, "no_new_parameter_default": True, "interpretation": cfg["interpretation"]}
    with target.open("xb") as stream:
        stream.write(encoded(result))
    print("All eighty temporal conditions and independent raw audits finished.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--finish", action="store_true")
    args = parser.parse_args()
    if args.finish:
        require(args.seed is None, "finish scope must be explicit")
        finish()
    else:
        run_seed(args.seed)
