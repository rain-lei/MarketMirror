"""Export the completed frozen resource study without changing its model or scores.

Requires the actual complete execution proof. Reopens every condition to derive
single-sided matched shares and gross notional in integer monetary minor units.
This is a reporting step; it does not repeat the independent raw ledger audit.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

from .resource_progress import progress

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "research/configs/temporal_resource_unit_study_2022_v2.json"
SOURCE = ROOT / "research_outputs/temporal_transport_resource_units_2022_v2"
PARENT = ROOT / "research_outputs/temporal_transport_execution_2022_v1"
ARMS = (
    "uniform_100_original_resources",
    "raw_initial_fixed_cash_shares",
    "raw_initial_preserve_wealth_stock_notional",
)
LABELS = dict(zip(ARMS, ("统一100", "原价／固定现金股数", "原价／保持财富及股票名义市值")))
FIELDS = ("mean", "volatility", "zero_fraction", "stock_correlation", "portfolio_volatility")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def metrics(row):
    a, c = row["comparison"]["synthetic"], row["common_factor_metrics"]["synthetic"]
    return dict(zip(FIELDS, (a["mean"], a["standard_deviation"], a["zero_return_fraction"],
                            c["mean_pairwise_stock_return_correlation"], c["equal_weight_daily_return_std"])))


def median_complete(xs):
    return statistics.median(xs) if xs and all(x is not None for x in xs) else None


def csv_bytes(rows):
    require(bool(rows), "Cannot export an empty table")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)  # None is an empty cell; never a numeric zero.
    return stream.getvalue().encode("utf-8-sig")


def fmt(value, percent=False):
    if value is None:
        return "未定义"
    return f"{value * 100:.4f}%" if percent else f"{value:,.4f}"


def report(arms, strata, pair_summary, folder):
    rel = "../" + folder.relative_to(ROOT).as_posix()
    passes = "、".join(f"{LABELS[r['resource_arm']]} {r['joint_pass_conditions']}/240" for r in arms)
    lines = [
        "# 2022资源初始化对照：完整结果", "",
        "2026-10-05。原登记的720条件、720配对和36分层已完成实际执行及最终独立核验。"
        + "冻结的五项联合开发容差通过数为：" + passes + "。原默认参数保持不变。", "",
        "本项是已查看2022窗口上的有限机制开发检查。三类Agent采用预设决策规则；"
        "资源初始化差异用于解释模型内行为，不能据此识别真实投资者或宣称监管预测能力。", "",
        "## 数据和核验范围", "",
        "保留123家公司、41个三资产组合、58个交易日、5束种子和原48个机制条件。"
        "三种资源组各240条件：统一100基线复用已封存结果，另模拟480条件。"
        "新增全量审计覆盖1,141,440条日账本、3,424,320次资产拍卖、944,640项账户结果。"
        "十个整组生产／审计阶段和两个原始最终汇总／核验命令均须有实际退出0凭据。", "",
        "本报告逐项重开全部720个condition.json，核对完整7134公司日及汇总成交股数，"
        "计算单边成交额Σ(成交股数×实际清算价)。金额先以整数最小货币单位累加，"
        "表内除以100显示模型元；不计手续费、不将买卖两侧相加。"
        "此前独立审计已核对日统计与原始成交；本报告没有再次审计全部原始账本。", "",
        "## 三组各240条件", "",
        "下列数值为各组完整240条件的中位数；它们混合原登记的不同机制设置，"
        "用于描述本次有限设计。未定义值保留，不以0填补。", "",
        "| 资源组 | 联合通过/240 | 日均收益 | 个股日波动 | 平价比例 | 股票相关 | 组合日波动 | 成交股数中位数 | 单边成交额中位数（模型元） |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in arms:
        m = row["metric_medians"]
        lines.append("| " + " | ".join((LABELS[row["resource_arm"]], str(row["joint_pass_conditions"]),
            *(fmt(m[k], k != "stock_correlation") for k in FIELDS),
            fmt(row["matched_shares_median"]), fmt(row["gross_notional_minor_median"] / 100))) + " |")
    lines += ["", "## 全部36分层", "",
        "每行完整20条件；接收方式为原部分接收、仅市场公共或全部公共。"
        "剩余扰动依赖与市场风险接收保持原登记设置。", "",
        "| 资源组 | 剩余依赖 | 接收 | 市场风险 | 联合通过/20 | 日均收益 | 个股日波动 | 平价比例 | 股票相关 | 组合日波动 | 成交股数中位数 | 单边成交额中位数（模型元） |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    dep = {"independent": "独立", "historical_rank": "历史秩共同"}
    delivery = {"masked": "原部分", "public": "市场公共", "whole_public": "全部公共"}
    market = {"market_independent": "独立", "market_shared": "共同"}
    for row in strata:
        lines.append("| " + " | ".join((LABELS[row["resource_arm"]], dep[row["residual_dependence"]],
            delivery[row["delivery"]], market[row["risk_mode"]], str(row["joint_pass_conditions"]),
            *(fmt(row["metric_medians"][k], k != "stock_correlation") for k in FIELDS),
            fmt(row["matched_shares_median"]), fmt(row["gross_notional_minor_median"] / 100))) + " |")
    lines += ["", "## 配对资源变化", "",
        "每种对照保留全部240配对。差值定义为处理组减参照组，数字表示差值为正、"
        "为零或为负的条件数。正负表示方向，不直接代表好坏。共享公司、日期和机制的"
        "重复不能当成720份独立真实市场样本；这里不计算置信区间或显著性。", "",
        "| 处理组减参照组 | 指标 | 正/零/负/未定义 | 差值中位数 | 最小—最大差值 |",
        "| --- | --- | --- | ---: | ---: |"]
    names = {"mean": "日均收益", "volatility": "个股日波动", "zero_fraction": "平价比例",
             "stock_correlation": "股票相关", "portfolio_volatility": "组合日波动",
             "matched_shares": "成交股数", "gross_notional_minor": "单边成交额（模型元）"}
    for group in pair_summary:
        label = LABELS[group["treatment_resource_arm"]] + "减" + LABELS[group["reference_resource_arm"]]
        for field, stat in group["changes"].items():
            scale = 0.01 if field == "gross_notional_minor" else 1
            def shown(value):
                if value is not None and field in FIELDS and field != "stock_correlation":
                    return f"{value * 100:.4f}个百分点"
                return fmt(None if value is None else value * scale)
            lines.append("| " + " | ".join((label, names[field],
                "/".join(str(stat[k]) for k in ("positive", "zero", "negative", "undefined")),
                shown(stat["median"]), shown(stat["minimum"]) + "—" + shown(stat["maximum"]))) + " |")
    lines += ["", "## 解释边界", "",
        "原价／固定现金股数组改变了初始财富和现金股票比例。原价／保持财富及股票名义市值组"
        "按100股整手向下投影，余量回到原账户现金，个体财富保持，股份供给改变。"
        "背景订单数量上限和目标偏移仍按股数保持，因此名义订单规模随价格变化。"
        "这两组均包含资源结构变化，不能作纯价格单位转换的因果解释。", "",
        "来源价格仅在初始化时读取一次；后续由订单清算，不按历史真实日价重置。"
        "历史行情是描述性参照，联合开发容差继续如实报告；不会据此扩大行情拟合或选择新默认。"
        "当前原型重点仍为三类决策可解释、账务与时点正确、有限对照和可复现。", "",
        "## 全量表和凭据", "",
        f"- [全部720条件]({rel}/conditions.csv)、[全部720配对]({rel}/pairs.csv)、[全部36分层]({rel}/strata.csv)。",
        f"- [导出结果]({rel}/summary.json)、[导出范围、输入及输出摘要]({rel}/receipt.json)。",
        "- [原冻结协议](configs/temporal_resource_unit_study_2022_v2.json)、[完整结果](../research_outputs/temporal_transport_resource_units_2022_v2/results.json)、[最终独立核验](../research_outputs/temporal_transport_resource_units_2022_v2/final_verification.json)。",
        "- [实际完成凭据](../research_outputs/resource_unit_checkpoint_jobs_20261005_v1/completion.json)、[恢复执行记录](RESOURCE_BATCH_RECOVERY_2026.md)、[原预登记解释](TEMPORAL_RESOURCE_UNIT_STUDY_2022.md)。", ""]
    return "\n".join(lines).encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="research_outputs/resource_unit_result_exports_20261005_v1")
    parser.add_argument("--report", default="research/TEMPORAL_RESOURCE_UNIT_RESULTS_2022.md")
    args = parser.parse_args()
    folder, report_path = (ROOT / args.output).resolve(), (ROOT / args.report).resolve()
    require(folder.is_relative_to(ROOT) and report_path.is_relative_to(ROOT), "Outputs must remain in the project")
    require(report_path.parent == (ROOT / "research").resolve(), "The report belongs in research for its relative evidence links")
    require(not folder.exists() and not report_path.exists(), "Preserve previous exports and report")
    state = progress()
    require(state["resource_execution_complete"] is True, "Actual complete resource execution is required before reporting")
    cfg, result, final = read(CONFIG), read(SOURCE / "results.json"), read(SOURCE / "final_verification.json")
    require(final["results_sha256"] == sha(SOURCE / "results.json") and final["unique_conditions"] == 720
            and final["resource_pairs"] == 720 and final["strata"] == 36, "Final independent resource scope changed")
    bindings = {p.relative_to(ROOT).as_posix(): sha(p) for p in (
        CONFIG, SOURCE / "results.json", SOURCE / "final_verification.json",
        ROOT / "research_outputs/resource_unit_checkpoint_jobs_20261005_v1/completion.json",
        Path(__file__), ROOT / "research/simulation/call_auction.py")}
    index, records, daily_count = {}, [], 0
    seed_indices = {s["seed_id"]: i for i, s in enumerate(cfg["seeds"])}
    day_grid = {(stock, day) for stock in cfg["stocks"] for day in cfg["dates"]}
    require(len(day_grid) == 7134, "Expected complete registered company-day grid")
    for row in result["variants"]:
        seed, arm, ci = row["seed_id"], row["resource_arm"], row["parent_cell_index"]
        identity = (seed, arm, ci)
        require(identity not in index and arm in ARMS, "Duplicate or unknown resource condition")
        cell = ci if arm in ARMS[:2] else 48 + ci
        path = (PARENT if arm == ARMS[0] else SOURCE) / f"seed_{seed_indices[seed]}/cell_{cell}/condition.json"
        bindings[path.relative_to(ROOT).as_posix()] = sha(path)
        saved = read(path)["variant"]
        expected = {k: v for k, v in saved.items() if k != "daily_asset_rows"}
        if arm == ARMS[0]:
            expected.update(resource_arm=arm, parent_cell_index=ci, parent_condition_name=row["parent_condition_name"])
        require(expected == row, "Condition compact record differs from completed result")
        days = saved["daily_asset_rows"]
        require(len(days) == len(day_grid) and {(r["stock_code"], r["trade_date"]) for r in days} == day_grid,
                "A condition company-day grid is missing, duplicated or extra")
        shares, notional = 0, 0
        for day in days:
            q, p = day["matched_volume"], day["price_after_minor"]
            require(type(q) is int and q >= 0 and q % 100 == 0 and type(p) is int and p > 0,
                    "Undefined or invalid clearing price/volume must not be filled")
            shares += q
            notional += q * p
        require(shares == row["total_matched_volume"], "Derived single-sided shares disagree with audited total")
        daily_count += len(days)
        record = {"seed_id": seed, "resource_arm": arm, "parent_cell_index": ci,
            "parent_condition_name": row["parent_condition_name"], "residual_dependence": row["residual_dependence"],
            "delivery": row["delivery"], "risk_mode": row["risk_mode"], "background_anchor": row["background_anchor"],
            "feedback_scale": row["feedback_scale"], "joint_pass": row["joint_checks"]["all_five_pass"],
            **metrics(row), "matched_shares": shares, "gross_notional_minor": notional}
        index[identity] = record
        records.append(record)
    grid = {(s, a, i) for s in seed_indices for a in ARMS for i in range(48)}
    require(len(records) == len(index) == 720 and set(index) == grid and daily_count == 5_136_480,
            "The complete 720-condition reporting scope is required")
    pairs = []
    for pair in result["summary"]["resource_pairs"]:
        a = index[pair["seed_id"], pair["treatment_resource_arm"], pair["parent_cell_index"]]
        b = index[pair["seed_id"], pair["reference_resource_arm"], pair["parent_cell_index"]]
        changes = {k: None if a[k] is None or b[k] is None else a[k] - b[k] for k in FIELDS}
        require(changes == pair["metric_changes"], "Export metric changes differ from independently verified paired changes")
        pairs.append({k: pair[k] for k in ("seed_id", "parent_cell_index", "treatment_resource_arm", "reference_resource_arm",
                                          "joint_pass_treatment", "joint_pass_reference")} | {
            "change_" + k: changes[k] for k in FIELDS} | {
            "change_matched_shares": a["matched_shares"] - b["matched_shares"],
            "change_gross_notional_minor": a["gross_notional_minor"] - b["gross_notional_minor"]})
    require(len(pairs) == 720, "All three complete resource pair contrasts are required")
    strata, strata_flat = [], []
    for group in result["summary"]["strata"]:
        selected = [r for r in records if all(r[k] == group[k] for k in ("resource_arm", "residual_dependence", "delivery", "risk_mode"))]
        require(len(selected) == 20, "A reported stratum must retain all 20 conditions")
        require({k: median_complete([r[k] for r in selected]) for k in FIELDS} == group["metric_medians"], "Stratum metric medians differ")
        derived = {k + "_median": median_complete([r[k] for r in selected]) for k in ("matched_shares", "gross_notional_minor")}
        strata.append(group | derived)
        strata_flat.append({k: group[k] for k in ("resource_arm", "residual_dependence", "delivery", "risk_mode", "conditions", "joint_pass_conditions")} |
                           {"median_" + k: group["metric_medians"][k] for k in FIELDS} | derived)
    require(len(strata) == 36, "All resource strata are required")
    arms = []
    for arm in ARMS:
        selected = [r for r in records if r["resource_arm"] == arm]
        require(len(selected) == 240, "A resource arm must retain 240 conditions")
        count = sum(r["joint_pass"] for r in selected)
        require(count == result["summary"]["joint_pass_conditions_by_arm"][arm], "Arm joint-pass count differs")
        arms.append({"resource_arm": arm, "conditions": 240, "joint_pass_conditions": count,
                     "metric_medians": {k: median_complete([r[k] for r in selected]) for k in FIELDS},
                     **{k + "_median": median_complete([r[k] for r in selected]) for k in ("matched_shares", "gross_notional_minor")}})
    pair_summary = []
    for treatment, reference in ((ARMS[1], ARMS[0]), (ARMS[2], ARMS[0]), (ARMS[2], ARMS[1])):
        selected = [r for r in pairs if (r["treatment_resource_arm"], r["reference_resource_arm"]) == (treatment, reference)]
        require(len(selected) == 240, "A resource pair contrast must retain 240 pairs")
        changes = {}
        for field in (*FIELDS, "matched_shares", "gross_notional_minor"):
            xs = [r["change_" + field] for r in selected]
            defined = [x for x in xs if x is not None]
            changes[field] = {"positive": sum(x > 0 for x in defined), "zero": sum(x == 0 for x in defined),
                "negative": sum(x < 0 for x in defined), "undefined": len(xs) - len(defined),
                "median": median_complete(xs), "minimum": min(defined) if defined else None,
                "maximum": max(defined) if defined else None}
        pair_summary.append({"treatment_resource_arm": treatment, "reference_resource_arm": reference, "pairs": 240, "changes": changes})
    summary = {"status": "COMPLETE_DESCRIPTIVE_EXPORT_FROM_ACTUAL_FULL_RESOURCE_EXECUTION", "arms": arms,
               "strata": strata, "paired_changes": pair_summary, "new_simulations_or_parameter_changes": False,
               "raw_ledgers_reaudited_by_this_export": False, "empirical_forecast_validation": False}
    outputs = {folder / "conditions.csv": csv_bytes(records), folder / "pairs.csv": csv_bytes(pairs),
               folder / "strata.csv": csv_bytes(strata_flat), folder / "summary.json": encoded(summary),
               report_path: report(arms, strata, pair_summary, folder)}
    require(all(sha(ROOT / name) == digest for name, digest in bindings.items()), "Report inputs changed during reading")
    folder.mkdir()
    for path, data in outputs.items():
        with path.open("xb") as stream:
            stream.write(data)
    receipt = {"status": "PASS_COMPLETE_RESOURCE_RESULT_EXPORT", "created_at_utc": datetime.now(timezone.utc).isoformat(),
               "conditions": 720, "pairs": 720, "strata": 36, "company_day_rows_reopened": daily_count,
               "unknown_values_preserved": True, "single_sided_notional_excludes_fees": True,
               "scientific_protocols_changed": False, "raw_ledgers_reaudited": False,
               "input_sha256": bindings, "output_sha256": {p.relative_to(ROOT).as_posix(): sha(p) for p in outputs}}
    with (folder / "receipt.json").open("xb") as stream:
        stream.write(encoded(receipt))
    print(json.dumps({"status": receipt["status"], "report": report_path.relative_to(ROOT).as_posix(),
                      "joint_pass_by_arm": result["summary"]["joint_pass_conditions_by_arm"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
