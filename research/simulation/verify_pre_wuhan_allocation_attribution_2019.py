"""Independent compensated aggregation of archived factual allocation positions."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "research_outputs/pre_wuhan_allocation_attribution_2019_v1"
CONFIG = ROOT / "research/configs/pre_wuhan_allocation_attribution_2019.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_allocation_attribution_statistics_2019_v1.json"


class CheckedSum:
    """Neumaier compensated sum, independent of the producer's running sums."""
    def __init__(self):
        self.value, self.correction = 0.0, 0.0

    def add(self, value):
        if not math.isfinite(value):
            raise ValueError("nonfinite allocation statistic")
        total = self.value + value
        self.correction += ((self.value - total) + value if abs(self.value) >= abs(value) else (value - total) + self.value)
        self.value = total

    def total(self):
        return self.value + self.correction


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def serialize(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def validate_position(row, components):
    if set(row["component_share_equivalents"]) != set(components):
        raise ValueError("allocation component membership differs")
    values = list(row["component_share_equivalents"].values())
    actual = row["factual_delta_share_equivalent"]
    if any(not math.isfinite(v) for v in [*values, actual, row["portfolio_belief_score"], row["asset_belief"], row["current_weight"], row["desired_weight"]]):
        raise ValueError("nonfinite allocation position")
    difference = abs(math.fsum(values) - actual)
    if not math.isclose(math.fsum(values), actual, rel_tol=1e-12, abs_tol=1e-9):
        raise ValueError("allocation position does not telescope")
    quantities = [row[s + "_quantity"] for s in ("submitted", "accepted", "filled")]
    if any(type(v) is not int for v in quantities) or not 0 <= quantities[2] <= quantities[1] <= quantities[0]:
        raise ValueError("allocation integer submitted/accepted/filled flow differs")
    if quantities[0] == 0:
        if row["submitted_side"] != "hold" or quantities[1:] != [0, 0]:
            raise ValueError("zero allocation submission has nonzero execution")
    elif row["submitted_side"] not in {"buy", "sell"} or (actual > 0) != (row["submitted_side"] == "buy"):
        raise ValueError("allocation submitted side differs from factual delta")
    return difference


def compute():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    result = json.loads((SOURCE / "results.json").read_text(encoding="utf-8"))
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    bindings = {**manifest["inputs"], **manifest["code_sha256"], str(SOURCE / "manifest.json"): digest(SOURCE / "manifest.json")}
    for name, record in manifest["artifacts"].items():
        bindings[str(SOURCE / name)] = record["sha256"]
    if (result["components"] != config["components"] or result["agent_signal_enabled"] is not False
            or bindings.get(str(CONFIG)) != digest(CONFIG)):
        raise ValueError("allocation result protocol or disabled signal gate differs")

    def verify():
        for path, expected in bindings.items():
            if digest(path) != expected:
                raise ValueError("independent allocation source/output/program differs: " + path)

    verify()
    groups, seen, per_path, row_count, maximum_error = {}, set(), {}, 0, 0.0
    with gzip.open(SOURCE / "positions.jsonl.gz", "rt", encoding="utf-8") as h:
        for line in h:
            row = json.loads(line)
            maximum_error = max(maximum_error, validate_position(row, config["components"]))
            key = (row["variant"], row["basket_index"], row["session"], row["owner"], row["stock_code"])
            if key in seen or row["variant"] not in config["variants"]:
                raise ValueError("allocation position identity duplicates or differs")
            seen.add(key)
            per_path.setdefault((row["variant"], row["basket_index"]), Counter())[row["session"]] += 1
            score = row["portfolio_belief_score"]
            sign = "positive" if score > 1e-12 else "negative" if score < -1e-12 else "zero"
            labels = ["all", "branch:" + row["branch"], "role:" + row["role"],
                      "role_branch:" + row["role"] + ":" + row["branch"], "score:" + sign + ":" + row["branch"]]
            for label in labels:
                key = (row["variant"], label)
                if key not in groups:
                    groups[key] = {"positions": 0, "components": {c: CheckedSum() for c in config["components"]},
                                   "factual": CheckedSum(), "flow": Counter()}
                group = groups[key]
                group["positions"] += 1
                for component, value in row["component_share_equivalents"].items():
                    group["components"][component].add(value)
                group["factual"].add(row["factual_delta_share_equivalent"])
                if row["submitted_quantity"]:
                    for stage in ("submitted", "accepted", "filled"):
                        group["flow"][stage + "_" + row["submitted_side"]] += row[stage + "_quantity"]
            row_count += 1
    if (row_count != config["expected_asset_positions"] or len(per_path) != config["expected_paths"]
            or any(set(v) != set(range(43)) or any(n != 36 for n in v.values()) for v in per_path.values())):
        raise ValueError("allocation independent full-grid coverage differs")
    rebuilt = {}
    maximum_summary_error = 0.0
    for (variant, label), value in groups.items():
        record = {"positions": value["positions"],
                  "component_share_equivalent_sums": {c: v.total() for c, v in value["components"].items()},
                  "factual_delta_share_equivalent_sum": value["factual"].total(),
                  **{stage + "_" + side: value["flow"][stage + "_" + side] for stage in ("submitted", "accepted", "filled") for side in ("buy", "sell")}}
        rebuilt.setdefault(variant, {})[label] = record
        saved = result["groups"][variant][label]
        if saved["positions"] != record["positions"] or any(saved[k] != record[k] for k in record if k.startswith(("submitted_", "accepted_", "filled_"))):
            raise ValueError("allocation independently summed factual flow differs")
        numbers = [(saved["factual_delta_share_equivalent_sum"], record["factual_delta_share_equivalent_sum"])]
        numbers += [(saved["component_share_equivalent_sums"][c], v) for c, v in record["component_share_equivalent_sums"].items()]
        for actual, expected in numbers:
            if not math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-7):
                raise ValueError("allocation compensated aggregate differs")
            maximum_summary_error = max(maximum_summary_error, abs(actual - expected))
    if any(set(result["groups"][v]) != set(rebuilt[v]) for v in config["variants"]):
        raise ValueError("allocation independent group coverage differs")
    verify()
    return {"pipeline_version": "pre-wuhan-allocation-independent-compensated-statistics-v1", "status": "PASS",
            "positions_checked": row_count, "component_cells_checked": row_count * len(config["components"]),
            "integer_flow_cells_checked": row_count * 3, "paths_checked": len(per_path), "groups": rebuilt,
            "maximum_position_identity_error": maximum_error, "maximum_summary_absolute_difference": maximum_summary_error,
            "inputs": dict(sorted(bindings.items())), "code_sha256": {str(Path(__file__).resolve()): digest(__file__)},
            "agent_signal_enabled": False,
            "interpretation": "Independent compensated sums agree with all archived factual positions and integer order stages. Position components telescope, whole grid is present, actual source resources were audited by the producer. Bookkeeping order remains explicit; this is not causal assignment of executed sales, a new path intervention, real investor calibration or prediction validation."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("allocation statistics require a direct research_outputs file")
    result = compute()
    raw = serialize(result)
    if args.audit_existing:
        if output.read_bytes() != raw:
            raise ValueError("allocation independent statistics differ from byte-identical rebuild")
        print("Independent allocation statistics rebuilt byte-identically.")
    else:
        with output.open("xb") as h:h.write(raw)
    print(json.dumps({k: result[k] for k in ("status", "positions_checked", "component_cells_checked", "integer_flow_cells_checked", "maximum_summary_absolute_difference")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
