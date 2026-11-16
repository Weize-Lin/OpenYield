"""Compare completed diagnostic MC/MNIS reports without producing an algorithm ranking."""
import argparse
import hashlib
import json
from pathlib import Path

CONDITIONS = ("case", "corner", "vdd", "temperature_c", "relative_std", "u0_positive",
              "model_sha256", "driver_sizes", "timing", "interconnect", "equivalent")


def condition_mismatches(left, right):
    fields = [k for k in CONDITIONS if k not in left or k not in right or left[k] != right[k]]
    threshold = "threshold_mv" if "threshold_mv" in left or "threshold_mv" in right else "threshold_ns"
    if threshold not in left or threshold not in right or left[threshold] != right[threshold]:
        fields.append(threshold)
    if left.get("initialization_sigma") != right.get("initialization_sigma"):
        fields.append("initialization_sigma")
    if left.get("resolved_circuit") != right.get("resolved_circuit"):
        fields.append("resolved_circuit")
    return fields


def compare(mc_dir, mnis_dir, output):
    mc_dir, mnis_dir, output = mc_dir.resolve(), mnis_dir.resolve(), output.resolve()
    for source in (mc_dir, mnis_dir):
        if output == source or source in output.parents:
            raise ValueError("Comparison output must be separate from source runs")
    raw = [(d / "run_summary.json").read_bytes() for d in (mc_dir, mnis_dir)]
    mc, mnis = [json.loads(data) for data in raw]
    if mc.get("algorithm") != "repository_MC" or mnis.get("algorithm") not in (
            "repository_MNIS_original_proposal", "repository_MNIS_configured_proposal"):
        raise ValueError("Expected custom-sampling repository MC and repository MNIS reports")
    if mc.get("status") != "complete" or mnis.get("status") not in ("budget_stopped", "stopped"):
        raise ValueError("Only completed diagnostic runs can be compared; inspect failed reports separately")
    mismatches = condition_mismatches(mc, mnis)
    mc_calls = mc["requested_samples"]
    mnis_calls = mnis["simulator_submitted_samples"]
    numerical = mnis["diagnostics"]["numeric_status"]
    warnings = ["diagnostic_only_no_ranking", "small_MC_is_not_ground_truth"]
    if mismatches:
        warnings.append("experiment_conditions_differ")
    if mc["budget"] != mnis["budget"]:
        warnings.append("configured_budget_differs")
    if mc_calls != mnis_calls:
        warnings.append("actual_simulator_sample_counts_differ")
    if numerical in ("underflow", "overflow", "no_estimation", "zero_observed_contribution"):
        warnings.append("MNIS_" + numerical)
    if numerical == "underflow":
        mnis_display = f"10^({mnis['diagnostics']['log10_pfail']:.6f})（普通表示下溢）"
    elif mnis.get("pfail_estimate") is None:
        mnis_display = "无可用普通数值估计"
    else:
        mnis_display = str(mnis["pfail_estimate"])
    result = dict(
        kind="diagnostic_comparison", ranking_eligible=False, condition_mismatches=mismatches,
        warnings=warnings,
        sources=[dict(path=str(path), summary_sha256=hashlib.sha256(data).hexdigest(),
                      revision=summary.get("git_revision"))
                 for path, data, summary in zip((mc_dir,mnis_dir),raw,(mc,mnis))],
        mc=dict(budget=mc["budget"], requested_samples=mc_calls, returned_samples=mc["returned_rows"],
                failure_count=mc["failure_count"], pfail_estimate=mc["pfail_estimate"],
                interval=mc["pfail_interval"]),
        mnis=dict(implementation=mnis["algorithm"], budget=mnis["budget"],
                  algorithm_requests=mnis["algorithm_requested_samples"],
                  simulator_submitted_samples=mnis_calls,
                  simulator_returned_samples=mnis["simulator_returned_samples"],
                  zero_target_samples=mnis["zero_target_samples"],
                  estimation_draws=mnis["diagnostics"]["estimation_draws_evaluated"],
                  numeric_status=numerical, displayed_estimate=mnis_display),
    )
    report = [
        "# MC 与 MNIS 的诊断对照", "",
        f"MNIS 实现：{mnis['algorithm']}。", "",
        "此表检查接入结果，不产生排名。门限和预算若用于调试，不能当成正式设计指标。", "",
        "| 项目 | MC | MNIS |", "| --- | --- | --- |",
        f"| 配置预算名额 | {mc['budget']} | {mnis['budget']} |",
        f"| 实际送入仿真的样本 | {mc_calls} | {mnis_calls} |",
        f"| 实际返回的样本 | {mc['returned_rows']} | {mnis['simulator_returned_samples']} |",
        f"| 零目标密度而不仿真的样本 | 0 | {mnis['zero_target_samples']} |",
        f"| 估计结果 | {mc['pfail_estimate']}（失效{mc['failure_count']}/{mc['returned_rows']}） | {mnis_display} |",
        f"| 仿真代码提交 | {mc.get('git_revision')} | {mnis.get('git_revision')} |", "",
        f"固定工况不同的字段：{mismatches or '无'}。",
        f"检查提示：{', '.join(warnings)}。", "",
        "MC按目标工艺直接抽样；MNIS按报告标明的提议分布抽样，再用权重估算，不能直接拿它的失效条数相除。",
        "MNIS预算含初始化、复测、估算和零目标密度样本；表中另列实际仿真，不能称为等实际仿真次数比较。",
        "同一个seed不表示两种算法用了相同样本。小样本MC结果仅供诊断，不是精确真值。",
        "若MNIS下溢，不能把输出的零理解为没有电路失效，也不能仅凭对数值极小就认定估计准确。",
    ]
    output.mkdir(parents=True, exist_ok=False)
    (output / "comparison.json").write_text(json.dumps(result, indent=2, allow_nan=False))
    (output / "report.md").write_text("\n".join(report)+"\n",encoding="utf-8")
    print(json.dumps(result,indent=2,ensure_ascii=False))
    print("report="+str(output/"report.md"))
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mc-run",type=Path,required=True)
    p.add_argument("--mnis-run",type=Path,required=True)
    p.add_argument("--output-dir",type=Path,required=True)
    a=p.parse_args()
    try:
        compare(a.mc_run,a.mnis_run,a.output_dir)
    except (OSError,ValueError,KeyError,TypeError) as exc:
        p.exit(2,str(exc)+"\n")


if __name__=="__main__":
    main()

