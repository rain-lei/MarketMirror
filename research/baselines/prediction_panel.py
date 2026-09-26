"""Construct a close-time panel without promoting unverified financial fields."""

from __future__ import annotations

import bisect
import json
import math
import re
import sqlite3
import statistics
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from .event_study import DailyObservation
from .run_experiments import _load_market
from ..data_pipeline.aggregate_qa_features import KEYWORDS
from ..data_pipeline.asof_features import VisibleTextFeatures
from ..data_pipeline.build_dataset import POLICY_PATH, SOURCE_TIMEZONE, VERSION as QA_VERSION, valid_stock_code
from ..data_pipeline.market_data import VERSION as MARKET_VERSION, strict_date
from ..data_pipeline.provenance import file_sha256

MARKET_FEATURES = ["stock_return_now", "market_return_now", "stock_mean_past", "stock_vol_past",
                   "market_mean_past", "market_vol_past"]
TEXT_FEATURES = ["log_question_count", "log_known_reply_count", "known_reply_share",
                 "log_question_length_mean", "log_reply_length_mean"] + [
    f"{prefix}_{category}_share" for prefix in ("question", "reply") for category in KEYWORDS
]


@dataclass(frozen=True)
class Question:
    question_available_at: str
    question_text: str
    reply_available_at: str | None = None
    reply_text: str | None = None
    reply_eligible: bool = False


def close_at(day: date) -> str:
    return datetime.combine(day, time(15), tzinfo=SOURCE_TIMEZONE).isoformat(timespec="microseconds")


def text_window(records: list[Question], start: str, cutoff: str, code: str) -> dict[str, Any]:
    """Records must be ordered by question availability; reply gating is independent."""
    times = [row.question_available_at for row in records]
    if times != sorted(times):
        raise ValueError("question records must be chronologically ordered")
    features = VisibleTextFeatures()
    for row in records[bisect.bisect_left(times, start):bisect.bisect_right(times, cutoff)]:
        reply = (row.reply_text if row.reply_eligible and row.reply_available_at is not None
                 and row.reply_available_at <= cutoff else None)
        features.add(row.question_text, reply)
    return features.row(code, start, cutoff)


def load_prediction_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    keys = {"run_id", "data_kind", "qa_database", "qa_source_sha256", "qa_coverage", "market_manifest",
            "stock_codes", "panel", "splits", "ridge_lambdas", "bootstrap"}
    if not isinstance(config, dict) or set(config) != keys:
        raise ValueError("prediction config has missing or unknown fields")
    for name in ("run_id", "qa_database", "market_manifest"):
        if not isinstance(config[name], str) or not config[name].strip():
            raise ValueError(f"{name} must be a nonempty string")
    if config["data_kind"] not in ("observed", "synthetic"):
        raise ValueError("data_kind must be observed or synthetic")
    if not isinstance(config["qa_source_sha256"], str) or not re.fullmatch("[a-f0-9]{64}", config["qa_source_sha256"]):
        raise ValueError("qa_source_sha256 must identify exactly one source")
    codes = config["stock_codes"]
    if (not isinstance(codes, list) or not codes or any(not isinstance(c, str) or valid_stock_code(c) != c for c in codes)
            or len(set(codes)) != len(codes)):
        raise ValueError("stock_codes require unique six-digit strings")
    coverage = config["qa_coverage"]
    if not isinstance(coverage, dict) or set(coverage) != {"start_date", "end_date", "basis"}:
        raise ValueError("qa_coverage requires dates and a declared coverage basis")
    if not isinstance(coverage["basis"], str) or not coverage["basis"].strip():
        raise ValueError("QA coverage basis must be explicit")
    panel = config["panel"]
    if not isinstance(panel, dict) or set(panel) != {"start_date", "end_date", "text_window_days", "market_window_sessions"}:
        raise ValueError("invalid panel configuration")
    for key, minimum in (("text_window_days", 1), ("market_window_sessions", 2)):
        if isinstance(panel[key], bool) or not isinstance(panel[key], int) or panel[key] < minimum:
            raise ValueError(f"{key} is invalid")
    for values in (coverage, panel):
        if strict_date(values["start_date"]) > strict_date(values["end_date"]):
            raise ValueError("date range is reversed")
    splits = config["splits"]
    if not isinstance(splits, dict) or set(splits) != {"train", "validation", "test"}:
        raise ValueError("three chronological splits are required")
    previous = None
    for name in ("train", "validation", "test"):
        value = splits[name]
        if not isinstance(value, dict) or set(value) != {"start_date", "end_date"}:
            raise ValueError("split requires start_date and end_date")
        start, end = strict_date(value["start_date"]), strict_date(value["end_date"])
        if start > end or (previous is not None and start <= previous):
            raise ValueError("splits must be ordered and disjoint")
        if not strict_date(panel["start_date"]) <= start <= end <= strict_date(panel["end_date"]):
            raise ValueError("split is outside panel")
        previous = end
    lambdas = config["ridge_lambdas"]
    if (not isinstance(lambdas, list) or not lambdas or any(isinstance(v, bool) or not isinstance(v, (float, int))
            or not math.isfinite(v) or v <= 0 for v in lambdas) or len(set(lambdas)) != len(lambdas)):
        raise ValueError("ridge_lambdas must be unique, finite and positive")
    boot = config["bootstrap"]
    if not isinstance(boot, dict) or set(boot) != {"seed", "replicates", "block_sessions"}:
        raise ValueError("bootstrap requires seed, replicates and block_sessions")
    for name, minimum in (("seed", 0), ("replicates", 100), ("block_sessions", 1)):
        if isinstance(boot[name], bool) or not isinstance(boot[name], int) or boot[name] < minimum:
            raise ValueError("invalid bootstrap setting")
    return config


def make_panel(groups: dict[str, list[DailyObservation]], questions: dict[str, list[Question]],
               config: dict[str, Any]) -> list[dict[str, Any]]:
    """One next-session target per selected stock and close, with no missing-day filling."""
    settings, coverage = config["panel"], config["qa_coverage"]
    codes = config["stock_codes"]
    start, end = strict_date(settings["start_date"]), strict_date(settings["end_date"])
    coverage_start = datetime.combine(strict_date(coverage["start_date"]), time(), tzinfo=SOURCE_TIMEZONE)
    coverage_end = strict_date(coverage["end_date"])
    rows, common_dates = [], None
    for code in codes:
        if code not in groups or code not in questions:
            raise ValueError(f"selected stock {code} lacks market or QA source coverage")
        series = groups[code]
        dates = [v.trade_date for v in series]
        if dates != sorted(set(dates)):
            raise ValueError("market dates must be unique and ordered")
        selected_dates = [d for d in dates if start <= d <= end]
        if not selected_dates or (common_dates is not None and selected_dates != common_dates):
            raise ValueError("selected stocks need the same complete panel calendar")
        common_dates = selected_dates
        records = questions[code]
        if not any(q.question_available_at < close_at(strict_date(config["splits"]["train"]["start_date"])) for q in records):
            raise ValueError(f"stock {code} has no questions before training; future QA coverage cannot justify selection")
        for i, obs in enumerate(series):
            if not start <= obs.trade_date <= end:
                continue
            if i + 1 >= len(series) or i + 1 < settings["market_window_sessions"]:
                raise ValueError("insufficient market history or next-session target")
            cutoff_dt = datetime.fromisoformat(close_at(obs.trade_date))
            window_dt = cutoff_dt - timedelta(days=settings["text_window_days"])
            if window_dt < coverage_start or obs.trade_date > coverage_end:
                raise ValueError("text window exceeds declared QA coverage")
            text = text_window(records, window_dt.isoformat(timespec="microseconds"), close_at(obs.trade_date), code)
            past = series[i + 1 - settings["market_window_sessions"]:i + 1]
            sr, mr = [v.stock_return for v in past], [v.market_return for v in past]
            target = series[i + 1]
            row = {"stock_code": code, "as_of": close_at(obs.trade_date), "trade_date": obs.trade_date.isoformat(),
                   "target_date": target.trade_date.isoformat(), "target_available_at": close_at(target.trade_date),
                   "target_return": target.stock_return, "stock_return_now": obs.stock_return,
                   "market_return_now": obs.market_return, "stock_mean_past": statistics.fmean(sr),
                   "stock_vol_past": statistics.pstdev(sr), "market_mean_past": statistics.fmean(mr),
                   "market_vol_past": statistics.pstdev(mr), **{k: v for k, v in text.items() if k not in ("stock_code", "as_of")}}
            for name in ("question_count", "known_reply_count", "question_length_mean", "reply_length_mean"):
                row[f"log_{name}"] = math.log1p(text[name] or 0)
            for identity in codes[1:]:
                row[f"stock_is_{identity}"] = float(code == identity)
            if any(not math.isfinite(row[name]) for name in MARKET_FEATURES + TEXT_FEATURES + ["target_return"]):
                raise ValueError("nonfinite panel value")
            rows.append(row)
    return sorted(rows, key=lambda r: (r["as_of"], r["stock_code"]))


def load_panel_inputs(config_path: Path, config: dict[str, Any]):
    """Verify manifests once for the whole panel, rather than hashing the DB every day."""
    database = (config_path.parent / config["qa_database"]).resolve()
    qa_manifest_path = database.parent / "run_manifest.json"
    market_manifest_path = (config_path.parent / config["market_manifest"]).resolve()
    qa = json.loads(qa_manifest_path.read_text(encoding="utf-8"))
    market = json.loads(market_manifest_path.read_text(encoding="utf-8"))
    market_path = market_manifest_path.parent / "market_daily.csv"
    inputs = {p: file_sha256(p) for p in (config_path, database, qa_manifest_path, market_manifest_path, market_path, POLICY_PATH)}
    if qa["pipeline_version"] != QA_VERSION or inputs[database] != qa["artifacts"][database.name]["sha256"]:
        raise ValueError("QA dataset version or hash differs from manifest")
    if qa["feature_policy_sha256"] != inputs[POLICY_PATH]:
        raise ValueError("QA feature policy changed; rebuild dataset")
    if market["pipeline_version"] != MARKET_VERSION or market["data_kind"] != config["data_kind"]:
        raise ValueError("market version or data kind differs from config")
    if inputs[market_path] != market["artifacts"][market_path.name]["sha256"]:
        raise ValueError("market CSV hash differs from manifest")
    source_hash = config["qa_source_sha256"]
    sources = [s for s in qa["sources"] if s["sha256"] == source_hash]
    if len(sources) != 1:
        raise ValueError("selected QA source is absent or ambiguous")
    codes = config["stock_codes"]
    questions = {c: [] for c in codes}
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
        metadata = {k: json.loads(v) for k, v in conn.execute("SELECT key, value_json FROM dataset_metadata")}
        if any(metadata[k] != qa[k] for k in ("dataset_id", "pipeline_version", "feature_policy_sha256", "code_sha256")):
            raise ValueError("QA database metadata differs from manifest")
        sql = ("SELECT stock_code, question_available_at, question_text, reply_available_at, reply_text, reply_eligible "
               "FROM qa_record WHERE question_eligible=1 AND source_file_hash=? AND stock_code IN ("
               + ",".join("?" for _ in codes) + ") AND question_available_at>=? AND question_available_at<? "
               "ORDER BY stock_code, question_available_at, qa_id")
        coverage = config["qa_coverage"]
        lower = datetime.combine(strict_date(coverage["start_date"]), time(), tzinfo=SOURCE_TIMEZONE).isoformat(timespec="microseconds")
        upper = datetime.combine(strict_date(coverage["end_date"]) + timedelta(days=1), time(), tzinfo=SOURCE_TIMEZONE).isoformat(timespec="microseconds")
        for code, qt, text, rt, reply, eligible in conn.execute(sql, [source_hash, *codes, lower, upper]):
            questions[code].append(Question(qt, text, rt, reply, bool(eligible)))
    panel = make_panel(_load_market(market_path, market), questions, config)
    provenance = {"dataset_id": qa["dataset_id"], "market_dataset_id": market["market_dataset_id"],
                  "qa_source": sources[0], "qa_questions_by_stock": {c: len(v) for c, v in questions.items()},
                  "qa_assumptions": qa["assumptions"], "market_settings": market["settings"]}
    protected = {Path(s["path"]).resolve() for s in qa["sources"] + qa["financial_inputs"]}
    protected.update(Path(v["path"]).resolve() for v in market["inputs"].values())
    return panel, inputs, provenance, protected
