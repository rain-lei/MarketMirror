"""Finite source amendment for the independently audited failed requests."""
from pathlib import Path
import re
from urllib.parse import urlencode

from .audit_eastmoney_carrier import ACQUIRED, FAILED, raw_series, read_json, require
from .fetch_eastmoney_raw import inspect_response
from .provenance import file_sha256
from .temporal_transport_sources import SourceBatch, save, now, key_for, claim_run, RetryQuota, verify_protocol


def sdk_request_url(spec, start, end, public_parameter):
    return "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urlencode({
        "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116",
        "ut": public_parameter, "klt": "101", "fqt": str(spec["fqt"]), "secid": spec["secid"],
        "beg": start.replace("-", ""), "end": end.replace("-", "")}).replace("%2C", ",")


def validate_supplement(root, cfg):
    root = Path(root).resolve()
    require(cfg["version"] == "temporal-transport-failed-subset-sdk-source-amendment-v1"
        and cfg["outcome_analysis_enabled"] is False and cfg["max_workers"] == cfg["max_attempts"] == 2
        and cfg["minimum_request_interval_seconds"] == 8.0 and cfg["process_timeout_seconds"] == 55
        and cfg["maximum_response_bytes"] == 2_000_000 and cfg["original_batches_mutated"] is False,
        "Frozen finite source amendment changed")
    for name, digest in cfg["bindings"].items():
        path = (root / name).resolve()
        require(path.is_relative_to(root) and file_sha256(path) == digest, "Frozen source amendment binding changed: " + name)
    for name, digest in cfg["external_runtime_bindings"].items():
        require(file_sha256(Path(name)) == digest, "Source CLI runtime changed")
    parent = read_json(root / cfg["parent_protocol_path"])
    verify_protocol(root, parent)
    proof = read_json(root / cfg["parent_source_qc_path"])
    manifest = read_json(root / parent["output_root"] / "source_manifest.json")
    expected = [spec for spec in parent["requests"] if manifest["results"][key_for(spec)]["status"] == FAILED]
    require(proof["status"] == "PASS_INDEPENDENT_COMPLETE_SOURCE_GRID_NULL_FAILURES_QC_PENDING"
        and proof["terminal_request_receipts_checked"] == len(parent["requests"])
        and proof["failed_raw_requests"] == len(expected) == 25 and cfg["requests"] == expected
        and cfg["expected_session_dates"] == parent["expected_session_dates"]
        and cfg["download_period"] == parent["download_period"] and cfg["total_extra_retry_ceiling"] == len(expected),
        "Source amendment changed the failed subset, full calendar or retry ceiling")
    source = read_json(root / cfg["sdk_parameter_source_path"])
    body = source.get("data", source)
    text = body["markdown"]
    first = text.index("def stock_zh_a_hist(")
    last = text.index("\ndef ", first + 5)
    segment = text[first:last]
    require(body["metadata"]["sourceURL"] == cfg["sdk_parameter_source_url"]
        and cfg["sdk_fields2_literal"] in segment and cfg["public_parameter_literal"] in segment
        and re.fullmatch(r"[0-9a-f]{32}", cfg["documented_public_ut"]),
        "Documented public SDK source fields/parameter absent")
    return parent, proof


class SupplementBatch(SourceBatch):
    """Reuse bounded carrier process handling; archive the new URL explicitly."""
    def acquire(self, spec, max_attempts):
        key = key_for(spec)
        folder = self.folder / key
        folder.mkdir(exist_ok=True)
        start, end = self.cfg["download_period"]
        url = sdk_request_url(spec, start, end, self.cfg["documented_public_ut"])
        receipt = folder / "receipt.json"
        if receipt.exists():
            old = read_json(receipt)
            require(old["spec"] == spec and old["url"] == url and old["protocol_sha256"] == self.digest,
                "Resumed supplemental source identity changed")
            for name, digest in old["artifacts"].items():
                require(file_sha256(folder / name) == digest, "Resumed supplemental attempt changed")
            return key, old
        record = {"spec": spec, "url": url, "transport": self.transport, "protocol_sha256": self.digest,
            "attempts": [], "model_eligible": False, "execution_quote_eligible": False,
            "total_return_certified": False, "point_in_time_feed_certified": False,
            "explicit_trade_status_supplied": False, "carrier_is_not_original_http_wire_bytes": True}
        for attempt in range(1, max_attempts + 1):
            part = folder / f"attempt_{attempt}"
            if part.exists():
                record["attempts"].append({"attempt": attempt,
                    "status": "PRESERVED_INTERRUPTED_ATTEMPT_EXIT_UNOBSERVED", "observed_exit_code": None})
                continue
            if attempt > 1 and (self.quota is None or not self.quota.take(key, attempt)):
                record["attempts"].append({"attempt": attempt,
                    "status": "NOT_REQUESTED_FROZEN_RETRY_QUOTA_EXHAUSTED", "observed_exit_code": None})
                break
            part.mkdir()
            entry = {"attempt": attempt, "status": "WAITING_FOR_RATE_GATE", "observed_exit_code": None}
            record["attempts"].append(entry)
            try:
                self.gate.wait()
                entry.update(status="RUNNING", started_at_utc=now())
                save(part / "process.json", entry)
                body = self.perform_attempt(spec, url, part, entry)
                inspection = inspect_response(body, spec["secid"], start, end, self.cfg["expected_session_dates"])
                independent = raw_series(body, spec["secid"], start, end, self.cfg["expected_session_dates"])
                require(inspection == independent["inspection"], "Supplemental independent source parsers disagree")
                entry.update(status="VALID_CARRIER_SOURCE_RESPONSE", source_response_bytes=len(body))
                record.update(status=ACQUIRED, inspection=inspection, successful_attempt=part.name)
            except Exception as error:
                entry.update(status="FAILED_CARRIER_SOURCE_ATTEMPT", error_type=type(error).__name__, error=str(error))
            entry["finished_at_utc"] = now()
            save(part / "process.json", entry)
            if "successful_attempt" in record:
                break
        if "status" not in record:
            record["status"] = FAILED
        record["artifacts"] = {p.relative_to(folder).as_posix(): file_sha256(p)
            for p in sorted(folder.rglob("*")) if p.is_file()}
        save(receipt, record, True)
        return key, record


def execute_supplement(root, protocol_path):
    root, protocol_path = Path(root).resolve(), Path(protocol_path).resolve()
    cfg = read_json(protocol_path)
    parent, proof = validate_supplement(root, cfg)
    digest = file_sha256(protocol_path)
    output = (root / cfg["output_root"]).resolve()
    require(output.is_relative_to(root), "Supplemental source output escapes workspace")
    output.mkdir(exist_ok=True)
    scope = output / "scope.json"
    identity = {"protocol_sha256": digest, "request_count": len(cfg["requests"]),
        "original_fixed_request_count": len(parent["requests"]), "outcome_analysis_enabled": False}
    if scope.exists():
        require(read_json(scope) == identity, "Resumed supplemental source scope changed")
    else:
        require(not list(output.iterdir()), "Supplemental source directory is not registered empty")
        save(scope, identity, True)
    require(not (output / "source_manifest.json").exists(), "Preserve completed source supplement")
    full = output / "full"
    full.mkdir(exist_ok=True)
    claim_run(full, digest)
    quota = RetryQuota(full / "retry_reservations", cfg["total_extra_retry_ceiling"])
    batch = SupplementBatch(root, cfg, digest, full, "firecrawl", quota)
    results = batch.acquire_grid(cfg["requests"], cfg["max_attempts"])
    failed = sorted(key for key, record in results.items() if record["status"] == FAILED)
    manifest = {**identity, "status": "COMPLETE_FAILED_SUBSET_SOURCE_SUPPLEMENT_WITH_FAILURES" if failed else "COMPLETE_FAILED_SUBSET_SOURCE_SUPPLEMENT",
        "results": results, "failed_requests": failed, "total_extra_retries": quota.spent,
        "finished_at_utc": now(), "original_batches_mutated": False, "source_values_filled": False,
        "model_eligible_requests": 0, "new_period_model_effects_evaluated": False}
    save(output / "source_manifest.json", manifest, True)
    print({"source_supplement_requests": len(results), "failed_requests": len(failed)}, flush=True)
    return int(bool(failed))
