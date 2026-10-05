"""Compare real source text under declared mechanisms; never replay actual stock outcomes."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from ..semantic.source_case_facts import ROOT, canonical, validate_fact_record
from .agents import AgentParameters
from .agent_decision_trace import explain_path,audit_explanations
from .portfolio_market import simulate_portfolio,initial_portfolio
from .portfolio_audit import audit_portfolio_day
from .source_case_decision_link import load_mapping,case_inputs,MODES

CONFIG=ROOT/"research/configs/source_case_decision_study_2026_v1.json"
VERSION="six-real-source-case-three-role-decision-study-v1"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_study():
    cfg=json.loads(CONFIG.read_text(encoding="utf-8"))
    if (cfg["schema_version"]!=VERSION or cfg["historical_returns_or_prices_applied"] is not False
            or cfg["independent_semantic_validation"] is not False
            or cfg["case_count"]!=6 or cfg["modes"]!=list(MODES) or cfg["seeds"]!=[7,11,23,47,89]):
        raise ValueError("complete registered development study required")
    for name,digest in cfg["bindings"].items():
        if sha(ROOT/name)!=digest:
            raise ValueError("frozen source or implementation changed: "+name)
    prepared=ROOT/cfg["prepared_case_directory"]
    cases=[json.loads(line) for line in (prepared/"cases.jsonl").read_text(encoding="utf-8").splitlines()]
    review=ROOT/cfg["review_directory"]
    notes=[json.loads(line) for line in (review/"case_reviews.jsonl").read_text(encoding="utf-8").splitlines()]
    if [c["case_id"] for c in cases]!=cfg["case_ids"] or [n["case_id"] for n in notes]!=cfg["case_ids"]:
        raise ValueError("source/review identities differ")
    records={}
    for case,note in zip(cases,notes):
        if note["ready_for_registered_development_case"] is not True or note["independent_gold"] is not False:
            raise ValueError("case review incomplete or interpretation changed")
        records[case["case_id"]]=validate_fact_record(case,note["reviewed_record"])
    mapping=load_mapping(ROOT/cfg["mapping_path"])
    mechanism=json.loads((ROOT/cfg["model_mechanism_config"]).read_text(encoding="utf-8"))
    return cfg,cases,records,mapping,mechanism


def run_case(case,record,mapping,mechanism,seed,mode):
    joined,links=case_inputs(case,record,mapping,mode)
    agents=[AgentParameters(**p) for p in mechanism["agent_parameters"]]
    core={**mechanism["core"],"seed":seed};background={**mechanism["background"],"seed":seed}
    feedback={**mechanism["feedback"],"volatility_floor":0.01}
    venue=mechanism["venue"];portfolio=mechanism["portfolio_case"]
    use_text=mode!="no_text"
    result=simulate_portfolio(joined,agents,core,background,venue,feedback,portfolio,use_text)
    _,accounts,_,_=initial_portfolio(agents,core,background,["A","B","C"],portfolio,venue)
    explanations=explain_path(result,accounts,portfolio,venue,use_text)
    audit_explanations(explanations,result)
    previous={"accounts":{n:asdict(a) for n,a in accounts.items()},
        "prices":dict.fromkeys(["A","B","C"],venue["price_start_minor"]),"fee_pool_minor":0,
        "initial_cash_minor":sum(sum(a.wallets.values()) for a in accounts.values()),
        "initial_shares":{s:sum(a.shares[s] for a in accounts.values()) for s in ["A","B","C"]}}
    for session,day in enumerate(result["trace"]):
        previous=audit_portfolio_day(day,previous,venue,session,dense=session==0)
        link=links[session]
        for asset,obs in day["observations"].items():
            should_apply=link["information_active"] and asset==link["exposed_asset"] and use_text
            expected_signal=link["mapping"]["signal"] if should_apply else 0
            expected_uncertainty=link["mapping"]["uncertainty"] if should_apply else 0
            if obs["text_signal"]!=expected_signal or obs["text_uncertainty"]!=expected_uncertainty:
                raise ValueError("source-cutoff fact mapping did not enter the recorded observation")
    for owner,account in previous["accounts"].items():
        if any(account[k]!=result["summary"]["accounts"][owner][k] for k in ("wallets","shares","sellable")):
            raise ValueError("independent final state differs")
    if previous["fee_pool_minor"]!=result["summary"]["fee_pool_minor"]:
        raise ValueError("independent fee pool differs")
    return {"case_id":case["case_id"],"case_text_sha256":case["case_text_sha256"],
        "seed":seed,"mode":mode,"data_kind":"source_text_with_synthetic_market",
        "source_links":links,"reviewed_source_facts":record,"model_result":result,
        "decision_explanations":explanations,
        "audit":{"portfolio_days":len(result["trace"]),"asset_calls":len(result["trace"])*3,
                 "decisions":len(explanations),"asset_explanations":len(explanations)*3}}


def pair(left,right):
    fields=("desired_weights","requested_orders")
    lrows={(r["session"],r["actor"]):r for r in left["decision_explanations"]}
    rrows={(r["session"],r["actor"]):r for r in right["decision_explanations"]}
    if set(lrows)!=set(rrows):
        raise ValueError("unmatched case path pair")
    result={}
    for role in ("aggressive","conservative","institutional"):
        keys=[k for k in lrows if lrows[k]["role"]==role]
        def requests(row):
            return [(a,v["action"],v["whole_lot_quantity"]) for a,v in sorted(row["requested_orders"].items())]
        def fills(row):
            return [(a,v["filled_quantity"]*(1 if v["side"]=="buy" else -1)) for a,v in sorted(row["execution"].items())]
        result[role]={"decisions":len(keys),
            "targets_changed":sum(lrows[k]["desired_weights"]!=rrows[k]["desired_weights"] for k in keys),
            "requests_changed":sum(requests(lrows[k])!=requests(rrows[k]) for k in keys),
            "actual_fills_changed":sum(fills(lrows[k])!=fills(rrows[k]) for k in keys)}
    return {"case_id":right["case_id"],"seed":right["seed"],"reference_mode":left["mode"],
        "treatment_mode":right["mode"],"role_changes":result}


def write_new(path,value):
    with path.open("xb") as stream:stream.write(canonical(value)+b"\n")


def run_study(output):
    protocol_sha=sha(CONFIG)
    cfg,cases,records,mapping,mechanism=load_study()
    output.mkdir(parents=True,exist_ok=False)
    write_new(output/"frozen_plan.json",cfg)
    counts=Counter();pairs=[];artifacts={}
    for index,case in enumerate(cases):
        for seed in cfg["seeds"]:
            runs={}
            for mode in cfg["modes"]:
                run=run_case(case,records[case["case_id"]],mapping,mechanism,seed,mode)
                name=f"case{index}_seed{seed}_{mode}.json.gz"
                with (output/name).open("xb") as stream:
                    with gzip.GzipFile(fileobj=stream,mode="wb",mtime=0,filename="") as packed:
                        packed.write(canonical(run)+b"\n")
                with gzip.open(output/name,"rt",encoding="utf-8") as stream:
                    saved=json.load(stream)
                if saved!=run:
                    raise ValueError("case path changed on disk")
                audit_explanations(saved["decision_explanations"],saved["model_result"])
                artifacts[name]=sha(output/name);runs[mode]=run
                counts.update(paths=1,portfolio_days=run["audit"]["portfolio_days"],
                    asset_calls=run["audit"]["asset_calls"],decisions=run["audit"]["decisions"],
                    asset_explanations=run["audit"]["asset_explanations"],source_links=len(run["source_links"]))
            for treatment,reference in (("keywords","no_text"),("reviewed_llm","no_text"),
                                        ("reviewed_llm","keywords"),("llm_asset_placebo","reviewed_llm")):
                pairs.append(pair(runs[reference],runs[treatment]))
        print(json.dumps({"completed_cases":index+1,"paths":counts["paths"]}),flush=True)
    load_study()
    if sha(CONFIG)!=protocol_sha or {**dict(counts),"pairs":len(pairs)}!=cfg["expected_counts"]:
        raise ValueError("complete protocol changed or scope reduced")
    summary={"status":"COMPLETE_ALL_SIX_SOURCE_CASES_120_PATHS_AND_FULL_DECISION_LINKS",
        "version":VERSION,"protocol_sha256":protocol_sha,"counts":dict(counts),"pairs":pairs,
        "historical_returns_or_prices_applied":False,"independent_semantic_validation":False,
        "source_case_model_facts_were_reviewed_and_corrected":True,"empirical_calibration":False}
    write_new(output/"summary.json",summary)
    for name in ("summary.json","frozen_plan.json"):artifacts[name]=sha(output/name)
    write_new(output/"manifest.json",{"version":VERSION,"protocol_sha256":protocol_sha,
        "artifacts":artifacts,"bindings":cfg["bindings"],"counts":dict(counts)})
    print(json.dumps({"status":summary["status"],"counts":dict(counts),"pairs":len(pairs)}),flush=True)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",type=Path,required=True)
    args=parser.parse_args();run_study(args.output_dir)


if __name__=="__main__":
    main()
