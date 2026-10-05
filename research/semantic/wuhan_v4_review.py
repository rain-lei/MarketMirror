"""Archive a source-first, single-assistant review of the frozen v4 cohort."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .assistant_review import validate_review
from .prepare_wuhan_v4_holdout import ROOT
from .signal_validation import read_jsonl
from .wuhan_protocol_v4 import MODULE as PROTOCOL_MODULE, PACK_DIR, SOURCE_DIR, verify_pack


VERSION = "wuhan-v4-source-first-single-assistant-review-v1"
POLICY = ROOT / "research/configs/wuhan_v4_review_policy.json"
DECISIONS_NAME = "reference_labels.jsonl"
MANIFEST_NAME = "source_first_review_manifest.json"
REVIEW_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v4_holdout_review_v1"
DECISIONS_PATH = ROOT / "research_outputs/wuhan_pre_event_fresh_v4_holdout_review_decisions_v1.jsonl"


def _expected_pack(pack_dir: Path) -> tuple[dict, dict]:
    if pack_dir.resolve() != PACK_DIR.resolve():
        raise ValueError("v4 source-first review is restricted to the frozen fourth cohort")
    items, pack = verify_pack(pack_dir)
    derived = pack.get("derived_protocol", {})
    if (Path(derived.get("source_pack_directory", "")).resolve() != SOURCE_DIR.resolve()
            or len(items) != 108
            or any(item.get("stage") != "reply" for item in items.values())):
        raise ValueError("v4 source, reply coverage or frozen cohort identity differs")
    return items, pack


def assert_no_model_run(experiment_id: str) -> None:
    for path in (ROOT / "research_outputs").rglob("model_run_manifest.json"):
        run = json.loads(path.read_text(encoding="utf-8"))
        if run.get("pack_experiment_id") == experiment_id:
            raise ValueError("v4 source-first review must be finalized before this pack is sent to a model")


def finalize(pack_dir: Path, decisions: Path, output_dir: Path) -> dict:
    pack_dir, decisions, output_dir = (path.resolve() for path in (pack_dir, decisions, output_dir))
    items, pack = _expected_pack(pack_dir)
    assert_no_model_run(pack["experiment_id"])
    if (output_dir == pack_dir or output_dir == decisions or output_dir in pack_dir.parents
            or pack_dir in output_dir.parents or decisions in output_dir.parents
            or output_dir.exists() and any(output_dir.iterdir())):
        raise ValueError("v4 review output must be new and separate from inputs")
    outputs_root = (ROOT / "research_outputs").resolve()
    if output_dir == outputs_root or outputs_root not in output_dir.parents:
        raise ValueError("v4 review output must stay under the ignored research_outputs folder")
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    if (policy.get("protocol") != "wuhan-fourth-company-source-first-review-v4"
            or policy.get("review_before_model_outputs") is not True
            or policy.get("human_gold_ready") is not False
            or policy.get("reviewer_kind") != "single_ai_assistant"):
        raise ValueError("v4 review policy differs from the frozen single-review policy")
    labels = read_jsonl(decisions)
    audit = validate_review(items, labels)
    if len(labels) != len(items) or audit["needs_review_items"] or audit["unlabeled_items"]:
        raise ValueError("v4 source-first review requires a completed decision for every reply")

    inputs = {str(path.resolve()): file_sha256(path) for path in (
        pack_dir / "annotation_manifest.json", pack_dir / "annotation_items.jsonl",
        decisions, POLICY)}
    result = {"pipeline_version": VERSION,
              "generated_at": datetime.now(timezone.utc).isoformat(),
              "pack_experiment_id": pack["experiment_id"],
              "sample_role": policy["sample_role"],
              "reviewer_kind": policy["reviewer_kind"],
              "review_before_model_outputs": True,
              "model_outputs_previously_seen": False,
              "independent_company_sample": True,
              "external_preregistration": False,
              "human_gold_ready": False,
              "score_interpretation": policy["score_interpretation"],
              "audit": audit}
    if any(file_sha256(Path(name)) != digest for name, digest in inputs.items()):
        raise RuntimeError("v4 review input changed during finalization")

    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        stage = Path(temporary)
        shutil.copyfile(decisions, stage / DECISIONS_NAME)
        report = stage / "source_first_review_report.md"
        report.write_text(
            "# 武汉第四批公司样本：v4 模型前逐条复核\n\n"
            f"已逐条复核 {audit['reviewed_items']}/{len(items)} 条回复；"
            f"{audit['event_items']} 条含事件，共 {audit['events']} 个事件。\n\n"
            "复核先于本批模型调用，仅依据冻结问答和 v4 固定类别规则。"
            "事件引用来自公司回复且精确定位；单一 AI 参考不是人工金标准。"
            "样本与前三批公司分离，但未作外部预注册。\n",
            encoding="utf-8")
        code_hashes = {
            "wuhan_v4_review.py": file_sha256(Path(__file__)),
            "assistant_review.py": file_sha256(Path(__file__).with_name("assistant_review.py")),
            "wuhan_protocol_v4.py": file_sha256(PROTOCOL_MODULE),
        }
        manifest = {**result, "inputs": inputs, "code_sha256": code_hashes,
                    "artifacts": {path.name: {"sha256": file_sha256(path)}
                                  for path in stage.iterdir()}}
        (stage / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in stage.iterdir():
            path.replace(output_dir / path.name)
    return result


def load_review(pack_dir: Path = PACK_DIR, review_dir: Path = REVIEW_DIR) -> tuple[list[dict], dict]:
    pack_dir, review_dir = pack_dir.resolve(), review_dir.resolve()
    items, pack = _expected_pack(pack_dir)
    manifest_path = review_dir / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_code = {
        "wuhan_v4_review.py": file_sha256(Path(__file__)),
        "assistant_review.py": file_sha256(Path(__file__).with_name("assistant_review.py")),
        "wuhan_protocol_v4.py": file_sha256(PROTOCOL_MODULE),
    }
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    if (manifest.get("pipeline_version") != VERSION
            or manifest.get("pack_experiment_id") != pack["experiment_id"]
            or manifest.get("sample_role") != policy["sample_role"]
            or manifest.get("reviewer_kind") != policy["reviewer_kind"]
            or manifest.get("review_before_model_outputs") is not True
            or manifest.get("model_outputs_previously_seen") is not False
            or manifest.get("human_gold_ready") is not False
            or manifest.get("code_sha256") != expected_code):
        raise ValueError("v4 review identity, chronology or code binding differs")
    for path in (pack_dir / "annotation_manifest.json", pack_dir / "annotation_items.jsonl", POLICY):
        if manifest.get("inputs", {}).get(str(path.resolve())) != file_sha256(path):
            raise ValueError("v4 review pack or policy hash differs")
    if any(file_sha256(Path(name)) != digest for name, digest in manifest["inputs"].items()):
        raise ValueError("v4 review input changed")
    expected_artifacts = {DECISIONS_NAME, "source_first_review_report.md"}
    if set(manifest.get("artifacts", {})) != expected_artifacts:
        raise ValueError("v4 review artifact set differs")
    if any(file_sha256(review_dir / name) != info["sha256"]
           for name, info in manifest["artifacts"].items()):
        raise ValueError("v4 review artifact hash differs")
    labels = read_jsonl(review_dir / DECISIONS_NAME)
    if validate_review(items, labels) != manifest.get("audit"):
        raise ValueError("v4 review audit differs from the archived label file")
    return labels, manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_dir", type=Path, nargs="?", default=PACK_DIR)
    parser.add_argument("--decisions", type=Path, default=DECISIONS_PATH)
    parser.add_argument("--output-dir", type=Path, default=REVIEW_DIR)
    args = parser.parse_args()
    result = finalize(args.pack_dir, args.decisions, args.output_dir)
    print(json.dumps({"pack_experiment_id": result["pack_experiment_id"],
                      "reviewed_items": result["audit"]["reviewed_items"],
                      "event_items": result["audit"]["event_items"],
                      "events": result["audit"]["events"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
