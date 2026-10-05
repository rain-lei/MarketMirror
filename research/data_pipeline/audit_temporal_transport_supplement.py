"""Independent finite SDK-profile audit and complete-grid provenance merge."""
from collections import Counter
from datetime import datetime, timezone
import gzip
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .audit_eastmoney_carrier import ACQUIRED, FAILED, FIELDS, read_json, require, dense_source_rows
from .audit_temporal_transport_sources import audit_receipt, immutable_json, normalized_pair, validate_design
from .provenance import file_sha256


def check_sdk_source_url(url, spec, start, end, public_ut):
    parts = urlsplit(url)
    expected = {"secid": [spec["secid"]], "klt": ["101"], "fqt": [str(spec["fqt"])],
        "beg": [start.replace("-", "")], "end": [end.replace("-", "")],
        "fields1": ["f1,f2,f3,f4,f5,f6"], "fields2": [",".join(FIELDS) + ",f116"], "ut": [public_ut]}
    require(parts.scheme == "https" and parts.netloc == "push2his.eastmoney.com"
        and parts.path == "/api/qt/stock/kline/get" and not parts.fragment
        and parse_qs(parts.query, keep_blank_values=True) == expected, "SDK source URL escapes the fixed request profile")


def audit_merge_supplement(root, protocol_path, output):
    import json
    root, protocol_path, output = Path(root).resolve(), Path(protocol_path).resolve(), Path(output).resolve()
    require(output.is_relative_to(root) and not output.exists(), "Use a fresh supplemental independent QC directory")
    cfg = read_json(protocol_path)
    require(cfg["version"] == "temporal-transport-failed-subset-sdk-source-amendment-v1"
        and cfg["max_attempts"] == cfg["max_workers"] == 2 and cfg["minimum_request_interval_seconds"] == 8.0
        and cfg["process_timeout_seconds"] == 55 and cfg["outcome_analysis_enabled"] is False,
        "Source supplement scope/bounds changed")
    bindings = {protocol_path.relative_to(root).as_posix(): file_sha256(protocol_path)}

    def bind(name, expected=None):
        path = (root / name).resolve()
        require(path.is_relative_to(root) and path.is_file(), "Source supplement evidence escapes workspace or is missing")
        digest = file_sha256(path)
        require(expected is None or digest == expected, "Source supplement/parent artifact changed")
        bindings[path.relative_to(root).as_posix()] = digest
        return path

    for name, digest in cfg["bindings"].items():
        bind(name, digest)
    for name, digest in cfg["external_runtime_bindings"].items():
        require(file_sha256(Path(name)) == digest, "Source supplemental external runtime changed")
    parent = read_json(bind(cfg["parent_protocol_path"]))
    design = validate_design(root, parent)
    proof_path = bind(cfg["parent_source_qc_path"])
    proof = read_json(proof_path)
    manifest_path = bind(Path(parent["output_root"]) / "source_manifest.json")
    parent_manifest = read_json(manifest_path)
    expected = [spec for spec in parent["requests"] if parent_manifest["results"][spec["secid"] + "_fqt" + str(spec["fqt"])]["status"] == FAILED]
    require(proof["status"] == "PASS_INDEPENDENT_COMPLETE_SOURCE_GRID_NULL_FAILURES_QC_PENDING"
        and proof["terminal_request_receipts_checked"] == 370 and proof["failed_raw_requests"] == len(expected) == 25
        and cfg["requests"] == expected and cfg["total_extra_retry_ceiling"] == len(expected)
        and cfg["expected_session_dates"] == parent["expected_session_dates"]
        and cfg["download_period"] == parent["download_period"], "Source amendment changed the independently verified failed subset")
    for name, digest in proof["receipt_bindings"].items():
        bind(name, digest)
    for name, digest in proof["artifacts"].items():
        bind(proof_path.parent / name, digest)
    source = read_json(bind(cfg["sdk_parameter_source_path"]))
    body = source.get("data", source)
    first = body["markdown"].index("def stock_zh_a_hist(")
    last = body["markdown"].index("\ndef ", first + 5)
    segment = body["markdown"][first:last]
    require(body["metadata"]["sourceURL"] == cfg["sdk_parameter_source_url"]
        and cfg["sdk_fields2_literal"] in segment and cfg["public_parameter_literal"] in segment
        and '"' + cfg["documented_public_ut"] + '"' in cfg["public_parameter_literal"],
        "SDK query parameter lacks the bound primary adapter source")
    digest = file_sha256(protocol_path)
    raw = root / cfg["output_root"]
    manifest = read_json(bind(raw / "source_manifest.json"))
    records, receipt_bindings, attempt_classes = {}, {}, Counter()
    checker = lambda url, spec, start, end: check_sdk_source_url(url, spec, start, end, cfg["documented_public_ut"])
    for spec in expected:
        key = spec["secid"] + "_fqt" + str(spec["fqt"])
        folder = raw / "full" / key
        receipt = folder / "receipt.json"
        record = read_json(bind(receipt))
        verified = audit_receipt(folder, record, spec, cfg, digest, "firecrawl", url_checker=checker)
        records[key] = verified
        receipt_bindings[receipt.relative_to(root).as_posix()] = verified["receipt_sha256"]
        attempt_classes.update(verified["attempt_classes"])
    reserved_paths = sorted((raw / "full/retry_reservations").glob("retry_*.json"))
    reservations = [read_json(bind(path)) for path in reserved_paths]
    charged = {(r["request_key"], r["attempt"]) for r in reservations}
    require(len(charged) == len(reservations) <= len(expected)
        and all(key in records and attempt == 2 for key, attempt in charged), "Supplemental retry accounting exceeds frozen scope")
    for key, value in records.items():
        for attempt in value["record"]["attempts"]:
            require(attempt["attempt"] == 1 or attempt["status"] == "NOT_REQUESTED_FROZEN_RETRY_QUOTA_EXHAUSTED"
                or (key, attempt["attempt"]) in charged, "Supplemental retry lacks a durable reservation")
    failures = sorted(key for key, value in records.items() if value["record"]["status"] == FAILED)
    require(manifest["protocol_sha256"] == digest and manifest["request_count"] == len(expected)
        and manifest["results"] == {key: value["record"] for key, value in records.items()}
        and manifest["failed_requests"] == failures and manifest["total_extra_retries"] == len(reservations)
        and manifest["original_batches_mutated"] is False and manifest["source_values_filled"] is False
        and manifest["model_eligible_requests"] == 0 and manifest["new_period_model_effects_evaluated"] is False,
        "Supplemental manifest differs from independent complete receipts")
    process_path = bind(cfg["process_receipt_path"])
    process = read_json(process_path)
    require(type(process["actual_child_exit_code"]) is int and process["actual_child_exit_code"] == int(bool(failures))
        and process["protocol_sha256"] == digest and process["stage"] == "supplement",
        "Supplemental completion has no matching observed actual exit")
    bind(process["log_path"], process["log_sha256"])
    claims = sorted((raw / "full").glob("run_claim_*.json"))
    require(len(claims) == 1 and read_json(bind(claims[0]))["pid"] == process["pid"],
        "Supplemental source launch lacks a unique matching actual process")
    with gzip.open(proof_path.parent / "source_positions.jsonl.gz", "rt", encoding="utf-8") as stream:
        base_rows = [json.loads(line) for line in stream]
    by_key = {}
    for row in base_rows:
        key = row["secid"] + "_fqt" + str(row["fqt"])
        by_key.setdefault(key, []).append(row)
    require(len(base_rows) == 370 * 139 and len(by_key) == 370, "Original independent source grid is incomplete")
    selected, merged = {}, []
    lookup = {}
    for spec in parent["requests"]:
        key = spec["secid"] + "_fqt" + str(spec["fqt"])
        use_new = key in records and records[key]["record"]["status"] == ACQUIRED
        rows = list(dense_source_rows(records[key]["series"], spec, parent["expected_session_dates"])) if use_new else by_key[key]
        require(len(rows) == 139 and [r["trade_date"] for r in rows] == parent["expected_session_dates"],
            "Merged source request lost registered calendar sessions")
        receipt_path = raw / "full" / key / "receipt.json" if use_new else root / parent["output_root"] / "full" / key / "receipt.json"
        selected[key] = {"receipt_path": receipt_path.relative_to(root).as_posix(), "receipt_sha256": file_sha256(receipt_path),
            "selected_protocol_sha256": digest if use_new else file_sha256(root / cfg["parent_protocol_path"]),
            "selection_basis": "ONLY_INDEPENDENTLY_VERIFIED_FAILED_REQUEST_REPLACED_BY_NEW_VALID_SOURCE" if use_new else "ORIGINAL_TERMINAL_RECEIPT_PRESERVED",
            "original_failed_receipt_preserved": key in records}
        for row in rows:
            require(row["model_eligible"] is False and row["certified_trade_status"] is None,
                "Merged raw source promoted model eligibility")
            if row["source_observation_status"] != "SOURCE_ROW_PRESENT_QC_PENDING":
                require(row["raw_line"] is None and all(v is None for v in row["provider_fields"].values()),
                    "Merged source filled unknown fields")
            merged.append({**row, "selected_source_receipt_path": selected[key]["receipt_path"]})
        present = {r["trade_date"]: {"raw_line": r["raw_line"], "provider_fields": r["provider_fields"]}
            for r in rows if r["source_observation_status"] == "SOURCE_ROW_PRESENT_QC_PENDING"}
        lookup[key] = {"rows": present} if present else None
    output.mkdir()
    positions_path = output / "source_positions.jsonl.gz"
    with positions_path.open("xb") as raw_file:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_file, mtime=0) as stream:
            for row in merged:
                stream.write((json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode())
    immutable_json(output / "selected_sources.json", selected)
    pairs = []
    for code in design["stocks"]:
        secid = ("1." if code.startswith(("5", "6", "9")) else "0.") + code
        for fqt in (1, 2):
            pairs.append({"secid": secid, "adjustment_pair": [0, fqt],
                "comparison": normalized_pair(lookup[secid + "_fqt0"], lookup[secid + "_fqt" + str(fqt)])})
    immutable_json(output / "adjustment_pair_qc.json", pairs)
    summary = {"status": "PASS_INDEPENDENT_SUPPLEMENT_AND_FULL_SOURCE_MERGE_WITH_FAILURES" if failures else "PASS_INDEPENDENT_SUPPLEMENT_AND_FULL_SOURCE_MERGE",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(), "original_request_count": 370,
        "supplement_request_count": len(expected), "valid_supplement_sources": len(records) - len(failures),
        "remaining_failed_requests": failures, "original_failed_receipts_preserved": len(expected),
        "full_source_positions": len(merged), "source_position_counts": dict(sorted(Counter(r["source_observation_status"] for r in merged).items())),
        "supplement_attempt_classes": dict(sorted(attempt_classes.items())), "bindings": bindings,
        "receipt_bindings": receipt_bindings, "full_cohort_retained": True, "source_values_filled": False,
        "new_period_model_effects_evaluated": False, "model_eligible": False, "point_in_time_feed_certified": False,
        "economic_total_return_certified": False, "protocol_sha256": digest,
        "artifacts": {p.name: file_sha256(p) for p in (positions_path, output / "selected_sources.json", output / "adjustment_pair_qc.json")},
        "auditor_path": Path(__file__).relative_to(root).as_posix(), "auditor_sha256": file_sha256(Path(__file__))}
    immutable_json(output / "results.json", summary)
    print(json.dumps({key: summary[key] for key in ("status", "valid_supplement_sources", "remaining_failed_requests", "source_position_counts")}), flush=True)
    return summary
