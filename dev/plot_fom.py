#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plot_fom.py —— 把 mc_estimate.py 的过程表画成 FOM 收敛曲线

画什么
----
  上图：FOM（估计的稳定性）随累计样本数的变化，带一条目标线和停止点
  下图：Pf（失效率）随累计样本数的变化 —— FOM 就是由它算出来的，两张一起看才明白

用法
----
  python dev/plot_fom.py                                   # 读默认 CSV
  python dev/plot_fom.py --target 0.15                     # 画上目标线
  python dev/plot_fom.py a.csv b.csv --target 0.15         # 多条曲线对比（比如 MC vs MNIS）
  python dev/plot_fom.py --out dev/results/fom_a.png --no-pf

输出
----
  PNG 文件（默认 dev/results/fom_curve.png）+ 终端里打印一张小表
  提示：图里的文字用英文，避免 Linux 缺中文字体导致方框乱码
"""

import argparse
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def read_csv(path):
    """读一行一条记录的过程表；FOM 可能是空字符串（第一轮算不出来）。"""
    samples, pf, fom = [], [], []
    with open(path, encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                n = int(row["total_samples"])
                p = float(row["Pf"])
            except (KeyError, TypeError, ValueError):
                continue
            samples.append(n)
            pf.append(p)
            raw = (row.get("FOM") or "").strip()
            fom.append(float(raw) if raw else None)
    return samples, pf, fom


def main():
    parser = argparse.ArgumentParser(description="画 FOM 收敛曲线")
    parser.add_argument("csv", nargs="*", default=None,
                        help="过程表 CSV（默认 dev/results/mc_estimate.csv）")
    parser.add_argument("--out", default=os.path.join("dev", "results", "fom_curve.png"))
    parser.add_argument("--target", type=float, default=None, help="FOM 目标线（例如 0.15）")
    parser.add_argument("--no-pf", action="store_true", help="只画 FOM，不画 Pf")
    args = parser.parse_args()

    paths = args.csv or [os.path.join(REPO, "dev", "results", "mc_estimate.csv")]
    paths = [p if os.path.isabs(p) else os.path.join(REPO, p) for p in paths]
    missing = [p for p in paths if not os.path.isfile(p)]
    if missing:
        print("找不到文件: " + ", ".join(missing), file=sys.stderr)
        print("先跑一次 dev/mc_estimate.py，它会生成这张表。", file=sys.stderr)
        return 3

    series = []
    for path in paths:
        samples, pf, fom = read_csv(path)
        if not samples:
            print("{} 里没有有效数据".format(os.path.basename(path)), file=sys.stderr)
            continue
        series.append((os.path.basename(path), samples, pf, fom))
    if not series:
        return 3

    # 终端里也打印一份，方便没有图形界面时查看
    for name, samples, pf, fom in series:
        print("=" * 60)
        print(name)
        print("  {:>8} {:>10} {:>10}".format("样本数", "Pf", "FOM"))
        for n, p, f in zip(samples, pf, fom):
            print("  {:>8} {:>10.4g} {:>10}".format(n, p, "—" if f is None else "{:.3f}".format(f)))
        last = [f for f in fom if f is not None]
        if last:
            print("  最后一轮 FOM = {:.3f}".format(last[-1]))

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n[提示] 这个环境里没有 matplotlib，只打印了上面的表。")
        print("      在 openyield 环境里跑就有（environment.yml 里带 matplotlib）。")
        return 0

    rows = 1 if args.no_pf else 2
    figure, axes = plt.subplots(rows, 1, figsize=(7.2, 3.2 * rows + 0.6),
                                sharex=True, squeeze=False)
    axes = axes[:, 0]
    colors = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e"]

    for index, (name, samples, pf, fom) in enumerate(series):
        color = colors[index % len(colors)]
        xs = [n for n, f in zip(samples, fom) if f is not None]
        ys = [f for f in fom if f is not None]
        if xs:
            axes[0].plot(xs, ys, marker="o", markersize=4, color=color, label=name)
            axes[0].annotate("{:.3f}".format(ys[-1]), (xs[-1], ys[-1]),
                             textcoords="offset points", xytext=(6, 4),
                             fontsize=8, color=color)
            axes[0].scatter([xs[-1]], [ys[-1]], s=70, facecolors="none",
                            edgecolors=color, linewidths=1.5, zorder=5)
        if not args.no_pf:
            axes[1].plot(samples, pf, marker="o", markersize=4, color=color, label=name)

    if args.target is not None:
        axes[0].axhline(args.target, linestyle="--", linewidth=1.2, color="#c00000")
        axes[0].text(samples[0], args.target, "  target {:.3g}".format(args.target),
                     va="bottom", fontsize=8, color="#c00000")

    axes[0].set_ylabel("FOM  (std / mean of recent Pf)")
    axes[0].set_title("FOM convergence  (circle = stopping point)")
    axes[0].grid(alpha=0.3)
    axes[0].set_ylim(bottom=0)

    if not args.no_pf:
        axes[1].set_ylabel("Pf  (failure probability)")
        axes[1].set_xlabel("cumulative number of simulated samples")
        axes[1].grid(alpha=0.3)
        axes[1].set_ylim(bottom=0)
    else:
        axes[0].set_xlabel("cumulative number of simulated samples")

    if len(series) > 1:
        axes[0].legend(fontsize=8)

    figure.tight_layout()
    out = args.out if os.path.isabs(args.out) else os.path.join(REPO, args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    figure.savefig(out, dpi=150)
    print("\n图已保存: {}".format(os.path.relpath(out, REPO)))
    print("Windows 上打开: {}".format(
        "\\\\wsl$\\Ubuntu" + os.path.join(REPO, out).replace("/", "\\")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
