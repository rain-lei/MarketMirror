import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from research.data_pipeline.provenance import file_sha256
from research.workbench import build as workbench_build
from research.workbench.build import checked_agent_signal_gate, render_report, validate_public_payload
from research.workbench.run import CONFIG, read_public_job, run_selected, selected_config
from research.workbench.serve import create_server, validate_site


class WorkbenchPayloadTest(unittest.TestCase):
    def test_agent_signal_gate_waits_for_ai_review_and_score(self):
        result = checked_agent_signal_gate(
            {"gold_ready": False, "reviewed_items": 0, "items": 128},
            {"items": 128, "request_failures": 0},
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["status"], "awaiting_assistant_review_or_score")
        self.assertIn("不再要求双人", result["reason"])
        result = checked_agent_signal_gate(
            {"reviewed_items": 128, "scoring": {"passed": True}},
            {"items": 128, "request_failures": 0})
        self.assertTrue(result["passed"])
        self.assertFalse(result["gold_ready"])

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
                         "market_periods": [{"start": "2018-01-02", "end": "2018-01-03", "sessions": 2},
                                            {"start": "2020-01-02", "end": "2020-01-02", "sessions": 1}],
                         "integrity_passed": 1, "reexecution_passed": 1, "run_total": 1},
            "events": [{"event_id": "asset_management_guidance_date_only", "stock_code": "000001",
                        "event_date": "2018-05-02", "car": -0.09, "window_before": 3, "window_after": 5},
                       {"event_id": "event", "stock_code": "000001", "event_date": "2020-01-01",
                        "car": 0.01, "window_before": 3, "window_after": 5}],
            "independent_quotes": [{"period": "2018", "event_id": "asset_management_guidance_date_only",
                                    "stock_code": "000001", "baostock_car": -0.09,
                                    "eastmoney_unadjusted_car": -0.089,
                                    "car_difference_pp": 0.1,
                                    "event_window_max_absolute_difference_pp": 0.004,
                                    "estimation_basis_exception": False}],
            "review_readiness": None,
            "assistant_review": {"scoring": {"model_f1": 0.89, "keyword_f1": 0.51,
                                               "type_macro_f1": 0.66}},
            "semantic_ablation": {"stocks": 126, "sessions_per_stock": [124],
                                  "comparison_rows": 378, "directional_stocks": 15,
                                  "uncertainty_only_stocks": 29, "no_effect_stocks": 82,
                                  "blocked_execution_days": 18,
                                  "grouped": [{"category": "no_effect", "role": "aggressive", "stocks": 82,
                                               "mean_difference_multiple": 0.0, "positive": 0, "negative": 0}]},
            "semantic_memory": {"stocks": 126, "scenario_count": 20, "comparison_rows": 7560,
                                "grouped": [{"memory_mode": "exponential", "memory_sessions": 5, "lag_days": 7,
                                             "role": "aggressive", "mean_difference_multiple": -0.00012,
                                             "positive": 20, "negative": 24, "unchanged": 82,
                                             "changed_signal_days": 1900}]},
            "semantic_auction": {"stocks": 126, "agents_per_market": 12, "paths": 756, "ledger_rows": 93744,
                                 "audit": {"dense_tick_sweeps": 3917},
                                 "grouped": [{"quote_response_bps": 200, "mean_price_difference_multiple": 0.00012857,
                                              "changed_stock_prices": 23, "text_matched_volume": 220000,
                                              "no_text_matched_volume": 218300}]},
            "semantic_feedback": {"stocks": 126, "paths": 3528, "ledger_rows": 437472,
                                  "conditioned_baseline_parity_paths": 504,
                                  "audit": {"endogenous_input_rows": 374976, "dense_tick_sweeps": 10000},
                                  "grouped": [{"scenario_id": "endogenous_cohort_seed7", "quote_response_bps": 200,
                                               "mean_price_difference_multiple": 0.002, "changed_stock_prices": 30,
                                               "text_accepted_fill_fraction": 0.02, "no_text_accepted_fill_fraction": 0.03}]},
            "semantic_background": {"stocks": 126, "paths": 4032, "ledger_rows": 499968,
                                    "no_background_parity_paths": 504, "idle_resources_parity_paths": 1008,
                                    "audit": {"interval_price_checks": 499968, "sampled_full_tick_sessions": 16128,
                                              "sampled_traded_tick_sweeps": 10000},
                                    "grouped": [{"case_id": "active_quote25", "quote_response_bps": 200,
                                                 "mean_price_difference_multiple": 0.001, "no_text_trading_companies": 126,
                                                 "no_text_strategy_orders": {"accepted_fill_fraction": 0.2},
                                                 "no_text_volume_by_counterparty": {"strategy_background": 10000,
                                                                                   "background_background": 90000}}]},
            "agent_signal_gate": {"status": "eligible_under_ai_review", "passed": True,
                                  "gold_ready": False, "reviewed_items": 128, "required_items": 128,
                                  "adapter_version": "semantic-agent-signal-adapter-v1",
                                  "scope": "受控 Agent 语义信号消融资格；不代表投资者校准、历史因果复现或监管预测。",
                                  "reason": "AI 逐条复核及评分通过，可生成受控实验信号。"},
            "activity": [{"event_id": "asset_management_guidance_date_only", "stock_code": "000001",
                          "amount_fold": 0.781}],
            "placebo": {"asset_management_guidance_date_only": {"before": 150, "after": 27}},
            "prediction": {"rows": 1, "market_mae": 0.02, "text_mae": 0.01, "paired_difference": -0.01,
                           "interval_95": [-0.02, 0.01]},
            "visibility_lag_series": [{"lag_days": 7, "changed_text_feature_rows": 20,
                                       "market_test_mae": 0.02, "text_test_mae": 0.0201,
                                       "paired_mae_difference": 0.0001,
                                       "paired_interval_95": [-0.0005, 0.0006]}],
            "replays": [{"period": "Q1", "stock_code": "000001", "role": "role", "signal_multiple": 1.0,
                         "control_multiple": 1.0, "buyhold_multiple": 1.0, "max_drawdown": 0.1, "trades": 1}],
            "capacity_series": [{"period": "Q1", "stock_code": "000001", "sessions": 58,
                                 "diagnostic_p95_fraction": 0.3, "binding_days": 58,
                                 "aggregate_fill_rate": 0.05,
                                 "aggressive_uncapped_multiple": 0.95,
                                 "aggressive_capped_multiple": 0.94}],
            "counterfactual_series": [{"period": "2020 Q1", "event_id": "wuhan_date_only_conservative",
                                       "stock_code": "000001", "first_signal_trade_date": "2020-02-05",
                                       "impact_coefficient": 0.03,
                                       "event_window_net_order_delta_cny": -258000000,
                                       "event_end_price_delta": -0.6952,
                                       "terminal_price_delta": -0.0065}],
            "lagged_impact_series": [{"period": "2020 Q1", "event_id": "wuhan_date_only_conservative",
                                      "participation_rate": 0.05, "impact_depth_fraction": 0.05,
                                      "impact_coefficient": 0.03,
                                      "event_end_price_delta": -4.4335,
                                      "terminal_price_delta": 1.9206,
                                      "control_binding_days": 58, "event_binding_days": 58}],
            "runs": [{"id": "run", "integrity": "passed", "reexecution": "equivalent", "hash_checks": 2,
                      "compared_artifacts": 1}],
            "semantic": {"items": 128, "reviewed_items": 128, "protocol": "assistant_review_v1",
                         "status": "assistant_review_complete"},
            "holdout_model": {"model_id": "DeepSeek-V4-Flash-0731-W8A8",
                              "prompt_version": "semantic-prompt-v2", "items": 128,
                              "request_failures": 0, "parse_errors": 0,
                              "valid_empty_rows": 80, "valid_event_rows": 48,
                              "validated_events": 57},
            "evidence": [{"label": "source", "url": "https://example.org/source"}],
            "limitations": ["limit"],
        })
        self.assertIn("# MarketMirror 研究摘要报告", report)
        self.assertIn("2018-05-02", report)
        self.assertIn("2020-01-01", report)
        self.assertIn("第二行情源口径敏感性", report)
        self.assertIn("东方财富不复权 CAR", report)
        self.assertNotIn("下半年留出人工审核准备", report)
        self.assertIn("AI 语义复核状态", report)
        self.assertIn("Agent 语义信号接入门槛", report)
        self.assertIn("eligible_under_ai_review", report)
        self.assertNotIn("blocked_until_human_gold", report)
        self.assertIn("[-3, +5]", report)
        self.assertIn("2018-01-02 至 2018-01-03：2 日", report)
        self.assertIn("问答可见时间敏感性", report)
        self.assertIn("+0.0100", report)
        self.assertIn("0.781", report)
        self.assertIn("150", report)
        self.assertIn("资金规模与容量情景", report)
        self.assertIn("58/58", report)
        self.assertIn("已观察收益上的假设冲击", report)
        self.assertIn("-0.6952", report)
        self.assertIn("滞后成交额冲击敏感性", report)
        self.assertIn("+1.9206", report)
        self.assertIn("2020 下半年留出模型状态", report)
        self.assertIn("与 AI 参考一致性", report)
        self.assertIn("非独立人工金标准", report)
        self.assertIn("有限背景交易需求", report)
        self.assertIn("499,968", report)
        self.assertIn("16128", report)
        self.assertIn("active_quote25", report)
        self.assertIn("0.8900", report)
        self.assertIn("126 公司真实收益语义回放", report)
        self.assertIn("378 组公司与 Agent 对照", report)
        self.assertIn("18 个停牌参考日", report)
        self.assertIn("文本记忆与公开延迟敏感性", report)
        self.assertIn("7560 组公司与角色对照", report)
        self.assertIn("-0.012000%", report)
        self.assertIn("所有情景的无文本逐日路径不变", report)
        self.assertIn("有限资金与持仓的集合竞价", report)
        self.assertIn("93,744 条完整日账本", report)
        self.assertIn("3917 次有成交竞价", report)
        self.assertIn("模拟价格反馈与主体差异", report)
        self.assertIn("437,472 条完整日账本", report)
        self.assertIn("374,976 条内生输入", report)
        self.assertIn("endogenous_cohort_seed7", report)
        self.assertNotIn("question_text", report)
        self.assertNotIn("wxid_", report)

    def test_selected_run_uses_only_catalog_input(self):
        selected = selected_config(CONFIG, "observed_event")
        self.assertEqual([item["run_id"] for item in selected["runs"]], ["observed_event"])
        self.assertEqual(Path(selected["runs"][0]["input"]),
                         CONFIG.parent / "observed_pilot_2020.json")
        with self.assertRaisesRegex(ValueError, "absent"):
            selected_config(CONFIG, "../../private-script")
        selected_2018 = selected_config(CONFIG, "observed_event_2018")
        self.assertEqual(Path(selected_2018["runs"][0]["input"]), CONFIG.parent / "observed_pilot_2018.json")

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
                capabilities = json.loads(conn.getresponse().read())
                self.assertIn("observed_event", capabilities["runs"])
                self.assertTrue(capabilities["versions"]["observed_event"]["data_version"].startswith("inputs-"))
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
