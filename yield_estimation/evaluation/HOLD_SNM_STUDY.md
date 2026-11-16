# 单单元 6T 保持裕量对照实验

保持裕量（hold SNM）表示单元保住已存数据时能承受多大的电压扰动。本用例把裕量低于 327.65 mV 的样本判为失效。

## 从仓库根目录运行

激活 `openyield` 环境。先用 `command -v Xyce` 和 `python -c 'import torch; print(torch.__version__)'` 检查依赖。这次运行使用 Python 3.9.19、NumPy 1.26.4、SciPy 1.13.1、PyTorch 2.8.0+cpu 和 Xyce 7.4 开发版（`gb7bb12d8`）。
Linux 或 WSL 环境若缺少 PyTorch，可从官方 CPU 软件源安装。 [PyTorch 历史版本安装说明](https://pytorch.org/get-started/previous-versions/)

```bash
python -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
```

预览只列计划命令。加 `--run-xyce` 正式跑，输出目录必须是新目录。

```bash
python -m yield_estimation.evaluation.hold_snm_study --output-dir outputs/hold_snm_study --jobs 4
python -m yield_estimation.evaluation.hold_snm_study --output-dir outputs/hold_snm_study --jobs 4 --run-xyce
```

如果只想先看一次 Xyce 输出，可以用随仓库提供的 [少样本 MC 示例](cases/hold_snm_6t_configurable.json)。它采用自己的示例门限。

```bash
python -m yield_estimation.evaluation.run_diagnostics \
  --config yield_estimation/evaluation/cases/hold_snm_6t_configurable.json \
  --algorithms mc --output-dir outputs/hold_snm_first --run-xyce
```

## 实验条件

- 电路为 1×1 6T，TT 工艺角，1 V，27 °C。每条样本有六只管子的 `vth0`、`u0`、`voff`，共 18 个量。独立工艺波动的标准差是名义值绝对值的 5%，迁移率限定为正。完整输入见 [固定配置](cases/hold_snm_6t_study.json)。
- 用种子 8201 的 128 条先导样本选保持裕量的 15% 分位并取到 0.01 mV。取到 327.65 mV 时，20 条低于门限，先导样本仅用于选门限。
- 正式参考用种子 8311–8314，各跑 256 条独立 MC。算法比较用种子 9101–9108，每个算法每次给 48 条仿真预算。相同种子编号可以重复运行，算法的抽样规则各不相同。
- 四种重要性抽样算法从名义参数两侧各三个标准差的范围内开始找初始失效，初始批次 4 条，需要找到 1 条失效。估算阶段每批 1 条。MNIS 使用配置化提议分布，HSCS 从名义点搜索，AIS、ACS、HSCS 的配置方差为 `1e-5`。这是本次参数设置。
- 初始搜索和复测都计入预算。报告保存请求名额、实际提交和返回数。失效概率没有结果、工况不同或实际仿真不足 48 条，会在该种子留下原因，算法汇总误差留空。

## 先看哪些文件

| 文件 | 看什么 |
| --- | --- |
| `report.md` | 独立 MC 参考值、95% 置信区间、五算法误差和跨种子波动 |
| `per_seed.csv` | 每轮实际仿真条数、最终估算条数、失效率、平均权重的对数、权重有效样本数及告警 |
| `by_algorithm.csv` | 每种算法的平均绝对误差、均方根误差（RMSE）及八次估计的标准差 |
| `study_plan.json`、`study_runs.csv` | 冻结的命令、配置、退出码和报告校验值 |
| 各子目录的 `samples.csv` | 一条仿真对应一行实际测量与失效标签 |

平均绝对误差看的是每个种子的估计和 MC 参考点相差多少，RMSE 对偏得较远的几次更敏感。参考点也有抽样误差。单一工况、一个预算和八个种子的结果，适合核对这一组设置的表现。

## 实测记录

失效定义为保持裕量低于 327.65 mV。
独立 MC 参考为 142/1024（13.867%），95% Wilson 区间 11.885%–16.119%。

每个算法跑 8 个种子，每次预算 48 条，包含找失效点和复测。
只有实际仿真次数齐全、工况一致且有估计的种子才计入，缺任一个种子就留空该算法的汇总误差。
误差以参考点估计为准，参考值本身也有抽样误差。结果适用于本次工况和报告所列算法设置。

| 算法 | 可比较的种子 | 平均估计 (%) | 跨种子标准差 (百分点) | 平均绝对误差 (百分点) | RMSE (百分点) |
| --- | ---: | ---: | ---: | ---: | ---: |
| mc | 8/8 | 14.323 | 5.391 | 4.085 | 5.063 |
| mnis | 8/8 | 6.17e-06 | 1.47e-05 | 13.867 | 13.867 |
| ais | 8/8 | 2.44e-11 | 6.49e-11 | 13.867 | 13.867 |
| acs | 8/8 | 6.19e-09 | 1.24e-08 | 13.867 | 13.867 |
| hscs | 8/8 | 4.39e-04 | 0.001 | 13.867 | 13.867 |

逐个种子的诊断、提交与返回数见 per_seed.csv。
study_plan.json 保存实验配置和原始命令。并行运行的耗时受资源竞争影响，表格按仿真样本条数比较。

参考批次按种子 8311–8314 的失效条数分别为 34、37、43、28。
PDK 模型 SHA-256 为 `40fbaa38ec61aa2b3917754dfbc12ff34be47208940359acbf4a55a10ead7cc0`。

四个重要性抽样算法的 32/32 轮触发低平均权重告警，这些轮的权重有效样本数为 1.00–2.90。实际仿真全部返回，结合权重可见本次设置下的估计主要由极少数样本决定。这是这组工况、预算和算法设置下观察到的结果。
