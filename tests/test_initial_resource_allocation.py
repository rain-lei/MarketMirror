import copy
import unittest

from research.simulation.initial_resource_allocation import allocate, VERSION
from research.simulation.portfolio_auction import PortfolioAccount


class InitialResourceAllocationTests(unittest.TestCase):
    def fixture(self):
        accounts = {"s": PortfolioAccount({"a": 10000}, {"a": 100}, {"a": 100}),
            "b": PortfolioAccount({"a": 20000}, {"a": 200}, {"a": 200})}
        specs = {"s": {"kind": "strategy", "parameters": {"base_weight": .25}},
            "b": {"kind": "background", "asset": "a"}}
        return accounts, specs

    def test_exact_resource_transfer_preserves_each_wealth_and_input(self):
        accounts, specs = self.fixture()
        before = copy.deepcopy(accounts)
        new, metadata = allocate(accounts, specs, ["a"], 100, 10, VERSION)
        self.assertEqual(new["s"].shares["a"], 50)
        self.assertEqual(new["s"].wallets["a"], 15000)
        self.assertEqual(new["b"].shares["a"], 250)
        self.assertEqual(new["b"].wallets["a"], 15000)
        self.assertEqual(accounts, before)
        self.assertFalse(metadata["background_target_changed"])

    def test_insufficient_counterparty_cash_fails_without_mutation(self):
        accounts, specs = self.fixture()
        accounts["b"].wallets["a"] = 100
        before = copy.deepcopy(accounts)
        with self.assertRaises(ValueError):
            allocate(accounts, specs, ["a"], 100, 10, VERSION)
        self.assertEqual(accounts, before)

    def test_disabled_preserves_identity_and_unsettled_input_is_rejected(self):
        accounts, specs = self.fixture()
        unchanged, metadata = allocate(accounts, specs, ["a"], 100, 10)
        self.assertIs(unchanged, accounts)
        self.assertIsNone(metadata)
        accounts["s"].sellable["a"] = 90
        with self.assertRaises(ValueError):
            allocate(accounts, specs, ["a"], 100, 10, VERSION)
