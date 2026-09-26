import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.semantic.audit_model_run import audit_model_run
from research.semantic.run_model import list_models, run_model


class ModelRunnerTest(unittest.TestCase):
    def test_model_listing_is_read_only_and_returns_ids(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"data":[{"id":"DeepSeek-V4-Flash-0731-W8A8"},{"id":"other"}]}'

        with patch("research.semantic.run_model.urllib.request.urlopen", return_value=Response()) as opener:
            models = list_models("http://aigw.dlut.edu.cn", "secret")
        self.assertEqual(models, ["DeepSeek-V4-Flash-0731-W8A8", "other"])
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, "http://aigw.dlut.edu.cn/v1/models")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")

    def test_runner_archives_source_bound_raw_response_without_api_key(self):
        item = {"item_id": "item-1", "source_text_sha256": "a" * 64,
                "segments": [{"source": "question", "text": "测试问题"}]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "pack"
            pack.mkdir()
            (pack / "annotation_manifest.json").write_text("{}", encoding="utf-8")
            (pack / "annotation_items.jsonl").write_text(json.dumps(item, ensure_ascii=False) + "\n", encoding="utf-8")
            with patch("research.semantic.run_model.load_pack", return_value=({"item-1": item}, {"experiment_id": "exp-1"})), \
                 patch("research.semantic.run_model.request_completion", return_value='{"events":[]}') as request:
                result = run_model(pack, root / "out", "secret", limit=1)
            request.assert_called_once()
            self.assertEqual(result["model_rows"], 1)
            row = json.loads((root / "out" / "model_raw_outputs.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(set(row), {"item_id", "source_text_sha256", "model_id", "prompt_version", "raw_response"})
            self.assertEqual(row["model_id"], "DeepSeek-V4-Flash-0731-W8A8")
            self.assertEqual(row["raw_response"], '{"events":[]}')
            manifest = json.loads((root / "out" / "model_run_manifest.json").read_text(encoding="utf-8"))
            self.assertNotIn("secret", json.dumps(manifest, ensure_ascii=False))

    def test_resume_only_requests_items_missing_from_existing_output(self):
        items = {
            f"item-{index}": {"item_id": f"item-{index}", "source_text_sha256": str(index) * 64,
                               "segments": [{"source": "question", "text": f"问题 {index}"}]}
            for index in (1, 2)
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "pack"
            pack.mkdir()
            (pack / "annotation_manifest.json").write_text("{}", encoding="utf-8")
            (pack / "annotation_items.jsonl").write_text("\n".join(json.dumps(item, ensure_ascii=False)
                                                            for item in items.values()) + "\n", encoding="utf-8")
            with patch("research.semantic.run_model.load_pack", return_value=(items, {"experiment_id": "exp-2"})), \
                 patch("research.semantic.run_model.request_completion", return_value='{"events":[]}') as request:
                run_model(pack, root / "out", "secret", limit=1)
                request.reset_mock()
                result = run_model(pack, root / "out", "secret", resume=True)
            request.assert_called_once()
            self.assertEqual(result["model_rows"], 2)
            self.assertEqual(result["remaining_rows"], 0)
            rows = [json.loads(line) for line in (root / "out" / "model_raw_outputs.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["item_id"] for row in rows], ["item-1", "item-2"])

    def test_audit_reports_scope_and_blocks_accuracy_claim(self):
        item = {"item_id": "item-1", "source_text_sha256": "a" * 64,
                "segments": [{"source": "question", "text": "测试问题"}]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "pack"
            pack.mkdir()
            (pack / "annotation_manifest.json").write_text("{}", encoding="utf-8")
            (pack / "annotation_items.jsonl").write_text(json.dumps(item, ensure_ascii=False) + "\n", encoding="utf-8")
            with patch("research.semantic.run_model.load_pack", return_value=({"item-1": item}, {"experiment_id": "exp-3"})), \
                 patch("research.semantic.run_model.request_completion", return_value='{"events":[]}'), \
                 patch("research.semantic.audit_model_run.load_pack", return_value=({"item-1": item}, {"experiment_id": "exp-3"})):
                run_model(pack, root / "out", "secret")
                report = audit_model_run(pack, root / "out")
            self.assertEqual(report["raw"]["status"], "complete")
            self.assertTrue(report["scope"]["full_pack_requested"])
            self.assertFalse(report["scoring"]["accuracy_claim_allowed"])
            self.assertEqual(report["normalized"]["status"], "not_provided")


if __name__ == "__main__":
    unittest.main()
