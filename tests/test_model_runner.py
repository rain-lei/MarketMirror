import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.semantic.run_model import run_model


class ModelRunnerTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
