"""Archive a source-first AI review of the independent Wuhan company cohort."""

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


VERSION = "wuhan-independent-company-ai-review-v3"
ROOT = Path(__file__).resolve().parents[2]
VALIDATION_SOURCE = ROOT / "research_outputs/wuhan_pre_event_validation_source_2020_v1"
VALIDATION_PACK = ROOT / "research_outputs/wuhan_pre_event_independent_protocol_v3_v1"
POLICY = ROOT / "research/configs/wuhan_validation_review_policy_v3.json"
MODULE = Path(__file__).resolve()
DECISIONS_NAME = "reference_labels.jsonl"
MANIFEST_NAME = "blind_review_manifest.json"


def _assert_no_local_model_run(experiment_id: str) -> None:
    """Fail closed if this repository already archived a model run for this cohort."""
    for path in (ROOT / "research_outputs").rglob("model_run_manifest.json"):
        run = json.loads(path.read_text(encoding="utf-8"))
        if run.get("pack_experiment_id") == experiment_id:
            raise ValueError("blind review must be finalized before v3 model outputs for this cohort")


def _expected_pack(pack_dir: Path) -> tuple[dict, dict]:
    if pack_dir.resolve() != VALIDATION_PACK.resolve():
        raise ValueError("blind review is restricted to the frozen independent company cohort")
    items, pack = verify_pack(pack_dir)
    source = pack["derived_protocol"]
    if (Path(source["source_pack_directory"]).resolve() != VALIDATION_SOURCE.resolve()
            or len(items) != 102 or any(item["stage"] != "reply" for item in items.values())):
        raise ValueError("blind review source or reply coverage differs")
    return items, pack


def finalize(pack_dir: Path, decisions: Path, output_dir: Path) -> dict:
    pack_dir, decisions, output_dir = (path.resolve() for path in (pack_dir, decisions, output_dir))
    items, pack = _expected_pack(pack_dir)
    _assert_no_local_model_run(pack["experiment_id"])
    if (output_dir == pack_dir or output_dir == decisions or output_dir in pack_dir.parents
            or pack_dir in output_dir.parents or decisions in output_dir.parents
            or output_dir.exists() and any(output_dir.iterdir())):
        raise ValueError("blind review output must be a new directory separate from inputs")
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    if (policy.get("protocol") != "wuhan-independent-company-ai-review-v3"
            or policy.get("review_before_v3_model_outputs") is not True
            or policy.get("human_gold_ready") is not False):
        raise ValueError("blind review policy differs from its frozen role")
    labels = read_jsonl(decisions)
    audit = validate_review(items, labels)
    if len(labels) != len(items) or audit["needs_review_items"] or audit["unlabeled_items"]:
        raise ValueError("blind review requires explicit decisions for every company reply")
    inputs = {str(path): file_sha256(path) for path in (
        pack_dir / "annotation_manifest.json", pack_dir / "annotation_items.jsonl",
        decisions, POLICY)}
    result = {"pipeline_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
              "pack_experiment_id": pack["experiment_id"], "sample_role": policy["sample_role"],
              "reviewer_kind": policy["reviewer_kind"], "review_before_v3_model_outputs": True,
              "independent_company_sample": True, "human_gold_ready": False,
              "score_interpretation": policy["score_interpretation"], "audit": audit}
    if any(file_sha256(Path(name)) != digest for name, digest in inputs.items()):
        raise RuntimeError("blind review input changed during finalization")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        stage = Path(temporary)
        shutil.copyfile(decisions, stage / DECISIONS_NAME)
        report = stage / "blind_review_report.md"
        report.write_text("# 武汉事前独立公司样本：单一 AI 盲复核\n\n"
                          f"已逐条复核 {audit['reviewed_items']}/{len(items)} 条回复；"
                          f"{audit['event_items']} 条含事件，共 {audit['events']} 个事件。\n\n"
                          "复核只读固定来源和预先固定的事件规则，未参考本批 v3 模型输出。"
                          "每条决定有独立理由，所有事件由公司回复中的精确引文支持。"
                          "这是不同公司样本上的单一 AI 参考，不是人工金标准或现实收益预测验证。\n",
                          encoding="utf-8")
        manifest = {**result, "inputs": inputs,
                    "code_sha256": {"wuhan_validation_review.py": file_sha256(MODULE),
                                    "assistant_review.py": file_sha256(MODULE.with_name("assistant_review.py"))},
                    "artifacts": {path.name: {"sha256": file_sha256(path)}
                                  for path in (stage / DECISIONS_NAME, report)}}
        (stage / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
        for path in stage.iterdir():
            path.replace(output_dir / path.name)
    return result


def load_blind_review(pack_dir: Path, review_dir: Path) -> tuple[list[dict], dict]:
    pack_dir, review_dir = pack_dir.resolve(), review_dir.resolve()
    items, pack = _expected_pack(pack_dir)
    manifest = json.loads((review_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    if (manifest.get("pipeline_version") != VERSION
            or manifest.get("pack_experiment_id") != pack["experiment_id"]
            or manifest.get("sample_role") != policy["sample_role"]
            or manifest.get("reviewer_kind") != policy["reviewer_kind"]
            or manifest.get("review_before_v3_model_outputs") is not True
            or manifest.get("independent_company_sample") is not True
            or manifest.get("human_gold_ready") is not False
            or manifest.get("code_sha256") != {
                "wuhan_validation_review.py": file_sha256(MODULE),
                "assistant_review.py": file_sha256(MODULE.with_name("assistant_review.py"))}):
        raise ValueError("blind review identity, policy or code differs")
    required = (pack_dir / "annotation_manifest.json", pack_dir / "annotation_items.jsonl", POLICY)
    if any(manifest.get("inputs", {}).get(str(path)) != file_sha256(path) for path in required):
        raise ValueError("blind review source or policy hash differs")
    if any(file_sha256(Path(name)) != digest for name, digest in manifest["inputs"].items()):
        raise ValueError("blind review input hash differs")
    if set(manifest.get("artifacts", {})) != {DECISIONS_NAME, "blind_review_report.md"}:
        raise ValueError("blind review artifact set differs")
    if any(file_sha256(review_dir / name) != info["sha256"]
           for name, info in manifest["artifacts"].items()):
        raise ValueError("blind review artifact hash differs")
    labels = read_jsonl(review_dir / DECISIONS_NAME)
    if validate_review(items, labels) != manifest.get("audit"):
        raise ValueError("blind review labels differ from archived audit")
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
                      "event_items": result["audit"]["event_items"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
