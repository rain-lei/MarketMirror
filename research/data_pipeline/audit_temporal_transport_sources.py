"""Independent source receipt/QC verification without model outcome evaluation.

This module deliberately does not import the new source producer. It rebuilds
source rows from archived response text, preserves the full expected grid, and
keeps raw transport success separate from model eligibility.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re

from .audit_eastmoney_carrier import (
    ACQUIRED, FAILED, FIELDS, carrier_body, check_url, dense_source_rows,
    raw_series, read_json, require, compare_pair,
)
from .provenance import file_sha256


def immutable_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def key_for(spec):
    return spec["secid"] + "_fqt" + str(spec["fqt"])


def normalized_pair(first, adjusted):
    result = compare_pair(first, adjusted)
    if "only_fqt1_dates" in result:
        result["only_adjusted_dates"] = result.pop("only_fqt1_dates")
    return result


def validate_design(root, cfg):
    design_path = root / cfg["design_protocol_path"]
    design = read_json(design_path)
    base = read_json(root / design["original_numerical_protocol_path"])
    require(file_sha256(root / design["original_numerical_protocol_path"]) == design["original_numerical_protocol_sha256"],
        "Original numerical parameter protocol changed")
    require(design["stocks"] == base["stocks"] and design["baskets"] == base["baskets"]
        and len(design["stocks"]) == 123 and len(design["baskets"]) == 41,
        "Source design replaced the original cohort")
    for key in design["unchanged_numerical_keys"]:
        require(design[key] == base[key], "Numerical parameter changed: " + key)
        digest = hashlib.sha256(json.dumps(design[key], sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        require(digest == design["numerical_parameter_hashes"][key], "Numerical parameter hash differs")
    cells = {(anchor, feedback, residual, market, delivery)
        for anchor in ("initial_inventory", "current_inventory") for feedback in (0.0, 1.0)
        for residual in ("independent", "historical_rank") for market in ("market_independent", "market_shared")
        for delivery in ("masked", "public", "whole_public")}
    actual = [(c["background_anchor"], c["feedback_scale"], c["residual_dependence"], c["risk_mode"], c["delivery"])
        for c in design["variants"]]
    require(len(actual) == len(cells) == 48 and set(actual) == cells and len(design["seeds"]) == 5,
        "Full 240-condition design missing or duplicated")
    require(design["outcome_analysis_enabled"] is False and cfg["outcome_analysis_enabled"] is False
        and design["formal_execution_protocol_frozen"] is False and design["evaluation_mask_frozen"] is False,
        "A source-only registration opened the model gate")
    for registry in (cfg["bindings"], design["bindings"]):
        for name, digest in registry.items():
            path = (root / name).resolve()
            require(path.is_relative_to(root) and file_sha256(path) == digest, "Source/design binding changed: " + name)
    for name, digest in cfg["external_runtime_bindings"].items():
        require(file_sha256(Path(name)) == digest, "CLI source runtime changed")
    expected = [{"secid": ("1." if code.startswith(("5", "6", "9")) else "0.") + code,
        "kind": "stock", "fqt": fqt} for code in design["stocks"] for fqt in (0, 1, 2)]
    expected.append({"secid": "1.000300", "kind": "benchmark", "fqt": 0})
    require(cfg["requests"] == expected and len(expected) == 370
        and cfg["expected_session_dates"] == design["source_trade_dates"]
        and len(cfg["expected_session_dates"]) == 139 and len(design["dates"]) == 58,
        "Full source grid/calendar changed")
    require(cfg["probe_requests"] == [r for r in expected if r["secid"] in ("0.000001", "1.000300")],
        "Probe identities changed")
    require(cfg["probe_max_attempts"] == 1 and cfg["max_workers"] == cfg["max_attempts"] == 2
        and cfg["total_extra_retry_ceiling"] == 100 and cfg["minimum_request_interval_seconds"] == 8.0
        and cfg["direct_timeout_seconds"] == 15 and cfg["process_timeout_seconds"] == 55
        and cfg["maximum_response_bytes"] == 2_000_000, "Registered acquisition/retry bounds changed")
    return design


def audit_receipt(folder, record, spec, cfg, digest, transport, url_checker=check_url):
    """Rebuild every completed attempt, including source failures, from artifacts."""
    folder = Path(folder).resolve()
    receipt_path = folder / "receipt.json"
    require(read_json(receipt_path) == record and record["spec"] == spec and record["transport"] == transport
        and record["protocol_sha256"] == digest, "Source receipt identity changed")
    url_checker(record["url"], spec, *cfg["download_period"])
    for flag in ("model_eligible", "execution_quote_eligible", "total_return_certified",
            "point_in_time_feed_certified", "explicit_trade_status_supplied"):
        require(record[flag] is False, "Raw transport promoted an unverified certification")
    require(record["carrier_is_not_original_http_wire_bytes"] is (transport == "firecrawl"),
        "Carrier provenance overstated")
    all_paths = {p.relative_to(folder).as_posix(): p for p in folder.rglob("*")
        if p.is_file() and p != receipt_path}
    require(set(all_paths) == set(record["artifacts"]), "Unregistered/missing source attempt artifact")
    for name, digest_value in record["artifacts"].items():
        path = all_paths[name].resolve()
        require(path.is_relative_to(folder) and file_sha256(path) == digest_value, "Source artifact hash changed")
    attempts, successful, retry_count, classes = record["attempts"], [], 0, []
    require(1 <= len(attempts) <= cfg["max_attempts"] and [a["attempt"] for a in attempts] == list(range(1, len(attempts) + 1)),
        "Attempts exceeded scope or disappeared")
    for entry in attempts:
        part = folder / f"attempt_{entry['attempt']}"
        if entry["status"] == "NOT_REQUESTED_FROZEN_RETRY_QUOTA_EXHAUSTED":
            require(entry["attempt"] > 1 and not part.exists() and entry["observed_exit_code"] is None,
                "Unrequested retry has a forged process/source")
            classes.append("RETRY_QUOTA_EXHAUSTED")
            continue
        require(part.is_dir(), "Recorded source attempt folder missing")
        if entry["status"] == "PRESERVED_INTERRUPTED_ATTEMPT_EXIT_UNOBSERVED":
            require(entry["observed_exit_code"] is None, "Interrupted source exit inferred")
            classes.append("INTERRUPTED_EXIT_UNOBSERVED")
            continue
        require(read_json(part / "process.json") == entry, "Durable attempt differs from receipt")
        if entry["attempt"] > 1:
            retry_count += 1
        if transport == "firecrawl":
            command = entry["command"]
            require(command[:7] == [cfg["node_executable"], cfg["cli_entrypoint"], "scrape", record["url"],
                "--format", "markdown", "-o"] and len(command) == 8
                and Path(command[7]).resolve() == (part / "carrier.md").resolve(), "Carrier command changed")
            log = (part / "cli.log").read_text(encoding="utf-8")
            require(type(entry["pid"]) is int and type(entry["observed_exit_code"]) is int,
                "Completed carrier attempt lacks an actual observed exit")
        else:
            require(entry["observed_exit_code"] is None and "command" not in entry and "pid" not in entry,
                "Direct HTTP fabricated a process exit")
            log = ""
        if entry["status"] == "VALID_CARRIER_SOURCE_RESPONSE":
            if transport == "firecrawl":
                require(entry["observed_exit_code"] == 0 and re.search(r"Scrape ID: " + re.escape(entry["scrape_id"]) + r"\b", log),
                    "Carrier success lacks an actual successful exit/identifier")
                body = carrier_body((part / "carrier.md").read_text(encoding="utf-8"))
                require(body == (part / "source_response.json").read_bytes(), "Carrier JSON differs from preserved text")
            else:
                require(entry["http_status"] == 200, "Direct source promoted HTTP failure")
                body = (part / "source_response.json").read_bytes()
            require(len(body) == entry["source_response_bytes"] <= cfg["maximum_response_bytes"], "Source byte count differs")
            series = raw_series(body, spec["secid"], *cfg["download_period"], cfg["expected_session_dates"])
            successful.append((part.name, series))
            classes.append("VALID_SOURCE_QC_PENDING")
        else:
            require(entry["status"] == "FAILED_CARRIER_SOURCE_ATTEMPT" and entry["error_type"] and entry["error"],
                "Failed source attempt lacks diagnostic evidence")
            classes.append("OBSERVED_TIMEOUT" if entry["error_type"] == "TimeoutError" else
                "SERVER_RATE_LIMIT" if re.search(r"retry after|rate limit", log, re.I) else
                "BROWSER_EMPTY_RESPONSE" if "ERR_EMPTY_RESPONSE" in log else "OBSERVED_SOURCE_FAILURE")
    if record["status"] == ACQUIRED:
        require(len(successful) == 1 and record["successful_attempt"] == successful[0][0]
            and record["inspection"] == successful[0][1]["inspection"], "Promoted source differs from independent reconstruction")
        series = successful[0][1]
    else:
        require(record["status"] == FAILED and not successful and "inspection" not in record and "successful_attempt" not in record,
            "Failed request reported successful source/features")
        series = None
    return {"record": record, "series": series, "attempt_classes": classes,
        "retry_count": retry_count, "receipt_sha256": file_sha256(receipt_path)}


def verify_source_completion(root, raw_root, cfg, digest, expected_exit):
    """Verify the last actual exit and every preserved recovery predecessor.

    Run claims record identity at launch, not current liveness or completion.
    Completion therefore requires the observing wrapper's actual exit/log and
    the complete raw manifest checked by the caller, never just a claim file.
    """
    root, raw_root = Path(root).resolve(), Path(raw_root).resolve()
    bindings = {}

    def bound(name, expected=None):
        path = (root / name).resolve()
        require(path.is_relative_to(root) and path.is_file(), "Completion evidence escapes workspace or is missing")
        value = file_sha256(path)
        require(expected is None or value == expected, "Completion/recovery evidence changed")
        bindings[path.relative_to(root).as_posix()] = value
        return path

    paths = sorted((raw_root / "full").glob("run_claim_*.json"))
    require(paths and [p.name for p in paths] == [f"run_claim_{i:03}.json" for i in range(len(paths))],
        "Source run claims are missing or noncontiguous")
    claims = []
    for path in paths:
        bound(path)
        claim = read_json(path)
        require(claim["protocol_sha256"] == digest and type(claim["pid"]) is int and claim["pid"] > 0
            and type(claim["creation_ticks"]) is int and claim["creation_ticks"] > 0,
            "Source launch identity differs")
        claims.append(claim)
    candidates = []
    for path in (root / "research_outputs").glob("temporal_transport_source_full*process_*.json"):
        value = read_json(path)
        if value.get("pid") == claims[-1]["pid"] and value.get("stage") == "full":
            candidates.append(path)
    require(len(candidates) == 1, "Latest source claim has no unique observing process receipt")
    receipts = []

    def inspect(path, index):
        path = bound(path)
        process = read_json(path)
        require(process["stage"] == "full" and process["pid"] == claims[index]["pid"]
            and process.get("protocol_sha256", digest) == digest, "Source process/claim identity differs")
        command = process["command"]
        prefix = command[1:-3]
        require(command[-2:] == ["full", "--child"] and prefix in (["-X", "utf8"], ["-B", "-X", "utf8"])
            and Path(command[-3]).resolve() == (root / "research_outputs/acquire_temporal_transport_sources_20261003_v1.py").resolve(),
            "Observed source command changed")
        actual = process["actual_child_exit_code"]
        if actual is None:
            require(index < len(claims) - 1 and process["status"] == "RUNNING", "Latest source completion exit is unobserved")
        else:
            require(type(actual) is int and process["status"] == ("OBSERVED_CHILD_EXIT_ZERO" if actual == 0 else "OBSERVED_CHILD_EXIT_NONZERO"),
                "Source exit type/status differs")
            bound(process["log_path"], process["log_sha256"])
            require(actual == expected_exit if index == len(claims) - 1 else actual != 0,
                "Actual source exit differs from complete manifest or incomplete predecessor")
        receipts.append({"path": path.relative_to(root).as_posix(), "pid": process["pid"], "actual_exit": actual})
        if index == 0:
            require("recovery_registration_path" not in process, "First source claim invents a predecessor")
            return
        reg_path = bound(process["recovery_registration_path"], process["recovery_registration_sha256"])
        reg = read_json(reg_path)
        require(reg["protocol_sha256"] == digest and reg["fixed_request_count"] == len(cfg["requests"])
            and reg["producer_or_protocol_changed"] is False and reg["existing_attempts_overwritten"] is False
            and reg["new_period_model_effects_evaluated"] is False
            and reg["related_original_python_node_processes_observed"] == [],
            "Recovery changed source scope or was started with a related live owner")
        require(reg["existing_terminal_receipts"] == len(reg["existing_receipt_bindings"])
            and reg["global_retry_reservations_already_spent"] <= cfg["total_extra_retry_ceiling"],
            "Recovery lost previous source receipts or reset retry accounting")
        for name, value in reg["existing_receipt_bindings"].items():
            prior_receipt = bound(name, value)
            require(prior_receipt.parent.parent == raw_root / "full" and prior_receipt.name == "receipt.json",
                "Recovery receipt preservation escapes fixed source grid")
        if "previous_process_receipt_path" in reg:
            require(reg["status"] == "SAME_FROZEN_GRID_RECOVERY_AFTER_OBSERVED_INCOMPLETE_EXIT_AND_VERIFIED_OWNER_ABSENCE"
                and reg["previous_actual_exit_observed"] is True and type(reg["previous_actual_exit_code"]) is int
                and reg["previous_actual_exit_code"] != 0 and reg["previous_manifest_completed"] is False,
                "Recovery misstates previous actual incomplete exit")
            previous = bound(reg["previous_process_receipt_path"], reg["previous_process_receipt_sha256"])
            require(read_json(previous)["actual_child_exit_code"] == reg["previous_actual_exit_code"],
                "Recovery changed the previous observed exit")
            checks = reg["previous_owner_checks"]
            require(len(checks) == index, "Recovery skipped an earlier owner check")
            for i, check in enumerate(checks):
                require(bound(check["claim_path"], check["claim_sha256"]) == paths[i]
                    and check["registered_pid"] == claims[i]["pid"]
                    and check["registered_creation_ticks"] == claims[i]["creation_ticks"], "Recovery owner claim differs")
                identity = check["actual_identity_at_recovery"]
                require(identity is None or not identity["live"] or identity["creation_ticks"] != claims[i]["creation_ticks"],
                    "Recovery was started while the same previous owner was live")
            bound(reg["recovery_helper_path"], reg["recovery_helper_sha256"])
        else:
            require(index == 1 and reg["status"] == "SAME_FROZEN_GRID_RECOVERY_AFTER_VERIFIED_ORIGINAL_OWNER_ABSENCE"
                and reg["original_actual_exit_observed"] is False and reg["original_exit_code"] is None,
                "Recovery fabricated an unobserved original exit")
            previous = bound(reg["original_process_receipt"], reg["original_process_receipt_sha256"])
            require(read_json(previous)["actual_child_exit_code"] is None
                and file_sha256(paths[0]) == reg["original_claim_sha256"], "Original interrupted launch/receipt changed")
            identity = reg["original_owner_at_recovery"]
            require(identity is None or not identity["live"] or identity["creation_ticks"] != claims[0]["creation_ticks"],
                "Recovery was started while the original owner was live")
            bound("research_outputs/resume_registered_transport_sources_20261003_v1.py", reg["recovery_helper_sha256"])
        inspect(previous, index - 1)

    inspect(candidates[0], len(claims) - 1)
    return {"completed_process_path": candidates[0].relative_to(root).as_posix(),
        "actual_completion_exit_code": expected_exit, "run_claims_checked": len(claims),
        "preserved_process_lineage": receipts, "bindings": bindings}


def audit_stage(root, protocol_path, output, stage):
    root, protocol_path, output = Path(root).resolve(), Path(protocol_path).resolve(), Path(output).resolve()
    require(output.is_relative_to(root) and not output.exists(), "Use a fresh independent QC folder in the workspace")
    cfg = read_json(protocol_path)
    design = validate_design(root, cfg)
    digest = file_sha256(protocol_path)
    raw_root = (root / cfg["output_root"]).resolve()
    require(raw_root.is_relative_to(root), "Raw sources escape workspace")
    decision_path = raw_root / "transport_decision.json"
    decision = read_json(decision_path)
    require(decision["protocol_sha256"] == digest and decision["new_period_model_effects_evaluated"] is False,
        "Probe transport decision differs from source-only scope")
    output.mkdir()
    records, all_rows, receipt_bindings, classes = {}, [], {}, Counter()
    completion = None
    if stage == "probes":
        process_path = root / "research_outputs/temporal_transport_source_probes_process_20261003_v1.json"
        process = read_json(process_path)
        require(type(process["actual_child_exit_code"]) is int and process["protocol_sha256"] == digest,
            "Probe completion lacks an actual child exit")
        require(file_sha256(root / process["log_path"]) == process["log_sha256"], "Probe process log changed")
        valid_by_transport = {}
        for transport, results in decision["results"].items():
            require(transport in ("direct_http", "firecrawl") and set(results) == {key_for(r) for r in cfg["probe_requests"]},
                "Probe source grid changed")
            valid_by_transport[transport] = 0
            for spec in cfg["probe_requests"]:
                key = key_for(spec)
                folder = raw_root / "probes" / transport / key
                require(len(results[key]["attempts"]) == cfg["probe_max_attempts"], "Probe used an unregistered retry")
                verified = audit_receipt(folder, results[key], spec, cfg, digest, transport)
                records[transport + "/" + key] = verified
                valid_by_transport[transport] += verified["record"]["status"] == ACQUIRED
                receipt_bindings[(folder / "receipt.json").relative_to(root).as_posix()] = verified["receipt_sha256"]
                classes.update(verified["attempt_classes"])
                for row in dense_source_rows(verified["series"], spec, cfg["expected_session_dates"]):
                    all_rows.append({**row, "transport": transport})
        expected = "direct_http" if valid_by_transport.get("direct_http") == 4 else (
            "firecrawl" if valid_by_transport.get("firecrawl", 0) >= 1 else None)
        require(decision["selected_transport"] == expected and process["actual_child_exit_code"] == int(expected is None),
            "Source selection rule or actual completion exit differs")
        pairs = []
        status = "PASS_INDEPENDENT_SOURCE_PROBES_TRANSPORT_RULE_AND_NULL_FAILURES"
        planned_positions = len(records) * len(cfg["expected_session_dates"])
        pending = []
    else:
        require(stage in ("partial", "full") and decision["selected_transport"] in ("direct_http", "firecrawl"),
            "Full source stage has no selected transport")
        transport = decision["selected_transport"]
        manifest_path = raw_root / "source_manifest.json"
        manifest = read_json(manifest_path) if manifest_path.exists() else None
        require(stage != "full" or manifest is not None, "Full source manifest still pending")
        lookup, pending = {}, []
        for spec in cfg["requests"]:
            key = key_for(spec)
            folder = raw_root / "full" / key
            receipt_path = folder / "receipt.json"
            if not receipt_path.exists():
                require(stage == "partial", "Complete batch omitted a source request")
                pending.append(key)
                for day in cfg["expected_session_dates"]:
                    all_rows.append({"secid": spec["secid"], "fqt": spec["fqt"], "kind": spec["kind"],
                        "trade_date": day, "source_observation_status": "SOURCE_REQUEST_NOT_TERMINAL_PENDING",
                        "raw_line": None, "provider_fields": {field: None for field in FIELDS},
                        "certified_trade_status": None, "model_eligible": False, "transport": transport})
                continue
            record = read_json(receipt_path)
            verified = audit_receipt(folder, record, spec, cfg, digest, transport)
            records[key], lookup[key] = verified, verified["series"]
            receipt_bindings[receipt_path.relative_to(root).as_posix()] = verified["receipt_sha256"]
            classes.update(verified["attempt_classes"])
            for row in dense_source_rows(verified["series"], spec, cfg["expected_session_dates"]):
                all_rows.append({**row, "transport": transport})
        planned_positions = 370 * len(cfg["expected_session_dates"])
        require(len(all_rows) == planned_positions, "Independent QC omitted pending/failure source positions")
        reservations = sorted((raw_root / "full/retry_reservations").glob("retry_*.json"))
        require(len(reservations) <= cfg["total_extra_retry_ceiling"], "Full source global retries exceeded ceiling")
        retry_keys = [read_json(path) for path in reservations]
        charged = {(r["request_key"], r["attempt"]) for r in retry_keys}
        require(len(charged) == len(retry_keys),
            "Duplicate full-source retry reservations")
        require(all(key in {key_for(s) for s in cfg["requests"]} and attempt == 2 for key, attempt in charged),
            "Global retry reservation escapes the source grid")
        for key, verified in records.items():
            for attempt in verified["record"]["attempts"]:
                if attempt["attempt"] > 1 and attempt["status"] != "NOT_REQUESTED_FROZEN_RETRY_QUOTA_EXHAUSTED":
                    require((key, attempt["attempt"]) in charged, "Source retry lacks a durable global quota reservation")
        if stage == "full":
            failures = sorted(key for key, v in records.items() if v["record"]["status"] == FAILED)
            gaps = sorted(key for key, v in records.items() if v["record"].get("inspection", {}).get("calendar_coverage_complete") is False)
            require(manifest["protocol_sha256"] == digest and manifest["request_count"] == 370
                and manifest["results"] == {key: v["record"] for key, v in records.items()}
                and manifest["failed_requests"] == failures and manifest["calendar_gap_requests"] == gaps
                and manifest["total_extra_retries"] == len(reservations), "Full source manifest differs from independent receipts")
            completion = verify_source_completion(root, raw_root, cfg, digest, int(bool(failures)))
            for flag in ("missing_values_filled", "new_period_model_effects_evaluated", "point_in_time_feed_certified", "economic_total_return_certified"):
                require(manifest[flag] is False, "Full source manifest overstated eligibility")
            require(manifest["all_companies_retained"] is True and manifest["model_eligible_requests"] == 0,
                "Source manifest replaced or certified the cohort")
        pairs = []
        for stock in design["stocks"]:
            secid = ("1." if stock.startswith(("5", "6", "9")) else "0.") + stock
            for fqt in (1, 2):
                keys = (secid + "_fqt0", secid + "_fqt" + str(fqt))
                pairs.append({"secid": secid, "adjustment_pair": [0, fqt],
                    "request_pending": any(key in pending for key in keys),
                    "comparison": normalized_pair(lookup.get(keys[0]), lookup.get(keys[1]))})
        status = "PASS_INDEPENDENT_COMPLETE_SOURCE_GRID_NULL_FAILURES_QC_PENDING" if stage == "full" else "INDEPENDENT_PARTIAL_SOURCE_SNAPSHOT_NOT_BATCH_COMPLETION"
    row_counts = Counter(row["source_observation_status"] for row in all_rows)
    require(all(row["model_eligible"] is False and row["certified_trade_status"] is None for row in all_rows),
        "Raw/QC grid certified model use or trade status")
    for row in all_rows:
        if row["source_observation_status"] != "SOURCE_ROW_PRESENT_QC_PENDING":
            require(row["raw_line"] is None and all(value is None for value in row["provider_fields"].values()),
                "Failed, missing or pending source values were filled")
    positions_path = output / "source_positions.jsonl.gz"
    with positions_path.open("xb") as raw_file:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_file, mtime=0) as compressed:
            for row in all_rows:
                compressed.write((json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8"))
    pair_path = output / "adjustment_pair_qc.json"
    immutable_json(pair_path, pairs)
    summary = {"status": status, "stage": stage, "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol_sha256": digest, "design_protocol_sha256": file_sha256(root / cfg["design_protocol_path"]),
        "terminal_request_receipts_checked": len(records), "pending_requests": pending,
        "successful_raw_requests": sum(v["record"]["status"] == ACQUIRED for v in records.values()),
        "failed_raw_requests": sum(v["record"]["status"] == FAILED for v in records.values()),
        "source_position_counts": dict(sorted(row_counts.items())), "source_positions": len(all_rows),
        "planned_source_positions": planned_positions, "attempt_classifications": dict(sorted(classes.items())),
        "receipt_bindings": receipt_bindings, "unchanged_full_numerical_design": True,
        "source_completion_evidence": completion,
        "all_target_companies_retained": True, "missing_values_filled": False, "model_eligible": False,
        "new_period_model_effects_evaluated": False, "stock_trade_status_certified": False,
        "price_volume_units_certified": False, "point_in_time_feed_certified": False,
        "economic_total_return_certified": False,
        "artifacts": {p.name: file_sha256(p) for p in (positions_path, pair_path)},
        "auditor_path": Path(__file__).relative_to(root).as_posix(), "auditor_sha256": file_sha256(Path(__file__))}
    immutable_json(output / "results.json", summary)
    print(json.dumps({key: summary[key] for key in ("status", "terminal_request_receipts_checked",
        "successful_raw_requests", "failed_raw_requests", "source_positions", "source_position_counts")}), flush=True)
    return summary
