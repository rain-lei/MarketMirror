"""Check policy input variation before attempting to fit response coefficients."""

from __future__ import annotations

import argparse
import json
from fractions import Fraction
from pathlib import Path

import numpy as np

from .provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/pre_wuhan_policy_estimability_2019.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_policy_estimability_2019_v1.json"


def exact_rank(matrix: list[list[float]]) -> int:
    rows = [[Fraction(str(value)) for value in row] for row in matrix]
    rank = 0
    for column in range(len(rows[0])):
        pivot = next((index for index in range(rank, len(rows)) if rows[index][column] != 0), None)
        if pivot is None:
            continue
        rows[rank], rows[pivot] = rows[pivot], rows[rank]
        value = rows[rank][column]
        rows[rank] = [cell / value for cell in rows[rank]]
        for index in range(rank + 1, len(rows)):
            value = rows[index][column]
            if value:
                rows[index] = [cell - value * base for cell, base in zip(rows[index], rows[rank])]
        rank += 1
        if rank == len(rows):
            break
    return rank


def compute() -> dict:
    settings = json.loads(CONFIG.read_text(encoding="utf-8"))
    bindings = {str(ROOT / name): value for name, value in settings["inputs"].items()}
    bindings[str(CONFIG)] = file_sha256(CONFIG)
    if any(file_sha256(Path(name)) != value for name, value in bindings.items()):
        raise ValueError("policy estimability frozen input changed")
    manifest = json.loads((ROOT / "research_outputs/pre_wuhan_policy_calendar_2019_v1/manifest.json").read_text(encoding="utf-8"))
    audit = json.loads((ROOT / "research_outputs/pre_wuhan_policy_calendar_audit_2019_v1.json").read_text(encoding="utf-8"))
    bindings.update(manifest["inputs"] | manifest["code_sha256"] | audit["code_sha256"])
    if any(file_sha256(Path(name)) != value for name, value in bindings.items()):
        raise ValueError("policy estimability source or previous independent audit code changed")
    facts = json.loads((ROOT / "research_outputs/pre_wuhan_policy_calendar_2019_v1/policy_facts.json").read_text(encoding="utf-8"))
    rows = json.loads((ROOT / "research_outputs/pre_wuhan_policy_calendar_2019_v1/lagged_policy_calendar.json").read_text(encoding="utf-8"))["calendar"]
    split, check = settings["training_steps"], settings["check_steps"]
    if len(rows) != split + check or audit["calendar_steps_checked"] != len(rows):
        raise ValueError("policy estimability split or independently audited calendar differs")
    channels = [(row["instrument"], row["tenor_value"], row["tenor_unit"]) for row in settings["rate_channels"]]
    lookup = {key: index for index, key in enumerate(channels)}
    records = {record["source_id"]: record for record in facts["records"]}
    panel, unknown, announcements = [], [], []
    offshore = []
    for index, row in enumerate(rows):
        values = [0.0] * len(channels)
        ids = set()
        for source_id in row["newly_visible_source_ids"]:
            for operation in records[source_id]["operations"]:
                key = (operation["instrument"], operation["tenor_value"], operation["tenor_unit"])
                if key in lookup and operation["change_from_previous_observed_bps"] is None:
                    values[lookup[key]] = None
                    unknown.append({"trade_date": row["trade_date"], "source_id": source_id, "channel": list(key)})
        for event in row["new_observed_rate_changes"]:
            key = (event["instrument"], event["tenor_value"], event["tenor_unit"])
            if key in lookup:
                if event["market"] != "mainland" or values[lookup[key]] is None:
                    raise ValueError("policy estimability mixes offshore or unknown changes into mainland coefficients")
                values[lookup[key]] += event["change_from_previous_observed_bps"]
                ids.add(event["source_id"])
            else:
                offshore.append({"trade_date": row["trade_date"], "instrument": event["instrument"], "market": event["market"], "change_bps": event["change_from_previous_observed_bps"]})
        panel.append(values)
        if ids:
            announcements.append({"trade_date": row["trade_date"], "source_ids": sorted(ids), "split": "first_30_development_dates" if index < split else "last_13_development_dates"})
    summary = []
    for column, key in enumerate(channels):
        training = [row[column] for row in panel[:split]]
        later = [row[column] for row in panel[split:]]
        summary.append({"instrument": key[0], "tenor_value": key[1], "tenor_unit": key[2],
                        "training_nonzero_steps": sum(value not in {0.0, None} for value in training),
                        "check_nonzero_steps": sum(value not in {0.0, None} for value in later),
                        "training_unknown_steps": training.count(None), "check_unknown_steps": later.count(None),
                        "training_has_variation": len(set(value for value in training if value is not None)) > 1,
                        "response_coefficient": None})
    design = [[1.0, *row] for row in panel[:split]]
    ranks, duplicate_pairs = None, []
    if not unknown:
        rational_rank = exact_rank(design)
        numeric_rank = int(np.linalg.matrix_rank(np.asarray(design, dtype=float), tol=1e-10))
        if rational_rank != numeric_rank:
            raise ValueError("independent exact and numeric policy design ranks disagree")
        ranks = {"rows": split, "columns_including_intercept": len(channels) + 1,
                 "exact_fraction_rank": rational_rank, "numpy_rank": numeric_rank,
                 "jointly_identifiable": rational_rank == len(channels) + 1}
        for left in range(len(channels)):
            if not summary[left]["training_has_variation"]:
                continue
            for right in range(left + 1, len(channels)):
                if [row[left] for row in panel[:split]] == [row[right] for row in panel[:split]]:
                    duplicate_pairs.append({"left": list(channels[left]), "right": list(channels[right])})
    if any(file_sha256(Path(name)) != value for name, value in bindings.items()):
        raise ValueError("policy estimability input changed during analysis")
    return {"pipeline_version": "pre-wuhan-policy-coefficient-estimability-audit-v1", "channel_summary": summary,
            "training_design": ranks, "identical_nonconstant_training_channels": duplicate_pairs,
            "unknown_newly_observed_changes": unknown, "mainland_nonzero_rate_announcement_steps": announcements,
            "offshore_changes_excluded_from_mainland_design": offshore,
            "anticipated_implementation_transitions_separate": audit["anticipated_implementation_transitions"],
            "pulse_panel": [{"trade_date": row["trade_date"], "change_bps_by_channel": vector} for row, vector in zip(rows, panel)],
            "training_unit": "common_policy_step; repeating a policy value across stocks does not create independent policy events",
            "agent_signal_enabled": False, "coefficients_fitted": False, "market_outcomes_used": False,
            "interpretation": settings["interpretation"], "inputs": dict(sorted(bindings.items())),
            "code_sha256": file_sha256(Path(__file__)), "rank_library": {"name": "numpy", "version": np.__version__}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute()
    output = args.output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("policy estimability audit must be directly under research_outputs")
    payload = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    if args.audit_existing:
        if output.read_bytes() != payload:
            raise ValueError("policy estimability audit differs byte-for-byte")
    else:
        with output.open("xb") as handle:
            handle.write(payload)
    print(json.dumps({key: result[key] for key in ("channel_summary", "training_design", "identical_nonconstant_training_channels", "mainland_nonzero_rate_announcement_steps", "coefficients_fitted")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
