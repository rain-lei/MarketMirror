"""Independent raw-carrier QC. This module creates no returns or model inputs."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from .provenance import file_sha256

FIELDS = tuple(f"f{position}" for position in range(51, 62))
ACQUIRED = "RAW_SOURCE_ACQUIRED_MODEL_QC_PENDING"
FAILED = "FAILED_CARRIER_SOURCE_REQUEST"


def require(value, message):
    if not value:
        raise ValueError(message)


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key: " + key)
            result[key] = value
        return result

    def constant(value):
        raise ValueError("non-finite JSON constant: " + value)

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def read_json(path):
    return strict_json(Path(path).read_text(encoding="utf-8"))


def exact_date(value):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value), "noncanonical date")
    parsed = date.fromisoformat(value)
    require(parsed.isoformat() == value, "date round trip differs")
    return value


def carrier_body(text):
    match = re.fullmatch(r"```json\s*\n(\{[\s\S]*\})\s*\n```", text.strip())
    require(match is not None, "carrier has extra text, HTML or an incomplete JSON body")
    body = match.group(1).encode("utf-8")
    require(isinstance(strict_json(body), dict), "carrier is not a JSON object")
    return body


def raw_series(body, secid, start, end, expected_dates):
    """Parse field strings independently; units, adjusted basis and trade status remain unknown."""
    require(re.fullmatch(r"[01]\.\d{6}", secid), "invalid source identity")
    exact_date(start)
    exact_date(end)
    require(start <= end and expected_dates == sorted(set(expected_dates)), "invalid expected calendar")
    for day in expected_dates:
        require(start <= exact_date(day) <= end, "calendar escapes request")
    payload = strict_json(body)
    require(isinstance(payload, dict) and type(payload.get("rc")) is int and payload["rc"] == 0,
            "provider did not return an integer successful response code")
    data = payload.get("data")
    require(isinstance(data, dict) and data.get("code") == secid[2:]
            and type(data.get("market")) is int and data["market"] == int(secid[0]), "company/exchange identity differs")
    lines = data.get("klines")
    require(isinstance(lines, list) and len(lines) > 0, "no source K-lines")
    rows, dates, blanks, zeros = {}, [], [], []
    for line in lines:
        require(isinstance(line, str), "K-line is not a field string")
        columns = line.split(",")
        require(len(columns) == 11, "K-line does not contain the eleven requested fields")
        day = exact_date(columns[0])
        require(start <= day <= end and day not in rows, "source date escapes request or is duplicated")
        dates.append(day)
        numbers = {}
        for field, value in zip(FIELDS[1:], columns[1:]):
            if value in {"", "-"}:
                numbers[field] = None
                blanks.append({"trade_date": day, "field": field})
                continue
            try:
                number = Decimal(value)
            except InvalidOperation as error:
                raise ValueError("source field is not numeric: " + field) from error
            require(number.is_finite(), "non-finite source field")
            require(field not in {"f56", "f57"} or number >= 0, "negative volume/amount field")
            numbers[field] = number
        if numbers["f56"] == 0:
            zeros.append(day)
        rows[day] = {"raw_line": line, "provider_fields": dict(zip(FIELDS, columns))}
    require(dates == sorted(dates), "source dates are not ordered")
    missing = sorted(set(expected_dates) - set(dates))
    unexpected = sorted(set(dates) - set(expected_dates))
    inspection = {
        "rows": len(dates), "first_date": dates[0], "last_date": dates[-1],
        "missing_expected_sessions": missing, "unexpected_sessions": unexpected,
        "blank_numeric_fields": blanks, "zero_volume_dates_without_explicit_trade_status": zeros,
        "calendar_coverage_complete": not missing and not unexpected,
        "provider_code": data["code"], "provider_name": data.get("name"),
        "basis_verified_for_model_use": False, "model_eligible": False,
        "price_and_volume_units_verified": False, "explicit_trade_status_supplied": False,
    }
    return {"inspection": inspection, "rows": rows,
            "unverified_provider_header": {key: data.get(key) for key in ("name", "decimal", "dktotal", "preKPrice")}}


def dense_source_rows(series, spec, expected_dates):
    for day in expected_dates:
        value = series["rows"].get(day) if series is not None else None
        status = ("SOURCE_REQUEST_FAILED" if series is None else
                  "SOURCE_ROW_ABSENT_NOT_CERTIFIED_SUSPENSION" if value is None else "SOURCE_ROW_PRESENT_QC_PENDING")
        yield {"secid": spec["secid"], "fqt": spec["fqt"], "kind": spec["kind"], "trade_date": day,
               "source_observation_status": status, "raw_line": value["raw_line"] if value else None,
               "provider_fields": value["provider_fields"] if value else {field: None for field in FIELDS},
               "certified_trade_status": None, "model_eligible": False}


def bounded_path(root, path):
    target = Path(path).resolve()
    require(target.is_relative_to(Path(root).resolve()), "artifact escapes its declared source folder")
    return target


def check_url(url, spec, start, end):
    parts = urlsplit(url)
    expected = {"secid": [spec["secid"]], "klt": ["101"], "fqt": [str(spec["fqt"])],
                "beg": [start.replace("-", "")], "end": [end.replace("-", "")],
                "fields1": ["f1,f2,f3,f4,f5,f6"], "fields2": [",".join(FIELDS)]}
    require(parts.scheme == "https" and parts.netloc == "push2his.eastmoney.com"
            and parts.path == "/api/qt/stock/kline/get" and not parts.fragment
            and parse_qs(parts.query, keep_blank_values=True) == expected, "request URL differs from frozen scope")


def classify_attempt(entry, log):
    if entry.get("error_type") == "TimeoutError":
        return "OBSERVED_PROCESS_TIMEOUT"
    if re.search(r"retry after [0-9]+s|rate limit", log, re.I):
        return "SERVER_RATE_LIMIT"
    if "ERR_EMPTY_RESPONSE" in log:
        return "BROWSER_EMPTY_RESPONSE"
    if entry.get("observed_exit_code") is None:
        return "EXIT_UNOBSERVED"
    if entry.get("status") == "VALID_CARRIER_SOURCE_RESPONSE":
        return "VALID_SOURCE"
    return "OTHER_OBSERVED_SOURCE_FAILURE"


class ReceiptAuditor:
    def __init__(self, root, protocol_registry):
        self.root = Path(root).resolve()
        self.registry = protocol_registry
        self.cache, self.bindings = {}, {}
        self.checked_protocols = {}

    def bind(self, path, expected=None):
        path = Path(path).resolve()
        actual = file_sha256(path)
        require(expected is None or actual == expected, "source artifact hash differs: " + str(path))
        self.bindings[str(path)] = actual
        return actual

    def protocol(self, digest):
        if digest in self.checked_protocols:
            return self.checked_protocols[digest]
        require(digest in self.registry, "inherited protocol was not declared")
        path = Path(self.registry[digest])
        self.bind(path, digest)
        cfg = read_json(path)
        for name, expected in cfg["bindings"].items():
            self.bind(self.root / name, expected)
        for name, expected in cfg["external_runtime_bindings"].items():
            self.bind(name, expected)
        self.checked_protocols[digest] = cfg
        return cfg

    def audit(self, cfg, digest, spec):
        key = spec["secid"] + "_fqt" + str(spec["fqt"])
        folder = self.root / cfg["output_root"] / key
        receipt_path = folder / "receipt.json"
        cache_key = str(receipt_path.resolve())
        if cache_key in self.cache:
            return self.cache[cache_key]
        self.bind(receipt_path)
        row = read_json(receipt_path)
        require(row["spec"] == spec and row["protocol_sha256"] == digest and row["model_eligible"] is False,
                "source receipt identity or eligibility differs")
        start, end = cfg["download_period"]
        check_url(row["url"], spec, start, end)
        require(row["status"] in {ACQUIRED, FAILED}, "unknown receipt status")
        if "inherited_source_receipt" in row:
            previous_path = self.root / cfg["inherited_source_root"] / key / "receipt.json"
            require(Path(row["inherited_source_receipt"]).resolve() == previous_path.resolve(), "inherited receipt path differs")
            self.bind(previous_path, row["inherited_source_receipt_sha256"])
            prior_digest = cfg["inherited_protocol_sha256"]
            prior = self.audit(self.protocol(prior_digest), prior_digest, spec)
            require(prior["receipt"]["status"] == ACQUIRED, "a failed source was inherited as successful")
            expected = {**prior["receipt"], "protocol_sha256": digest,
                        "successful_attempt": str(prior["part"].resolve()),
                        "inherited_source_receipt": str(previous_path.resolve()),
                        "inherited_source_receipt_sha256": file_sha256(previous_path),
                        "artifacts": {str(p.resolve()): h for p, h in prior["artifact_paths"].items()}}
            require(row == expected, "inherited receipt is not an exact provenance-preserving projection")
            result = {**prior, "receipt": row, "receipt_path": receipt_path}
            self.cache[cache_key] = result
            return result
        require(all(not Path(name).is_absolute() for name in row["artifacts"]), "new source receipt has external artifact paths")
        artifacts = {bounded_path(folder, folder / name): expected for name, expected in row["artifacts"].items()}
        actual_paths = {p.resolve() for p in folder.rglob("*") if p.is_file() and p != receipt_path}
        require(set(artifacts) == actual_paths, "source receipt does not bind exactly every attempt file")
        for path, expected in artifacts.items():
            self.bind(path, expected)
        attempts = row["attempts"]
        require(isinstance(attempts, list) and 1 <= len(attempts) <= cfg["max_attempts"], "attempt scope differs")
        require([a["attempt"] for a in attempts] == list(range(1, len(attempts) + 1)), "attempt numbers differ")
        classes = []
        for entry in attempts:
            part = folder / f"attempt_{entry['attempt']}"
            if entry["status"] == "PRESERVED_INTERRUPTED_ATTEMPT_EXIT_UNOBSERVED":
                require(entry["observed_exit_code"] is None, "unobserved exit was invented")
                classes.append("EXIT_UNOBSERVED")
                continue
            process = read_json(part / "process.json")
            require(process == entry, "attempt ledger and process receipt differ")
            require(type(entry["observed_exit_code"]) is int and type(entry.get("pid")) is int
                    and entry["pid"] > 0, "attempt lacks an observed integer exit and process identity")
            require(datetime.fromisoformat(entry["finished_at_utc"]) >= datetime.fromisoformat(entry["started_at_utc"]), "process timestamps reversed")
            command = entry["command"]
            require(len(command) == 8 and Path(command[0]).resolve() == Path(cfg["node_executable"]).resolve()
                    and Path(command[1]).resolve() == Path(cfg["cli_entrypoint"]).resolve()
                    and command[2:7] == ["scrape", row["url"], "--format", "markdown", "-o"]
                    and Path(command[7]).resolve() == (part / "carrier.md").resolve(), "actual child command differs")
            log = (part / "cli.log").read_text(encoding="utf-8")
            if entry["status"] == "VALID_CARRIER_SOURCE_RESPONSE":
                require(entry["observed_exit_code"] == 0
                        and re.search(r"Scrape ID: " + re.escape(entry["scrape_id"]) + r"\b", log), "source success has no actual successful exit/scrape identifier")
            else:
                require(entry["status"] == "FAILED_CARRIER_SOURCE_ATTEMPT" and entry.get("error_type"), "failed attempt lacks an error record")
            classes.append(classify_attempt(entry, log))
        series, part = None, None
        if row["status"] == ACQUIRED:
            part = bounded_path(folder, folder / row["successful_attempt"])
            process = read_json(part / "process.json")
            require(process["observed_exit_code"] == 0 and process["status"] == "VALID_CARRIER_SOURCE_RESPONSE",
                    "source promoted a failed or unknown process")
            body = carrier_body((part / "carrier.md").read_text(encoding="utf-8"))
            require(body == (part / "rendered_response.json").read_bytes(), "rendered response differs from whole carrier body")
            series = raw_series(body, spec["secid"], start, end, cfg["expected_session_dates"])
            require(series["inspection"] == row["inspection"], "independent raw inspection differs")
        else:
            require("successful_attempt" not in row and "inspection" not in row
                    and all(a["status"] != "VALID_CARRIER_SOURCE_RESPONSE" for a in attempts), "failed request reports success/features")
        result = {"receipt": row, "receipt_path": receipt_path, "series": series, "part": part,
                  "artifact_paths": artifacts, "attempt_classifications": classes}
        self.cache[cache_key] = result
        return result


def compare_pair(first, second):
    if first is None or second is None:
        return {"status": "SOURCE_PAIR_INCOMPLETE", "both_sources_acquired": False,
                "calendar_equal": None, "common_rows": None, "field_difference_counts": None}
    a, b = first["rows"], second["rows"]
    differences = Counter()
    for day in sorted(set(a) & set(b)):
        for field in FIELDS[1:]:
            x, y = a[day]["provider_fields"][field], b[day]["provider_fields"][field]
            equal = x == y if x in {"", "-"} or y in {"", "-"} else Decimal(x) == Decimal(y)
            if not equal:
                differences[field] += 1
    return {"status": "RAW_PARAMETER_PAIR_CHECKED_BASIS_PENDING", "both_sources_acquired": True,
            "calendar_equal": set(a) == set(b), "common_rows": len(set(a) & set(b)),
            "only_fqt0_dates": sorted(set(a) - set(b)), "only_fqt1_dates": sorted(set(b) - set(a)),
            "field_difference_counts": {field: differences[field] for field in FIELDS[1:]},
            "basis_verified_for_model_use": False}
