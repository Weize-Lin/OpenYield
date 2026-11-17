# 离线统一结果分析

把准备比较的运行和独立 MC 参考写进同一份清单。分析只读取已有 `run_summary.json`，可同时看同题的不同算法、不同策略和不同实际成本。产物没有排行榜。

## 四项检查

| 检查 | 当前做法 |
| --- | --- |
| 目标定义 | 核对已解析电路、操作、目标单元、工艺抽样方式与参数顺序、模型文件摘要、外围和时序、失效判据及门限。每组按清单顺序，用第一个来源摘要未损坏、种子匹配且目标字段完整的条目作为基准，失败运行也能定义目标。记录 `same`、`different`、`unknown` 和具体字段。只有目标匹配的数值参与该组误差统计。 |
| 算法策略 | 单独保存实现名、算法身份、历史运行的初始化和提议字段，以及新版的完整估计器配置。同名估计器的不同策略各有一行；在同组比较不同策略时也可选用不同的估计器名称。 |
| 成本协议 | 分开列预算口径与上限、抽样和正式样本上限、已知时间限制、实际请求、提交、返回与正式分母。组表分别核对它们。历史配置不明确的预算协议标为 `unknown`，即便保存的实际仿真次数相同。按目标、策略、预算协议与档位拆开计算跨种子波动和误差。同一上限内实际成本不同的记录继续保留。 |
| 证据来源 | 保留输入摘要路径与 SHA-256、源码版本、未提交标记、模型及求解器字段。分析同时核对摘要中的 Xyce 版本和 SHA-256，两者用于判断求解器是否相同。安装路径可以不同。旧摘要只有路径时仍标记来源不完整。未提交源码不能只凭提交号还原。不同版本需要作为来源差异披露；相同版本但来源不完整时仍标未验证。 |

## 清单和命令

把下面内容存为 `outputs/analysis_inputs.json`，各个 `run` 和 `reference_runs` 条目指向包含 `run_summary.json` 的目录。相对路径从清单目录解析。逐个登记计划比较的种子，摘要还未生成的目录也可登记，缺失会计入该方法的总运行数和无估计数。可为某条 `run` 增加 `sha256`，用于核对冻结摘要。手写清单会被标为 `manual_manifest`，软件无法确认手写清单覆盖了原计划。

```json
{
  "schema_version": 1,
  "groups": [
    {
      "id": "hold_snm_case",
      "runs": [
        {"estimator": "mc", "seed": 9101, "run": "hold_study_protocol2/trials/9101/mc"},
        {"estimator": "mnis", "seed": 9101, "run": "hold_study_protocol2/trials/9101/mnis"}
      ],
      "reference_runs": [
        "hold_study_protocol2/reference/8311/mc",
        "hold_study_protocol2/reference/8312/mc",
        "hold_study_protocol2/reference/8313/mc",
        "hold_study_protocol2/reference/8314/mc"
      ]
    }
  ]
}
```

在项目根目录执行，选择新的输出目录：

```bash
conda activate openyield
python -m yield_estimation.evaluation.analyze_results \
  --manifest outputs/analysis_inputs.json --output-dir outputs/hold_comparison
```

正式运行时用实验程序自动生成分析清单。参考先独立运行，再在开始比较前锁定路径、种子和摘要哈希。

```bash
python -m yield_estimation.evaluation.experiment \
  --config comparative_experiment.json --output-dir outputs/comparative_run \
  --reference-run outputs/independent_mc_a/mc --reference-run outputs/independent_mc_b/mc
python -m yield_estimation.evaluation.analyze_results \
  --manifest outputs/comparative_run/analysis_manifest.json --output-dir outputs/comparative_report
```

第一次启动子进程前会写出 `experiment.json`、`analysis_plan.json` 和 `analysis_manifest.json`。请一起保留三份文件。分析时对照计划检查方法和种子是否被漏填、加入、改路径或换序，并检查参考是否被替换。 各条摘要还会逐项核对冻结的电路、工艺参数与失效判据。即使所有摘要记录了同一个新门限，也会标记偏离计划，排除在组内统计之外。还没产生摘要的计划条目会进入原订预算档位，计为无估计。手写清单也可用对象 `{ "run": "path", "sha256": "摘要的 SHA-256", "seed": 123 }` 登记参考。历史仅有路径的清单继续可读，并标明参考哈希未冻结。

参考必须显式指定。只接受完整的直接 MC 自定义工艺抽样，目标一致，种子在参考内部不重复，且和比较组的声明种子及目录分开。该检查防止直接复用同一批抽样记录，样本的来源质量仍要结合来源栏判断。参考结果合计失效数及样本数，给出 95% Wilson 区间。参考缺失或不合格时仍保留运行记录，误差栏留空。

## 如何读表

- `runs.csv` 保存每次原始估计、对数值、状态、告警、算法参数、成本和摘要证据。低权重和超 1 的有限估计照实进入误差。只剩对数估计时保留对数和上下溢标记，线性误差留空。目标不符的运行仍保留原始估计，组内误差留空。
- `groups.csv` 汇总目标匹配数、来源及成本缺口，分开报告协议、实际仿真次数与时间限制。历史报告明确数值不同与信息不足会显示不同状态。
- `references.csv` 给出参考计数、区间、来源和检查结果。旧来源不完整时可以看到描述性误差，不能据此确认可复现的等成本比较。
- `methods.csv` 每个预算档位和策略分别报告总数、可用估计、无估计比例及原因、参与线性统计和误差的条数、跨种子标准差、MAE 与 RMSE。`estimator_planned_runs` 和 `estimator_no_estimate_fraction` 保留整个方法的分母和无估计比例。误差相对参考点值计算。区间是**参考值**的区间。只有一条可用记录时标准差留空。`errors_with_unverified_or_different_source` 标明有多少误差来自来源未验证或不相同的记录。

`analysis.json` 保存同样的完整结构，`input_manifest.json` 保留原始清单。清单内相对路径的解析基准仍是原清单目录。

## 自带离线示例

从新检出的仓库安装依赖后，可以直接复跑合成示例。输入由脚本生成，无需历史项目输出或 Xyce。

```bash
conda env create -f environment.yml
conda activate openyield
CUDA_VISIBLE_DEVICES='' OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 python -m unittest discover -s tests -v
PYTHONPATH="$PWD" python docs/research/offline_result_example.py --output-dir outputs/offline_analysis_example
```

示例输出在 `outputs/offline_analysis_example/report/`。两条预算 20 的记录实际用 20 和 19 条，仍在同一预算档位；预算 30 单列；没有摘要的记录计入无估计。数据是说明格式的人工记录，不能用于评价算法。

## 验收

历史 327.65 mV Hold 组保留了 40 条运行及其中 32 条低权重告警，全部计算了描述性误差。独立参考复算得到 142/1024，即 13.867%，95% Wilson 区间约 11.885%–16.119%。组内实际提交数一致，历史预算协议与求解器版本有缺口，原运行标有未提交改动。

340 mV 小示例识别为同目标和同仿真上限，实际成本分别为 22 和 13，MNIS 原估计 `2.00568` 保留并标记超 1。另一个候选不足组为 1/1 无估计。把这条候选不足记录放入 340 mV 的负对照时，门限差异被正确标出。以上均读取已有结果。

用于新环境的公开回归使用合成摘要，还检查独立参考种子复用、哈希不匹配、门限和电路差异及两种成本口径。

```bash
python -m unittest discover -s tests -p 'test_result_analysis.py' -v
```
