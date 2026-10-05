"""Compare cached Eastmoney fqt=0 observations with pinned BaoStock event inputs."""

from __future__ import annotations

import argparse
import csv
import json
import math
import tempfile
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from ..baselines.event_study import DailyObservation, event_study
from ..registry.verify_catalog import audit_run, load_catalog
from .market_data import strict_date
from .provenance import file_sha256

VERSION = "independent-quote-check-v1"
ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "research/configs/integrity_catalog_2020.json"
CONFIG = ROOT / "research/configs/independent_quote_check_2018_2020.json"
OUTPUTS = ROOT / "research_outputs"
SNAPSHOT = OUTPUTS / "independent_eastmoney_2018_2020"
CODES = ("000001", "000002", "600519", "000300")


def finite_number(value: str | int | float, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def parse_eastmoney_capture(path: Path, expected_code: str, start: date,
                            end: date) -> dict[str, float]:
    """Accept only the exact fenced JSON shape saved by this capture method."""
    text = path.read_text(encoding="utf-8")
    prefix, suffix = "```json\n", "\n```"
    if not text.startswith(prefix) or not text.endswith(suffix):
        raise ValueError("Eastmoney capture is not a single fenced JSON response")
    response = json.loads(text[len(prefix):-len(suffix)])
    market = 1 if expected_code in {"600519", "000300"} else 0
    if (response.get("rc") != 0 or not isinstance(response.get("data"), dict)
            or response["data"].get("code") != expected_code
            or response["data"].get("market") != market):
        raise ValueError("Eastmoney response identity or status differs from requested symbol")
    klines = response["data"].get("klines")
    if not isinstance(klines, list) or len(klines) < 130:
        raise ValueError("Eastmoney response lacks the required historical sessions")
    returns = {}
    previous = None
    for line in klines:
        if not isinstance(line, str):
            raise ValueError("Eastmoney K-line must be a string")
        fields = line.split(",")
        if len(fields) != 11:
            raise ValueError("Eastmoney K-line field count changed")
        day = strict_date(fields[0])
        if not start <= day <= end or (previous is not None and day <= previous):
            raise ValueError("Eastmoney dates are outside request or not strictly ordered")
        previous = day
        for index in (1, 2, 3, 4):
            if finite_number(fields[index], "price") <= 0:
                raise ValueError("Eastmoney price must be positive")
        for index in (5, 6):
            if finite_number(fields[index], "turnover") < 0:
                raise ValueError("Eastmoney turnover cannot be negative")
        percent = finite_number(fields[8], "daily percent change")
        if percent < -100 or percent > 100:
            raise ValueError("Eastmoney daily percentage is outside a broad sanity range")
        returns[day.isoformat()] = percent
    return returns


def read_baostock_raw(path: Path, expected_symbol: str, start: date,
                      end: date) -> dict[str, float]:
    values = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not {"date", "code", "pctChg"} <= set(reader.fieldnames or []):
            raise ValueError("BaoStock raw fields changed")
        for row in reader:
            day = strict_date(row["date"])
            if not start <= day <= end:
                continue
            if row["code"] != expected_symbol or day.isoformat() in values:
                raise ValueError("BaoStock symbol/date identity changed")
            if expected_symbol != "sh.000300" and row.get("adjustflag") != "1":
                raise ValueError("BaoStock stock adjustment flag differs from original basis")
            values[day.isoformat()] = finite_number(row["pctChg"], "BaoStock pctChg")
    return values


def compare_series(east: dict[str, float], bao: dict[str, float],
                   tolerance_pp: float, material_pp: float) -> dict[str, Any]:
    if not east or set(east) != set(bao):
        raise ValueError("independent and baseline session calendars differ")
    differences = {day: abs(east[day] - bao[day]) for day in sorted(east)}
    return {"sessions": len(differences), "first_date": min(differences),
            "last_date": max(differences),
            "max_absolute_difference_pp": max(differences.values()),
            "beyond_rounding_days": sum(value > tolerance_pp for value in differences.values()),
            "material_differences": [{"trade_date": day, "eastmoney_pct": east[day],
                                       "baostock_pct": bao[day], "absolute_difference_pp": value}
                                      for day, value in differences.items() if value > material_pp]}


def source_symbol(code: str) -> str:
    return ("sh." if code in {"600519", "000300"} else "sz.") + code


def raw_baseline(year: str, code: str, inputs: dict[Path, str]) -> tuple[dict[str, Any], Path]:
    directory = OUTPUTS / f"observed_{year}/download"
    manifest_path = directory / "download_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_path = directory / (source_symbol(code).replace(".", "_") + "_raw.csv")
    if (manifest.get("provider") != "BaoStock"
            or file_sha256(raw_path) != manifest["artifacts"][raw_path.name]["sha256"]
            or not any(query.get("symbol") == source_symbol(code)
                       and query.get("adjustflag") == ("3" if code == "000300" else "1")
                       and query.get("raw_file") == raw_path.name for query in manifest["queries"])):
        raise ValueError("BaoStock raw response differs from its download manifest")
    inputs[manifest_path] = file_sha256(manifest_path)
    inputs[raw_path] = file_sha256(raw_path)
    return manifest, raw_path


def run_check(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty independent quote check output directory")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if (config.get("run_id") != "independent_quote_check_2018_2020"
            or config.get("request_parameters", {}).get("fqt") != "0"
            or config.get("request_parameters", {}).get("klt") != "101"
            or {p["year"] for p in config["periods"]} != {"2018", "2020"}
            or any(set(p["captures"]) != set(CODES) for p in config["periods"])):
        raise ValueError("independent quote check requires the fixed periods and unadjusted daily grid")
    strict_date(config["capture_date"])
    tolerance = finite_number(config["rounding_tolerance_percentage_points"], "rounding tolerance")
    material = finite_number(config["material_difference_percentage_points"], "material threshold")
    if not 0 < tolerance < material:
        raise ValueError("invalid quote comparison thresholds")
    documentation = SNAPSHOT / "source_documentation.md"
    if file_sha256(documentation) != config["documentation_sha256"]:
        raise ValueError("quote field documentation cache changed")
    pinned = {run["run_id"]: run for run in load_catalog(CATALOG)}
    inputs = {config_path: file_sha256(config_path), CATALOG: file_sha256(CATALOG),
              documentation: file_sha256(documentation)}
    outputs = []
    requests = []
    for period in config["periods"]:
        year = period["year"]
        start, end = strict_date(period["start_date"]), strict_date(period["end_date"])
        if start >= end:
            raise ValueError("independent quote period is reversed")
        run_id = "observed_event_2018" if year == "2018" else "observed_event"
        run = pinned[run_id]
        if audit_run(run, CATALOG.parent, ROOT / "research", OUTPUTS, {})["status"] != "passed":
            raise ValueError("original event run failed pinned source audit")
        event_manifest_path = (CATALOG.parent / run["manifest"]).resolve()
        event_results_path = event_manifest_path.parent / "event_results.json"
        event_results = json.loads(event_results_path.read_text(encoding="utf-8"))
        inputs[event_manifest_path] = file_sha256(event_manifest_path)
        inputs[event_results_path] = file_sha256(event_results_path)
        independent, baseline, comparisons = {}, {}, {}
        for code in CODES:
            capture = SNAPSHOT / "raw" / f"eastmoney-{year}-{code}.md"
            digest = period["captures"][code]
            if file_sha256(capture) != digest:
                raise ValueError("cached independent quote response differs from config pin")
            inputs[capture] = digest
            independent[code] = parse_eastmoney_capture(capture, code, start, end)
            _, baseline_path = raw_baseline(year, code, inputs)
            baseline[code] = read_baostock_raw(baseline_path, source_symbol(code), start, end)
            comparisons[code] = compare_series(independent[code], baseline[code], tolerance, material)
            params = {**config["request_parameters"], "secid": ("1." if code in {"600519", "000300"}
                                                            else "0.") + code,
                      "beg": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d")}
            requests.append({"year": year, "code": code,
                             "url": config["provider_endpoint"] + "?" + urlencode(params),
                             "capture_sha256": digest})
        calendar = set(independent["000300"])
        if any(set(independent[code]) != calendar for code in CODES):
            raise ValueError("independent symbols have different session calendars")
        events = []
        for original in event_results["results"]:
            code = original["stock_code"]
            if code not in CODES or code == "000300":
                raise ValueError("event result has an unexpected stock")
            observations = [DailyObservation(date.fromisoformat(day), independent[code][day] / 100,
                                             independent["000300"][day] / 100)
                            for day in sorted(calendar)]
            computed = event_study(observations, original["event_date_requested"],
                                   original["estimation_observations"], original["pre_event_gap"],
                                   original["window_before"], original["window_after"])
            event_dates = [item["trade_date"] for item in original["abnormal_returns"]]
            if (computed["event_date_used"] != original["event_date_used"]
                    or computed["estimation_start"] != original["estimation_start"]
                    or computed["estimation_end"] != original["estimation_end"]
                    or [item["trade_date"] for item in computed["abnormal_returns"]] != event_dates):
                raise ValueError("independent event alignment differs from baseline")
            window_diffs = [abs(independent[code][day] - baseline[code][day]) for day in event_dates]
            estimate_material = [row["trade_date"] for row in comparisons[code]["material_differences"]
                                 if original["estimation_start"] <= row["trade_date"] <= original["estimation_end"]]
            events.append({"event_id": original["event_id"], "stock_code": code,
                           "event_date_used": original["event_date_used"],
                           "event_sessions": len(event_dates),
                           "event_window_max_absolute_difference_pp": max(window_diffs),
                           "event_window_beyond_rounding_days": sum(d > tolerance for d in window_diffs),
                           "estimation_material_difference_dates": estimate_material,
                           "baostock_adjusted_car": original["cumulative_abnormal_return"],
                           "eastmoney_unadjusted_car": computed["cumulative_abnormal_return"],
                           "car_difference_pp": 100 * (computed["cumulative_abnormal_return"]
                                                       - original["cumulative_abnormal_return"])})
        outputs.append({"year": year, "start_date": period["start_date"],
                        "end_date": period["end_date"], "quotes": comparisons, "events": events})
    if any(file_sha256(path) != digest for path, digest in inputs.items()):
        raise RuntimeError("quote source changed during comparison")
    result = {"pipeline_version": VERSION, "run_id": config["run_id"],
              "capture_date": config["capture_date"],
              "data_kind": "cross_provider_current_vintage_diagnostic",
              "comparison_basis": "Eastmoney fqt=0 unadjusted daily percent versus BaoStock adjustflag=1 pctChg; CAR differences mix rounding and return-basis effects",
              "rounding_tolerance_percentage_points": tolerance,
              "material_difference_percentage_points": material,
              "periods": outputs}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        staging = Path(tmp)
        (staging / "independent_quote_results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (staging / "independent_quote_report.md").write_text(render_report(result), encoding="utf-8")
        code = Path(__file__)
        manifest = {"pipeline_version": VERSION, "config_sha256": file_sha256(config_path),
                    "inputs": {str(path): digest for path, digest in sorted(inputs.items())},
                    "code_sha256": {code.name: file_sha256(code),
                                    "event_study.py": file_sha256(ROOT / "research/baselines/event_study.py")},
                    "requests": requests,
                    "artifacts": {path.name: {"sha256": file_sha256(path)} for path in staging.iterdir()},
                    "limitations": ["Firecrawl captured the API body as fenced JSON; not raw HTTP wire bytes.",
                                    "The two providers' adjustment bases differ; CAR is a basis-sensitivity comparison, not a same-definition replication.",
                                    "Historical data are current-vintage responses, not archived as-of-market-date feeds."]}
        (staging / "independent_quote_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return result


def render_report(result: dict[str, Any]) -> str:
    tolerance = result["rounding_tolerance_percentage_points"]
    lines = ["# 独立行情源逐日核对：2018 与 2020 固定事件", "",
             f"使用 {result['capture_date']} 缓存的东方财富日 K 线接口 `fqt=0` 不复权涨跌幅，与固定 BaoStock `adjustflag=1` 的 `pctChg` 对齐。"
             "东方财富响应由 Firecrawl 保存为 JSON 代码块，不是 HTTP 原始字节；AKShare 官方文档和实现源码说明该字段为百分数且 `fqt=0` 对应不复权。", "",
             f"描述性舍入容差为 {tolerance:.4f} 个百分点；超过 0.02 个百分点另列为较大口径差异候选。"
             "容差不是统计显著性阈值，两种复权口径的 CAR 不能视为同定义的独立复现。", "",
             "| 时期 | 序列 | 对齐日数 | 最大逐日差（百分点） | 超舍入容差日数 | 超 0.02 点日数 |",
             "|---|---|---:|---:|---:|---:|"]
    for period in result["periods"]:
        for code, quote in period["quotes"].items():
            lines.append(f"| {period['year']} | {code} | {quote['sessions']} | "
                         f"{quote['max_absolute_difference_pp']:.6f} | "
                         f"{quote['beyond_rounding_days']} | {len(quote['material_differences'])} |")
    lines += ["", "## 大差异日", "",
              "这些日期说明不复权与复权收益口径可能分离；本次未独立核对公司行为记录，不把差异直接归因为某项分红或拆分。", "",
              "| 时期 | 序列 | 日期 | 东方财富涨跌幅 | BaoStock 涨跌幅 | 绝对差（百分点） |",
              "|---|---|---|---:|---:|---:|"]
    for period in result["periods"]:
        for code, quote in period["quotes"].items():
            for row in quote["material_differences"]:
                lines.append(f"| {period['year']} | {code} | {row['trade_date']} | "
                             f"{row['eastmoney_pct']:+.4f}% | {row['baostock_pct']:+.6f}% | "
                             f"{row['absolute_difference_pp']:.6f} |")
    lines += ["", "## 固定事件窗口与 CAR 口径敏感性", "",
              "沿用原事件研究的 120 日估计、5 日间隔和 [-3,+5] 窗口，使用第二来源不复权日涨跌幅和同源沪深 300 重算。"
              "事件日、估计区间和窗口日逐一与原运行核对；下表 CAR 差混合逐日舍入与收益口径差异，不应理解为已验证同口径历史复现。", "",
              "| 时期 | 事件 | 股票 | 事件窗口最大逐日差（百分点） | 估计期大差异日 | BaoStock CAR | 东方财富不复权 CAR | 差（百分点） |",
              "|---|---|---|---:|---|---:|---:|---:|"]
    for period in result["periods"]:
        for row in period["events"]:
            exception = ", ".join(row["estimation_material_difference_dates"]) or "无"
            lines.append(f"| {period['year']} | {row['event_id']} | {row['stock_code']} | "
                         f"{row['event_window_max_absolute_difference_pp']:.6f} | {exception} | "
                         f"{100 * row['baostock_adjusted_car']:+.4f}% | "
                         f"{100 * row['eastmoney_unadjusted_car']:+.4f}% | "
                         f"{row['car_difference_pp']:+.4f} |")
    lines += ["", "2018 所有对齐日、2020 两个事件窗口的逐日涨跌幅都处于显示精度附近；"
              "2020 万科 2019-08-15 的大差异落在两种事件对齐的估计期内，对 CAR 拟合形成口径敏感性。"
              "本试验不能核实首次公开时刻、样本代表性、公司行为或事件因果，也不能校准 Agent 成交和价格冲击。", "",
              "来源说明：[AKShare 历史行情字段与复权口径](https://akshare.akfamily.xyz/data/stock/stock.html)，"
              "[AKShare 实现源码](https://github.com/akfamily/akshare/blob/master/akshare/stock_feature/stock_hist_em.py)。"
              "本地配置保存 8 个请求的参数和响应哈希。", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run_check(args.config, args.output_dir)
    print(json.dumps({"run_id": result["run_id"], "periods": len(result["periods"]),
                      "event_comparisons": sum(len(p["events"]) for p in result["periods"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
