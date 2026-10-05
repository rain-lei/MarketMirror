import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from research.data_pipeline.provenance import file_sha256
from research.semantic.annotation_pack import VERSION as PACK_VERSION, canonical_hash
from research.semantic.review_interface import build_review_interface
from research.semantic.review_workflow import prepare_packets
from research.semantic.signal_validation import validate_labels


ROOT = Path(__file__).resolve().parents[1]


def item(number, text):
    segments = [{"source": "question", "text": text}]
    return {"item_id": format(number, "064x"), "source_text_sha256": canonical_hash(segments),
            "segments": segments, "stage": "question", "split": "train",
            "stock_code": str(number).zfill(6), "qa_id": f"source-{number}",
            "selection_stratum": "none"}


def pack_at(root):
    pack = root / "pack"
    pack.mkdir()
    samples = [item(1, '😀公司公告 </script><script>alert("x")</script>'),
               item(2, "流动性风险有所下降")]
    items_path = pack / "annotation_items.jsonl"
    items_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in samples), encoding="utf-8")
    (pack / "annotation_manifest.json").write_text(json.dumps({
        "pipeline_version": PACK_VERSION, "experiment_id": "synthetic-review-fixture",
        "counts": {"items": len(samples)},
        "artifacts": {"annotation_items.jsonl": {"sha256": file_sha256(items_path)}}}), encoding="utf-8")
    return pack


class ReviewInterfaceTest(unittest.TestCase):
    def test_blinded_pages_are_bound_to_blank_packets_and_escape_source_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = pack_at(root)
            packets = root / "packets"
            prepare_packets(pack, packets)
            output = root / "pages"
            manifest = build_review_interface(pack, packets, output)
            self.assertEqual(manifest["items_per_reviewer"], 2)
            self.assertEqual(set(manifest["artifacts"]), {"reviewer_a.html", "reviewer_b.html"})
            for slot in ("reviewer_a", "reviewer_b"):
                page_path = output / f"{slot}.html"
                html = page_path.read_text(encoding="utf-8")
                self.assertEqual(file_sha256(page_path), manifest["artifacts"][page_path.name]["sha256"])
                self.assertEqual(html.count("</script>"), 3)
                self.assertNotIn('😀公司公告 </script>', html)
                self.assertNotIn('"stock_code"', html)
                self.assertNotIn('"split"', html)
                self.assertNotIn('"model_id"', html)
                match = re.search(r'<script id="review-data" type="application/json">(.*?)</script>', html, re.S)
                self.assertIsNotNone(match)
                payload = json.loads(match.group(1))
                self.assertEqual(payload["slot"], slot)
                self.assertEqual(len(payload["tasks"]), 2)
                self.assertEqual([row["status"] for row in payload["labels"]], ["unlabeled"] * 2)
                self.assertEqual(set(payload["tasks"][0]),
                                 {"item_id", "source_text_sha256", "stage", "segments"})
                self.assertTrue(any("</script>" in row["segments"][0]["text"] for row in payload["tasks"]))

            with self.assertRaisesRegex(ValueError, "new empty"):
                build_review_interface(pack, packets, output)

    def test_tampered_packet_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = pack_at(root)
            packets = root / "packets"
            prepare_packets(pack, packets)
            path = packets / "reviewer_b_tasks.jsonl"
            path.write_text(path.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                build_review_interface(pack, packets, root / "pages")

    @unittest.skipUnless(shutil.which("node"), "Node is needed for offline browser-core test")
    def test_browser_core_source_offsets_and_draft_validation(self):
        asset = ROOT / "research/semantic/review_assets/review_core.js"
        subprocess.run(["node", "--check", str(asset)], check=True, capture_output=True, encoding="utf-8")
        subprocess.run(["node", "--check", str(asset.with_name("review.js"))],
                       check=True, capture_output=True, encoding="utf-8")
        script = r'''
const assert = require("node:assert/strict");
const core = require(process.argv[1]);
const segments = [{source: "question", text: "😀公告：流动性改善"}];
const event = core.makeEvent({event_type: "liquidity", direction: "positive",
  horizon: "unknown", intensity: "0.5", uncertainty: "0.2", industries: "",
  source: "question", quote: "公告：流动性改善"}, segments);
assert.deepEqual(event.evidence_spans[0],
  {source: "question", start: 1, end: 9, quote: "公告：流动性改善"});
const task = {item_id: "id-1", source_text_sha256: "hash-1", stage: "question", segments};
const row = {item_id: "id-1", source_text_sha256: "hash-1", annotator_id: "human-a",
  status: "labeled", events: [event], notes: ""};
assert.equal(core.validateDraft([task], [row]).completed, 1);
const bad = structuredClone(row);
bad.events[0].evidence_spans[0].end += 1;
assert.throws(() => core.validateDraft([task], [bad]), /证据位置/);
assert.throws(() => core.exactSpan([{source: "question", text: "政策政策"}], "question", "政策"), /重复/);
assert.throws(() => core.exactSpan(segments, "reply", "公告"), /不可见/);
process.stdout.write(JSON.stringify(row));
'''
        result = subprocess.run(["node", "-e", script, str(asset)], check=True,
                                capture_output=True, encoding="utf-8")
        source = [{"source": "question", "text": "😀公告：流动性改善"}]
        python_item = {"item_id": "id-1", "source_text_sha256": "hash-1", "segments": source}
        self.assertEqual(validate_labels({"id-1": python_item}, [json.loads(result.stdout)])["reviewed_items"], 1)


if __name__ == "__main__":
    unittest.main()
