# 同一套电路配置怎么用于仿真和测评

从仓库根目录开始，先复制 `cases/read_6t_configurable.json` 为自己的实验文件。
共同工况仍由外层的 `corner`、`vdd`、`temperature_c` 给出。
`circuit` 负责描述要研究的电路，测评入口另外读取工艺波动、门限、种子和预算。

| circuit 字段 | 含义 |
| --- | --- |
| `array` | 行数、列数、是否开启列复用，例如 `[3, 2, false]` |
| `target` | 从 0 开始的行列坐标。新用例省略或写 `null` 时选最后一个单元 |
| `cell_type` | `SRAM_6T_CELL` 或 `SRAM_10T_CELL`，和外层 `case` 对应。留空沿用用例的类型 |
| `cell_6t` | 四个以米计的正尺寸和三个模型名，顺序与 main_sram.py 一致。写 `null` 时沿用单元 YAML。10T 用例沿用 10T YAML |
| `operation` | 六种操作见下表 |
| `variation_mode` | `custom`、`nominal`、`shared`、`per-device`，用法见下文 |
| `real_cell_mode` | `0` 保留全部阵列晶体管，`1` 到 `4` 用 OpenYield 的等效阵列模型。显式写 `null` 时读取并记下全局 YAML 的实际模式 |
| `w_rc` | 布线端点的局部串联 RC 开关 |
| `interconnect_config` | 互连 YAML 路径、同结构的 JSON 对象或 `null`。路径相对于仓库根目录。测评会将 YAML 内容解析进本轮配置 |

旧的 `sram_6t_3x2_read` 用例仍固定为 3×2、目标 `[1,1]`、无列复用。
改阵列、列复用或目标位置时用 `sram_6t_read`。
用例名为 `sram_6t_<操作>` 或 `sram_10t_<操作>`，连续读写的操作后缀为 `read_write`。
自定义样本按行、列、管子排好，每只管子写入 `vth0`、`u0`、`voff` 三项。
一个 6T 单元用 18 项，10T 用 30 项。2×2 的 10T 读写每条样本共 120 项。
电路尺寸、互连和器件组合的测量是否正常，以实际运行的样本账本为准。
在读用例中，HSCS 的辅助写打在最后一行最后一列，最终读失效以 `target` 指定的位置为准。

先预览算法与配置，再用新输出目录加 `--run-xyce` 开始仿真。

```bash
python -m yield_estimation.evaluation.run_diagnostics \
  --config yield_estimation/evaluation/cases/read_6t_configurable.json \
  --algorithms mc mnis ais acs hscs --allow-hscs-mixed-search \
  --proposal-variance 0.000001 --output-dir outputs/configurable_read
```

保存后的 `experiment.json` 包含完整的解析后电路配置。
传统的独立仿真入口也可读取同一份明确电路选项，例如只生成网表：

```bash
python main_sram.py --circuit-config outputs/configurable_read/experiment.json --deck-only
```

独立仿真沿用它自己的抽样设置。测评的 `seed`、`budget`、`batch_size` 只由测评入口使用。它们对应单次抽样种子、每个算法的名额上限和直接 MC 分批条数。
例如同一份 `custom` 电路配置，独立入口从单元 YAML 取样本表，测评入口由算法生成样本。

逐条读结果在各算法的 `samples.csv`，HSCS 另有 `auxiliary_write_samples.csv`。
完整工况和阶段计数在 `run_summary.json`，多算法的并排统计在 `collection/summary.csv`。
排行榜后续基于独立 MC 参考值、多随机种子、明确的误差指标与预算口径制作。
## 选择如何产生样本

| 模式 | 样本怎么来 | 运行设置与输出 |
| --- | --- | --- |
| `custom` | 由算法提供位单元的参数表。每项按当前模型卡的名义值和 `relative_std` 设置波动，迁移率参数保持正数 | `u0_positive: true`。完整阵列可选五个算法，等效阵列可选 MC。每批保留 `samples.npy` |
| `nominal` | 用模型卡的名义参数运行一次 | `--algorithms mc`，`budget: 1`。报告给出单次是否通过 |
| `shared` | Xyce 抽样，同一模型卡的器件共用参数 | `--algorithms mc`，`u0_positive: false`，`budget >= 2` |
| `per-device` | Xyce 为保留的每只 MOS 管构造独立的随机参数 | `--algorithms mc`，`u0_positive: false` |

`shared` 和 `per-device` 用 Xyce 的分层抽样。设置 `batch_size >= budget`，
并使用正的 32 位整数种子，让一轮只提交一次 `.SAMPLING`。
表里保留每条测量和通过情况，报告给出观测失效比例。
Wilson 区间用于直接从目标工艺独立抽样的 `custom` MC。

等效模式用简化负载表示部分阵列。模式 1 保留目标行和列，2 只保留目标行，3 只保留目标列，4 只保留目标单元。报告保存实际模式和 `approximation`。
当前等效模式的测评走 MC。三种 SNM 都用独立单元及 `real_cell_mode: 0`。

## 选择初始化范围

6T 的 18、108、576 维用例可以沿用仓库的历史边界。
10T 或其他尺寸若要运行 MNIS、AIS、ACS、HSCS，在外层写 `"initialization_sigma": 3`。
这表示每项围绕自己的名义值向两侧各取三个标准差，作为找第一批失效点的范围。
这个范围不改变 MC 要估计的工艺分布。报告保存每项的实际搜索上下限。

这个开关也让 MNIS 的抽样方差按目标工艺逐项设定，让 HSCS 从名义点出发搜索。
报告注明使用哪套设置。AIS、ACS、HSCS 的后续抽样方差仍由 `--proposal-variance` 给定。
需要对照历史设置时用原 6T 用例，省略 `initialization_sigma`。

用 10T 保持裕量做一次五算法检查时，可以复制 `cases/hold_snm_10t_configurable.json`，
设 `initialization_sigma: 3`、`budget: 9`、`threshold_mv: 700`，
把五个算法都选上并传 `--proposal-variance 1e-8`。这里故意把失效门限放在容易找到失效的位置，
用来检查从初始化到估算的过程。该门限和方差都属于这一次调试设置。

已完成的小样本验证：

- 3×2 读 [2,0]。独立入口生成网表，五算法读取同一配置运行，
  各自存档的模型、时序、互连、驱动和等效设置一致。
  HSCS 用 4 条预算跑完 3 条读与 1 条辅助写，尚未进入最终估算。
- 2×2 读 [1,0]。直接 MC 的 2 条真实仿真均返回，每条保存 72 个器件参数。
  同一配置可用 `initialization_sigma` 为 MNIS 指定适合 72 项参数的搜索范围。


## 选择操作和门限

每次运行的配置只选一个操作。所有启用的算法使用该操作对应的测量规则。
10T 的例子把表中的 `_6t_` 换成 `_10t_`。

| 操作 | `cases/` 下的示例 | 配置门限 | 逐样本数值 |
| --- | --- | --- | --- |
| `read` | `read_6t_configurable.json` | `threshold_ns`，读延迟上限 | `read_delay_s` |
| `write` | `write_6t_configurable.json` | `threshold_ns`，写延迟上限 | `write_delay_s` |
| `read&write` | `read_write_6t_configurable.json` | `threshold_ns`，输出周期上限 | `output_period_s` |
| `hold_snm` | `hold_snm_6t_configurable.json` | `threshold_mv`，保持裕量下限 | `hold_snm_v` |
| `read_snm` | `read_snm_6t_configurable.json` | `threshold_mv`，读裕量下限 | `read_snm_v` |
| `write_snm` | `write_snm_6t_configurable.json` | `threshold_mv`，写裕量下限 | `write_snm_v` |

- 读写组合沿用 OpenYield 的写 1、读、写 0、读序列。时间判据使用底层 `TVOUT_PERIOD`，
  即 `OUT` 两次连续上升跨越的间隔；每个受检读写周期另有读回、存储状态和恢复电压检查。
- 读、写和读写组合的时间量超过上限记为时序失效，有完整电压证据的访问失效单独记。
  SNM 小于下限记为裕量失效。测量值恰好等于门限时通过。
- SNM 用例固定单个单元 `array: [1, 1, false]` 和 `target: [0, 0]`。
  门限以 mV 配置，账本中实际裕量以 V 保存。
  若静态扫描无法确定裕量，受影响批次记为无法判定，保留 Xyce 输出和解析调试表。
- HSCS 读用例显式使用 `--allow-hscs-mixed-search`，辅助写记录另存，费用计入预算。
  其他五种操作用所选电路操作搜索失效区域。搜索费用计入预算，只有最终估算阶段参与概率估计。
- 若算法因仿真或证据缺失而中断，保留已完成的账本，不从剩余样本推算整体失效率。
  启动成功、小样本下溢或没有非零权重都不能用于排名。

例子是在单个 6T 单元上用 MC 测读 SNM：

```bash
python -m yield_estimation.evaluation.run_diagnostics \
  --config yield_estimation/evaluation/cases/read_snm_6t_configurable.json \
  --algorithms mc --output-dir outputs/example_read_snm --run-xyce
```

将 `--algorithms mc` 换成所需算法列表即可。读写或 SNM 的门限示例用于检查流程，
实验用值应按研究工况设置。运行后可查看各算法的 `samples.csv`、
`run_summary.json`、`report.md`，以及多算法运行的 `collection/summary.csv`。
