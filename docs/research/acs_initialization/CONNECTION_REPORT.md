# 球面初始化接入与离线检查

本次从 `14c682a` 接入已审阅的研究方案。**完成的是程序接入与离线检查，没有新的 SRAM 仿真结果，也没有新的算法效果结论。** 现有 ACS 的正式采样、分量系数、候选更新和估计公式未改。

## 接在哪里

| 文件 | 原来怎样 | 本次改动与理由 |
| --- | --- | --- |
| `research_settings.py` | 初始化只接受已有四种方法 | 增加 ACS `variant` 模式显式选择的 `sphere_surface`；检查半径严格递增、每层批次与预算对应。默认不变 |
| `run.py` | 整理平面配置 | 在建电路前拒绝 `max(radii)*relative_std>=1` 的球面仿射配置，避免无法保证正迁移率 |
| `research.py` | 统一生成、测量和保存候选 | 逐层生成球面样本，沿用现有预算、候选判定、前四保留及复测；盒式和球面都保存实际标准化半径和方向 |
| `experiment.py`、`_process_limits.py` | 实验循环没有逐任务超时 | 显式提供 `execution` 时按进程组执行任务和整轮限制；提前保存全部计划任务和初始状态，保留超时、中断及未启动任务 |
| `analysis_plan.py` | 冻结目标和分析清单 | 保存执行限制，和实际启动使用同一个任务顺序函数 |
| `analyze_results.py` | 主要根据原生摘要判断是否可分析 | 对有限时计划读取 `execution.json`；保留原始估计，但超时或中断的任务不当作完整估计；时间协议进入预算分组 |

没有新增算法身份类别。球面是已有 `variant` 身份下的显式研究选择；`legacy` 和 MC `reference` 含义保持原状。旧配置不带 `execution` 时维持已有运行方式；未增加网页控件。

## 研究者如何指定

完整的两组配置见 [sram_comparison.json](sram_comparison.json)。它可通过现有 `normalize_experiment` 校验，仍使用同一个 `run_experiment` 入口，不再复制电路生成器或统计引擎。

球面组中的初始化配置如下。

```json
"initialization": {
  "method": "sphere_surface",
  "radii": [1.0, 2.0, 3.0, 4.0],
  "max_simulations": 64,
  "min_failed_candidates": 4,
  "on_insufficient": "stop"
}
```

半径在无量纲坐标中定义。样本为 `nominal + sigma * radius * direction`，方向由正态向量除以其长度得到，`sigma=abs(nominal)*0.05`。每层 16 点，达到 4 个失效点后结束初始化，保留发现顺序中的前四个。半径用尽仍不足就保存全部候选并返回无估计，不再扩大半径或换种子。

这份研究工况中，最大半径 4 保证初始化迁移率至少为名义值的 80%。没有剪裁或重抽。正式提议仍可能超出支持域，沿用原有“整批未完成、停止该次运行”的处理；这不是本次修复的统计策略。

## 怎样解释两组差别

**比较的是两套完整初始化方案，不能只归因于球面形状。** 18 维盒式每维在 ±4 之间，最大半径可达 `4*sqrt(18)≈16.97`，均方根半径为 `sqrt(18*16/3)≈9.80`。球面组半径最多为 4，还使用逐层搜索。形状、径向范围和搜索过程共同变化。

两组都记录真实候选坐标、标准化半径和方向，后续能查看起点具体在哪里。记录这些诊断不等于已经识别出全部物理失效区域，也不新增第三组。只有未来真实对照才能回答本工况和预算下这套初始化是否改善。

## 超时与未完成任务

配置显式提供下面的执行限制。它们用于 POSIX 主线程，包括 WSL；未验证 Windows 原生进程组执行。

```json
"execution": {
  "task_timeout_seconds": 180,
  "experiment_timeout_seconds": 1800,
  "task_kill_grace_seconds": 10,
  "experiment_kill_grace_seconds": 30,
  "order": "seed_major"
}
```

同一种子先盒式后球面，再到下一个种子。每个任务启动前就有配置、命令和执行状态记录。超时先终止整个任务进程组，宽限后强制结束；即使父进程先退出，也清理仍留在该组的孙进程。外层终止实验时也转发给独立任务进程组。

整轮时间用完后，不再启动剩余任务，清单保留它们的未启动状态。超时任务若留下原生摘要，分析器仍展示其中的原始估计与告警，同时标明超时，不计作完整有效估计。没有执行记录或记录不符合冻结计划时，也不默认为成功。

这不是新的算法停止规则。正式算法仍按固定预算结束，没有依据估计值高低提前停止或重启。初始化、复测和正式估计共用原来的成本记录。

## 实际做了哪些离线检查

新增 13 项公开检查，现有公开 Python 检查总计 64 项。开发过程的全套运行输出为 `Ran 64 tests in 5.480s`、`OK`；最终提交的干净源码复跑结果同步在当天日志。下面这些检查都不调用 Xyce。

- 18 维球面经真实工艺尺度映射后半径正确，迁移率为正。非法半径、重复或递减半径、NaN、错误预算被拒绝。
- 共享候选层加真实生产 ACS 循环的固定数学测量案例：预算 68 和 69 都只提交、返回 68 条，初始化/复测/正式为 `32+4+32`，正式分母为 32；下一整批未多生成。使用冻结的历史核方差，不为让测试通过更换提议。
- 候选不足时用完四层，保留 64 条候选，结果是无估计。已有盒式样本与原随机规则逐项一致，历史无初始化策略块的配置仍可运行。
- Python 替代进程验证任务超时后可继续下一任务、整轮超时后不再启动、外层终止转发以及忽略 TERM 的孙进程清理。
- 离线分析验证全部计划任务保留，无估计比例不会漏计，超时原始 `1.2` 不被剪裁或伪装成完整估计。

一次测试读取 `/proc` 时恰逢进程被系统回收，出现 `ProcessLookupError`。修正为识别“进程已经不存在”，没有放宽时间或数值容差。原始报错和后续通过输出都在日志。另在复核中补上历史配置没有初始化策略块的兼容分支及回归。

这些检查验证的是指定生产路径的接入、记录和停止行为，不能当成 ACS 精度认证或论文完整复现。

## 可复跑命令

在仓库根目录，激活已有 CPU 依赖环境后运行。这里只运行解析测量和 Python 替代进程。

```bash
conda activate openyield
CUDA_VISIBLE_DEVICES='' OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 PYTHONPATH="$PWD/tests:$PWD" \
  python -m unittest test_sphere_connection test_execution_limits -v
```

只校验真实对照配置，不启动任何子进程：

```bash
PYTHONPATH="$PWD" python - <<'PY'
from pathlib import Path
from unittest.mock import patch
from yield_estimation.evaluation.experiment import normalize_experiment, child_configs
with patch('subprocess.Popen', side_effect=AssertionError('No launch during validation')):
    config = normalize_experiment(Path('docs/research/acs_initialization/sram_comparison.json'))
    print('validated_tasks=', len(list(child_configs(config))))
PY
```

实际配置校验记录为 16 条任务、提交上限 2112，文件 SHA-256 为 `92585fc3ed15913333e4d7035fe9688178794f140555dbd334156f308144a142`。校验通过只说明满足程序约束，不证明电路能够仿真或预算足够。

## 真实运行仍未做什么

2,112 条独立提交上限、8 个种子、两组正式核方差、门限和超时均未改变。原 [预案数据](sram_plan.json) 作为历史方案保留；新的可校验入口配置是 `sram_comparison.json`。

启动前仍需基于最终提交做求解器、模型和独立参考指纹核对，冻结参考摘要哈希，再单独确认真实电路资源。现有 16.309% MC 参考带抽样区间，不能当作精确答案；参考条件不匹配时不自动追加 MC 或更换门限。本轮没有这项新参考核验或真实电路结果。

[启动前指纹预检](LAUNCH_PREFLIGHT.md) 已完成目标与独立参考核对；真实资源仍待确认。执行源码锁定 56320b4，报告文档提交不替代它。
