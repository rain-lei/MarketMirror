"""Independently check both the rank-failed and source-completed clock studies."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path

from . import audit_extended_policy_rate_diagnostic as reference_audit

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "research_outputs/policy_information_clock_completed_2019_2020_v1"
OUTPUT = ROOT / "research_outputs/policy_information_clock_completed_audit_2019_2020_v1.json"
CHINA = timezone(timedelta(hours=8))


def mixed_facts(record: dict) -> dict:
    """Read the two distinct heading/table sections with raw regex bounds."""
    raw = (ROOT / record["source"]["archive_path"]).read_text(encoding="utf-8")
    markup = reference_audit.region(raw, "id", "zoom")
    body = reference_audit.plain(markup)
    titles = re.findall(r"<title\b[^>]*>(.*?)</title>", raw, re.S | re.I)
    clocks = re.findall(r'<[^>]*\bid=["\x27]shijian["\x27][^>]*>([^<]*)', raw, re.I)
    if len(titles) != 1 or len(clocks) != 1 or reference_audit.plain(titles[0]) != record["title"]:
        raise ValueError("independent mixed-tool title or clock differs")
    stamp = datetime.strptime(clocks[0].strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=CHINA)
    signed = re.findall(r"([〇零一二三四五六七八九]{4})年([一二三四五六七八九十]+)月([一二三四五六七八九十]+)日", body)
    if (len(signed) != 1 or date(*(reference_audit.cnumber(value) for value in signed[0])) != stamp.date()
            or "中国人民银行公开市场业务操作室" not in body
            or not re.fullmatch(r"公开市场业务交易公告 \[" + str(stamp.year) + r"\]第\d+号", record["title"])):
        raise ValueError("independent mixed-tool signature or year differs")
    tables = list(re.finditer(r"<table\b[^>]*>(.*?)</table>", markup, re.S | re.I))
    if len(tables) != 2:
        raise ValueError("independent mixed-tool table coverage differs")
    operations, end = [], 0
    for table in tables:
        context = re.sub(r"\s+", "", reference_audit.plain(markup[end:table.start()]))
        label = re.search(r"(TMLF|(?<!T)MLF)操作情况$", context)
        if not label:
            raise ValueError("independent mixed-tool heading is not adjacent to its table")
        data = [[re.sub(r"\s+", "", reference_audit.plain(cell)) for cell in re.findall(r"<(?:td|th)\b[^>]*>(.*?)</(?:td|th)>", line, re.S | re.I)]
                for line in re.findall(r"<tr\b[^>]*>(.*?)</tr>", table[1], re.S | re.I)]
        if len(data) != 2 or data[0] != ["期限", "操作量", "操作利率"] or len(data[1]) != 3:
            raise ValueError("independent mixed-tool row/header coverage differs")
        tenor = re.fullmatch(r"(\d+)年(?:（可展期(\d+)次，实际期限为(\d+)年）)?", data[1][0])
        amount = re.fullmatch(r"(\d+(?:\.\d+)?)亿元", data[1][1])
        rate = re.fullmatch(r"(\d+(?:\.\d+)?)%", data[1][2])
        if not (tenor and amount and rate):
            raise ValueError("independent mixed-tool tender evidence differs")
        renewals, actual_years = (int(tenor[2]), int(tenor[3])) if tenor[2] else (None, None)
        if renewals is not None and (label[1] != "TMLF" or actual_years != int(tenor[1]) * (renewals + 1)):
            raise ValueError("independent TMLF extension schedule differs")
        operations.append({"instrument": "tmlf" if label[1] == "TMLF" else "mlf", "tenor_value": int(tenor[1]), "tenor_unit": "年",
                           "gross_amount_100m_yuan": float(Decimal(amount[1])), "rate_pct": float(Decimal(rate[1])), "rate_kind": "tender_interest", "market": "mainland"})
        saved = record["operations"][len(operations) - 1]
        if (saved["table_heading"] != label[1] or saved["renewal_count_stated"] != renewals or saved["actual_term_years_stated"] != actual_years
                or saved["tenor_qualifier_text"] != (data[1][0] if renewals is not None else None)):
            raise ValueError("independent TMLF qualifier fields differ")
        end = table.end()
    if set(row["instrument"] for row in operations) != {"mlf", "tmlf"}:
        raise ValueError("independent mixed-tool identity set differs")
    if (record["publication_timestamp"] != stamp.isoformat() or record["available_at"] != stamp.isoformat()
            or record["signed_date"] != stamp.date().isoformat() or record["publication_precision"] != "second"
            or re.sub(r"\s+", "", record["body_text"]) != re.sub(r"\s+", "", body)
            or [{key: row[key] for key in reference_audit.FIELDS} for row in record["operations"]] != operations
            or record["explicit_no_reverse_repo"] is not True or record["gross_reverse_repo_amount_100m_yuan"] != 0.0
            or "不开展逆回购" not in re.sub(r"\s+", "", body) or record["net_liquidity_100m_yuan"] is not None
            or record["policy_measures"] != [] or record["use_policy"]["agent_signal_enabled"] is not False):
        raise ValueError("independent mixed-tool raw facts or missingness differs")
    return {"source_id": record["source_id"], "timestamp": stamp.isoformat(), "available": stamp.isoformat(), "signed_date": stamp.date().isoformat(),
            "source_sha256": record["source"]["sha256"], "title": record["title"], "operations": operations, "gross": 0.0}


def compute(source: Path) -> dict:
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    completed = manifest["pipeline_version"] == "policy-information-clock-source-completion-contrast-v2"
    if not completed and manifest["pipeline_version"] != "policy-information-clock-crossed-development-contrast-v1":
        raise ValueError("independent clock audit does not recognize this protocol")
    config_path = ROOT / ("research/configs/policy_information_clock_completion_2019_2020.json" if completed else "research/configs/policy_information_clock_contrast_2019_2020.json")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    bindings = {str(manifest_path): reference_audit.digest(manifest_path), **manifest["inputs"], **manifest["code_sha256"]}
    for name, artifact in manifest["artifacts"].items():
        bindings[str(source / name)] = artifact["sha256"]
    if any(reference_audit.digest(Path(name)) != value for name, value in bindings.items()):
        raise ValueError("independent clock source, producer or artifact changed")
    parent = ROOT / config["source_directory"]
    records = json.loads(((source if completed else parent) / "policy_facts.json").read_text(encoding="utf-8"))["records"]
    panels = json.loads((source / "clock_panels.json").read_text(encoding="utf-8"))
    result = json.loads((source / "results.json").read_text(encoding="utf-8"))
    selection = json.loads((ROOT / "research/configs/extended_policy_rate_sources_2019_2020.json").read_text(encoding="utf-8"))
    selected = reference_audit.independently_selected_ids(selection)
    if completed:
        extra = json.loads((ROOT / "research/configs/policy_rate_predecessor_sources_2019.json").read_text(encoding="utf-8"))
        selected |= reference_audit.independently_selected_ids(extra)
    if len(records) != (212 if completed else 178) or len({row["source_id"] for row in records}) != len(records) or {row["source_id"] for row in records} != selected:
        raise ValueError("independent complete clock source selection differs")
    sources, previous = [], {}
    for record in records:
        fact = mixed_facts(record) if any("table_heading" in operation for operation in record["operations"]) else reference_audit.source_facts(record)
        for operation, saved in zip(fact["operations"], record["operations"]):
            key = operation["instrument"], operation["tenor_value"], operation["tenor_unit"]
            prior = previous.get(key)
            delta = float((Decimal(str(operation["rate_pct"])) - Decimal(str(prior["rate_pct"]))) * 100) if prior else None
            if saved["previous_observation"] != prior or saved["change_from_previous_observed_bps"] != delta:
                raise ValueError("independent completed rate predecessor differs")
            operation.update({"previous_observation": prior, "change_from_previous_observed_bps": delta})
            previous[key] = {"title": record["title"], "publication_timestamp": fact["timestamp"], "source_sha256": fact["source_sha256"], "rate_pct": operation["rate_pct"]}
        sources.append(fact)
    if sources != sorted(sources, key=lambda row: (row["available"], row["source_id"])):
        raise ValueError("independent completed source time order differs")
    if completed:
        old_records = json.loads((parent / "policy_facts.json").read_text(encoding="utf-8"))["records"]
        by_id = {row["source_id"]: row for row in records}
        amendments = []
        for before in old_records:
            after = by_id[before["source_id"]]
            if {key: value for key, value in before.items() if key != "operations"} != {key: value for key, value in after.items() if key != "operations"}:
                raise ValueError("independent source completion changed original article facts")
            for old, new in zip(before["operations"], after["operations"]):
                if {key: value for key, value in old.items() if key not in {"previous_observation", "change_from_previous_observed_bps"}} != {key: value for key, value in new.items() if key not in {"previous_observation", "change_from_previous_observed_bps"}}:
                    raise ValueError("independent source completion changed original tender facts")
                if old["previous_observation"] != new["previous_observation"] or old["change_from_previous_observed_bps"] != new["change_from_previous_observed_bps"]:
                    amendments.append({"source_id": before["source_id"], "instrument": new["instrument"], "tenor_value": new["tenor_value"], "tenor_unit": new["tenor_unit"],
                                       "old_previous_observation": old["previous_observation"], "completed_previous_observation": new["previous_observation"],
                                       "old_change_bps": old["change_from_previous_observed_bps"], "completed_change_bps": new["change_from_previous_observed_bps"]})
        if result["completed_predecessor_changes"] != amendments or result["original_source_facts_unchanged"] is not True:
            raise ValueError("independent predecessor amendment record differs")
    benchmark_path = next(ROOT / name for name in config["inputs"] if name.endswith("/benchmark_prices.csv"))
    raw_quotes = reference_audit.rows(benchmark_path.parent / "sh_000300_raw.csv")
    days = [row["calendar_date"] for row in reference_audit.rows(benchmark_path.parent / "calendar_raw.csv") if row["is_trading_day"] == "1"]
    saved_quotes = reference_audit.rows(benchmark_path)
    if [row["date"] for row in raw_quotes] != days or [row["trade_date"] for row in saved_quotes] != days or any(Decimal(raw["close"]) != Decimal(saved["close"]) for raw, saved in zip(raw_quotes, saved_quotes)):
        raise ValueError("independent clock benchmark price/calendar rows differ")
    closes = [float(row["close"]) for row in raw_quotes]
    returns = [None] + [closes[i] / closes[i - 1] - 1 for i in range(1, len(days))]
    protocol = json.loads((ROOT / "research/configs/extended_policy_market_diagnostic_2019_2020.json").read_text(encoding="utf-8"))
    channels = [(row["instrument"], row["tenor_value"], row["tenor_unit"]) for row in protocol["additional_policy_features"]]
    lookup = {key: i for i, key in enumerate(channels)}
    rebuilding, counts, expected_mapping = {}, Counter(), []
    names = [row["name"] for row in config["variants"]]
    if any(set(panels[key]) != set(names) for key in ("calendars", "policy_vectors", "model_rows")) or set(result["variants"]) != set(names):
        raise ValueError("independent clock contrast omits a prespecified variant")
    for variant in config["variants"]:
        name, market_lag, policy_lag = variant["name"], variant["market_lag_sessions"], variant["policy_lag_sessions"]
        clocks = [{"trade_date": day, "signal_cutoff_date": days[i - policy_lag], "execution_reference_date": days[i - 1]}
                  for i, day in enumerate(days) if config["study_start_date"] <= day <= config["study_end_date"]]
        if any(len(panels[key][name]) != len(clocks) for key in ("calendars", "policy_vectors", "model_rows")) or len(clocks) != 139:
            raise ValueError("independent clock contrast full date coverage differs")
        last_cutoff, model_rows = None, []
        for clock, saved, vector, model in zip(clocks, panels["calendars"][name], panels["policy_vectors"][name], panels["model_rows"][name]):
            cutoff = clock["signal_cutoff_date"] + "T23:59:59+08:00"
            visible = [row for row in sources if row["available"] <= cutoff]
            fresh = [] if last_cutoff is None else [row for row in visible if row["available"] > last_cutoff]
            if (any(saved[key] != value for key, value in clock.items()) or saved["cutoff_timestamp"] != cutoff
                    or saved["baseline_state_only"] != (last_cutoff is None)
                    or saved["baseline_source_ids"] != ([row["source_id"] for row in visible] if last_cutoff is None else [])
                    or saved["newly_visible_source_ids"] != [row["source_id"] for row in fresh]
                    or saved["known_rrr_schedule"] != [] or saved["newly_effective_known_rrr_measure_ids"] != []
                    or saved["agent_signal_enabled"] is not False):
                raise ValueError("independent policy information cutoff or inherited state differs")
            rates, changes = {}, []
            for row in visible:
                for operation in row["operations"]:
                    key = operation["instrument"] + ":" + str(operation["tenor_value"]) + operation["tenor_unit"]
                    rates[key] = {key: operation[key] for key in ("rate_pct", "rate_kind", "market", "change_from_previous_observed_bps")}
                    rates[key].update({"source_id": row["source_id"], "source_sha256": row["source_sha256"], "publication_timestamp": row["timestamp"], "available_at": row["available"]})
            if saved["latest_observed_instrument_rates"] != rates:
                raise ValueError("independent policy latest-rate state differs")
            gross = [row["gross"] for row in visible if row["signed_date"] == clock["signal_cutoff_date"] and row["gross"] is not None]
            if saved["cutoff_date_gross_reverse_repo_100m_yuan"] != (sum(gross) if gross else None) or saved["cutoff_date_net_liquidity_100m_yuan"] is not None:
                raise ValueError("independent policy gross/net distinction differs")
            values, events, unknown = [0.0] * len(channels), [], []
            for row in fresh:
                for operation in row["operations"]:
                    delta = operation["change_from_previous_observed_bps"]
                    if delta not in {None, 0.0}:
                        changes.append({key: operation[key] for key in ("instrument", "tenor_value", "tenor_unit", "market", "rate_kind", "rate_pct", "change_from_previous_observed_bps", "previous_observation")}
                                       | {"source_id": row["source_id"], "available_at": row["available"], "source_sha256": row["source_sha256"], "equity_direction": None, "response_coefficient": None, "agent_signal_enabled": False})
                    key = operation["instrument"], operation["tenor_value"], operation["tenor_unit"]
                    if operation["market"] != "mainland" or key not in lookup:
                        continue
                    i = lookup[key]
                    if delta is None:
                        values[i] = None;unknown.append({"source_id": row["source_id"], "channel": list(key)})
                    elif values[i] is not None:
                        values[i] += delta
                    if delta not in {None, 0.0}:
                        events.append({"source_id": row["source_id"], "channel": list(key), "change_bps": delta})
                        expected_mapping.append({"source_id": row["source_id"], "channel": list(key), "change_bps": delta, "trade_date": clock["trade_date"], "variant": name})
            if saved["new_observed_rate_changes"] != changes or vector != {"trade_date": clock["trade_date"], "change_bps_by_channel": values, "observed_rate_changes": events, "unknown_predecessors": unknown}:
                raise ValueError("independent fresh-rate pulses or missing values differ")
            index = days.index(clock["trade_date"])
            market_index = index - market_lag
            history = returns[market_index - 19:market_index + 1]
            mean = sum(history) / 20
            features = [1.0, closes[market_index] / closes[market_index - 5] - 1,
                        math.sqrt(sum((value - mean) ** 2 for value in history) / 19), sum(abs(value) for value in history) / 20]
            if len(model["baseline_features"]) != 4:
                raise ValueError("independent clock baseline shape differs")
            for actual, expected in zip(model["baseline_features"], features):
                reference_audit.near(actual, expected, 1e-14)
            reference_audit.near(model["target_return"], returns[index], 1e-14)
            reference_audit.near(model["target_absolute_return"], abs(returns[index]), 1e-14)
            exclusion = "unknown_new_policy_predecessor" if unknown else None
            if (any(model[key] != value for key, value in clock.items()) or model["market_feature_cutoff_date"] != days[market_index]
                    or model["market_feature_cutoff_timestamp"] != days[market_index] + "T23:59:59+08:00"
                    or model["policy_cutoff_timestamp"] != cutoff or model["policy_features"] != values or model["intrinsic_exclusion_reason"] != exclusion):
                raise ValueError("independent separated market/policy clock differs")
            model_rows.append({"trade_date": clock["trade_date"], "baseline": features, "policy": values,
                               "return": returns[index], "absolute": abs(returns[index]), "intrinsic_exclusion": exclusion, "events": events})
            counts["clock_steps"] += 1;counts["latest_rate_cells"] += len(rates)
            last_cutoff = cutoff
        rebuilding[name] = model_rows
    exclusions = []
    for i, row in enumerate(rebuilding[names[0]]):
        reasons = {name: rebuilding[name][i]["intrinsic_exclusion"] for name in names if rebuilding[name][i]["intrinsic_exclusion"]}
        if reasons:
            exclusions.append({"trade_date": row["trade_date"], "unavailable_variants": reasons})
        for name in names:
            rebuilding[name][i]["paired_exclusion"] = "shared_clock_pairing_exclusion" if reasons else None
            if panels["model_rows"][name][i]["paired_exclusion_reason"] != rebuilding[name][i]["paired_exclusion"]:
                raise ValueError("independent common date exclusions differ")
    if result["shared_exclusions"] != exclusions or result["policy_step_mapping"] != expected_mapping or result["agent_signal_enabled"] is not False:
        raise ValueError("independent exclusion/policy mapping or adoption gate differs")
    max_difference, independent_ranks, fitted_count = 0.0, {}, 0
    for name, rebuilt in rebuilding.items():
        fit = result["variants"][name]
        train = [row for row in rebuilt if row["paired_exclusion"] is None and row["trade_date"] <= config["training_end_date"]]
        check = [row for row in rebuilt if row["paired_exclusion"] is None and row["trade_date"] >= config["evaluation_start_date"]]
        if len(train) != (96 if completed else 94) or len(check) != 43 or fit["training_rows"] != len(train) or fit["evaluation_rows"] != len(check):
            raise ValueError("independent common training/check split differs")
        expected_fit_excluded = [{"trade_date": row["trade_date"], "reason": row["paired_exclusion"]} for row in rebuilt if row["paired_exclusion"]]
        if fit["excluded_rows"] != expected_fit_excluded:
            raise ValueError("independent fit exclusion record differs")
        designs, ranks = {}, {}
        for model, extra in (("market_state", False), ("market_state_plus_rates", True)):
            matrix = [row["baseline"] + (row["policy"] if extra else []) for row in train]
            rank = reference_audit.matrix_rank(matrix)
            ranks[model], designs[model] = rank, matrix
            observed = fit["training_designs"][model]
            if (observed["columns"] != len(matrix[0]) or observed["training_rows"] != len(train)
                    or observed["exact_fraction_rank"] != rank or observed["numpy_rank"] != rank
                    or observed["full_column_rank"] != (rank == len(matrix[0]))
                    or rank != len(matrix[0]) and observed["condition_number"] is not None):
                raise ValueError("independent clock exact rank/no-fit state differs")
        identifiable = all(ranks[model] == len(matrix[0]) for model, matrix in designs.items())
        independent_ranks[name] = ranks
        if fit["coefficients_fitted"] != identifiable or fit["agent_signal_enabled"] is not False:
            raise ValueError("independent per-variant fitting/adoption gate differs")
        for key, predicate in (("training_policy_dates", lambda row: row["trade_date"] <= config["training_end_date"]),
                               ("evaluation_policy_dates", lambda row: row["trade_date"] >= config["evaluation_start_date"])):
            if fit[key] != [row["trade_date"] for row in rebuilt if row["events"] and predicate(row)]:
                raise ValueError("independent per-variant policy-date counts differ")
        if not identifiable:
            if fit["targets"] != {} or fit["no_fit_reason"] != "rank_deficient_training_design":
                raise ValueError("independent rank-failed variant was fitted")
            continue
        fitted_count += 1
        for target, field in (("target_return", "return"), ("target_absolute_return", "absolute")):
            saved = fit["targets"][target]
            training = [row[field] for row in train]
            actual = [row[field] for row in check]
            target_mean = sum(training) / len(training)
            reference_audit.near(saved["training_target_mean"], target_mean)
            if len(saved["evaluation_predictions"]) != len(check):
                raise ValueError("independent clock prediction scope differs")
            for model, matrix in designs.items():
                coefficients = reference_audit.decimal_ols(matrix, training)
                stored_coefficients = saved["models"][model]["coefficients"]
                if len(stored_coefficients) != len(coefficients):
                    raise ValueError("independent clock coefficient count differs")
                for a, b in zip(stored_coefficients, coefficients):
                    reference_audit.near(a, b, 1e-8);max_difference = max(max_difference, abs(a - b))
                check_design = [row["baseline"] + (row["policy"] if model == "market_state_plus_rates" else []) for row in check]
                predictions = [sum(a * b for a, b in zip(row, coefficients)) for row in check_design]
                for row, a, predicted, saved_row in zip(check, actual, predictions, saved["evaluation_predictions"]):
                    if row["trade_date"] != saved_row["trade_date"]:
                        raise ValueError("independent clock prediction date differs")
                    reference_audit.near(saved_row["actual"], a)
                    reference_audit.near(saved_row[model], predicted)
                    counts["ols_predictions"] += 1
                sse = sum((a - p) ** 2 for a, p in zip(actual, predictions))
                ref = sum((a - target_mean) ** 2 for a in actual)
                metrics = saved["models"][model]["metrics"]
                reference_audit.near(metrics["mae"], sum(abs(a - p) for a, p in zip(actual, predictions)) / len(actual))
                reference_audit.near(metrics["rmse"], math.sqrt(sse / len(actual)))
                reference_audit.near(metrics["r2_against_training_target_mean"], 1 - sse / ref)
                direction = None if field == "absolute" else sum((a > 0) - (a < 0) == (p > 0) - (p < 0) for a, p in zip(actual, predictions)) / len(actual)
                reference_audit.near(metrics["return_direction_accuracy"], direction)
                if metrics["rows"] != len(actual) or metrics["absolute_return_predictions_below_zero"] != (sum(p < 0 for p in predictions) if field == "absolute" else None):
                    raise ValueError("independent clock metric count or negative forecast differs")
    if result["all_variants_full_rank"] != (fitted_count == len(names)):
        raise ValueError("independent whole-contrast rank gate differs")
    if fitted_count == len(names):
        expected_contrasts = []
        pairs = [("policy", "market2_policy2", "market2_policy1"), ("policy", "market1_policy2", "market1_policy1"),
                 ("market", "market2_policy2", "market1_policy2"), ("market", "market2_policy1", "market1_policy1")]
        for factor, old, new in pairs:
            for target in ("target_return", "target_absolute_return"):
                for model in ("market_state", "market_state_plus_rates"):
                    a = result["variants"][old]["targets"][target]["models"][model]["metrics"]
                    b = result["variants"][new]["targets"][target]["models"][model]["metrics"]
                    expected_contrasts.append({"updated_factor": factor, "two_session_variant": old, "one_session_variant": new,
                                               "target": target, "model": model, "evaluation_rows": a["rows"],
                                               "mae_relative_change_percent": (b["mae"] / a["mae"] - 1) * 100,
                                               "rmse_relative_change_percent": (b["rmse"] / a["rmse"] - 1) * 100,
                                               "direction_accuracy_difference": b["return_direction_accuracy"] - a["return_direction_accuracy"] if a["return_direction_accuracy"] is not None else None})
        if result["contrasts"] != expected_contrasts:
            raise ValueError("independent timing contrasts differ")
        for market_lag in (1, 2):
            for target in ("target_return", "target_absolute_return"):
                a = result["variants"][f"market{market_lag}_policy1"]["targets"][target]["models"]["market_state"]
                b = result["variants"][f"market{market_lag}_policy2"]["targets"][target]["models"]["market_state"]
                if a != b:
                    raise ValueError("independent policy-only contrast changed its baseline")
    elif result["contrasts"] != []:
        raise ValueError("rank-failed clock comparison emitted global timing contrasts")
    if any(reference_audit.digest(Path(name)) != value for name, value in bindings.items()):
        raise ValueError("independent clock inputs changed during audit")
    return {"pipeline_version": "independent-policy-information-clock-audit-v1", "study_stage": "source_completed" if completed else "initial_rank_failed",
            "raw_sources_checked": len(sources), "raw_rate_observations_checked": sum(len(row["operations"]) for row in sources),
            "clock_steps_checked": counts["clock_steps"], "latest_rate_cells_checked": counts["latest_rate_cells"], "ols_predictions_checked": counts["ols_predictions"],
            "independently_rebuilt_ranks": independent_ranks, "shared_exclusions": exclusions, "maximum_independent_coefficient_difference": max_difference,
            "agent_signal_enabled": False, "inputs": dict(sorted(bindings.items())),
            "code_sha256": {str(Path(__file__).resolve()): reference_audit.digest(Path(__file__)),
                            str(Path(reference_audit.__file__).resolve()): reference_audit.digest(Path(reference_audit.__file__))},
            "interpretation": "Raw regex section parsing and provider CSV reconstruction, exact Fraction rank and 70-digit Decimal OLS; no production parser, clock, feature or fit functions called. Completed source changes and initial rank failure are both retained, with identical target-date pairing per stage."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute(args.source_dir.resolve())
    raw = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if args.audit_existing:
        if args.output.read_bytes() != raw:
            raise ValueError("clock audit differs from frozen result")
        print("Independent information-clock audit rebuilt byte-identically.")
    else:
        with args.output.open("xb") as handle:
            handle.write(raw)
    print(json.dumps({key: value for key, value in result.items() if key not in {"inputs", "code_sha256", "interpretation"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
