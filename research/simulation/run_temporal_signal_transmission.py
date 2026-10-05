"""Produce all 160 complete archived-ledger transmission conditions."""
import argparse
from collections import Counter
import gzip
from pathlib import Path
import tempfile

from .temporal_transmission_study import ROOT, CONFIG, OUTPUT, CELLS, load, encoded, read, require, conditions, process_condition
from ..data_pipeline.provenance import file_sha256


def seed(si):
    protocol, cfg, mask = load()
    require(type(si) is int and 0 <= si < 5, "explicit transmission seed required")
    target = OUTPUT / f"seed_{si}"
    require(not target.exists(), "preserve completed transmission seed")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f"pending_seed_{si}_", dir=OUTPUT))
    prepared, counts, summaries = conditions(si, cfg), Counter(), []
    with (stage / "positions.jsonl.gz").open("xb") as raw, gzip.GzipFile(fileobj=raw, filename="", mode="wb", mtime=0) as archive:
        for arm in ("baseline", "candidate"):
            for ci in range(16):
                summary = process_condition(si, arm, ci, cfg, mask, prepared, lambda r: archive.write(encoded(r)))
                counts.update(summary["checks"])
                summaries.append(summary)
                print(f"Transmission seed {si+1}/5 {arm} condition {ci+1}/16 complete.", flush=True)
    for field, expected in protocol["per_seed_fixed_scope"].items():
        require(counts[field] == expected, "complete transmission seed coverage differs: " + field)
    (stage / "summary.json").write_bytes(encoded({"status": "COMPLETE_FULL_SIGNAL_TRANSMISSION_SEED",
        "protocol_sha256": file_sha256(CONFIG), "seed_id": cfg["seeds"][si]["seed_id"], "conditions": summaries, "checks": dict(counts)}))
    (stage / "checkpoint.json").write_bytes(encoded({"protocol_sha256": file_sha256(CONFIG),
        "artifacts": {name: file_sha256(stage / name) for name in ("positions.jsonl.gz", "summary.json")}}))
    load()
    stage.replace(target)


def finish():
    protocol, cfg, mask = load()
    target = OUTPUT / "results.json"
    require(not target.exists(), "preserve final transmission study")
    jobs = read(ROOT / protocol["jobs_receipt_path"])
    require(jobs["status"] == "COMPLETE_FULL_SIGNAL_TRANSMISSION_CHILD_PROCESSES"
        and jobs["protocol_sha256"] == file_sha256(CONFIG) and len(jobs["jobs"]) == 10
        and all(r["observed_process_exit_code"] == 0 for r in jobs["jobs"])
        and {(r["seed_index"],r["phase"]) for r in jobs["jobs"]} == {(i,p) for i in range(5) for p in ("producer","audit")}, "all ten real exits required")
    all_conditions, counts, artifacts = [], Counter(), {}
    for si in range(5):
        folder = OUTPUT / f"seed_{si}"
        checkpoint = read(folder / "checkpoint.json")
        require(checkpoint["protocol_sha256"] == file_sha256(CONFIG), "transmission checkpoint changed")
        for name, digest in checkpoint["artifacts"].items():
            require(file_sha256(folder / name) == digest, "transmission artifact changed")
        saved, audit = read(folder / "summary.json"), read(folder / "independent.json")
        require(saved["status"] == "COMPLETE_FULL_SIGNAL_TRANSMISSION_SEED"
            and audit["status"] == "PASS_FULL_SIGNAL_TRANSMISSION_SEED"
            and audit["protocol_sha256"] == file_sha256(CONFIG) and audit["checks"] == saved["checks"], "full independent transmission audit required")
        all_conditions.extend(saved["conditions"])
        counts.update(saved["checks"])
        for name in ("checkpoint.json","independent.json"):
            p=folder/name
            artifacts[str(p.relative_to(ROOT)).replace("\\","/")]=file_sha256(p)
    require(len(all_conditions) == len({(c['arm'],c['seed_id'],c['name']) for c in all_conditions}) == 160, "all original and new conditions required")
    for field, expected in protocol["full_fixed_scope"].items():
        require(counts[field] == expected, "complete transmission aggregate differs")
    target.write_bytes(encoded({"status":"COMPLETE_FULL_TEMPORAL_SIGNAL_TRANSMISSION_2021_V1",
        "protocol_sha256":file_sha256(CONFIG),"checks":dict(counts),"conditions":all_conditions,"artifacts":artifacts,
        "stages":protocol["stages"],"evaluation_mask_metadata":{"unknown_targets":len(mask['unknown_positions']),
            "common_dates":len(mask['full_cohort_portfolio_common_dates']),"fixed_primary_pairs":sum(p['primary_eligible'] for p in mask['correlation_pairs'])},
        "is_full_path_counterfactual":False,"new_default_selected":False,"goal_complete":False,"interpretation":protocol['interpretation']}))
    print("All 160 full transmission conditions and independent audits aggregated.",flush=True)


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed",type=int)
    parser.add_argument("--finish",action="store_true")
    args=parser.parse_args()
    finish() if args.finish else seed(args.seed)
