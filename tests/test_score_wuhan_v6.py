import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from research.data_pipeline.provenance import file_sha256
from research.semantic.annotation_pack import canonical_hash
from research.semantic.run_model import _raw_row
from research.semantic import score_wuhan_v6 as target


class WuhanV6ScoreTest(unittest.TestCase):
    def test_normalized_archive_reconstructs_from_all_raw_responses(self):
        items = {}
        for index in range(250):
            item_id = f"item-{index:03d}"
            segments = [{"source": "question", "text": "近期进展？"},
                        {"source": "reply", "text": f"公司回复编号{index}。"}]
            items[item_id] = {"item_id": item_id, "stock_code": f"{index:06d}",
                              "stage": "reply", "segments": segments,
                              "source_text_sha256": canonical_hash(segments)}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack_dir = root / "research_outputs" / "source"
            raw_dir = root / "research_outputs" / "raw"
            review_dir = root / "research_outputs" / "review"
            normal_dir = root / "research_outputs" / "normal"
            for path in (pack_dir, raw_dir, review_dir):
                path.mkdir(parents=True)
            for directory_path, name in (
                (pack_dir, "annotation_manifest.json"),
                (pack_dir, "annotation_items.jsonl"),
                (review_dir, target.REVIEW_MANIFEST_NAME),
            ):
                (directory_path / name).write_text("{}\n", encoding="utf-8")
            prompt = root / "prompt.md"
            prompt.write_text("frozen v6", encoding="utf-8")
            raw_path = raw_dir / target.RAW_NAME
            rows = [_raw_row(item, target.MODEL, target.PROMPT_VERSION,
                             '{"events":[]}') for item in items.values()]
            raw_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n"
                                        for row in rows), encoding="utf-8")
            run = {"pipeline_version": target.VERSION_RUN,
                   "generated_at": datetime.now(timezone.utc).isoformat(),
                   "pack_experiment_id": "sixth-pack",
                   "provider_base_url": target.BASE_URL, "model_id": target.MODEL,
                   "prompt_version": target.PROMPT_VERSION, "temperature": 0.0,
                   "input_sha256": {}, "code_sha256": {},
                   "reviewed_items": 250, "review_before_model": True,
                   "requested_rows": 250, "model_rows": 250, "remaining_rows": 0,
                   "request_failures": 0, "raw_bytes": raw_path.stat().st_size,
                   "artifacts": {target.RAW_NAME: {"sha256": file_sha256(raw_path)}}}
            (raw_dir / target.RAW_MANIFEST_NAME).write_text(json.dumps(run), encoding="utf-8")
            review = {"generated_at": (datetime.now(timezone.utc)
                                       - timedelta(days=1)).isoformat()}
            with (patch.object(target, "ROOT", root),
                  patch.object(target, "PROMPT", prompt),
                  patch.object(target, "verify_snapshot",
                               return_value=(items, {"experiment_id": "sixth-pack"})),
                  patch.object(target, "load_review", return_value=([{}] * 250, review)),
                  patch.object(target, "model_inputs", return_value={}),
                  patch.object(target, "model_code_hashes", return_value={})):
                normalized = target.normalize(pack_dir, raw_dir, review_dir, normal_dir)
                self.assertEqual(normalized["parse_errors"], 0)
                _, _, predictions = target.audit_normal(pack_dir, raw_dir,
                                                         review_dir, normal_dir)
                self.assertEqual(len(predictions), 250)
                self.assertTrue(all(not row["events"] for row in predictions))

                prediction_path = normal_dir / target.PREDICTIONS_NAME
                altered = [json.loads(line) for line in prediction_path.read_text(
                    encoding="utf-8").splitlines()]
                altered[0]["parse_error"] = "forged"
                prediction_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n"
                                                  for row in altered), encoding="utf-8")
                manifest_path = normal_dir / target.NORMAL_MANIFEST_NAME
                saved = json.loads(manifest_path.read_text(encoding="utf-8"))
                saved["parse_errors"] = 1
                saved["artifacts"][target.PREDICTIONS_NAME]["sha256"] = file_sha256(
                    prediction_path)
                manifest_path.write_text(json.dumps(saved, ensure_ascii=False) + "\n",
                                         encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "differ from raw reconstruction"):
                    target.audit_normal(pack_dir, raw_dir, review_dir, normal_dir)

    def test_fixed_reference_and_keyword_flow_into_support_gate(self):
        names = ["regulation", "liquidity", "earnings", "governance", "other"]
        items, labels, predictions = {}, [], []
        for index in range(250):
            item_id = f"item-{index:03d}"
            segments = [{"source": "question", "text": "公司近期有什么变化？"},
                        {"source": "reply", "text": f"公司事实编号{index}已经确认。"}]
            digest = canonical_hash(segments)
            items[item_id] = {"item_id": item_id, "stock_code": f"{index:06d}",
                              "stage": "reply", "segments": segments,
                              "source_text_sha256": digest}
            events = []
            if index < 30:
                quote = segments[1]["text"]
                events = [{"event_type": names[index % 5], "direction": "neutral",
                           "affected_industries": [], "horizon": "unknown",
                           "intensity": 0.5, "uncertainty": 0.5,
                           "evidence_spans": [{"source": "reply", "start": 0,
                                               "end": len(quote), "quote": quote}]}]
            labels.append({"item_id": item_id, "source_text_sha256": digest,
                           "annotator_id": "ai:test", "status": "labeled",
                           "events": events, "notes": f"逐条参考判断 {index}"})
            predictions.append({"item_id": item_id, "source_text_sha256": digest,
                                "model_id": target.MODEL,
                                "prompt_version": target.PROMPT_VERSION,
                                "events": events, "parse_error": None})

        def empty_keyword(item):
            return {"item_id": item["item_id"],
                    "source_text_sha256": item["source_text_sha256"],
                    "model_id": "keyword", "prompt_version": "keyword",
                    "events": [], "parse_error": None}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack_dir = root / "research_outputs" / "source"
            raw_dir = root / "research_outputs" / "raw"
            review_dir = root / "research_outputs" / "review"
            normal_dir = root / "research_outputs" / "normal"
            score_dir = root / "research_outputs" / "score"
            for path in (pack_dir, raw_dir, review_dir, normal_dir,
                         root / "research" / "configs"):
                path.mkdir(parents=True)
            for directory_path, filenames in (
                (pack_dir, ("annotation_manifest.json", "annotation_items.jsonl")),
                (raw_dir, (target.RAW_MANIFEST_NAME, target.RAW_NAME)),
                (review_dir, (target.REVIEW_MANIFEST_NAME, "reference_labels.jsonl")),
                (normal_dir, (target.NORMAL_MANIFEST_NAME, target.PREDICTIONS_NAME)),
            ):
                for name in filenames:
                    (directory_path / name).write_text("{}\n", encoding="utf-8")
            (raw_dir / target.RAW_MANIFEST_NAME).write_text(json.dumps({
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "request_failures": 0}), encoding="utf-8")
            (root / "research/configs" / target.UNIVERSE_NAME).write_text(json.dumps({
                "stock_codes": [item["stock_code"] for item in items.values()],
                "selection_audit": {"excluded_company_count": 630}}), encoding="utf-8")
            (root / "research/configs" / target.SNAPSHOT_NAME).write_text("{}\n",
                                                                            encoding="utf-8")
            prompt = root / "prompt.md"
            prompt.write_text("frozen v6", encoding="utf-8")
            review = {"generated_at": (datetime.now(timezone.utc)
                                       - timedelta(days=1)).isoformat(),
                      "audit": {"reviewed_items": 250},
                      "model_outputs_previously_seen": False,
                      "review_before_model_outputs": True}
            with (patch.object(target, "ROOT", root),
                  patch.object(target, "PROMPT", prompt),
                  patch.object(target, "audit_normal",
                               return_value=(items, {"experiment_id": "sixth-pack"}, predictions)),
                  patch.object(target, "load_review", return_value=(labels, review)),
                  patch.object(target, "verify_cohort", return_value={
                      "companies": 250, "selected_pairs_sha256": "selected-hash"}),
                  patch.object(target, "predict_item", side_effect=empty_keyword)):
                result = target.score(pack_dir, raw_dir, review_dir, normal_dir, score_dir)
                audit = target.audit_score(pack_dir, raw_dir, review_dir,
                                           normal_dir, score_dir)
                self.assertTrue(audit["byte_identical_fresh_rescore"])

                report_path = score_dir / target.SCORE_NAME
                report_path.write_bytes(report_path.read_bytes() + b" ")
                manifest_path = score_dir / target.SCORE_MANIFEST_NAME
                saved = json.loads(manifest_path.read_text(encoding="utf-8"))
                saved["artifacts"][target.SCORE_NAME]["sha256"] = file_sha256(
                    report_path)
                manifest_path.write_text(json.dumps(saved, ensure_ascii=False) + "\n",
                                         encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "fresh source reconstruction"):
                    target.audit_score(pack_dir, raw_dir, review_dir, normal_dir, score_dir)

        self.assertTrue(result["research_signal_gate"]["passed"])
        self.assertEqual(result["research_signal_gate"]["reference_positive_replies"], 30)
        self.assertEqual(result["research_signal_gate"]["reference_reply_support_by_type"],
                         {name: 6 for name in names})
        self.assertEqual(result["model"]["event_detection"]["f1"], 1.0)
        self.assertEqual(result["keyword"]["event_detection"]["f1"], 0.0)


if __name__ == "__main__":
    unittest.main()
