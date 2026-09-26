import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from research.data_pipeline.provenance import file_sha256
from research.workbench import build as workbench_build
from research.workbench.build import render_report, validate_public_payload
from research.workbench.run import CONFIG, read_public_job, run_selected, selected_config
from research.workbench.serve import create_server, validate_site


class WorkbenchPayloadTest(unittest.TestCase):
    def test_summary_only_payload_accepts_public_metrics_and_sources(self):
        validate_public_payload({"events": [{"car": -0.04, "stock_code": "000001"}],
                                 "evidence": [{"url": "https://example.org/notice"}]})

    def test_private_text_identifiers_and_host_paths_fail(self):
        for payload in ({"question_text": "private"},
                        {"nested": [{"raw_response": "private"}]},
                        {"path": r"D:\rain\private.xlsx"},
                        {"value": "wxid_private_identifier"}):
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "private"):
                validate_public_payload(payload)

    def test_report_renders_only_summary_fields(self):
        report = render_report({
            "context": "summary",
            "overview": {"question_rows": 1, "stocks": 2, "market_sessions": 3,
                         "integrity_passed": 1, "reexecution_passed": 1, "run_total": 1},
            "events": [{"event_id": "event", "stock_code": "000001", "event_date": "2020-01-01", "car": 0.01}],
            "prediction": {"rows": 1, "market_mae": 0.02, "text_mae": 0.01, "paired_difference": -0.01,
                           "interval_95": [-0.02, 0.01]},
            "replays": [{"period": "Q1", "stock_code": "000001", "role": "role", "signal_multiple": 1.0,
                         "control_multiple": 1.0, "buyhold_multiple": 1.0, "max_drawdown": 0.1, "trades": 1}],
            "runs": [{"id": "run", "integrity": "passed", "reexecution": "equivalent", "hash_checks": 2,
                      "compared_artifacts": 1}],
            "semantic": {"items": 1, "dual_reviewed": 0, "pending": 1, "conflicts": 0, "status": "no_dual_review"},
            "evidence": [{"label": "source", "url": "https://example.org/source"}],
            "limitations": ["limit"],
        })
        self.assertIn("# MarketMirror 研究摘要报告", report)
        self.assertNotIn("question_text", report)
        self.assertNotIn("wxid_", report)

    def test_selected_run_uses_only_catalog_input(self):
        selected = selected_config(CONFIG, "observed_event")
        self.assertEqual([item["run_id"] for item in selected["runs"]], ["observed_event"])
        self.assertEqual(Path(selected["runs"][0]["input"]),
                         CONFIG.parent / "observed_pilot_2020.json")
        with self.assertRaisesRegex(ValueError, "absent"):
            selected_config(CONFIG, "../../private-script")

    def test_single_run_persists_selection_and_result_hashes(self):
        def fake_reexecute(config_path, output_dir):
            output_dir.mkdir()
            (output_dir / "reexecution_results.json").write_text(json.dumps({"runs": [
                {"run_id": "observed_event", "status": "equivalent", "artifacts":
                 [{"artifact": "event_results.json", "status": "identical"}]}]}), encoding="utf-8")
            (output_dir / "reexecution_report.md").write_text("verified", encoding="utf-8")
            artifacts = {name: {"sha256": file_sha256(output_dir / name)} for name in
                         ("reexecution_results.json", "reexecution_report.md")}
            (output_dir / "reexecution_manifest.json").write_text(json.dumps({"artifacts": artifacts}), encoding="utf-8")
            self.assertEqual(json.loads(config_path.read_text(encoding="utf-8"))["runs"][0]["run_id"],
                             "observed_event")
            return {"runs": [{"status": "equivalent", "artifacts":
                              [{"artifact": "event_results.json", "status": "identical"}]}]}

        with tempfile.TemporaryDirectory() as tmp, patch("research.workbench.run.reexecute", fake_reexecute):
            output_root = Path(tmp)
            record = run_selected("observed_event", output_root)
            job_dir = output_root / record["job_id"]
            self.assertEqual(record["status"], "passed")
            self.assertEqual(record["compared_artifacts"], 1)
            self.assertEqual(record["selected_config_sha256"], file_sha256(job_dir / "selected_config.json"))
            self.assertEqual(json.loads((job_dir / "job_status.json").read_text(encoding="utf-8"))["status"], "passed")
            self.assertEqual(read_public_job(output_root, record["job_id"])["status"], "passed")
            (job_dir / "results/reexecution_results.json").write_text("tampered", encoding="utf-8")
            self.assertEqual(read_public_job(output_root, record["job_id"])["status"], "record_changed")

    def test_local_server_rejects_cross_origin_and_unlisted_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            site = root / "site"
            site.mkdir()
            artifacts = {}
            for name in ("index.html", "app.js", "data.js", "style.css", "report.md"):
                (site / name).write_text(name, encoding="utf-8")
                artifacts[name] = {"sha256": file_sha256(site / name)}
            (site / "workbench_manifest.json").write_text(json.dumps({
                "artifacts": artifacts,
                "integrity_catalog_sha256": file_sha256(workbench_build.CONFIG),
                "code_sha256": file_sha256(Path(workbench_build.__file__)),
            }), encoding="utf-8")
            validate_site(site)
            server = create_server(site, root / "jobs", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_port
                conn = http.client.HTTPConnection("127.0.0.1", port)
                conn.request("GET", "/api/capabilities")
                self.assertIn("observed_event", json.loads(conn.getresponse().read())["runs"])
                body = json.dumps({"run_id": "observed_event"})
                conn.request("POST", "/api/jobs", body, {"Content-Type": "application/json"})
                response = conn.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                conn.request("POST", "/api/jobs", json.dumps({"run_id": "unknown"}),
                             {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"})
                response = conn.getresponse()
                self.assertEqual(response.status, 400)
                response.read()
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
            (site / "app.js").write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "changed"):
                validate_site(site)
            (site / "app.js").write_text("app.js", encoding="utf-8")
            stale = json.loads((site / "workbench_manifest.json").read_text(encoding="utf-8"))
            stale["integrity_catalog_sha256"] = "0" * 64
            (site / "workbench_manifest.json").write_text(json.dumps(stale), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "another catalog"):
                validate_site(site)


if __name__ == "__main__":
    unittest.main()
