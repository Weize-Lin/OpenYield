# 第一次使用 OpenYield 良率测评

先从公开案例复算一份结果，再练习修改自己的配置。以下在 Linux 或 WSL 的 Bash 中执行，需要 Git、Conda 和 curl。作者侧演练使用了 x86_64 的 WSL 和一个新 Conda 前缀，安装时使用了已有包缓存。

## 1. 取源码并安装

```bash
workspace=$(mktemp -d "$HOME/OpenYield-first-use.XXXXXX")
git clone --depth 1 --single-branch --branch hold-snm-workflow-bd09101 \
    https://github.com/Weize-Lin/OpenYield.git "$workspace/source"
cd "$workspace/source"
git rev-parse --short HEAD
conda env create --prefix "$workspace/env" --file environment.yml
conda activate "$workspace/env"
python --version
```

应看到提交 `bd09101` 和 Python `3.9.19`。标签对应固定的历史源码，Git 显示 `detached HEAD` 是正常的。运行后续命令时请保持在 `source` 仓库根目录。若 Bash 能找到 `conda` 却无法激活环境，先运行 `source "$(conda info --base)/etc/profile.d/conda.sh"`，再重试激活。

## 2. 下载公开结果并离线复算

```bash
mkdir "$workspace/download" "$workspace/evidence" "$workspace/replay"
url=https://github.com/Weize-Lin/OpenYield/releases/download/hold-snm-workflow-bd09101
for asset in OpenYield-hold-workflow-bd09101.zip SHA256SUMS.txt; do
    curl --fail --show-error --silent --location "$url/$asset" -o "$workspace/download/$asset"
done
(cd "$workspace/download" && sha256sum -c SHA256SUMS.txt)
python -m zipfile -e "$workspace/download/OpenYield-hold-workflow-bd09101.zip" "$workspace/evidence"
evidence="$workspace/evidence/hold_workflow_bd09101"
(cd "$evidence" && sha256sum --check --quiet SHA256SUMS)
for phase in reference comparison; do
    python -m yield_estimation.evaluation.analyze_results \
        --manifest "$evidence/$phase/analysis_manifest.json" \
        --output-dir "$workspace/replay/$phase"
    diff -u "$evidence/recorded_tables/$phase/groups.csv" "$workspace/replay/$phase/groups.csv"
    diff -u "$evidence/recorded_tables/$phase/methods.csv" "$workspace/replay/$phase/methods.csv"
done
python "$evidence/verify_estimates.py" "$workspace/replay"
```

最后应看到 `12 MC estimates and 32 importance-sampling estimates`。分析命令读取已保存的摘要，校验脚本再用逐样本测量和已存密度核对估计。这个步骤只做离线分析。

## 3. 复制配置，修改后只做检查

下面把保持裕量门限改成 **340 mV**，每个方法每个种子最多提交 **24 条**，算法抽样上限也是 24。只选 MC 与 MNIS，各跑一个种子的配置。`experiment.threshold_mv` 是电路的失效门限，`estimators` 是启用的算法列表。

```bash
cp docs/research/hold_workflow_cases/comparison.json "$workspace/custom.json"
python - "$workspace/custom.json" <<'PY'
import json
import sys
from pathlib import Path
from yield_estimation.evaluation.experiment import child_configs, normalize_experiment

path = Path(sys.argv[1])
config = json.loads(path.read_text(encoding="utf-8"))
config["experiment"]["threshold_mv"] = 340.0
config["budget_per_algorithm"] = 24
config["max_algorithm_draws"] = 24
config["seeds"] = [9101]
config["estimators"] = [item for item in config["estimators"] if item["algorithm"] in ("mc", "mnis")]
validated = normalize_experiment(config)
path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
for profile, seed, child in child_configs(validated):
    print(profile["id"], seed, child["threshold_mv"], child["budget"], child["research"]["max_algorithm_draws"])
PY
```

应打印 `mc 9101 340.0 24 24` 和 `mnis 9101 340.0 24 24`。这表示配置通过了程序的检查规则。电路能否运行、门限是否适合研究目标、预算能否支撑结论，需要另行验证。评估 340 mV 下的准确性时，需要同样使用 340 mV 的独立参考。

## 4. 先看这几列结果

打开 `$workspace/replay/comparison/runs.csv`。先看 `estimator`、`seed`、`status`、`pfail_estimate`、`cost` 和 `warnings`。`pfail_estimate` 是概率，`0.1875` 就是 18.75%。

| 公开案例中的记录 | 看到了什么 |
| --- | --- |
| MC，种子 9101 | `complete`，提交并返回 48 条，估计 18.75%。 |
| MNIS，种子 9101 | `budget_stopped`，提交并返回 48 条，预算用完时有估计，约为 0.0005403%。同时出现 `low_observed_weight_mass`。 |

`low_observed_weight_mass` 表示本次正式估算样本的平均权重低于 0.1，是针对这批样本的诊断。判断估计是否偏低，要对照 `references.csv` 里的独立参考。本案例参考为 16.309%，95% 区间为 14.172%–18.697%，由此能看出这次 MNIS 估计明显偏低。权重公式是否正确、算法是否有偏，需要其他证据。

`methods.csv` 汇总每个算法多个种子的误差与无估计比例，`groups.csv` 帮你检查这些任务是否在估计同一个工况。配置更多选项见[实验配置](../../yield_estimation/evaluation/RESEARCH_EXPERIMENT.md)，表格字段见[离线结果分析](../../yield_estimation/evaluation/RESULT_ANALYSIS.md)。
