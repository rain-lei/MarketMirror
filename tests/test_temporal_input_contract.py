import copy
from datetime import date, timedelta
import unittest

from research.data_pipeline.temporal_input_contract import (
    ABSENT, FAILED, PRESENT, SUSPENDED, assess_temporal_inputs)


class TemporalInputContractTests(unittest.TestCase):
    def setUp(self):
        self.days = [(date(2020, 9, 1) + timedelta(days=i)).isoformat() for i in range(30)]
        self.analysis = self.days[25:]
        self.specs = [{"secid": "0.000001", "fqt": 2, "kind": "stock"}]
        self.pairs = [{"secid": "0.000001", "trade_date": d,
            "unadjusted_source": self.source("0.000001", d, 0, "stock", str(200 + 2 * i + i % 3)),
            "back_adjusted_source": self.source("0.000001", d, 2, "stock", str(2000 + 20 * i + 10 * (i % 3)))}
            for i, d in enumerate(self.days)]
        self.benchmark = [self.source("1.000300", d, 0, "benchmark", str(100 + i + i % 2)) for i, d in enumerate(self.days)]

    @staticmethod
    def source(secid, day, fqt, kind, close):
        fields = {"f51": day, "f52": close, "f53": close, "f54": close, "f55": close,
            "f56": "10", "f57": "1000", "f58": "0", "f59": "0", "f60": "0", "f61": "1"}
        return {"secid": secid, "trade_date": day, "fqt": fqt, "kind": kind,
            "raw_line": ",".join(fields.values()), "provider_fields": fields,
            "source_observation_status": PRESENT, "documented_trade_status": None}

    @staticmethod
    def absent(source, status=ABSENT, documented=None):
        source.update(raw_line=None, source_observation_status=status, documented_trade_status=documented)
        source["provider_fields"] = {k: None for k in source["provider_fields"]}

    @staticmethod
    def field(source, name, value):
        source["provider_fields"][name] = value
        source["raw_line"] = ",".join(source["provider_fields"].values()) if all(
            type(v) is str for v in source["provider_fields"].values()) else "invalid"

    def run_assessment(self):
        return assess_temporal_inputs(self.pairs, self.benchmark, self.specs, self.days, self.analysis, 5)

    def test_information_reference_and_outcome_have_distinct_dates(self):
        first = self.run_assessment()["by_position"][0]
        self.assertEqual((first["signal_cutoff_date"], first["execution_reference_date"], first["trade_date"]),
            (self.days[23], self.days[24], self.days[25]))
        self.assertEqual([r["trade_date"] for r in first["strict_calendar_profile"]["history"]], self.days[19:24])
        self.assertFalse(first["model_eligible"])

    def test_future_target_and_reference_cannot_change_agent_history(self):
        before = self.run_assessment()["by_position"][0]
        for i in (24, 25):
            self.pairs[i]["back_adjusted_source"] = self.source("0.000001", self.days[i], 2, "stock", str(9000 + i))
            self.pairs[i]["unadjusted_source"] = self.source("0.000001", self.days[i], 0, "stock", str(900 + i))
            self.benchmark[i] = self.source("1.000300", self.days[i], 0, "benchmark", str(800 + i))
        after = self.run_assessment()["by_position"][0]
        self.assertEqual(before["strict_calendar_profile"], after["strict_calendar_profile"])
        self.assertEqual(before["available_observation_profile"], after["available_observation_profile"])
        self.assertNotEqual(before["observed_adjacent_vendor_return_fraction"], after["observed_adjacent_vendor_return_fraction"])

    def test_suspension_keeps_daily_holes_and_reports_stale_available_history(self):
        for key in ("unadjusted_source", "back_adjusted_source"):
            self.absent(self.pairs[22][key], documented=SUSPENDED if key == "unadjusted_source" else None)
        first = self.run_assessment()["by_position"][0]
        self.assertFalse(first["strict_calendar_profile"]["complete_observed_history"])
        self.assertEqual(first["strict_history_unknown_dates"], self.days[22:24])
        self.assertTrue(first["available_observation_profile"]["complete_observed_history"])
        self.assertEqual(first["available_observation_profile"]["latest_history_date"], self.days[21])
        self.assertEqual(first["available_observation_profile"]["stale_calendar_sessions"], 2)
        self.assertEqual(self.pairs[22]["unadjusted_source"]["provider_fields"]["f53"], None)

    def test_unknown_cutoff_slot_is_not_the_latest_real_observation(self):
        for key in ("unadjusted_source", "back_adjusted_source"):
            self.absent(self.pairs[23][key], documented=SUSPENDED if key == "unadjusted_source" else None)
        first = self.run_assessment()["by_position"][0]
        strict = first["strict_calendar_profile"]
        self.assertEqual(strict["history_last_slot_date"], self.days[23])
        self.assertEqual(strict["latest_history_date"], self.days[22])
        self.assertEqual(strict["stale_calendar_sessions"], 1)
        self.assertIsNone(strict["moments"])

    def test_closed_session_and_resume_do_not_invent_daily_returns_or_reference_quotes(self):
        for key in ("unadjusted_source", "back_adjusted_source"):
            self.absent(self.pairs[25][key], documented=SUSPENDED if key == "unadjusted_source" else None)
        closed, resumed = self.run_assessment()["by_position"][:2]
        self.assertIs(closed["execution_available_for_conditional_replay"], False)
        self.assertIsNone(closed["observed_adjacent_vendor_return_fraction"])
        self.assertIsNone(resumed["observed_adjacent_vendor_return_fraction"])
        self.assertIsNone(resumed["raw_reference_close"])
        self.assertFalse(closed["daily_gap_return_imputed"])

    def test_failed_adjusted_request_does_not_become_zero_or_suspension(self):
        self.absent(self.pairs[25]["back_adjusted_source"], FAILED)
        row = self.run_assessment()["by_position"][0]
        self.assertIsNone(row["observed_adjacent_vendor_return_fraction"])
        self.assertIs(row["execution_available_for_conditional_replay"], True)

    def test_undocumented_absence_keeps_trade_status_unknown(self):
        for key in ("unadjusted_source", "back_adjusted_source"):
            self.absent(self.pairs[25][key])
        row = self.run_assessment()["by_position"][0]
        self.assertIsNone(row["execution_available_for_conditional_replay"])
        self.assertIsNone(row["observed_adjacent_vendor_return_fraction"])

    def test_zero_volume_is_not_a_suspension_certificate(self):
        for key in ("unadjusted_source", "back_adjusted_source"):
            self.field(self.pairs[25][key], "f56", "0")
        row = self.run_assessment()["by_position"][0]
        self.assertIsNone(row["execution_available_for_conditional_replay"])
        self.assertIsNotNone(row["observed_adjacent_vendor_return_fraction"])

    def test_real_zero_stock_returns_remain_known_zero(self):
        for row in self.pairs:
            row["back_adjusted_source"] = self.source("0.000001", row["trade_date"], 2, "stock", "2000")
        first = self.run_assessment()["by_position"][0]
        self.assertEqual(first["observed_adjacent_vendor_return_fraction"], [0, 1])
        self.assertEqual(first["strict_calendar_profile"]["moments"]["stock_variance_fraction"], [0, 1])
        self.assertEqual(first["strict_calendar_profile"]["moments"]["beta_fraction"], [0, 1])

    def test_constant_benchmark_does_not_manufacture_zero_beta(self):
        self.benchmark = [self.source("1.000300", d, 0, "benchmark", "100") for d in self.days]
        first = self.run_assessment()["by_position"][0]
        self.assertTrue(first["strict_calendar_profile"]["complete_observed_history"])
        self.assertIsNone(first["strict_calendar_profile"]["moments"]["beta_fraction"])
        self.assertFalse(first["strict_calendar_profile"]["public_factor_estimate_defined"])

    def test_missing_benchmark_is_not_skipped_or_filled(self):
        self.absent(self.benchmark[22])
        first = self.run_assessment()["by_position"][0]
        self.assertFalse(first["strict_calendar_profile"]["complete_observed_history"])
        self.assertFalse(first["available_observation_profile"]["complete_observed_history"])
        self.assertIsNone(first["available_observation_profile"]["moments"])

    def test_duplicate_or_missing_company_positions_are_rejected(self):
        original = copy.deepcopy(self.pairs)
        for bad in (original[:-1], original + [copy.deepcopy(original[0])]):
            with self.assertRaises(ValueError):
                assess_temporal_inputs(bad, self.benchmark, self.specs, self.days, self.analysis, 5)

    def test_nonfinite_or_nonpositive_source_is_rejected(self):
        for value in ("NaN", "Infinity", "0", "-1"):
            pairs = copy.deepcopy(self.pairs)
            self.field(pairs[23]["back_adjusted_source"], "f53", value)
            with self.assertRaises(ValueError):
                assess_temporal_inputs(pairs, self.benchmark, self.specs, self.days, self.analysis, 5)

    def test_wrong_identity_and_boolean_parameter_are_rejected(self):
        for field, value in (("secid", "1.600000"), ("fqt", True)):
            pairs = copy.deepcopy(self.pairs)
            pairs[23]["back_adjusted_source"][field] = value
            with self.assertRaises(ValueError):
                assess_temporal_inputs(pairs, self.benchmark, self.specs, self.days, self.analysis, 5)

    def test_partial_unknown_fields_and_contradictory_suspension_are_rejected(self):
        pairs = copy.deepcopy(self.pairs)
        self.absent(pairs[25]["unadjusted_source"], documented=SUSPENDED)
        with self.assertRaises(ValueError):
            assess_temporal_inputs(pairs, self.benchmark, self.specs, self.days, self.analysis, 5)
        self.absent(pairs[25]["back_adjusted_source"])
        pairs[25]["unadjusted_source"]["provider_fields"]["f53"] = "0"
        with self.assertRaises(ValueError):
            assess_temporal_inputs(pairs, self.benchmark, self.specs, self.days, self.analysis, 5)

    def test_insufficient_tminus2_warmup_is_rejected(self):
        with self.assertRaises(ValueError):
            assess_temporal_inputs(self.pairs, self.benchmark, self.specs, self.days, [self.days[6]], 5)


if __name__ == "__main__":
    unittest.main()
