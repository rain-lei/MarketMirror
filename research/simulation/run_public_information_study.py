"""Produce durable complete fixed-risk conditions and replay each artifact bytewise."""
from __future__ import annotations
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import tempfile

from .public_information_study import (ROOT, CONFIG, OUTPUT, CELLS, read, require, load_study,
    seed_paths, checkpoint, seal, path_key, prior_index, write_gzip_json, read_gzip_json, summary)
from . import fixed_risk_study as prior
from .fixed_risk_study import legacy_name
from ..data_pipeline.provenance import file_sha256
from .run_pre_wuhan_public_factor_channels_2019 import (AgentParameters, controls_for, subset_industry_shocks,
    record_bytes, metrics, verify_specs)
from .public_information_risk import subset
from .portfolio_public_information_risk import simulate_portfolio
from .public_factor_numeric_inputs_v3 import verify_numeric

def execute_cell(loaded, cfg, seed, si, cell, ci, paths, core, background, stage):
    _, base, replay, _, codes, dates, _, joined, _, _, shared, _, _, industry_cfg, source = loaded
    agents = [AgentParameters(**r) for r in replay["agents"]]
    case = next(c for c in base["cases"] if c["case_id"] == "separated_institution35")
    old_paths = {(r["seed_id"], r["variant"], r["basket_index"]): r for r in source["path_summaries"]}
    old_ci = prior_index(cell)
    golden_folder = prior.OUTPUT / f"seed_{si}/cell_{old_ci}"
    prior.checkpoint(golden_folder, [seed["seed_id"], prior.CELLS[old_ci]["name"]])
    golden = read(golden_folder / "condition.json")
    daily, saved_paths, counts, flows = [], [], Counter(), Counter()
    with (stage / "ledger.jsonl.gz").open("wb") as raw, gzip.GzipFile(fileobj=raw, filename="", mode="wb", mtime=0) as ledger:
        old_stream = gzip.open(golden_folder / "ledger.jsonl.gz", "rt", encoding="utf-8") if cell["delivery"] == "masked" else None
        try:
            for bi in range(41):
                basket = codes[bi * 3:(bi + 1) * 3]
                issuer = subset(paths[path_key(cell)], basket)
                shocks = {"scenario_id": "common_component", "common": shared["common"], "asset_specific": {s: [0.0] * len(dates) for s in basket}}
                sim = simulate_portfolio({s: joined[s] for s in basket}, agents, core, background, base["venue"],
                    base["feedback_parameters"], case, False, scenario_shocks=shocks,
                    background_response=industry_cfg["background_response"], industry_shocks=subset_industry_shocks(shared["industry"], basket),
                    issuer_valuation=issuer, quantity_controls=controls_for(cell["background_anchor"]),
                    target_feedback_scale=cell["feedback_scale"], quote_feedback_scale=cell["feedback_scale"])
                if cell["delivery"] == "public" and bi == 0:
                    again = simulate_portfolio({s: joined[s] for s in basket}, agents, core, background, base["venue"],
                        base["feedback_parameters"], case, False, scenario_shocks=shocks,
                        background_response=industry_cfg["background_response"], industry_shocks=subset_industry_shocks(shared["industry"], basket),
                        issuer_valuation=issuer, quantity_controls=controls_for(cell["background_anchor"]),
                        target_feedback_scale=cell["feedback_scale"], quote_feedback_scale=cell["feedback_scale"])
                    require(record_bytes(sim) == record_bytes(again), "first public basket complete replay differs")
                    counts["first_public_basket_complete_replays"] += 1
                specs = sim["participant_specs"]
                original = golden["paths"][bi]
                verify_specs(specs, old_paths[seed["seed_id"], cell["background_anchor"] + "_feedback_full", bi]["participant_specs"],
                    cell["feedback_scale"], cell["feedback_scale"], None)
                require(len(sim["trace"]) == 43, "risk producer path length differs")
                if old_stream:
                    require(sim["summary"] == original["summary"] and specs == original["participant_specs"], "risk legacy complete summary bridge differs")
                    counts["masked_complete_paths_matched"] += 1
                for session, day in enumerate(sim["trace"]):
                    require(day["trade_date"] == dates[session] and day["signal_cutoff_date"] == joined[basket[0]][session]["signal_cutoff_date"]
                        and day["execution_reference_date"] == joined[basket[0]][session]["execution_reference_date"], "risk producer calendar differs")
                    if old_stream:
                        old_row = json.loads(next(old_stream))
                        require((old_row["seed_id"], old_row["variant"], old_row["basket_index"], old_row["portfolio_auction"]["session"])
                            == (seed["seed_id"], prior.CELLS[old_ci]["name"], bi, session), "risk legacy archive identity differs")
                        require(day == {k: v for k, v in old_row.items() if k not in {"seed_id", "variant", "basket_index"}}, "risk legacy complete daily bridge differs")
                        counts["masked_daily_records_matched"] += 1
                    for stock, call in day["portfolio_auction"]["asset_calls"].items():
                        daily.append({"stock_code": stock, "trade_date": day["trade_date"], "signal_cutoff_date": day["signal_cutoff_date"],
                            "price_before_minor": call["price_before_minor"], "price_after_minor": call["price_after_minor"],
                            "matched_volume": call["matched_volume"], "observed_return": joined[stock][session]["observed_return"]})
                        for order in call["orders"]:
                            role = "background" if specs[order["owner"]]["kind"] == "background" else specs[order["owner"]]["parameters"]["role"]
                            for label, field in (("requested", "quantity"), ("accepted", "accepted_quantity"), ("filled", "filled_quantity")):
                                flows[role + "|" + order["side"] + "|" + label] += order[field]
                            if "cash_and_fee_reservation" in order["reasons"]:
                                flows[role + "|" + order["side"] + "|cash_clipped"] += order["quantity"] - order["accepted_quantity"]
                    ledger.write(record_bytes({"seed_id": seed["seed_id"], "variant": cell["name"], "basket_index": bi, **day}))
                    counts["full_path_ledger_records"] += 1
                    counts["asset_calls"] += len(basket)
                saved_paths.append({"seed_id": seed["seed_id"], "variant": cell["name"], "basket_index": bi,
                    "participant_specs": specs, "summary": sim["summary"]})
            if old_stream:
                require(next(old_stream, None) is None, "risk legacy partition has extra records")
        finally:
            if old_stream:
                old_stream.close()
    require(counts["full_path_ledger_records"] == 1763 and counts["asset_calls"] == 5289 and len(saved_paths) == 41, "risk cell incomplete")
    variant = {"seed_id": seed["seed_id"], **cell, **metrics(daily, cfg["joint_limits"]), "daily_asset_rows": daily,
        "role_order_quantities": dict(flows), "total_matched_volume": sum(r["matched_volume"] for r in daily)}
    result = {"variant": variant, "paths": saved_paths, "checks": dict(counts)}
    (stage / "condition.json").write_bytes(record_bytes(result))

def run_seed(si):
    require(type(si) is int and 0 <= si < 5, 'public seed outside frozen grid')
    cfg, loaded = load_study()
    seed = cfg['seeds'][si]
    folder = OUTPUT / f'seed_{si}'
    folder.mkdir(parents=True, exist_ok=True)
    core, background, paths = seed_paths(loaded, seed)
    prepared = folder / 'prepared'
    if prepared.exists():
        checkpoint(prepared, [seed['seed_id'], 'input_paths'])
        require(read_gzip_json(prepared / 'issuer_paths.json.gz') == paths, 'public resumed source differs')
    else:
        stage = Path(tempfile.mkdtemp(prefix='prepare-', dir=folder))
        write_gzip_json(stage / 'issuer_paths.json.gz', paths)
        seal(stage, ['issuer_paths.json.gz'], [seed['seed_id'], 'input_paths'])
        stage.replace(prepared)
    for ci, cell in enumerate(CELLS):
        target = folder / f'cell_{ci}'
        if target.exists():
            checkpoint(target, [seed['seed_id'], cell['name']])
        else:
            stage = Path(tempfile.mkdtemp(prefix=f'pending_{ci}_', dir=folder))
            execute_cell(loaded, cfg, seed, si, cell, ci, paths, core, background, stage)
            seal(stage, ['ledger.jsonl.gz', 'condition.json'], [seed['seed_id'], cell['name']])
            stage.replace(target)
        print(f'Public information producer seed {si + 1}/5 condition {ci + 1}/16 complete.', flush=True)
    verify_numeric(loaded[8])
    record = {'status': 'COMPLETE_FULL_PUBLIC_INFORMATION_SEED', 'seed_id': seed['seed_id'],
        'protocol_sha256': file_sha256(CONFIG), 'checkpoints': {str(p.relative_to(folder)): file_sha256(p)
        for p in [prepared / 'checkpoint.json'] + [folder / f'cell_{i}/checkpoint.json' for i in range(16)]}}
    target = folder / 'production_complete.json'
    if target.exists():
        require(target.read_bytes() == record_bytes(record), 'completed public seed receipt changed')
    else:
        target.write_bytes(record_bytes(record))
    print('Public information complete seed production, masked bridges and first-basket replays passed.', flush=True)


def finish():
    cfg, loaded = load_study()
    target = OUTPUT / 'results.json'
    require(not target.exists(), 'preserve completed public information results')
    variants, counts, producer_counts, bindings, received_measurements = [], Counter(), Counter(), {}, {}
    for si, seed in enumerate(cfg['seeds']):
        folder = OUTPUT / f'seed_{si}'
        production = read(folder / 'production_complete.json')
        audit = read(folder / 'independent.json')
        require(production['status'] == 'COMPLETE_FULL_PUBLIC_INFORMATION_SEED'
            and production['protocol_sha256'] == file_sha256(CONFIG)
            and audit['status'] == 'PASS_FULL_PUBLIC_INFORMATION_SEED_RAW_LEDGER_AND_STATISTICS'
            and audit['checks'] == cfg['per_seed_audit_scope'], 'complete public production/audit differs')
        for name, expected in production['checkpoints'].items():
            require(file_sha256(folder / name) == expected, 'completed public checkpoint changed')
            bindings[str((folder / name).relative_to(ROOT))] = expected
        checkpoint(folder / 'prepared', [seed['seed_id'], 'input_paths'])
        for ci, cell in enumerate(CELLS):
            checkpoint(folder / f'cell_{ci}', [seed['seed_id'], cell['name']])
            saved = read(folder / f'cell_{ci}/condition.json')
            variants.append(saved['variant'])
            producer_counts.update(saved['checks'])
        for name in ('production_complete.json', 'independent.json'):
            bindings[str((folder / name).relative_to(ROOT))] = file_sha256(folder / name)
        counts.update(audit['checks'])
        received_measurements[seed['seed_id']] = audit['finite_received_message_measurements']
    require(dict(counts) == cfg['derived_expected_coverage'], 'full public audit scope differs')
    require(producer_counts['first_public_basket_complete_replays'] == 40, 'first public basket replay grid differs')
    result = {'status': 'COMPLETE_FULL_PUBLIC_INFORMATION_MATCHED_RECEIVED_RISK_STUDY',
        'protocol_sha256': file_sha256(CONFIG), 'market_dataset_id': loaded[3]['market_dataset_id'],
        'sample': loaded[14]['sample'], 'seeds': cfg['seeds'], 'variants': variants, 'checks': dict(counts),
        'producer_checks': dict(producer_counts), 'finite_received_message_measurements': received_measurements,
        **summary(variants, cfg['seeds']), 'artifacts': bindings, 'semantic_gate_enabled': False,
        'no_new_parameter_default': True, 'interpretation': cfg['interpretation']}
    target.write_bytes(record_bytes(result))
    print('Public information all eighty conditions and full raw independent audits finished.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int)
    parser.add_argument('--finish', action='store_true')
    args = parser.parse_args()
    if args.finish:
        require(args.seed is None, 'finish scope must be explicit')
        finish()
    else:
        run_seed(args.seed)
