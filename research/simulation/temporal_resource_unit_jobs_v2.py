"""Actual child exits, first full seed gate, then the remaining registered grid."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime,timezone
import os
import subprocess
import sys

from .temporal_resource_unit_study_v2 import ROOT,CONFIG,OUTPUT,load_study,read,encoded,require
from ..data_pipeline.provenance import file_sha256
from ..data_pipeline.temporal_transport_sources import windows_process_identity

PREFIX="temporal_resource_unit_jobs_20261004_v2"


def now(): return datetime.now(timezone.utc).isoformat()


def job(si,phase,module,args):
    stem=PREFIX+(f"_seed_{si}_{phase}" if si is not None else f"_{phase}")
    log,receipt=(ROOT/"research_outputs"/(stem+suffix) for suffix in (".txt",".json"))
    require(not log.exists() and not receipt.exists(),"Existing resource job needs actual liveness reconciliation")
    command=[sys.executable,"-B","-X","utf8","-m",module,*args]
    with log.open("xb") as stream:
        process=subprocess.Popen(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
        actual=windows_process_identity(process.pid)
        require(actual is not None and actual["live"],"Resource child was not observed live")
        record={"status":"RUNNING_ACTUAL_RESOURCE_UNIT_CHILD","seed_index":si,"phase":phase,"command":command,
            "pid":process.pid,"creation_ticks":actual["creation_ticks"],"started_at_utc":now(),
            "observed_process_exit_code":None,"protocol_sha256":file_sha256(CONFIG),"log_path":log.relative_to(ROOT).as_posix()}
        with receipt.open("xb") as f: f.write(encoded(record))
        print(f"Resource unit {stem}: actual child PID {process.pid} started.",flush=True)
        code=process.wait()
    record.update(status="PASS_ACTUAL_RESOURCE_UNIT_CHILD" if code==0 else "FAIL_ACTUAL_RESOURCE_UNIT_CHILD",
        observed_process_exit_code=code,finished_at_utc=now(),log_sha256=file_sha256(log))
    receipt.write_bytes(encoded(record))
    print(f"Resource unit {stem}: actual child exit {code}.",flush=True)
    return {**record,"receipt_path":receipt.relative_to(ROOT).as_posix(),"receipt_sha256":file_sha256(receipt)}


def pipeline(si):
    rows=[job(si,"producer","research.simulation.run_temporal_resource_unit_study_v2",["--seed",str(si)])]
    if rows[0]["observed_process_exit_code"]==0:
        rows.append(job(si,"audit","research.simulation.audit_temporal_resource_unit_study_v2",["--seed",str(si)]))
    return rows


def main():
    cfg,*_=load_study()
    owner=ROOT/"research_outputs"/(PREFIX+"_owner.json")
    receipt=ROOT/cfg["jobs_receipt_path"]
    require(not OUTPUT.exists() and not owner.exists() and not receipt.exists(),"Preserve prior resource owner and output")
    with owner.open("xb") as f: f.write(encoded({"status":"RUNNING","owner":windows_process_identity(os.getpid()),"started_at_utc":now(),"protocol_sha256":file_sha256(CONFIG)}))
    rows=pipeline(0)
    first=len(rows)==2 and all(r["observed_process_exit_code"]==0 for r in rows)
    if first:
        require(read(OUTPUT/"seed_0/independent.json")["checks"]==cfg["per_seed_audit_scope"],"First complete 96-condition seed gate reduced")
        with ThreadPoolExecutor(max_workers=3) as pool:
            for future in as_completed([pool.submit(pipeline,si) for si in range(1,5)]): rows.extend(future.result())
    complete=len(rows)==10 and all(r["observed_process_exit_code"]==0 for r in rows)
    load_study()
    with receipt.open("xb") as f: f.write(encoded({"status":"COMPLETE_FULL_RESOURCE_UNIT_CHILD_PROCESSES" if complete else "FAILED_RESOURCE_UNIT_EXECUTION",
        "protocol_sha256":file_sha256(CONFIG),"first_complete_seed_integration_passed":first,
        "jobs":sorted(rows,key=lambda r:(r["seed_index"],r["phase"])),"max_concurrent_seed_pipelines":3}))
    if not complete: return 1
    final=[job(None,"finish","research.simulation.finish_temporal_resource_unit_study_v2",[])]
    if final[-1]["observed_process_exit_code"]==0:
        final.append(job(None,"final_audit","research.simulation.audit_temporal_resource_unit_summary_v2",[]))
    passed=len(final)==2 and all(r["observed_process_exit_code"]==0 for r in final)
    with (ROOT/"research_outputs"/(PREFIX+"_completion.json")).open("xb") as f:
        f.write(encoded({"status":"COMPLETE_FULL_RESOURCE_UNIT_FINAL_ACTUAL_PROCESSES" if passed else "FAILED_RESOURCE_UNIT_FINAL_PROCESSES",
            "protocol_sha256":file_sha256(CONFIG),"jobs_receipt_sha256":file_sha256(receipt),
            "final_jobs":final,"owner_receipt_sha256":file_sha256(owner)}))
    return 0 if passed else 1


if __name__=="__main__": raise SystemExit(main())
