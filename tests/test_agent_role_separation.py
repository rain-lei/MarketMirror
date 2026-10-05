import copy
import unittest

from research.simulation import audit_agent_role_separation as target


def agent_row(role, action, target_weight, filled):
    return {"role": role,
            "decision": {"action": action, "target_weight": target_weight,
                         "reason_codes": ["market_signal"]},
            "filled_shares": filled}


class AgentRoleSeparationTest(unittest.TestCase):
    def test_counts_role_and_signal_action_differences(self):
        agents = {role: {"role": role} for role in target.ROLES}
        market = [
            {"trade_date": "2020-01-02", "market_signal": 0.2,
             "agents": {role: agent_row(role, "hold", 0.1, 0.0)
                        for role in target.ROLES}},
            {"trade_date": "2020-01-03", "market_signal": -0.3,
             "agents": {"aggressive": agent_row("aggressive", "sell", 0.0, -2.0),
                        "conservative": agent_row("conservative", "hold", 0.1, 0.0),
                        "institutional": agent_row("institutional", "hold", 0.1, 0.0)}},
        ]
        zero = copy.deepcopy(market)
        zero[1]["agents"]["aggressive"] = agent_row("aggressive", "hold", 0.1, 0.0)
        result = {"agents": agents, "paths": {"000001": {
            "market_signal": {"trace": market,
                              "summary": {role: {"risk_budget_breach_days": 0}
                                          for role in target.ROLES}},
            "zero_signal": {"trace": zero},
        }}}
        audit = target.analyze_run(result)
        self.assertEqual(audit["company_days"], 2)
        self.assertEqual(audit["all_same_action_company_days"], 1)
        self.assertEqual(audit["at_least_two_actions_differ_company_days"], 1)
        self.assertEqual(audit["by_role"]["aggressive"]["signal_action_changes"], 1)
        self.assertEqual(audit["by_role"]["aggressive"]["signal_fill_changes"], 1)
        self.assertEqual(audit["by_role"]["conservative"]["signal_action_changes"], 0)

        broken = copy.deepcopy(result)
        broken["paths"]["000001"]["zero_signal"]["trace"][1]["trade_date"] = "2020-01-04"
        with self.assertRaisesRegex(ValueError, "paired replay day"):
            target.analyze_run(broken)


if __name__ == "__main__":
    unittest.main()
