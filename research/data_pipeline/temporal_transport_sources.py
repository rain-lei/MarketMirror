"""Bounded fixed-grid source acquisition, before temporal simulation effects.

Source validity is separate from trading status, units, return eligibility and
model success. A failed request cannot remove a company or become a zero.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import re
import subprocess
import threading
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import os

from .audit_eastmoney_carrier import carrier_body, check_url, raw_series
from .fetch_eastmoney_raw import inspect_response, request_url
from .provenance import file_sha256
from research_outputs.acquire_temporal_firecrawl_raw_20261002_v6 import RequestGate

VERSION = "temporal-transport-raw-sources-v1"
ACQUIRED = "RAW_SOURCE_ACQUIRED_MODEL_QC_PENDING"
FAILED = "FAILED_CARRIER_SOURCE_REQUEST"


def require(value, message):
    if not value:
        raise ValueError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value, exclusive=False):
    with Path(path).open("x" if exclusive else "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def key_for(spec):
    return spec["secid"] + "_fqt" + str(spec["fqt"])


def source_url(spec, start, end):
    require(type(spec["fqt"]) is int and spec["fqt"] in (0, 1, 2), "Invalid adjustment parameter")
    parts = urlsplit(request_url(spec["secid"], 0, start, end))
    query = dict(parse_qsl(parts.query))
    query["fqt"] = str(spec["fqt"])
    url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), "")).replace("%2C", ",")
    check_url(url, spec, start, end)
    return url


class RetryQuota:
    """Durable global retry ceiling; an interrupted retry remains spent."""
    def __init__(self, folder, maximum):
        require(type(maximum) is int and maximum >= 0, "Invalid retry ceiling")
        self.folder = Path(folder)
        self.folder.mkdir(exist_ok=True)
        self.maximum = maximum
        self.lock = threading.Lock()
        existing = sorted(self.folder.glob("retry_*.json"))
        require(len(existing) <= maximum, "Recorded retries exceed frozen ceiling")
        require([p.name for p in existing] == [f"retry_{i:03}.json" for i in range(1, len(existing) + 1)],
            "Retry records are not contiguous")
        self.spent = len(existing)

    def take(self, key, attempt):
        with self.lock:
            if self.spent >= self.maximum:
                return False
            self.spent += 1
            save(self.folder / f"retry_{self.spent:03}.json",
                {"request_key": key, "attempt": attempt, "reserved_at_utc": now()}, True)
            return True


def windows_process_identity(pid):
    """Distinguish a live recorded owner from PID reuse without listing commands."""
    require(os.name == "nt", "This acquisition runner requires its registered Windows host")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x00100000 | 0x1000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return None
        raise OSError(ctypes.get_last_error(), "Cannot establish source process liveness")
    try:
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME)]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        creation, finish, system, user = (wintypes.FILETIME() for _ in range(4))
        require(kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(finish),
            ctypes.byref(system), ctypes.byref(user)), "Cannot establish process creation time")
        state = kernel.WaitForSingleObject(handle, 0)
        require(state in (0, 258), "Cannot establish source process state")
        return {"pid": pid, "creation_ticks": (creation.dwHighDateTime << 32) | creation.dwLowDateTime,
            "live": state == 258}
    finally:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)


def claim_run(folder, protocol_sha256):
    """A receipt alone is insufficient to declare a previous process stopped."""
    for path in sorted(folder.glob("run_claim_*.json")):
        old = json.loads(path.read_text(encoding="utf-8"))
        require(old["protocol_sha256"] == protocol_sha256, "Existing run claim belongs to another protocol")
        identity = windows_process_identity(old["pid"])
        require(identity is None or not identity["live"] or identity["creation_ticks"] != old["creation_ticks"],
            "The same source stage already has a live process")
    identity = windows_process_identity(os.getpid())
    require(identity and identity["live"], "Cannot verify current source process")
    index = len(list(folder.glob("run_claim_*.json")))
    save(folder / f"run_claim_{index:03}.json", {**identity,
        "protocol_sha256": protocol_sha256, "started_at_utc": now()}, True)


class SourceBatch:
    def __init__(self, root, cfg, protocol_sha256, folder, transport, quota=None):
        require(transport in ("direct_http", "firecrawl"), "Unregistered source transport")
        self.root, self.cfg, self.digest = Path(root), cfg, protocol_sha256
        self.folder, self.transport = Path(folder), transport
        self.folder.mkdir(parents=True, exist_ok=True)
        self.gate = RequestGate(cfg["minimum_request_interval_seconds"])
        self.quota = quota

    def perform_attempt(self, spec, url, part, entry):
        if self.transport == "direct_http":
            request = Request(url, headers={"User-Agent": "MarketMirrorResearch/1.0",
                "Referer": "https://quote.eastmoney.com/"})
            with urlopen(request, timeout=self.cfg["direct_timeout_seconds"]) as stream:
                entry.update(http_status=stream.status, content_type=stream.headers.get("Content-Type"))
                body = stream.read(self.cfg["maximum_response_bytes"] + 1)
            require(len(body) <= self.cfg["maximum_response_bytes"], "Raw response exceeds registered byte bound")
            (part / "source_response.json").write_bytes(body)
            require(entry["http_status"] == 200, "Source HTTP status is not successful")
            return body
        command = [self.cfg["node_executable"], self.cfg["cli_entrypoint"], "scrape", url,
            "--format", "markdown", "-o", str(part / "carrier.md")]
        entry["command"] = command
        with (part / "cli.log").open("x", encoding="utf-8") as log:
            child = subprocess.Popen(command, cwd=self.root, stdout=log, stderr=subprocess.STDOUT)
            entry["pid"] = child.pid
            save(part / "process.json", entry)
            try:
                entry["observed_exit_code"] = child.wait(timeout=self.cfg["process_timeout_seconds"])
            except subprocess.TimeoutExpired:
                child.kill()
                entry["observed_exit_code"] = child.wait()
                raise TimeoutError("Carrier process exceeded registered timeout")
        log_text = (part / "cli.log").read_text(encoding="utf-8")
        limited = re.search(r"retry after ([0-9]+)s", log_text)
        if entry["observed_exit_code"] != 0 and limited:
            delay = int(limited.group(1)) + 3
            self.gate.defer(delay)
            entry["rate_limit_wait_seconds_before_next_request"] = delay
        require(type(entry["observed_exit_code"]) is int and entry["observed_exit_code"] == 0,
            "Carrier has an observed process failure")
        scrape_id = re.search(r"Scrape ID: ([0-9a-f-]+)", log_text)
        require(scrape_id is not None, "Carrier lacks completion identifier")
        entry["scrape_id"] = scrape_id.group(1)
        require((part / "carrier.md").stat().st_size <= self.cfg["maximum_response_bytes"],
            "Carrier exceeds registered byte bound")
        body = carrier_body((part / "carrier.md").read_text(encoding="utf-8"))
        (part / "source_response.json").write_bytes(body)
        return body

    def acquire(self, spec, max_attempts):
        key = key_for(spec)
        folder = self.folder / key
        folder.mkdir(exist_ok=True)
        start, end = self.cfg["download_period"]
        url = source_url(spec, start, end)
        receipt = folder / "receipt.json"
        if receipt.exists():
            saved = json.loads(receipt.read_text(encoding="utf-8"))
            require(saved["spec"] == spec and saved["url"] == url and saved["transport"] == self.transport
                and saved["protocol_sha256"] == self.digest, "Resumed source identity changed")
            for name, digest in saved["artifacts"].items():
                require(file_sha256(folder / name) == digest, "Resumed source artifact changed")
            return key, saved
        record = {"spec": spec, "url": url, "transport": self.transport, "protocol_sha256": self.digest,
            "attempts": [], "model_eligible": False, "execution_quote_eligible": False,
            "total_return_certified": False, "point_in_time_feed_certified": False,
            "explicit_trade_status_supplied": False,
            "carrier_is_not_original_http_wire_bytes": self.transport == "firecrawl"}
        for attempt in range(1, max_attempts + 1):
            part = folder / f"attempt_{attempt}"
            if part.exists():
                record["attempts"].append({"attempt": attempt, "status": "PRESERVED_INTERRUPTED_ATTEMPT_EXIT_UNOBSERVED",
                    "observed_exit_code": None})
                continue
            if attempt > 1 and (self.quota is None or not self.quota.take(key, attempt)):
                record["attempts"].append({"attempt": attempt, "status": "NOT_REQUESTED_FROZEN_RETRY_QUOTA_EXHAUSTED",
                    "observed_exit_code": None})
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
                require(inspection == independent["inspection"], "Independent raw source parsers disagree")
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

    def acquire_grid(self, specs, max_attempts):
        keys = [key_for(spec) for spec in specs]
        require(len(keys) == len(set(keys)), "Duplicate source request identities")
        results = {}
        with ThreadPoolExecutor(max_workers=self.cfg["max_workers"]) as executor:
            futures = [executor.submit(self.acquire, spec, max_attempts) for spec in specs]
            for future in as_completed(futures):
                key, record = future.result()
                results[key] = record
                if len(specs) <= 4 or len(results) % 10 == 0:
                    print(f"Source requests terminal {len(results)}/{len(specs)}; failures "
                        f"{sum(r['status'] == FAILED for r in results.values())}.", flush=True)
        require(set(results) == set(keys), "Source grid silently dropped a request")
        return dict(sorted(results.items()))


def verify_protocol(root, cfg):
    require(cfg["version"] == VERSION and cfg["outcome_analysis_enabled"] is False
        and cfg["semantic_gate_enabled"] is False and cfg["max_workers"] == cfg["max_attempts"] == 2
        and cfg["minimum_request_interval_seconds"] == 8.0 and cfg["total_extra_retry_ceiling"] == 100
        and cfg["direct_timeout_seconds"] == 15 and cfg["process_timeout_seconds"] == 55
        and cfg["maximum_response_bytes"] == 2_000_000, "Frozen source controls changed")
    original = json.loads((root / cfg["design_protocol_path"]).read_text(encoding="utf-8"))
    expected = [{"secid": ("1." if code.startswith(("5", "6", "9")) else "0.") + code,
        "fqt": fqt, "kind": "stock"} for code in original["stocks"] for fqt in (0, 1, 2)]
    expected.append({"secid": "1.000300", "fqt": 0, "kind": "benchmark"})
    require(len(expected) == 370 and cfg["requests"] == expected
        and cfg["expected_session_dates"] == original["source_trade_dates"], "Frozen full cohort/calendar changed")
    for name, digest in cfg["bindings"].items():
        require(file_sha256(root / name) == digest, "Frozen source binding changed: " + name)
    for name, digest in cfg["external_runtime_bindings"].items():
        require(file_sha256(Path(name)) == digest, "Registered source runtime changed")


def execute(root, protocol_path, stage):
    root, protocol_path = Path(root).resolve(), Path(protocol_path).resolve()
    cfg = json.loads(protocol_path.read_text(encoding="utf-8"))
    verify_protocol(root, cfg)
    digest = file_sha256(protocol_path)
    output = root / cfg["output_root"]
    output.mkdir(parents=True, exist_ok=True)
    require(output.resolve().is_relative_to(root), "Source output escapes workspace")
    identity = {"protocol_sha256": digest, "request_count": len(cfg["requests"]), "outcome_analysis_enabled": False}
    scope = output / "scope.json"
    if scope.exists():
        require(json.loads(scope.read_text(encoding="utf-8")) == identity, "Resumed source scope changed")
    else:
        require(not list(output.iterdir()), "Unregistered source folder is not empty")
        save(scope, identity, True)
    decision = output / "transport_decision.json"
    if stage == "probes":
        require(not decision.exists(), "Preserve completed source transport decision")
        probe_root = output / "probes"
        probe_root.mkdir(exist_ok=True)
        claim_run(probe_root, digest)
        reports = {}
        for transport in ("direct_http", "firecrawl"):
            batch = SourceBatch(root, cfg, digest, probe_root / transport, transport)
            results = batch.acquire_grid(cfg["probe_requests"], 1)
            reports[transport] = results
            valid = sum(r["status"] == ACQUIRED for r in results.values())
            if valid == 4 or (transport == "firecrawl" and valid >= 1):
                selected = transport
                break
        else:
            selected = None
        value = {**identity, "status": "SOURCE_TRANSPORT_SELECTED_QC_PENDING" if selected else "NO_VALID_SOURCE_TRANSPORT",
            "finished_at_utc": now(), "selected_transport": selected, "results": reports,
            "new_period_model_effects_evaluated": False,
            "probe_receipt_bindings": {p.relative_to(output).as_posix(): file_sha256(p)
                for p in sorted(probe_root.rglob("receipt.json"))}}
        save(decision, value, True)
        print(json.dumps({"status": value["status"], "selected_transport": selected}), flush=True)
        return int(selected is None)
    require(stage == "full" and decision.exists(), "Registered source probe decision required")
    choice = json.loads(decision.read_text(encoding="utf-8"))
    require(choice["protocol_sha256"] == digest and choice["selected_transport"] in ("direct_http", "firecrawl"),
        "Source transport decision identity or availability changed")
    for name, expected_digest in choice["probe_receipt_bindings"].items():
        require(file_sha256(output / name) == expected_digest, "Probe source receipt changed")
    full = output / "full"
    full.mkdir(exist_ok=True)
    require(not (output / "source_manifest.json").exists(), "Preserve completed source batch")
    claim_run(full, digest)
    quota = RetryQuota(full / "retry_reservations", cfg["total_extra_retry_ceiling"])
    batch = SourceBatch(root, cfg, digest, full, choice["selected_transport"], quota)
    results = batch.acquire_grid(cfg["requests"], cfg["max_attempts"])
    failures = [key for key, r in results.items() if r["status"] == FAILED]
    gaps = [key for key, r in results.items() if r.get("inspection", {}).get("calendar_coverage_complete") is False]
    manifest = {**identity, "status": "COMPLETE_FIXED_GRID_SOURCE_WITH_FAILURES" if failures else "COMPLETE_FIXED_GRID_SOURCE_QC_PENDING",
        "finished_at_utc": now(), "selected_transport": choice["selected_transport"],
        "transport_decision_sha256": file_sha256(decision), "results": results,
        "failed_requests": failures, "calendar_gap_requests": gaps, "total_extra_retries": quota.spent,
        "all_companies_retained": True, "model_eligible_requests": 0, "missing_values_filled": False,
        "new_period_model_effects_evaluated": False, "point_in_time_feed_certified": False,
        "economic_total_return_certified": False, "limitations": cfg["limitations"]}
    save(output / "source_manifest.json", manifest, True)
    print(json.dumps({"status": manifest["status"], "requests": len(results),
        "failed_requests": len(failures), "calendar_gap_requests": len(gaps), "retries": quota.spent}), flush=True)
    return int(bool(failures))
