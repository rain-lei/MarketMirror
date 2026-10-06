import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from design.server import PlatformStore, create_server
from design.strategy_config import CONFIG, defaults, parameters_digest
from design.strategy_preview import preview_decisions
from research.simulation.agents import AgentParameters
from research.simulation.portfolio_auction import PortfolioAccount
from research.simulation.portfolio_market import portfolio_decision


def payload(**scenario):
    return {"parameters": defaults(), "scenario": {
        "signal": .6, "uncertainty": .05, "market": 0., "volatility": .03,
        "scope": "A", "history": "first", **scenario}}


class StrategyPreviewTest(unittest.TestCase):
    def test_actual_engine_terms_targets_and_confirmation_have_distinct_meanings(self):
        result = preview_decisions(payload())
        self.assertEqual(result["mode"], "strategy_decision_preview")
        self.assertTrue(result["assumptions"]["identical_state_before"])
        self.assertFalse(result["assumptions"]["fills_simulated"])
        self.assertFalse(result["assumptions"]["llm_called"])
        a = result["roles"]["aggressive"]
        self.assertAlmostEqual(a["with_message"]["beliefs"]["A"], .535)
        self.assertAlmostEqual(a["with_message"]["total_target_weight"], .3 + .4 * .535 / 3)
        self.assertAlmostEqual(a["baseline"]["total_target_weight"], .3)
        c = result["roles"]["conservative"]["with_message"]
        self.assertGreater(c["beliefs"]["A"], 0)
        self.assertAlmostEqual(c["total_target_weight"], .15)
        self.assertEqual(c["order_weight_changes"], dict.fromkeys("ABC", 0.))
        self.assertIn("confirmation_or_rebalance_wait", c["reasons"])
        for pair in result["roles"].values():
            for row in pair.values():
                self.assertEqual(row["nav_minor"], 100_000_000)
                self.assertEqual(row["current_weights"], dict.fromkeys("ABC", .05))
            self.assertEqual(pair["baseline"]["terms"]["A"]["text"], 0.)
            self.assertEqual(pair["baseline"]["terms"]["A"]["uncertainty"], 0.)

    def test_each_condition_reuses_the_same_before_memory_and_matches_direct_engine(self):
        data = payload(market=-.2, signal=.4, history="positive", scope="public")
        result = preview_decisions(data)
        model = json.loads(CONFIG.read_text(encoding="utf-8"))
        before = result["state_before"]
        observations = {a: {"market_signal": -.2, "text_signal": .4, "text_uncertainty": .05} for a in "ABC"}
        for spec in model["agent_parameters"]:
            for condition, enabled in (("baseline", False), ("with_message", True)):
                memory = copy.deepcopy(before["memory"])
                account = PortfolioAccount({"shared": before["cash_minor"]}, dict(before["shares"]), dict(before["shares"]))
                actual = portfolio_decision(AgentParameters(**spec), {"momentum_loading": 1., "market_bias": 0.},
                                            account, before["prices_minor"], observations, before["covariance"],
                                            model["portfolio_case"], memory, before["decision_step"] - 1, enabled)
                stored = result["roles"][spec["role"]][condition]
                for field, value in actual.items():
                    self.assertEqual(stored[field], value)
                self.assertEqual(stored["memory_after"], memory)
        self.assertEqual(before["memory"], {"sign": 1, "streak": 2})

    def test_prior_confirmation_can_release_the_conservative_rule(self):
        first = preview_decisions(payload())
        confirmed = preview_decisions(payload(history="positive"))
        c = confirmed["roles"]["conservative"]["with_message"]
        self.assertGreater(c["planned_turnover"], 0.)
        self.assertNotIn("confirmation_or_rebalance_wait", c["reasons"])
        self.assertEqual(first["state_before"]["decision_step"], 1)
        self.assertEqual(confirmed["state_before"]["decision_step"], 4)
        self.assertEqual(first["state_before"]["shares"], confirmed["state_before"]["shares"])

    def test_high_risk_prioritizes_liquidation_even_before_confirmation(self):
        result = preview_decisions(payload(volatility=.3))
        for role in ("conservative", "institutional"):
            decision = result["roles"][role]["with_message"]
            self.assertTrue(decision["risk_liquidation"])
            self.assertIn("risk_liquidation_priority", decision["reasons"])
            self.assertNotIn("confirmation_or_rebalance_wait", decision["reasons"])
            self.assertTrue(all(v < 0 for v in decision["order_weight_changes"].values()))
            self.assertLessEqual(decision["desired_portfolio_risk"], defaults()[role]["risk_budget"] + 1e-12)

    def test_parameter_edits_are_precise_and_do_not_change_no_text_decisions(self):
        data = payload(uncertainty=0.)
        original = preview_decisions(data)
        data["parameters"]["aggressive"]["text_sensitivity"] = 1.35791
        changed = preview_decisions(data)
        self.assertEqual(changed["strategy_parameters_sha256"], parameters_digest(data["parameters"]))
        self.assertEqual(changed["input"]["parameters"]["aggressive"]["text_sensitivity"], 1.35791)
        self.assertEqual(original["roles"]["aggressive"]["baseline"], changed["roles"]["aggressive"]["baseline"])
        self.assertNotEqual(original["roles"]["aggressive"]["with_message"], changed["roles"]["aggressive"]["with_message"])
        data["parameters"]["aggressive"]["text_sensitivity"] = 0.
        pair = preview_decisions(data)["roles"]["aggressive"]
        self.assertEqual(pair["baseline"], pair["with_message"])

    def test_scope_controls_exactly_which_assets_receive_the_text(self):
        single = preview_decisions(payload())["roles"]["aggressive"]["with_message"]
        public = preview_decisions(payload(scope="public"))["roles"]["aggressive"]["with_message"]
        self.assertEqual(single["beliefs"]["B"], 0.)
        self.assertEqual(single["beliefs"]["C"], 0.)
        self.assertEqual(len(set(public["beliefs"].values())), 1)

    def test_repeated_previews_cannot_mutate_input_or_frozen_model(self):
        raw = CONFIG.read_bytes()
        data = payload(history="negative", signal=-.6)
        before = copy.deepcopy(data)
        self.assertEqual(preview_decisions(data), preview_decisions(data))
        self.assertEqual(data, before)
        self.assertEqual(CONFIG.read_bytes(), raw)

    def test_invalid_values_and_incomplete_requests_are_rejected(self):
        invalid = [None, [], {}, {**payload(), "source": "unwanted text"}]
        for field, value in (("signal", True), ("signal", 1.1), ("uncertainty", -1),
                             ("market", "0"), ("volatility", float("nan")),
                             ("volatility", 0), ("history", []), ("scope", "B")):
            data = payload()
            data["scenario"][field] = value
            invalid.append(data)
        data = payload()
        del data["scenario"]["history"]
        invalid.append(data)
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                preview_decisions(data)

    def test_http_preview_is_available_without_persisting_or_changing_working_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            server = create_server(directory, 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            def request(data, origin=base):
                req = Request(base + "/api/platform/strategy-preview", data=json.dumps(data).encode(),
                              headers={"Content-Type": "application/json", "Origin": origin})
                try:
                    response = urlopen(req, timeout=10)
                except HTTPError as exc:
                    response = exc
                with response:
                    return response.status, json.loads(response.read())
            try:
                before = PlatformStore(directory).strategies()
                data = payload()
                data["parameters"]["aggressive"]["text_sensitivity"] = 1.2345
                code, result = request(data)
                self.assertEqual(code, 200)
                self.assertEqual(result, preview_decisions(data))
                self.assertEqual(request({})[0], 400)
                self.assertEqual(request(data, "https://example.invalid")[0], 403)
                self.assertEqual(PlatformStore(directory).strategies(), before)
                self.assertEqual(list(directory.glob("*/experiment.json")), [])
                self.assertEqual(list(directory.glob("analyses/*.json")), [])
                with urlopen(base + "/strategy-preview.js", timeout=10) as response:
                    self.assertEqual(response.status, 200)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
