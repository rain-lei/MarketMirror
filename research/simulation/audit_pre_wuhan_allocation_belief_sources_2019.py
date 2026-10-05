"""Trace own-price feedback, shared shocks and private messages in allocations."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from .allocation_belief_sources import SOURCES, decompose_sources
from .verify_pre_wuhan_allocation_attribution_2019 import CheckedSum

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/pre_wuhan_allocation_belief_sources_2019.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_allocation_belief_sources_2019_v1.json"
QUANTITY = ROOT / "research_outputs/pre_wuhan_quantity_controls_2019_v1"
ATTRIBUTION = ROOT / "research_outputs/pre_wuhan_allocation_attribution_2019_v1"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def serialize(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def compute():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if config["sources"] != SOURCES or config["agent_signal_enabled"] is not False:
        raise ValueError("belief provenance source order or disabled signal gate changed")
    bindings = {str(CONFIG): digest(CONFIG)}
    for path, expected in config["inputs"].items():
        bindings[str((ROOT / path).resolve())] = expected
    for directory in (QUANTITY, ATTRIBUTION):
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        for key in ("inputs", "code_sha256"):
            for path, sha in manifest[key].items():
                path = str((ROOT / path).resolve())
                if path in bindings and bindings[path] != sha:
                    raise ValueError("conflicting belief source hash bindings")
                bindings[path] = sha
        for name, record in manifest["artifacts"].items():
            bindings[str(directory / name)] = record["sha256"]
    code = {str(ROOT / ("research/simulation/" + name + ".py")): digest(ROOT / ("research/simulation/" + name + ".py"))
            for name in ("allocation_belief_sources", "audit_pre_wuhan_allocation_belief_sources_2019", "verify_pre_wuhan_allocation_attribution_2019")}

    def verify():
        for path, expected in {**bindings, **code}.items():
            if digest(path) != expected:
                raise ValueError("belief source/archive/code hash differs: " + path)

    verify()
    reference = json.loads((QUANTITY / "results.json").read_text(encoding="utf-8"))["result"]
    prior = json.loads((ATTRIBUTION / "results.json").read_text(encoding="utf-8"))
    specs = {(p["variant"], p["basket_index"]): p["participant_specs"] for p in reference["path_summaries"]}
    groups, counts, max_error = {}, Counter(), 0.0
    with gzip.open(ATTRIBUTION / "positions.jsonl.gz", "rt", encoding="utf-8") as positions, gzip.open(QUANTITY / "ledger.jsonl.gz", "rt", encoding="utf-8") as ledger:
        for line in ledger:
            day = json.loads(line)
            actors = specs[day["variant"], day["basket_index"]]
            for owner, decision in day["decisions"].items():
                actor = actors[owner]
                decomposition = decompose_sources(actor["parameters"], actor["profile"], day["observations"], day["issuer_information_receipts"][owner], decision)
                for a in sorted(day["observations"]):
                    raw = next(positions, None)
                    if raw is None:
                        raise ValueError("belief provenance prior position missing")
                    row = json.loads(raw)
                    if (row["variant"], row["basket_index"], row["trade_date"], row["session"], row["owner"], row["stock_code"]) != (
                            day["variant"], day["basket_index"], day["trade_date"], day["portfolio_auction"]["session"], owner, a):
                        raise ValueError("belief provenance aligned position identity differs")
                    scale = decision["nav_minor"] / day["portfolio_auction"]["prices_before_minor"][a]
                    parts = {k: value * scale for k, value in decomposition["component_equal_asset_weight_changes"].items()}
                    actual = row["component_share_equivalents"]["belief_total_exposure"]
                    if not math.isclose(math.fsum(parts.values()), actual, rel_tol=1e-12, abs_tol=1e-9):
                        raise ValueError("belief source decomposition differs from factual allocation stage")
                    max_error = max(max_error, abs(math.fsum(parts.values()) - actual))
                    sign = "positive" if row["portfolio_belief_score"] > 1e-12 else "negative" if row["portfolio_belief_score"] < -1e-12 else "zero"
                    labels = ["all", "branch:" + row["branch"], "role:" + row["role"],
                              "role_branch:" + row["role"] + ":" + row["branch"], "score:" + sign + ":" + row["branch"]]
                    for label in labels:
                        key = (row["variant"], label)
                        group = groups.setdefault(key, {"positions": 0, "components": {s: CheckedSum() for s in SOURCES}, "factual": CheckedSum()})
                        group["positions"] += 1
                        for k, value in parts.items():group["components"][k].add(value)
                        group["factual"].add(actual)
                    counts["aligned_asset_positions"] += 1
                counts["decisions_with_factual_beliefs_reconstructed"] += 1
            counts["source_ledger_records"] += 1
        if next(positions, None) is not None:
            raise ValueError("belief provenance has excess prior positions")
    if (counts["aligned_asset_positions"] != config["expected_positions"] or counts["source_ledger_records"] != config["expected_ledger_records"]
            or counts["decisions_with_factual_beliefs_reconstructed"] != config["expected_decisions"]):
        raise ValueError("belief provenance full coverage differs")
    output_groups = {}
    for (variant, label), group in groups.items():
        saved = prior["groups"][variant][label]
        record = {"positions": group["positions"], "source_share_equivalent_sums": {k: v.total() for k, v in group["components"].items()},
                  "factual_belief_exposure_share_equivalent_sum": group["factual"].total()}
        if (record["positions"] != saved["positions"] or not math.isclose(record["factual_belief_exposure_share_equivalent_sum"], saved["component_share_equivalent_sums"]["belief_total_exposure"], rel_tol=1e-10, abs_tol=1e-7)):
            raise ValueError("belief provenance group differs from prior attribution")
        output_groups.setdefault(variant, {})[label] = record
    verify()
    return {"pipeline_version": config["version"], "sources": SOURCES, "groups": output_groups, "checks": dict(counts),
            "maximum_position_share_equivalent_difference": max_error, "inputs": dict(sorted(bindings.items())), "code_sha256": code,
            "agent_signal_enabled": False, "interpretation": config["interpretation"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("belief provenance requires a direct research_outputs file")
    result = compute()
    raw = serialize(result)
    if args.audit_existing:
        if output.read_bytes() != raw:
            raise ValueError("belief provenance differs from byte-identical reconstruction")
        print("Allocation belief sources rebuilt byte-identically.")
    else:
        with output.open("xb") as h:h.write(raw)
    print(json.dumps({"checks": result["checks"], "maximum_position_share_equivalent_difference": result["maximum_position_share_equivalent_difference"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
