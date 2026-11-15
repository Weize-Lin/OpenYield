#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_run.py —— 把仿真测量值变成 pass / fail

为什么要它
----------
OpenYield 的仿真器产出的是"测量值"，不是"合格 / 不合格"。真正的数据在
deck.sp.mt0 / .mt1 / ... 里（每个样本一个文件，20 多个测量项）。
本脚本按仓库自己的定义补上判定这一层，
对应 docs/README.md 的 open item 第 8 条（failure defined by the release checks）。

判据来自仓库源码，不是自己发明的
------------------------------
① 电压判据（Sram6TCoreMcTestbench.access_validity）
     六个相位 ACCESS / HOLD / PRE_ACCESS / BOUNDARY / ROLE / ISO
     的节点误差 |V*_ERROR_c| <= 0.10 * VDD
     写操作额外含 VWEN_ACCESS_ERROR_c，同一阈值
     数据恢复 |VRESTORE_ERROR_c| <= 0.02 * VDD
② 时序判据（本脚本补充）
     读：TREAD_TOTAL <= summary.json 的 timing.low_read
     写：TWRITE_TOTAL <= timing.low_write
③ 未完成
     测量值为 FAILED / NA / 缺失（Xyce 设了 MEASFAIL=1，失败会写 FAILED）
     按 AGENTS.md：未完成与失效必须分开计数

用法
----
    python dev/check_run.py outputs/timed4
    python dev/check_run.py outputs/*/
    python dev/check_run.py outputs/timed4 --list          # 列出全部测量项
    python dev/check_run.py outputs/timed4 --no-timing     # 只查电压判据
    python dev/check_run.py outputs/timed4 --detail        # 逐项打印每个检查

退出码
------
    0 全部通过   1 存在失效   2 存在未完成   3 参数或路径错误
"""

import argparse
import glob
import json
import os
import re
import sys

VOLT_PHASES = ("ACCESS", "HOLD", "PRE_ACCESS", "BOUNDARY", "ROLE", "ISO")
PHASE_LIMIT_FRACTION = 0.10      # access_validity: 0.1 * vdd
RESTORE_LIMIT_FRACTION = 0.02    # access_validity: 0.02 * vdd
TIMING_METRIC = {"read": ("TREAD_TOTAL", "low_read"),
                 "write": ("TWRITE_TOTAL", "low_write"),
                 "read&write": ("TVOUT_PERIOD", None)}

VOLT_PATTERN = re.compile(
    r"^V(?:WEN_)?({})_ERROR_(\d+)$".format("|".join(VOLT_PHASES)))
RESTORE_PATTERN = re.compile(r"^VRESTORE_ERROR_(\d+)$")


def natural_key(text):
    """让 mt2 排在 mt10 前面。"""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", text)]


def find_runs(paths):
    """把命令行参数展开成 run 目录列表（含 summary.json 的目录）。"""
    runs = []
    for raw in paths:
        for path in sorted(glob.glob(raw)) or [raw]:
            path = os.path.abspath(path)
            if os.path.isfile(os.path.join(path, "summary.json")):
                runs.append(path)
                continue
            if not os.path.isdir(path):
                print("找不到路径: {}".format(path), file=sys.stderr)
                continue
            found = [os.path.join(path, child) for child in sorted(os.listdir(path))
                     if os.path.isfile(os.path.join(path, child, "summary.json"))]
            if not found:
                for child in sorted(os.listdir(path)):
                    sub = os.path.join(path, child)
                    if os.path.isdir(sub):
                        found += [os.path.join(sub, grand) for grand in sorted(os.listdir(sub))
                                  if os.path.isfile(os.path.join(sub, grand, "summary.json"))]
            runs.extend(found)
    seen, ordered = set(), []
    for run in runs:
        if run not in seen:
            seen.add(run)
            ordered.append(run)
    return ordered


def load_summary(run_dir):
    with open(os.path.join(run_dir, "summary.json"), encoding="utf-8") as handle:
        return json.load(handle)


def mt_files(run_dir):
    files = glob.glob(os.path.join(run_dir, "deck.sp.mt*"))
    return sorted(files, key=natural_key)


def parse_measures(path):
    """解析 'KEY = VALUE'；VALUE 可能是数字，也可能是 FAILED / NA。"""
    measures, bad = {}, {}
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if "=" not in line:
                continue
            key, _, raw = line.partition("=")
            key, raw = key.strip(), raw.strip()
            if not key:
                continue
            try:
                measures[key] = float(raw)
            except ValueError:
                bad[key] = raw
    return measures, bad


def fmt_seconds(value):
    if value is None:
        return "—"
    for unit, scale in (("ns", 1e-9), ("ps", 1e-12), ("fs", 1e-15)):
        if abs(value) >= scale:
            return "{:.4g} {}".format(value / scale, unit)
    return "{:.4g} s".format(value)


def build_checks(summary, measures, use_timing):
    """返回 [(名称, 实测值, 阈值, 显示用字符串)]。"""
    vdd = float(summary.get("vdd", 1.0))
    checks = []
    for name in sorted(measures):
        match = VOLT_PATTERN.match(name)
        if match:
            limit = PHASE_LIMIT_FRACTION * vdd
            checks.append((name, abs(measures[name]), limit, "{:.3g} V".format(measures[name])))
            continue
        if RESTORE_PATTERN.match(name):
            limit = RESTORE_LIMIT_FRACTION * vdd
            checks.append((name, abs(measures[name]), limit, "{:.3g} V".format(measures[name])))
    if use_timing:
        operation = summary.get("operation", "read")
        metric, budget_key = TIMING_METRIC.get(operation, (None, None))
        if metric and budget_key and metric in measures:
            budget = summary.get("timing", {}).get(budget_key)
            if budget:
                checks.append((metric, measures[metric], float(budget), fmt_seconds(measures[metric])))
    return checks


def check_run(run_dir, use_timing, detail):
    summary = load_summary(run_dir)
    operation = summary.get("operation", "read")
    vdd = float(summary.get("vdd", 1.0))
    files = mt_files(run_dir)
    if not files:
        print("run: {}".format(os.path.basename(run_dir)))
        print("     [跳过] 没有 deck.sp.mt* 文件（这次运行没真跑仿真）")
        print()
        return None

    timing = summary.get("timing", {})
    timing_metric, budget_key = TIMING_METRIC.get(operation, (None, None))
    timing_budget = timing.get(budget_key) if budget_key else None

    print("run: {}".format(os.path.basename(run_dir)))
    print("     规模 {}×{} · {} · 角 {} · 样本 {} · 种子 {} · VDD {:.3g} V".format(
        summary.get("rows"), summary.get("cols"), operation, summary.get("corner"),
        summary.get("mc_runs"), summary.get("seed"), vdd))
    print("     判据 ① 电压误差 <= {:.3g} V（{} 个相位）+ 恢复 <= {:.3g} V".format(
        PHASE_LIMIT_FRACTION * vdd, len(VOLT_PHASES), RESTORE_LIMIT_FRACTION * vdd))
    if use_timing and timing_metric:
        if timing_budget:
            print("          ② {} <= {}（timing.{}，查表得到）".format(
                timing_metric, fmt_seconds(float(timing_budget)), budget_key))
        else:
            print("          ② {} 无对应预算，本次跳过时序判据".format(timing_metric))
    print("          ③ 测量值为 FAILED / NA / 缺失 → 记为未完成，与失效分开统计")

    passed, failed, incomplete = 0, [], []
    worst_all, worst_all_name = 0.0, "—"
    for path in files:
        measures, bad = parse_measures(path)
        tag = os.path.basename(path).split(".")[-1]
        if bad:
            incomplete.append((tag, "、".join(sorted(bad))))
            print("     {:>4}  未完成（{}）".format(tag, "、".join(sorted(bad))))
            continue
        checks = build_checks(summary, measures, use_timing)
        if not checks:
            incomplete.append((tag, "没有可判定的测量项"))
            print("     {:>4}  未完成（没有可判定的测量项）".format(tag))
            continue
        breaking = [c for c in checks if c[1] > c[2]]
        worst = max(checks, key=lambda c: c[1] / c[2])
        ratio = worst[1] / worst[2]
        if ratio > worst_all:
            worst_all, worst_all_name = ratio, worst[0]
        if breaking:
            failed.append((tag, worst[0], ratio))
            print("     {:>4}  FAIL  最紧 {:.1%} · {}".format(tag, ratio, worst[0]))
            for name, value, limit, shown in breaking:
                print("              {:>22} {} > {}".format(name, shown, fmt_seconds(limit)
                                                          if "T" in name[:1] and limit < 1e-3
                                                          else "{:.3g} V".format(limit)))
        else:
            passed += 1
            print("     {:>4}  pass  最紧 {:.1%} · {}".format(tag, ratio, worst[0]))
            if detail:
                for name, value, limit, shown in sorted(checks, key=lambda c: -c[1] / c[2]):
                    print("              {:>22} {:>7.1%}  {}".format(name, value / limit, shown))

    print("     合计 {} 个样本：通过 {} · 失效 {} · 未完成 {}".format(
        len(files), passed, len(failed), len(incomplete)))
    if worst_all:
        print("     全场最紧 {:.1%}（{}）—— 越接近 100% 越危险".format(worst_all, worst_all_name))
    if failed:
        print("     失效样本：" + "、".join("{}({} {:.1%})".format(t, n, r) for t, n, r in failed))
    print()
    return passed, len(failed), len(incomplete)


def main():
    parser = argparse.ArgumentParser(
        description="按仓库 access_validity 的定义，把 OpenYield 仿真测量值变成 pass / fail")
    parser.add_argument("paths", nargs="+", help="run 目录，或包含 run 目录的父目录，支持通配符")
    parser.add_argument("--list", action="store_true", help="只列出第一个样本里有哪些测量项")
    parser.add_argument("--no-timing", action="store_true", help="只查电压判据，不查时序")
    parser.add_argument("--detail", action="store_true", help="逐项打印通过样本的全部检查")
    parser.add_argument("-q", "--quiet", action="store_true", help="只打印汇总")
    args = parser.parse_args()

    runs = find_runs(args.paths)
    if not runs:
        print("没有找到含 summary.json 的运行目录。", file=sys.stderr)
        return 3

    if args.list:
        for run in runs:
            files = mt_files(run)
            if not files:
                print("{}: 没有 mt 文件".format(os.path.basename(run)))
                continue
            measures, bad = parse_measures(files[0])
            print("{}  ({} 个样本)".format(os.path.basename(run), len(files)))
            for key in sorted(measures):
                print("    {:<22} {}".format(key, measures[key]))
            for key in sorted(bad):
                print("    {:<22} {}  ← 未完成".format(key, bad[key]))
        return 0

    print("=" * 72)
    totals = [0, 0, 0]
    for run in runs:
        result = check_run(run, use_timing=not args.no_timing, detail=args.detail)
        if result:
            for index, value in enumerate(result):
                totals[index] += value
    print("=" * 72)
    print("全部 {} 个运行：通过 {} · 失效 {} · 未完成 {}".format(
        len(runs), totals[0], totals[1], totals[2]))

    if totals[1]:
        return 1
    if totals[2]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
