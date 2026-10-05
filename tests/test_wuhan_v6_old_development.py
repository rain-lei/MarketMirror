import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.data_pipeline.provenance import file_sha256
from research.semantic import wuhan_v6_old_development as adapter


class WuhanV6OldDevelopmentTest(unittest.TestCase):
    def test_identity_payload_and_runner_restoration(self):
        runner = adapter.runner
        original_version = runner.VERSION
        original_preflight = runner.preflight
        items = {
            "one": {"item_id": "one", "stock_code": "000001",
                    "source_text_sha256": "a" * 64,
                    "segments": [{"source": "question", "text": "提问一"},
                                 {"source": "reply", "text": "回复一"}]},
        }
        calls = []

        def fake_preflight():
            inputs = {"v5_candidate_prompt": file_sha256(runner.PROMPT)}
            return items, [], {"experiment_id": "legacy-test",
                               "source_experiment_id": "source-test",
                               "inputs": inputs, "code": runner._code_hashes()}

        def fake_completion(url, key, model, messages, timeout, retries, temperature):
            calls.append(messages)
            return '{"events":[]}'

        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory)
            with (patch.object(runner, "OUTPUTS", output_root),
                  patch.object(runner, "preflight", side_effect=fake_preflight),
                  patch.object(runner, "_code_hashes", return_value={"legacy": "hash"}),
                  patch.object(runner, "verify_v3_pack",
                               return_value=({}, {"experiment_id": "v3-test"})),
                  patch.object(runner, "request_completion", side_effect=fake_completion)):
                with adapter.configured_runner() as active:
                    _, _, identity = active.preflight()
                    self.assertEqual(active.VERSION, adapter.VERSION)
                    self.assertEqual(identity["inputs"], {
                        "v6_candidate_prompt": file_sha256(adapter.PROMPT)})
                    self.assertNotIn("v5_candidate_prompt", identity["inputs"])
                    self.assertEqual(identity["code"][adapter.MODULE.name],
                                     file_sha256(adapter.MODULE))
                    manifest = active.run_raw(output_root / "mock", api_key="mock-only")

            self.assertEqual(runner.VERSION, original_version)
            self.assertIs(runner.preflight, original_preflight)
            self.assertEqual(manifest["experiment_id"], identity["experiment_id"])
            self.assertEqual(manifest["sample_role"], "seen_development_only")
            self.assertEqual(manifest["model_rows"], 1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(json.loads(calls[0][1]["content"]),
                             {"segments": items["one"]["segments"]})
            self.assertNotIn("stock_code", calls[0][1]["content"])


if __name__ == "__main__":
    unittest.main()
