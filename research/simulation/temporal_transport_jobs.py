"""Durable actual-exit evidence for the full registered transport execution."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys

from .temporal_transport_study import ROOT, CONFIG, OUTPUT, load_study, read, encoded, require
from ..data_pipeline.provenance import file_sha256
from ..data_pipeline.temporal_transport_sources import windows_process_identity

PREFIX = "temporal_transport_jobs_20261004_v1"


def now():
    return datetime.now(timezone.utc).isoformat()


def job(seed_index, phase, module, arguments):
    stem = PREFIX + (f"_seed_{seed_index}_{phase}" if seed_index is not None else f"_{phase}")
    log_path, receipt_path = (ROOT / "research_outputs" / (stem + suffix) for suffix in (".txt", ".json"))
    require(not receipt_path.exists() and not log_path.exists(), "Preserve prior process; actual liveness must be reviewed before recovery")
    command = [sys.executable, "-B", "-X", "utf8", "-m", module, *arguments]
    started = now()
    with log_path.open("xb") as log:
        process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        identity = windows_process_identity(process.pid)
        require(identity is not None, "Cannot establish actual transport child identity")
        running = {"status": "RUNNING_ACTUAL_TRANSPORT_CHILD", "seed_index": seed_index, "phase": phase,
            "command": command, "pid": process.pid, "creation_ticks": identity["creation_ticks"],
            "started_at_utc": started, "observed_process_exit_code": None,
            "protocol_sha256": file_sha256(CONFIG), "log_path": log_path.relative_to(ROOT).as_posix()}
        with receipt_path.open("xb") as stream:
            stream.write(encoded(running))
        print(f"Transport {stem}: actual child PID {process.pid} started.", flush=True)
        code = process.wait()
    record = {**running, "status": "PASS_ACTUAL_TRANSPORT_CHILD" if code == 0 else "FAIL_ACTUAL_TRANSPORT_CHILD",
        "finished_at_utc": now(), "observed_process_exit_code": code, "log_sha256": file_sha256(log_path)}
    receipt_path.write_bytes(encoded(record))
    print(f"Transport {stem}: actual child exit {code}.", flush=True)
    return {**record, "receipt_path": receipt_path.relative_to(ROOT).as_posix(), "receipt_sha256": file_sha256(receipt_path)}


def pipeline(seed_index):
    producer = job(seed_index, "producer", "research.simulation.run_temporal_transport_study", ["--seed", str(seed_index)])
    if producer["observed_process_exit_code"]:
        return [producer]
    auditor = job(seed_index, "audit", "research.simulation.audit_temporal_transport_study", ["--seed", str(seed_index)])
    return [producer, auditor]


def main():
    cfg, *_ = load_study()
    target = ROOT / cfg["jobs_receipt_path"]
    owner_path = ROOT / "research_outputs" / (PREFIX + "_owner.json")
    require(not target.exists() and not owner_path.exists(), "Preserve prior transport orchestration owner")
    require(not OUTPUT.exists(), "Preserve existing transport execution; explicit observed recovery needed")
    with owner_path.open("xb") as stream:
        stream.write(encoded({"status": "RUNNING", "owner": windows_process_identity(os.getpid()),
            "started_at_utc": now(), "protocol_sha256": file_sha256(CONFIG)}))
    # The first seed is a complete 48-condition integration check. It belongs
    # to the same preregistered 240-condition result, without duplicate reruns.
    jobs = pipeline(0)
    first_passed = len(jobs) == 2 and all(row["observed_process_exit_code"] == 0 for row in jobs)
    if first_passed:
        cfg, *_ = load_study()
        first_audit = read(OUTPUT / "seed_0/independent.json")
        require(first_audit["checks"] == cfg["per_seed_audit_scope"], "First-seed full independent integration scope differs")
        with ThreadPoolExecutor(max_workers=3) as pool:
            for future in as_completed([pool.submit(pipeline, si) for si in range(1, 5)]):
                jobs.extend(future.result())
    complete = len(jobs) == 10 and all(row["observed_process_exit_code"] == 0 for row in jobs)
    load_study()
    record = {"status": "COMPLETE_FULL_TRANSPORT_CHILD_PROCESSES" if complete else "FAILED_TRANSPORT_EXECUTION",
        "jobs": sorted(jobs, key=lambda row: (row["seed_index"], row["phase"])), "first_complete_seed_integration_passed": first_passed,
        "max_concurrent_seed_pipelines": 3, "protocol_sha256": file_sha256(CONFIG)}
    with target.open("xb") as stream:
        stream.write(encoded(record))
    if not complete:
        return 1
    final_jobs = [job(None, "finish", "research.simulation.run_temporal_transport_study", ["--finish"])]
    if final_jobs[-1]["observed_process_exit_code"] == 0:
        final_jobs.append(job(None, "final_audit", "research.simulation.audit_temporal_transport_summary", []))
    final_complete = len(final_jobs) == 2 and all(row["observed_process_exit_code"] == 0 for row in final_jobs)
    final_path = ROOT / "research_outputs" / (PREFIX + "_completion.json")
    with final_path.open("xb") as stream:
        stream.write(encoded({"status": "COMPLETE_FULL_TRANSPORT_FINAL_ACTUAL_PROCESSES" if final_complete else "FAILED_TRANSPORT_FINAL_PROCESSES",
            "jobs_receipt_sha256": file_sha256(target), "final_jobs": final_jobs, "protocol_sha256": file_sha256(CONFIG),
            "owner_receipt_sha256": file_sha256(owner_path)}))
    return 0 if final_complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
