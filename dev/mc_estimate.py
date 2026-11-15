#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mc_estimate.py —— 照着 yield_estimation/model_lib/MC.py 的逻辑，重写成能跑的版本

为什么要重写而不是修原文件
------------------------
原 MC.py 有 6 道墙：import torch（环境里没有）、5 处 /home/lixy 硬编码路径、
两处一运行必崩的 bug（save_result 传了不存在的 seed 参数、print(x.shape())）。
按 AGENTS.md 的规矩，动仓库代码要先建立波形正确性；所以在 dev/ 里另写一份，
逻辑与 MC.py 一致，接口换成当前 release 的 runner。

与 MC.py 的逐条对应
------------------
  MC.py 第 110 行：从 N(means, cov) 抽 initial_num 个  → 本脚本：runner 的 --vth-std 抽样
  MC.py 第 115 行：run_mc_simulation(...) 拿到 y        → 本脚本：调用 per_device_mc.run
  MC.py 第 44 行 ：indicator(y) 判失效                  → 本脚本：check_run.build_checks（仓库 access_validity 口径）
  MC.py 第 66 行 ：P_fail = 失效数/总数                 → 相同（累计样本）
  MC.py 第 75 行 ：FOM = 最近 K 轮 Pf 的 std/mean       → 相同
  MC.py 第 70 行 ：均值=0 时 FOM 记为 1（永不收敛）      → 相同
  MC.py 第 126 行：while FOM>0.01 或样本未满             → 相同（轮数上限可配）

用法
----
  # 先用很小的规模试通（2 轮 × 5 个样本，约 5 分钟）
  python dev/mc_estimate.py --mc-runs 5 --max-samples 10 --fom-target 0.20

  # 正式跑：每轮 20 个，最多 200 个，FOM 目标 10%
  python dev/mc_estimate.py --mc-runs 20 --max-samples 200 --fom-target 0.10

  # 让失效出现：放大 sigma / 收紧时序预算
  python dev/mc_estimate.py --vth-std 0.15 --mc-runs 10 --max-samples 60 --fom-target 0.15
  python dev/mc_estimate.py --budget 7.5e-10 --mc-runs 10 --max-samples 60 --fom-target 0.15

退出码
------
  0 正常结束（FOM 达标）  1 达到样本/轮数上限仍未达标  3 出错
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from check_run import build_checks, load_summary, mt_files, parse_measures  # noqa: E402


def run_batch(rows, cols, operation, target_row, target_col, batch, seed,
              vth_std, real_cell_mode, outdir, log_path):
    """调当前的 release runner 跑一批样本（等价于 MC.py 里的一次 run_mc_simulation）。"""
    cmd = [
        sys.executable, "-m", "sram_compiler.per_device_mc.run",
        "--rows", str(rows), "--cols", str(cols),
        "--target-row", str(target_row), "--target-col", str(target_col),
        "--operation", operation,
        "--mc-runs", str(batch),
        "--seed", str(seed),
        "--vth-std", str(vth_std),
        "--real-cell-mode", str(real_cell_mode),
        "--output-dir", outdir,
        "--run-xyce",
    ]
    started = time.time()
    with open(log_path, "w", encoding="utf-8") as log:
        result = subprocess.run(cmd, cwd=REPO, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode != 0:
        raise RuntimeError("runner 退出码 {}，日志见 {}".format(result.returncode, log_path))
    return time.time() - started


def gather(run_dirs):
    """把若干 run 目录里的测量值全部读出来（对应 MC.py 里不断累积的 self.y）。"""
    samples = []
    for run_dir in run_dirs:
        for path in mt_files(run_dir):
            measures, bad = parse_measures(path)
            samples.append((measures, bad))
    return samples


def evaluate(samples, summary, use_timing):
    """判失效并给出 (失效数, 总数, 未完成数)。判据来自仓库 access_validity（见 check_run）。"""
    failed = incomplete = 0
    for measures, bad in samples:
        if bad or not measures:
            incomplete += 1
            continue
        checks = build_checks(summary, measures, use_timing)
        if not checks:
            incomplete += 1
            continue
        if any(value > limit for _, value, limit, _ in checks):
            failed += 1
    return failed, len(samples), incomplete


def std_over_mean(values):
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    if mean == 0:
        return 1.0                      # 与 MC.py 第 70 行一致：均值为 0 时不收敛
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return (var ** 0.5) / mean


def main():
    parser = argparse.ArgumentParser(
        description="照 MC.py 的逻辑重写的可运行版：抽样→仿真→判失效→算 Pf 与 FOM→自动停")
    parser.add_argument("--rows", type=int, default=4)
    parser.add_argument("--cols", type=int, default=2)
    parser.add_argument("--operation", default="read",
                        choices=["read", "write", "read&write", "hold_snm", "read_snm", "write_snm"])
    parser.add_argument("--target-row", type=int, default=3)
    parser.add_argument("--target-col", type=int, default=1)
    parser.add_argument("--vth-std", type=float, default=0.05,
                        help="随机参数的相对 sigma（默认 0.05 = 5%%；放大它让失效出现）")
    parser.add_argument("--real-cell-mode", type=int, default=0)
    parser.add_argument("--budget", type=float, default=None,
                        help="覆盖时序预算（秒）；不给则用 summary.json 的 timing.low_read")
    parser.add_argument("--mc-runs", type=int, default=10, help="每轮样本数")
    parser.add_argument("--max-samples", type=int, default=100, help="累计样本上限")
    parser.add_argument("--max-rounds", type=int, default=20, help="轮数上限")
    parser.add_argument("--fom-target", type=float, default=0.10,
                        help="FOM 目标，达到即停（MC.py 用 0.01，太严会跑很久）")
    parser.add_argument("--fom-rounds", type=int, default=3,
                        help="用最近多少轮算 FOM（MC.py 用 10）")
    parser.add_argument("--seed", type=int, default=20260711, help="基础种子，每轮自动 +1")
    parser.add_argument("--out-dir", default=os.path.join("outputs", "mc_est"))
    parser.add_argument("--csv", default=os.path.join("dev", "results", "mc_estimate.csv"))
    parser.add_argument("--no-timing", action="store_true", help="只查电压判据")
    args = parser.parse_args()

    os.chdir(REPO)
    out_root = args.out_dir if os.path.isabs(args.out_dir) else os.path.join(REPO, args.out_dir)
    csv_path = args.csv if os.path.isabs(args.csv) else os.path.join(REPO, args.csv)
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    os.makedirs(out_root, exist_ok=True)

    print("=" * 74)
    print("MC 估计（逻辑照 MC.py，接口用当前 release 的 runner）")
    print("  阵列 {}×{} · {} · vth-std {:.3g} · 每轮 {} 个 · 上限 {} 个 / {} 轮".format(
        args.rows, args.cols, args.operation, args.vth_std,
        args.mc_runs, args.max_samples, args.max_rounds))
    print("  停止条件：FOM <= {:.3g}（最近 {} 轮）或 样本/轮数到上限".format(
        args.fom_target, args.fom_rounds))
    print("=" * 74)

    started = time.time()
    run_dirs, pf_history, rows_out = [], [], []
    total = failed = 0
    summary = None
    exit_code = 1

    for round_index in range(args.max_rounds):
        if total >= args.max_samples:
            break
        batch = min(args.mc_runs, args.max_samples - total)
        round_dir = os.path.join(out_root, "round{:02d}".format(round_index))
        log_path = os.path.join(out_root, "round{:02d}.log".format(round_index))
        seed = args.seed + round_index

        print("\n[轮 {}] 跑 {} 个样本（seed {}）……".format(round_index + 1, batch, seed))
        elapsed_batch = run_batch(args.rows, args.cols, args.operation,
                                  args.target_row, args.target_col, batch, seed,
                                  args.vth_std, args.real_cell_mode, round_dir, log_path)
        if not os.path.isdir(round_dir):
            print("  [错误] 没有产出目录，日志见 {}".format(log_path))
            return 3
        config_dir = None
        for found in sorted(glob.glob(os.path.join(round_dir, "*"))):
            if os.path.isfile(os.path.join(found, "summary.json")):
                config_dir = found
                break
        if config_dir is None:
            print("  [错误] 本轮的 summary.json 没找到，日志见 {}".format(log_path))
            return 3
        run_dirs.append(config_dir)
        if summary is None:
            summary = load_summary(config_dir)

        if args.budget and "timing" in summary:
            summary["timing"]["low_read"] = args.budget

        samples = gather(run_dirs)
        failed, total, incomplete = evaluate(samples, summary, not args.no_timing)
        pf = failed / total if total else 0.0
        pf_history.append(pf)
        recent = pf_history[-args.fom_rounds:]
        fom = std_over_mean(recent)

        rows_out.append((round_index + 1, total, failed, pf, fom,
                         incomplete, time.time() - started))
        print("  [MC] # 样本 {} · 失效 {} · 未完成 {} · Pf {:.4g} · FOM {} · 本轮 {:.0f}s"
              .format(total, failed, incomplete, pf,
                      "—" if fom is None else "{:.3f}".format(fom), elapsed_batch))

        if fom is not None and len(pf_history) >= args.fom_rounds:
            if fom <= args.fom_target:
                print("\n>>> FOM {:.3f} <= 目标 {:.3g}，收敛，停止。".format(fom, args.fom_target))
                exit_code = 0
                break
            if pf == 0.0:
                print("    提示：Pf = 0（一个都没坏），FOM 恒为 1，永远收敛不了。")
                print("          要么放大 --vth-std，要么用 --budget 收紧时序预算。")

    print("\n" + "=" * 74)
    if rows_out:
        last = rows_out[-1]
        print("结束：{} 轮 · 累计 {} 个样本 · 失效 {} 个 · Pf {:.4g}{}".format(
            last[0], last[1], last[2], last[3],
            " · FOM {}".format("—" if last[4] is None else "{:.3f}".format(last[4]))))
        print("总耗时 {:.1f} 分钟 · 平均 {:.1f} 秒/样本".format(
            last[6] / 60.0, last[6] / max(last[1], 1)))
    if exit_code == 1:
        print("未达到 FOM 目标，是样本数/轮数先到上限。")

    with open(csv_path, "w", encoding="utf-8") as handle:
        handle.write("round,total_samples,failed,Pf,FOM,incomplete,elapsed_s\n")
        for row in rows_out:
            handle.write("{},{},{},{:.6g},{},{},{:.1f}\n".format(
                row[0], row[1], row[2], row[3],
                "" if row[4] is None else "{:.6g}".format(row[4]), row[5], row[6]))
    print("过程表已写入 {}".format(os.path.relpath(csv_path, REPO)))
    print("=" * 74)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
