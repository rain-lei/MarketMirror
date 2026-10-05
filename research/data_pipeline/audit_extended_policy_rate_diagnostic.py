"""Independently rebuild raw rates, clocks, benchmark features and OLS results."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "research_outputs/extended_policy_rate_diagnostic_2019_2020_v2"
OUTPUT = ROOT / "research_outputs/extended_policy_rate_diagnostic_audit_2019_2020_v2.json"
CHINA = timezone(timedelta(hours=8))
FIELDS = ("instrument", "tenor_value", "tenor_unit", "gross_amount_100m_yuan", "rate_pct", "rate_kind", "market")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plain(markup: str) -> str:
    markup = re.sub(r"<(script|style)\b[^>]*>.*?</\1>|<!--.*?-->", "", markup, flags=re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", markup))).strip()


def region(raw: str, attribute: str, value: str) -> str:
    matches = list(re.finditer(r'<div\b[^>]*\b' + attribute + r'=["\x27]' + re.escape(value) + r'["\x27][^>]*>', raw, re.I))
    if len(matches) != 1:
        raise ValueError("independent rate source region is missing or ambiguous")
    suffix = re.sub(r"<(script|style)\b[^>]*>.*?</\1>|<!--.*?-->", "", raw[matches[0].end():], flags=re.S | re.I)
    depth = 1
    for match in re.finditer(r"</?div\b[^>]*>", suffix, re.I):
        depth += -1 if match[0].startswith("</") else 1
        if depth == 0:
            return suffix[:match.start()]
    raise ValueError("independent rate source region is unterminated")


def cnumber(value: str) -> int:
    mapping = dict(zip("〇零一二三四五六七八九", (0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9)))
    if "十" in value:
        left, right = value.split("十")
        return (mapping[left] if left else 1) * 10 + (mapping[right] if right else 0)
    return int("".join(str(mapping[char]) for char in value))


def independently_selected_ids(config: dict) -> set[str]:
    identities = set()
    for index in config["discovery_indices"]:
        saved = json.loads((ROOT / index["path"]).read_text(encoding="utf-8"))
        saved = saved.get("data", saved)
        for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", saved["rawHtml"], re.S | re.I):
            body = plain(row)
            titles = re.findall(r"公开市场业务交易公告 \[(\d{4})\]第(\d+)号", body)
            if not titles:
                continue
            days = re.findall(r"\d{4}-\d\d-\d\d", body)
            if len(titles) != 1 or len(days) != 1 or int(titles[0][0]) != date.fromisoformat(days[0]).year:
                raise ValueError("independent index table title/date coverage differs")
            if config["source_start_date"] <= days[0] <= config["source_end_date"]:
                identities.add(f"pbc_{titles[0][0]}_{titles[0][1]}")
    for item in config["lpr_sources"]:
        identities.add(item["source_id"])
    return identities


def source_facts(record: dict) -> dict:
    raw = (ROOT / record["source"]["archive_path"]).read_text(encoding="utf-8")
    titles = re.findall(r"<title\b[^>]*>(.*?)</title>", raw, re.S | re.I)
    wanted = record["title"] + ("_中国货币网" if record["kind"] == "lpr" else "")
    if len(titles) != 1 or plain(titles[0]) != wanted or "\ufffd" in raw:
        raise ValueError("independent source title/encoding differs")
    if record["kind"] == "lpr":
        heading = plain(region(raw, "class", "title-heading"))
        clocks = re.findall(r"\d{4}-\d\d-\d\d \d\d:\d\d(?![:\d])", plain(region(raw, "class", "article-a-toolbar")))
        if heading != record["title"] or len(clocks) != 1:
            raise ValueError("independent LPR heading or minute clock differs")
        stamp = datetime.strptime(clocks[0], "%Y-%m-%d %H:%M").replace(tzinfo=CHINA)
        body = plain(region(raw, "id", "ewebeditor_content"))
        compact = re.sub(r"\s+", "", body)
        one, five = re.findall(r"1年期LPR为(\d+(?:\.\d+)?)%", compact), re.findall(r"5年期以上LPR为(\d+(?:\.\d+)?)%", compact)
        signed = re.findall(r"(\d{4})年(\d+)月(\d+)日贷款市场报价利率", compact)
        if (len(one) != 1 or len(five) != 1 or len(signed) != 1
                or date(*(int(value) for value in signed[0])) != stamp.date()
                or "以上LPR在下一次发布LPR之前有效" not in compact):
            raise ValueError("independent LPR tenor, validity or signed date differs")
        timestamp, precision = stamp.isoformat(timespec="minutes"), "minute"
        available = (stamp + timedelta(seconds=59)).isoformat()
        absent, gross = None, None
        operations = [{"instrument": "lpr", "tenor_value": term, "tenor_unit": unit,
                       "gross_amount_100m_yuan": None, "rate_pct": float(Decimal(value)),
                       "rate_kind": "lending_benchmark", "market": "mainland"}
                      for term, unit, value in ((1, "年", one[0]), (5, "年以上", five[0]))]
    else:
        clocks = re.findall(r'<[^>]*\bid=["\x27]shijian["\x27][^>]*>([^<]*)', raw, re.I)
        if len(clocks) != 1:
            raise ValueError("independent OMO second clock differs")
        stamp = datetime.strptime(clocks[0].strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=CHINA)
        timestamp, available, precision = stamp.isoformat(), stamp.isoformat(), "second"
        markup = region(raw, "id", "zoom")
        body = plain(markup)
        signed = re.findall(r"([〇零一二三四五六七八九]{4})年([一二三四五六七八九十]+)月([一二三四五六七八九十]+)日", body)
        if len(signed) != 1 or date(*(cnumber(value) for value in signed[0])) != stamp.date() or "中国人民银行公开市场业务操作室" not in body:
            raise ValueError("independent OMO signed date/operator differs")
        if not re.fullmatch(r"公开市场业务交易公告 \[" + str(stamp.year) + r"\]第\d+号", record["title"]):
            raise ValueError("independent OMO bulletin year differs")
        compact = re.sub(r"\s+", "", body)
        absent = bool(re.search(r"(?:不开展|无)逆回购", compact))
        hk = "香港金融管理局" in compact and "央行票据" in compact
        tmlf = "TMLF操作情况" in compact or "定向中期借贷便利操作情况" in compact
        mlf = bool(re.search(r"(?<!T)MLF操作情况|(?<!定向)中期借贷便利操作情况", compact))
        repo, cbs = "逆回购操作情况" in compact, "央行票据互换" in compact or "CBS" in compact
        operations = []
        for line in re.findall(r"<tr\b[^>]*>(.*?)</tr>", markup, re.S | re.I):
            cells = [re.sub(r"\s+", "", plain(cell)) for cell in re.findall(r"<(?:td|th)\b[^>]*>(.*?)</(?:td|th)>" , line, re.S | re.I)]
            if hk and len(cells) == 4 and "央行票据（香港）" in cells[0]:
                amount, term, rate = cells[1:]
                instrument, kind, market = "hk_central_bank_bill", "bill_yield", "offshore_hk"
            elif len(cells) == 3:
                term, amount, rate = cells
                instrument = "cbs" if cbs else "tmlf" if tmlf and "年" in term else "mlf" if mlf and "年" in term else "reverse_repo" if repo else None
                kind, market = "fee" if cbs else "tender_interest", "mainland"
            else:
                continue
            term_match = re.fullmatch(r"(\d+)(天|年|个月)(?:[（(]\d+天[）)])?", term)
            amount_match, rate_match = re.fullmatch(r"(\d+(?:\.\d+)?)亿元", amount), re.fullmatch(r"(\d+(?:\.\d+)?)%", rate)
            if not (term_match and amount_match and rate_match):
                continue
            if instrument is None:
                raise ValueError("independent tender tool cannot be classified")
            operations.append({"instrument": instrument, "tenor_value": int(term_match[1]), "tenor_unit": term_match[2],
                               "gross_amount_100m_yuan": float(Decimal(amount_match[1])), "rate_pct": float(Decimal(rate_match[1])),
                               "rate_kind": kind, "market": market})
        if cbs and not operations:
            amount = re.findall(r"操作量为(\d+(?:\.\d+)?)亿元", compact)
            term = re.findall(r"期限为(\d+)(个月|年)", compact)
            fee = re.findall(r"费率为(\d+(?:\.\d+)?)%", compact)
            if len(amount) != 1 or len(term) != 1 or len(fee) != 1:
                raise ValueError("independent CBS fee evidence is missing")
            operations.append({"instrument": "cbs", "tenor_value": int(term[0][0]), "tenor_unit": term[0][1],
                               "gross_amount_100m_yuan": float(Decimal(amount[0])), "rate_pct": float(Decimal(fee[0])), "rate_kind": "fee", "market": "mainland"})
        gross = 0.0 if absent else sum(row["gross_amount_100m_yuan"] for row in operations if row["instrument"] == "reverse_repo") if repo else None
    if (record["publication_timestamp"] != timestamp or record["available_at"] != available or record["publication_precision"] != precision
            or record["signed_date"] != stamp.date().isoformat()
            or re.sub(r"\s+", "", record["body_text"]) != re.sub(r"\s+", "", body)
            or [{key: row[key] for key in FIELDS} for row in record["operations"]] != operations
            or record["explicit_no_reverse_repo"] != absent or record["gross_reverse_repo_amount_100m_yuan"] != gross
            or record["net_liquidity_100m_yuan"] is not None or record["policy_measures"] != [] or record["use_policy"]["agent_signal_enabled"] is not False):
        raise ValueError(f"independent raw facts, scope or missingness differ: {record['source_id']}")
    return {"source_id": record["source_id"], "timestamp": timestamp, "available": available, "signed_date": stamp.date().isoformat(),
            "source_sha256": record["source"]["sha256"], "title": record["title"], "operations": operations, "gross": gross}


def rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def near(actual, expected, tolerance=1e-10) -> None:
    if actual is None or expected is None:
        if actual is not expected:
            raise ValueError("independent null comparison differs")
    elif not math.isfinite(actual) or not math.isfinite(expected) or abs(actual - expected) > tolerance:
        raise ValueError(f"independent numeric comparison differs: {actual} / {expected}")


def matrix_rank(matrix: list[list[float]]) -> int:
    work = [[Fraction(str(value)) for value in row] for row in matrix]
    rank = 0
    for column in range(len(work[0])):
        candidates = [i for i in range(rank, len(work)) if work[i][column]]
        if not candidates:
            continue
        pivot = candidates[0]
        work[pivot], work[rank] = work[rank], work[pivot]
        for index in range(rank + 1, len(work)):
            if work[index][column]:
                ratio = work[index][column] / work[rank][column]
                work[index] = [left - ratio * right for left, right in zip(work[index], work[rank])]
        rank += 1
    return rank


def decimal_ols(matrix: list[list[float]], targets: list[float]) -> list[float]:
    """Independent 70-digit normal equations; no NumPy or production solver."""
    with localcontext() as context:
        context.prec = 70
        x = [[Decimal(str(value)) for value in row] for row in matrix]
        y = [Decimal(str(value)) for value in targets]
        width = len(x[0])
        system = [[sum(row[i] * row[j] for row in x) for j in range(width)]
                  + [sum(row[i] * value for row, value in zip(x, y))] for i in range(width)]
        for column in range(width):
            pivot = max(range(column, width), key=lambda i: abs(system[i][column]))
            if not system[pivot][column]:
                raise ValueError("independent OLS normal equations are singular")
            system[pivot], system[column] = system[column], system[pivot]
            divisor = system[column][column]
            system[column] = [value / divisor for value in system[column]]
            for index in range(width):
                if index != column:
                    multiplier = system[index][column]
                    system[index] = [left - multiplier * right for left, right in zip(system[index], system[column])]
        return [float(row[-1]) for row in system]


def compute() -> dict:
    manifest_path = SOURCE / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bindings = {str(manifest_path): digest(manifest_path), **manifest["inputs"], **manifest["code_sha256"]}
    for name, artifact in manifest["artifacts"].items():
        bindings[str(SOURCE / name)] = artifact["sha256"]
    if any(digest(Path(name)) != value for name, value in bindings.items()):
        raise ValueError("independent extended diagnostic input/producer/artifact changed")
    protocol = json.loads((ROOT / "research/configs/extended_policy_market_diagnostic_2019_2020.json").read_text(encoding="utf-8"))
    selection = json.loads((ROOT / "research/configs/extended_policy_rate_sources_2019_2020.json").read_text(encoding="utf-8"))
    records = json.loads((SOURCE / "policy_facts.json").read_text(encoding="utf-8"))["records"]
    calendar = json.loads((SOURCE / "rate_calendar.json").read_text(encoding="utf-8"))["calendar"]
    panel = json.loads((SOURCE / "diagnostic_panel.json").read_text(encoding="utf-8"))
    result = json.loads((SOURCE / "results.json").read_text(encoding="utf-8"))
    if (len(records) != 178 or len({row["source_id"] for row in records}) != 178
            or {row["source_id"] for row in records} != independently_selected_ids(selection)
            or Counter(row["kind"] for row in records) != {"omo": 170, "lpr": 8}):
        raise ValueError("independent extended source coverage differs")
    previous, sources = {}, []
    for record in records:
        fact = source_facts(record)
        for operation, saved in zip(fact["operations"], record["operations"]):
            key = operation["instrument"], operation["tenor_value"], operation["tenor_unit"]
            prior = previous.get(key)
            delta = float((Decimal(str(operation["rate_pct"])) - Decimal(str(prior["rate_pct"]))) * 100) if prior else None
            if saved["previous_observation"] != prior or saved["change_from_previous_observed_bps"] != delta:
                raise ValueError("independent same-tool/tenor rate predecessor differs")
            operation.update({"previous_observation": prior, "change_from_previous_observed_bps": delta})
            previous[key] = {"title": record["title"], "publication_timestamp": fact["timestamp"],
                             "source_sha256": fact["source_sha256"], "rate_pct": operation["rate_pct"]}
        sources.append(fact)
    if sources != sorted(sources, key=lambda row: (row["available"], row["source_id"])):
        raise ValueError("independent rate source ordering differs")
    benchmark_path = next(ROOT / name for name in protocol["market_inputs"] if name.endswith("/benchmark_prices.csv"))
    base = benchmark_path.parent
    days = [row["calendar_date"] for row in rows(base / "calendar_raw.csv") if row["is_trading_day"] == "1"]
    quotes = rows(base / "sh_000300_raw.csv")
    published = rows(benchmark_path)
    if ([row["date"] for row in quotes] != days or [row["trade_date"] for row in published] != days
            or len(days) != len(set(days)) or days != sorted(days)):
        raise ValueError("independent provider benchmark/open-date coverage differs")
    closes = [float(row["close"]) for row in quotes]
    if any(Decimal(raw["close"]) != Decimal(saved["close"]) for raw, saved in zip(quotes, published)):
        raise ValueError("independent benchmark price levels were changed")
    primary = json.loads((base / "baostock_download_manifest.json").read_text(encoding="utf-8"))
    provider_check = next(row for row in primary["quote_checks"] if row["symbol"] == protocol["benchmark_id"])
    tolerance_pct = provider_check["return_comparison_tolerance"] * 100
    same_differences = [abs((float(row["close"]) / float(row["preclose"]) - 1) * 100 - float(row["pctChg"])) for row in quotes]
    adjacent_differences = [0.0] + [abs((closes[index] / closes[index - 1] - 1) * 100 - float(quotes[index]["pctChg"])) for index in range(1, len(quotes))]
    level_differences = [{"trade_date": row["date"], "source_preclose": row["preclose"],
                          "previous_source_close": quotes[index - 1]["close"],
                          "difference_index_points": float(Decimal(row["preclose"]) - Decimal(quotes[index - 1]["close"])),
                          "adjacent_return_difference_percentage_points": adjacent_differences[index]}
                         for index, row in enumerate(quotes) if index and Decimal(row["preclose"]) != Decimal(quotes[index - 1]["close"])]
    quote_check = result["benchmark_quote_checks"]
    if (quote_check["provider_rows"] != len(quotes) or quote_check["price_levels_repaired"] is not False
            or quote_check["exact_preclose_level_differences"] != level_differences
            or max(same_differences + adjacent_differences) > tolerance_pct):
        raise ValueError("independent raw index quote discrepancy or repair status differs")
    near(quote_check["tolerance_percentage_points"], tolerance_pct)
    near(quote_check["maximum_same_day_difference_percentage_points"], max(same_differences))
    near(quote_check["maximum_adjacent_difference_percentage_points"], max(adjacent_differences))
    returns = [None] + [closes[i] / closes[i - 1] - 1 for i in range(1, len(days))]
    clocks = [{"trade_date": day, "signal_cutoff_date": days[index - 2], "execution_reference_date": days[index - 1]}
              for index, day in enumerate(days) if protocol["study_start_date"] <= day <= protocol["study_end_date"]]
    if len(calendar) != 139 or len(clocks) != len(calendar) or len(panel["policy_panel"]) != len(calendar) or len(panel["model_rows"]) != len(calendar):
        raise ValueError("independent complete diagnostic calendar coverage differs")
    channels = [(row["instrument"], row["tenor_value"], row["tenor_unit"]) for row in protocol["additional_policy_features"]]
    lookup = {key: index for index, key in enumerate(channels)}
    rebuilt, last_cutoff, latest_cells, pulses, unknown_cells = [], None, 0, [], 0
    for clock, stored, vector, model in zip(clocks, calendar, panel["policy_panel"], panel["model_rows"]):
        cutoff = clock["signal_cutoff_date"] + "T23:59:59+08:00"
        visible = [source for source in sources if source["available"] <= cutoff]
        fresh = [] if last_cutoff is None else [source for source in visible if source["available"] > last_cutoff]
        if (any(stored[key] != value for key, value in clock.items()) or stored["cutoff_timestamp"] != cutoff
                or stored["newly_visible_source_ids"] != [row["source_id"] for row in fresh]
                or stored["baseline_source_ids"] != ([row["source_id"] for row in visible] if last_cutoff is None else [])
                or stored["baseline_state_only"] != (last_cutoff is None) or stored["known_rrr_schedule"] != []
                or stored["newly_effective_known_rrr_measure_ids"] != [] or stored["agent_signal_enabled"] is not False):
            raise ValueError("independent policy visibility or inherited state differs")
        rates = {}
        for source in visible:
            for operation in source["operations"]:
                key = operation["instrument"] + ":" + str(operation["tenor_value"]) + operation["tenor_unit"]
                rates[key] = {field: operation[field] for field in ("rate_pct", "rate_kind", "market", "change_from_previous_observed_bps")}
                rates[key].update({"source_id": source["source_id"], "source_sha256": source["source_sha256"], "publication_timestamp": source["timestamp"], "available_at": source["available"]})
        if rates != stored["latest_observed_instrument_rates"]:
            raise ValueError("independent latest observed rate cells differ")
        latest_cells += len(rates)
        gross = [source["gross"] for source in visible if source["signed_date"] == clock["signal_cutoff_date"] and source["gross"] is not None]
        if stored["cutoff_date_gross_reverse_repo_100m_yuan"] != (sum(gross) if gross else None) or stored["cutoff_date_net_liquidity_100m_yuan"] is not None:
            raise ValueError("independent cutoff gross/net missingness differs")
        values, events, unknown, all_changes = [0.0] * len(channels), [], [], []
        for source in fresh:
            for operation in source["operations"]:
                delta = operation["change_from_previous_observed_bps"]
                if delta not in {None, 0.0}:
                    all_changes.append({key: operation[key] for key in ("instrument", "tenor_value", "tenor_unit", "market", "rate_kind", "rate_pct", "change_from_previous_observed_bps", "previous_observation")}
                                       | {"source_id": source["source_id"], "available_at": source["available"], "source_sha256": source["source_sha256"], "equity_direction": None, "response_coefficient": None, "agent_signal_enabled": False})
                key = operation["instrument"], operation["tenor_value"], operation["tenor_unit"]
                if operation["market"] != "mainland" or key not in lookup:
                    continue
                column = lookup[key]
                if delta is None:
                    values[column] = None
                    unknown.append({"source_id": source["source_id"], "channel": list(key)})
                elif values[column] is not None:
                    values[column] += delta
                if delta not in {None, 0.0}:
                    events.append({"source_id": source["source_id"], "channel": list(key), "change_bps": delta})
        if (stored["new_observed_rate_changes"] != all_changes or vector != {"trade_date": clock["trade_date"], "change_bps_by_channel": values,
                "observed_rate_changes": events, "unknown_predecessors": unknown}):
            raise ValueError("independent fresh policy pulses or unknown cells differ")
        unknown_cells += len(unknown)
        if events:
            pulses.append({"trade_date": clock["trade_date"], "events": events})
        index = days.index(clock["trade_date"])
        cutoff_index = index - 2
        history = returns[cutoff_index - 19:cutoff_index + 1]
        mean = sum(history) / 20
        features = [1.0, closes[cutoff_index] / closes[cutoff_index - 5] - 1,
                    math.sqrt(sum((value - mean) ** 2 for value in history) / 19), sum(abs(value) for value in history) / 20]
        exclusion = "unknown_new_policy_predecessor" if any(value is None for value in values) else None
        if len(model["baseline_features"]) != len(features):
            raise ValueError("independent benchmark feature shape differs")
        for actual, expected in zip(model["baseline_features"], features):
            near(actual, expected, 1e-14)
        near(model["target_return"], returns[index], 1e-14)
        near(model["target_absolute_return"], abs(returns[index]), 1e-14)
        if (any(model[key] != value for key, value in clock.items()) or model["policy_features"] != values or model["paired_exclusion_reason"] != exclusion):
            raise ValueError("independent benchmark feature time/split/missingness differs")
        rebuilt.append({**clock, "baseline": features, "policy": values, "return": returns[index], "absolute": abs(returns[index]), "excluded": exclusion})
        last_cutoff = cutoff
    train = [row for row in rebuilt if row["excluded"] is None and row["trade_date"] <= protocol["training_end_date"]]
    check = [row for row in rebuilt if row["excluded"] is None and row["trade_date"] >= protocol["evaluation_start_date"]]
    exclusions = [{"trade_date": row["trade_date"], "reason": row["excluded"]} for row in rebuilt if row["excluded"]]
    if len(train) != 95 or len(check) != 43 or result["training_rows"] != 95 or result["evaluation_rows"] != 43 or result["excluded_rows"] != exclusions or result["agent_signal_enabled"] is not False or result["coefficients_fitted"] is not True:
        raise ValueError("independent paired diagnostic split or fitting gate differs")
    if (result["baseline_feature_order"] != protocol["baseline_features"] or result["policy_feature_order"] != protocol["additional_policy_features"]
            or result["training_policy_dates"] != [row["trade_date"] for row in pulses if row["trade_date"] <= protocol["training_end_date"]]
            or result["evaluation_policy_dates"] != [row["trade_date"] for row in pulses if row["trade_date"] >= protocol["evaluation_start_date"]]
            or set(result["targets"]) != {"target_return", "target_absolute_return"}):
        raise ValueError("independent feature order, policy dates or target scope differs")
    max_coefficient_difference, predictions_checked = 0.0, 0
    for target, field in (("target_return", "return"), ("target_absolute_return", "absolute")):
        saved = result["targets"][target]
        if len(saved["evaluation_predictions"]) != len(check) or set(saved["models"]) != {"market_state", "market_state_plus_rates"}:
            raise ValueError("independent paired prediction/model coverage differs")
        y = [row[field] for row in train]
        actual = [row[field] for row in check]
        training_mean = sum(y) / len(y)
        near(saved["training_target_mean"], training_mean)
        for name, extra in (("market_state", False), ("market_state_plus_rates", True)):
            x = [row["baseline"] + (row["policy"] if extra else []) for row in train]
            z = [row["baseline"] + (row["policy"] if extra else []) for row in check]
            rank = matrix_rank(x)
            diagnostic = result["training_designs"][name]
            if rank != len(x[0]) or diagnostic["exact_fraction_rank"] != rank or diagnostic["numpy_rank"] != rank or diagnostic["full_column_rank"] is not True:
                raise ValueError("independent diagnostic training rank differs")
            coefficients = decimal_ols(x, y)
            if len(coefficients) != len(saved["models"][name]["coefficients"]):
                raise ValueError("independent OLS coefficient shape differs")
            for expected, observed in zip(coefficients, saved["models"][name]["coefficients"]):
                near(observed, expected, 1e-8)
                max_coefficient_difference = max(max_coefficient_difference, abs(observed - expected))
            predictions = [sum(left * right for left, right in zip(row, coefficients)) for row in z]
            for row, expected, observed in zip(check, predictions, saved["evaluation_predictions"]):
                if observed["trade_date"] != row["trade_date"]:
                    raise ValueError("independent OLS evaluation date differs")
                near(observed["actual"], row[field])
                near(observed[name], expected)
                predictions_checked += 1
            sse = sum((a - p) ** 2 for a, p in zip(actual, predictions))
            reference = sum((a - training_mean) ** 2 for a in actual)
            metrics = saved["models"][name]["metrics"]
            near(metrics["mae"], sum(abs(a - p) for a, p in zip(actual, predictions)) / len(check))
            near(metrics["rmse"], math.sqrt(sse / len(check)))
            near(metrics["r2_against_training_target_mean"], 1 - sse / reference)
            expected_direction = None if field == "absolute" else sum((a > 0) - (a < 0) == (p > 0) - (p < 0) for a, p in zip(actual, predictions)) / len(check)
            near(metrics["return_direction_accuracy"], expected_direction)
            expected_negative = sum(p < 0 for p in predictions) if field == "absolute" else None
            if metrics["rows"] != len(check) or metrics["absolute_return_predictions_below_zero"] != expected_negative:
                raise ValueError("independent prediction count or impossible absolute prediction differs")
    if any(digest(Path(name)) != value for name, value in bindings.items()):
        raise ValueError("independent diagnostic input changed during audit")
    censored = [{"source_id": row["source_id"], "available_at": row["available"], "channel": [op["instrument"], op["tenor_value"], op["tenor_unit"]],
                 "change_bps": op["change_from_previous_observed_bps"]} for row in sources for op in row["operations"]
                if row["available"] > last_cutoff and op["market"] == "mainland" and (op["instrument"], op["tenor_value"], op["tenor_unit"]) in lookup and op["change_from_previous_observed_bps"] not in {None, 0.0}]
    return {"pipeline_version": "independent-extended-rate-market-diagnostic-audit-v2", "raw_sources_checked": len(sources),
            "rate_observations_checked": sum(len(row["operations"]) for row in sources), "calendar_steps_checked": len(calendar),
            "latest_rate_cells_checked": latest_cells, "policy_steps": pulses, "unknown_policy_cells_retained": unknown_cells,
            "benchmark_model_rows_checked": len(rebuilt), "paired_training_rows": len(train), "paired_evaluation_rows": len(check),
            "ols_predictions_checked": predictions_checked, "maximum_independent_coefficient_difference": max_coefficient_difference,
            "right_censored_observed_rate_changes": censored, "benchmark_provider_rows_checked": len(quotes),
            "independent_ols_method": "70-digit Decimal normal equations, distinct from NumPy least squares", "agent_signal_enabled": False,
            "interpretation": "Raw original HTML regex and provider CSV reconstruction do not call production parsers, calendars, features or estimator. All rows are development diagnostics; no causal surprise interpretation or market-core adoption.",
            "inputs": dict(sorted(bindings.items())), "code_sha256": digest(Path(__file__))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute()
    raw = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if args.audit_existing:
        if args.output.read_bytes() != raw:
            raise ValueError("independent diagnostic audit differs from frozen output")
        print("Independent raw/clock/OLS audit rebuilt byte-identically.")
    else:
        with args.output.open("xb") as handle:
            handle.write(raw)
    print(json.dumps({key: value for key, value in result.items() if key not in {"inputs", "code_sha256", "policy_steps", "interpretation"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
