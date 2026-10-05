import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.data_pipeline.provenance import file_sha256
from research.semantic.annotation_pack import canonical_hash
from research.semantic import wuhan_v6_review as target


class WuhanV6ReviewTest(unittest.TestCase):
    def setUp(self):
        segments = [{"source": "question", "text": "审批是否完成？"},
                    {"source": "reply", "text": "申请已获正式受理，目前仍在审批。"}]
        self.items = {"item-one": {"item_id": "item-one", "stock_code": "000001",
                                   "stage": "reply", "segments": segments,
                                   "source_text_sha256": canonical_hash(segments)}}
        self.decision = {"index": 1, "events": [{
            "type": "regulation", "quote": "申请已获正式受理，目前仍在审批。",
            "direction": "neutral", "intensity": 0.5, "uncertainty": 0.4}],
            "notes": "回复确认正式受理并仍在审查，未把程序状态误写成已获批。"}

    def test_review_requires_exact_reply_evidence(self):
        labels, audit = target.compile_decisions(self.items, [self.decision])
        self.assertEqual(audit["reviewed_items"], 1)
        self.assertEqual(audit["events"], 1)
        self.assertEqual(labels[0]["events"][0]["evidence_spans"][0]["source"], "reply")
        self.assertFalse(labels[0]["events"][0]["direction"] == "positive")

        question_quote = {**self.decision, "events": [
            {**self.decision["events"][0], "quote": "审批是否完成？"}]}
        with self.assertRaisesRegex(ValueError, "quote must occur once in reply"):
            target.compile_decisions(self.items, [question_quote])
        with self.assertRaisesRegex(ValueError, "out of order"):
            target.compile_decisions(self.items, [{**self.decision, "index": 2}])

    def test_partial_work_validates_only_the_reviewed_prefix(self):
        second = {**self.items["item-one"], "item_id": "item-two", "stock_code": "000002"}
        items = {**self.items, "item-two": second}
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "decisions.jsonl"
            work.write_text(json.dumps(self.decision, ensure_ascii=False) + "\n", encoding="utf-8")
            with (patch.object(target, "load_policy", return_value={"sample_size": 2}),
                  patch.object(target, "verify_snapshot",
                               return_value=(items, {"experiment_id": "sixth-pack"})),
                  patch.object(target, "assert_no_model_run")):
                checked = target.check_work(Path(directory), work)
                self.assertEqual(checked["audit"]["reviewed_items"], 1)
                self.assertEqual(checked["remaining_items"], 1)
                forged = {**self.decision, "events": [
                    {**self.decision["events"][0], "quote": "审批是否完成？"}]}
                work.write_text(json.dumps(forged, ensure_ascii=False) + "\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "quote must occur once in reply"):
                    target.check_work(Path(directory), work)

    def test_review_cannot_finalize_after_matching_model_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "research_outputs" / "model-run"
            run_dir.mkdir(parents=True)
            (run_dir / "model_run_manifest.json").write_text(
                json.dumps({"pack_experiment_id": "sixth-pack"}), encoding="utf-8")
            with patch.object(target, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "must precede"):
                    target.assert_no_model_run("sixth-pack")
                target.assert_no_model_run("different-pack")

    def test_archive_labels_must_reconstruct_from_item_decisions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outputs = root / "research_outputs"
            source_dir = outputs / "source"
            work_dir = outputs / "work"
            review_dir = outputs / "review"
            source_dir.mkdir(parents=True)
            work_dir.mkdir(parents=True)
            (source_dir / "annotation_manifest.json").write_text("{}\n", encoding="utf-8")
            (source_dir / "annotation_items.jsonl").write_text("{}\n", encoding="utf-8")
            second = {**self.items["item-one"], "item_id": "item-two",
                      "stock_code": "000002"}
            items = {**self.items, "item-two": second}
            work_path = work_dir / "decisions_work.jsonl"
            decisions = [self.decision,
                         {"index": 2, "events": [],
                          "notes": "这条回复只说明既有资料，没有确认新的公司事件。"}]
            work_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n"
                                         for row in decisions), encoding="utf-8")
            prompt = root / "prompt.md"
            policy_path = root / "policy.json"
            prompt.write_text("frozen v6 prompt", encoding="utf-8")
            policy_path.write_text("{}\n", encoding="utf-8")
            policy = {"sample_size": 2, "sample_role": "sixth_disjoint_company_evaluation",
                      "reviewer_kind": "single_ai_assistant", "score_interpretation": "test"}
            with (patch.object(target, "ROOT", root),
                  patch.object(target, "PROMPT", prompt),
                  patch.object(target, "POLICY", policy_path),
                  patch.object(target, "verify_snapshot",
                               return_value=(items, {"experiment_id": "sixth-pack"})),
                  patch.object(target, "load_policy", return_value=policy),
                  patch.object(target, "_code_hashes", return_value={"builder": "test-hash"}),
                  patch.object(target, "assert_no_model_run")):
                target.finalize(source_dir, work_path, review_dir)
                labels, manifest = target.load_review(source_dir, review_dir, work_path)
                self.assertEqual(len(labels), 2)
                self.assertEqual(manifest["audit"]["reviewed_items"], 2)

                labels_path = review_dir / target.DECISIONS_NAME
                forged = [json.loads(line) for line in labels_path.read_text(
                    encoding="utf-8").splitlines()]
                forged[0]["events"] = []
                labels_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n"
                                               for row in forged), encoding="utf-8")
                manifest_path = review_dir / target.MANIFEST_NAME
                saved = json.loads(manifest_path.read_text(encoding="utf-8"))
                saved["artifacts"][target.DECISIONS_NAME]["sha256"] = file_sha256(labels_path)
                manifest_path.write_text(json.dumps(saved, ensure_ascii=False) + "\n",
                                         encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "differ from work decisions"):
                    target.load_review(source_dir, review_dir, work_path)


if __name__ == "__main__":
    unittest.main()
