"""Resume complete public-factor conditions, then deterministically merge all artifacts."""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import ExitStack
import gzip
import json
from pathlib import Path
import shutil
import tempfile

from ..data_pipeline.provenance import file_sha256
from .public_factor_numeric_inputs_v3 import ROOT, CONFIG_V3, load_inputs, verify_numeric
from .public_factor_condition_worker_v3 import execute_cell
from .run_pre_wuhan_public_factor_channels_2019 import (CONFIG, SOURCE, OUTPUT, VERSION, CELLS, parameters_for,
    build_features, build_path, verify_panel, record_bytes, summarize, expected_coverage)

CHECKPOINTS = ROOT / "research_outputs/pre_wuhan_public_factor_checkpoints_v3"
REBUILD = ROOT / "research_outputs/pre_wuhan_public_factor_checkpoints_rebuild_v3"


def manifest(folder, names, identity):
    value = {"identity": identity, "execution_config_sha256": file_sha256(CONFIG_V3),
             "artifacts": {name: file_sha256(folder / name) for name in names}}
    (folder / "checkpoint.json").write_bytes(record_bytes(value))
    return value


def verify_checkpoint(folder, identity):
    saved = json.loads((folder / "checkpoint.json").read_text(encoding="utf-8"))
    if saved["identity"] != identity or saved["execution_config_sha256"] != file_sha256(CONFIG_V3):
        raise ValueError("checkpoint scope/config differs")
    if {p.name for p in folder.iterdir()} != set(saved["artifacts"]) | {"checkpoint.json"}:
        raise ValueError("checkpoint contains undeclared/missing files")
    for name, expected in saved["artifacts"].items():
        if Path(name).name != name or file_sha256(folder / name) != expected:
            raise ValueError("checkpoint artifact changed: " + name)
    return saved


def gzip_writer(stack, path):
    raw = stack.enter_context(path.open("wb"))
    return stack.enter_context(gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0))


def old_name(cell):
    return cell["background_anchor"] + "_feedback_" + ("full" if cell["feedback_scale"] else "both_off")


def prepare(root, loaded):
    folder = root / "prepared"
    if folder.exists():
        verify_checkpoint(folder, "prepared_sources")
        return json.loads((folder / "preparation.json").read_text(encoding="utf-8"))
    cfg, _, _, _, codes, dates, groups, joined, _, _, _, _, _, _, _ = loaded
    features = build_features(groups, joined, parameters_for(cfg, cfg["seeds"][0]))
    with tempfile.TemporaryDirectory(prefix="prepare-", dir=root) as temp:
        stage = Path(temp)
        counts, files = Counter(), []
        for i, seed in enumerate(cfg["seeds"]):
            path = build_path(features, parameters_for(cfg, seed))
            counts["independent_seeded_public_rows"] += verify_panel(path, groups, joined, parameters_for(cfg, seed))
            counts["common_innovation_draws"] += len(dates)
            name = f"factor_{i}.jsonl.gz"
            with ExitStack() as stack:
                gzip_writer(stack, stage / name).write(record_bytes({"seed_id": seed["seed_id"], "public_path": path}))
            files.append(name)
        counts["independent_lagged_feature_rows"] = sum(len(v) for v in features.values())
        wanted = {(s["seed_id"], old_name(c)): f"legacy_{si}_{ci}.jsonl.gz" for si, s in enumerate(cfg["seeds"])
                  for ci, c in enumerate(CELLS) if c["public_controls"] is None}
        partitions = Counter()
        with ExitStack() as stack:
            streams = {key: gzip_writer(stack, stage / name) for key, name in wanted.items()}
            gzip_writer(stack, stage / "empty.jsonl.gz")
            with gzip.open(SOURCE / "ledger.jsonl.gz", "rb") as source:
                for line in source:
                    row = json.loads(line)
                    key = row["seed_id"], row["variant"]
                    if key in streams:
                        streams[key].write(line)
                        partitions[key] += 1
        if set(partitions) != set(wanted) or any(n != 41 * 43 for n in partitions.values()):
            raise ValueError("source legacy partition coverage differs")
        files.extend(wanted.values())
        files.append("empty.jsonl.gz")
        prepared = {"checks": dict(counts), "source_legacy_rows": sum(partitions.values()),
                    "source_legacy_sha256": file_sha256(SOURCE / "ledger.jsonl.gz")}
        (stage / "preparation.json").write_bytes(record_bytes(prepared))
        files.append("preparation.json")
        manifest(stage, files, "prepared_sources")
        stage.replace(folder)
    print("Prepared full source factors and all 20 original-condition partitions.", flush=True)
    return prepared


def combined_gzip(path, sources):
    with ExitStack() as stack:
        output = gzip_writer(stack, path)
        for source in sources:
            with gzip.open(source, "rb") as stream:
                shutil.copyfileobj(stream, output, length=1024 * 1024)


def finish(root, loaded, audit_existing):
    cfg, base, _, market, codes, dates, _, _, inputs, code_hashes, _, _, feature_checks, _, source = loaded
    prepared = json.loads((root / "prepared/preparation.json").read_text(encoding="utf-8"))
    paths, variants, checks, folders = [], [], Counter(prepared["checks"]), []
    for si, seed in enumerate(cfg["seeds"]):
        for ci, cell in enumerate(CELLS):
            folder = root / f"cell_{si}_{ci}"
            verify_checkpoint(folder, [seed["seed_id"], cell["name"]])
            result = json.loads((folder / "condition.json").read_text(encoding="utf-8"))
            if result["variant"]["seed_id"] != seed["seed_id"] or result["variant"]["name"] != cell["name"]:
                raise ValueError("condition result identity differs")
            paths.extend(result["paths"])
            variants.append(result["variant"])
            checks.update(result["checks"])
            folders.append(folder)
    checks["conditions"], checks["paths"] = len(variants), len(paths)
    expected = expected_coverage(5, 41, 43, 3, 12, base["background"]["participants"])
    if dict(checks) != expected or expected != cfg["derived_expected_coverage"]:
        raise ValueError("resumed full coverage differs: " + json.dumps(dict(checks), sort_keys=True))
    result = {"pipeline_version": VERSION, "agent_signal_enabled": False, "sample": source["sample"],
        "market_dataset_id": market["market_dataset_id"], "seeds": cfg["seeds"], "limits": cfg["joint_limits"],
        "variants": variants, "path_summaries": paths, **summarize(variants), "checks": dict(checks),
        "source_feature_checks": feature_checks, "interpretation": cfg["interpretation"]}
    with tempfile.TemporaryDirectory(prefix="public-factor-merge-", dir=OUTPUT.parent) as temp:
        stage = Path(temp)
        (stage / "results.json").write_bytes(record_bytes({"result": result}))
        for name in ("ledger.jsonl.gz", "same_state_orders.jsonl.gz"):
            combined_gzip(stage / name, [p / name for p in folders])
        combined_gzip(stage / "factor_paths.jsonl.gz", [root / f"prepared/factor_{i}.jsonl.gz" for i in range(5)])
        verify_numeric(inputs)
        verify_numeric(code_hashes)
        names = ("results.json", "ledger.jsonl.gz", "same_state_orders.jsonl.gz", "factor_paths.jsonl.gz")
        declaration = json.loads(CONFIG_V3.read_text(encoding="utf-8"))
        output_manifest = {"pipeline_version": VERSION, "inputs": inputs, "code_sha256": code_hashes,
            "historical_nonexecuted_bindings": declaration["historical_nonexecuted_binding"],
            "execution_scope": declaration["version"],
            "artifacts": {n: {"sha256": file_sha256(stage / n)} for n in names}}
        (stage / "manifest.json").write_bytes((json.dumps(output_manifest, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode())
        if audit_existing:
            for name in (*names, "manifest.json"):
                if file_sha256(stage / name) != file_sha256(OUTPUT / name):
                    raise ValueError("checkpoint full producer rebuild differs: " + name)
            print("All five public factor artifacts rebuilt byte-identically from recomputed checkpoints.", flush=True)
        else:
            if OUTPUT.exists():
                raise ValueError("do not replace existing formal output")
            stage.replace(OUTPUT)
            print(json.dumps(dict(checks), sort_keys=True), flush=True)
    completion = {"status": "COMPLETE_BYTE_REBUILD" if audit_existing else "COMPLETE_PRODUCTION", "checks": dict(checks),
        "artifact_hashes": {n: file_sha256(OUTPUT / n) for n in (*names, "manifest.json")},
        "execution_config_sha256": file_sha256(CONFIG_V3)}
    (root / "completion.json").write_bytes(record_bytes(completion))
    return completion


def run(max_cells, audit_existing=False):
    if type(max_cells) is not int or max_cells < 1:
        raise ValueError("max cells must be a positive integer")
    root = REBUILD if audit_existing else CHECKPOINTS
    root.mkdir(exist_ok=True)
    loaded = load_inputs()
    cfg = loaded[0]
    prepare(root, loaded)
    finished = root / "completion.json"
    if finished.exists():
        record = json.loads(finished.read_text(encoding="utf-8"))
        for name, expected in record["artifact_hashes"].items():
            if file_sha256(OUTPUT / name) != expected:
                raise ValueError("completed output changed")
        print("Checkpoint execution already complete and formal outputs rechecked.", flush=True)
        return record
    completed_now = 0
    for si, seed in enumerate(cfg["seeds"]):
        with gzip.open(root / f"prepared/factor_{si}.jsonl.gz", "rt", encoding="utf-8") as stream:
            public = json.load(stream)["public_path"]
        receipts = {}
        for ci, cell in enumerate(CELLS):
            folder = root / f"cell_{si}_{ci}"
            if folder.exists():
                verify_checkpoint(folder, [seed["seed_id"], cell["name"]])
                if ci == 0:
                    saved = json.loads((folder / "condition.json").read_text(encoding="utf-8"))
                    receipts = {tuple(map(int, k.split("|"))): h for k, h in saved["receipt_hashes"].items()}
                continue
            if completed_now >= max_cells:
                print(f"Saved {completed_now} new complete conditions; remaining cells can resume without recomputing completed cells.", flush=True)
                return {"status": "CHECKPOINT_BATCH_COMPLETE", "new_conditions": completed_now}
            legacy = root / "prepared" / (f"legacy_{si}_{ci}.jsonl.gz" if cell["public_controls"] is None else "empty.jsonl.gz")
            with tempfile.TemporaryDirectory(prefix=f"pending_{si}_{ci}_", dir=root) as temp:
                stage = Path(temp)
                execute_cell(loaded, seed, cell, public, receipts, legacy, stage)
                manifest(stage, ("ledger.jsonl.gz", "same_state_orders.jsonl.gz", "condition.json"), [seed["seed_id"], cell["name"]])
                stage.replace(folder)
            completed_now += 1
            print(f"Durably committed condition {si * 16 + ci + 1}/80.", flush=True)
    return finish(root, loaded, audit_existing)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-cells", type=int, default=4)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    run(args.max_cells, args.audit_existing)
    print("Checkpoint batch process completed.", flush=True)
