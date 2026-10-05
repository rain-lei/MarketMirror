"""Import declared price/return series against an explicit session calendar."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import platform
import re
import tempfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .build_dataset import valid_stock_code
from .provenance import file_sha256

VERSION = "market-import-v1"
OUTPUT_FIELDS = ["trade_date", "stock_code", "benchmark_id", "stock_return", "market_return", "stock_price", "benchmark_price"]


def strict_date(value: str) -> date:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValueError("date must use YYYY-MM-DD without a time suffix")
    return date.fromisoformat(value)


def read_config(path: Path) -> dict[str, Any]:
    settings = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(settings, dict):
        raise ValueError("market configuration must be an object")
    common = {"stock_input", "benchmark_input", "calendar_input", "benchmark_id", "stock_format", "benchmark_format",
              "stock_value_column", "benchmark_value_column", "stock_data_source", "benchmark_data_source",
              "calendar_data_source", "data_kind", "return_type"}
    conditional = {"stock_price_basis", "benchmark_price_basis", "stock_return_unit", "benchmark_return_unit",
                   "stock_return_basis", "benchmark_return_basis"}
    if common - settings.keys() or settings.keys() - common - conditional:
        raise ValueError("market configuration has missing or unknown fields")
    for key in common:
        if not isinstance(settings[key], str) or not settings[key].strip():
            raise ValueError(f"{key} must be a nonempty string")
    for key in conditional & settings.keys():
        if not isinstance(settings[key], str) or not settings[key].strip():
            raise ValueError(f"{key} must be a nonempty string")
    if settings["data_kind"] not in {"observed", "synthetic"} or settings["return_type"] != "simple":
        raise ValueError("data_kind must be observed/synthetic and return_type must be simple")
    for prefix in ("stock", "benchmark"):
        if settings[f"{prefix}_value_column"] in {"trade_date", "stock_code", "benchmark_id"}:
            raise ValueError("value column cannot be an identity or date column")
        fmt = settings[f"{prefix}_format"]
        if fmt == "prices":
            required = {f"{prefix}_price_basis"}
            forbidden = {f"{prefix}_return_unit", f"{prefix}_return_basis"}
            bases = {"split_dividend_adjusted", "split_adjusted"} if prefix == "stock" else {"price_index", "total_return_index"}
            if settings.get(f"{prefix}_price_basis") not in bases:
                raise ValueError(f"{prefix} price basis must be declared; unadjusted stock prices are unsupported")
        elif fmt == "returns":
            required = {f"{prefix}_return_unit", f"{prefix}_return_basis"}
            forbidden = {f"{prefix}_price_basis"}
            bases = {"adjusted_price_return", "total_return",
                     "mixed_adjusted_and_unadjusted_price_return"} if prefix == "stock" else {
                         "price_index_return", "total_return_index_return"}
            if settings.get(f"{prefix}_return_unit") not in {"decimal", "percent"}:
                raise ValueError(f"{prefix} return unit must be decimal or percent")
            if settings.get(f"{prefix}_return_basis") not in bases:
                raise ValueError(f"{prefix} return basis must be declared")
        else:
            raise ValueError(f"{prefix}_format must be prices or returns")
        if required - settings.keys() or forbidden & settings.keys():
            raise ValueError(f"{prefix} price/return settings contradict the selected format")
    return settings


def read_rows(path: Path, required: set[str]):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or required - set(fields):
            raise ValueError(f"missing or duplicate CSV headers in {path.name}")
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"CSV row length differs from its header: {path.name}, line {reader.line_num}")
            if any(v.strip() for v in row.values()):
                yield reader.line_num, row


def numeric(value: str, fmt: str, unit: str | None) -> float:
    try:
        number = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError("market value must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError("market value must be finite")
    if fmt == "prices":
        if number <= 0:
            raise ValueError("prices must be positive")
    else:
        if unit == "percent":
            number /= 100
        if number < -1:
            raise ValueError("simple returns cannot be below -100 percent")
    return number


def _returns(series: dict[date, float], fmt: str) -> dict[date, float]:
    if fmt == "returns":
        return dict(series)
    ordered = sorted(series)
    result = {}
    for previous, current in zip(ordered, ordered[1:]):
        value = series[current] / series[previous] - 1
        if not math.isfinite(value) or value < -1:
            raise ValueError("price-to-return conversion produced an invalid return")
        result[current] = value
    return result


def _require_sessions(series: dict[date, float], calendar: list[date], name: str, full: bool = False):
    if not series:
        raise ValueError(f"no observations for {name}")
    dates = set(series)
    expected = set(calendar) if full else {d for d in calendar if min(dates) <= d <= max(dates)}
    missing, extra = expected - dates, dates - set(calendar)
    if missing or extra:
        detail = f"{len(missing)} missing sessions, {len(extra)} dates outside calendar"
        raise ValueError(f"{name}: {detail}; no filling or multi-session return conversion is allowed")


def import_market(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    config_hash = file_sha256(config_path)
    settings = read_config(config_path)
    paths = {prefix: (config_path.parent / settings[f"{prefix}_input"]).resolve() for prefix in ("stock", "benchmark", "calendar")}
    protected = {config_path, *paths.values()}
    names = ["market_daily.csv", "market_manifest.json", "market_quality_report.md"]
    if any(output_dir / name in protected for name in names):
        raise ValueError("market output must not overwrite input files")
    inputs = {prefix: {"path": str(path), "sha256": file_sha256(path),
                       "data_source": settings[f"{prefix}_data_source"]} for prefix, path in paths.items()}
    calendar = []
    for _, row in read_rows(paths["calendar"], {"trade_date"}):
        calendar.append(strict_date(row["trade_date"]))
    if not calendar or len(calendar) != len(set(calendar)):
        raise ValueError("calendar must contain unique, nonempty session dates")
    calendar.sort()
    benchmark = {}
    counts = Counter()
    for _, row in read_rows(paths["benchmark"], {"trade_date", "benchmark_id", settings["benchmark_value_column"]}):
        if not row["benchmark_id"].strip():
            raise ValueError("benchmark_id is missing")
        if row["benchmark_id"] != settings["benchmark_id"]:
            counts["benchmark_rows_not_selected"] += 1
            continue
        day = strict_date(row["trade_date"])
        if day in benchmark:
            raise ValueError("duplicate benchmark session")
        benchmark[day] = numeric(row[settings["benchmark_value_column"]], settings["benchmark_format"], settings.get("benchmark_return_unit"))
    _require_sessions(benchmark, calendar, settings["benchmark_id"], full=True)
    stocks = {}
    for line, row in read_rows(paths["stock"], {"trade_date", "stock_code", settings["stock_value_column"]}):
        code = valid_stock_code(row["stock_code"])
        if code is None:
            raise ValueError(f"invalid stock code at line {line}")
        day = strict_date(row["trade_date"])
        series = stocks.setdefault(code, {})
        if day in series:
            raise ValueError(f"duplicate stock session: {code}, {day}")
        series[day] = numeric(row[settings["stock_value_column"]], settings["stock_format"], settings.get("stock_return_unit"))
    if not stocks:
        raise ValueError("stock input has no observations")
    market_returns = _returns(benchmark, settings["benchmark_format"])
    normalized = []
    summaries = {}
    for code in sorted(stocks):
        series = stocks[code]
        _require_sessions(series, calendar, code)
        if settings["stock_format"] == "prices" and len(series) < 2:
            raise ValueError(f"two price sessions are required for {code}")
        stock_returns = _returns(series, settings["stock_format"])
        aligned = sorted(set(stock_returns) & set(market_returns))
        if not aligned:
            raise ValueError(f"no aligned return observations for {code}")
        counts["stock_input_rows"] += len(series)
        counts["warmup_rows_without_aligned_returns"] += len(series) - len(aligned)
        for day in aligned:
            sr, mr = stock_returns[day], market_returns[day]
            counts["absolute_returns_over_50_percent"] += int(abs(sr) > 0.5)
            normalized.append({
                "trade_date": day.isoformat(), "stock_code": code, "benchmark_id": settings["benchmark_id"],
                "stock_return": sr, "market_return": mr,
                "stock_price": series[day] if settings["stock_format"] == "prices" else None,
                "benchmark_price": benchmark[day] if settings["benchmark_format"] == "prices" else None,
            })
        summaries[code] = {"input_rows": len(series), "return_rows": len(aligned),
                           "first_return_date": aligned[0].isoformat(), "last_return_date": aligned[-1].isoformat()}
    for prefix, path in paths.items():
        if file_sha256(path) != inputs[prefix]["sha256"]:
            raise RuntimeError("market input changed during import")
    if file_sha256(config_path) != config_hash:
        raise RuntimeError("market configuration changed during import")
    counts.update({"benchmark_input_rows": len(benchmark), "calendar_sessions": len(calendar),
                   "stock_codes": len(stocks), "market_rows": len(normalized)})
    code_hashes = {name: file_sha256(Path(__file__).with_name(name)) for name in ("market_data.py", "build_dataset.py", "provenance.py")}
    payload = json.dumps([settings, inputs, config_hash, code_hashes], sort_keys=True, ensure_ascii=False)
    manifest = {
        "pipeline_version": VERSION, "market_dataset_id": hashlib.sha256(payload.encode()).hexdigest(),
        "generated_at": datetime.now(timezone.utc).isoformat(), "data_kind": settings["data_kind"],
        "config_path": str(config_path), "config_sha256": config_hash, "settings": settings,
        "inputs": inputs, "counts": dict(counts), "series": summaries, "code_sha256": code_hashes,
        "python_version": platform.python_version(), "session_dates": [d.isoformat() for d in calendar],
        "assumptions": [
            "Calendar and price/return basis are declared by the input provider, not independently verified.",
            "All output returns are simple decimal returns. Prices use current/previous session minus one.",
            "No filling, missing-session bridging, winsorizing or corporate-action adjustment is performed.",
            "Each stock must cover every supplied session between its first and last input date.",
            "A price series needs one warmup session; a stock may have its own listing or coverage start.",
            "Stock and benchmark return bases may differ; both are preserved for interpretation.",
            "A mixed stock return basis must be declared explicitly and must not be interpreted as a single uniform adjustment series.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        staging = Path(tmp)
        with (staging / names[0]).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            writer.writerows(sorted(normalized, key=lambda r: (r["stock_code"], r["trade_date"])))
        manifest["artifacts"] = {names[0]: {"sha256": file_sha256(staging / names[0]), "bytes": (staging / names[0]).stat().st_size}}
        lines = ["# Market import report", "", f"Data kind: **{settings['data_kind']}**.",
                 "Synthetic data is an implementation fixture and provides no historical-market evidence." if settings["data_kind"] == "synthetic" else
                 "Observed data provenance and adjustment basis are provider declarations; import validation is not source verification.", "",
                 f"Benchmark: `{settings['benchmark_id']}`", f"Stock codes: {len(stocks)}", f"Aligned return rows: {len(normalized)}", "",
                 "## Checks and assumptions", ""] + [f"- {v}" for v in manifest["assumptions"]]
        (staging / names[2]).write_text("\n".join(lines) + "\n", encoding="utf-8")
        manifest["artifacts"][names[2]] = {"sha256": file_sha256(staging / names[2])}
        (staging / names[1]).write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        for name in names:
            (staging / name).replace(output_dir / name)
    return manifest
