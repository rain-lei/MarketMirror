"""Scientific plot of observed gross amount relative to pre-event medians."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

from ..data_pipeline.market_data import read_rows
from ..data_pipeline.provenance import file_sha256
from .activity_event_study import VERSION as ACTIVITY_EVENT_VERSION


def plot_activity(results_path: Path, output_dir: Path) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    results_path, output_dir = results_path.resolve(), output_dir.resolve()
    manifest_path = results_path.parent / "activity_event_manifest.json"
    csv_path = results_path.parent / "event_activity.csv"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    inputs = {p: file_sha256(p) for p in (results_path, manifest_path, csv_path)}
    if (manifest["pipeline_version"] != ACTIVITY_EVENT_VERSION
            or inputs[results_path] != manifest["artifacts"][results_path.name]["sha256"]
            or inputs[csv_path] != manifest["artifacts"][csv_path.name]["sha256"]):
        raise ValueError("activity study inputs differ from manifest")
    result = json.loads(results_path.read_text(encoding="utf-8"))
    if result["pipeline_version"] != ACTIVITY_EVENT_VERSION or not result["runs"]:
        raise ValueError("figure needs a nonempty supported observed activity study")
    by_pair = defaultdict(list)
    required = {"event_id", "stock_code", "relative_trade_day", "amount_to_estimation_median"}
    for _, row in read_rows(csv_path, required):
        value = float(row["amount_to_estimation_median"])
        if not math.isfinite(value) or value < 0:
            raise ValueError("nonfinite or negative activity fold")
        by_pair[(row["event_id"], row["stock_code"])].append((int(row["relative_trade_day"]), value))
    if set(by_pair) != {(r["event_id"], r["stock_code"]) for r in result["runs"]}:
        raise ValueError("figure stock/event pairs differ from result summary")
    for run in result["runs"]:
        points = sorted(by_pair[(run["event_id"], run["stock_code"])])
        if len(points) != 9 or [d for d, _ in points] != list(range(-3, 6)):
            raise ValueError("activity figure requires the validated nine-session event windows")
        if abs(dict(points)[0] - run["event_day_amount_fold"]) > 1e-10:
            raise ValueError("event-day fold differs from result summary")
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    chinese_font = font_path.exists()
    if chinese_font:
        font_manager.fontManager.addfont(str(font_path))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=font_path).get_name()
    plt.rcParams["axes.unicode_minus"] = False
    events = list(dict.fromkeys(r["event_id"] for r in result["runs"]))
    codes = sorted({r["stock_code"] for r in result["runs"]})
    palette = ["#235789", "#bf5b30", "#3d816e", "#8c65a3"]
    colors = {code: palette[i % len(palette)] for i, code in enumerate(codes)}
    fig, axes = plt.subplots(1, len(events), figsize=(max(7.4, 6.2 * len(events)), 5.2), sharey=True, squeeze=False)
    labels = {"wuhan_date_only_conservative": "日期保守对齐" if chinese_font else "Date-only conservative",
              "wuhan_effective_time_upper_bound": "生效时刻代理" if chinese_font else "Effective-time proxy"}
    for axis, event_id in zip(axes[0], events):
        runs = [r for r in result["runs"] if r["event_id"] == event_id]
        for run in runs:
            x, y = zip(*sorted(by_pair[(event_id, run["stock_code"])]))
            axis.plot(x, y, color=colors[run["stock_code"]], linewidth=2, marker="o", markersize=4,
                      label=run["stock_code"])
        axis.axhline(1, color="#8b969e", linewidth=1, linestyle=":")
        axis.axvline(0, color="#8b969e", linewidth=1, linestyle="--")
        axis.set_xticks(range(-3, 6))
        axis.set_xlim(-3.25, 5.25)
        axis.set_ylim(bottom=0)
        axis.set_title(labels.get(event_id, event_id) + "\n" + runs[0]["event_date_used"], fontsize=11)
        axis.set_xlabel("相对交易日" if chinese_font else "Relative session")
        axis.grid(axis="y", alpha=0.18)
        axis.spines[["top", "right"]].set_visible(False)
        axis.legend(frameon=False, loc="upper right", fontsize=9)
    axes[0][0].set_ylabel("日成交额 ÷ 事件前估计期中位数" if chinese_font else "Daily amount / pre-event median")
    fig.suptitle("同一事件的成交额对齐敏感性" if chinese_font else "Trading amount under alternative alignments",
                 x=0.08, ha="left", fontsize=15)
    note = ("右图 +1 与左图 0 是同一交易日（2月3日）；成交额为双向总量，不代表净买压、盘口深度或因果效应。"
            if chinese_font else "Gross two-sided activity; alignments are not independent events or net order flow.")
    fig.text(0.08, 0.035, note, fontsize=9, color="#56616d")
    fig.subplots_adjust(left=0.08, right=0.98, bottom=0.17, top=0.78, wspace=0.14)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty activity figure directory")
    if any(output_dir == p or output_dir in p.parents for p in inputs):
        raise ValueError("figure output must not contain study inputs")
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for suffix in ("png", "svg"):
        target = output_dir / ("activity_alignment_comparison." + suffix)
        fig.savefig(target, dpi=220, facecolor="white")
        artifacts[target.name] = {"sha256": file_sha256(target)}
    plt.close(fig)
    if any(file_sha256(p) != h for p, h in inputs.items()):
        raise RuntimeError("activity study changed during rendering")
    report = {"pipeline_version": "activity-figure-v1", "analysis_id": result["analysis_id"],
              "input_sha256": {str(p): h for p, h in inputs.items()},
              "code_sha256": file_sha256(Path(__file__)), "matplotlib_version": matplotlib.__version__,
              "artifacts": artifacts, "interpretation": result["interpretation"]}
    (output_dir / "figure_manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dependency-dir", type=Path)
    args = parser.parse_args()
    if args.dependency_dir:
        import sys
        sys.path.insert(0, str(args.dependency_dir.resolve()))
    report = plot_activity(args.results, args.output_dir)
    print(json.dumps({"analysis_id": report["analysis_id"], "artifacts": report["artifacts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
