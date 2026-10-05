"""Audit sparse policy-event support using designs only, without fitting targets."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from . import audit_extended_policy_rate_diagnostic as exact_audit

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/policy_event_support_2019_2020.json"
OUTPUT = ROOT / "research_outputs/policy_event_support_2019_2020_v1.json"


def compute() -> dict:
    import numpy as np

    protocol = json.loads(CONFIG.read_text(encoding="utf-8"))
    source = ROOT / protocol["source_directory"]
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    previous_audit_path = ROOT / "research_outputs/policy_information_clock_completed_audit_2019_2020_v1.json"
    previous_audit = json.loads(previous_audit_path.read_text(encoding="utf-8"))
    bindings = {str(CONFIG): exact_audit.digest(CONFIG),
                **{str(ROOT / key): value for key, value in protocol["inputs"].items()},
                **manifest["inputs"], **manifest["code_sha256"],
                **previous_audit["inputs"], **previous_audit["code_sha256"]}
    bindings.update({str(source / name): artifact["sha256"] for name, artifact in manifest["artifacts"].items()})

    def validate_bindings() -> None:
        if any(exact_audit.digest(Path(name)) != value for name, value in bindings.items()):
            raise ValueError("policy-event support inputs or previously audited producers changed")

    validate_bindings()
    if (protocol["agent_signal_enabled"] is not False
            or previous_audit["study_stage"] != "source_completed"
            or previous_audit["shared_exclusions"] != []):
        raise ValueError("support audit requires the completed, independently checked paired designs")
    panels = json.loads((source / "clock_panels.json").read_text(encoding="utf-8"))
    completion_path = ROOT / "research/configs/policy_information_clock_completion_2019_2020.json"
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    names = [item["name"] for item in completion["variants"]]
    if set(panels["model_rows"]) != set(names):
        raise ValueError("support audit omitted a prespecified information-clock variant")
    tolerance = 1e-10

    def ranks(matrix: list[list[float]]) -> dict:
        if (not matrix or not matrix[0]
                or any(len(row) != len(matrix[0]) or any(not math.isfinite(v) for v in row) for row in matrix)):
            raise ValueError("support design is empty, ragged or nonfinite")
        exact = exact_audit.matrix_rank(matrix)
        numeric = int(np.linalg.matrix_rank(np.asarray(matrix, dtype=float), tol=tolerance))
        if exact != numeric:
            raise ValueError("exact Fraction and independent SVD design ranks disagree")
        return {"rows": len(matrix), "columns": len(matrix[0]),
                "exact_fraction_rank": exact, "numpy_rank": numeric,
                "full_column_rank": exact == len(matrix[0])}

    variants = {}
    for name in names:
        # Only access design/date/missingness fields. Market response fields are never used.
        all_rows = panels["model_rows"][name]
        rows = [row for row in all_rows if row["trade_date"] <= protocol["training_end_date"]
                and row["paired_exclusion_reason"] is None]
        if (len(rows) != completion["expected_raw_training_steps"]
                or len({row["trade_date"] for row in rows}) != len(rows)
                or any(row["intrinsic_exclusion_reason"] is not None for row in rows)):
            raise ValueError("support audit training dates or missingness differs")
        baseline = [row["baseline_features"] for row in rows]
        policy = [row["policy_features"] for row in rows]
        if any(len(a) != 4 or len(b) != 5 for a, b in zip(baseline, policy)):
            raise ValueError("support audit did not recover the frozen four-plus-five feature design")
        design = [a + b for a, b in zip(baseline, policy)]
        full_rank, policy_rank, base_rank = ranks(design), ranks(policy), ranks(baseline)
        expected_rank = previous_audit["independently_rebuilt_ranks"][name]
        if (base_rank["exact_fraction_rank"] != expected_rank["market_state"]
                or full_rank["exact_fraction_rank"] != expected_rank["market_state_plus_rates"]):
            raise ValueError("support audit did not recover the previous independent design ranks")
        u, _, _ = np.linalg.svd(np.asarray(design, dtype=float), full_matrices=False)
        leverage = np.sum(u[:, :full_rank["numpy_rank"]] ** 2, axis=1)
        if (not np.isfinite(leverage).all() or np.any(leverage < -tolerance)
                or np.any(leverage > 1 + tolerance)
                or abs(float(leverage.sum()) - full_rank["numpy_rank"]) > tolerance):
            raise ValueError("SVD projection diagonal is invalid")
        event_indices = [i for i, values in enumerate(policy) if any(value != 0 for value in values)]
        events = []
        for index in event_indices:
            indicator = [float(i == index) for i in range(len(rows))]
            augmented = ranks([values + [unit] for values, unit in zip(policy, indicator)])
            in_span = augmented["exact_fraction_rank"] == policy_rank["exact_fraction_rank"]
            if in_span and abs(float(leverage[index]) - 1.0) > tolerance:
                raise ValueError("exact event-indicator membership disagrees with unit SVD leverage")
            reduced_policy = ranks([values for i, values in enumerate(policy) if i != index])
            reduced_full = ranks([values for i, values in enumerate(design) if i != index])
            events.append({"trade_date": rows[index]["trade_date"], "policy_features_bps": policy[index],
                           "event_indicator_augmented_policy_rank": augmented,
                           "event_indicator_in_policy_column_span": in_span,
                           "full_design_projection_leverage": float(leverage[index]),
                           "leave_one_date_out_policy_design": reduced_policy,
                           "leave_one_date_out_full_design": reduced_full})
        non_event_leverages = [float(value) for i, value in enumerate(leverage) if i not in event_indices]
        variants[name] = {"training_rows": len(rows), "training_policy_dates": len(event_indices),
                          "baseline_design": base_rank, "policy_design": policy_rank, "full_design": full_rank,
                          "events": events, "projection_trace": float(leverage.sum()),
                          "non_event_leverage_min": min(non_event_leverages) if non_event_leverages else None,
                          "non_event_leverage_max": max(non_event_leverages) if non_event_leverages else None}
    events = [item for variant in variants.values() for item in variant["events"]]
    validate_bindings()
    return {"pipeline_version": "independent-design-only-policy-event-support-audit-v1",
            "training_end_date": protocol["training_end_date"], "numpy_version": np.__version__,
            "numeric_rank_and_leverage_tolerance": tolerance,
            "target_response_fields_used": [], "coefficients_refitted": False, "agent_signal_enabled": False,
            "variants": variants,
            "summary": {"variants_checked": len(variants), "leave_one_policy_date_out_designs_checked": len(events),
                        "leave_one_date_out_full_designs_losing_rank": sum(not item["leave_one_date_out_full_design"]["full_column_rank"] for item in events),
                        "event_indicators_in_policy_span": sum(item["event_indicator_in_policy_column_span"] for item in events),
                        "event_projection_leverages_within_tolerance_of_one": sum(abs(item["full_design_projection_leverage"] - 1) <= tolerance for item in events)},
            "inputs": dict(sorted(bindings.items())),
            "code_sha256": {str(Path(__file__).resolve()): exact_audit.digest(Path(__file__)),
                            str(Path(exact_audit.__file__).resolve()): exact_audit.digest(Path(exact_audit.__file__))},
            "interpretation": protocol["limitations"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute()
    raw = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if args.audit_existing:
        if args.output.read_bytes() != raw:
            raise ValueError("policy-event support audit differs from the frozen result")
        print("Design-only policy-event support audit rebuilt byte-identically.")
    else:
        with args.output.open("xb") as handle:
            handle.write(raw)
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
