"""Run repository MNIS with legacy or configured proposal settings."""
import argparse
import csv
import json
import math
import os
from pathlib import Path
import shutil

import numpy as np
from scipy.special import logsumexp
from yield_estimation.evaluation.outcomes import MeasurementRule
from yield_estimation.evaluation.run import (
    read_config, evaluation_circuit_options, validate_algorithm_case, build_evaluation_case, configured_initialization,
    RecordedAccessTestbench, source_revision, PARAMETER_FORMAT, functional_failure_key,
)
from yield_estimation.model_lib.MNIS import MNIS, MNISBudgetReached


def weight_summary(records):
    completed = [r for r in records if r["status"] == "evaluated"]
    count = sum(len(r["samples"]) for r in completed)
    logs = []
    supported = 0
    for record in completed:
        supported += int(record["support"].sum())
        failure = record["support"] & record["indicator"]
        logs.extend((record["log_f"][failure] - record["log_g"][failure]).tolist())
    if not count:
        return dict(estimation_draws_evaluated=0, supported_estimation_draws=0,
                    log10_pfail=None, log_domain_pfail=None, numeric_status="no_estimation")
    if not logs:
        return dict(estimation_draws_evaluated=count, supported_estimation_draws=supported,
                    log10_pfail=None, log_domain_pfail=0.0, numeric_status="zero_observed_contribution")
    value = float(logsumexp(logs) - np.log(count))
    with np.errstate(over="ignore", under="ignore"):
        linear = float(np.exp(value))
    state = "underflow" if linear == 0 else ("overflow" if not math.isfinite(linear) else "representable")
    return dict(estimation_draws_evaluated=count, supported_estimation_draws=supported,
                log10_pfail=value / math.log(10),
                log_domain_pfail=linear if math.isfinite(linear) and linear > 0 else None, numeric_status=state)


def sampling_diagnostics(records):
    completed = [r for r in records if r["status"] == "evaluated"]
    count = sum(len(r["samples"]) for r in completed)
    if not count:
        return dict(log10_mean_weight=None, weight_ess=None, warnings=["no_estimation_samples"])
    weights = np.concatenate([r["log_f"] - r["log_g"] for r in completed])
    finite = weights[np.isfinite(weights)]
    if not len(finite):
        return dict(log10_mean_weight=None, weight_ess=0.0, warnings=["all_observed_weights_zero"])
    total = float(logsumexp(finite))
    log_mean = total - math.log(count)
    scaled = np.exp(finite - finite.max())
    ess = float(scaled.sum() ** 2 / (scaled ** 2).sum())
    warnings = []
    if log_mean < math.log(.1):
        warnings.append("low_observed_weight_mass")
    elif log_mean > math.log(10):
        warnings.append("high_observed_weight_mass")
    return dict(log10_mean_weight=log_mean / math.log(10), weight_ess=ess, warnings=warnings)


def run(config, output, initial_batch=2, initial_failures=1, estimation_batch=1):
    for count in (initial_batch, initial_failures, estimation_batch):
        if type(count) is not int or count < 1:
            raise ValueError("MNIS batch sizes and initial failure count must be positive integers")
    options = evaluation_circuit_options(config)
    operation = options["operation"]
    rule = MeasurementRule(operation, config)
    validate_algorithm_case(options, "mnis", config)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    settings = dict(initial_batch=initial_batch, initial_failures=initial_failures,
                    estimation_batch=estimation_batch, strict_budget=True,
                    skip_zero_target=True, legacy_clipping=False)
    (output / "run_config.json").write_text(json.dumps(config, indent=2))
    (output / "mnis_settings.json").write_text(json.dumps(settings, indent=2))
    (output / "samples.csv").write_text(f"sample,batch,xyce_run,{rule.column},outcome\n")
    revision, dirty = source_revision()
    proposal_mode = config.get("mnis_proposal_covariance", "legacy")
    implementation = ("repository_MNIS_original_proposal" if proposal_mode == "legacy"
                      else "repository_MNIS_configured_proposal")
    summary = dict(config, algorithm=implementation,
                   git_revision=revision, git_tracked_files_modified=dirty,
                   budget_basis="algorithm_requests_including_recheck_and_zero_target_draws",
                   config_batch_size_note="MC setting; MNIS uses mnis_settings.json",
                   process_param_format=PARAMETER_FORMAT, settings=settings, operation=operation, **rule.metadata())
    algorithm = recorder = None
    original_dir = Path.cwd()
    error = None
    status = "incomplete"
    try:
        summary["xyce"] = shutil.which("Xyce")
        if not summary["xyce"]:
            raise RuntimeError("Xyce not found; activate openyield")
        means, target, bench = build_evaluation_case(config, options, output, summary)
        summary["nominal_parameters"] = means.tolist()
        bounds = configured_initialization(config, target, summary)
        recorder = RecordedAccessTestbench(bench, output, config)
        variances = target.sigma ** 2 if proposal_mode == "target_diagonal" else None
        summary["proposal_covariance"] = proposal_mode
        algorithm = MNIS(recorder, target, len(means), means, .01, initial_failures,
                         initial_batch, estimation_batch, 13, .24, 1,
                         seed=config["seed"], IS_bound_on=False, target=options["target"], operation=operation,
                         initialization_bounds=bounds, proposal_variance=variances)
        algorithm.threshold = rule.threshold
        algorithm.indicator = rule.failures
        os.chdir(output)
        algorithm.start_estimate(config["budget"], strict_budget=True, skip_zero_target=True)
        status = "stopped"
    except MNISBudgetReached as exc:
        status = "budget_stopped"
        error = str(exc)
    except (Exception, KeyboardInterrupt) as exc:
        status = "error"
        error = type(exc).__name__ + ": " + str(exc)
    finally:
        os.chdir(original_dir)
    records = getattr(algorithm, "estimation_records", [])
    for i, record in enumerate(records):
        np.savez(output / f"estimation_{i:04d}.npz", **record)
    stats = weight_summary(records)
    # A partial estimate remains diagnostic only when any requested simulation failed.
    summary.update(status=status, detail=error, diagnostics=stats,
        sampling_diagnostics=sampling_diagnostics(records),
        algorithm_requested_samples=getattr(algorithm, "submitted_samples", 0),
        phase_samples=getattr(algorithm, "phase_samples", {}),
        zero_target_samples=getattr(algorithm, "zero_target_samples", 0),
        simulator_submitted_samples=recorder.submitted if recorder else 0,
        simulator_returned_samples=recorder.returned if recorder else 0,
        original_estimate=getattr(algorithm, "last_estimate", None),
        pfail_estimate=stats["log_domain_pfail"] if status != "error" else None,
        log10_pfail=stats["log10_pfail"] if status != "error" else None,
        ranking_eligible=False)
    summary["unclassified_samples"] = recorder.submitted - recorder.returned if recorder else 0
    if rule.is_snm:
        summary["margin_failures"] = recorder.margin_failures if recorder else 0
    else:
        summary[functional_failure_key(operation)] = recorder.functional_failures if recorder else 0
        summary["timing_failures"] = recorder.timing_failures if recorder else 0
    with (output / "estimation_draws.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["round", "draw", "target_supported", "round_status", rule.column,
                         "failure_indicator", "log_target_density", "log_proposal_density"])
        for i, record in enumerate(records):
            for j, supported in enumerate(record["support"]):
                evaluated = record["status"] == "evaluated"
                delay = record["delays"][j]
                writer.writerow([i, j, bool(supported), record["status"],
                    float(delay) if supported and np.isfinite(delay) else "",
                    bool(record["indicator"][j]) if supported and evaluated else "",
                    float(record["log_f"][j]), float(record["log_g"][j])])
    (output / "run_summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False))
    text = [
        "# MNIS 接入诊断", "",
        f"- 实现：{summary['algorithm']}",
        f"- 提议分布的方差：{summary.get('proposal_covariance', 'unavailable')}",
        f"- 测评：{operation}，{rule.metric} {rule.operator} {rule.threshold_value:g} {rule.unit} 判为超限",
        f"- 状态：{status}；说明：{error}",
        f"- 代码：{revision}；未提交改动：{dirty}",
        f"- 算法预算名额：{summary['algorithm_requested_samples']}/{config['budget']}",
        f"- 分阶段请求：{summary['phase_samples']}",
        f"- 实际送入仿真：{summary['simulator_submitted_samples']}；返回：{summary['simulator_returned_samples']}",
        f"- 零目标密度样本：{summary['zero_target_samples']}，不仿真、不重抽，保留在估计分母",
        f"- 估算抽样总数：{stats['estimation_draws_evaluated']}；其中属于目标范围：{stats['supported_estimation_draws']}",
        f"- 数值状态：{stats['numeric_status']}",
        f"- 对数核对得到的估计值：{summary['pfail_estimate']}；log10：{summary['log10_pfail']}", "",
        f"- 抽样诊断：{summary['sampling_diagnostics']}",
        "平均权重远小于1或远大于1时给出诊断提示，不是算法正确与否的判决；ESS也不是准确性保证。",
        "这是接入诊断，不能据此给算法排名。没有找到初始失效点时估计留空。",
        "zero_observed_contribution表示本批估算没有非零贡献，不能证明实际失效率为零。",
        "underflow表示普通数字表示不了这么小的估计，应看log10；这也不证明估计准确。",
        "samples.csv只列实际仿真；estimation_draws.csv列全部估算抽样，目标工艺范围外的样本不填写电路测量值或判定。",
        "预算按算法请求计费，包括复测和零密度样本；它是实际仿真次数的保守上限，不能称为等实际调用数比较。",
        "算法内部的估算输出保存在 original_estimate。对数核对使用本轮报告记录的提议分布。",
    ]
    (output / "report.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("status", "algorithm_requested_samples",
          "simulator_submitted_samples", "simulator_returned_samples", "zero_target_samples",
          "pfail_estimate", "log10_pfail", "diagnostics")}, indent=2))
    print("report=" + str(output / "report.md"))
    return 1 if status == "error" else 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--initial-batch", type=int, default=2)
    p.add_argument("--initial-failures", type=int, default=1)
    p.add_argument("--estimation-batch", type=int, default=1)
    a = p.parse_args()
    try:
        return run(read_config(a.config), a.output_dir, a.initial_batch, a.initial_failures, a.estimation_batch)
    except (OSError, ValueError) as exc:
        p.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())

