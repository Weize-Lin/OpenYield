# MC 判定与门限重统计验收

## 这次做到哪里

十月一号版已经可以用配置文件跑原仓库的 MC，保存每个样本的结果。
现在又补了“读取已有结果，换门限重新统计”的入口。
只改允许的读延迟时，不需要把相同电路重新仿真。

这不是多个算法的排名结果。目前接通的是 MC，MNIS 等算法尚未接入这个新入口。

## 对应 Git 版本

| 提交 | 修改 |
| --- | --- |
| `70ff20af26be73b05c51203611acf3aeb2c9235e` | 实时评估和离线重统计共用读结果分类函数 |
| `07fb2dba9256b28d890b8e95c41908707b208393` | 增加门限重统计命令及用法 |

基准仍是 `8bb03ee97a534ab0aff4fd894cc4d6c4ccf08008`。
本次没有改电路结构、MC 失效率计算公式或 MNIS。

## 怎么判定

共用代码在 `outcomes.py`：

```python
if functional_failure:
    return FUNCTIONAL_FAILURE
if delay_s is None or not math.isfinite(delay_s) or delay_s < 0:
    raise ValueError("A classified read needs a finite, nonnegative delay")
return TIMING_FAILURE if delay_s > threshold_s else PASS
```

- 有效读延迟不超过门限，记通过。等于门限也通过。
- 有效读延迟超过门限，记超时失效。
- 仿真接口根据完整电压测量确认读访问检查不通过，记读功能失效。
- 求解失败、测量不完整，保留为未判定，整轮不给失效率。

这里的功能失效标记来自前面的电压检查，不能拿一个缺失值直接当功能失效。
本次对缺测和功能失效的检查使用了预设测量数据，没有宣称触发了真实电路功能失效。

## 真正跑了什么

助手在 openyield 环境执行。3×2 6T、TT、1 V、27 ℃、108 维存储单元参数，
相对尺度 5%、u0 限制为正、seed=2、预算 4、每批最多 3 条，实际分成 3+1。
外围和副本电路参数保持标称。
从原示例配置复制到输出目录，只把 threshold_ns 改成 0.7，再运行：

```bash
python -m yield_estimation.evaluation.run \
  --config outputs/oct01_mc_timing_failure_config.json \
  --output-dir outputs/oct01_mc_timing_failure
```

四条读延迟为 0.7131475、0.7131480、0.7131447、0.7131435 ns。

| 检查 | 数据来源 | 结果 |
| --- | --- | --- |
| 原来 1 ns 门限 | 上次真实 MC，4 条 | 4 条通过 |
| 改成 0.713146 ns | 读取上次结果，不仿真 | 2 条通过，2 条超时 |
| 改成 0.7 ns | 读取上次结果，不仿真 | 4 条超时 |
| 配置 0.7 ns 再运行 | 本次真实 MC，4 条 | 4 条超时，0 条功能失效，0 条未判定 |

真实仿真和离线统计退出码均为 0。实际验收记录：

```json
{
  "same_sample_arrays": true,
  "same_outcomes": true,
  "unchanged_source_files": true,
  "max_read_delay_difference_s": 0.0,
  "live_samples": 4,
  "offline_additional_samples": 0,
  "timing_failures": 4,
  "functional_failures": 0,
  "live_revision": "07fb2dba9256b28d890b8e95c41908707b208393",
  "reassessment_revision": "07fb2dba9256b28d890b8e95c41908707b208393",
  "tracked_files_modified": false
}
```

两次使用相同种子、相同分批，输入参数完全相同，读延迟也相同。
新增四次仿真是重复验收，不能当作八个独立样本来提高统计可信度。
另外完成了 14 项本地离线程序检查，包含门限边界、预算、异常记账、已有文件保护。
本地 Python 测试文件按上游约定未跟踪；详细源码和命令保留在项目工作日志。

## 平时怎么重统计

已有完整 MC 运行目录时：

```bash
python -m yield_estimation.evaluation.reassess \
  --source-run outputs/oct01_mc_smoke \
  --threshold-ns 0.7 \
  --output-dir outputs/my_threshold_check
```

输出目录必须是新的。打开其中的 `report.md` 看结论，
`samples.csv` 看每条数据，`run_summary.json` 查原始版本和输入文件指纹。
这些输出文件在 outputs 中，按仓库约定不提交到 Git。

这里只沿用原来已确认的功能标签，不重新检查原始电压波形。
不完整运行会被拒绝，原始报告和样本数对不上也会被拒绝。

## 结论和下一步

这次验证了“按配置仿真—逐样本判定—出报告—换门限重统计”的 MC 流程。
没有证明真实失效率，也没有校准正式门限。
0.7 ns 和 0.713146 ns 都是检查程序使用的门限，
尤其后者是看过数据后选的，不能当作预先规定的设计要求。

下一阶段先记录原版 MNIS 的抽样和计算权重的规则，再逐项接入同一实验配置、
仿真和记账入口。兼容性修复单独提交；如果需要改变抽样规则，明确标为变体，
不把此前调整过的 MNIS 直接当作原版搬过来。
