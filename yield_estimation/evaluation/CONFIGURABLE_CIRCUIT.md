# 同一套电路配置怎么用于仿真和测评

从仓库根目录开始，先复制 `cases/read_6t_configurable.json` 为自己的实验文件。
共同工况仍由外层的 `corner`、`vdd`、`temperature_c` 给出。
`circuit` 负责描述要研究的电路，测评入口另外读取工艺波动、门限、种子和预算。

| circuit 字段 | 含义 |
| --- | --- |
| `array` | 行数、列数、是否开启列复用，例如 `[3, 2, false]` |
| `target` | 从 0 开始的行列坐标。新用例省略或写 `null` 时选最后一个单元 |
| `cell_type` | 当前测评支持 `SRAM_6T_CELL` |
| `cell_6t` | 四个以米计的正尺寸和三个模型名，顺序与 main_sram.py 一致。写 `null` 时沿用单元 YAML |
| `operation` | 支持 `read`。直接 MC 也支持 `write` |
| `variation_mode` | 测评用 `custom`，算法逐批提供抽样参数 |
| `real_cell_mode` | 当前测评用 `0`，保留全部阵列晶体管 |
| `w_rc` | 布线端点的局部串联 RC 开关 |
| `interconnect_config` | 互连 YAML 路径、同结构的 JSON 对象或 `null`。路径相对于仓库根目录。测评会将 YAML 内容解析进本轮配置 |

旧的 `sram_6t_3x2_read` 用例仍固定为 3×2、目标 `[1,1]`、无列复用。
改阵列、列复用或目标位置时用 `sram_6t_read`。
直接 MC 按阵列大小决定样本长度。其余四种算法的现有边界数据支持 18、108、576 维，
即 1、6、32 个 6T 单元。选择超出范围的算法组合会在预览阶段报错。
这些是接口和数据覆盖范围，新的尺寸、互连和器件组合仍需电路验证。
HSCS 的辅助写打在最后一行最后一列，最终读失效以 `target` 指定的位置为准。

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

独立仿真沿用它自己的抽样设置。测评的 `seed`、`budget`、`batch_size` 只由测评入口使用。
例如同一份 `custom` 电路配置，独立入口从单元 YAML 取样本表，测评入口由算法生成样本。

逐条读结果在各算法的 `samples.csv`，HSCS 另有 `auxiliary_write_samples.csv`。
完整工况和阶段计数在 `run_summary.json`，多算法的并排统计在 `collection/summary.csv`。
排行榜后续基于独立 MC 参考值、多随机种子、明确的误差指标与预算口径制作。
读写组合、SNM、10T 和等效阵列还需各自的测评适配与验证。

已完成的小样本验证：

- 3×2 读 [2,0]。独立入口生成网表，五算法读取同一配置运行，
  各自存档的模型、时序、互连、驱动和等效设置一致。
  HSCS 用 4 条预算跑完 3 条读与 1 条辅助写，尚未进入最终估算。
- 2×2 读 [1,0]。直接 MC 的 2 条真实仿真均返回，每条保存 72 个器件参数。
  同一配置选择 MNIS 时，预览阶段已经报出缺少对应的抽样维度。


## 直接 MC 写测评

复制 `write_6t_configurable.json`，用 `case: sram_6t_write` 和 `circuit.operation: write`。
`threshold_ns` 是该次写测评的延迟门限。示例中的 0.10 ns 用于流程诊断，正式实验需要按研究目的设定。

```bash
python -m yield_estimation.evaluation.run_diagnostics \
  --config yield_estimation/evaluation/cases/write_6t_configurable.json \
  --algorithms mc --output-dir outputs/example_write_mc --run-xyce
```

逐样本账本是 `mc/samples.csv`，其 `write_delay_s` 列以秒记录 `TWRITE_TOTAL`。
写操作达到现有电压检查要求且延迟不超过门限，记为通过。电压证据完整但有一项不合格时，
记为 `write_functional_failure`。写入正常但超时，记为 `timing_failure`。
仿真或必要测量无法完成时，受影响批次记为 `unclassified`，整轮失效率及区间留空。
写失效各类计数和概率保存在 `mc/run_summary.json` 与 `mc/report.md`。

当前写模式只开放 MC。读模式的 HSCS 辅助写仍用来帮助搜索读失效，计入预算并单列。
