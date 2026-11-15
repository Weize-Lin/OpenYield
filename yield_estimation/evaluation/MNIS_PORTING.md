# MC与原版MNIS目前怎么使用

## 现在完成了什么

十月一号版的电路没有换。现在有两个运行入口：MC和原版提议分布MNIS。
还有一个读取已有结果生成诊断对照表的入口。
目前能比较运行结果和消耗，但不能据此生成可信的算法排行榜。

MNIS保留了原来的边界盒初始化、原始坐标距离选中心和原高斯提议方差。
没有移植以前的0.1偏移、目标分布初始化或目标尺度提议分布。

## 三个命令

在仓库根目录、openyield环境中运行。每次输出目录都必须是新的。

MC：

```bash
python -m yield_estimation.evaluation.run \
  --config yield_estimation/evaluation/cases/read_3x2_rel5.json \
  --output-dir outputs/my_mc
```

MNIS：

```bash
python -m yield_estimation.evaluation.mnis_run \
  --config yield_estimation/evaluation/cases/read_3x2_rel5.json \
  --initial-batch 2 --initial-failures 1 --estimation-batch 1 \
  --output-dir outputs/my_mnis
```

这个示例配置只有4个预算名额、门限1ns，可能停在找失效点的阶段。
没有找到失效点时，报告的估计值留空，不能当作0。

查看两轮诊断：

```bash
python -m yield_estimation.evaluation.compare_diagnostics \
  --mc-run outputs/my_mc \
  --mnis-run outputs/my_mnis \
  --output-dir outputs/my_comparison
```

对照会核对工艺、电压、温度、门限、模型、驱动、时序和互连。
发现不一致会标出来。它不产生排名，不将小样本MC当作准确真值。

## 程序怎么走

1. 入口读取JSON配置，确定3×2 6T读电路、工艺范围和允许的读延迟。
2. 通过原来的configure/build_testbench建立电路仿真接口。
3. MC按目标工艺直接抽样。MNIS先按原边界范围找失效点，再按原提议分布抽样。
4. 实际进入仿真的参数交给RecordedReadTestbench，再调用原testbench和Xyce。
5. 电压证据完整时，记录通过、超时或读功能失效。求解失败、缺测保留为未判定。
6. MC直接计算失效比例；MNIS给估算样本乘上密度比，再求平均。
7. 保存逐样本数据、估算抽样明细和可读报告。

工艺配置里的relative_std=0.05表示相对标准差5%，u0限制为正。
MNIS使用相同的目标工艺密度，但抽样分布仍是它自己的原版提议分布。
要估算的对象相同，不代表抽到的参数也相同。

## 为什么有些MNIS样本不仿真

原版高斯提议可能产生负u0，而当前目标工艺只允许正u0。
负u0在目标工艺中的密度为0，所以它的加权贡献必为0。
这时不用让Xyce去计算非法参数电路。

代码仍保留这个抽样，不裁剪、不丢弃重抽，估计分母也包含它。
不仿真的样本没有电路通过/失效标签，延迟留空。
support判断独立于浮点密度，避免把数值下溢误当作工艺不允许。

## 预算怎么看

MNIS分三段记账：

- initialization：寻找失效点。
- recheck：对找到的失效点再仿真。
- estimation：正式估算抽样。

budget是算法请求名额的上限，包含零目标密度样本和复测。
实际送进Xyce的数量另列，所以两个算法budget相同，不一定实际仿真次数相同。
剩余预算不足下一整批时就停，不擅自拆批改变原来的批平均。
这是一种保守的预算接入方式，尚不是按相同实际仿真次数完成的正式比较。

## 打开哪些结果

- report.md：先看运行是否完成、预算和数值警告。
- run_summary.json：查原版结果、对数核对结果、代码版本、模型和配置。
- samples.csv：实际送入仿真的样本及其结果。
- estimation_draws.csv：MNIS所有估算抽样，包含零目标密度样本。
- estimation_*.npz：每轮完整参数、密度、范围标记和延迟。
- batch_*/：原始网表、输入参数和Xyce输出。

MNIS的普通估计留空但log10有值、numeric_status为underflow，表示结果太小而不能以普通浮点数表示。
zero_observed_contribution表示这一批抽样没有非零贡献，仍不能证明真实失效率是0。
no_estimation表示尚未得到估算样本。

平均权重和ESS用于观察抽样情况，不是准确性保证。
平均权重小于0.1或大于10的提示只是诊断提示，不是评分规则。

## 已经跑到什么结果

真实工况：3×2 6T、TT、1V、27℃、rel5、seed2、0.7ns调试门限。

| 项目 | MC | MNIS |
| --- | --- | --- |
| 算法预算名额 | 12 | 12 |
| 实际仿真返回 | 12 | 6 |
| 估算阶段抽样 | 12 | 9 |
| 零目标密度抽样 | 0 | 6 |
| 结果 | 12/12超过调试门限 | log10估计约-6287.024621 |
| 使用限制 | 小样本，不能当真值 | 下溢，观测平均权重极小，不能排名 |

两轮固定工况检查没有差异。MNIS的实际6次包含初始化2、复测1、有效估算3。
其权重ESS为1。这个实验说明接入、记账和异常显示已经工作，
不证明MNIS理论错误，也不证明它在这个工况上的估计准确。

## Git对应

| 提交 | 内容 |
| --- | --- |
| f852b0b | 原MNIS的路径和目录清理兼容修复 |
| 7cea3a2 | 严格预算和真实请求计数 |
| 8ad84dc | 共同工艺的密度计算 |
| 7d2fd30 | 保留零目标密度抽样，跳过非法电路仿真 |
| b3a8830 | MNIS诊断入口和估算明细 |
| 5b34b5c | 下溢显示与权重诊断 |
| 54b6743 | MC/MNIS诊断对照工具 |

这不是完全未经修改的原文件。停止规则、记录和数值诊断做了工程修复；
原初始化方式、中心选择和提议方差保留。

## 后面缺什么

还需要确定正式任务的设计门限、预算口径和参考结果，
再用独立种子重复运行评估稳定性。
原版MNIS在当前诊断中的抽样覆盖不足，先如实展示，不为接近MC而偷偷调参。
其他原算法仍需分别审查和接入。
