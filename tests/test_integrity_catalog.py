import json
import tempfile
import unittest
from pathlib import Path

from research.data_pipeline.provenance import file_sha256
from research.registry.verify_catalog import audit_run, load_catalog


class IntegrityCatalogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.research = root / "research"
        self.catalog_dir = self.research / "configs"
        self.output_root = root / "research_outputs"
        self.run_dir = self.output_root / "demo"
        for path in (self.catalog_dir, self.run_dir):
            path.mkdir(parents=True)
        self.code = self.research / "sample.py"
        self.source = root / "source.csv"
        self.artifact = self.run_dir / "result.json"
        self.config = self.catalog_dir / "config.json"
        self.code.write_text("VALUE = 1\n", encoding="utf-8")
        self.source.write_text("value\n1\n", encoding="utf-8")
        self.artifact.write_text('{"result": 1}\n', encoding="utf-8")
        self.config.write_text('{"run": 1}\n', encoding="utf-8")
        self.manifest_path = self.run_dir / "manifest.json"
        self.manifest = {"artifacts": {"result.json": {"sha256": file_sha256(self.artifact)}},
                         "inputs": {str(self.source): file_sha256(self.source)},
                         "code_sha256": {"sample.py": file_sha256(self.code)},
                         "config_sha256": file_sha256(self.config)}
        self.save_manifest()

    def save_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.run = {"run_id": "demo", "manifest": "../../research_outputs/demo/manifest.json",
                    "manifest_sha256": file_sha256(self.manifest_path),
                    "extra_hashes": {"config_sha256": "config.json"}}

    def audit(self):
        return audit_run(self.run, self.catalog_dir, self.research, self.output_root, {})

    def test_checks_inputs_code_artifact_extra_and_manifest_pin(self):
        result = self.audit()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["checks"], {"artifacts": 1, "inputs": 1, "code": 1, "extra": 1})
        self.artifact.write_text('{"result": 2}\n', encoding="utf-8")
        changed = self.audit()
        self.assertEqual(changed["status"], "failed")
        self.assertTrue(any("artifacts: hash mismatch" in issue for issue in changed["failures"]))
        self.manifest["artifacts"]["result.json"]["sha256"] = file_sha256(self.artifact)
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        changed = self.audit()
        self.assertTrue(any("manifest hash differs" in issue for issue in changed["failures"]))

    def test_code_and_input_changes_are_detected(self):
        self.code.write_text("VALUE = 2\n", encoding="utf-8")
        self.source.write_text("value\n2\n", encoding="utf-8")
        failures = self.audit()["failures"]
        self.assertTrue(any("code: hash mismatch" in issue for issue in failures))
        self.assertTrue(any("inputs: hash mismatch" in issue for issue in failures))

    def test_artifact_path_cannot_escape_run_directory(self):
        self.manifest["artifacts"] = {"../source.csv": {"sha256": file_sha256(self.source)}}
        self.save_manifest()
        failures = self.audit()["failures"]
        self.assertTrue(any("artifact path escapes" in issue for issue in failures))

    def test_catalog_rejects_duplicate_runs(self):
        path = self.catalog_dir / "catalog.json"
        path.write_text(json.dumps({"runs": [self.run, self.run]}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            load_catalog(path)


if __name__ == "__main__":
    unittest.main()
