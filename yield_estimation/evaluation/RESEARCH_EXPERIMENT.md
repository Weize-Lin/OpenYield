# 配置良率算法对照实验

先选一块 SRAM 和一个失效门限，再选要对照的估计器。比如研究 MNIS 怎样挑起点，可以先跑一次，留下发现的失效点。然后让两份 MNIS 都读取这份候选表，一份按原始距离选，一份按工艺波动幅度换算距离再选。两份配置的差别就落在选点规则上。

## 从哪里开始

在项目根目录激活装有 Xyce 的 `openyield` 环境。运行 MNIS、AIS、ACS、HSCS 还需要相关 Python 依赖，参见 [权重验收](VALIDATION.md)。用已有示例启动一次小试跑。

```bash
python -m yield_estimation.evaluation.experiment \
  --config yield_estimation/evaluation/cases/research_experiment_v1.json \
  --output-dir outputs/research_example
```

Python 用的是同一个入口。`config` 可以是字典，也可以是 JSON 文件路径。相对路径以启动程序的工作目录为准。输出目录必须是新目录。

```python
import json
from pathlib import Path
from yield_estimation.evaluation.experiment import run_experiment

config = json.loads(Path("yield_estimation/evaluation/cases/research_experiment_v1.json").read_text())
config["seeds"] = [9101, 9102]
result = run_experiment(config, "outputs/research_python_example")
print(result["trials"])
```

`experiment` 里填电路、供电、温度、工艺分布和失效判据。`case`、门限单位、共享的 `circuit` 字段见 [电路配置](CONFIGURABLE_CIRCUIT.md)。示例使用 `340 mV` 做接口验收，门限是实验设定。`estimators` 写要运行的算法，`seeds` 默认为 `[0]`。同一个算法可以写两份，每份用不同的 `id`。分支支持哪些算法与电路组合依旧由各入口校验，如仿真器内部随机抽样只支持直接 MC。

## 实验预算

`budget_per_algorithm` 是每个估计器、每个种子的额度。例如五个估计器、两个种子、每次额度 100，就有十个各自独立的 100 次上限。

| `budget_unit` | 如何扣预算 |
| --- | --- |
| `algorithm_draws` | 以算法记入账本的抽样请求为上限。初始化、复测、搜索、正式估算都计入。MNIS 也计入原工艺概率密度为零的正式样本。 |
| `xyce_samples` | 以实际提交给 Xyce 的参数行数为上限。再用必填的 `max_algorithm_draws` 限制算法抽样请求，避免长时间抽到无法提交的样本。`max_algorithm_draws` 至少等于仿真上限。 |

每批整体检查额度。下一批放不下时，可能剩几次名额。仿真上限拦住一批已经生成的样本时，算法请求数和仿真提交数也可能不同。`initial_batch` 和 `estimation_batch` 控制每批大小。`estimation_draws` 可以单独限制正式抽样的次数，须为 `estimation_batch` 的整数倍。实际能用多少还取决于总预算和前面几个阶段的开销。当前入口按上限控制流程，FOM 留作诊断记录。

## 选择算法版本和初始点

| `mode` | 含义 |
| --- | --- |
| `legacy` | 使用 Oct 分支接入测评时的历史设置。历史的抽样、选点和提议分布由各算法自己处理。仍使用共享电路、判据和预算。 |
| `reference` | 目前登记的实现是按用户指定工艺分布直接抽样的 MC。用足够多且独立的样本才适合做参考。 |
| `variant` | 显式指定 MNIS、AIS、ACS 或 HSCS 的初始化、选点或正式抽样规则。 |

四个重要性抽样算法的变体须填 `initialization`。

| `initialization.method` | 候选点从哪来 |
| --- | --- |
| `legacy` | 用该算法自己的历史初始化方式产生候选点，再按变体配置选择。只在有历史边界数据的电路上可用。 |
| `target` | 按这次实验的原工艺分布抽参数。 |
| `uniform_box` | 每个参数在名义值上下的一段范围均匀抽样。`radius_sigma` 是范围相对工艺标准差的倍数，默认 3。会限制迁移率的下边界为正。 |
| `from_file` | 用已有 `initialization.json` 中的参数行。`path` 指向文件。平台核对 SHA-256、当前电路、模型、参数顺序、工艺和门限，重新仿真每条使用的参数。 |

`min_failed_candidates` 是期望至少找到的失效点数，`max_simulations` 是这一步能提交的最多行数。找不足时状态是 `initialization_insufficient`，估计值为空。最后一批若多找到了几个，候选表会保留。导入方案的历史成本单独记录，本轮复测照常扣本轮预算。候选表用于初始化，准确性参考应独立生成。

- **MNIS** 填 `center_selection.rule`，选择一个点做正式抽样中心。
- **AIS** 填 `initial_pool.rule` 与 `initial_pool.count`，选出一组起点，然后由现有 AIS 更新。正式批大小必须能整除为每个初始点同样多的样本。
- **ACS** 同样接收一组起点，仍由 ACS 构造混合分布，并把后续发现的失效点加入。
- **HSCS** 接收一组起点，继续聚类和搜索。`hscs_search_origin` 可用 `nominal` 或 `legacy`。可用 `hscs_search_max_simulations` 单独限制搜索。做读操作时，须显式设 `allow_hscs_mixed_search: true`。读测评时，HSCS 用末行末列的写仿真来指导搜索，辅助写沿用读门限的数值来筛选搜索方向。正式估算只看目标单元的读结果，表里分别记录 `write_search` 和 `estimation`。辅助写单独落在 `auxiliary_write_samples.csv`。

三种选点规则分别是 `discovery_order`、`nearest_raw`、`nearest_standardized`。后者会先把每个参数离名义值的差除以该参数在工艺分布中的标准差。调整它会改变不同参数在比较距离时的影响。

## 指定正式抽样的分散程度

- `proposal.covariance` 默认 `target_diagonal`，用原工艺参数的方差做各方向的起始尺度。`legacy` 使用历史分散尺度，可选 `legacy_variance` 显式覆盖标量方差。
- `proposal.std_scale` 乘在标准差上。例如 `0.8` 会把方差乘上 `0.64`。
- `proposal.target_fraction` 默认为 `0`。设为 `0.25` 时，每个样本有四分之一的机会从原工艺 `f` 抽取，其余从算法提议分布 `q` 抽取。程序用混合后的 `g(x) = 0.75 q(x) + 0.25 f(x)` 计算权重 `f(x) / g(x)`。HSCS 内部原有的名义中心高斯分量仍在 `q` 里。

MNIS 对目标密度为零的点会计入正式抽样分母，贡献为零，跳过 Xyce。AIS、ACS、HSCS 遇到超出目标支持范围的正式样本时会把本次运行标为错误，因为后面还依赖样本标签更新。要查原因，可看该批的参数、分布和未解决状态。

## 如何做两种规则的对照

运行示例后，从 `mnis-standardized/seed_9101/mnis/initialization.json` 取候选表。在新配置的 `estimators` 放两份 MNIS，给不同的 `id`。两份都把 `initialization.method` 改成 `from_file`，`initialization.path` 指向这份表。统一各项预算、种子、候选数量和提议分布，一份用 `nearest_raw`，另一份用 `nearest_standardized`。查看各自的 `initialization.json` 里的 `selection.selected_ids` 以及 `source_candidate_id`。两种规则有时会选中同一个点，这个结果也应保留。

## 从哪里看结果

- `experiment.json` 保留规范化配置。每次运行下面还有 `config.json`、`command.json` 和 `process.log`。
- `trials.csv` 每个估计器和种子一行，先看状态、仿真实际提交及返回、正式估算样本数和告警。`experiment_summary.json` 给 Python 使用。
- 每个算法目录的 `run_summary.json` 里有 `research_cost.phases`。能拆开看初始化、复测、HSCS 搜索和正式抽样的花费。`samples.csv` 有实际仿真分类。
- `estimation_*.npz` 带每批样本、失效标签、对数密度以及 `proposal_json`。AIS 和 ACS 每次更新中心后，下一批有自己的分布记录。需要核对权重时，从该批 `log_f - log_g` 看起。

表里出现 `estimate_above_one` 或 `low_observed_weight_mass` 就保留结果和告警做诊断。小样本下，少数权重可能撑起整个估计。`initialization_insufficient`、`no_estimation_samples` 或 `runner_error` 都应检查后再引用结果。当前表默认无排名，比较准确性还需要独立参考、更多预算和多个种子。

```bash
python -m unittest discover -s tests -p 'test_research_experiment.py' -v
```
