import copy
import unittest
from dataclasses import asdict

from research.simulation.agents import AgentParameters
from research.simulation.audit_background_response import verify_strategy_quotes
from research.simulation.audit_industry_response import audit_industry_grid, verify_industry_delivery, verify_shared_background
from research.simulation.industry_shocks import build_industry_grid, subset_industry_shocks, validate_industry_shocks
from research.simulation.portfolio_audit import audit_portfolio_day
from research.simulation.portfolio_market import initial_portfolio, simulate_portfolio
from test_background_market import CONFIG, CORE, FEEDBACK
from test_semantic_auction_experiment import PARAMETERS, VENUE, sample_steps

DESIGN = {"variants": ["uniform_common", "industry_only_grouped", "industry_only_permuted", "common_plus_industry_grouped", "common_plus_industry_permuted"],
          "pulse_sessions": [1, 2], "pulse_polarities": [1, -1], "total_cross_stock_rms": 0.2,
          "industry_seed": "marketmirror-industry-shared-stress-v1", "permutation_seed": "marketmirror-industry-permutation-control-v1"}
MEMBERSHIP = {f"{index + 1:06d}": ("C13", "J66", "I65", "F51")[index % 4] for index in range(12)}


def grid(sessions=3):
    return build_industry_grid(MEMBERSHIP, sessions, DESIGN["pulse_sessions"], DESIGN["pulse_polarities"],
                               DESIGN["total_cross_stock_rms"], DESIGN["industry_seed"], DESIGN["permutation_seed"])


class IndustryShockTest(unittest.TestCase):
    def fixture(self, cell_index=1):
        codes = ["000001", "000002", "000003"]
        joined = {code: copy.deepcopy(sample_steps()) for code in codes}
        sessions = len(joined[codes[0]])
        cell = grid(sessions)[cell_index]
        agents = [AgentParameters(**row) for row in PARAMETERS]
        background = CONFIG["background_cases"][3]
        case = {"case_id": "industry_test", "cash_mode": "separated", "institutional_asset_cap": 0.35}
        shocks = {"scenario_id": "common_component", "common": cell["common"], "asset_specific": {code: [0.0] * sessions for code in codes}}
        industry = subset_industry_shocks(cell["industry"], codes)
        response = {"valuation_response_bps": 200, "pulse_participation_bps": 10000}
        args = (joined, agents, CORE, background, VENUE, FEEDBACK, case, False)
        return args, shocks, industry, response

    def test_grid_is_dose_matched_with_exact_permutation_multisets_and_real_membership_preserved(self):
        first = grid()
        self.assertEqual(first, grid())
        checks = audit_industry_grid(first, MEMBERSHIP, 3, DESIGN)
        self.assertEqual(checks["independent_asset_session_dose_checks"], 5 * 12 * 3)
        self.assertTrue(checks["paired_daily_shock_multisets_equal"])
        for cell in first[1:]:
            self.assertEqual(cell["industry"]["membership"], MEMBERSHIP)
        altered = copy.deepcopy(first)
        altered[1]["industry"]["by_group"]["C13"][1] *= -1
        with self.assertRaisesRegex(ValueError, "independent group/dose"):
            audit_industry_grid(altered, MEMBERSHIP, 3, DESIGN)

    def test_subset_does_not_recenter_dose_using_one_basket(self):
        full = grid()[3]["industry"]
        subset = subset_industry_shocks(full, ["000001", "000002", "000003"])
        checked = validate_industry_shocks(list(subset["membership"]), 3, subset)
        for group in checked["by_group"]:
            self.assertEqual(checked["by_group"][group], full["by_group"][group])

    def test_actual_beliefs_orders_and_settlement_have_independent_audits(self):
        args, shocks, industry, response = self.fixture(3)
        result = simulate_portfolio(*args, scenario_shocks=shocks, background_response=response, industry_shocks=industry)
        _, accounts, specs, _ = initial_portfolio(args[1], CORE, args[3], list(args[0]), args[6], VENUE)
        state = {"accounts": {name: asdict(account) for name, account in accounts.items()},
                 "prices": {stock: VENUE["price_start_minor"] for stock in args[0]}, "fee_pool_minor": 0,
                 "initial_cash_minor": sum(sum(account.wallets.values()) for account in accounts.values()),
                 "initial_shares": {stock: sum(account.shares[stock] for account in accounts.values()) for stock in args[0]}}
        for session, day in enumerate(result["trace"]):
            verify_industry_delivery(day, specs, shocks, industry, session)
            verify_strategy_quotes(day, state, VENUE, specs)
            for stock in args[0]:
                group = industry["path_groups"][stock]
                verify_shared_background(day, state, stock, session, VENUE, args[3], shocks["common"][session],
                                         industry["by_group"][group][session], industry["membership"][stock], group, response, specs)
            state = audit_portfolio_day(day, state, VENUE, session, dense=True)
        self.assertEqual(state["prices"], result["summary"]["final_prices_minor"])
        tampered = copy.deepcopy(result["trace"][1])
        tampered["observations"]["000001"]["industry_shock"] += 0.1
        with self.assertRaisesRegex(ValueError, "not delivered"):
            verify_industry_delivery(tampered, specs, shocks, industry, 1)

    def test_no_industry_preserves_defaults_and_outcomes_unused_by_agents(self):
        args, shocks, industry, response = self.fixture()
        legacy = simulate_portfolio(*args, scenario_shocks=shocks, background_response=response)
        self.assertEqual(legacy, simulate_portfolio(*args, scenario_shocks=shocks, background_response=response, industry_shocks=None))
        original = simulate_portfolio(*args, scenario_shocks=shocks, background_response=response, industry_shocks=industry)
        changed = copy.deepcopy(args[0])
        for steps in changed.values():
            for step in steps:
                step.update(observed_return=0.99, text_signal=0.8, text_uncertainty=0.7, text_evidence="unused text")
        self.assertEqual(original, simulate_portfolio(changed, *args[1:], scenario_shocks=shocks, background_response=response, industry_shocks=industry))

    def test_future_industry_changes_cannot_change_earlier_trace(self):
        args, shocks, industry, response = self.fixture()
        original = simulate_portfolio(*args, scenario_shocks=shocks, background_response=response, industry_shocks=industry)
        later = copy.deepcopy(industry)
        for values in later["by_group"].values():
            values[2] *= -1
        altered = simulate_portfolio(*args, scenario_shocks=shocks, background_response=response, industry_shocks=later)
        self.assertEqual(original["trace"][:2], altered["trace"][:2])

    def test_incomplete_or_false_assignment_and_combined_bounds_are_rejected(self):
        args, shocks, industry, response = self.fixture()
        missing = copy.deepcopy(industry)
        missing["membership"].pop("000001")
        with self.assertRaisesRegex(ValueError, "coverage"):
            simulate_portfolio(*args, scenario_shocks=shocks, background_response=response, industry_shocks=missing)
        false = copy.deepcopy(industry)
        false["path_groups"]["000001"], false["path_groups"]["000002"] = false["path_groups"]["000002"], false["path_groups"]["000001"]
        with self.assertRaisesRegex(ValueError, "relabelled"):
            validate_industry_shocks(list(args[0]), len(args[0]["000001"]), false)
        with self.assertRaisesRegex(ValueError, "explicit common"):
            simulate_portfolio(*args, industry_shocks=industry)
        large = copy.deepcopy(industry)
        for values in large["by_group"].values():
            values[1] = 0.8
        shocks["common"][1] = 0.7
        with self.assertRaisesRegex(ValueError, "combined common"):
            simulate_portfolio(*args, scenario_shocks=shocks, background_response=response, industry_shocks=large)


if __name__ == "__main__":
    unittest.main()
