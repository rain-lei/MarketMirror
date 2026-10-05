import json
import tempfile
import unittest
from pathlib import Path

from research.data_pipeline.provenance import file_sha256
from research.semantic.annotation_pack import VERSION as PACK_VERSION, canonical_hash
from research.semantic.review_interface import build_review_interface
from research.semantic.review_readiness import audit_review_package
from research.semantic.review_workflow import prepare_packets


class ReviewReadinessTest(unittest.TestCase):
    def test_audits_complete_blank_128_item_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "pack"
            pack.mkdir()
            rows = []
            for number in range(128):
                segments = [{"source": "question", "text": f"问题 {number}：政策和流动性"}]
                rows.append({"item_id": f"{number:064x}",
                             "source_text_sha256": canonical_hash(segments),
                             "segments": segments, "stage": "question", "split": "test",
                             "stock_code": f"{number:06d}", "qa_id": f"qa-{number}",
                             "selection_stratum": "policy"})
            items_path = pack / "annotation_items.jsonl"
            items_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                                  encoding="utf-8")
            (pack / "annotation_manifest.json").write_text(json.dumps({
                "pipeline_version": PACK_VERSION, "experiment_id": "synthetic-128",
                "counts": {"items": 128},
                "artifacts": {"annotation_items.jsonl": {"sha256": file_sha256(items_path)}}}),
                encoding="utf-8")
            packets = root / "packets"
            prepare_packets(pack, packets)
            pages = root / "pages"
            build_review_interface(pack, packets, pages)
            result = audit_review_package(pack, packets, pages)
            self.assertEqual(result["status"], "ready_for_human_review")
            self.assertEqual(result["items"], 128)
            self.assertEqual(result["reviewed_items"], 0)
            self.assertFalse(result["gold_ready"])


if __name__ == "__main__":
    unittest.main()
