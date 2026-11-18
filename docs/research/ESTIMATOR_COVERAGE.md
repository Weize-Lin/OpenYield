# 五种估计器的验收覆盖与结果分析起点

公开回归用真实的估计路径，电路测量由明确的解析函数代替。测试通过表明给定案例中的约定得到验证。论文复现、真实 SRAM 的误差、稀有失效覆盖、无偏性与置信区间仍需各自的证据。

## 五算法覆盖

| 算法 | 目前的正面证据 | 仍待验证 |
| --- | --- | --- |
| MC | 明确工艺抽样的参考路径。公开测试覆盖工艺采样、正迁移率支持域和密度。历史 Hold 参考保留 142/1024、95% Wilson 区间 11.885%–16.119%。 | 参考值的不确定度随工况和样本数变化。稀有失效下需要充分的独立参考样本，少量 MC 或 0 次观测失效都不能充当真值。 |
| MNIS | 生产路径在已知概率 0.5 的题目上用三个种子验收。支持域外样本留在分母，不调用测量。离线复算完整候选表，记录首失效点、原始距离和标准化距离三种选择。 | 标准化选点对精度和效率的影响、历史低权重成因与论文最小范数搜索等价性。 |
| AIS | 测试经过真实 runner，保留实际构造、采样、密度和中心更新。两批之间中心变化，每批密度独立复算，旧点只在选下一轮中心时重评分。 | 多失效区、不同维度、重要性采样重用与停止规则的一般统计性质，论文一致性。 |
| ACS | 两批生产回归独立核对当批密度、追加失效点及预算。已证实当前和历史代码均未执行聚类。 | 按方向分簇的论文策略是否能改善结果、多个失效区覆盖、当前策略的真实误差。 |
| HSCS | 生产路径保留聚类、搜索、实际高斯混合与原工艺混合。独立核对密度、正式分母、辅助写和搜索时耗尽预算的处理。 | 多簇情况下的搜索几何、与论文流程的等价性、辅助写对其他读工况的有效性及估计统计性质。 |

ACS 历史路径采用均匀盒初始化，当前不调用方向聚类。分量系数取累计失效点的归一化目标密度。后续核对作者 Algorithm 3 并独立计算发现，在固定候选、相同核参数和对应簇质量的条件下，分簇混合可以化简为当前的扁平混合。因此，未调用聚类本身不能直接认定为数学错误或低估原因。初始化坐标、样本池更新和停止规则仍需逐项核对，见 [ACS 实现核对](ACS_IMPLEMENTATION_AUDIT.md)。 [OpenYield 使用的 ACS 引文及名称](https://arxiv.org/html/2508.04106v2) · [ACS 作者报告](https://ispd.cc/slides/2019/25_PD_Adaptive.pdf) · [第一作者博士论文第 3 章](https://escholarship.org/content/qt58f61961/qt58f61961_noSplash_5af6b5baafcfcae77097c91fc3fa01ea.pdf)

[统计验收](../../yield_estimation/evaluation/ESTIMATION_ACCEPTANCE.md) · [离线选点证据](mnis_selection/README.md) · [自适应路径公开测试](../../tests/test_adaptive_production_contract.py)

这组解析输入固定：AIS 的保持裕量样本全部失效，HSCS 的读样本全部失效，辅助写全部满足搜索门限，且 HSCS 只处理一个簇。验收覆盖这些条件下的记录、加权、更新和停止。通过与失效混合、多失效区域及多簇搜索仍待测试。

## 自适应路径测试实测输出

两个 runner 使用生产分布和 `proposal_json` 快照。用 SciPy 正态与截断正态计算原工艺密度，再按保存的分量系数与混合比例独立算提议密度。每轮正式估计以该轮真实提议计算贡献，用全部已完成的正式行作分母。

| 解析案例 | 预算 | 算法请求 | 实际提交 | 实际返回 | 正式分母 |
| --- | ---: | ---: | ---: | ---: | ---: |
| AIS，算法抽样上限 | 35 / 36 | 35 | 35 | 35 | 32 |
| HSCS，算法抽样上限 | 12 / 13 | 12 | 12 | 12 | 4 |
| HSCS，仿真样本上限 | 13 | 14 | 12 | 12 | 4 |
| HSCS，搜索阶段耗尽 | 4 | 4 | 4 | 4 | 0，失效率留空 |

HSCS 的完整案例有一次聚类，搜索每次把区间减半，直到窄于起始宽度的 5%。这个解析设置有 5 次辅助写，2 个正式读批次共 4 条。辅助写输出满足写搜索门限而读测量超过读门限，用这个区别检查两类样本的记账。仿真样本上限场景中，最后一批已提出请求，因 2 条放不进去而未提交。它留在审计记录，正式分母仍是 4。

在 WSL 仓库根目录复跑：

```bash
conda activate openyield
CUDA_VISIBLE_DEVICES='' OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
  python -m unittest discover -s tests -p 'test_adaptive_production_contract.py' -v
```

从 Git 暂存区快照导出的干净源码目录复跑了全部公开测试。复用已安装 CPU 依赖。实际输出节选：

```text
OK
AIS budget=36 requests/submitted/returned=35/35/35 formal=32 proposal_changed=True
HSCS stopped in auxiliary search: requests/submitted/returned=4/4/4 formal=0 estimate=None
HSCS budget=13 unit=xyce_samples requests=14 submitted=12 returned=12 writes=5 formal=4
Xyce sentinel: no calls
```

## 统一分析的第一项差异

已读现有 [`experiment.result_row`](../../yield_estimation/evaluation/experiment.py)、[`condition_mismatches`](../../yield_estimation/evaluation/compare_diagnostics.py)、[`collect`](../../yield_estimation/evaluation/collect_diagnostics.py) 与 [`hold_snm_study`](../../yield_estimation/evaluation/hold_snm_study.py)。统一入口已经有逐种子的状态、估计、各类成本和告警。Hold 研究另有条件审查、独立参考与按方法误差。配置通用的分析需要保留这些证据及各自口径。

用同一个 340 mV 公开示例实测旧条件检查器。MC 与 MNIS 的电路和门限一致，检查器却返回 `initialization_sigma` 和 `mnis_proposal_covariance`，它们是此例有意设置的算法差异。两条记录的实际仿真成本确实不同，为 22 与 13。这需要分别报告**共享工况、策略身份和比较预算**。即使同工况，两条记录也不会被当成等仿真成本数据。

已保留弱结果的三个例子：历史 Hold 组有 32 条低权重告警，340 mV 小样本 MNIS 的原估计 `2.00568` 在逐次表中可查，另一个预设候选不足案例的失效率为空。统一分析使用这些旧结果做离线试件，按共享工况、固定来源和预算判断可比性，报告每组总运行数、无估计比例及原因。下溢保留对数值与状态，正式可比较组的精度再相对独立参考报告。现有用法见 [离线分析](../../yield_estimation/evaluation/RESULT_ANALYSIS.md)。
