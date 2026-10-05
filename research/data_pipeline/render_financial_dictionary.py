"""Render a reviewable financial field dictionary from an extraction report.

The extraction report contains observed structure and descriptive statistics.  It
does not establish the economic meaning of a column.  This renderer keeps those
two claims separate and produces a small, deterministic review artifact without
including local source paths or row-level text.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .provenance import file_sha256


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "data_contracts" / "financial_fields.json"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _missing_rate(item: dict[str, Any], total_rows: int) -> float | None:
    if not total_rows:
        return None
    return round(item["missing_count"] / total_rows, 8)


def build_dictionary(report_path: Path, output_dir: Path) -> dict[str, Any]:
    report_path = report_path.resolve()
    output_dir = output_dir.resolve()
    report = _read_json(report_path)
    catalog = _read_json(CATALOG_PATH)
    fields = report.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ValueError("extraction report has no fields")
    counts = report.get("counts", {})
    total_rows = int(counts.get("source_rows", 0))
    catalog_names = list(catalog.get("snapshot_metrics", [])) + list(catalog.get("row_context_metrics", []))
    observed_names = [item.get("field_name") for item in fields]
    if sorted(catalog_names) != sorted(observed_names):
        raise ValueError("field catalog and extraction report do not contain the same columns")

    unresolved = [
        {"key": "report_period", "status": "unverified", "question": "来源季度字段是否代表报告期，以及报告年度如何确定？"},
        {"key": "publication_time", "status": "unverified", "question": "财务值何时对外可见，是否存在公告修订或版本？"},
        {"key": "unit", "status": "unverified", "question": "每个指标的币种、数量级和每股单位是什么？"},
        {"key": "transform", "status": "unverified", "question": "原值是否经过缩放、取绝对值、累计或其他数值变换？"},
        {"key": "statement_type", "status": "unverified", "question": "字段属于期末余额、单季值、累计值还是其他报表口径？"},
        {"key": "p_and_pe", "status": "unverified", "question": "p、p/e 的观测时间、估值口径和计算来源是什么？"},
    ]
    rendered_fields = []
    for item in fields:
        name = str(item["field_name"])
        rendered_fields.append({
            "field_name": name,
            "role": str(item["role"]),
            "structural_status": "observed",
            "verification_status": str(item.get("verification_status", "unverified")),
            "missing_count": int(item["missing_count"]),
            "missing_rate": _missing_rate(item, total_rows),
            "numeric_count": int(item["numeric_count"]),
            "non_numeric_count": int(item["non_numeric_count"]),
            "negative_count": int(item["negative_count"]),
            "zero_count": int(item["zero_count"]),
            "numeric_min": item.get("numeric_min"),
            "numeric_max": item.get("numeric_max"),
            "unit": None,
            "transform": None,
            "period_basis": None,
            "publication_time": None,
        })

    dictionary = {
        "dictionary_version": "financial-field-dictionary-v1",
        "generated_from": {
            "report_file": report_path.name,
            "report_sha256": file_sha256(report_path),
            "source_file": report.get("source_file"),
            "source_file_sha256": report.get("source_file_hash"),
            "catalog_sha256": file_sha256(CATALOG_PATH),
            "pipeline_version": report.get("pipeline_version"),
        },
        "scope": {
            "source_rows": total_rows,
            "field_count": len(rendered_fields),
            "snapshot_field_count": sum(item["role"] == "snapshot" for item in rendered_fields),
            "row_context_field_count": sum(item["role"] == "row_context" for item in rendered_fields),
            "all_values_status": "unverified",
        },
        "fields": rendered_fields,
        "unresolved_semantics": unresolved,
        "allowed_uses": [
            "审计字段存在、缺失率、数值形态和来源回溯关系。",
            "为后续人工核对建立逐字段清单。",
        ],
        "blocked_uses": [
            "不能据此推断报告期、公告日、币种或数值单位。",
            "不能将字段直接写入 verified 的 financial_quarter 表。",
            "不能把 p、p/e 或公司标签当作已验证的预测特征。",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "field_dictionary.json"
    md_path = output_dir / "field_dictionary.md"
    json_path.write_text(json.dumps(dictionary, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(dictionary), encoding="utf-8")
    return dictionary


def render_markdown(dictionary: dict[str, Any]) -> str:
    source = dictionary["generated_from"]
    scope = dictionary["scope"]
    lines = [
        "# 财务字段口径字典（待核对）", "",
        "本文件只描述输入结构和统计形态，不把列名或数值外观解释为财务事实。所有字段当前为 `unverified`。", "",
        f"- 提取报告：`{source['report_file']}`（SHA-256 `{source['report_sha256']}`）",
        f"- 来源文件：`{source.get('source_file')}`（SHA-256 `{source.get('source_file_sha256')}`）",
        f"- 字段合同 SHA-256：`{source['catalog_sha256']}`",
        f"- 来源行数：{scope['source_rows']:,}；字段数：{scope['field_count']}（快照 {scope['snapshot_field_count']}，行上下文 {scope['row_context_field_count']}）", "",
        "## 字段统计", "",
        "| 字段 | 层级 | 缺失率 | 数值 | 非数值 | 负值 | 零值 | 数值范围 | 状态 |",
        "|---|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for item in dictionary["fields"]:
        minimum = item["numeric_min"]
        maximum = item["numeric_max"]
        value_range = "—" if minimum is None else f"{minimum} … {maximum}"
        rate = "—" if item["missing_rate"] is None else f"{item['missing_rate']:.2%}"
        lines.append(
            f"| {item['field_name'].replace('|', '/')} | {item['role']} | {rate} | {item['numeric_count']:,} | "
            f"{item['non_numeric_count']:,} | {item['negative_count']:,} | {item['zero_count']:,} | {value_range} | `{item['verification_status']}` |"
        )
    lines += ["", "## 尚未确认的经济含义", ""]
    lines.extend(f"- `{item['key']}`：{item['question']}（{item['status']}）" for item in dictionary["unresolved_semantics"])
    lines += ["", "## 可用范围", ""]
    lines.extend(f"- {item}" for item in dictionary["allowed_uses"])
    lines += ["", "## 当前禁止", ""]
    lines.extend(f"- {item}" for item in dictionary["blocked_uses"])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a reviewable financial field dictionary")
    parser.add_argument("report", type=Path, help="financial_quality_report.json")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    dictionary = build_dictionary(args.report, args.output_dir)
    print(json.dumps({"field_count": dictionary["scope"]["field_count"], "output_dir": str(args.output_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
