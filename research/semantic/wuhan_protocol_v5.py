"""Freeze and verify the fifth Wuhan company cohort and its v5 prompt."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..data_pipeline.provenance import file_sha256
from .annotation_pack import VERSION as PACK_VERSION, canonical_hash
from .prepare_wuhan_v5_holdout import (
    PROMPT,
    ROOT,
    SNAPSHOT_NAME,
    UNIVERSE_NAME,
    verify_existing as verify_cohort,
)
from .signal_validation import load_pack


VERSION = "wuhan-company-confirmed-v5"
DERIVED_VERSION = "wuhan-derived-annotation-pack-v5"
BASE_URL = "http://aigw.dlut.edu.cn/v1"
MODEL = "DeepSeek-V4-Flash-0731-W8A8"
MODULE = Path(__file__).resolve()
SELECTION_MODULE = MODULE.with_name("prepare_wuhan_v5_holdout.py")
SOURCE_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v5_holdout_source_v1"
PACK_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v5_holdout_protocol_v5_v1"


def _identity(source: dict[str, Any], source_manifest_sha256: str,
              source_items_sha256: str, prompt_sha256: str,
              builder_sha256: str, selection_sha256: str) -> str:
    return canonical_hash({
        "version": DERIVED_VERSION,
        "source_experiment_id": source["experiment_id"],
        "source_manifest_sha256": source_manifest_sha256,
        "source_items_sha256": source_items_sha256,
        "prompt_sha256": prompt_sha256,
        "builder_sha256": builder_sha256,
        "selection_builder_sha256": selection_sha256,
    })


def verify_source(source_dir: Path = SOURCE_DIR) -> tuple[dict[str, dict], dict]:
    if source_dir.resolve() != SOURCE_DIR.resolve():
        raise ValueError("v5 source must be the frozen fifth company snapshot")
    cohort = verify_cohort(ROOT / "research/configs" / UNIVERSE_NAME,
                           ROOT / "research/configs" / SNAPSHOT_NAME)
    items, source = load_pack(source_dir)
    config = source.get("config", {})
    selected = json.loads((ROOT / "research/configs" / UNIVERSE_NAME).read_text(encoding="utf-8"))
    if (source.get("pipeline_version") != PACK_VERSION
            or source.get("snapshot_pipeline_version") != "policy-event-qna-snapshot-v1"
            or source.get("selection_rule") != "latest_company_confirmed_reply_per_asset_before_cutoff"
            or config.get("run_id") != "wuhan_pre_event_fresh_v5_holdout_2020_v1"
            or config.get("universe_config") != UNIVERSE_NAME
            or source.get("universe_size") != 126
            or len(items) != 106
            or source.get("counts", {}).get("reply_items") != len(items)
            or source.get("counts", {}).get("companies_without_reply_snapshot") != 20
            or cohort["companies"] != 126
            or any(item.get("stage") != "reply"
                   or item.get("available_at", "") > source.get("snapshot_as_of", "")
                   for item in items.values())):
        raise ValueError("v5 source coverage, visibility or fifth cohort differs")
    codes = {item["stock_code"] for item in items.values()}
    if len(codes) != len(items) or not codes <= set(selected["stock_codes"]):
        raise ValueError("v5 source replies do not belong to unique selected companies")
    for path_text, expected in source.get("input_sha256", {}).items():
        path = Path(path_text)
        if not path.is_file() or file_sha256(path) != expected:
            raise ValueError(f"v5 source input hash differs: {path.name}")
    for name, expected in source.get("code_sha256", {}).items():
        path = MODULE.with_name(name)
        if not path.is_file() or file_sha256(path) != expected:
            raise ValueError(f"v5 source builder hash differs: {name}")
    return items, source


def derive_pack(source_dir: Path = SOURCE_DIR, output_dir: Path = PACK_DIR) -> dict:
    source_dir, output_dir = source_dir.resolve(), output_dir.resolve()
    items, source = verify_source(source_dir)
    outputs = (ROOT / "research_outputs").resolve()
    if (output_dir == outputs or outputs not in output_dir.parents
            or output_dir == source_dir or output_dir in source_dir.parents
            or source_dir in output_dir.parents
            or output_dir.exists() and any(output_dir.iterdir())):
        raise ValueError("v5 derived pack must be a new separate local output")
    source_manifest = source_dir / "annotation_manifest.json"
    source_items = source_dir / "annotation_items.jsonl"
    hashes = {
        "source_manifest_sha256": file_sha256(source_manifest),
        "source_items_sha256": file_sha256(source_items),
        "prompt_sha256": file_sha256(PROMPT),
        "builder_sha256": file_sha256(MODULE),
        "selection_builder_sha256": file_sha256(SELECTION_MODULE),
    }
    experiment_id = _identity(
        source, hashes["source_manifest_sha256"], hashes["source_items_sha256"],
        hashes["prompt_sha256"], hashes["builder_sha256"],
        hashes["selection_builder_sha256"])
    manifest = {
        **source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": source["run_id"] + "_company_confirmed_v5",
        "experiment_id": experiment_id,
        "frozen_model_protocol": {
            "provider_base_url": BASE_URL, "model_id": MODEL,
            "prompt_version": VERSION, "prompt_sha256": hashes["prompt_sha256"],
            "temperature": 0.0,
        },
        "derived_protocol": {
            "version": DERIVED_VERSION,
            "source_pack_directory": str(source_dir),
            "source_pack_experiment_id": source["experiment_id"],
            **hashes,
        },
        "input_sha256": {
            **source["input_sha256"],
            str(source_manifest): hashes["source_manifest_sha256"],
            str(source_items): hashes["source_items_sha256"],
            str(PROMPT.resolve()): hashes["prompt_sha256"],
            str(SELECTION_MODULE): hashes["selection_builder_sha256"],
        },
        "code_sha256": {**source["code_sha256"], MODULE.name: hashes["builder_sha256"]},
        "artifacts": {"annotation_items.jsonl": {"sha256": hashes["source_items_sha256"]}},
        "limitations": [
            *source.get("limitations", []),
            "v5 was developed on previously seen cohorts; this fifth company cohort is a one-time evaluation.",
            "Reference labels, if completed before v5 model calls, are one AI assistant's judgments, not human gold.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        stage = Path(temporary)
        shutil.copyfile(source_items, stage / "annotation_items.jsonl")
        (stage / "annotation_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if (file_sha256(source_manifest) != hashes["source_manifest_sha256"]
                or file_sha256(source_items) != hashes["source_items_sha256"]
                or file_sha256(PROMPT) != hashes["prompt_sha256"]
                or file_sha256(MODULE) != hashes["builder_sha256"]
                or file_sha256(SELECTION_MODULE) != hashes["selection_builder_sha256"]):
            raise RuntimeError("v5 pack source, prompt or builder changed while deriving")
        for path in stage.iterdir():
            path.replace(output_dir / path.name)
    if len(items) != 106:
        raise AssertionError("v5 pack reply count changed")
    return manifest


def verify_pack(pack_dir: Path = PACK_DIR) -> tuple[dict[str, dict], dict]:
    if pack_dir.resolve() != PACK_DIR.resolve():
        raise ValueError("v5 pack must be the frozen fifth company protocol")
    items, pack = load_pack(pack_dir)
    derived = pack.get("derived_protocol", {})
    source_items, source = verify_source(SOURCE_DIR)
    source_manifest = SOURCE_DIR / "annotation_manifest.json"
    source_items_path = SOURCE_DIR / "annotation_items.jsonl"
    hashes = {
        "source_manifest_sha256": file_sha256(source_manifest),
        "source_items_sha256": file_sha256(source_items_path),
        "prompt_sha256": file_sha256(PROMPT),
        "builder_sha256": file_sha256(MODULE),
        "selection_builder_sha256": file_sha256(SELECTION_MODULE),
    }
    protocol = {"provider_base_url": BASE_URL, "model_id": MODEL,
                "prompt_version": VERSION, "prompt_sha256": hashes["prompt_sha256"],
                "temperature": 0.0}
    expected_inputs = {
        **source["input_sha256"],
        str(source_manifest.resolve()): hashes["source_manifest_sha256"],
        str(source_items_path.resolve()): hashes["source_items_sha256"],
        str(PROMPT.resolve()): hashes["prompt_sha256"],
        str(SELECTION_MODULE): hashes["selection_builder_sha256"],
    }
    expected_code = {**source["code_sha256"], MODULE.name: hashes["builder_sha256"]}
    if (derived.get("version") != DERIVED_VERSION
            or Path(derived.get("source_pack_directory", "")).resolve() != SOURCE_DIR.resolve()
            or derived.get("source_pack_experiment_id") != source["experiment_id"]
            or any(derived.get(name) != value for name, value in hashes.items())
            or pack.get("frozen_model_protocol") != protocol
            or pack.get("run_id") != source["run_id"] + "_company_confirmed_v5"
            or pack.get("input_sha256") != expected_inputs
            or pack.get("code_sha256") != expected_code
            or pack.get("experiment_id") != _identity(
                source, hashes["source_manifest_sha256"], hashes["source_items_sha256"],
                hashes["prompt_sha256"], hashes["builder_sha256"],
                hashes["selection_builder_sha256"])
            or items != source_items
            or file_sha256(pack_dir / "annotation_items.jsonl") != hashes["source_items_sha256"]):
        raise ValueError("v5 pack differs from its frozen source or prompt")
    for path_text, expected in pack.get("input_sha256", {}).items():
        path = Path(path_text)
        if not path.is_file() or file_sha256(path) != expected:
            raise ValueError(f"v5 pack input hash differs: {path.name}")
    for name, expected in pack.get("code_sha256", {}).items():
        path = MODULE.with_name(name)
        if not path.is_file() or file_sha256(path) != expected:
            raise ValueError(f"v5 pack code hash differs: {name}")
    return items, pack


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        items, result = verify_pack()
        print(json.dumps({"items": len(items), "experiment_id": result["experiment_id"]},
                         ensure_ascii=False))
    else:
        result = derive_pack()
        print(json.dumps({"items": result["counts"]["reply_items"],
                          "experiment_id": result["experiment_id"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
