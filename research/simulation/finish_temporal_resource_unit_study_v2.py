"""Aggregate complete independent child audits and all 720 resource conditions."""
from collections import Counter

from .temporal_resource_unit_study_v2 import (
    ROOT, CONFIG, OUTPUT, PARENT_CONFIG, PARENT_OUTPUT, PARENT_CELLS, CELLS,
    BASELINE, load_study, read, require, encoded, checkpoint, bindings_ok, summarize_resource_pairs)
from ..data_pipeline.provenance import file_sha256


def compact(row):
    return {k:v for k,v in row.items() if k != "daily_asset_rows"}


def main():
    cfg, *_ = load_study()
    result_path = OUTPUT/"results.json"
    require(not result_path.exists(), "Preserve completed resource results")
    jobs_path = ROOT/cfg["jobs_receipt_path"]
    jobs = read(jobs_path)
    require(jobs["status"]=="COMPLETE_FULL_RESOURCE_UNIT_CHILD_PROCESSES"
        and jobs["protocol_sha256"]==file_sha256(CONFIG)
        and len(jobs["jobs"])==10
        and {(j["seed_index"],j["phase"]) for j in jobs["jobs"]}=={(i,p) for i in range(5) for p in ("producer","audit")}
        and all(type(j["observed_process_exit_code"]) is int and j["observed_process_exit_code"]==0 for j in jobs["jobs"]),
        "Ten complete actual producer/auditor exits required")
    for job in jobs["jobs"]:
        require(file_sha256(ROOT/job["log_path"])==job["log_sha256"]
                and file_sha256(ROOT/job["receipt_path"])==job["receipt_sha256"],"Actual child process evidence changed")
    rows, counts, producer_counts, artifacts = [], Counter(), Counter(), {}
    for si,seed in enumerate(cfg["seeds"]):
        folder=OUTPUT/f"seed_{si}"
        production,audit=read(folder/"production_complete.json"),read(folder/"independent.json")
        require(production["status"]=="COMPLETE_FULL_RESOURCE_UNIT_SEED"
            and audit["status"]=="PASS_FULL_RESOURCE_UNIT_SEED_RAW_LEDGER_AND_STATISTICS"
            and production["protocol_sha256"]==audit["protocol_sha256"]==file_sha256(CONFIG)
            and audit["checks"]==cfg["per_seed_audit_scope"],"Resource seed function or independent scope differs")
        for name,digest in production["checkpoints"].items():
            require(file_sha256(folder/name)==digest,"New resource checkpoint changed")
            artifacts[(folder/name).relative_to(ROOT).as_posix()]=digest
        for ci,cell in enumerate(CELLS):
            checkpoint(folder/f"cell_{ci}",[seed["seed_id"],cell["name"]],CONFIG)
            saved=read(folder/f"cell_{ci}/condition.json")
            require(saved["checks"]==cfg["per_condition_producer_scope"],"New resource producer count differs")
            rows.append(compact(saved["variant"]))
            producer_counts.update(saved["checks"])
        counts.update(audit["checks"])
        for p in (folder/"production_complete.json",folder/"independent.json"):
            artifacts[p.relative_to(ROOT).as_posix()]=file_sha256(p)
        parent_folder=PARENT_OUTPUT/f"seed_{si}"
        for ci,cell in enumerate(PARENT_CELLS):
            checkpoint(parent_folder/f"cell_{ci}",[seed["seed_id"],cell["name"]],PARENT_CONFIG)
            saved=read(parent_folder/f"cell_{ci}/condition.json")
            rows.append({**compact(saved["variant"]),"resource_arm":BASELINE,"parent_cell_index":ci,
                         "parent_condition_name":cell["name"]})
    require(len(rows)==720 and counts["conditions"]==480
        and counts["full_path_ledger_records"]==1141440 and counts["asset_calls"]==3424320
        and counts["complete_wealth_drawdown_risk_accounts"]==944640,
        "Full registered candidate ledger or account scope incomplete")
    summary=summarize_resource_pairs(rows,cfg["seeds"])
    bindings_ok(cfg)
    with result_path.open("xb") as f:
        f.write(encoded({"status":"COMPLETE_FULL_720_RESOURCE_UNIT_DESCRIPTIVE_STUDY",
            "protocol_sha256":file_sha256(CONFIG),"jobs_receipt_sha256":file_sha256(jobs_path),
            "unique_conditions":720,"newly_simulated_conditions":480,"reused_original_conditions":240,
            "independent_checks":dict(counts),"producer_checks":dict(producer_counts),
            "variants":rows,"summary":summary,"artifacts":artifacts,
            "full_daily_rows_retained_in_every_condition_and_raw_ledger":True,
            "new_period_validation_or_empirical_calibration":False,"default_selected":False,"goal_complete":False}))
    print("Complete 720 resource conditions aggregated; independent final audit is still required.",flush=True)


if __name__=="__main__": main()
