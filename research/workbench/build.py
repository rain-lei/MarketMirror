"""Build a local dashboard from audited, explicitly whitelisted research summaries."""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..data_pipeline.provenance import file_sha256
from ..registry.verify_catalog import audit_run, load_catalog
from ..semantic.agent_signal_adapter import VERSION as AGENT_SIGNAL_ADAPTER_VERSION
from ..semantic.audit_model_run import audit_model_run
from ..semantic.review_readiness import audit_review_package

VERSION = "research-workbench-v16"
ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/integrity_catalog_2020.json"
CAPACITY_CONFIG = ROOT / "research/configs/integrity_catalog_capacity_series.json"
CAPACITY_REEXEC_CONFIG = ROOT / "research/configs/reexecution_catalog_capacity_series.json"
COUNTERFACTUAL_CONFIG = ROOT / "research/configs/integrity_catalog_observed_counterfactual.json"
LAGGED_IMPACT_CONFIG = ROOT / "research/configs/integrity_catalog_lagged_impact.json"
VISIBILITY_LAG_CONFIG = ROOT / "research/configs/integrity_catalog_visibility_lag_2020.json"
INDEPENDENT_QUOTE_CONFIG = ROOT / "research/configs/integrity_catalog_independent_quotes.json"
REVIEW_READINESS_CONFIG = ROOT / "research/configs/integrity_catalog_semantic_review_readiness_h2_2020.json"
HOLDOUT_MODEL_CONFIG = ROOT / "research/configs/integrity_catalog_semantic_holdout_model_h2_2020.json"
OUTPUTS = ROOT / "research_outputs"
ASSETS = Path(__file__).resolve().parent / "assets"
PRIVATE_FIELDS = {"question_text", "reply_text", "user_name", "source_path", "source_file_hash",
                  "raw_response", "segments", "annotation_items", "evidence_spans"}


def validate_public_payload(value: Any) -> None:
    """Fail closed if a future summary edit accidentally embeds source text or host paths."""
    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if key in PRIVATE_FIELDS:
                    raise ValueError(f"private field cannot enter workbench: {key}")
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, str):
            if re.search(r"(?<![A-Za-z])[A-Za-z]:[\\/]|wxid_|[/\\]Users[/\\]", node, re.IGNORECASE):
                raise ValueError("local private path or identifier cannot enter workbench")
    visit(value)


def checked_report(directory: Path, manifest_name: str, result_name: str,
                   code_path: Path) -> dict[str, Any]:
    manifest = json.loads((directory / manifest_name).read_text(encoding="utf-8"))
    if manifest["auditor_code_sha256"] != file_sha256(code_path):
        raise ValueError(f"auditor code changed since {manifest_name}")
    if result_name not in manifest["artifacts"]:
        raise ValueError(f"report is absent from audited artifacts: {result_name}")
    for name, info in manifest["artifacts"].items():
        if file_sha256(directory / name) != info["sha256"]:
            raise ValueError(f"audited report artifact changed: {name}")
    return json.loads((directory / result_name).read_text(encoding="utf-8"))


def checked_semantic_review() -> dict[str, Any]:
    directory = OUTPUTS / "semantic_review_comparison_pilot_2020"
    manifest = json.loads((directory / "comparison_manifest.json").read_text(encoding="utf-8"))
    if manifest["code_sha256"] != file_sha256(ROOT / "research/semantic/review_workflow.py"):
        raise ValueError("semantic review code changed since comparison")
    if "comparison_results.json" not in manifest["artifacts"]:
        raise ValueError("semantic review result is absent from audited artifacts")
    for path, digest in manifest["inputs"].items():
        if file_sha256(Path(path)) != digest:
            raise ValueError("semantic review input changed")
    for name, info in manifest["artifacts"].items():
        if file_sha256(directory / name) != info["sha256"]:
            raise ValueError("semantic review report changed")
    return json.loads((directory / "comparison_results.json").read_text(encoding="utf-8"))


def checked_financial_dictionary() -> dict[str, Any]:
    """Load the public financial dictionary only after checking its provenance."""
    directory = OUTPUTS / "financial_2020"
    report_path = directory / "financial_quality_report.json"
    dictionary_path = directory / "field_dictionary.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    dictionary = json.loads(dictionary_path.read_text(encoding="utf-8"))
    generated = dictionary.get("generated_from", {})
    scope = dictionary.get("scope", {})
    if generated.get("report_sha256") != file_sha256(report_path):
        raise ValueError("financial dictionary does not match quality report")
    if generated.get("source_file_sha256") != report.get("source_file_hash"):
        raise ValueError("financial dictionary source hash does not match quality report")
    if generated.get("catalog_sha256") != file_sha256(ROOT / "research/data_contracts/financial_fields.json"):
        raise ValueError("financial dictionary does not match field catalog")
    if scope.get("all_values_status") != "unverified" or any(
            item.get("verification_status") != "unverified" for item in dictionary.get("fields", [])):
        raise ValueError("workbench refuses to display a financial dictionary with promoted values")
    validate_public_payload(dictionary)
    return dictionary


def checked_model_run(version: str = "v1") -> dict[str, Any]:
    """Expose only metadata from an optional DeepSeek semantic run."""
    pack_dir = OUTPUTS / "semantic_annotation_pilot_2020"
    if version not in {"v1", "v2"}:
        raise ValueError("unknown workbench model run version")
    raw_dir = OUTPUTS / f"semantic_model_deepseek_{version}"
    pack_manifest = json.loads((pack_dir / "annotation_manifest.json").read_text(encoding="utf-8"))
    pack_items = int(pack_manifest.get("counts", {}).get("items", 0))
    if not raw_dir.exists():
        return {"status": "not_run", "model_id": None,
                "scope": {"pack_items": pack_items, "requested_rows": 0, "full_pack_requested": False},
                "raw": {"rows": 0, "remaining_rows": pack_items, "request_failures": 0, "status": "not_run"},
                "normalized": {"status": "not_provided"},
                "scoring": {"status": "unavailable_without_adjudicated_gold", "accuracy_claim_allowed": False}}
    normalized_dir = OUTPUTS / f"semantic_model_deepseek_{version}_normalized"
    revalidated_dir = OUTPUTS / "semantic_model_deepseek_v1_revalidated"
    if version == "v1" and revalidated_dir.exists():
        normalized_dir = revalidated_dir
    result = audit_model_run(pack_dir, raw_dir, normalized_dir if normalized_dir.exists() else None)
    result["status"] = "audited"
    validate_public_payload(result)
    return result


def checked_holdout_model_run() -> dict[str, Any]:
    """Publish only frozen holdout coverage after both provenance audits pass."""
    if (OUTPUTS / "semantic_holdout_h2_2020_gold/gold_manifest.json").exists():
        raise ValueError("holdout gold exists; workbench scoring integration is required")
    pinned = load_catalog(HOLDOUT_MODEL_CONFIG)
    expected = {"semantic_holdout_h2_2020_raw_model", "semantic_holdout_h2_2020_normalized",
                "semantic_holdout_h2_2020_diagnostics"}
    if len(pinned) != 3 or {run["run_id"] for run in pinned} != expected:
        raise ValueError("holdout model catalog must contain the three fixed products")
    cache: dict[Path, str] = {}
    if any(audit_run(run, HOLDOUT_MODEL_CONFIG.parent, ROOT / "research", OUTPUTS, cache)["status"]
           != "passed" for run in pinned):
        raise ValueError("holdout model failed pinned integrity checks")
    integrity = checked_report(OUTPUTS / "integrity_catalog_semantic_holdout_model_h2_2020_v1",
                               "integrity_manifest.json", "integrity_results.json",
                               ROOT / "research/registry/verify_catalog.py")
    if (integrity["catalog_sha256"] != file_sha256(HOLDOUT_MODEL_CONFIG)
            or integrity["passed_runs"] != 3):
        raise ValueError("stored holdout model audit does not match the pinned catalog")
    pack_dir = OUTPUTS / "semantic_holdout_h2_2020"
    raw_dir = OUTPUTS / "semantic_holdout_h2_2020_model"
    normalized_dir = OUTPUTS / "semantic_holdout_h2_2020_normalized"
    diagnostics_dir = OUTPUTS / "semantic_holdout_h2_2020_diagnostics"
    frozen = json.loads((ROOT / "research/configs/semantic_holdout_h2_2020.json").read_text(encoding="utf-8"))
    pack = json.loads((pack_dir / "annotation_manifest.json").read_text(encoding="utf-8"))
    raw = json.loads((raw_dir / "model_run_manifest.json").read_text(encoding="utf-8"))
    audit = audit_model_run(pack_dir, raw_dir, normalized_dir)
    diagnostic = json.loads((diagnostics_dir / "diagnostics.json").read_text(encoding="utf-8"))
    coverage = diagnostic["coverage"]
    if (pack["config"] != frozen or pack["counts"]["items"] != 128
            or raw["model_id"] != frozen["frozen_model_id"]
            or raw["prompt_version"] != frozen["frozen_prompt_version"]
            or raw["input_sha256"]["prompt"] != frozen["frozen_prompt_sha256"]
            or raw["provider_base_url"] != "http://aigw.dlut.edu.cn/v1"
            or raw["temperature"] != 0
            or audit["scope"]["pack_items"] != 128
            or not audit["scope"]["full_pack_requested"]
            or audit["raw"]["status"] != "complete"
            or audit["normalized"]["status"] != "complete"
            or audit["raw"]["rows"] != 128 or audit["normalized"]["rows"] != 128
            or coverage["pack_items"] != 128 or coverage["returned_rows"] != 128
            or coverage["valid_rows"] != 128 or coverage["parse_error_rows"] != 0
            or coverage["valid_empty_rows"] + coverage["valid_event_rows"] != 128
            or diagnostic["audit"]["raw"]["sha256"] != audit["raw"]["sha256"]
            or diagnostic["audit"]["normalized"]["sha256"] != audit["normalized"]["sha256"]):
        raise ValueError("holdout summary differs from the frozen complete model run")
    public = {"status": "audited_unscored", "model_id": audit["model_id"],
              "prompt_version": audit["prompt_version"], "items": 128,
              "request_failures": audit["raw"]["request_failures"],
              "parse_errors": audit["normalized"]["parse_errors"],
              "valid_empty_rows": coverage["valid_empty_rows"],
              "valid_event_rows": coverage["valid_event_rows"],
              "validated_events": coverage["validated_events"],
              "gold_ready": False, "accuracy_claim_allowed": False}
    validate_public_payload(public)
    return public


def checked_capacity_series() -> list[dict[str, Any]]:
    """Summarize only pinned, rerun-equivalent capacity scenarios."""
    pinned = load_catalog(CAPACITY_CONFIG)
    cache: dict[Path, str] = {}
    audits = [audit_run(run, CAPACITY_CONFIG.parent, ROOT / "research", OUTPUTS, cache)
              for run in pinned]
    integrity = checked_report(OUTPUTS / "integrity_catalog_capacity_series",
                               "integrity_manifest.json", "integrity_results.json",
                               ROOT / "research/registry/verify_catalog.py")
    reexecution = checked_report(OUTPUTS / "reexecution_catalog_capacity_series",
                                 "reexecution_manifest.json", "reexecution_results.json",
                                 ROOT / "research/registry/reexecute.py")
    expected = {run["run_id"] for run in pinned}
    if (len(pinned) != 6 or any(audit["status"] != "passed" for audit in audits)
            or integrity["catalog_sha256"] != file_sha256(CAPACITY_CONFIG)
            or integrity["passed_runs"] != len(pinned)
            or reexecution["integrity_catalog_sha256"] != file_sha256(CAPACITY_CONFIG)
            or reexecution["config_sha256"] != file_sha256(CAPACITY_REEXEC_CONFIG)
            or reexecution["passed_runs"] != len(pinned)
            or {run["run_id"] for run in reexecution["runs"]} != expected
            or any(run["status"] != "equivalent" for run in reexecution["runs"])):
        raise ValueError("capacity series is not fully pinned and independently reexecuted")
    manifests = {run["run_id"]: (CAPACITY_CONFIG.parent / run["manifest"]).resolve()
                 for run in pinned}
    periods = (("2018 H1", "2018_h1"), ("2020 Q1", "2020_q1"),
               ("2020 Apr–Dec", "2020_later"))
    rows = []
    for period, suffix in periods:
        capacity_manifest = manifests[f"historical_capacity_{suffix}"]
        participation_manifest = manifests[f"historical_participation_{suffix}"]
        capacity = json.loads((capacity_manifest.parent / "capacity_results.json").read_text(encoding="utf-8"))
        participation = json.loads((participation_manifest.parent / "participation_results.json").read_text(encoding="utf-8"))
        for code in ("000001", "000002", "600519"):
            matching_capacity = [row for row in capacity["results"]
                                 if row["stock_code"] == code and row["path"] == "market_signal"
                                 and row["aum_cny_per_agent"] == 1_000_000_000]
            matching_control = [row for row in participation["scenarios"]
                                if row["stock_code"] == code and row["use_market_signal"]
                                and row["aum_cny_per_agent"] == 1_000_000_000
                                and row["participation_rate"] is None]
            matching_capped = [row for row in participation["scenarios"]
                               if row["stock_code"] == code and row["use_market_signal"]
                               and row["aum_cny_per_agent"] == 1_000_000_000
                               and row["participation_rate"] == 0.01]
            if any(len(group) != 1 for group in (matching_capacity, matching_control, matching_capped)):
                raise ValueError("capacity summary scenario is absent or ambiguous")
            diagnostic, control, capped = matching_capacity[0], matching_control[0], matching_capped[0]
            if (diagnostic["sessions"] != control["sessions"]
                    or control["sessions"] != capped["sessions"]
                    or set(control["summary"]) != set(capped["summary"])):
                raise ValueError("capacity summary scenarios use different sessions or agents")
            aggressive = next(name for name, agent in control["summary"].items()
                              if agent["role"] == "aggressive")
            rows.append({"period": period, "stock_code": code,
                         "sessions": capped["sessions"],
                         "diagnostic_p95_fraction": diagnostic["p95_fraction"],
                         "binding_days": capped["binding_days"],
                         "aggregate_fill_rate": capped["aggregate_fill_rate"],
                         "aggressive_uncapped_multiple": 1 + control["summary"][aggressive]["return"],
                         "aggressive_capped_multiple": 1 + capped["summary"][aggressive]["return"]})
    validate_public_payload(rows)
    return rows


def checked_counterfactual_series() -> list[dict[str, Any]]:
    """Expose paired scenario metrics only when both original and fresh rerun match."""
    pinned = load_catalog(COUNTERFACTUAL_CONFIG)
    expected = {"observed_counterfactual_2018", "observed_counterfactual_2020"}
    if len(pinned) != 2 or {run["run_id"] for run in pinned} != expected:
        raise ValueError("counterfactual catalog must contain the two fixed events")
    cache: dict[Path, str] = {}
    rows = []
    for run in pinned:
        audit = audit_run(run, COUNTERFACTUAL_CONFIG.parent, ROOT / "research", OUTPUTS, cache)
        if audit["status"] != "passed":
            raise ValueError("counterfactual original run failed pinned integrity checks")
        original_path = (COUNTERFACTUAL_CONFIG.parent / run["manifest"]).resolve()
        original = json.loads(original_path.read_text(encoding="utf-8"))
        rerun_dir = OUTPUTS / ("observed_2018" if run["run_id"].endswith("2018")
                               else "observed_2020") / "counterfactual_verified_rerun_v1"
        rerun = json.loads((rerun_dir / "counterfactual_manifest.json").read_text(encoding="utf-8"))
        artifacts = {"counterfactual_results.json", "counterfactual_report.md"}
        if (original.get("pipeline_version") != "observed-return-assumed-impact-v1"
                or rerun.get("pipeline_version") != original["pipeline_version"]
                or rerun.get("scenario_id") != original["scenario_id"]
                or rerun.get("inputs") != original["inputs"]
                or rerun.get("code_sha256") != original["code_sha256"]
                or set(original["artifacts"]) != artifacts
                or rerun.get("artifacts") != original["artifacts"]
                or any(file_sha256(rerun_dir / name) != original["artifacts"][name]["sha256"]
                       for name in artifacts)):
            raise ValueError("counterfactual fresh rerun differs from pinned results")
        result = json.loads((original_path.parent / "counterfactual_results.json").read_text(encoding="utf-8"))
        if (result.get("scenario_id") != original["scenario_id"]
                or result.get("data_kind") != "observed_return_counterfactual"
                or result.get("stock_code") != "000001"
                or len(result.get("paths", [])) != 6
                or len(result.get("paired_effects", [])) != 3
                or result["timing"]["first_signal_cutoff_date"] < result["available_on_date"]):
            raise ValueError("counterfactual result shape or visibility gate changed")
        zero = result["paths"][:2]
        if (any(path["impact_coefficient"] != 0 for path in zero)
                or any(not math.isclose(path["final_price_index"],
                                        result["observed_return_only_price_index"], rel_tol=1e-12)
                       for path in zero)):
            raise ValueError("counterfactual zero-impact control no longer matches observed returns")
        period = "2018 H1" if run["run_id"].endswith("2018") else "2020 Q1"
        for index, pair in enumerate(result["paired_effects"]):
            baseline, scenario = result["paths"][2 * index:2 * index + 2]
            if (baseline["scenario_enabled"] or not scenario["scenario_enabled"]
                    or baseline["impact_coefficient"] != pair["impact_coefficient"]
                    or scenario["impact_coefficient"] != pair["impact_coefficient"]
                    or not math.isclose(pair["terminal_price_delta"],
                                        scenario["final_price_index"] - baseline["final_price_index"],
                                        abs_tol=1e-9)):
                raise ValueError("counterfactual paired scenarios are inconsistent")
            public = {"period": period, "event_id": result["event_id"],
                      "stock_code": result["stock_code"],
                      "first_signal_trade_date": result["timing"]["first_signal_trade_date"],
                      "aum_cny_per_agent": result["aum_cny_per_agent"],
                      "liquidity_notional_cny": result["liquidity_notional_cny"],
                      "scenario_signal": result["scenario_signal"],
                      "impact_coefficient": pair["impact_coefficient"],
                      "event_window_net_order_delta_cny": pair["event_window_net_order_delta_cny"],
                      "event_end_price_delta": pair["event_end_price_delta"],
                      "terminal_price_delta": pair["terminal_price_delta"]}
            if any(type(value) not in (int, float) or not math.isfinite(value)
                   for key, value in public.items() if key not in {"period", "event_id", "stock_code",
                                                                    "first_signal_trade_date"}):
                raise ValueError("counterfactual public metric is not finite")
            rows.append(public)
    validate_public_payload(rows)
    return rows


def checked_lagged_impact_series() -> list[dict[str, Any]]:
    """Expose all pinned sensitivity pairs only after byte-identical independent reruns."""
    pinned = load_catalog(LAGGED_IMPACT_CONFIG)
    expected = {"lagged_impact_2018", "lagged_impact_2020"}
    if len(pinned) != 2 or {run["run_id"] for run in pinned} != expected:
        raise ValueError("lagged impact catalog must contain the two fixed events")
    cache: dict[Path, str] = {}
    rows = []
    for run in pinned:
        if audit_run(run, LAGGED_IMPACT_CONFIG.parent, ROOT / "research", OUTPUTS, cache)["status"] != "passed":
            raise ValueError("lagged impact original run failed pinned integrity checks")
        original_path = (LAGGED_IMPACT_CONFIG.parent / run["manifest"]).resolve()
        original = json.loads(original_path.read_text(encoding="utf-8"))
        rerun_dir = OUTPUTS / ("observed_2018" if run["run_id"].endswith("2018")
                               else "observed_2020") / "lagged_impact_verified_rerun_v1"
        rerun = json.loads((rerun_dir / "lagged_impact_manifest.json").read_text(encoding="utf-8"))
        artifacts = {"lagged_impact_results.json", "lagged_impact_report.md"}
        if (original.get("pipeline_version") != "lagged-turnover-assumed-impact-v1"
                or rerun.get("pipeline_version") != original["pipeline_version"]
                or rerun.get("scenario_id") != original["scenario_id"]
                or rerun.get("inputs") != original["inputs"]
                or rerun.get("code_sha256") != original["code_sha256"]
                or set(original["artifacts"]) != artifacts
                or rerun.get("artifacts") != original["artifacts"]
                or any(file_sha256(rerun_dir / name) != original["artifacts"][name]["sha256"]
                       for name in artifacts)):
            raise ValueError("lagged impact fresh rerun differs from pinned results")
        result = json.loads((original_path.parent / "lagged_impact_results.json").read_text(encoding="utf-8"))
        paths = result.get("paths", [])
        pairs = result.get("paired_effects", [])
        rates = (0.01, 0.05)
        coefficients = (0.0, 0.01, 0.03)
        expected_grid = {(rate, depth, coefficient) for rate in rates for depth in rates
                         for coefficient in coefficients}
        if (result.get("scenario_id") != original["scenario_id"]
                or result.get("data_kind") != "observed_return_sensitivity"
                or result.get("stock_code") != "000001"
                or result.get("participation_rates") != list(rates)
                or result.get("impact_depth_fractions") != list(rates)
                or result.get("impact_coefficients") != list(coefficients)
                or len(paths) != 24 or len(pairs) != 12
                or {(pair["participation_rate"], pair["impact_depth_fraction"],
                     pair["impact_coefficient"]) for pair in pairs} != expected_grid
                or result["timing"]["first_signal_cutoff_date"] < result["available_on_date"]):
            raise ValueError("lagged impact result shape or visibility gate changed")
        period = "2018 H1" if run["run_id"].endswith("2018") else "2020 Q1"
        for index, pair in enumerate(pairs):
            baseline, scenario = paths[2 * index:2 * index + 2]
            parameters = ("participation_rate", "impact_depth_fraction", "impact_coefficient")
            if (baseline["scenario_enabled"] or not scenario["scenario_enabled"]
                    or any(baseline[key] != pair[key] or scenario[key] != pair[key]
                           for key in parameters)
                    or not math.isclose(pair["terminal_price_delta"],
                                        scenario["final_price_index"] - baseline["final_price_index"],
                                        abs_tol=1e-9)
                    or pair["control_binding_days"] != baseline["binding_days"]
                    or pair["event_binding_days"] != scenario["binding_days"]):
                raise ValueError("lagged impact paired scenarios are inconsistent")
            if pair["impact_coefficient"] == 0 and any(
                    not math.isclose(path["final_price_index"],
                                     result["observed_return_only_price_index"],
                                     rel_tol=1e-12, abs_tol=1e-10)
                    for path in (baseline, scenario)):
                raise ValueError("lagged impact zero-impact control differs from observed returns")
            public = {"period": period, "event_id": result["event_id"],
                      "stock_code": result["stock_code"],
                      "first_signal_trade_date": result["timing"]["first_signal_trade_date"],
                      "aum_cny_per_agent": result["aum_cny_per_agent"],
                      "participation_rate": pair["participation_rate"],
                      "impact_depth_fraction": pair["impact_depth_fraction"],
                      "impact_coefficient": pair["impact_coefficient"],
                      "event_window_net_order_delta_cny": pair["event_window_net_order_delta_cny"],
                      "event_end_price_delta": pair["event_end_price_delta"],
                      "terminal_price_delta": pair["terminal_price_delta"],
                      "control_binding_days": pair["control_binding_days"],
                      "event_binding_days": pair["event_binding_days"]}
            if any(type(value) not in (int, float) or not math.isfinite(value)
                   for key, value in public.items() if key not in {"period", "event_id", "stock_code",
                                                                    "first_signal_trade_date"}):
                raise ValueError("lagged impact public metric is not finite")
            rows.append(public)
    validate_public_payload(rows)
    return rows


def checked_visibility_lag_series() -> list[dict[str, Any]]:
    """Display the fixed timing diagnostic only after source and rerun checks."""
    pinned = load_catalog(VISIBILITY_LAG_CONFIG)
    if len(pinned) != 1 or pinned[0]["run_id"] != "text_publication_delay_sensitivity_2020":
        raise ValueError("visibility catalog must contain the fixed 2020 diagnostic")
    if audit_run(pinned[0], VISIBILITY_LAG_CONFIG.parent, ROOT / "research", OUTPUTS, {})["status"] != "passed":
        raise ValueError("visibility lag run failed pinned integrity checks")
    integrity = checked_report(OUTPUTS / "integrity_catalog_visibility_lag_2020_v1",
                               "integrity_manifest.json", "integrity_results.json",
                               ROOT / "research/registry/verify_catalog.py")
    if (integrity["catalog_sha256"] != file_sha256(VISIBILITY_LAG_CONFIG)
            or integrity["passed_runs"] != 1):
        raise ValueError("stored visibility audit does not match pinned catalog")
    original_path = (VISIBILITY_LAG_CONFIG.parent / pinned[0]["manifest"]).resolve()
    original = json.loads(original_path.read_text(encoding="utf-8"))
    rerun_dir = OUTPUTS / "text_pilot_2020/visibility_lag_verified_rerun_v1"
    rerun = json.loads((rerun_dir / "visibility_lag_manifest.json").read_text(encoding="utf-8"))
    artifacts = {"visibility_lag_results.json", "visibility_lag_report.md"}
    if (original.get("pipeline_version") != "text-visibility-lag-v1"
            or rerun != original or set(original["artifacts"]) != artifacts
            or any(file_sha256(rerun_dir / name) != original["artifacts"][name]["sha256"]
                   for name in artifacts)):
        raise ValueError("visibility lag independent rerun differs from original")
    result = json.loads((original_path.parent / "visibility_lag_results.json").read_text(encoding="utf-8"))
    scenarios = result.get("scenarios", [])
    if (result.get("pipeline_version") != original["pipeline_version"]
            or result.get("source_run_id") != "text_prediction"
            or result.get("delay_unit") != "calendar_days"
            or result.get("panel_rows") != 678 or result.get("test_rows") != 177
            or result.get("baseline_reproduced") is not True
            or result.get("market_only_invariant") is not True
            or [row["lag_days"] for row in scenarios] != [0, 1, 3, 7]
            or scenarios[0]["changed_text_feature_rows"] != 0
            or any(row["test_rows"] != 177
                   or row["market_test_mae"] != scenarios[0]["market_test_mae"]
                   or len(row["paired_interval_95"]) != 2
                   or not row["paired_interval_95"][0] <= 0 <= row["paired_interval_95"][1]
                   for row in scenarios)):
        raise ValueError("visibility lag result no longer matches the fixed comparison")
    public = [{"lag_days": row["lag_days"],
               "changed_text_feature_rows": row["changed_text_feature_rows"],
               "market_test_mae": row["market_test_mae"],
               "text_test_mae": row["text_test_mae"],
               "paired_mae_difference": row["paired_mae_difference"],
               "paired_interval_95": row["paired_interval_95"]} for row in scenarios]
    if any(type(value) not in (int, float) or not math.isfinite(value)
           for row in public for value in (row["changed_text_feature_rows"], row["market_test_mae"],
                                           row["text_test_mae"], row["paired_mae_difference"],
                                           *row["paired_interval_95"])):
        raise ValueError("visibility lag public metric is not finite")
    validate_public_payload(public)
    return public


def checked_independent_quotes() -> list[dict[str, Any]]:
    """Expose the current-vintage cross-provider diagnostic with its basis caveat."""
    pinned = load_catalog(INDEPENDENT_QUOTE_CONFIG)
    if len(pinned) != 1 or pinned[0]["run_id"] != "independent_quote_check_2018_2020":
        raise ValueError("independent quote catalog has an unexpected run")
    if audit_run(pinned[0], INDEPENDENT_QUOTE_CONFIG.parent, ROOT / "research", OUTPUTS, {})["status"] != "passed":
        raise ValueError("independent quote run failed pinned integrity checks")
    integrity = checked_report(OUTPUTS / "integrity_catalog_independent_quotes_v1",
                               "integrity_manifest.json", "integrity_results.json",
                               ROOT / "research/registry/verify_catalog.py")
    if (integrity["catalog_sha256"] != file_sha256(INDEPENDENT_QUOTE_CONFIG)
            or integrity["passed_runs"] != 1):
        raise ValueError("stored independent quote audit does not match catalog")
    original_path = (INDEPENDENT_QUOTE_CONFIG.parent / pinned[0]["manifest"]).resolve()
    original = json.loads(original_path.read_text(encoding="utf-8"))
    rerun_dir = OUTPUTS / "independent_eastmoney_2018_2020/check_verified_rerun_v1"
    rerun = json.loads((rerun_dir / "independent_quote_manifest.json").read_text(encoding="utf-8"))
    artifacts = {"independent_quote_results.json", "independent_quote_report.md"}
    if (original.get("pipeline_version") != "independent-quote-check-v1"
            or rerun != original or set(original["artifacts"]) != artifacts
            or any(file_sha256(rerun_dir / name) != original["artifacts"][name]["sha256"]
                   for name in artifacts)):
        raise ValueError("independent quote rerun differs from original")
    result = json.loads((original_path.parent / "independent_quote_results.json").read_text(encoding="utf-8"))
    periods = result.get("periods", [])
    if (result.get("pipeline_version") != original["pipeline_version"]
            or result.get("data_kind") != "cross_provider_current_vintage_diagnostic"
            or [p["year"] for p in periods] != ["2018", "2020"]
            or [len(p["events"]) for p in periods] != [3, 6]
            or any(set(p["quotes"]) != {"000001", "000002", "600519", "000300"}
                   or any(q["sessions"] != sessions for q in p["quotes"].values())
                   for p, sessions in zip(periods, (168, 171)))):
        raise ValueError("independent quote result has unexpected scope")
    tolerance = result["rounding_tolerance_percentage_points"]
    rows = []
    for period in periods:
        for event in period["events"]:
            if event["event_window_beyond_rounding_days"] != 0 or event["event_window_max_absolute_difference_pp"] > tolerance:
                raise ValueError("independent quote event-window returns exceed rounding tolerance")
            row = {"period": period["year"], "event_id": event["event_id"],
                   "stock_code": event["stock_code"],
                   "baostock_car": event["baostock_adjusted_car"],
                   "eastmoney_unadjusted_car": event["eastmoney_unadjusted_car"],
                   "car_difference_pp": event["car_difference_pp"],
                   "event_window_max_absolute_difference_pp": event["event_window_max_absolute_difference_pp"],
                   "estimation_basis_exception": bool(event["estimation_material_difference_dates"])}
            if any(type(row[key]) not in (int, float) or not math.isfinite(row[key])
                   for key in ("baostock_car", "eastmoney_unadjusted_car", "car_difference_pp",
                               "event_window_max_absolute_difference_pp")):
                raise ValueError("independent quote public metric is not finite")
            rows.append(row)
    validate_public_payload(rows)
    return rows


def checked_review_readiness() -> dict[str, Any]:
    """Expose the human-review handoff only after package and page audits pass."""
    pinned = load_catalog(REVIEW_READINESS_CONFIG)
    if len(pinned) != 1 or pinned[0]["run_id"] != "semantic_review_readiness_h2_2020":
        raise ValueError("review readiness catalog has an unexpected run")
    if audit_run(pinned[0], REVIEW_READINESS_CONFIG.parent, ROOT / "research", OUTPUTS, {})["status"] != "passed":
        raise ValueError("review readiness run failed pinned integrity checks")
    integrity = checked_report(OUTPUTS / "integrity_catalog_semantic_review_readiness_h2_2020_v2",
                               "integrity_manifest.json", "integrity_results.json",
                               ROOT / "research/registry/verify_catalog.py")
    if (integrity["catalog_sha256"] != file_sha256(REVIEW_READINESS_CONFIG)
            or integrity["passed_runs"] != 1):
        raise ValueError("stored review readiness audit does not match catalog")
    original_path = (REVIEW_READINESS_CONFIG.parent / pinned[0]["manifest"]).resolve()
    result = json.loads((original_path.parent / "review_readiness.json").read_text(encoding="utf-8"))
    if (result.get("pipeline_version") != "semantic-review-readiness-v1"
            or result.get("status") != "ready_for_human_review"
            or result.get("items") != 128
            or result.get("reviewer_slots") != 2
            or result.get("reviewed_items") != 0
            or result.get("blank_label_rows_per_reviewer") != 128
            or result.get("interface_pages") != 2
            or result.get("gold_ready") is not False):
        raise ValueError("review readiness result no longer describes the blank H2 package")
    package = audit_review_package(OUTPUTS / "semantic_holdout_h2_2020",
                                  OUTPUTS / "semantic_holdout_h2_2020_review",
                                  OUTPUTS / "semantic_holdout_h2_2020_interface")
    if package != result:
        raise ValueError("stored review readiness result differs from a fresh package audit")
    public = {key: result[key] for key in ("status", "items", "reviewer_slots", "reviewed_items",
                                           "blank_label_rows_per_reviewer", "interface_pages", "gold_ready")}
    validate_public_payload(public)
    return public


def checked_agent_signal_gate(review_readiness: dict[str, Any], holdout_model: dict[str, Any]) -> dict[str, Any]:
    """Publish the current Agent signal eligibility without manufacturing a score."""
    if review_readiness.get("gold_ready") is not False:
        raise ValueError("workbench expects the current H2 package to have no gold standard")
    if review_readiness.get("reviewed_items") != 0 or review_readiness.get("items") != 128:
        raise ValueError("workbench Agent gate summary no longer matches the blank H2 package")
    if holdout_model.get("items") != 128 or holdout_model.get("request_failures") != 0:
        raise ValueError("workbench Agent gate summary no longer matches the frozen model run")
    result = {"status": "blocked_until_human_gold", "passed": False,
              "gold_ready": False, "reviewed_items": 0, "required_items": 128,
              "adapter_version": AGENT_SIGNAL_ADAPTER_VERSION,
              "scope": "受控 Agent 语义信号消融资格；不代表投资者校准、历史因果复现或监管预测。",
              "reason": "尚无独立双人审核、第三人裁定和留出评分金标准；适配器会拒绝生成真实信号流。"}
    validate_public_payload(result)
    return result


def collect_data() -> dict[str, Any]:
    pinned = load_catalog(CONFIG)
    cache: dict[Path, str] = {}
    audits = [audit_run(run, CONFIG.parent, ROOT / "research", OUTPUTS, cache) for run in pinned]
    if any(run["status"] != "passed" for run in audits):
        raise ValueError("workbench refuses to display runs with failed pinned integrity checks")
    integrity = checked_report(OUTPUTS / "integrity_catalog_2018_2020_v3", "integrity_manifest.json",
                               "integrity_results.json", ROOT / "research/registry/verify_catalog.py")
    reexecution = checked_report(OUTPUTS / "reexecution_catalog_2018_2020_v4", "reexecution_manifest.json",
                                 "reexecution_results.json", ROOT / "research/registry/reexecute.py")
    if (integrity["catalog_sha256"] != file_sha256(CONFIG)
            or integrity["passed_runs"] != len(pinned)
            or reexecution["integrity_catalog_sha256"] != file_sha256(CONFIG)
            or reexecution["config_sha256"] != file_sha256(ROOT / "research/configs/reexecution_catalog_2020.json")
            or reexecution["passed_runs"] != len(pinned)):
        raise ValueError("stored audit or reexecution report is incomplete or belongs to another catalog")
    expected_ids = {run["run_id"] for run in pinned}
    if ({run["run_id"] for run in integrity["runs"]} != expected_ids
            or {run["run_id"] for run in reexecution["runs"]} != expected_ids
            or any(run["status"] != "equivalent" for run in reexecution["runs"])):
        raise ValueError("stored audit or reexecution run statuses do not match the pinned catalog")
    reviewed = checked_semantic_review()
    financial_dictionary = checked_financial_dictionary()
    model_runs = [checked_model_run(version) for version in ("v1", "v2")]
    completed_model_runs = [run for run in model_runs if run["status"] != "not_run"]
    model_run = completed_model_runs[-1] if completed_model_runs else model_runs[0]
    holdout_model = checked_holdout_model_run()
    capacity_series = checked_capacity_series()
    counterfactual_series = checked_counterfactual_series()
    lagged_impact_series = checked_lagged_impact_series()
    visibility_lag_series = checked_visibility_lag_series()
    independent_quotes = checked_independent_quotes()
    review_readiness = checked_review_readiness()
    agent_signal_gate = checked_agent_signal_gate(review_readiness, holdout_model)
    if ({row["event_id"] for row in counterfactual_series}
            != {row["event_id"] for row in lagged_impact_series}):
        raise ValueError("fixed and lagged impact events differ; shared filter is unsafe")
    manifests = {run["run_id"]: (CONFIG.parent / run["manifest"]).resolve() for run in pinned}

    def artifact(run_id: str, name: str) -> dict[str, Any]:
        return json.loads((manifests[run_id].parent / name).read_text(encoding="utf-8"))

    event = artifact("observed_event", "event_results.json")
    event_2018 = artifact("observed_event_2018", "event_results.json")
    activity = artifact("activity_event", "event_activity.json")
    activity_2018 = artifact("activity_event_2018", "event_activity.json")
    prediction = artifact("text_prediction", "prediction_results.json")
    base_prediction = prediction["evaluation"]["test_metrics"]
    if (visibility_lag_series[0]["market_test_mae"] != base_prediction["market_only"]["pooled"]["mae"]
            or visibility_lag_series[0]["text_test_mae"] != base_prediction["market_plus_text"]["pooled"]["mae"]):
        raise ValueError("visibility lag zero-delay result differs from displayed baseline")
    stress = artifact("synthetic_stress", "stress_results.json")
    replay_q1 = artifact("historical_replay_q1", "historical_replay.json")
    replay_later = artifact("historical_replay_later", "historical_replay.json")
    replay_2018 = artifact("historical_replay_2018", "historical_replay.json")
    placebo = artifact("event_date_diagnostic", "placebo_results.json")
    placebo_2018 = artifact("event_date_diagnostic_2018", "placebo_results.json")
    dataset_manifest = json.loads(manifests["unified_dataset"].read_text(encoding="utf-8"))
    run_status = {r["run_id"]: r for r in reexecution["runs"]}
    market_periods = []
    market_dates = set()
    for run_id in ("observed_market_2018", "observed_market"):
        dates = json.loads(manifests[run_id].read_text(encoding="utf-8"))["session_dates"]
        market_dates.update(dates)
        market_periods.append({"start": dates[0], "end": dates[-1], "sessions": len(dates)})
    summary = {
        "version": VERSION,
        "context": "探索性研究工作台：结果不构成因果结论、可交易收益或监管预测。",
        "overview": {"question_rows": dataset_manifest["counts"]["qa_rows"],
                     "stocks": dataset_manifest["counts"]["companies"],
                     "market_sessions": len(market_dates),
                     "market_periods": market_periods,
                     "integrity_passed": integrity["passed_runs"],
                     "reexecution_passed": reexecution["passed_runs"],
                     "run_total": len(pinned)},
        "runs": [{"id": run["run_id"], "integrity": audit["status"],
                  "reexecution": run_status[run["run_id"]]["status"],
                  "hash_checks": sum(audit["checks"].values()),
                  "compared_artifacts": len(run_status[run["run_id"]]["artifacts"]),
                  "artifacts": [{"name": item["artifact"], "status": item["status"],
                                 "expected_sha256": item["expected_sha256"],
                                 "regenerated_sha256": item["regenerated_sha256"]}
                                for item in run_status[run["run_id"]]["artifacts"]]}
                 for run, audit in zip(pinned, audits)],
        "events": [{"event_id": row["event_id"], "stock_code": row["stock_code"],
                    "event_date": row["event_date_used"], "car": row["cumulative_abnormal_return"],
                    "window_before": row["window_before"], "window_after": row["window_after"],
                    "daily": [{"relative_day": d["relative_trade_day"], "date": d["trade_date"],
                               "abnormal_return": d["abnormal_return"]} for d in row["abnormal_returns"]]}
                   for report in (event_2018, event) for row in report["results"]],
        "activity": [{"event_id": row["event_id"], "stock_code": row["stock_code"],
                      "amount_fold": row["event_day_amount_fold"],
                      "volume_fold": row["event_day_volume_fold"]}
                     for report in (activity_2018, activity) for row in report["runs"]],
        "placebo": {event_id: {"before": report["diagnostic"]["excluded_or_retained_dates"].get("before_actual_event", 0),
                              "after": report["diagnostic"]["excluded_or_retained_dates"].get("after_actual_event", 0),
                              "blackout_start": report["diagnostic"]["blackout_start"],
                              "blackout_end": report["diagnostic"]["blackout_end"]}
                    for report in (placebo_2018, placebo)
                    for event_id in {row["event_id"] for row in report["comparisons"]}},
        "prediction": {"rows": prediction["evaluation"]["test_metrics"]["market_only"]["pooled"]["rows"],
                       "market_mae": prediction["evaluation"]["test_metrics"]["market_only"]["pooled"]["mae"],
                       "text_mae": prediction["evaluation"]["test_metrics"]["market_plus_text"]["pooled"]["mae"],
                       "paired_difference": prediction["evaluation"]["paired_mae_difference"]["mean"],
                       "interval_95": prediction["evaluation"]["paired_mae_difference"]["interval_95"]},
        "stress": {"steps": stress["steps"],
                   "agents": [{"name": name, "role": row["role"], "trades": row["trades"],
                               "max_drawdown": row["max_drawdown"]} for name, row in stress["summary"].items()]},
        "replays": [],
        "capacity_series": capacity_series,
        "counterfactual_series": counterfactual_series,
        "lagged_impact_series": lagged_impact_series,
        "visibility_lag_series": visibility_lag_series,
        "independent_quotes": independent_quotes,
        "review_readiness": review_readiness,
        "agent_signal_gate": agent_signal_gate,
        "semantic": {"items": reviewed["pack_items"], "dual_reviewed": reviewed["dual_reviewed_items"],
                     "pending": reviewed["pending_items"], "conflicts": reviewed["conflict_items"],
                     "gold_ready": reviewed["gold_ready"], "status": reviewed["status"]},
        "financial_dictionary": financial_dictionary,
        "model_run": model_run,
        "model_runs": completed_model_runs,
        "holdout_model": holdout_model,
        "evidence": [{"label": "人民银行：2018 资管新规答记者问", "url": event_2018["config"]["events"][0]["evidence_source"]},
                     {"label": "新华社：武汉通告", "url": "https://www.xinhuanet.com/politics/2020-01/23/c_1125495557.htm"},
                     {"label": "上交所：春节休市调整", "url": "http://www.sse.com.cn/disclosure/announcement/general/c/c_20200127_4991582.shtml"},
                     {"label": "BaoStock API 文档", "url": "https://www.baostock.com/mainContent?file=pythonAPI.md"},
                     {"label": "AKShare 东方财富历史行情字段文档", "url": "https://akshare.akfamily.xyz/data/stock/stock.html"}],
        "limitations": ["原始问答公开时点、财务单位与标签定义尚未独立核实。",
                        "三类 Agent 参数是示意值；没有可观察持仓、净订单流或盘口深度作行为与冲击校准。",
                        "假设冲击情景把模拟冲击叠加在已实现收益上，可能重复计入真实市场运动；不是历史价格复现或预警验证。",
                        "2018 资管新规是去杠杆背景下的一个节点；窗口包含发布前交易日，存在预期和同期冲击。2018 回放未接入当年问答或政策文本。",
                        "文本预测增益区间包含零；语义标注尚无双人完成条目。"],
    }
    displayed_events = {(row["event_id"], row["stock_code"]): row for row in summary["events"]}
    if set(displayed_events) != {(row["event_id"], row["stock_code"]) for row in independent_quotes}:
        raise ValueError("independent quote event set differs from displayed original events")
    if any(not math.isclose(row["baostock_car"], displayed_events[(row["event_id"], row["stock_code"])]["car"],
                            abs_tol=1e-12, rel_tol=0) for row in independent_quotes):
        raise ValueError("independent quote baseline CAR differs from displayed event")
    for label, replay in (("2018 H1", replay_2018), ("2020 Q1", replay_q1), ("2020 Apr–Dec", replay_later)):
        for code, paths in replay["paths"].items():
            active, control = paths["market_signal"]["summary"], paths["zero_signal"]["summary"]
            for name, row in active.items():
                initial = replay["agents"][name]["initial_cash"]
                summary["replays"].append({"period": label, "stock_code": code, "role": row["role"],
                                           "agent": name, "signal_multiple": row["final_wealth"] / initial,
                                           "control_multiple": control[name]["final_wealth"] / initial,
                                           "buyhold_multiple": row["buy_hold_reference_wealth"] / initial,
                                           "max_drawdown": row["max_drawdown"], "trades": row["trades"]})
    validate_public_payload(summary)
    return summary


def render_report(data: dict[str, Any]) -> str:
    """Render the same whitelisted summary used by the browser into Markdown."""
    overview = data["overview"]
    role_labels = {"aggressive": "激进型", "conservative": "保守型", "institutional": "机构型"}
    lines = ["# MarketMirror 研究摘要报告", "", data["context"], "",
             "> 本文件由已通过本机完整性核验的摘要生成；它不包含问答原文、个人路径或完整数据库。", "",
             "## 研究概况", "",
             f"- 问答来源行：{overview['question_rows']:,}",
             f"- 来源股票代码：{overview['stocks']:,}",
             f"- 行情交易日：{overview['market_sessions']}",
             f"- 固定运行核验：{overview['integrity_passed']}/{overview['run_total']}",
             f"- 独立重跑：{overview['reexecution_passed']}/{overview['run_total']}", "",
             "## 历史事件窗口", "",
             "CAR 是事件前后整个窗口的异常日收益之和，包含发布前的交易日，不能解释为政策发布后的跌幅或因果效应。", "",
             "| 事件口径 | 股票 | 对齐日 | CAR |", "|---|---:|---|---:|"]
    for row in data["events"]:
        lines.append(f"| {row['event_id']} | {row['stock_code']} | {row['event_date']} | {row['car']:.4%} |")
    if overview.get("market_periods"):
        lines += ["", "行情覆盖分段（合计交易日按日期去重）："]
        lines.extend(f"- {p['start']} 至 {p['end']}：{p['sessions']} 日。" for p in overview["market_periods"])
    if data["events"] and "window_before" in data["events"][0]:
        lines += ["", "事件窗口（相对交易日）："]
        windows = {r["event_id"]: (r["window_before"], r["window_after"]) for r in data["events"]}
        lines.extend(f"- {event_id}：[-{before}, +{after}]。" for event_id, (before, after) in windows.items())
    if data.get("independent_quotes"):
        lines += ["", "### 第二行情源口径敏感性", "",
                  "东方财富当次历史快照为不复权日涨跌幅，原事件实验为 BaoStock adjustflag=1；两者并非同一收益定义。下表的 CAR 差值混合了显示舍入与收益调整基准差异，只用于口径敏感性诊断。", "",
                  "| 年份 | 事件口径 | 股票 | BaoStock CAR | 东方财富不复权 CAR | 差（百分点） | 事件窗口最大日差（百分点） | 估计期存在大差异日 |",
                  "|---|---|---|---:|---:|---:|---:|---|"]
        for row in data["independent_quotes"]:
            lines.append(f"| {row['period']} | {row['event_id']} | {row['stock_code']} | "
                         f"{row['baostock_car']:.4%} | {row['eastmoney_unadjusted_car']:.4%} | "
                         f"{row['car_difference_pp']:+.4f} | {row['event_window_max_absolute_difference_pp']:.4f} | "
                         f"{'是' if row['estimation_basis_exception'] else '否'} |")
        lines += ["", "2018/2020 九组事件窗口内的日收益差均未超过 0.0052 个百分点；2020 年核对区间有三处更大的股票收益口径差，仅万科 A 的 2019-08-15 进入两种武汉对齐口径的估计期。数据为 2026-09-27 抓取的当前历史版本，尚未核对当时可见版本、公司行为公告或同定义复权结果。"]
    if data.get("activity") or data.get("placebo"):
        lines += ["", "## 成交活动与日期对照", "",
                  "成交倍数以事件估计期的日中位数为基期；日期排名只描述所选候选集合，不是 p 值或因果检验。", "",
                  "| 事件口径 | 股票 | 事件日成交额倍数 | 事前候选日 | 事后候选日 |",
                  "|---|---|---:|---:|---:|"]
        for row in data.get("activity", []):
            candidate = data.get("placebo", {}).get(row["event_id"], {})
            lines.append(f"| {row['event_id']} | {row['stock_code']} | {row['amount_fold']:.3f} | "
                         f"{candidate.get('before', '—')} | {candidate.get('after', '—')} |")
    prediction = data["prediction"]
    lines += ["", "## 文本增量预测", "",
              f"测试预测 {prediction['rows']} 条；纯行情 MAE {prediction['market_mae']:.4%}，行情加文本 MAE {prediction['text_mae']:.4%}。",
              f"配对 MAE 差值（文本 − 行情）{prediction['paired_difference']:.4%}，近似 95% 区间 [{prediction['interval_95'][0]:.4%}, {prediction['interval_95'][1]:.4%}]。"]
    if data.get("visibility_lag_series"):
        lines += ["", "### 问答可见时间敏感性", "",
                  "在同一固定样本上，问答来源时间分别额外后移 0/1/3/7 个自然日。0 日结果复现原实验，纯行情预测在所有情景中一致；延迟是假设，不是已核实的首次公开时刻。原始运行经固定清单核验，两份结果/报告与独立重跑逐字节一致。", "",
                  "| 额外延迟 | 文本特征改变行 | 行情 MAE | 行情＋文本 MAE | 文本－行情差（百分点） | 近似 95% 区间（百分点） |",
                  "|---:|---:|---:|---:|---:|---:|"]
        for row in data["visibility_lag_series"]:
            low, high = row["paired_interval_95"]
            lines.append(f"| {row['lag_days']} 日 | {row['changed_text_feature_rows']} | "
                         f"{100 * row['market_test_mae']:.4f} | {100 * row['text_test_mae']:.4f} | "
                         f"{100 * row['paired_mae_difference']:+.4f} | "
                         f"[{100 * low:+.4f}, {100 * high:+.4f}] |")
        lines += ["", "区间均包含零；来源公开日志缺失，不能据延迟扫描反推真实时刻或证明稳定预测增益。"]
    lines += ["", "## Agent 规则回放", "",
              "| 时期 | 股票 | 角色 | 市场信号 | 零信号 | 买入持有 | 最大回撤 | 交易 |", "|---|---:|---|---:|---:|---:|---:|---:|"]
    for row in data["replays"]:
        lines.append(f"| {row['period']} | {row['stock_code']} | {role_labels.get(row['role'], row['role'])} | {row['signal_multiple']:.3f} | {row['control_multiple']:.3f} | {row['buyhold_multiple']:.3f} | {row['max_drawdown']:.2%} | {row['trades']} |")
    if data.get("capacity_series"):
        lines += ["", "## 资金规模与容量情景", "",
                  "每类 Agent 在每只股票分别假设 10 亿元；诊断列用同日实际总成交额作事后比例，回放列只用决策前的 t-2 总成交额设 1% 假设上限。6 项容量相关运行另经固定清单核验并从输入独立重跑，12 份产物逐字节一致。", "",
                  "| 时期 | 股票 | 事后参与比例 95 分位 | 触及上限 | 请求额实际完成比例 | 激进型无限制末值/初值 | 激进型容量约束末值/初值 |",
                  "|---|---|---:|---:|---:|---:|---:|"]
        for row in data["capacity_series"]:
            lines.append(f"| {row['period']} | {row['stock_code']} | {row['diagnostic_p95_fraction']:.2%} | "
                         f"{row['binding_days']}/{row['sessions']} | {row['aggregate_fill_rate']:.2%} | "
                         f"{row['aggressive_uncapped_multiple']:.3f} | {row['aggressive_capped_multiple']:.3f} |")
        lines += ["", "1% 是未经校准的敏感性参数；日总成交额不是盘口可执行深度，期末财富差异不证明策略改善或预测能力。"]
    if data.get("counterfactual_series"):
        lines += ["", "## 已观察收益上的假设冲击", "",
                  "2018 与 2020 各有三种冲击系数及有/无手设事件信号配对；两项固定运行通过来源核验，四份结果/报告与全新目录重跑逐字节一致。信号强度、固定流动性和价格冲击均未经校准，不是 LLM 输出或真实市场预测。", "",
                  "| 时期 | 事件 | 首次受信号影响收益日 | 冲击系数 | 情景窗口净订单差（亿元） | 情景末日价格指数差 | 全期末价格指数差 |",
                  "|---|---|---|---:|---:|---:|---:|"]
        for row in data["counterfactual_series"]:
            lines.append(f"| {row['period']} | {row['event_id']} | {row['first_signal_trade_date']} | "
                         f"{row['impact_coefficient']:.3f} | "
                         f"{row['event_window_net_order_delta_cny']/1e8:+.3f} | "
                         f"{row['event_end_price_delta']:+.4f} | {row['terminal_price_delta']:+.4f} |")
        lines += ["", "归一化价格指数不是实际成交价。已实现收益包含真实交易作用，叠加模拟冲击可能重复计入市场运动；这些数值仅说明模型机制与参数敏感性。"]
    if data.get("lagged_impact_series"):
        lines += ["", "## 滞后成交额冲击敏感性", "",
                  "2018 与 2020 各 24 条路径，按有/无手设信号配成 12 组；原始清单核验及独立重跑的结果、报告逐字节核对通过。容量预算和独立冲击分母分别取 t-2 历史双向总成交额的 1% 或 5%，不是盘口深度估计。", "",
                  "| 时期 | 容量比例 | 冲击分母比例 | 冲击系数 | 情景末日价格指数差 | 全期末价格指数差 | 触及容量日（无/有信号） |",
                  "|---|---:|---:|---:|---:|---:|---:|"]
        for row in data["lagged_impact_series"]:
            lines.append(f"| {row['period']} | {row['participation_rate']:.0%} | "
                         f"{row['impact_depth_fraction']:.0%} | {row['impact_coefficient']:.3f} | "
                         f"{row['event_end_price_delta']:+.4f} | {row['terminal_price_delta']:+.4f} | "
                         f"{row['control_binding_days']}/{row['event_binding_days']} |")
        lines += ["", "同一事件的全期末差异可随假设比例变号。Agent 行为、信号、资金规模和冲击关系均未用真实订单校准；已实现收益再叠加冲击还可能重复计数。此表只展示模型对假设的敏感性。"]
    lines += ["", "## 运行核验", "", "| 运行 | 完整性 | 重跑 | 哈希检查 | 比较产物 |", "|---|---|---|---:|---:|"]
    for row in data["runs"]:
        lines.append(f"| {row['id']} | {row['integrity']} | {row['reexecution']} | {row['hash_checks']} | {row['compared_artifacts']} |")
    semantic = data["semantic"]
    lines += ["", "## 产物证据", "", "每项产物名称与比较状态来自独立重跑报告；哈希在本机运行目录中保存。", ""]
    for row in data["runs"]:
        lines.append(f"### {row['id']}")
        lines.extend(f"- `{item['name']}`：{item['status']}" for item in row.get("artifacts", []))
        lines.append("")
    lines += ["## 语义审核状态", "", f"样本 {semantic['items']} 条；双人完成 {semantic['dual_reviewed']} 条；待审 {semantic['pending']} 条；分歧 {semantic['conflicts']} 条；状态 `{semantic['status']}`。", ""]
    readiness = data.get("review_readiness")
    if readiness:
        lines += ["## 下半年留出人工审核准备", "",
                  f"审核包状态 `{readiness['status']}`；固定条目 {readiness['items']} 条；独立审核位 {readiness['reviewer_slots']} 个；当前已审核 {readiness['reviewed_items']} 条；每位审核者的空白标签行 {readiness['blank_label_rows_per_reviewer']} 条；离线页面 {readiness['interface_pages']} 个。",
                  "该状态只证明来源绑定、页面脱敏、空白标签和哈希一致，不代表语义准确率，也没有生成金标准。", ""]
    gate = data.get("agent_signal_gate")
    if gate:
        lines += ["## Agent 语义信号接入门槛", "",
                  f"状态 `{gate['status']}`；适配器 `{gate['adapter_version']}`；已审 {gate['reviewed_items']}/{gate['required_items']} 条。",
                  gate["reason"], gate["scope"], ""]
    lines += ["## 公开证据", ""]
    lines.extend(f"- [{item['label']}]({item['url']})" for item in data["evidence"])
    financial = data.get("financial_dictionary")
    if financial:
        scope = financial["scope"]
        lines += ["", "## 财务字段口径状态", "",
                  f"字段 {scope['field_count']} 个（快照 {scope['snapshot_field_count']}，行上下文 {scope['row_context_field_count']}）；来源行 {scope['source_rows']:,}。所有值状态为 `unverified`，该字典只描述结构统计。", "",
                  "| 字段 | 层级 | 缺失率 | 数值 | 非数值 | 范围 | 状态 |",
                  "|---|---|---:|---:|---:|---|---|"]
        for item in financial["fields"]:
            minimum, maximum = item["numeric_min"], item["numeric_max"]
            value_range = "—" if minimum is None else f"{minimum:g} … {maximum:g}"
            rate = "—" if item["missing_rate"] is None else f"{item['missing_rate']:.2%}"
            lines.append(f"| {item['field_name'].replace('|', '/')} | {item['role']} | {rate} | {item['numeric_count']:,} | {item['non_numeric_count']:,} | {value_range} | `{item['verification_status']}` |")
        lines += ["", "### 尚未确认", ""]
        lines.extend(f"- `{item['key']}`：{item['question']}（{item['status']}）" for item in financial["unresolved_semantics"])
    model_run = data.get("model_run")
    if model_run:
        lines += ["", "## LLM 开发样本运行状态", ""]
        if model_run.get("status") == "not_run":
            lines.append("尚未执行 DeepSeek 语义抽取；没有模型输出或准确率结论。")
        else:
            raw = model_run["raw"]
            normalized = model_run["normalized"]
            lines.append(f"模型 `{model_run.get('model_id')}`；原始响应 {raw['rows']}/{model_run['scope']['requested_rows']} 条，失败 {raw['request_failures']} 条；标准化状态 `{normalized['status']}`；`accuracy_claim_allowed=false`。")
            if "rows" in normalized:
                lines.append(f"标准化 {normalized['rows']} 条，解析失败 {normalized['parse_errors']} 条，缺失预测 {normalized['missing_predictions']} 条。结构和证据跨度校验不证明语义准确，仍需人工金标准。")
        history = data.get("model_runs", [])
        if len(history) > 1:
            lines += ["", "| 提示版本 | 原始响应 | 标准化条数 | 解析失败 |", "|---|---:|---:|---:|"]
            for run in history:
                normalized = run["normalized"]
                lines.append(f"| {run['prompt_version']} | {run['raw']['rows']} | {normalized.get('rows', '—')} | {normalized.get('parse_errors', '—')} |")
            lines += ["", "同样本协议开发对照；v1 按补充边界检查后的规则重新校验。此样本已用于开发，不能称为未接触的最终留出测试。"]
    holdout = data.get("holdout_model")
    if holdout:
        lines += ["", "## 2020 下半年留出模型状态", "",
                  f"固定模型 `{holdout['model_id']}`、提示 `{holdout['prompt_version']}` 已返回 {holdout['items']}/{holdout['items']} 条；"
                  f"请求失败 {holdout['request_failures']}，结构/证据解析失败 {holdout['parse_errors']}。",
                  f"有效空事件响应 {holdout['valid_empty_rows']} 条，含事件响应 {holdout['valid_event_rows']} 条，"
                  f"共 {holdout['validated_events']} 个抽取事件。", "",
                  "这些是来源与格式核对结果；没有独立双人裁定金标准，不能计算准确率或判定研究性 Agent 接入门槛。模型输出未用于历史回放决策。"]
    lines += ["", "## 研究限制", ""]
    lines.extend(f"- {item}" for item in data["limitations"])
    return "\n".join(lines) + "\n"


def build_workbench(output_dir: Path) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty workbench output directory")
    data = collect_data()
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        staging = Path(tmp)
        for name in ("index.html", "app.js", "style.css"):
            shutil.copyfile(ASSETS / name, staging / name)
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        (staging / "data.js").write_text("window.MARKETMIRROR_DATA = " + payload + ";\n", encoding="utf-8")
        (staging / "report.md").write_text(render_report(data), encoding="utf-8")
        source_reports = {name: file_sha256(path) for name, path in {
            "integrity_results": OUTPUTS / "integrity_catalog_2018_2020_v3/integrity_results.json",
            "reexecution_results": OUTPUTS / "reexecution_catalog_2018_2020_v4/reexecution_results.json",
            "capacity_integrity_results": OUTPUTS / "integrity_catalog_capacity_series/integrity_results.json",
            "capacity_reexecution_results": OUTPUTS / "reexecution_catalog_capacity_series/reexecution_results.json",
            "counterfactual_catalog": COUNTERFACTUAL_CONFIG,
            "counterfactual_2018_original_manifest": OUTPUTS / "observed_2018/counterfactual_assumed_impact_v4/counterfactual_manifest.json",
            "counterfactual_2020_original_manifest": OUTPUTS / "observed_2020/counterfactual_assumed_impact_v4/counterfactual_manifest.json",
            "counterfactual_2018_rerun_manifest": OUTPUTS / "observed_2018/counterfactual_verified_rerun_v1/counterfactual_manifest.json",
            "counterfactual_2020_rerun_manifest": OUTPUTS / "observed_2020/counterfactual_verified_rerun_v1/counterfactual_manifest.json",
            "lagged_impact_catalog": LAGGED_IMPACT_CONFIG,
            "lagged_impact_2018_original_manifest": OUTPUTS / "observed_2018/lagged_impact_v1/lagged_impact_manifest.json",
            "lagged_impact_2020_original_manifest": OUTPUTS / "observed_2020/lagged_impact_v1/lagged_impact_manifest.json",
            "lagged_impact_2018_rerun_manifest": OUTPUTS / "observed_2018/lagged_impact_verified_rerun_v1/lagged_impact_manifest.json",
            "lagged_impact_2020_rerun_manifest": OUTPUTS / "observed_2020/lagged_impact_verified_rerun_v1/lagged_impact_manifest.json",
            "visibility_lag_catalog": VISIBILITY_LAG_CONFIG,
            "visibility_lag_integrity_results": OUTPUTS / "integrity_catalog_visibility_lag_2020_v1/integrity_results.json",
            "visibility_lag_original_manifest": OUTPUTS / "text_pilot_2020/visibility_lag_v2/visibility_lag_manifest.json",
            "visibility_lag_rerun_manifest": OUTPUTS / "text_pilot_2020/visibility_lag_verified_rerun_v1/visibility_lag_manifest.json",
            "independent_quote_catalog": INDEPENDENT_QUOTE_CONFIG,
            "independent_quote_integrity_results": OUTPUTS / "integrity_catalog_independent_quotes_v1/integrity_results.json",
            "independent_quote_original_manifest": OUTPUTS / "independent_eastmoney_2018_2020/check_v2/independent_quote_manifest.json",
            "independent_quote_rerun_manifest": OUTPUTS / "independent_eastmoney_2018_2020/check_verified_rerun_v1/independent_quote_manifest.json",
            "review_readiness_catalog": REVIEW_READINESS_CONFIG,
            "review_readiness_integrity_results": OUTPUTS / "integrity_catalog_semantic_review_readiness_h2_2020_v2/integrity_results.json",
            "review_readiness_manifest": OUTPUTS / "semantic_holdout_h2_2020_readiness_v2/review_readiness_manifest.json",
            "holdout_model_catalog": HOLDOUT_MODEL_CONFIG,
            "holdout_model_integrity_results": OUTPUTS / "integrity_catalog_semantic_holdout_model_h2_2020_v1/integrity_results.json",
            "holdout_model_raw_manifest": OUTPUTS / "semantic_holdout_h2_2020_model/model_run_manifest.json",
            "holdout_model_normalization_manifest": OUTPUTS / "semantic_holdout_h2_2020_normalized/normalization_manifest.json",
            "holdout_model_diagnostics_manifest": OUTPUTS / "semantic_holdout_h2_2020_diagnostics/diagnostics_manifest.json",
            "semantic_comparison": OUTPUTS / "semantic_review_comparison_pilot_2020/comparison_results.json",
            "agent_signal_adapter": ROOT / "research/semantic/agent_signal_adapter.py",
            "financial_quality_report": OUTPUTS / "financial_2020/financial_quality_report.json",
            "financial_dictionary": OUTPUTS / "financial_2020/field_dictionary.json"}.items()}
        for version in ("v1", "v2"):
            model_manifest = OUTPUTS / f"semantic_model_deepseek_{version}/model_run_manifest.json"
            normalized_dir = OUTPUTS / ("semantic_model_deepseek_v1_revalidated" if version == "v1"
                                        else "semantic_model_deepseek_v2_normalized")
            normalization_manifest = normalized_dir / "normalization_manifest.json"
            if model_manifest.exists():
                source_reports[f"semantic_model_{version}_manifest"] = file_sha256(model_manifest)
            if normalization_manifest.exists():
                source_reports[f"semantic_model_{version}_normalization_manifest"] = file_sha256(normalization_manifest)
        manifest = {"pipeline_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
                    "integrity_catalog_sha256": file_sha256(CONFIG),
                    "source_reports": source_reports,
                    "code_sha256": file_sha256(Path(__file__)),
                    "artifacts": {p.name: {"sha256": file_sha256(p)} for p in staging.iterdir()}}
        (staging / "workbench_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return {"output_dir": str(output_dir), "run_count": data["overview"]["run_total"],
            "reviewed_labels": data["semantic"]["dual_reviewed"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_workbench(args.output_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
