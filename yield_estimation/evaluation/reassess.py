"""Reclassify a completed MC run at a new delay limit without running Xyce."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path

from yield_estimation.evaluation.outcomes import (
    FUNCTIONAL_FAILURE, PASS, TIMING_FAILURE, RULE_VERSION,
    classify_read, threshold_seconds,
)
from yield_estimation.evaluation.run import CASE, CONFIGURABLE_CASE, read_config, source_revision

COLUMNS = ["sample", "batch", "xyce_run", "read_delay_s", "outcome"]


def load_source(source):
    """Check the saved ledger against its original configuration and summary."""
    raw = {name: (source / name).read_bytes()
           for name in ("run_config.json", "run_summary.json", "samples.csv")}
    config = read_config(source / "run_config.json")
    summary = json.loads(raw["run_summary.json"])
    if (summary.get("status") != "complete" or summary.get("error") is not None
            or summary.get("algorithm") != "repository_MC" or summary.get("case") not in (CASE, CONFIGURABLE_CASE)
            or summary.get("assessment_kind", "live_mc") != "live_mc"):
        raise ValueError("Source must be a completed live repository_MC evaluation")
    if any(summary.get(key) != value for key, value in config.items()):
        raise ValueError("Source configuration and summary disagree")
    reader = csv.DictReader(io.StringIO(raw["samples.csv"].decode("utf-8")))
    if reader.fieldnames != COLUMNS:
        raise ValueError("Source samples.csv has an unexpected column layout")
    rows = list(reader)
    if not rows or not (
        len(rows) == config["budget"] == summary.get("requested_samples")
        == summary.get("returned_rows") and summary.get("unclassified_samples") == 0
    ):
        raise ValueError("Source sample count, budget or completion counts disagree")
    limit = threshold_seconds(config["threshold_ns"])
    counts = {PASS: 0, TIMING_FAILURE: 0, FUNCTIONAL_FAILURE: 0}
    previous_batch = None
    batch_first = 0
    for index, row in enumerate(rows, 1):
        if row["sample"] != str(index) or None in row:
            raise ValueError("Source sample IDs must be consecutive and rows well formed")
        if row["batch"] != previous_batch:
            batch_first = index - 1
            previous_batch = row["batch"]
        if row["batch"] != f"batch_{batch_first:04d}" or row["xyce_run"] != str(index - batch_first - 1):
            raise ValueError("Source batch and simulator row IDs disagree")
        functional = row["outcome"] == FUNCTIONAL_FAILURE
        if functional and row["read_delay_s"] != "":
            raise ValueError("Confirmed functional failure must have an empty delay field")
        delay = None if functional else float(row["read_delay_s"])
        original = classify_read(delay, limit, functional_failure=functional)
        if row["outcome"] != original:
            raise ValueError(f"Source outcome disagrees with its original limit at sample {index}")
        counts[original] += 1
    for field, outcome in (("passed_samples", PASS), ("timing_failures", TIMING_FAILURE),
                           ("read_functional_failures", FUNCTIONAL_FAILURE)):
        if summary.get(field) != counts[outcome]:
            raise ValueError(f"Source summary disagrees with sample rows: {field}")
    failures = counts[TIMING_FAILURE] + counts[FUNCTIONAL_FAILURE]
    estimate = summary.get("pfail_estimate")
    if (summary.get("failure_count") != failures
            or type(estimate) not in (int, float)
            or not math.isclose(estimate, failures / len(rows), rel_tol=1e-12, abs_tol=1e-15)):
        raise ValueError("Source failure count or estimate disagrees with sample rows")
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in raw.items()}
    return config, summary, rows, hashes


def reassess(source, output, threshold_ns):
    source, output = source.resolve(), output.resolve()
    if output == source or source in output.parents:
        raise ValueError("Use a separate output directory outside the source run")
    limit = threshold_seconds(threshold_ns)
    config, original, rows, hashes = load_source(source)
    counts = {PASS: 0, TIMING_FAILURE: 0, FUNCTIONAL_FAILURE: 0}
    changes = 0
    for row in rows:
        previous = row["outcome"]
        functional = previous == FUNCTIONAL_FAILURE
        delay = None if functional else float(row["read_delay_s"])
        row["outcome"] = classify_read(delay, limit, functional_failure=functional)
        counts[row["outcome"]] += 1
        changes += row["outcome"] != previous
    failures = counts[TIMING_FAILURE] + counts[FUNCTIONAL_FAILURE]
    revision, modified = source_revision()
    summary = dict(original)
    summary.update(
        assessment_kind="offline_threshold_reassessment",
        threshold_ns=threshold_ns, source_threshold_ns=config["threshold_ns"],
        outcome_rule_version=RULE_VERSION, source_run=str(source), source_file_sha256=hashes,
        reassessment_git_revision=revision, reassessment_tracked_files_modified=modified,
        new_simulator_samples=0, changed_outcomes=changes,
        passed_samples=counts[PASS], timing_failures=counts[TIMING_FAILURE],
        read_functional_failures=counts[FUNCTIONAL_FAILURE], failure_count=failures,
        pfail_estimate=failures / len(rows), pfail_interval=None,
        interval_note="Exploratory reuse of saved samples; no confirmatory interval reported.",
        evidence_scope="Uses saved simulator classifications; does not recheck raw voltage evidence.",
    )
    output.mkdir(parents=True, exist_ok=False)
    with (output / "samples.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    (output / "run_config.json").write_text(
        json.dumps(dict(config, threshold_ns=threshold_ns), indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output / "run_summary.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8",
    )
    report = [
        "# 已有 MC 样本的门限重统计", "",
        f"- 原始运行：`{source}`",
        f"- 原始仿真代码：`{original.get('git_revision')}`",
        f"- 本次统计代码：`{revision}`",
        f"- 原门限：{config['threshold_ns']:.12g} ns；新门限：{threshold_ns:.12g} ns",
        f"- 使用已有样本：{len(rows)}；本次新增仿真样本：0",
        f"- 通过：{counts[PASS]}；超时：{counts[TIMING_FAILURE]}；已确认读功能失效：{counts[FUNCTIONAL_FAILURE]}",
        f"- 改变分类的样本：{changes}",
        f"- 这个门限下，已有样本的失效比例：**{failures}/{len(rows)} = {failures/len(rows):.4%}**", "",
        "这是同一批样本换门限后的检查，不是独立的新实验。原始结果没有覆盖。",
        "如果看过数据才挑选门限，该比例只能用于探索，不能据此宣称正式失效率或算法排名。",
        "本次沿用原报告已确认的读功能失效标签，不重新验证原始电压证据，也不改变工况或抽样分布。",
        "run_summary.json 保存三个输入文件的 SHA-256 指纹，便于以后核对所用文件。指纹不是数据真实性认证。",
    ]
    (output / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("status=complete")
    for key in ("new_simulator_samples", "changed_outcomes", "failure_count", "pfail_estimate"):
        print(f"{key}={summary[key]}")
    print(f"report={output / 'report.md'}")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--threshold-ns", type=float, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        reassess(args.source_run, args.output_dir, args.threshold_ns)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"Threshold reassessment: {exc}\n")


if __name__ == "__main__":
    main()
