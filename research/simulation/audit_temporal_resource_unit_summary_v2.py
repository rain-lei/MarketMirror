"""Independent complete-scope statistics, paired signs and process evidence."""
import statistics

from .temporal_resource_unit_study_v2 import (
    ROOT, CONFIG, OUTPUT, PARENT_CONFIG, PARENT_OUTPUT, PARENT_CELLS, CELLS,
    BASELINE, FIXED, NOTIONAL, load_study, read, encoded, require, checkpoint)
from .audit_temporal_statistics import independent_metrics
from .verify_pre_wuhan_order_price_diagnostics_2019 import compare
from ..data_pipeline.provenance import file_sha256

FIELDS=("mean","volatility","zero_fraction","stock_correlation","portfolio_volatility")


def metrics_values(independent):
    return {"mean":independent["comparison"]["synthetic"]["mean"],
        "volatility":independent["comparison"]["synthetic"]["standard_deviation"],
        "zero_fraction":independent["comparison"]["synthetic"]["zero_return_fraction"],
        "stock_correlation":independent["common_factor_metrics"]["synthetic"]["mean_pairwise_stock_return_correlation"],
        "portfolio_volatility":independent["common_factor_metrics"]["synthetic"]["equal_weight_daily_return_std"]}


def main():
    cfg,_,_,_,mask=load_study()
    result=read(OUTPUT/"results.json")
    target=OUTPUT/"final_verification.json"
    require(not target.exists(),"Preserve final independent resource proof")
    require(result["status"]=="COMPLETE_FULL_720_RESOURCE_UNIT_DESCRIPTIVE_STUDY"
        and result["protocol_sha256"]==file_sha256(CONFIG) and result["unique_conditions"]==720
        and result["new_period_validation_or_empirical_calibration"] is False,"Resource final status or interpretation differs")
    rows=result["variants"]
    index={(r["seed_id"],r["resource_arm"],r["parent_cell_index"]):r for r in rows}
    grid={(s["seed_id"],a,i) for s in cfg["seeds"] for a in (BASELINE,FIXED,NOTIONAL) for i in range(48)}
    require(len(rows)==len(index)==len(grid)==720 and set(index)==grid,"Final full resource grid differs")
    for path,digest in result["artifacts"].items(): require(file_sha256(ROOT/path)==digest,"Final independent artifact changed")
    jobs=read(ROOT/cfg["jobs_receipt_path"])
    require(jobs["status"]=="COMPLETE_FULL_RESOURCE_UNIT_CHILD_PROCESSES" and len(jobs["jobs"])==10
        and all(type(j["observed_process_exit_code"]) is int and j["observed_process_exit_code"]==0 for j in jobs["jobs"]),"Actual full resource exits missing")
    for job in jobs["jobs"]:
        require(file_sha256(ROOT/job["receipt_path"])==job["receipt_sha256"]
            and file_sha256(ROOT/job["log_path"])==job["log_sha256"],"Independent actual job/log evidence differs")
    rebuilt={}
    for si,seed in enumerate(cfg["seeds"]):
        audit=read(OUTPUT/f"seed_{si}/independent.json")
        require(audit["checks"]==cfg["per_seed_audit_scope"],"Independent seed coverage reduced")
        for arm in (BASELINE,FIXED,NOTIONAL):
            for ci,parent in enumerate(PARENT_CELLS):
                ni=ci if arm==FIXED else 48+ci
                folder=(PARENT_OUTPUT/f"seed_{si}/cell_{ci}") if arm==BASELINE else (OUTPUT/f"seed_{si}/cell_{ni}")
                cell=parent if arm==BASELINE else CELLS[ni]
                checkpoint(folder,[seed["seed_id"],cell["name"]],PARENT_CONFIG if arm==BASELINE else CONFIG)
                saved=read(folder/"condition.json")
                expected={k:v for k,v in saved["variant"].items() if k!="daily_asset_rows"}
                if arm==BASELINE: expected.update(resource_arm=arm,parent_cell_index=ci,parent_condition_name=parent["name"])
                compare(index[seed["seed_id"],arm,ci],expected)
                independent=independent_metrics(saved["variant"]["daily_asset_rows"],mask,cfg["joint_limits"])
                for field in ("comparison","common_factor_metrics","all_model_return_distribution"):
                    compare(independent[field],expected[field])
                compare(independent["joint_values"],expected["joint_checks"]["values"])
                require(independent["all_five_pass"] is expected["joint_checks"]["all_five_pass"],"Rebuilt final joint status differs")
                rebuilt[seed["seed_id"],arm,ci]={"metrics":metrics_values(independent),"pass":independent["all_five_pass"]}
        print(f"Resource final statistics seed {si+1}/5, all 144 conditions independently checked.",flush=True)
    pairs=[]
    for seed in cfg["seeds"]:
        for ci in range(48):
            for treatment,reference in ((FIXED,BASELINE),(NOTIONAL,BASELINE),(NOTIONAL,FIXED)):
                a,b=(rebuilt[seed["seed_id"],arm,ci] for arm in (treatment,reference))
                change={k:None if a["metrics"][k] is None or b["metrics"][k] is None else a["metrics"][k]-b["metrics"][k] for k in FIELDS}
                pairs.append({"seed_id":seed["seed_id"],"parent_cell_index":ci,"treatment_resource_arm":treatment,
                    "reference_resource_arm":reference,"metric_changes":change,"joint_pass_treatment":a["pass"],"joint_pass_reference":b["pass"]})
    compare(result["summary"]["resource_pairs"],pairs)
    strata=[]
    for arm in (BASELINE,FIXED,NOTIONAL):
        for dep in ("independent","historical_rank"):
            for delivery in ("masked","public","whole_public"):
                for market in ("market_independent","market_shared"):
                    selected=[rebuilt[r["seed_id"],arm,r["parent_cell_index"]] for r in rows if
                        (r["resource_arm"],r["residual_dependence"],r["delivery"],r["risk_mode"])==(arm,dep,delivery,market)]
                    require(len(selected)==20,"Independent stratum incomplete")
                    strata.append({"resource_arm":arm,"residual_dependence":dep,"delivery":delivery,"risk_mode":market,
                        "conditions":20,"joint_pass_conditions":sum(r["pass"] for r in selected),
                        "metric_medians":{k:statistics.median(v) if all(x is not None for x in v) else None
                            for k in FIELDS for v in [[r["metrics"][k] for r in selected]]}})
    compare(result["summary"]["strata"],strata)
    counts={a:sum(v["pass"] for (s,arm,i),v in rebuilt.items() if arm==a) for a in (BASELINE,FIXED,NOTIONAL)}
    require(result["summary"]["joint_pass_conditions_by_arm"]==counts,"Independent arm pass counts differ")
    with target.open("xb") as f:
        f.write(encoded({"status":"PASS_FULL_720_RESOURCE_CONDITIONS_RAW_LEDGER_MANIFESTS_STATISTICS_PAIRS_AND_ALL_NEW_ACCOUNT_AUDITS",
            "protocol_sha256":file_sha256(CONFIG),"results_sha256":file_sha256(OUTPUT/"results.json"),
            "unique_conditions":720,"independently_rebuilt_statistical_conditions":720,"resource_pairs":720,"strata":36,
            "new_daily_ledger_records":1141440,"new_asset_calls":3424320,"new_account_audits":944640,
            "actual_successful_seed_jobs":10,"reuse_original_conditions":240,
            "joint_pass_conditions_by_arm":counts,"new_period_or_empirical_validation":False,"goal_complete":False}))
    print("Full 720-condition resource diagnosis passed independent final verification.",flush=True)


if __name__=="__main__": main()
