"""Diagnostic entry for repository AIS/ACS/HSCS; explicit proposal variance, no clipping."""
import argparse
import csv
import json
import math
import os
from pathlib import Path
import shutil

import numpy as np
from yield_estimation.evaluation.run import (
    read_config, evaluation_circuit_options, validate_algorithm_dimensions, build_evaluation_case,
    RecordedReadTestbench, source_revision, PARAMETER_FORMAT,
)
from yield_estimation.model_lib.AIS import AIS
from yield_estimation.model_lib.ACS import ACS
from yield_estimation.model_lib.HSCS import HSCS
from yield_estimation.evaluation.mixed_search import RecordedMixedSearchTestbench
from yield_estimation.model_lib._recorded_algorithm import AlgorithmBudgetReached
from yield_estimation.evaluation.mnis_run import weight_summary, sampling_diagnostics


def run(config, output, initial_batch=2, initial_failures=1, estimation_batch=1,
        algorithm_name="ais", proposal_variance=1.0, allow_hscs_mixed_search=False):
    if algorithm_name not in ("ais", "acs", "hscs"):
        raise ValueError("Choose ais, acs or hscs")
    if algorithm_name == "hscs" and not allow_hscs_mixed_search:
        raise ValueError("HSCS uses auxiliary writes to guide read estimation; explicitly allow mixed search")
    if not math.isfinite(proposal_variance) or proposal_variance <= 0:
        raise ValueError("proposal variance must be positive and finite")
    for count in (initial_batch, initial_failures, estimation_batch):
        if type(count) is not int or count < 1:
            raise ValueError("MNIS batch sizes and initial failure count must be positive integers")
    if algorithm_name == "ais" and estimation_batch % initial_failures:
        raise ValueError("AIS estimation batch must be a multiple of initial failure count")
    options = evaluation_circuit_options(config)
    validate_algorithm_dimensions(options, algorithm_name)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    rows, cols, _ = options["array"]
    settings = dict(initial_batch=initial_batch, initial_failures=initial_failures,
                    estimation_batch=estimation_batch, strict_budget=True,
                    skip_zero_target=False, legacy_clipping=False,
                    proposal_variance=proposal_variance, algorithm=algorithm_name,
                    auxiliary_search=(dict(operation="write", target=[rows - 1, cols - 1], threshold_ns=config["threshold_ns"],
                                           find_samples=1, ratio=0.1, final_operation="read", final_target=options["target"])
                                      if algorithm_name == "hscs" else None))
    (output / "run_config.json").write_text(json.dumps(config, indent=2))
    (output / "algorithm_settings.json").write_text(json.dumps(settings, indent=2))
    (output / "samples.csv").write_text("sample,batch,xyce_run,read_delay_s,outcome\n")
    revision, dirty = source_revision()
    summary = dict(config, algorithm="repository_" + algorithm_name.upper() + "_configured_proposal",
                   git_revision=revision, git_tracked_files_modified=dirty,
                   budget_basis="submitted_requests_including_recheck",
                   config_batch_size_note="MC setting; adaptive algorithm uses algorithm_settings.json",
                   process_param_format=PARAMETER_FORMAT, settings=settings)
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
        recorder_class = RecordedMixedSearchTestbench if algorithm_name == "hscs" else RecordedReadTestbench
        recorder = recorder_class(bench, output, config)
        if algorithm_name == "ais":
            algorithm = AIS(target, recorder, 1, False, len(means),
                            g_cal_num=proposal_variance, initial_failed_data_num=initial_failures,
                            num_generate_each_norm=estimation_batch // initial_failures,
                            sample_num_each_sphere=initial_batch, seed=config["seed"])
        elif algorithm_name == "acs":
            algorithm = ACS(recorder, len(means), 1, False, target, proposal_variance,
                            initial_failures, initial_batch, estimation_batch, 10, config["seed"])
        else:
            algorithm = HSCS(target, recorder, means, len(means), g_var_num=proposal_variance,
                IS_sample_num=estimation_batch, initial_failed_data_num=initial_failures,
                sample_num_each_sphere=initial_batch, find_MN_sam_num=1, seed=config["seed"],
                IS_bound_on=False, num_rows=rows, num_cols=cols)
        algorithm.read_target = tuple(options["target"])
        algorithm.threshold = config["threshold_ns"] * 1e-9
        os.chdir(output)
        algorithm.start_estimate(config["budget"], strict_budget=True)
        status = "stopped"
    except AlgorithmBudgetReached as exc:
        status = "budget_stopped"
        error = str(exc)
    except (Exception, KeyboardInterrupt) as exc:
        status = "error"
        error = type(exc).__name__ + ": " + str(exc)
    finally:
        os.chdir(original_dir)
    for i, record in enumerate(getattr(algorithm, "request_records", [])):
        np.savez(output / f"request_{i:04d}.npz", **record)
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
        auxiliary_write_submitted=getattr(recorder, "write_submitted", 0),
        auxiliary_write_returned=getattr(recorder, "write_returned", 0),
        original_estimate=getattr(algorithm, "last_estimate", None),
        pfail_estimate=stats["log_domain_pfail"] if status != "error" else None,
        log10_pfail=stats["log10_pfail"] if status != "error" else None,
        ranking_eligible=False)
    with (output / "estimation_draws.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["round", "draw", "target_supported", "round_status", "read_delay_s",
                         "failure_indicator", "log_target_density", "log_proposal_density"])
        for i, record in enumerate(records):
            for j, supported in enumerate(record["support"]):
                evaluated = record["status"] == "evaluated"
                delay = record["delays"][j]
                writer.writerow([i, j, bool(supported), record["status"],
                    float(delay) if supported and np.isfinite(delay) else "",
                    bool(record["indicator"][j]) if supported and evaluated else "",
                    float(record["log_f"][j]), float(record["log_g"][j])])
    original = summary["original_estimate"]
    if original:
        summary["original_estimate"] = {k: str(v) if isinstance(v, float) and not math.isfinite(v) else v for k, v in original.items()}
    (output / "run_summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False))
    text = [f"# {algorithm_name.upper()} 接入诊断", "",
        f"- 状态：{status}；说明：{error}",
        f"- 版本：{revision}；未提交改动：{dirty}",
        f"- 预算：{config['budget']}；提交请求：{summary['algorithm_requested_samples']}",
        f"- 阶段计数：{summary['phase_samples']}",
        f"- 实际送入仿真：{summary['simulator_submitted_samples']}；返回：{summary['simulator_returned_samples']}",
        f"- 抽样方差：{proposal_variance}（算法设置，不是目标工艺分布）",
        f"- 数值状态：{stats['numeric_status']}；估计：{summary['pfail_estimate']}；log10：{summary['log10_pfail']}",
        f"- 抽样诊断：{summary['sampling_diagnostics']}", "",
        "调用仓库原算法类，增加预算与记录；AIS 修正每批多抽样时的分母。",
        "没有更换初始均匀抽样或中心更新方法。方差由用户显式选择，实际设置在 algorithm_settings.json。",
        "初始化、复测、估算全部计入预算。预算不足执行完整下一批时停止。",
        "非法参数和无法判定的仿真不会当成通过或电路失效，不会补抽。",
        "request_*.npz 保留每次请求，estimation_*.npz 和 estimation_draws.csv 保留估算密度与标签。",
        "samples.csv 记录实际仿真。原公式结果在 original_estimate，对数核对值另列。",
        "这是接入测试，不是算法排名。少量样本下即使进程成功，也可能没有可靠估计。"]
    if algorithm_name == "hscs":
        text += ["", f"辅助写测试：提交 {summary['auxiliary_write_submitted']}，返回 {summary['auxiliary_write_returned']}。",
                 f"读 {options['target']} 找点；写 {[rows - 1, cols - 1]} 辅助选位置；最终估计读 {options['target']} 的失效概率。",
                 "写测试计入总预算，在 auxiliary_write_samples.csv 单列；不计入读失效条数或估算分母。",
                 "沿用原来的同一门限用于辅助写搜索；这不是独立的写良率测试，也未声称这种选点策略有效。",
                 "原算法的球面搜索几何未重新设计；参数范围或仿真证据不满足要求时如实停止。"]
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
    p.add_argument("--algorithm", choices=("ais", "acs", "hscs"), required=True)
    p.add_argument("--proposal-variance", type=float, default=1.0)
    p.add_argument("--allow-hscs-mixed-search", action="store_true")
    a = p.parse_args()
    try:
        return run(read_config(a.config), a.output_dir, a.initial_batch, a.initial_failures, a.estimation_batch,
                   a.algorithm, a.proposal_variance, a.allow_hscs_mixed_search)
    except (OSError, ValueError) as exc:
        p.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())

