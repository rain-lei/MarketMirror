"""Archive a source-first, single-assistant review of the fresh Wuhan cohort."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .assistant_review import validate_review
from .signal_validation import read_jsonl
from .wuhan_protocol_v3 import verify_pack


VERSION = "wuhan-fresh-holdout-source-first-review-v1"
ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v3_holdout_source_v1"
PACK_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v3_holdout_protocol_v3_v1"
POLICY = ROOT / "research/configs/wuhan_validation_review_policy_v3.json"
SOURCE_EXPERIMENT_ID = "839209c649839f13fb620c8f4650d5c5d7d74f28ccdbb34bf33f833d7415c5fd"
DECISIONS_NAME = "reference_labels.jsonl"
MANIFEST_NAME = "source_first_review_manifest.json"


def _expected_pack(pack_dir: Path) -> tuple[dict, dict]:
    if pack_dir.resolve() != PACK_DIR.resolve():
        raise ValueError("source-first review is restricted to the frozen fresh company cohort")
    items, pack = verify_pack(pack_dir)
    derived = pack.get("derived_protocol", {})
    source_dir = Path(derived.get("source_pack_directory", "")).resolve()
    if (source_dir != SOURCE_DIR.resolve()
            or derived.get("source_pack_experiment_id") != SOURCE_EXPERIMENT_ID
            or len(items) != 94
            or any(item["stage"] != "reply" for item in items.values())):
        raise ValueError("fresh source, reply coverage or frozen cohort identity differs")
    return items, pack


def _assert_no_local_model_run(experiment_id: str) -> None:
    for path in (ROOT / "research_outputs").rglob("model_run_manifest.json"):
        run = json.loads(path.read_text(encoding="utf-8"))
        if run.get("pack_experiment_id") == experiment_id:
            raise ValueError("source-first review must be finalized before model outputs exist")


def finalize(pack_dir: Path, decisions: Path, output_dir: Path) -> dict:
    pack_dir, decisions, output_dir = (path.resolve() for path in (pack_dir, decisions, output_dir))
    items, pack = _expected_pack(pack_dir)
    _assert_no_local_model_run(pack["experiment_id"])
    if (output_dir == pack_dir or output_dir == decisions or output_dir in pack_dir.parents
            or pack_dir in output_dir.parents or decisions in output_dir.parents
            or output_dir.exists() and any(output_dir.iterdir())):
        raise ValueError("review output must be a new directory separate from inputs")
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    if (policy.get("review_before_v3_model_outputs") is not True
            or policy.get("human_gold_ready") is not False
            or policy.get("reviewer_kind") != "single_ai_assistant"):
        raise ValueError("fresh review policy differs from the frozen single-assistant policy")
    labels = read_jsonl(decisions)
    audit = validate_review(items, labels)
    if len(labels) != len(items) or audit["needs_review_items"] or audit["unlabeled_items"]:
        raise ValueError("source-first review requires explicit decisions for every reply")

    inputs = {str(path): file_sha256(path) for path in (
        pack_dir / "annotation_manifest.json", pack_dir / "annotation_items.jsonl",
        decisions, POLICY)}
    result = {"pipeline_version": VERSION,
              "generated_at": datetime.now(timezone.utc).isoformat(),
              "pack_experiment_id": pack["experiment_id"],
              "source_experiment_id": SOURCE_EXPERIMENT_ID,
              "sample_role": "fresh_disjoint_company_cohort",
              "reviewer_kind": "single_ai_assistant",
              "review_before_v3_model_outputs": True,
              "model_outputs_previously_seen": False,
              "independent_company_sample": True,
              "external_preregistration": False,
              "human_gold_ready": False,
              "score_interpretation": policy["score_interpretation"],
              "audit": audit}
    if any(file_sha256(Path(name)) != digest for name, digest in inputs.items()):
        raise RuntimeError("fresh review input changed during finalization")

    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        stage = Path(temporary)
        shutil.copyfile(decisions, stage / DECISIONS_NAME)
        report = stage / "source_first_review_report.md"
        report.write_text(
            "# 武汉新留出公司样本：模型前逐条复核\n\n"
            f"已逐条复核 {audit['reviewed_items']}/{len(items)} 条回复；"
            f"{audit['event_items']} 条含事件，共 {audit['events']} 个事件。\n\n"
            "复核先于本批模型调用，仅依据冻结的问答文本和固定事件规则。"
            "每条结论保留独立理由，事件引用来自公司回复且逐字定位。"
            "该批与既有公司样本分离，但不是外部预注册；单一 AI 参考不是人工金标准。\n",
            encoding="utf-8")
        manifest = {**result, "inputs": inputs,
                    "code_sha256": {
                        "wuhan_fresh_holdout_review.py": file_sha256(Path(__file__)),
                        "assistant_review.py": file_sha256(Path(__file__).with_name("assistant_review.py")),
                        "wuhan_protocol_v3.py": file_sha256(Path(__file__).with_name("wuhan_protocol_v3.py"))},
                    "artifacts": {path.name: {"sha256": file_sha256(path)}
                                  for path in stage.iterdir()}}
        (stage / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in stage.iterdir():
            path.replace(output_dir / path.name)
    return result


def load_review(pack_dir: Path, review_dir: Path) -> tuple[list[dict], dict]:
    pack_dir, review_dir = pack_dir.resolve(), review_dir.resolve()
    items, pack = _expected_pack(pack_dir)
    manifest_path = review_dir / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_code = {
        "wuhan_fresh_holdout_review.py": file_sha256(Path(__file__)),
        "assistant_review.py": file_sha256(Path(__file__).with_name("assistant_review.py")),
        "wuhan_protocol_v3.py": file_sha256(Path(__file__).with_name("wuhan_protocol_v3.py"))}
    if (manifest.get("pipeline_version") != VERSION
            or manifest.get("pack_experiment_id") != pack["experiment_id"]
            or manifest.get("source_experiment_id") != SOURCE_EXPERIMENT_ID
            or manifest.get("review_before_v3_model_outputs") is not True
            or manifest.get("model_outputs_previously_seen") is not False
            or manifest.get("human_gold_ready") is not False
            or manifest.get("code_sha256") != expected_code):
        raise ValueError("fresh review identity, order or code binding differs")
    required = (pack_dir / "annotation_manifest.json", pack_dir / "annotation_items.jsonl", POLICY)
    if any(manifest.get("inputs", {}).get(str(path.resolve())) != file_sha256(path)
           for path in required):
        raise ValueError("fresh review pack or policy hash differs")
    if any(file_sha256(Path(name)) != digest for name, digest in manifest["inputs"].items()):
        raise ValueError("fresh review input hash differs")
    expected_artifacts = {DECISIONS_NAME, "source_first_review_report.md"}
    if set(manifest.get("artifacts", {})) != expected_artifacts:
        raise ValueError("fresh review artifact set differs")
    if any(file_sha256(review_dir / name) != info["sha256"]
           for name, info in manifest["artifacts"].items()):
        raise ValueError("fresh review output hash differs")
    labels = read_jsonl(review_dir / DECISIONS_NAME)
    if validate_review(items, labels) != manifest.get("audit"):
        raise ValueError("fresh review audit counts differ")
    return labels, manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_dir", type=Path)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = finalize(args.pack_dir, args.decisions, args.output_dir)
    print(json.dumps({"pack_experiment_id": result["pack_experiment_id"],
                      "reviewed_items": result["audit"]["reviewed_items"],
                      "event_items": result["audit"]["event_items"],
                      "events": result["audit"]["events"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
