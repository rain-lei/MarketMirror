"""Past information and source targets cannot be relabeled by sealed hashes."""
from copy import deepcopy
import unittest

from research.data_pipeline.audit_temporal_transport_numeric import audit_numeric_contract
from research.data_pipeline.temporal_input_contract import assess_temporal_inputs
from research.simulation.temporal_risk_inputs import compile_positions
from research.simulation.temporal_residual_coupling import source_windows
from research.simulation.temporal_return_metrics import freeze_mask


class TestTransportIndependentNumericAudit(unittest.TestCase):
    def fixture(self):
        calendar = ["2022-01-04", "2022-01-05", "2022-01-06", "2022-01-07", "2022-01-10", "2022-01-11", "2022-01-12"]
        dates, stocks = calendar[-2:], ["000001", "600001"]
        closes = {"000001": [100, 104, 102, 106, 105, 109, 107], "600001": [100, 101, 104, 102, 108, 110, 109],
            "000300": [100, 101, 103, 102, 104, 106, 105]}
        def source(stock, fqt, day, kind):
            price = str(closes[stock][calendar.index(day)])
            fields = dict(zip([f"f{i}" for i in range(51, 62)], [day, price, price, price, price, "100", "10000", "0", "0", "0", "1"]))
            return {"secid": ("1." if stock.startswith("6") or kind == "benchmark" else "0.") + stock,
                "kind": kind, "fqt": fqt, "trade_date": day, "source_observation_status": "SOURCE_ROW_PRESENT_QC_PENDING",
                "provider_fields": fields, "raw_line": ",".join(fields.values()), "model_eligible": False, "certified_trade_status": None}
        rows = [source(stock, fqt, day, "stock") for stock in stocks for fqt in (0, 1, 2) for day in calendar]
        benchmark = [source("000300", 0, day, "benchmark") for day in calendar]
        lookup = {(row["secid"][2:], row["fqt"], row["trade_date"]): row for row in rows}
        pairs = [{"secid": lookup[stock, 0, day]["secid"], "trade_date": day,
            "unadjusted_source": lookup[stock, 0, day], "back_adjusted_source": lookup[stock, 2, day]} for stock in stocks for day in calendar]
        specs = [{"secid": lookup[stock, 2, calendar[0]]["secid"], "fqt": 2, "kind": "stock"} for stock in stocks]
        positions = assess_temporal_inputs(pairs, benchmark, specs, calendar, dates, 2)["by_position"]
        risk, features, joined = compile_positions(positions, stocks, dates, 2)
        inputs = {"risk": risk, "features": features, "joined": joined}
        return rows, benchmark, stocks, calendar, dates, inputs, source_windows(features), freeze_mask(joined, stocks, dates)

    def run_audit(self, fixture):
        return audit_numeric_contract(*fixture, window=2)

    def test_all_raw_source_histories_and_masks_are_independently_rebuilt(self):
        result = self.run_audit(self.fixture())
        self.assertEqual(result["company_evaluation_histories_checked"], 4)
        self.assertEqual(result["actual_past_return_observations_checked"], 8)
        self.assertEqual(result["evaluation_correlation_pairs_checked"], 1)
        self.assertFalse(result["model_eligible"])

    def test_changed_target_is_not_accepted_even_if_mask_changed_with_it(self):
        fixture = self.fixture()
        fixture[5]["joined"]["000001"][0]["observed_return"] = 0
        fixture[7]["targets_by_stock"]["000001"][0] = 0
        with self.assertRaisesRegex(ValueError, "Target/venue/text"):
            self.run_audit(fixture)

    def test_modified_past_stock_risk_is_rejected(self):
        fixture = self.fixture()
        fixture[5]["risk"]["000001"][0]["history"][0]["stock_return"] = 0
        with self.assertRaisesRegex(ValueError, "Past numeric risk/factor"):
            self.run_audit(fixture)

    def test_future_or_invented_common_date_is_rejected(self):
        fixture = self.fixture()
        fixture[6][0]["common_source_dates"][-1] = fixture[4][0]
        with self.assertRaisesRegex(ValueError, "rank windows"):
            self.run_audit(fixture)

    def test_incomplete_full_source_grid_is_not_accepted(self):
        fixture = list(self.fixture())
        fixture[0] = fixture[0][:-1]
        with self.assertRaisesRegex(ValueError, "source grid"):
            self.run_audit(fixture)


if __name__ == "__main__":
    unittest.main()
