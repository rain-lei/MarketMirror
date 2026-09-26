import json
import tempfile
import unittest
from pathlib import Path

from research.data_pipeline.render_financial_dictionary import build_dictionary


class FinancialDictionaryTest(unittest.TestCase):
    def test_render_separates_observed_shape_from_unverified_meaning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog_path = Path(__file__).resolve().parents[1] / "research" / "data_contracts" / "financial_fields.json"
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            names = [(name, "snapshot") for name in catalog["snapshot_metrics"]]
            names += [(name, "row_context") for name in catalog["row_context_metrics"]]
            report = {
                "pipeline_version": "test",
                "source_file": "source.xlsx",
                "source_file_hash": "a" * 64,
                "counts": {"source_rows": 10},
                "fields": [
                    {
                        "field_name": name, "role": role, "missing_count": 2,
                        "numeric_count": 8, "non_numeric_count": 0, "negative_count": 1,
                        "zero_count": 0, "numeric_min": -1, "numeric_max": 3,
                        "verification_status": "unverified",
                    }
                    for name, role in names
                ],
            }
            report_path = root / "financial_quality_report.json"
            report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            output = root / "out"
            dictionary = build_dictionary(report_path, output)
            self.assertEqual(dictionary["scope"]["field_count"], 68)
            self.assertEqual(dictionary["scope"]["all_values_status"], "unverified")
            self.assertEqual(dictionary["fields"][0]["missing_rate"], 0.2)
            self.assertEqual(len(dictionary["unresolved_semantics"]), 6)
            text = (output / "field_dictionary.md").read_text(encoding="utf-8")
            self.assertIn("尚未确认的经济含义", text)
            self.assertNotIn(str(root), text)


if __name__ == "__main__":
    unittest.main()
