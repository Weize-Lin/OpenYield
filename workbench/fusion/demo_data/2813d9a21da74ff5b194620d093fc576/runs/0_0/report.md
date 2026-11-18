# MNIS 接入诊断

- 实现：repository_MNIS_configured_proposal
- 基础提议协方差：target_diagonal
- 混入原工艺分布的概率：0，实际选中 0 条
- 测评：hold_snm，hold_snm < 327.65 mV 判为超限
- 状态：budget_stopped；说明：Used 12/12; next estimation batch needs 1
- 代码：312f137e9ab960f1178f4991d9bf4675575e2f01；未提交改动：False
- 算法预算名额：12/12
- 分阶段请求：{'initialization': 4, 'recheck': 1, 'estimation': 7}
- 实际送入仿真：12；返回：12
- 零目标密度样本：0，不仿真、不重抽，保留在估计分母
- 估算抽样总数：7；其中属于目标范围：7
- 数值状态：representable
- 对数核对得到的估计值：4.598920750139648e-07；log10：-6.33734407424279

- 抽样诊断：{'log10_mean_weight': -5.2747686422928055, 'weight_ess': 1.544408374363521, 'warnings': ['low_observed_weight_mass']}
平均权重远小于1或远大于1时给出诊断提示，不是算法正确与否的判决；ESS也不是准确性保证。
这是接入诊断，不能据此给算法排名。没有找到初始失效点时估计留空。
zero_observed_contribution表示本批估算没有非零贡献，不能证明实际失效率为零。
underflow表示普通数字表示不了这么小的估计，应看log10；这也不证明估计准确。
samples.csv只列实际仿真；estimation_draws.csv列全部估算抽样，目标工艺范围外的样本不填写电路测量值或判定。
预算按算法请求计费，包括复测和零密度样本；它是实际仿真次数的保守上限，不能称为等实际调用数比较。
算法内部的估算输出保存在 original_estimate。对数核对使用本轮报告记录的提议分布。
