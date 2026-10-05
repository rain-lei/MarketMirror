"""Enumerate first-day strategy inventory balances without using observed outcomes."""

from __future__ import annotations

import argparse
import itertools
import json
import math
from dataclasses import asdict
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .agents import AgentParameters
from .feedback_auction import price_observation
from .portfolio_market import covariance, initial_portfolio, portfolio_decision


ROOT = Path(__file__).resolve().parents[2]
BASE_CONFIG = ROOT / "research/configs/wuhan_pre_event_pit_portfolio_no_text_2020.json"
REPLAY_CONFIG = ROOT / "research/configs/wuhan_pre_event_pit_replay_2020.json"
OUTPUT = ROOT / "research_outputs/strategy_inventory_balance_audit_2019_v1.json"
VERSION = "strategy-inventory-balance-audit-v1"


def first_day_net(inventory: tuple[int, int, int, int], base: dict, agents: list[AgentParameters]) -> dict:
    assets = ["asset_a", "asset_b", "asset_c"]
    venue = base["venue"]
    core = {**base["core"], "inventory": list(inventory)}
    case = base["cases"][0]
    prices = {asset: venue["price_start_minor"] for asset in assets}
    observation = {**price_observation([], "2019-10-31", base["feedback_parameters"]),
                   "text_signal": 0.0, "text_uncertainty": 0.0,
                   "text_evidence": "text:disabled"}
    observations = {asset: observation for asset in assets}
    cov = covariance({asset: [] for asset in assets}, "2019-10-31", base["feedback_parameters"])
    cohort, accounts, _, _ = initial_portfolio(agents, core, base["background"], assets, case, venue)
    roles = {agent.role: 0 for agent in agents}
    for agent, profile in cohort:
        decision = portfolio_decision(agent, profile, accounts[agent.name], prices, observations,
                                      cov, case, {"sign": 0, "streak": 0}, 0, False)
        delta = decision["order_weight_changes"][assets[0]]
        quantity = math.floor(abs(delta) * decision["nav_minor"]
                              / (prices[assets[0]] * venue["lot_size"])) * venue["lot_size"]
        if delta < 0:
            quantity = min(quantity, accounts[agent.name].shares[assets[0]])
        roles[agent.role] += quantity if delta > 0 else -quantity
    return {"inventory": list(inventory), "net_requested": sum(roles.values()),
            "role_net_requested": roles}


def compute() -> tuple[dict, dict]:
    base = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    replay = json.loads(REPLAY_CONFIG.read_text(encoding="utf-8"))
    agents = [AgentParameters(**row) for row in replay["agents"]]
    lot = base["venue"]["lot_size"]
    original = tuple(base["core"]["inventory"])
    total = sum(original)
    candidates = []
    for units in itertools.product(range(total // lot + 1), repeat=3):
        fourth = total // lot - sum(units)
        if fourth < 0:
            continue
        inventory = tuple(unit * lot for unit in (*units, fourth))
        candidates.append(first_day_net(inventory, base, agents))
    if len(candidates) != 4060:
        raise ValueError("inventory candidate count differs")
    minimum = min(abs(row["net_requested"]) for row in candidates)
    best = sorted((row for row in candidates if abs(row["net_requested"]) == minimum),
                  key=lambda row: row["inventory"])
    result = {
        "pipeline_version": VERSION,
        "information_rule": "empty simulated history, zero text, 2019-10-31 cutoff; no observed outcome",
        "original_inventory": list(original),
        "preserved_total_shares": total,
        "lot_size": lot,
        "enumerated_candidates": len(candidates),
        "zero_net_candidate_count": sum(row["net_requested"] == 0 for row in candidates),
        "minimum_absolute_net_requested": minimum,
        "minimum_candidates": best[:20],
        "balanced_inventory_used_in_sensitivity": [100, 400, 600, 900],
        "balanced_inventory_first_day": first_day_net((100, 400, 600, 900), base, agents),
        "interpretation": "Holding total strategy shares fixed at 2,700 and requiring whole lots, no initial inventory vector removes first-day net strategy selling under the current role rules. The later balanced sensitivity changes total shares and wealth; it is a separate initial-state scenario, not a pure reallocation.",
    }
    paths = (BASE_CONFIG, REPLAY_CONFIG, ROOT / "research/simulation/agents.py",
             ROOT / "research/simulation/feedback_auction.py",
             ROOT / "research/simulation/portfolio_market.py", Path(__file__))
    return result, {str(path.resolve()): file_sha256(path) for path in paths}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result, bindings = compute()
    payload = {"result": result, "input_sha256": bindings}
    destination = args.output.resolve()
    if args.audit_existing:
        if json.loads(destination.read_text(encoding="utf-8")) != payload:
            raise ValueError("inventory balance archive differs from recomputation")
    else:
        if destination.exists() or destination.parent != OUTPUT.parent.resolve():
            raise ValueError("inventory balance output must be a new research_outputs file")
        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("enumerated_candidates", "zero_net_candidate_count",
                                                    "minimum_absolute_net_requested", "balanced_inventory_first_day")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
