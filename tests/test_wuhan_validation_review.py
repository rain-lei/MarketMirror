import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.semantic.annotation_pack import canonical_hash
from research.semantic.wuhan_validation_review import finalize, load_blind_review


class WuhanValidationReviewTest(unittest.TestCase):
    def test_full_blind_reference_is_source_bound_and_tamper_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = root / "pack"
            source = root / "source"
            pack.mkdir()
            source.mkdir()
            (pack / "annotation_manifest.json").write_text("{}", encoding="utf-8")
            (pack / "annotation_items.jsonl").write_text("{}\n", encoding="utf-8")
            segments = [{"source": "question", "text": "是不是已经签约？"},
                        {"source": "reply", "text": "尚未签约。"}]
            items = {f"item-{index}": {"item_id": f"item-{index}", "stage": "reply",
                                       "stock_code": f"{index:06d}",
                                       "source_text_sha256": canonical_hash(segments),
                                       "segments": segments} for index in range(102)}
            manifest = {"experiment_id": "fixture", "derived_protocol": {
                "source_pack_directory": str(source)}}
            decisions = root / "decisions.jsonl"
            labels = [{"item_id": item["item_id"],
                       "source_text_sha256": item["source_text_sha256"],
                       "annotator_id": "ai:fixture-reviewer", "status": "labeled", "events": [],
                       "notes": f"第{index}条回复明确尚未签约，提问不能当成已完成交易。"}
                      for index, item in enumerate(items.values())]
            decisions.write_text("".join(json.dumps(label, ensure_ascii=False) + "\n"
                                         for label in labels), encoding="utf-8")
            with patch("research.semantic.wuhan_validation_review.VALIDATION_PACK", pack), \
                 patch("research.semantic.wuhan_validation_review.VALIDATION_SOURCE", source), \
                 patch("research.semantic.wuhan_validation_review.ROOT", root), \
                 patch("research.semantic.wuhan_validation_review.verify_pack",
                       return_value=(items, manifest)):
                result = finalize(pack, decisions, root / "review")
                self.assertEqual(result["audit"]["reviewed_items"], 102)
                loaded, archive = load_blind_review(pack, root / "review")
                self.assertEqual(loaded, labels)
                self.assertFalse(archive["human_gold_ready"])
                with self.assertRaisesRegex(ValueError, "restricted to the frozen independent"):
                    load_blind_review(root / "wrong", root / "review")
                model = root / "research_outputs/model"
                model.mkdir(parents=True)
                (model / "model_run_manifest.json").write_text(
                    json.dumps({"pack_experiment_id": "fixture"}), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "before v3 model outputs"):
                    finalize(pack, decisions, root / "second-review")
                archived = root / "review/reference_labels.jsonl"
                archived.write_text(archived.read_text(encoding="utf-8") + "\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "artifact hash differs"):
                    load_blind_review(pack, root / "review")


if __name__ == "__main__":
    unittest.main()
