"""Render verified event-study cumulative abnormal-return curves as static figures."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from ..data_pipeline.provenance import file_sha256


def plot_results(results_path: Path, output_dir: Path) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    results_path = results_path.resolve()
    manifest_path = results_path.parent / "experiment_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = file_sha256(results_path)
    if digest != manifest["artifacts"][results_path.name]["sha256"]:
        raise ValueError("event results hash differs from experiment manifest")
    data = json.loads(results_path.read_text(encoding="utf-8"))
    if data["status"] != "complete" or not data["results"]:
        raise ValueError("figure requires a complete experiment with nonempty results")
    groups = defaultdict(list)
    for result in data["results"]:
        groups[result["event_id"]].append(result)
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    chinese_font = font_path.exists()
    if chinese_font:
        font_manager.fontManager.addfont(str(font_path))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=font_path).get_name()
    plt.rcParams["axes.unicode_minus"] = False
    count = len(groups)
    fig, axes = plt.subplots(1, count, figsize=(max(7, count * 6.3), 5.4), sharey=True, squeeze=False)
    labels = {
        "wuhan_date_only_conservative": "日期保守规则" if chinese_font else "Date-only conservative alignment",
        "wuhan_effective_time_upper_bound": "生效时刻代理（非首次发布时间）" if chinese_font else "Effective-time proxy (not first publication)",
    }
    codes = sorted({r["stock_code"] for r in data["results"]})
    palette = ["#235789", "#bf5b30", "#3d816e", "#8c65a3", "#967343"]
    colors = {code: palette[i % len(palette)] for i, code in enumerate(codes)}
    for axis, (event_id, series) in zip(axes[0], groups.items()):
        reference = series[0]
        dates = [r["trade_date"][5:] for r in reference["abnormal_returns"]]
        positions = list(range(len(dates)))
        for result in series:
            if [r["trade_date"][5:] for r in result["abnormal_returns"]] != dates:
                raise ValueError("figure stocks must share the same event sessions")
            total, values = 0.0, []
            for observation in result["abnormal_returns"]:
                total += observation["abnormal_return"]
                values.append(total * 100)
            if abs(total - result["cumulative_abnormal_return"]) > 1e-10:
                raise ValueError("curve sum differs from reported CAR")
            axis.plot(positions, values, color=colors[result["stock_code"]], linewidth=2,
                      marker="o", markersize=3.5, label=result["stock_code"])
        zero = next(i for i, r in enumerate(reference["abnormal_returns"]) if r["relative_trade_day"] == 0)
        axis.axvline(zero, color="#788490", linestyle="--", linewidth=1)
        axis.axhline(0, color="#788490", linewidth=0.8)
        axis.set_xticks(positions, dates, rotation=35)
        axis.set_title(labels.get(event_id, event_id) + "\n" + reference["event_date_used"], fontsize=11)
        axis.set_xlabel("事件窗口内的实际交易日" if chinese_font else "Actual sessions within event window")
        axis.grid(axis="y", alpha=0.18)
        axis.spines[["top", "right"]].set_visible(False)
        axis.legend(frameon=False, loc="upper left", fontsize=9)
    axes[0][0].set_ylabel("累计异常收益（百分点）" if chinese_font else "Cumulative abnormal return (percentage points)")
    alignment_pilot = set(groups) == {"wuhan_date_only_conservative", "wuhan_effective_time_upper_bound"}
    headline = ("同一事件的两种对齐口径" if chinese_font else "Alternative alignments of the same event") if alignment_pilot else (
        "事件窗口累计异常收益" if chinese_font else "Cumulative abnormal returns in event windows")
    if data["data_kind"] == "synthetic":
        headline = "合成数据：实现验证" if chinese_font else "Synthetic implementation validation"
    fig.suptitle(headline, fontsize=15, x=0.08, ha="left")
    note = ("样本为3只预选股票；曲线为异常简单收益之和。两种口径不是独立事件，也不表示因果效应或预测能力。"
            if chinese_font and len(codes) == 3 and alignment_pilot else "Preselected sample; curves sum abnormal simple returns. No causal or forecasting claim.")
    fig.text(0.08, 0.035, note, fontsize=9, color="#56616d")
    fig.subplots_adjust(left=0.08, right=0.98, bottom=0.2, top=0.78, wspace=0.15)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for suffix in ("png", "svg"):
        target = output_dir / ("event_alignment_comparison." + suffix)
        if target in {results_path, manifest_path}:
            raise ValueError("figure output must not overwrite experiment inputs")
        fig.savefig(target, dpi=220, facecolor="white")
        artifacts[target.name] = {"sha256": file_sha256(target)}
    plt.close(fig)
    if file_sha256(results_path) != digest:
        raise RuntimeError("event results changed during rendering")
    report = {"input_sha256": digest, "data_kind": data["data_kind"], "experiment_id": data["experiment_id"],
              "code_sha256": file_sha256(Path(__file__)), "matplotlib_version": matplotlib.__version__, "artifacts": artifacts}
    (output_dir / "figure_manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description="Render verified event-study scientific figures")
    parser.add_argument("results", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dependency-dir", type=Path)
    args = parser.parse_args()
    if args.dependency_dir:
        import sys
        sys.path.insert(0, str(args.dependency_dir.resolve()))
    print(plot_results(args.results, args.output_dir))


if __name__ == "__main__":
    main()
