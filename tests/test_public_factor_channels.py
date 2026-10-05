import copy
import unittest
from datetime import date

import test_issuer_valuation
from research.simulation.audit_own_price_feedback import verify_final_resources
from research.simulation.audit_pre_wuhan_allocation_attribution_2019 import initial_from_specs, independent_covariance
from research.simulation.audit_public_market_factor import verify_panel
from research.simulation.audit_public_factor_channels import verify_day, verify_same_state, verify_specs, submitted_orders
from research.simulation.portfolio_audit import audit_portfolio_day
from research.simulation.portfolio_price_feedback_channels import simulate_portfolio as old_simulate
from research.simulation.portfolio_public_factor_channels import simulate_portfolio
from research.simulation.public_market_factor import build_features, build_path, feature, validate_path
from research.simulation.public_factor_channels import MODES, same_state_orders, validate_controls


def fixture():
    args, shocks, industry, response, source, _, issuer = test_issuer_valuation.fixture()
    for index, rows in enumerate(source.values()):
        for j, row in enumerate(rows):
            row.market_return = ((j % 5) - 2) / 100
            row.benchmark_id = "sh.000300"
            row.stock_return = (index + 1) * row.market_return
    parameters = {"history_window": 3, "benchmark_id": "sh.000300", "belief_scale_bps": 1000,
                  "max_shift_bps": 500, "risk_multiplier": 1.0, "innovation_seed": "fixture-common"}
    features = build_features(source, args[0], parameters)
    return args, shocks, industry, response, source, issuer, build_path(features, parameters)


def run(inputs, anchor, scale, mode):
    args, shocks, industry, response, _, issuer, factor = inputs
    return simulate_portfolio(*args, scenario_shocks=shocks, industry_shocks=industry, background_response=response,
        issuer_valuation=issuer, quantity_controls=anchor, target_feedback_scale=scale, quote_feedback_scale=scale,
        public_factor_path=factor if mode != "none" else None, public_factor_controls=MODES[mode])


def initial(args, specs):
    return initial_from_specs(specs, sorted(args[0]), {"core": args[2], "background": args[3], "venue": args[4]}, args[6])


class PublicFactorTest(unittest.TestCase):
    def test_hand_beta_positive_negative_and_zero_exposure_are_not_missing(self):
        history = [{"trade_date": f"2020-06-{24+i:02d}", "market_return": x, "stock_return": 2*x, "benchmark_id": "sh.000300"}
                   for i, x in enumerate((-0.01, 0.0, 0.01))]
        for beta in (2, -2, 0):
            rows = [{**r, "stock_return": beta * r["market_return"]} for r in history]
            result = feature(rows, "2020-07-01", "2020-06-26", 3, "sh.000300")
            self.assertEqual([beta, 1], result["beta_fraction"])
        for r in history:
            r["market_return"] = 0.0
        with self.assertRaisesRegex(ValueError, "zero benchmark variance"):
            feature(history, "2020-07-01", "2020-06-26", 3, "sh.000300")

    def test_raw_moments_independently_rebuild_every_feature_and_common_draw(self):
        args, _, _, _, source, _, factor = fixture()
        self.assertEqual(12, verify_panel(factor, source, args[0], factor["parameters"]))
        rows = list(factor["by_stock"].values())
        for i in range(4):
            self.assertEqual(1, len({r[i]["innovation_sha256"] for r in rows}))
            self.assertEqual(1, len({r[i]["standardized_innovation"] for r in rows}))
        self.assertNotEqual(rows[0][0]["valuation_shift_bps"], rows[1][0]["valuation_shift_bps"])

    def test_missing_cutoff_duplicate_dates_and_conflicting_benchmark_fail_explicitly(self):
        args, _, _, _, source, _, factor = fixture()
        for change in ("missing", "duplicate", "benchmark"):
            altered = copy.deepcopy(source)
            stock = next(iter(altered))
            if change == "missing":
                cutoff = date.fromisoformat(args[0][stock][0]["signal_cutoff_date"])
                altered[stock] = [r for r in altered[stock] if r.trade_date != cutoff]
            elif change == "duplicate":
                altered[stock].append(copy.deepcopy(altered[stock][0]))
            else:
                for r in altered[stock]:
                    r.market_return += 0.01
            with self.assertRaises(ValueError):
                build_features(altered, args[0], factor["parameters"])

    def test_future_source_poison_and_target_text_poison_do_not_change_eligible_information(self):
        args, _, _, _, source, _, factor = fixture()
        altered = copy.deepcopy(source)
        earliest = args[0][next(iter(args[0]))][0]["signal_cutoff_date"]
        for rows in altered.values():
            for r in rows:
                if r.trade_date.isoformat() > earliest:
                    r.stock_return, r.market_return = 0.4, 0.2 + (r.trade_date.day % 7) / 100
        rebuilt = build_features(altered, args[0], factor["parameters"])
        original = build_features(source, args[0], factor["parameters"])
        for stock in original:
            self.assertEqual(original[stock][0], rebuilt[stock][0])
        poisoned = copy.deepcopy(args[0])
        for steps in poisoned.values():
            for step in steps:
                step.update(observed_return=-0.99, text_signal=0.98, text_uncertainty=0.98, text_evidence="future-forbidden")
        self.assertEqual(original, build_features(source, poisoned, factor["parameters"]))
        inputs = fixture()
        new_args = list(inputs[0])
        new_args[0] = poisoned
        poisoned_inputs = (new_args, *inputs[1:])
        for mode in MODES:
            self.assertEqual(run(inputs, None, 1, mode), run(poisoned_inputs, None, 1, mode))

    def test_factor_rejects_forged_moments_future_history_boolean_numbers_and_cap_flag(self):
        args, _, _, _, _, _, factor = fixture()
        calendar = [(s["trade_date"], s["signal_cutoff_date"], s["execution_reference_date"]) for s in next(iter(args[0].values()))]
        for key, value in (("beta_fraction", [True, 1]), ("was_capped", 1), ("raw_shift_bps", float("nan")), ("history_sha256", "fake")):
            altered = copy.deepcopy(factor)
            next(iter(altered["by_stock"].values()))[0][key] = value
            with self.assertRaises(ValueError):
                validate_path(sorted(args[0]), calendar, altered)
        for c in ({"strategy_target_scale": True, "quote_scale": 1}, {"strategy_target_scale": 0, "quote_scale": 0},
                  {"strategy_target_scale": 1, "quote_scale": 1, "hidden_tuning": 0}):
            with self.assertRaises(ValueError):
                validate_controls(c)

    def test_none_cell_reproduces_all_four_complete_original_objects(self):
        inputs = fixture()
        args, shocks, industry, response, _, issuer, _ = inputs
        for anchor in (None, {"background_anchor": "current_inventory", "strategy_wait": "original"}):
            for scale in (1.0, 0.0):
                old = old_simulate(*args, scenario_shocks=shocks, industry_shocks=industry, background_response=response,
                    issuer_valuation=issuer, quantity_controls=anchor, target_feedback_scale=scale, quote_feedback_scale=scale)
                self.assertEqual(old, run(inputs, anchor, scale, "none"))

    def test_all_sixteen_cells_keep_private_receipts_and_independent_full_settlement(self):
        inputs = fixture()
        args, shocks, industry, response, _, issuer, factor = inputs
        receipts = None
        for anchor in (None, {"background_anchor": "current_inventory", "strategy_wait": "original"}):
            baseline = run(inputs, anchor, 1, "none")
            for scale in (1.0, 0.0):
                for mode in MODES:
                    result = run(inputs, anchor, scale, mode)
                    specs = result["participant_specs"]
                    verify_specs(specs, baseline["participant_specs"], scale, scale, MODES[mode])
                    state = initial(args, specs)
                    histories = {a: [] for a in args[0]}
                    streaks = {n: {"sign": 0, "streak": 0} for n, s in specs.items() if s["kind"] == "strategy"}
                    current_receipts = [d["issuer_information_receipts"] for d in result["trace"]]
                    if receipts is None:
                        receipts = current_receipts
                    self.assertEqual(receipts, current_receipts)
                    for session, day in enumerate(result["trace"]):
                        independent_covariance(day, histories, args[5])
                        counts = verify_day(day, state, specs, args[4], args[3], response, shocks, industry, issuer,
                            anchor, args[6], streaks, session, scale, scale, factor if mode != "none" else None, MODES[mode])
                        self.assertEqual(12, counts["strategy_allocation_checks"])
                        state = audit_portfolio_day(day, state, args[4], session, dense=True)
                        for a, call in day["portfolio_auction"]["asset_calls"].items():
                            histories[a].append({"trade_date": day["trade_date"], "price_before_minor": call["price_before_minor"], "price_after_minor": call["price_after_minor"]})
                    verify_final_resources(state, result["summary"])

    def test_same_state_quote_only_targets_and_target_only_background_are_exactly_unchanged(self):
        inputs = fixture()
        args, shocks, industry, response, _, issuer, factor = inputs
        for anchor in (None, {"background_anchor": "current_inventory", "strategy_wait": "original"}):
            for scale in (1.0, 0.0):
                result = run(inputs, anchor, scale, "none")
                specs, day = result["participant_specs"], result["trace"][0]
                state = initial(args, specs)
                streaks = {n: {"sign": 0, "streak": 0} for n, s in specs.items() if s["kind"] == "strategy"}
                frozen = copy.deepcopy((day, state, specs, streaks, factor))
                payloads = {}
                for mode, ctrl in MODES.items():
                    payload = same_state_orders(day, state, specs, args[4], args[6], streaks,
                        {a: rows[0] for a, rows in factor["by_stock"].items()}, factor["parameters"], ctrl, args[3], response)
                    payloads[mode] = payload
                    verify_same_state(payload, day, state, specs, args[4], args[3], response, shocks, industry, issuer,
                        anchor, args[6], streaks, 0, scale, scale, factor, ctrl)
                self.assertEqual(frozen, (day, state, specs, streaks, factor))
                self.assertEqual(submitted_orders(day), payloads["none"]["orders"])
                self.assertNotEqual(payloads["none"]["orders"], payloads["quotes_only"]["orders"])
                self.assertNotEqual(payloads["none"]["decisions"], payloads["targets_only"]["decisions"])

    def test_independent_auditor_rejects_tampered_public_quote_and_background_quantity(self):
        inputs = fixture()
        args, shocks, industry, response, _, issuer, factor = inputs
        result = run(inputs, None, 1, "both")
        specs, state = result["participant_specs"], initial(args, result["participant_specs"])
        for change in ("quote", "background", "factor", "receipt"):
            day = copy.deepcopy(result["trace"][0])
            stock = sorted(args[0])[0]
            if change == "quote":
                next(iter(day["decisions"].values()))["public_quote_shift_bps"][stock] += 1
            elif change == "background":
                next(iter(day["background_demands"][stock].values()))["target_shares"] += 100
            elif change == "factor":
                day["observations"][stock]["public_factor"]["history_sha256"] = "fake"
            else:
                name = next(iter(day["issuer_information_receipts"]))
                asset = next(iter(day["issuer_information_receipts"][name]))
                day["issuer_information_receipts"][name][asset]["received"] = 1
            streaks = {n: {"sign": 0, "streak": 0} for n, s in specs.items() if s["kind"] == "strategy"}
            with self.assertRaises(ValueError):
                verify_day(day, state, specs, args[4], args[3], response, shocks, industry, issuer,
                           None, args[6], streaks, 0, 1, 1, factor, MODES["both"])
