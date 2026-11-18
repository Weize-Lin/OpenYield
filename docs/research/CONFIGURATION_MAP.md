# 良率测评可配置项与实际生效路径

本清单记录开发分支 `codex/benchmark-oct01` 的 `c421253`，只读检查入口、校验和参数消费点。公开的真实电路流程证据是 `bd09101` 的 1×1 6T 保持裕量案例。下面所说的“可配”表示代码有读取或校验路径。不同组合的电路能否完成仿真、现有估计结果能否支撑科学结论，仍看各自的实测证据。

## 一、研究人员从哪里开始

| 任务          | 现有入口                                                                  | 输入与沿途文件                                                                                                                                                             |
| ----------- | --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 改一组实验设置，先检查 | `yield_estimation.evaluation.experiment.normalize_experiment(config)` | 收 JSON 路径或 Python 字典。展开每个算法和种子，校验电路和组合限制。返回解析后的配置。                                                                                                                  |
| 运行统一测评      | `python -m yield_estimation.evaluation.experiment`                    | `--config` 和 `--output-dir` 必填，`--reference-run` 可重复指定独立参考目录。先冻结 `experiment.json` 与两份分析清单，再并列记录每个算法和种子的 `config.json`、`command.json`、`process.log` 与 `trials.csv`。 |
| 对已有结果做分析    | `python -m yield_estimation.evaluation.analyze_results`               | 输入 `analysis_manifest.json`，从各任务的摘要生成 `runs.csv`、`groups.csv`、`references.csv` 和 `methods.csv`。                                                                     |
| 单独查看网表与电路仿真 | `main_sram.py --circuit-config`                                       | 可读独立的电路 JSON，或根层有 `circuit` 的单次配置。`--deck-only` 控制是否执行主仿真。该入口仍用自己的样本、次数和种子约定。                                                                                       |

调用链从 `experiment.py:19` 校验配置，经 `experiment.py:67` 把一个实验展开成多个子任务，再由 `experiment.py:106` 和 `run_diagnostics.py:14` 将 MC 派到 `run.py`，MNIS 派到 `mnis_run.py`，AIS、ACS、HSCS 派到 `adaptive_run.py`。三条路径共用 `run.py:207` 的电路构造，后者调用 `main_sram.py:165` 的 `configure_circuit` 和 `main_sram.py:199` 的 `build_testbench`。仿真前的最终输入和实际测量，可以从子任务的 `config.json` 到算法目录的 `run_summary.json`、`samples.csv` 逐项回查。

**两个入口的 JSON 形状要分清。** 统一实验顶层有 `experiment`、`estimators` 和预算，电路在 `experiment.circuit`。诊断入口 `run_diagnostics` 保存的旧 `experiment.json` 则在根层放 `circuit`，它的预览也只校验这种结构。要让 `main_sram.py` 读取统一实验的电路，应提取解析后的 `experiment.circuit`，或使用子任务的扁平 `config.json`。直接把外层实验 JSON 填给 `--circuit-config` 会走未知字段检查。依据 `main_sram.py:345`、`experiment.py:52`、`run_diagnostics.py:56`。

## 二、电路和工艺

表格中的字段均从统一实验 JSON 的 `experiment` 往下找。

| 字段 | 作用与限制 | 源码落点 |
| --- | --- | --- |
| `case`、`circuit.cell_type`、`circuit.operation` | 用例决定 6T/10T 和操作。允许读、写、读写、保持/读/写 SNM。读写的用例后缀为 `read_write`，电路操作值为 `read&write`。后两项可省略，由用例填充。填写时须与 `case` 一致。 | `run.py:36`、`run.py:99` |
| `circuit.array`、`circuit.target` | `[行数, 列数, 是否启用列复用]`，目标是从 0 开始的 `[行, 列]`。普通用例省略目标时取末行末列。历史 `sram_6t_3x2_read` 锁定 `[3,2,false]` / `[1,1]`，修改用 `sram_6t_read`。所有 SNM 固定 `[1,1,false]` / `[0,0]`，完整电路模式 0。 | `main_sram.py:113`、`run.py:121`、`:143` |
| `corner`、`vdd`、`temperature_c` | 选工艺角 `TT/FF/SS/FS/SF`、电源电压 V、温度 ℃。配置传入共享加载器，仿真使用选择的工艺卡和温度。`vdd` 和 `temperature_c` 若在 `circuit` 再填，值须一致。`corner` 重复填法见下条。 | `run.py:74`、`:116`、`:207`、`:342`；`main_sram.py:78` |
| `circuit.cell_6t` | `[pd_width, pg_width, pu_width, length, pd_model, pg_model, pu_model]`。前四项填米，后三项为模型名。`null` 沿用 YAML。只能覆盖 6T，10T 的尺寸与器件选择由本分支 YAML 提供。 | `main_sram.py:88`、`:130`；`run.py:109` |
| `circuit.real_cell_mode` | `0` 为完整阵列。`1`–`4` 依次在简化其余单元时保留目标行列、目标行、目标列、目标单元。测评省略时用 0，显式 `null` 读 YAML 后保存实际值。1–4 只供 MC；SNM 固定 0。 | `run.py:109`、`:140`、`:169`；`CONFIGURABLE_CIRCUIT.md:63` |
| `circuit.w_rc`、`circuit.interconnect_config` | 前者开关存储/外围连接处的局部串联 RC，数值固定在构造器。后者才是分布互连配置，可给仓库根目录相对 YAML 路径、等价 JSON 对象或 `null`。YAML 会展开到已解析配置，报告保存实际互连。 | `main_sram.py:148`、`:155`、`:165`、`:199`；`run.py:236`；`CONFIGURABLE_CIRCUIT.md:17` |
| `circuit.variation_mode`、`relative_std`、`u0_positive` | `custom` 用当前模型的名义值、一个共同相对标准差和正迁移率约束构成目标工艺。波动坐标为每只管的 `vth0`、`u0`、`voff`，一个 6T 单元 18 维，10T 30 维。每个坐标的普通正态标准差为 `abs(名义值) × relative_std`，`u0` 再截断到正值。`shared`、`per-device` 由 Xyce 用相对波动设置抽样。`nominal` 只看名义点。 | `run.py:164`、`:207`、`:251`、`:539`；`main_sram.py:199` |

**共享工艺角校验已修复。** 外层与电路内显式填写且冲突时，统一测评校验直接拒绝并指明字段。只填电路内工艺角时补齐统一值。解析后的工艺角进入摘要和比较目标。网页只放一个工艺角输入，导入配置仍由后端检查。公开案例的 44 份摘要均为一致的 TT。依据 run.py 的 normalize_config 与 evaluation_circuit_options。

### 抽样方式与算法的组合

| 模式 | 可选算法 | 生效条件与结果含义 |
| --- | --- | --- |
| `custom` + 完整阵列 | MC、MNIS、AIS、ACS、HSCS | `u0_positive: true`。四种重要性抽样还要满足下节的初始化限制。直接 MC 用 `reference` 身份时要求 `custom`。作为独立参考还需同工况、完整直接 MC、独立种子等证据。 |
| `custom` + 等效阵列 | MC | 记录实际 `real_cell_mode` 与 `approximation`。和完整阵列在分析里属于不同目标设置。 |
| `nominal` | MC 的 `legacy` 身份 | 预算必须为 1，输出一次名义检查的通过、失效或无法判定，失效率留空。 |
| `shared` 或 `per-device` | MC 的 `legacy` 身份 | `u0_positive: false`，`batch_size` 至少等于预算。种子为 1 到 2³¹−1。`shared` 要求预算至少 2，同一模型共享；`per-device` 对保留器件单独抽样。输出观测失效比例，独立直接 MC 的 Wilson 区间仅在 `custom` 路径生成。 |

依据 `run.py:148`、`:169`、`:507` 和 `experiment.py:62`。

## 三、失效判据和预算

| 选项 | 当前生效规则 |
| --- | --- |
| `experiment.threshold_ns` | 读测 `TREAD_TOTAL`，写测 `TWRITE_TOTAL`，读写测 `TVOUT_PERIOD`，即输出两次上升跨越的间隔。超过门限记时序失效。有电压证据的访问功能失败另记。等于门限通过。 |
| `experiment.threshold_mv` | 对 `hold_snm`、`read_snm` 或 `write_snm` 使用对应裕量。低于门限记失效，等于门限通过。CSV 裕量用 V，配置用 mV。无法获得有效测量的批次保留为无法判定。 |
| `budget_per_algorithm`、`budget_unit`、`max_algorithm_draws` | 每种算法配置与每个种子独立计。`algorithm_draws` 要求两个上限相等。`xyce_samples` 以实际提交参数行扣额度，算法抽样上限至少等于提交额度。初始化、复测、搜索和正式阶段分别记账，正式估算用自己的分母。 |
| `seeds`、`experiment.batch_size` | 种子列表须非空、互异，可省略为 `[0]`。普通种子范围为 0 到 2³²−1，Xyce 原生模式再收紧。`batch_size` 调直接 MC 分批，改变它可能改变同种子产生的样本。重要性抽样的批大小由各算法配置确定。 |

依据 `outcomes.py:42`、`:75`，`run.py:85`、`:315`、`:539`，`research_settings.py:147` 与 `research.py:49`。预算应显示三种实际数量和正式分母，不能只呈现一个“仿真次数”。

## 四、每个算法的研究选项

| 字段 | 哪个算法用，何时生效 |
| --- | --- |
| `estimators[].id`、`algorithm`、`mode` | ID 决定输出路径，忽略大小写查重。同一算法可登记多份策略，ID 各不相同。`reference` 只注册直接 MC。`legacy` 保留历史接入策略。`variant` 用于四种重要性抽样的显式策略，MC 不接收每算法的提议或初始化配置。 |
| `initialization.method` | 四种 IS 的 `variant`。`target` 按目标工艺找候选，`uniform_box` 用逐维 `radius_sigma`（默认 3）围住名义值，`legacy` 调历史候选搜索，`from_file` 导入旧候选并重仿真。`legacy` 初始化和旧 MNIS 提议受已接入的 6T 18/108/576 维限制。维数相同还需核对工况与坐标含义；通用配置宜明确显示所选策略。 |
| `initialization.min_failed_candidates`、`max_simulations`、`on_insufficient` | 最少候选失效数 ≤ 初始化仿真上限 ≤ 每任务预算。当前不足策略固定 `stop`，报告 `initialization_insufficient` 和空估计。已提交的样本仍扣额度。|
| `initialization.path`、`sha256` | 仅 `from_file`。相对路径按执行目录解析，配置阶段固定文件哈希。运行时对照电路、模型、参数顺序、分布、门限及支持域，再做复测。换了门限的旧候选表会因身份不符被拒绝。 |
| `center_selection.rule` / `initial_pool.rule`、`initial_pool.count` | MNIS 只选一个正式采样中心，用 `center_selection`。AIS、ACS、HSCS 选初始池，用 `initial_pool`，池大小不能超过要求候选数。规则是发现顺序 `discovery_order`、原始距离 `nearest_raw`、标准化距离 `nearest_standardized`。HSCS 还会搜索，初始池不等于最终中心。 |
| `initial_batch`、`initial_failures`、`estimation_batch`、`estimation_draws` | IS 初始批、历史初始失效数/变体中池大小的默认值、正式批及可选正式样本上限。MNIS 变体 `initial_failures` 固定 1，想扩大候选数改 `min_failed_candidates`。AIS 的正式批必须是初始池大小的整数倍。正式上限必须是正式批整数倍，且仍受总预算约束。 |
| `proposal.covariance`、`legacy_variance`、`std_scale`、`target_fraction` | IS 变体正式采样选 `target_diagonal` 或 `legacy`。`legacy_variance` 只在 `legacy` 可填，提供正的标量方差。`std_scale` 乘标准差，所以方差乘其平方。`target_fraction` 是外层按目标工艺抽样的概率，范围 `[0,1)`，实际用混合密度算权重。 |
| `allow_hscs_mixed_search`、`hscs_search_origin`、`hscs_search_max_simulations` | 只适用于 HSCS。正式操作为读时必须显式允许辅助写。辅助写打在末行末列，沿用读门限的数值用于搜索，花费计入预算，正式估算仍读配置的目标。其余操作用本操作搜索。搜索起点和搜索上限供 HSCS 变体设置，预算账本列出 `write_search` 或 `failure_region_search`。 |

依据 `research_settings.py:31`、`:100`、`:131`，`research.py:59`、`:89`、`:101`、`:193`，`mnis_run.py:108`、`model_lib/MNIS.py:264`，`adaptive_run.py:35`、`:79`，以及 `mixed_search.py:8`。

有两个名称容易让人以为作用相同。`initialization.radius_sigma` 真正控制 `uniform_box` 的候选生成。子任务派生的 `initialization_sigma: 3.0` 是给底层构造器的默认边界，网页应编辑前者。HSCS 本身默认的 `ratio=0.1` 是名义点附近的高斯分量，`proposal.target_fraction` 才表示外层的目标工艺混入。依据 `experiment.py:73`、`research.py:139`、`model_lib/HSCS.py:22`、`:288`。

## 五、现在固定或没有接到统一入口的项

- 10T 管子详细尺寸与模型覆盖、外围驱动尺寸、底层时钟周期等由 YAML 或编译器决定。完整设置会出现在摘要，统一实验暂时没有对应可写字段。`threshold_ns` 改的是判定线，底层时钟周期另有 `set_timing_parameters()`。依据 `main_sram.py:88`、`run.py:236`、`sram_compiler/testbenches/base_testbench.py:43`。
- 工艺目标分布基于模型名义值和单个 `relative_std`。没有任意相关矩阵、每参数独立分布编辑、外部模型卡文件选择或通用自定义失效规则入口。
- `MC_RUNS`、`MC_SEED`、`RUN_XYCE` 是独立仿真入口的设置。统一测评用预算和种子列表，计划验证、仿真执行和结果分析分开。固定案例的墙钟限时由外部启动脚本维护。算法的 FOM 留作诊断，ACS 现有路径未启用聚类，HSCS 的内部混合常数和搜索步长不是统一实验控件。依据 `main_sram.py:52`、`experiment.py:146`、`model_lib/ACS.py:228`、`adaptive_run.py:93`、`model_lib/HSCS.py:249`。

## 六、网页第一版应该暴露什么

1. **工况与预算。** 先选 6T/10T 与一个操作，自动生成 `case` 和门限单位。普通访问开放阵列、列复用和目标，SNM 显示固定单元。工艺角、电压、温度各只填一次。主要选项再有抽样方式、相对波动、种子和每任务预算。预览显示任务数和预算口径。
2. **算法条件。** 默认展示公开案例的冻结配置作示范。用户可选择受支持的算法，编辑其他工况时依已校验的组合启用按钮。先选 `custom` + 完整阵列才开放四种 IS。高级配置按算法显示初始化、选点、提议与搜索。HSCS 读要明确解释辅助写和费用，再由用户显式启用。历史身份与研究变体分别标注。
3. **先校验，再运行。** 表单生成单一实验 JSON，调用 `normalize_experiment`，用解析后的配置显示真实单位、限制和文件。配置成功只能证明程序接受它。实际执行用 `run_experiment`，离线分析用 `analyze_results`，业务规则继续由这两个现有入口控制。
4. **结果页展示依据。** 逐运行展示原估计、状态、请求/提交/返回、正式分母、告警。存在同目标独立参考时同时给参考区间与描述性误差。参考不匹配时保留运行，误差栏留空。把公开案例、历史小样本和仅通过配置校验的状态标出。当前分析器将 `ranking_eligible` 固定为 `False`，网页可并排显示比较结果。
