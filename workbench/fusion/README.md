# OpenYield Fusion 良率评测工作台

Fusion 把现有 OpenYield 实验和分析入口接成跨电路工作台。算法、工艺与失效判定仍由
`yield_estimation/evaluation` 执行；网页负责配置、套件编排、证据查看和下载。
本次整合保留双方 Git 历史，没有把算法退回 Fusion 最初使用的旧版本。

## 研究者应该选择哪个版本

本 PR 合并前，完整工作台入口是 **`Weize-Lin/OpenYield` 的 `codex/integrate-fusion` 分支**。
PR #1 进入正式审查，尚未合并。`codex/benchmark-oct01` 保留原后端，暂未接入本次工作台。
新用户可以在新目录取得集成分支，保留现有目录：

```bash
git clone --branch codex/integrate-fusion https://github.com/Weize-Lin/OpenYield.git OpenYield-fusion
cd OpenYield-fusion
```

依赖安装见根目录环境说明及 [首次使用指南](../../docs/research/YIELD_QUICKSTART.md)。
下面的启动命令只启动页面，不自动提交电路。合并后的分支入口将另行更新文档，本说明不提前假设已经合并。

当前证据分成两个阶段：

| 阶段 | 实际源码/证据 | 已完成的事 |
|---|---|---|
| 离线整合 | `fe35746`、`d948267`、`330527d` 的留存记录 | 64 项后端与 29 项 Fusion Python 检查、只读演示和配置校验；该阶段没有新 Xyce 电路仿真 |
| 后续真实闭环 | 实际源码 `80f3df7`，证据提交 `f159e3e` | 从页面提交一次套件，两个 MC 任务，8 条提交/8 条返回，四表下载和离线复算通过 |

后续真实验收不会改写此前离线阶段的事实。当前本轮收尾只修改文档和 PR 状态，
没有追加仿真或更改生产代码。完整边界见 [正式审查清单](REVIEW_CHECKLIST.md)。

## 在 WSL 启动

在仓库根目录，先激活已经安装好依赖的 OpenYield 环境：

```bash
conda activate openyield
bash workbench/fusion/start.sh
```

打开 **http://127.0.0.1:8766**。启动页面不会自动仿真；点击运行才提交任务。
Ctrl+C 停止服务。默认结果在本仓库 `outputs/fusion_workbench/`。
现有后端本地页面和命令行入口继续保留。

`start.sh` 从脚本所在位置定位仓库，不依赖用户名和当前目录。可以显式指定：

```bash
OPENYIELD_PYTHON=/path/to/env/bin/python \
OPENYIELD_REPOSITORY=/path/to/integrated-checkout \
  bash workbench/fusion/start.sh --port 8766 --output-root /path/to/results
```

仓库必须包含本次整合的软件接口；外接旧版会报错，不能自动降级。
完整模式需要正常的 OpenYield Python 依赖，真实运行还需要 Xyce，见根目录环境说明。
依赖安装、工况科学性、算法论文一致性，不由页面校验通过来证明。

Windows 也可以在 PowerShell 启动，路径使用自己的 WSL Linux 路径：

```powershell
.\workbench\fusion\start.ps1 -Distribution Ubuntu-22.04 `
  -Repository /home/shammy/projects/OpenYield-integrate-fusion `
  -Python /home/shammy/miniconda3/envs/openyield/bin/python -NoBrowser
```

这里的用户路径只是本机示例，不是程序默认值。发行版留空使用 WSL 默认发行版。
`Start-OpenYield-Fusion.cmd` 继续调用这个可配置脚本；没有隐藏的私人依赖目录。
服务器在当前窗口运行；也可用 `stop.ps1 -Port 8766` 请求停止。

## 只看已有证据

只查看随仓库的 Fusion 历史演示，不需要 NumPy、Conda 或 Xyce：

```bash
python3 workbench/fusion/server.py --demo workbench/fusion/demo_data --port 8767
```

打开 **http://127.0.0.1:8767**。也可以直接打开 `demo_site/index.html` 静态演示。
随仓库的演示包保留原始数据、旧源码版本和告警，是历史案例，不能当成当前源码重新运行的结果。
`MILESTONES.md`、原图文手册和 PDF 同样保留为历史说明；当前启动与复算方式以本页为准。

## 查看固定版本的公开案例

- **既有五算法保持裕量流程案例**：使用 [GitHub Release：hold-snm-workflow-bd09101](https://github.com/Weize-Lin/OpenYield/releases/tag/hold-snm-workflow-bd09101)。标签固定到 `bd09101003e654bd41b090949b5afdc2a6cd2fc2`；按 [首次使用指南](../../docs/research/YIELD_QUICKSTART.md) 在对应源码上离线复算。不要将当前分支当成旧案例的原始运行源码。
- **本次 Fusion 两电路 MC 闭环案例**：查看 [报告、四表与精简证据包](docs/acceptance-80f3df7/README.md)。实际运行源码固定为 `80f3df7ff83d2c8e7eae751ed10d5111d485ab1f`，原始证据保存在提交 `f159e3e`；按报告中的命令解压后复算，不启动 Xyce。
- **随仓库只读演示**：使用上面的 `--demo` 或静态页面。它保留同学原来的 `312f137` 来源，不能当成当前分支的重新仿真结果。

这三类材料用途不同，不混用源码、参考或精度结论。本轮没有移动旧标签、替换 Release 附件或修改历史演示数据。

## 正常运行时怎样核对来源

不再把后端钉死在某个提交号。工作台检查共享 API 版本和入口，并确保实际导入的模块来自配置仓库。
启动真实任务要求已跟踪源码干净；服务启动后改变提交，需要重启，避免内存代码和子进程代码不同。
未跟踪文件不自动删除，其状态会记录。

每个新任务的 `job.json` 和 `launch*.json` 保存真实提交、修改状态、仓库路径和软件接口版本。
后端继续保存每次实验的源码、器件模型哈希、Xyce 版本和可执行文件哈希；共享分析器继续检查这些来源。
软件接口兼容不代表算法科学有效，也不会把历史演示改标为新版本。

## 多电路套件的限时与复算

30 秒表示整轮子进程执行时间上限：第一个电路花 20 秒，第二个最多只剩 10 秒。
取消、超时和启动错误都保留全部计划。终止宽限最多 **30 秒**，用于清理进程；最终离线分析另计。
不承诺“含分析的总墙钟时间”严格等于运行限时。

开始前会写入：

- `suite_manifest.json`：完整电路列表、每个冻结清单的 SHA-256、实际输出位置。
- `plans/<编号>/`：冻结配置、单电路计划和参考摘要哈希。
- `suite_status.json`：各电路已完成、报错、超时、取消或未启动状态。

查看 `analysis/runs.csv` 的每条原始估计、状态、成本、告警和来源；
`methods.csv` 展示各电路内按策略与预算分组的汇总；`groups.csv` 核对条件；
`references.csv` 展示参考及区间。跨电路按列展示，不把不同电路混成一个准确率或权威排名。
没有估计的运行仍计入分母；低权重、超 1 的差结果不会因不好看而被删除。

保存后用同一个共享分析入口重新生成表格，输出必须是新目录：

```bash
python -m yield_estimation.evaluation.analyze_results \
  --manifest outputs/fusion_workbench/<任务ID>/suite_manifest.json \
  --output-dir outputs/reanalysis-<新名字>
```

网页“导入已有分析清单”也接受这个套件清单。旧的单电路 `groups` 清单继续兼容。
Fusion 旧版只含 `suite: [路径]` 的不完整清单不自动升级：它没有冻结的未启动计划，不能补造历史证据。

## 公开离线检查

在根目录、已激活依赖环境执行，不调用 Xyce 或 GPU：

```bash
CUDA_VISIBLE_DEVICES='' OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 PYTHONPATH="$PWD/tests:$PWD" \
python -m unittest test_evaluation_weight_contract test_research_experiment \
  test_estimation_contract test_adaptive_production_contract test_result_analysis \
  test_local_web test_acs_initialization_study test_sphere_connection test_execution_limits -v

CUDA_VISIBLE_DEVICES='' OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 PYTHONPATH="$PWD" \
python -m unittest discover -s workbench/fusion/tests -p 'test_*.py' -v

node tests/test_local_form.cjs
node workbench/fusion/tests/test_suite_form.cjs
```

Fusion 的固定数据在临时目录生成，明确标为模拟数据；不需要任何人的私人 `outputs/`。
限时逻辑用替代执行器和模拟时钟检查；受控取消使用可立即终止的 Python 子进程，没有真实电路等待。
可选浏览器检查先安装 `npm --prefix workbench/fusion install`、`npx playwright install chromium`，
启动上面的只读演示，再执行 `node workbench/fusion/tests/browser.mjs`。
已有浏览器可通过 `OPENYIELD_BROWSER_EXECUTABLE` 指定；测试地址用 `OPENYIELD_TEST_URL` 指定。
浏览器检查只允许连接演示模式，避免意外启动仿真。

## 导出新的只读演示

```bash
python workbench/fusion/tools/build_demo.py --job <任务ID> \
  --destination outputs/fusion-export-<新名字>
```

导出新目录，存在则拒绝，不覆盖仓库公开演示。每轮保留首个批次的电路附件，
样本、配置、报告、日志和参数测量表按实际可用证据导出。

## 归属与范围

OpenYield 原项目及双方协作贡献继续按 Apache-2.0 保留，见 `NOTICE`、Git 历史及原有说明。
整合没有改变 MC/MNIS/AIS/ACS/HSCS 的算法策略、模型或门限，没有接入 TabPFN/GPU、代码上传或服务器部署。
离线接入检查和后续真实验收分别留档；新工作台已完成上述两个 MC 任务的 Xyce 闭环。
这不覆盖所有配置、其他四种算法或真实异常分支，也不构成算法精度认证。
