# dev/ — 本地实验脚本（不属于上游仓库）

> **注意**：本目录被 `.gitignore` 第 35 行 `/dev/` 忽略，普通 `git add` 不会收录。
> 这些脚本是本机实验工具，**不提交给上游（ShenShan123/OpenYield）**；只在本协作分支上保留，供同伴复用。
> 若需提交，必须 `git add -f dev/xxx.py`。

**版本基线**：OpenYield **V2.2.6**（提交 `df3fab4`）。
**环境**：WSL2 + Ubuntu 24.04 + conda 环境 `openyield`（Python 3.9 / PySpice 1.5 / **Xyce 7.4.0**）。
**单样本仿真耗时**：4×2 阵列、读操作，约 **28 秒**。

**用途边界（2026-11-15 核对）**：这是 11 月 12 日本地实验工具的记录，供协作者了解和检查已有工作。当前协作分支已有 `yield_estimation/evaluation/`；这里的脚本不替代该入口，也不作为已经验收的正式良率工具。以下历史源码行号指当时研读的版本。

---

## 1. `check_run.py` —— 判定层（把测量值变成 pass / fail）

**为什么需要它**：平台已经有检查逻辑。`summary.json` 中的 `access_checked` 为真，表示整批访问检查都通过；`metrics_checked` 表示所需测量值完整。这些汇总结果没有逐条展示每个样本超了哪项限制。本脚本读取 `deck.sp.mt0…mtN`，补充逐样本显示和统计。

**判据来源**：电压限值参考 `sram_compiler/testbenches/sram_6t_core_MC_testbench.py` 的 `access_validity()`；将总延迟与 `timing.low_read / low_write` 比较，是本地脚本另加的实验判据。该时序选择尚未得到项目负责人的确认，不能称为已确认的正式规格。

| 判据 | 阈值 |
|---|---|
| `V{ACCESS,HOLD,PRE_ACCESS,BOUNDARY,ROLE,ISO}_ERROR_c` | ≤ 0.10 × VDD |
| `VRESTORE_ERROR_c` | ≤ 0.02 × VDD |
| 时序：`TREAD_TOTAL`（读）/ `TWRITE_TOTAL`（写） | ≤ `summary.json` 的 `timing.low_read` / `low_write` |
| 测量值为 `FAILED` / `NA` | 单独计为**未完成**，不与失效混计（AGENTS.md 要求） |

**用法**：

```bash
python dev/check_run.py outputs/mc100            # 判定一个运行目录
python dev/check_run.py outputs/*/               # 批量
python dev/check_run.py outputs/mc100 --list     # 列出全部 25 个测量项
python dev/check_run.py outputs/mc100 --detail   # 逐项打印每个样本的检查
python dev/check_run.py outputs/mc100 --no-timing  # 只查电压判据
```

**输出示例**：

```
合计 100 个样本：通过 100 · 失效 0 · 未完成 0
全场最紧 20.3%（TREAD_TOTAL）—— 越接近 100% 越危险
```

退出码：`0` 全通过 / `1` 有失效 / `2` 有未完成 / `3` 参数或路径错误。

---

## 2. `mc_estimate.py` —— 带“自动停”的蒙特卡洛

**与当时研读的 `yield_estimation/model_lib/MC.py` 的对应**：旧版本存在本机缺少 torch、写死作者电脑路径和调用错误等问题。林同学后续已修改算法文件，不能把这些历史问题全部套用到当前协作版本。本脚本借鉴其流程，但抽样范围、判定和 FOM 计算并非完全一致。

| `MC.py` | 本脚本 |
|---|---|
| 第 110 行：从 N(means, cov) 抽 10,000 个 | runner 的 `--vth-std` 抽样 |
| 第 115 行：`run_mc_simulation(...)` | 调 `per_device_mc.run` |
| 第 44 行：`indicator(y)` | `check_run.build_checks`（= 仓库 `access_validity` 口径） |
| 第 66 行：`Pf = 失效数/总数` | 相同（累计样本） |
| 第 75 行：`FOM = 最近 K 轮 std/mean` | 借鉴此形式，但本脚本算方差除以 K−1，旧源码的默认计算除以 K；默认窗口也不同 |
| 第 70 行：均值=0 时 FOM 记 1 | 相同（忠实还原，导致永不收敛） |
| 第 126 行：`while FOM>0.01 …` | 相同（目标可配、轮数有上限） |

**用法**：

```bash
# 小规模试通
python dev/mc_estimate.py --mc-runs 5 --max-samples 10 --fom-target 0.20

# 另开一轮实验：下面两个保存位置都应尚未使用
# 720 ps 下，已有 100 个样本重新判定为 14% 失效；新抽样结果可能不同
python dev/mc_estimate.py --budget 7.2e-10 --mc-runs 10 --max-samples 60 --fom-target 0.15 --out-dir outputs/mc_read720_trial01 --csv dev/results/mc_read720_trial01.csv

# 只用电压判据
python dev/mc_estimate.py --budget 7.2e-10 --no-timing --mc-runs 10 --max-samples 60
```

**输出**：终端逐轮打印 `[MC] # 样本 N · 失效 M · Pf … · FOM …`，并写 `dev/results/mc_estimate.csv`
（列：`round,total_samples,failed,Pf,FOM,incomplete,elapsed_s`）。

**退出码**：`0` FOM 达标 / `1` 撞样本或轮数上限 / `3` 出错。

---

## 3. `plot_fom.py` —— FOM 收敛曲线

```bash
python dev/plot_fom.py --target 0.15
python dev/plot_fom.py a.csv b.csv --target 0.15    # 多组对比（如 MC vs MNIS）
```

上图 = FOM（带目标线与停止点圆圈），下图 = Pf。图内文字用英文，避免 Linux 缺中文字体导致方框。
保存到 `dev/results/fom_curve.png`。

---

## 已知限制（写在这里，避免误读结果）

1. **本脚本的 `--budget` 是允许的延迟，单位为秒**；协作测评入口的 `budget` 则是允许使用的样本名额，两者含义不同。720 ps 是人为收紧的调试门限；查表的 3.6 ns 也是当前本地判据选择，正式失效标准仍待确认。
2. **FOM 有“假收敛”风险**：`std/mean` 在最近三轮恰好相同时会得到 0。少量样本下极易误判。
   缓解方向：要求最少轮数；或改用每轮独立 Pf 而非累计 Pf 计算 FOM。
3. **已有 100 个样本在当前判据下为 0 失效**，最紧一项占所选限值 20.3%；这不能证明真实失效率为零。增加工艺波动或收紧门限用于检查流程时，应明确标记条件变化。
4. 默认运行会复用 `outputs/mc_est/roundNN`，并覆盖默认 CSV。为保留旧实验，每次用 `--out-dir` 和 `--csv` 指定新的保存位置，不要删除已有结果来腾位置。
5. **缺测处理不完整**：脚本能识别文本 `FAILED / NA`，但未逐项确认所有应有测量都存在，也没有完整拦截 `NaN / inf` 等无效数字；存在漏判风险。它也没有核对测量文件数是否等于请求样本数。
6. **未完成样本的统计尚待修正**：`mc_estimate.py` 会单列未完成数量，但仍把它们放进失效率分母，也可能继续判定停止条件。出现未完成样本时，该失效率与停止结论不能作为正式结果。
7. 仿真入口返回错误时，本脚本会中断；真实电压失效仍可能触发这种中断。现有 720 ps 实验只验证了脚本附加时序判据下的统计流程。
8. 已核对的本地实验限于 4×2 读操作。虽然命令行列出其他操作，不能据此认为均已支持；例如 `--budget` 当前只改 `low_read`。本脚本判据也未覆盖仿真器的全部检查，更未完成波形正确性验收。

## 本次提交前的检查（2026-11-15）

- 只读取已有 `outputs/mc100` 结果，再运行判定脚本，仍得到 100 通过、0 失效、0 未完成，最高限值占比 20.3%。没有重新运行这 100 次仿真。
- 核对保留的 25 样本 CSV：5 个失效，Pf=0.20，最后 FOM 约 0.173，未达到当时 0.15 的目标。
- 这些检查确认已有记录与脚本输出一致，不代表上述限制已修复，也不代表通过了正式电路或算法验收。

---

## 数据位置

| 内容 | 路径 |
|---|---|
| 100 样本（σ=5%，0 失效） | `outputs/mc100/` |
| 25 样本（预算 720 ps，Pf 0.20） | `outputs/mc_est/` |
| 过程表 | `dev/results/mc_estimate.csv` |
| 收敛曲线 | `dev/results/fom_curve.png` |
