"""Cross-year official rate facts and strictly lagged diagnostic panels."""

from __future__ import annotations

import math
import re
import statistics
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction

from .monetary_operations import BulletinHTML, CHINA, chinese_number, normalize


def parse_rate_bulletin(raw: bytes, expected_date: str, expected_title: str) -> dict:
    """Versioned generalization; the frozen 2019 parser is left untouched."""
    text = raw.decode("utf-8")
    if "\ufffd" in text:
        raise ValueError("rate bulletin contains replacement characters")
    parser = BulletinHTML()
    parser.feed(text)
    if parser.region_counts != {"title": 1, "timestamp": 1, "body": 1}:
        raise ValueError("rate bulletin article regions are missing or ambiguous")
    title = normalize("".join(parser.values["title"]))
    timestamp = normalize("".join(parser.values["timestamp"]))
    body = normalize(" ".join(parser.values["body"]))
    identity = re.fullmatch(r"公开市场业务交易公告 \[(\d{4})\]第\d+号", title)
    day = date.fromisoformat(expected_date)
    if title != expected_title or not identity or int(identity[1]) != day.year:
        raise ValueError("rate bulletin title/year differs from frozen discovery")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", timestamp):
        raise ValueError("rate bulletin lacks the explicit historical second clock")
    stamp = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CHINA)
    if stamp.date() != day:
        raise ValueError("rate bulletin page date differs from discovery")
    footer = re.findall(r"([〇零一二三四五六七八九]{4})年([一二三四五六七八九十]+)月([一二三四五六七八九十]+)日", body)
    if len(footer) != 1 or date(*(chinese_number(value) for value in footer[0])) != day:
        raise ValueError("rate bulletin signed date differs or is absent")
    if "中国人民银行公开市场业务操作室" not in body:
        raise ValueError("rate bulletin operator signature is absent")
    absent = bool(re.search(r"(?:不开展|无)逆回购(?:操作)?", body))
    hk = "香港金融管理局" in body and "央行票据" in body
    cbs = "央行票据互换" in body or "CBS" in body
    compact = re.sub(r"\s+", "", body)
    tmlf = bool(re.search(r"(?:TMLF|定向中期借贷便利)操作情况", compact))
    mlf = bool(re.search(r"(?<!T)MLF操作情况|(?<!定向)中期借贷便利操作情况", compact))
    if mlf and tmlf:
        raise ValueError("mixed MLF and TMLF tables require a section-aware parser")
    repo = "逆回购操作情况" in body
    operations = []
    for cells in parser.rows:
        fields = [cells[2], cells[1], cells[3]] if hk and len(cells) == 4 and "央行票据（香港）" in cells[0] else cells
        if len(fields) != 3:
            continue
        term = re.fullmatch(r"(\d+)(天|年|个月)(?:[（(]\d+天[）)])?", re.sub(r"\s+", "", fields[0]))
        amount = re.fullmatch(r"(\d+(?:\.\d+)?)亿元", re.sub(r"\s+", "", fields[1]))
        rate = re.fullmatch(r"(\d+(?:\.\d+)?)%", re.sub(r"\s+", "", fields[2]))
        if not (term and amount and rate):
            continue
        instrument = ("hk_central_bank_bill" if hk else "cbs" if cbs else "tmlf" if tmlf and term[2] == "年"
                      else "mlf" if mlf and term[2] == "年" else "reverse_repo" if repo else None)
        if instrument is None:
            raise ValueError("rate bulletin tender table has an unsupported instrument")
        operations.append({"instrument": instrument, "tenor_value": int(term[1]), "tenor_unit": term[2],
                           "gross_amount_100m_yuan": float(Decimal(amount[1])), "rate_pct": float(Decimal(rate[1])),
                           "rate_kind": "bill_yield" if hk else "fee" if cbs else "tender_interest",
                           "market": "offshore_hk" if hk else "mainland", "table_evidence": cells})
    if cbs and not operations:
        amount = re.search(r"操作量为(\d+(?:\.\d+)?)亿元", body)
        tenor = re.search(r"期限为(\d+)(个月|年)", body)
        rate = re.search(r"费率为(\d+(?:\.\d+)?)%", body)
        if not (amount and tenor and rate):
            raise ValueError("CBS fee/amount/tenor evidence is incomplete")
        operations.append({"instrument": "cbs", "tenor_value": int(tenor[1]), "tenor_unit": tenor[2],
                           "gross_amount_100m_yuan": float(Decimal(amount[1])), "rate_pct": float(Decimal(rate[1])),
                           "rate_kind": "fee", "market": "mainland", "table_evidence": None})
    required = ({"hk_central_bank_bill"} if hk else set()) | ({"cbs"} if cbs else set())
    required |= {"tmlf"} if tmlf else {"mlf"} if mlf else set()
    required |= {"reverse_repo"} if repo else set()
    if (not required <= {row["instrument"] for row in operations}
            or absent and any(row["instrument"] == "reverse_repo" for row in operations)
            or not operations and not absent):
        raise ValueError("stated rate operations are omitted or contradictory")
    return {"kind": "omo", "title": title, "publication_timestamp": stamp.isoformat(),
            "publication_precision": "second", "available_at": stamp.isoformat(),
            "availability_rule": "explicit_original_page_second_clock", "signed_date": expected_date,
            "body_text": body, "operations": operations, "policy_measures": [], "explicit_no_reverse_repo": absent,
            "gross_reverse_repo_amount_100m_yuan": 0.0 if absent else sum(row["gross_amount_100m_yuan"] for row in operations if row["instrument"] == "reverse_repo") if repo else None,
            "net_liquidity_100m_yuan": None,
            "use_policy": {"mode": "source_verified_context", "agent_signal_enabled": False}}


def make_clocks(days: list[str], first: str, last: str) -> list[dict]:
    if not days or days != sorted(set(days)):
        raise ValueError("diagnostic calendar is empty, duplicated or unordered")
    for day in days:
        date.fromisoformat(day)
    if first > last or first < days[2] or last > days[-1]:
        raise ValueError("diagnostic clock range lacks the required history/coverage")
    return [{"trade_date": day, "signal_cutoff_date": days[index - 2],
             "execution_reference_date": days[index - 1]}
            for index, day in enumerate(days) if first <= day <= last]


def policy_vectors(records: list[dict], calendar: list[dict], channels: list[tuple]) -> list[dict]:
    if len(set(channels)) != len(channels):
        raise ValueError("rate diagnostic channels are duplicated")
    by_id = {record["source_id"]: record for record in records}
    if len(by_id) != len(records):
        raise ValueError("rate diagnostic sources are duplicated")
    lookup = {key: index for index, key in enumerate(channels)}
    panel = []
    for row in calendar:
        values, events, unknown = [0.0] * len(channels), [], []
        for identity in row["newly_visible_source_ids"]:
            record = by_id[identity]
            if record["available_at"] > row["cutoff_timestamp"]:
                raise ValueError("future rate source entered diagnostic inputs")
            for operation in record["operations"]:
                key = operation["instrument"], operation["tenor_value"], operation["tenor_unit"]
                if operation["market"] != "mainland" or key not in lookup:
                    continue
                column, delta = lookup[key], operation["change_from_previous_observed_bps"]
                if delta is None:
                    values[column] = None
                    unknown.append({"source_id": identity, "channel": list(key)})
                elif values[column] is not None:
                    values[column] += delta
                if delta not in {None, 0.0}:
                    events.append({"source_id": identity, "channel": list(key), "change_bps": delta})
        panel.append({"trade_date": row["trade_date"], "change_bps_by_channel": values,
                      "observed_rate_changes": events, "unknown_predecessors": unknown})
    return panel


def diagnostic_rows(days: list[str], closes: dict[str, float], calendar: list[dict], panel: list[dict]) -> list[dict]:
    if set(closes) != set(days) or any(not math.isfinite(value) or value <= 0 for value in closes.values()):
        raise ValueError("benchmark prices lack exact calendar coverage or positive finite levels")
    positions = {day: index for index, day in enumerate(days)}
    returns = [None] + [closes[day] / closes[days[index - 1]] - 1 for index, day in enumerate(days) if index]
    if len(panel) != len(calendar):
        raise ValueError("rate and clock panel sizes differ")
    rows = []
    for clock, policy in zip(calendar, panel):
        if clock["trade_date"] != policy["trade_date"]:
            raise ValueError("rate and clock rows are misaligned")
        index = positions[clock["trade_date"]]
        if (index < 2 or clock["signal_cutoff_date"] != days[index - 2]
                or clock["execution_reference_date"] != days[index - 1]):
            raise ValueError("diagnostic clock does not use the exact prior two exchange dates")
        cutoff = index - 2
        values = policy["change_bps_by_channel"]
        excluded = "insufficient_twenty_return_history" if cutoff < 20 else "unknown_new_policy_predecessor" if any(value is None for value in values) else None
        features = None
        if cutoff >= 20:
            history = returns[cutoff - 19:cutoff + 1]
            features = [1.0, closes[days[cutoff]] / closes[days[cutoff - 5]] - 1,
                        statistics.stdev(history), sum(abs(value) for value in history) / 20]
        target = returns[index]
        rows.append({**clock, "baseline_features": features, "policy_features": values,
                     "target_return": target, "target_absolute_return": abs(target), "paired_exclusion_reason": excluded})
    return rows


def fraction_rank(matrix: list[list[float]]) -> int:
    if not matrix or not matrix[0] or any(len(row) != len(matrix[0]) for row in matrix):
        raise ValueError("diagnostic design matrix is empty or ragged")
    rows = [[Fraction(str(value)) for value in row] for row in matrix]
    rank = 0
    for column in range(len(rows[0])):
        pivot = next((i for i in range(rank, len(rows)) if rows[i][column]), None)
        if pivot is None:
            continue
        rows[rank], rows[pivot] = rows[pivot], rows[rank]
        base = rows[rank][column]
        rows[rank] = [value / base for value in rows[rank]]
        for index in range(rank + 1, len(rows)):
            multiplier = rows[index][column]
            if multiplier:
                rows[index] = [value - multiplier * ref for value, ref in zip(rows[index], rows[rank])]
        rank += 1
        if rank == len(rows):
            break
    return rank


def error_metrics(actual: list[float], predicted: list[float], training_mean: float, absolute_target: bool) -> dict:
    if not actual or len(actual) != len(predicted) or any(not math.isfinite(value) for value in actual + predicted):
        raise ValueError("diagnostic predictions are empty, unpaired or nonfinite")
    sse = sum((a - p) ** 2 for a, p in zip(actual, predicted))
    reference = sum((a - training_mean) ** 2 for a in actual)
    return {"rows": len(actual), "mae": sum(abs(a - p) for a, p in zip(actual, predicted)) / len(actual),
            "rmse": math.sqrt(sse / len(actual)), "r2_against_training_target_mean": 1 - sse / reference if reference else None,
            "return_direction_accuracy": None if absolute_target else sum((a > 0) - (a < 0) == (p > 0) - (p < 0) for a, p in zip(actual, predicted)) / len(actual),
            "absolute_return_predictions_below_zero": sum(p < 0 for p in predicted) if absolute_target else None}


def fit_diagnostic(rows: list[dict], training_end: str, evaluation_start: str) -> dict:
    import numpy as np

    if training_end >= evaluation_start:
        raise ValueError("diagnostic training and evaluation periods overlap")
    paired = [row for row in rows if row["paired_exclusion_reason"] is None]
    train = [row for row in paired if row["trade_date"] <= training_end]
    check = [row for row in paired if row["trade_date"] >= evaluation_start]
    if len(train) + len(check) != len(paired) or not train or not check:
        raise ValueError("diagnostic split is empty or leaves unassigned paired rows")
    designs, diagnostics = {}, {}
    for name, extra in (("market_state", False), ("market_state_plus_rates", True)):
        matrix = [row["baseline_features"] + (row["policy_features"] if extra else []) for row in train]
        x = np.asarray(matrix, dtype=float)
        exact, numerical = fraction_rank(matrix), int(np.linalg.matrix_rank(x, tol=1e-10))
        if exact != numerical:
            raise ValueError("independent exact and numerical diagnostic ranks disagree")
        diagnostics[name] = {"training_rows": len(train), "columns": x.shape[1], "exact_fraction_rank": exact,
                             "numpy_rank": numerical, "full_column_rank": exact == x.shape[1],
                             "condition_number": float(np.linalg.cond(x)) if exact == x.shape[1] else None}
        designs[name] = (x, np.asarray([row["baseline_features"] + (row["policy_features"] if extra else []) for row in check], dtype=float))
    result = {"training_rows": len(train), "evaluation_rows": len(check), "excluded_rows": [{"trade_date": row["trade_date"], "reason": row["paired_exclusion_reason"]} for row in rows if row["paired_exclusion_reason"]],
              "training_designs": diagnostics, "coefficients_fitted": all(row["full_column_rank"] for row in diagnostics.values()),
              "targets": {}, "agent_signal_enabled": False, "numpy_version": np.__version__}
    if not result["coefficients_fitted"]:
        result["no_fit_reason"] = "rank_deficient_training_design"
        return result
    for target in ("target_return", "target_absolute_return"):
        training = np.asarray([row[target] for row in train], dtype=float)
        actual = [row[target] for row in check]
        mean = float(np.mean(training))
        models, prediction_rows = {}, []
        for name, (x, z) in designs.items():
            coefficients, _, fitted_rank, _ = np.linalg.lstsq(x, training, rcond=1e-12)
            if fitted_rank != x.shape[1]:
                raise ValueError("OLS solver rejected the independently checked training rank")
            predictions = [float(value) for value in z @ coefficients]
            models[name] = {"coefficients": [float(value) for value in coefficients],
                            "metrics": error_metrics(actual, predictions, mean, target == "target_absolute_return")}
            for index, (row, a, predicted) in enumerate(zip(check, actual, predictions)):
                if name == "market_state":
                    prediction_rows.append({"trade_date": row["trade_date"], "actual": a})
                prediction_rows[index][name] = predicted
        result["targets"][target] = {"training_target_mean": mean, "models": models, "evaluation_predictions": prediction_rows}
    return result
