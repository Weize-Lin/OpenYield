"""Run selected MC/MNIS diagnostics sequentially from one frozen configuration."""
import argparse
import json
import math
from pathlib import Path
import shlex
import subprocess
import sys

from yield_estimation.evaluation.run import (
    ROOT, evaluation_circuit_options, read_config, source_revision, validate_algorithm_dimensions,
)

MODULES = {"mc": "yield_estimation.evaluation.run",
           "mnis": "yield_estimation.evaluation.mnis_run",
           **{name: "yield_estimation.evaluation.adaptive_run" for name in ("ais", "acs", "hscs")}}


def plan(config_path, output, algorithms, initial_batch, initial_failures, estimation_batch,
         proposal_variance=1.0, allow_hscs_mixed_search=False):
    commands = []
    for algorithm in algorithms:
        command = [sys.executable, "-m", MODULES[algorithm], "--config", str(config_path),
                   "--output-dir", str(output / algorithm)]
        if algorithm != "mc":
            command += ["--initial-batch", str(initial_batch), "--initial-failures",
                        str(initial_failures), "--estimation-batch", str(estimation_batch)]
        if algorithm in ("ais", "acs", "hscs"):
            command += ["--algorithm", algorithm, "--proposal-variance", str(proposal_variance)]
        if algorithm == "hscs" and allow_hscs_mixed_search:
            command += ["--allow-hscs-mixed-search"]
        commands.append((algorithm, command))
    if set(algorithms) == {"mc", "mnis"}:
        commands.append(("comparison", [sys.executable, "-m",
            "yield_estimation.evaluation.compare_diagnostics",
            "--mc-run", str(output / "mc"), "--mnis-run", str(output / "mnis"),
            "--output-dir", str(output / "comparison")]))
    elif len(algorithms) > 1:
        commands.append(("collection", [sys.executable, "-m",
            "yield_estimation.evaluation.collect_diagnostics", "--runs",
            *[str(output / name) for name in algorithms], "--output-dir", str(output / "collection")]))
    return commands


def execute(config_path, output, algorithms, run_xyce=False,
            initial_batch=2, initial_failures=1, estimation_batch=1,
            proposal_variance=1.0, allow_hscs_mixed_search=False):
    if not algorithms or len(set(algorithms)) != len(algorithms) or any(a not in MODULES for a in algorithms):
        raise ValueError("Choose supported algorithms without duplicates")
    if any(type(n) is not int or n < 1 for n in (initial_batch, initial_failures, estimation_batch)):
        raise ValueError("MNIS sample settings must be positive integers")
    if not math.isfinite(proposal_variance) or proposal_variance <= 0:
        raise ValueError("Proposal variance must be positive and finite")
    if "hscs" in algorithms and not allow_hscs_mixed_search:
        raise ValueError("HSCS needs explicit --allow-hscs-mixed-search; auxiliary writes consume budget")
    if "ais" in algorithms and estimation_batch % initial_failures:
        raise ValueError("AIS estimation batch must be a multiple of initial failures")
    config = read_config(config_path)
    options = evaluation_circuit_options(config)
    for algorithm in algorithms:
        validate_algorithm_dimensions(options, algorithm)
    print("Circuit: " + json.dumps(options, sort_keys=True), flush=True)
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"Output exists; choose a new directory: {output}")
    commands = plan(output / "experiment.json", output, algorithms,
                    initial_batch, initial_failures, estimation_batch, proposal_variance, allow_hscs_mixed_search)
    for name, command in commands:
        print(name + ": " + shlex.join(command), flush=True)
    if not run_xyce:
        print("Preview only: no files created, no simulations started. Add --run-xyce to execute.")
        return 0

    output.mkdir(parents=True)
    (output / "experiment.json").write_text(json.dumps(config, indent=2, allow_nan=False))
    revision = source_revision()
    manifest = dict(kind="diagnostic_suite", ranking_eligible=False, status="running",
                    git_revision=revision[0], git_tracked_files_modified=revision[1],
                    algorithms=algorithms, stages=[])
    def save():
        temp = output / "suite_summary.json.tmp"
        temp.write_text(json.dumps(manifest, indent=2, allow_nan=False))
        temp.replace(output / "suite_summary.json")
    save()
    for name, command in commands:
        stage = dict(name=name, command=command, shell_command=shlex.join(command),
                     status="running", log=str(output / (name + ".log")), exit_code=None)
        manifest["stages"].append(stage)
        if name == "comparison" and any(s["status"] != "process_succeeded" for s in manifest["stages"][:-1]):
            stage["status"] = "skipped_prior_error"
            save()
            continue
        if source_revision() != revision:
            stage["status"] = "blocked_source_changed"
            save()
            break
        save()
        try:
            with Path(stage["log"]).open("w") as log:
                result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            stage["exit_code"] = result.returncode
            stage["status"] = "process_succeeded" if result.returncode == 0 else "process_failed"
            if result.returncode == 0:
                report = output / name / ("comparison.json" if name in ("comparison", "collection") else "run_summary.json")
                data = json.loads(report.read_text())
                stage["result_status"] = data.get("status", data.get("kind"))
                stage["numeric_status"] = data.get("diagnostics", {}).get("numeric_status")
                stage["report"] = str(output / name / "report.md")
        except KeyboardInterrupt:
            stage["status"] = "interrupted"
            save()
            break
        except (OSError, ValueError) as exc:
            stage["status"] = "error"
            stage["detail"] = type(exc).__name__ + ": " + str(exc)
        save()
        print(name + ": " + stage["status"], flush=True)
    complete = len(manifest["stages"]) == len(commands) and all(
        s["status"] == "process_succeeded" for s in manifest["stages"])
    manifest["status"] = "completed_diagnostics" if complete else "incomplete"
    save()
    lines = ["# 诊断任务执行记录", "", f"整体状态：{manifest['status']}", "",
             "| 步骤 | 进程状态 | 退出码 | 数值状态 |", "| --- | --- | --- | --- |"]
    for s in manifest["stages"]:
        lines.append(f"| {s['name']} | {s['status']} | {s['exit_code']} | {s.get('numeric_status', '')} |")
    lines += ["", "进程成功只表示命令正常结束，不表示失效率估计可靠。请看各子目录report.md。",
              "所有步骤使用experiment.json中的同一份配置。命令原文及输出分别在suite_summary.json和各步骤.log。",
              "这是诊断入口，没有正式算法排名。预算名额与实际仿真调用数仍分开记录。"]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("report=" + str(output / "report.md"))
    return 0 if complete else 1


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--algorithms", nargs="+", choices=tuple(MODULES), default=["mc", "mnis"])
    p.add_argument("--run-xyce", action="store_true")
    p.add_argument("--initial-batch", type=int, default=2)
    p.add_argument("--initial-failures", type=int, default=1)
    p.add_argument("--estimation-batch", type=int, default=1)
    p.add_argument("--proposal-variance", type=float, default=1.0)
    p.add_argument("--allow-hscs-mixed-search", action="store_true")
    a = p.parse_args()
    try:
        return execute(a.config, a.output_dir, a.algorithms, a.run_xyce,
                       a.initial_batch, a.initial_failures, a.estimation_batch,
                       a.proposal_variance, a.allow_hscs_mixed_search)
    except (OSError, ValueError) as exc:
        p.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())

