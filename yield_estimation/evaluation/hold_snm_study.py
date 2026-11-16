"""Reproduce the single-cell 6T hold-SNM comparison from one frozen setup."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import json
import math
from pathlib import Path
import shlex
import subprocess
import sys
from statistics import mean, stdev
from time import perf_counter

from .compare_diagnostics import condition_mismatches
from .run import ROOT, read_config, source_revision, wilson_interval
from .run_diagnostics import MODULES, plan


CASE = Path(__file__).with_name("cases") / "hold_snm_6t_study.json"
REFERENCE_SEEDS = (8311, 8312, 8313, 8314)
TRIAL_SEEDS = tuple(range(9101, 9109))
REFERENCE_BATCH = 32
REFERENCE_PER_SEED = 256
INITIAL_BATCH = 4
PROPOSAL_VARIANCE = 1e-5


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def save_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def study_plan(output, base):
    tasks = []
    for group, seeds in (("reference", REFERENCE_SEEDS), ("trials", TRIAL_SEEDS)):
        algorithms = ["mc"] if group == "reference" else list(MODULES)
        for seed in seeds:
            folder = output / group / str(seed)
            config = dict(base, seed=seed)
            if group == "reference":
                config.update(budget=REFERENCE_PER_SEED, batch_size=REFERENCE_BATCH)
            config_path = folder / "experiment.json"
            commands = plan(config_path, folder, algorithms, INITIAL_BATCH, 1, 1, PROPOSAL_VARIANCE)
            for algorithm, command in commands:
                if algorithm in algorithms:
                    tasks.append(dict(group=group, seed=seed, algorithm=algorithm,
                                      config=config, config_path=str(config_path), command=command,
                                      run=str(folder / algorithm), log=str(folder / (algorithm + ".log"))))
    return tasks


def execute_task(task):
    command, path = task["command"], Path(task["log"])
    start = perf_counter()
    with path.open("w", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    run = Path(task["run"]) / "run_summary.json"
    raw = run.read_bytes() if run.exists() else None
    print(f"{task['group']}/{task['seed']}/{task['algorithm']} exit={result.returncode}", flush=True)
    return dict(exit_code=result.returncode, wall_seconds=perf_counter() - start,
                summary_sha256=hashlib.sha256(raw).hexdigest() if raw else None,
                summary=json.loads(raw) if raw else {})


def run_counts(summary):
    return (summary.get("simulator_submitted_samples", summary.get("requested_samples")),
            summary.get("simulator_returned_samples", summary.get("returned_rows")))


def check_result(task, result, common):
    summary = result["summary"]
    if not summary:
        return ["missing_summary"]
    issues = []
    if result["exit_code"] or summary.get("status") not in ("complete", "budget_stopped", "stopped"):
        issues.append("run_incomplete")
    if condition_mismatches(common, summary):
        issues.append("different_experiment_conditions")
    if any(summary.get(key) != common.get(key)
           for key in ("git_revision", "git_tracked_files_modified")):
        issues.append("different_source")
    config = task["config"]
    budget = config["budget"]
    if summary.get("seed") != config["seed"] or summary.get("budget") != budget:
        issues.append("wrong_trial_config")
    if run_counts(summary) != (budget, budget) or summary.get("unclassified_samples", 0):
        issues.append("different_actual_simulation_count")
    return issues


def trial_row(task, result, common, reference):
    summary = result["summary"]
    issues = check_result(task, result, common)
    draws = summary.get("diagnostics", {}).get("estimation_draws_evaluated")
    numeric = summary.get("diagnostics", {}).get("numeric_status", "direct_MC")
    estimate = summary.get("pfail_estimate")
    if numeric == "underflow" and summary.get("log10_pfail") is not None:
        estimate = 0.0
    if estimate is None or not math.isfinite(estimate) or estimate < 0:
        issues.append("no_usable_estimate")
    if task["algorithm"] != "mc" and not draws:
        issues.append("no_estimation_draws")
    submitted, returned = run_counts(summary)
    weights = summary.get("sampling_diagnostics", {})
    return dict(algorithm=task["algorithm"], implementation=summary.get("algorithm"), seed=task["seed"],
                budget=task["config"]["budget"], algorithm_requests=summary.get("algorithm_requested_samples",
                submitted), simulator_submitted=submitted, simulator_returned=returned,
                estimation_draws=draws if task["algorithm"] != "mc" else returned,
                zero_target_draws=summary.get("zero_target_samples", 0), estimate=estimate,
                log10_pfail=summary.get("log10_pfail"), numeric_status=numeric,
                above_one=estimate is not None and estimate > 1,
                log10_mean_weight=weights.get("log10_mean_weight"), weight_ess=weights.get("weight_ess"),
                weight_warnings=";".join(weights.get("warnings", ())),
                issues=";".join(issues), abs_error=None if issues else abs(estimate - reference),
                summary_sha256=result["summary_sha256"])


def method_rows(seeds):
    rows = []
    for name in MODULES:
        trials = [row for row in seeds if row["algorithm"] == name]
        valid = [row for row in trials if not row["issues"]]
        scored = len(valid) == len(TRIAL_SEEDS)
        estimates = [row["estimate"] for row in valid]
        errors = [row["abs_error"] for row in valid]
        rows.append(dict(algorithm=name, eligible_runs=len(valid), total_runs=len(trials),
                         mean_estimate=mean(estimates) if scored else None,
                         seed_std=stdev(estimates) if scored else None,
                         mean_absolute_error=mean(errors) if scored else None,
                         rmse=math.hypot(*errors) / math.sqrt(len(errors)) if scored else None,
                         weight_diagnostic_runs=sum(row["weight_ess"] is not None for row in trials),
                         low_weight_runs=sum("low_observed_weight_mass" in row["weight_warnings"].split(";")
                                             for row in trials),
                         estimates_above_one=sum(row["above_one"] for row in trials)))
    return rows


def report(output, base, tasks, results):
    reference = [(task, result) for task, result in zip(tasks, results) if task["group"] == "reference"]
    common = reference[0][1]["summary"]
    reference_issues = [check_result(task, result, common) for task, result in reference]
    if any(reference_issues):
        raise ValueError(f"Reference incomplete or inconsistent: {reference_issues}; inspect study_runs.csv")
    trials = sum(result["summary"]["returned_rows"] for _, result in reference)
    failures = sum(result["summary"]["failure_count"] for _, result in reference)
    point = failures / trials
    lower, upper = wilson_interval(failures, trials)
    save_json(output / "reference.json", dict(samples=trials, failures=failures, estimate=point,
                                             wilson_95=[lower, upper], seeds=REFERENCE_SEEDS))
    rows = [trial_row(task, result, common, point) for task, result in zip(tasks, results)
            if task["group"] == "trials"]
    save_csv(output / "per_seed.csv", rows)
    methods = method_rows(rows)
    save_csv(output / "by_algorithm.csv", methods)
    lines = ["# 6T 单单元保持裕量对照", "",
             f"失效定义为保持裕量低于 {base['threshold_mv']} mV。",
             f"独立 MC 参考为 {failures}/{trials}（{point:.3%}），95% Wilson 区间 {lower:.3%}–{upper:.3%}。", "",
             f"每个算法跑 {len(TRIAL_SEEDS)} 个种子，每次预算 {base['budget']} 条，包含找失效点和复测。",
             "工况和预算通过表示本轮有估计、提交与返回齐全且工况一致。低权重告警单独记入，保留这些轮次的误差。",
             "低权重告警的分母是有权重诊断的种子数。任何种子未通过工况与预算核对时，汇总误差留空。",
             "误差以参考点估计为准，参考值本身也有抽样误差。结果适用于本次工况和报告所列算法设置。", "",
             "| 算法 | 通过工况与预算核对 | 低权重告警 | 平均估计 (%) | 跨种子标准差 (百分点) | 平均绝对误差 (百分点) | RMSE (百分点) |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in methods:
        values = [row[key] for key in ("mean_estimate", "seed_std", "mean_absolute_error", "rmse")]
        metrics = " | ".join(display_percent(value) for value in values)
        count = row["total_runs"]
        diagnosed = row["weight_diagnostic_runs"]
        warning = f"{row['low_weight_runs']}/{diagnosed}" if diagnosed else "—"
        if row["low_weight_runs"]:
            warning = f"**{warning}**"
        lines.append(f"| {row['algorithm']} | {row['eligible_runs']}/{count} | {warning} | {metrics} |")
    lines += ["", "逐个种子的诊断、提交与返回数见 per_seed.csv。",
              "study_plan.json 保存实验配置和原始命令。并行运行的耗时受资源竞争影响，表格按仿真样本条数比较。"]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)


def display_percent(value):
    if value is None:
        return "—"
    if 0 < abs(value) < 1e-5:
        return f"{100 * value:.2e}"
    return f"{100 * value:.3f}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--jobs", type=int, default=1, help="Parallel independent jobs (default 1)")
    parser.add_argument("--run-xyce", action="store_true")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    output = args.output_dir.resolve()
    if output.exists():
        parser.error("Choose a new output directory")
    base = read_config(CASE)
    tasks = study_plan(output, base)
    print(f"Reference: {len(REFERENCE_SEEDS)} × {REFERENCE_PER_SEED}. Trials: "
          f"{len(TRIAL_SEEDS)} seeds × {len(MODULES)} algorithms × {base['budget']}.")
    if not args.run_xyce:
        print("Preview only.")
        for task in tasks:
            print(shlex.join(task["command"]))
        return 0
    output.mkdir(parents=True)
    save_json(output / "study_plan.json", dict(
        source_revision=source_revision(), recipe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        tasks=tasks,
    ))
    for task in tasks:
        file = Path(task["config_path"])
        if not file.exists():
            file.parent.mkdir(parents=True)
            save_json(file, task["config"])
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(execute_task, tasks))
    save_csv(output / "study_runs.csv", [
        dict(group=task["group"], seed=task["seed"], algorithm=task["algorithm"], log=task["log"],
             **{key: result[key] for key in ("exit_code", "wall_seconds", "summary_sha256")})
        for task, result in zip(tasks, results)
    ])
    try:
        report(output, base, tasks, results)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    return int(any(result["exit_code"] for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
