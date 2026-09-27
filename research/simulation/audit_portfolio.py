"""Verify source-aware portfolio paths, independent wallets and risk metrics."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import statistics
from collections import Counter
from dataclasses import asdict
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .agents import AgentParameters
from .audit_feedback import check_feedback
from .audit_background import verify_background_demands
from .portfolio_audit import audit_portfolio_day
from .portfolio_experiment import load_summary, experiment_inputs
from .portfolio_market import initial_portfolio, portfolio_decision

VERSION = "source-aware-portfolio-audit-v2"


def independent_covariance(histories, cutoff, settings):
    window = settings["volatility_sessions"]
    values = {a: ([0.0] * window + [r["price_after_minor"] / r["price_before_minor"] - 1
              for r in rows if r["trade_date"] <= cutoff])[-window:] for a, rows in histories.items()}
    return {a: {b: max(settings["volatility_floor"] ** 2, statistics.variance(values[a])) if a == b
                else 0.75 * statistics.covariance(values[a], values[b]) for b in values} for a in values}


def verify_grouped_results(summary, results, paths, cases, responses, baskets, start_price):
    if results["grouped"] != summary["grouped"]:
        raise ValueError("portfolio result and summary groups differ")
    grouped = {(g["case_id"], g["quote_response_bps"]): g for g in summary["grouped"]}
    expected = {(case["case_id"], response) for case in cases for response in responses}
    if len(grouped) != len(summary["grouped"]) or set(grouped) != expected:
        raise ValueError("portfolio group coverage differs")
    for case in cases:
        for response in responses:
            group = grouped[case["case_id"], response]
            if (group["cash_mode"] != case["cash_mode"]
                    or group["institutional_asset_cap"] != case["institutional_asset_cap"]):
                raise ValueError("portfolio group case metadata differs")
            means = {}
            for flag, prefix in ((False, "no_text"), (True, "text")):
                selected = [p for p in paths.values() if p["case_id"] == case["case_id"]
                            and p["quote_response_bps"] == response and p["use_text"] is flag]
                if len(selected) != len(baskets):
                    raise ValueError("portfolio group path count differs")
                requested = sum(p["strategy_requested"] for p in selected)
                accepted = sum(p["strategy_accepted"] for p in selected)
                filled = sum(p["strategy_filled"] for p in selected)
                exact = {prefix + "_strategy_requested": requested,
                         prefix + "_strategy_accepted": accepted,
                         prefix + "_strategy_filled": filled,
                         prefix + "_cash_clipped_orders": sum(p["cash_clipped_orders"] for p in selected),
                         prefix + "_risk_breach_sessions": sum(a["risk_breach_sessions"] for p in selected for a in p["accounts"].values()),
                         prefix + "_concentration_breach_sessions": sum(a["concentration_breach_sessions"] for p in selected for a in p["accounts"].values())}
                approximate = {prefix + "_strategy_fill_fraction": filled / accepted if accepted else 0.0,
                               prefix + "_strategy_requested_fill_fraction": filled / requested if requested else 0.0,
                               prefix + "_mean_final_price_multiple": statistics.mean(
                                   price / start_price for p in selected for price in p["final_prices_minor"].values())}
                approximate[prefix + "_role_wealth_multiple"] = {
                    role: statistics.mean(p["role_wealth_multiple"][role] for p in selected)
                    for role in ("aggressive", "conservative", "institutional")}
                if any(group.get(k) != v for k, v in exact.items()):
                    raise ValueError("portfolio group integer totals differ")
                for k, v in approximate.items():
                    if isinstance(v, dict):
                        if set(group[k]) != set(v) or any(not math.isclose(group[k][r], value, rel_tol=1e-12, abs_tol=1e-12)
                                                           for r, value in v.items()):
                            raise ValueError("portfolio group role wealth differs")
                    elif not math.isclose(group[k], v, rel_tol=1e-12, abs_tol=1e-12):
                        raise ValueError("portfolio group rate or price differs")
                means[prefix] = approximate[prefix + "_mean_final_price_multiple"]
            if not math.isclose(group["mean_text_price_difference_multiple"], means["text"] - means["no_text"],
                                rel_tol=1e-12, abs_tol=1e-12):
                raise ValueError("portfolio group text price difference differs")


def audit_directory(directory, config, output_dir, progress=None):
    directory, config, output_dir = [p.resolve() for p in (directory, config, output_dir)]
    summary = load_summary(directory)
    manifest_path = directory / "portfolio_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cfg, parent, auction, base, _, _, inputs, joined, core, background, baskets = experiment_inputs(config)
    if manifest["inputs"] != inputs or summary["run_id"] != cfg["run_id"]:
        raise ValueError("portfolio audit sources differ")
    if (output_dir == directory or output_dir in directory.parents or directory in output_dir.parents
            or any(output_dir == Path(p) or output_dir in Path(p).parents for p in inputs)
            or output_dir.exists() and any(output_dir.iterdir())):
        raise ValueError("portfolio audit output must be a new separate directory")
    bindings = {p:file_sha256(p) for p in (manifest_path, config, Path(__file__), Path(__file__).with_name("portfolio_audit.py"))}
    results = json.loads((directory / "portfolio_results.json").read_text(encoding="utf-8"))
    if any(results[k] != summary[k] for k in ("experiment_id", "run_id", "stocks", "baskets", "paths", "ledger_rows", "asset_call_rows", "cases", "quote_response_bps")):
        raise ValueError("portfolio result and summary identity differs")
    paths = {p["path_id"]:p for p in results["path_summaries"]}
    expected = {f"{i}:{c['case_id']}:{r}:{int(t)}" for i in range(len(baskets)) for c in cfg["cases"] for r in cfg["quote_response_bps"] for t in (False,True)}
    if set(paths) != expected or len(paths) != len(results["path_summaries"]) or summary["paths"] != len(expected) or summary["baskets"] != baskets:
        raise ValueError("portfolio path or basket coverage differs")
    cases = {c["case_id"]:c for c in cfg["cases"]}
    agents = [AgentParameters(**a) for a in base["agents"]]
    states, histories, policy_states, cohorts, specs, wealth, counts, hashers, totals = {}, {}, {}, {}, {}, {}, Counter(), {}, {}
    rows, calls, dense_calls, dense_traded, traded, halted = 0, 0, 0, 0, 0, 0
    with gzip.open(directory / "portfolio_ledger.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            key = record.pop("path_id")
            if key not in paths:
                raise ValueError("undeclared portfolio path")
            path = paths[key]
            case = cases[path["case_id"]]
            assets = baskets[path["basket_index"]]
            if (key != f"{path['basket_index']}:{path['case_id']}:{path['quote_response_bps']}:{int(path['use_text'])}"
                    or path["quote_response_bps"] not in cfg["quote_response_bps"] or path["assets"] != assets
                    or path["cash_mode"] != case["cash_mode"] or type(path["use_text"]) is not bool):
                raise ValueError("portfolio path assets or funding mode differs")
            venue = {**auction["venue"], "quote_response_bps":path["quote_response_bps"]}
            if key not in states:
                cohort, accounts, specs[key], wealth[key] = initial_portfolio(agents,core,background,assets,case,venue)
                cohorts[key] = cohort
                if results["participant_specs"][key] != specs[key]:
                    raise ValueError("portfolio participant specification differs")
                states[key] = {"accounts":{n:asdict(a) for n,a in accounts.items()}, "prices":{a:venue["price_start_minor"] for a in assets},
                               "fee_pool_minor":0, "initial_cash_minor":sum(sum(a.wallets.values()) for a in accounts.values()),
                               "initial_shares":{s:sum(a.shares[s] for a in accounts.values()) for s in assets}}
                histories[key] = {a:[] for a in assets}
                policy_states[key] = {a.name:dict(sign=0,streak=0) for a,_ in cohort}
                hashers[key] = hashlib.sha256(b"[")
                totals[key] = {n:dict(peak=w,max_drawdown=0.0,risk_breach_sessions=0,concentration_breach_sessions=0) for n,w in wealth[key].items()}
                totals[key]["orders"] = Counter()
            session = counts[key]
            if session >= len(joined[assets[0]]):
                raise ValueError("portfolio path has excessive sessions")
            source = joined[assets[0]][session]
            if any(record[f] != source[f] for f in ("trade_date","signal_cutoff_date","execution_reference_date")) or set(record["observations"]) != set(assets):
                raise ValueError("portfolio calendar or observation coverage differs")
            cov = independent_covariance(histories[key], record["signal_cutoff_date"], parent["feedback_parameters"])
            if (set(record["covariance"]) != set(assets) or any(set(record["covariance"][a]) != set(assets)
                    or any(not math.isclose(record["covariance"][a][b], cov[a][b], rel_tol=1e-12, abs_tol=1e-12) for b in assets) for a in assets)):
                raise ValueError("portfolio covariance differs from prior own-price returns")
            order = sorted(specs[key],key=lambda n:(hashlib.sha256(f"{core['seed']}:{session}:{n}".encode()).hexdigest(),n)) if core["order_mode"] == "hashed" else list(specs[key])
            if core["order_mode"] == "reverse":order.reverse()
            if record["arrival_order"] != order:
                raise ValueError("portfolio arrival priority differs")
            for asset in assets:
                observation = record["observations"][asset]
                check_feedback({"signal_cutoff_date":record["signal_cutoff_date"],"feedback":observation},histories[key][asset],parent["feedback_parameters"])
                s = joined[asset][session]
                text, uncertainty = (s["text_signal"],s["text_uncertainty"]) if path["use_text"] else (0.0,0.0)
                evidence = s["text_evidence"] if path["use_text"] else "text:disabled"
                call = record["portfolio_auction"]["asset_calls"][asset]
                if (observation["text_signal"] != text or observation["text_uncertainty"] != uncertainty or observation["text_evidence"] != evidence
                        or call["execution_available"] != s["execution_available"]
                        or any(o["sequence"] != order.index(o["owner"]) or o["order_id"] != f"{session}:{asset}:{o['owner']}" for o in call["orders"])):
                    raise ValueError("portfolio text, halt or order identity differs")
                names = {n:sp for n,sp in specs[key].items() if sp["kind"] == "strategy" or sp["asset"] == asset}
                flat = {"price_minor":states[key]["prices"][asset], "accounts":{n:{"shares":a["shares"][asset]} for n,a in states[key]["accounts"].items()}}
                verify_background_demands({"auction":call,"background_demands":record["background_demands"][asset]},flat,background,asset,session,venue,names)
            if set(record["decisions"]) != set(policy_states[key]):
                raise ValueError("portfolio strategy decisions incomplete")
            for agent, profile in cohorts[key]:
                from .portfolio_auction import PortfolioAccount
                account = PortfolioAccount(**states[key]["accounts"][agent.name])
                expected_decision = portfolio_decision(agent,profile,account,states[key]["prices"],record["observations"],record["covariance"],case,
                                                       policy_states[key][agent.name],session,path["use_text"])
                if record["decisions"][agent.name] != expected_decision:
                    raise ValueError("portfolio decision rule reexecution differs")
                d = record["decisions"][agent.name]
                risk = math.sqrt(max(0.0,sum(d["desired_weights"][a]*cov[a][b]*d["desired_weights"][b] for a in assets for b in assets)))
                if (risk > agent.risk_budget + 1e-9 or sum(d["desired_weights"].values()) > agent.max_weight + 1e-9
                        or any(w > d["asset_weight_cap"] + 1e-9 for w in d["desired_weights"].values())
                        or not d["risk_liquidation"] and sum(abs(v) for v in d["order_weight_changes"].values()) > agent.max_turnover + 1e-9):
                    raise ValueError("portfolio target violates risk, concentration or turnover")
                for asset in assets:
                    delta = d["order_weight_changes"][asset]
                    quantity = math.floor(abs(delta)*d["nav_minor"]/(states[key]["prices"][asset]*venue["lot_size"]))*venue["lot_size"]
                    if delta < 0:quantity=min(quantity,account.shares[asset])
                    actual=[o for o in record["portfolio_auction"]["asset_calls"][asset]["orders"] if o["owner"]==agent.name]
                    if not quantity:
                        if actual:raise ValueError("holding portfolio strategy submitted order")
                        continue
                    side="buy" if delta>0 else "sell"
                    price=Decimal(states[key]["prices"][asset])*(1+Decimal(str(d["beliefs"][asset]))*venue["quote_response_bps"]/10000
                          +Decimal((-1 if side=="buy" else 1)*venue["quote_spread_bps"])/20000)
                    quote=int((price/venue["tick_minor"]).to_integral_value(rounding=ROUND_FLOOR if side=="buy" else ROUND_CEILING))*venue["tick_minor"]
                    bounds=record["portfolio_auction"]["asset_calls"][asset]["price_bounds_minor"]
                    quote=max(bounds[0],min(bounds[1],quote))
                    if len(actual)!=1 or any(actual[0][f]!=v for f,v in (("quantity",quantity),("side",side),("limit_price_minor",quote))):
                        raise ValueError("portfolio strategy order differs from decision")
            states[key] = audit_portfolio_day(record,states[key],venue,session,dense=session%31==0)
            dense_calls += len(assets) if session%31==0 else 0
            for asset in assets:
                call=record["portfolio_auction"]["asset_calls"][asset]
                dense_traded += int(session%31==0 and call["matched_volume"]>0)
                histories[key][asset].append({"trade_date":record["trade_date"],"price_before_minor":call["price_before_minor"],"price_after_minor":call["price_after_minor"]})
                traded += int(call["matched_volume"]>0);halted+=int(not call["execution_available"]);calls+=1
                for o in call["orders"]:
                    if specs[key][o["owner"]]["kind"]=="strategy":
                        for f in ("quantity","accepted_quantity","filled_quantity"):totals[key]["orders"][f]+=o[f]
                        totals[key]["orders"]["cash_clipped_orders"]+=int("cash_and_fee_reservation" in o["reasons"])
            for n,a in states[key]["accounts"].items():
                value=sum(a["wallets"].values())+sum(a["shares"][s]*states[key]["prices"][s] for s in assets)
                t=totals[key][n];t["peak"]=max(t["peak"],value);t["max_drawdown"]=max(t["max_drawdown"],1-value/t["peak"])
                if specs[key][n]["kind"]=="strategy":
                    weights={s:a["shares"][s]*states[key]["prices"][s]/value for s in assets}
                    risk=math.sqrt(max(0.0,sum(weights[s]*cov[s][b]*weights[b] for s in assets for b in assets)))
                    p=specs[key][n]["parameters"]
                    t["risk_breach_sessions"]+=int(risk>p["risk_budget"]+1e-9 or sum(weights.values())>p["max_weight"]+1e-9)
                    t["concentration_breach_sessions"]+=int(any(w>record["decisions"][n]["asset_weight_cap"]+1e-9 for w in weights.values()))
            if session:hashers[key].update(b", ")
            hashers[key].update(json.dumps(record,sort_keys=True,ensure_ascii=False,allow_nan=False).encode())
            counts[key]+=1;rows+=1
            if progress and rows%5000==0:progress(rows,summary["ledger_rows"])
    if set(states)!=expected or rows!=summary["ledger_rows"] or calls!=summary["asset_call_rows"]:
        raise ValueError("portfolio ledger coverage incomplete")
    for key,state in states.items():
        p=paths[key];hashers[key].update(b"]")
        if (counts[key]!=len(joined[p["assets"][0]]) or p["sessions"]!=counts[key] or p["trace_sha256"]!=hashers[key].hexdigest()
                or p["final_prices_minor"]!=state["prices"] or p["fee_pool_minor"]!=state["fee_pool_minor"]):
            raise ValueError("portfolio final state or trace differs")
        requested,accepted,filled=[totals[key]["orders"][f] for f in ("quantity","accepted_quantity","filled_quantity")]
        if (p["strategy_requested"]!=requested or p["strategy_accepted"]!=accepted or p["strategy_filled"]!=filled
                or p["cash_clipped_orders"]!=totals[key]["orders"]["cash_clipped_orders"]
                or p["strategy_fill_fraction"]!=(filled/accepted if accepted else 0.0)
                or p["strategy_requested_fill_fraction"]!=(filled/requested if requested else 0.0)):
            raise ValueError("portfolio fill totals differ")
        for n,a in state["accounts"].items():
            saved=p["accounts"][n]
            value=sum(a["wallets"].values())+sum(a["shares"][s]*state["prices"][s] for s in p["assets"])
            if (any(saved[f]!=v for f,v in a.items()) or saved["initial_wealth_minor"]!=wealth[key][n] or saved["final_wealth_minor"]!=value
                    or saved["wealth_multiple"]!=value/wealth[key][n] or any(saved[f]!=totals[key][n][f] for f in ("max_drawdown","risk_breach_sessions","concentration_breach_sessions"))):
                raise ValueError("portfolio account metrics differ")
        for role in ("aggressive","conservative","institutional"):
            selected=[a for a in p["accounts"].values() if a["role"]==role]
            if p["role_wealth_multiple"][role]!=sum(a["final_wealth_minor"] for a in selected)/sum(a["initial_wealth_minor"] for a in selected):
                raise ValueError("portfolio role wealth differs")
    verify_grouped_results(summary, results, paths, cfg["cases"], cfg["quote_response_bps"], baskets,
                           auction["venue"]["price_start_minor"])
    if (any(file_sha256(p)!=h for p,h in bindings.items()) or any(file_sha256(Path(p))!=h for p,h in inputs.items())
            or any(file_sha256(directory/n)!=d["sha256"] for n,d in manifest["artifacts"].items())):
        raise ValueError("portfolio audit source, code or artifact changed")
    audit={"pipeline_version":VERSION,"status":"passed","experiment_id":summary["experiment_id"],"paths":len(states),"ledger_rows":rows,
           "asset_call_rows":calls,"traded_asset_calls":traded,"halted_asset_calls":halted,"sampled_dense_asset_calls":dense_calls,
           "sampled_traded_tick_sweeps":dense_traded,
           "checks":{"shared_wallets":True,"bilateral_settlement":True,"causal_covariance":True,"target_constraints":True,
                     "decision_rule_reexecution":True,"background_demands":True,"sources_and_halts":True,
                     "final_accounts_and_traces":True,"grouped_results":True}}
    output_dir.mkdir(parents=True,exist_ok=True)
    target=output_dir/"portfolio_audit.json";target.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
    binding={"pipeline_version":VERSION,"run_manifest":str(manifest_path),"run_manifest_sha256":file_sha256(manifest_path),
             "config_path":str(config),"config_sha256":file_sha256(config),"code_sha256":file_sha256(Path(__file__)),
             "wallet_audit_sha256":file_sha256(Path(__file__).with_name("portfolio_audit.py")),"result_sha256":file_sha256(target)}
    (output_dir/"portfolio_audit_manifest.json").write_text(json.dumps(binding,ensure_ascii=False,indent=2),encoding="utf-8")
    return audit


def load_audit(directory,audit_dir):
    m=json.loads((audit_dir/"portfolio_audit_manifest.json").read_text(encoding="utf-8"))
    if (m["pipeline_version"]!=VERSION or Path(m["run_manifest"]).resolve()!=(directory/"portfolio_manifest.json").resolve()
            or m["run_manifest_sha256"]!=file_sha256(directory/"portfolio_manifest.json") or m["config_sha256"]!=file_sha256(Path(m["config_path"]))
            or m["code_sha256"]!=file_sha256(Path(__file__)) or m["wallet_audit_sha256"]!=file_sha256(Path(__file__).with_name("portfolio_audit.py"))
            or m["result_sha256"]!=file_sha256(audit_dir/"portfolio_audit.json")):
        raise ValueError("portfolio audit binding changed")
    result=json.loads((audit_dir/"portfolio_audit.json").read_text(encoding="utf-8"))
    if result["status"]!="passed" or not result["checks"] or any(v is not True for v in result["checks"].values()):
        raise ValueError("portfolio audit did not pass")
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory",type=Path);parser.add_argument("--config",type=Path,required=True);parser.add_argument("--output-dir",type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(audit_directory(args.directory,args.config,args.output_dir,lambda n,t:print(f"Audited {n}/{t} portfolio days",flush=True)),ensure_ascii=False))


if __name__=="__main__":main()
