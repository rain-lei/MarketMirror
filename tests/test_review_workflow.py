import copy
import json
import tempfile
import unittest
from pathlib import Path

from research.data_pipeline.provenance import file_sha256
from research.semantic.annotation_pack import VERSION as PACK_VERSION, canonical_hash
from research.semantic.review_workflow import (blinded_task, cohens_kappa, compare_reviewers,
                                               finalize_labels, run_comparison, validate_adjudication)


def make_item(number, split="train"):
    segments = [{"source": "question", "text": "公司利润增长，政策支持"}]
    return {"item_id": format(number, "064x"), "source_text_sha256": canonical_hash(segments),
            "segments": segments, "stage": "question", "split": split,
            "stock_code": str(number).zfill(6), "qa_id": f"source-{number}", "selection_stratum": "earnings"}


def make_event(kind="earnings", quote="利润增长"):
    text = "公司利润增长，政策支持"
    start = text.index(quote)
    return {"event_type": kind, "direction": "positive", "affected_industries": [],
            "horizon": "unknown", "intensity": 0.5, "uncertainty": 0.2,
            "evidence_spans": [{"source": "question", "start": start, "end": start + len(quote), "quote": quote}]}


def label(item, reviewer, events, status="labeled"):
    return {"item_id": item["item_id"], "source_text_sha256": item["source_text_sha256"],
            "annotator_id": reviewer if status != "unlabeled" else None,
            "status": status, "events": events, "notes": ""}


class ReviewWorkflowTest(unittest.TestCase):
    def test_blinded_packet_contains_only_visible_text_and_binding(self):
        task = blinded_task(make_item(1))
        self.assertEqual(set(task), {"item_id", "source_text_sha256", "stage", "segments"})
        self.assertEqual(task["segments"][0]["text"], "公司利润增长，政策支持")

    def test_agreement_conflicts_pending_and_kappa(self):
        samples = [make_item(1), make_item(2, "validation"), make_item(3, "test")]
        items = {item["item_id"]: item for item in samples}
        a = [label(samples[0], "human-a", []), label(samples[1], "human-a", [make_event()]),
             label(samples[2], "human-a", [])]
        b = [label(samples[0], "human-b", []), label(samples[1], "human-b", []),
             label(samples[2], None, [], "unlabeled")]
        result, conflicts, pending = compare_reviewers(items, a, b)
        self.assertEqual(result["status"], "needs_adjudication")
        self.assertEqual((result["dual_reviewed_items"], result["conflict_items"], result["pending_items"]),
                         (2, 1, 1))
        self.assertEqual(result["exact_event_agreement"], 0.5)
        self.assertEqual(result["event_presence_kappa"], 0.0)
        self.assertEqual(conflicts[0]["reason_codes"], ["event_presence"])
        self.assertEqual(pending[0]["reviewer_b_status"], "unlabeled")
        self.assertFalse(result["gold_ready"])

    def test_multievent_order_does_not_create_false_conflict(self):
        sample = make_item(1)
        items = {sample["item_id"]: sample}
        events = [make_event(), make_event("regulation", "政策支持")]
        a = [label(sample, "human-a", events)]
        b = [label(sample, "human-b", list(reversed(copy.deepcopy(events))))]
        result, conflicts, pending = compare_reviewers(items, a, b)
        self.assertEqual((conflicts, pending), ([], []))
        self.assertEqual(result["exact_event_agreement"], 1.0)
        self.assertIsNone(result["event_presence_kappa"])
        self.assertEqual(result["status"], "agreement_complete_requires_final_approval")
        self.assertFalse(result["gold_ready"])

    def test_unreviewed_and_identity_or_source_errors(self):
        sample = make_item(1)
        items = {sample["item_id"]: sample}
        blank = [label(sample, None, [], "unlabeled")]
        result, _, pending = compare_reviewers(items, blank, blank)
        self.assertEqual(result["status"], "no_dual_review")
        self.assertEqual(len(pending), 1)
        self.assertIsNone(cohens_kappa([]))
        a = [label(sample, "same-person", [])]
        with self.assertRaisesRegex(ValueError, "different annotator"):
            compare_reviewers(items, a, [label(sample, "same-person", [])])
        wrong = [label(sample, "human-b", [])]
        wrong[0]["source_text_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "source text hash"):
            compare_reviewers(items, a, wrong)

    def test_finalization_requires_full_dual_review_separate_signer_and_conflict_reason(self):
        samples = [make_item(1), make_item(2)]
        items = {item["item_id"]: item for item in samples}
        a = [label(samples[0], "human-a", []), label(samples[1], "human-a", [make_event()])]
        b = [label(samples[0], "human-b", []), label(samples[1], "human-b", [])]
        comparison, _, _ = compare_reviewers(items, a, b)
        final = [label(samples[0], "adjudicator", []), label(samples[1], "adjudicator", [make_event()])]
        with self.assertRaisesRegex(ValueError, "written adjudication"):
            validate_adjudication(items, comparison, final)
        final[1]["notes"] = "Reviewed both evidence spans and resolved the event presence disagreement."
        self.assertEqual(validate_adjudication(items, comparison, final)["resolved_conflicts"], 1)
        same_signer = copy.deepcopy(final)
        for row in same_signer:
            row["annotator_id"] = "human-a"
        with self.assertRaisesRegex(ValueError, "distinct"):
            validate_adjudication(items, comparison, same_signer)
        incomplete = copy.deepcopy(comparison)
        incomplete["dual_reviewed_items"] = 1
        with self.assertRaisesRegex(ValueError, "complete independent dual review"):
            validate_adjudication(items, incomplete, final)

    def test_synthetic_full_review_comparison_and_gold_export(self):
        samples = [make_item(1), make_item(2)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "pack"
            pack.mkdir()
            items_path = pack / "annotation_items.jsonl"
            items_path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in samples), encoding="utf-8")
            (pack / "annotation_manifest.json").write_text(json.dumps({
                "pipeline_version": PACK_VERSION, "experiment_id": "synthetic-pack",
                "counts": {"items": len(samples)},
                "artifacts": {"annotation_items.jsonl": {"sha256": file_sha256(items_path)}}}), encoding="utf-8")
            a_path, b_path = root / "a.jsonl", root / "b.jsonl"
            a = [label(samples[0], "human-a", []), label(samples[1], "human-a", [make_event()])]
            b = [label(samples[0], "human-b", []), label(samples[1], "human-b", [])]
            for path, rows in ((a_path, a), (b_path, b)):
                path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
            compared = run_comparison(pack, a_path, b_path, root / "comparison")
            self.assertEqual(compared["conflict_items"], 1)
            final = [label(samples[0], "adjudicator", []), label(samples[1], "adjudicator", [make_event()])]
            final[1]["notes"] = "Evidence supports the event after reviewing both decisions."
            final_path = root / "final.jsonl"
            final_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in final), encoding="utf-8")
            result = finalize_labels(pack, root / "comparison", final_path, root / "gold")
            self.assertEqual(result["status"], "all_items_adjudicated")
            self.assertEqual(result["resolved_conflicts"], 1)
            self.assertEqual(len((root / "gold/gold_labels.jsonl").read_text(encoding="utf-8").splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
