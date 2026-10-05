"""Call the authorized model for fixed source-case facts and archive every result."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .local_credential import load_api_key
from .run_model import request_completion, endpoint_url
from .source_case_facts import ROOT, VERSION, build_cases, normalize_facts, canonical

CONFIG = ROOT / "research/configs/source_case_facts_development_2026_v1.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_plan():
    plan = json.loads(CONFIG.read_text(encoding="utf-8"))
    if (plan["schema_version"] != VERSION or plan["model_id"] != "DeepSeek-V4-Flash-0731-W8A8"
            or plan["provider_base_url"] != "http://aigw.dlut.edu.cn/v1"
            or plan["independent_semantic_validation"] is not False
            or plan["historical_economic_calibration"] is not False or plan["cases"] != 6):
        raise ValueError("fixed development case protocol required")
    for name, digest in plan["bindings"].items():
        if sha(ROOT/name) != digest:
            raise ValueError("frozen source-case input changed: "+name)
    folder = ROOT/plan["source_case_directory"]
    cases = [json.loads(line) for line in (folder/"cases.jsonl").read_text(encoding="utf-8").splitlines()]
    if cases != build_cases() or [c["case_id"] for c in cases] != plan["case_ids"]:
        raise ValueError("cases cannot be reconstructed from their original archives")
    return plan,cases


def run(output):
    protocol_sha = sha(CONFIG)
    plan,cases = load_plan()
    output.mkdir(parents=True,exist_ok=False)
    key = load_api_key()
    prompt = (ROOT/plan["prompt_path"]).read_text(encoding="utf-8")
    receipt = {"status":"RUNNING_ACTUAL_FIXED_SIX_CASE_FACT_EXTRACTION",
        "protocol_sha256":protocol_sha,"model_id":plan["model_id"],"actual_pid":os.getpid(),
        "started_at_utc":datetime.now(timezone.utc).isoformat(),"cases_completed":0,
        "request_failures":0,"parse_failures":0,"raw_response_files":[],
        "outbound_fields":plan["outbound_fields"],"independent_semantic_validation":False}
    manifest = output/"manifest.json"
    manifest.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding="utf-8")
    for index,case in enumerate(cases):
        load_plan()
        payload = {"source_kind":case["source_kind"],"segments":case["segments"]}
        messages = [{"role":"system","content":prompt},
                    {"role":"user","content":canonical(payload).decode("utf-8")}]
        raw,normalized,error = None,None,None
        try:
            raw = request_completion(endpoint_url(plan["provider_base_url"]),key,plan["model_id"],
                messages,plan["request_timeout_seconds"],plan["request_retries"],plan["temperature"])
        except RuntimeError as exc:
            # request_completion exposes only a sanitized error class, never credentials or source body.
            error = {"stage":"request","type":type(exc).__name__,"message":str(exc)}
            receipt["request_failures"] += 1
        if raw is not None:
            try:
                normalized = normalize_facts(case,raw)
            except (ValueError,TypeError,KeyError,json.JSONDecodeError) as exc:
                error = {"stage":"parse","type":type(exc).__name__,"message":str(exc)}
                receipt["parse_failures"] += 1
        record = {"case_id":case["case_id"],"case_text_sha256":case["case_text_sha256"],
            "model_id":plan["model_id"],"prompt_sha256":sha(ROOT/plan["prompt_path"]),
            "sent_segments_sha256":hashlib.sha256(canonical(payload)).hexdigest(),
            "raw_response":raw,"normalized":normalized,"error":error,
            "completed_at_utc":datetime.now(timezone.utc).isoformat()}
        name=f"case_{index}.json"
        with (output/name).open("xb") as stream:stream.write(canonical(record)+b"\n")
        receipt["cases_completed"] += 1
        receipt["raw_response_files"].append({"name":name,"sha256":sha(output/name)})
        manifest.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps({"completed_cases":index+1,"case_kind":case["source_kind"],
                          "request_failed":error is not None and error["stage"]=="request",
                          "parse_failed":error is not None and error["stage"]=="parse"}),flush=True)
    load_plan()
    if sha(CONFIG) != protocol_sha:
        raise ValueError("source case protocol changed during requests")
    receipt.update(status="COMPLETE_ACTUAL_FIXED_SIX_CASE_FACT_EXTRACTION",
                   finished_at_utc=datetime.now(timezone.utc).isoformat(),
                   all_requests_and_parses_succeeded=receipt["request_failures"]==receipt["parse_failures"]==0)
    manifest.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:receipt[k] for k in ("status","cases_completed","request_failures","parse_failures")}),flush=True)
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",type=Path,required=True)
    args=parser.parse_args()
    run(args.output_dir)


if __name__ == "__main__":
    main()
