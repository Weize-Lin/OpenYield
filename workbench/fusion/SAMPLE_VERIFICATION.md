# 在已有任务里核验样本

这项功能只检查保存的数据，不启动 Xyce。核验结果另存，不覆盖平台原标签，不重算良率或排名。

## 当前分支与使用入口

当前是待整合的本地交付分支 `codex/deliver-sample-verification`，尚未推送、尚未合入 `codex/benchmark-oct01`。
因此当前日常开发分支还没有这项核验首版；需在包含本提交的工作目录使用。
经用户批准合入后，日常继续选择 `codex/benchmark-oct01`，启动方法相同，不另建第二套平台。
历史公开案例的固定标签不移动。

## 启动和进入

在包含本功能的仓库根目录运行：

```bash
conda activate openyield
bash workbench/fusion/start.sh
```

打开 http://127.0.0.1:8766 。在“证据记录”选择任务，点击该轮“查看证据”，再点“样本核验”。
普通任务自动读取 run_config.json、samples.csv 及 batch_* 文件。无需用户填写机器路径、门限或波形段。

## 新建实验后直接核验

从“配置实验”选择3×2 6T、读、目标(1,1)、1 V、mode0、无列复用、custom抽样，按研究目的填写MC预算/种子和原延迟要求。该窄范围还要求2.5 ns基线、7.12 ns观察和19 ns结束的当前协议。
也可导入[已实测流程配置](../../docs/research/sample_verification/new_task_config.json)：一个MC种子、2条样本、1 ns流程要求。它是功能演练配置，不是推荐良率分析预算或工业规格。导入该示例后，将页面“整场限时”设为120秒、参考留空；这些页面启动字段不在config.json里。
点击“校验当前配置”只检查配置规则；点击“运行已校验配置”才会真实求解，要先安排资源。
结束后在同一任务“证据记录 → 查看证据 → 样本核验”检查新样本，不需要运行登记工具、搬文件或补清单。
完整真实验收范围见[验收说明](../../docs/research/sample_verification/ACCEPTANCE.md)。

## 已有任务怎样核验

先看逐样本支持范围和材料缺口。可以核验全部样本，或选择“指定样本”，输入保存的样本编号，用逗号分开。
点击“开始离线核验”。状态只显示实际处理状态及已完成数，不显示猜测百分比。
取消在当前样本读取完成后生效，已检查和未启动项都保留；关闭详情不取消后台检查。

## 看什么

- 原判断：原 samples.csv 或已登记历史记录的标签，不是核验程序重新推算的标签。
- 仿真完成情况：由保存的日志、时间覆盖和结束标记独立观察；原退出码未知就显示未记录。
- 独立读出观察：存储状态、必要控制、输出电压是否支持读0。不能只因测量 FAILED 判定物理失效。
- 速度：按原配置的要求及来源字段检查。缺少原要求，功能可观察，性能结论不足；条件冲突不是原标签错误。
- 核验结论：支持原判断、存在冲突、证据不足、不支持。可筛选，冲突分参数对应、检查条件、测量、电路观察。

点“打开样本”可以看原要求、来源文件及字段、关键电压/时间和输入参数。波形此时才读取，按该样本的段号绘图。
图上有中文节点说明和 ns/V 单位。显示抽点保留区间极值，只影响图形；核验使用原始保存点。
图形不是求解精度证明。没有可确认节点或波形段时明确报错，不显示空图。
需要图片时点“导出波形图片”，主动生成 PNG；不预先为所有样本生成图片。

CSV 和详细 JSON 可下载。每次结果保存在所属任务的 sample_checks/<检查编号>/ 中，包括对象清单、工具文件哈希、源码状态、输入哈希和状态。
重复核验生成新目录，不覆盖之前记录。服务重启时未结束检查标记为中断，不自动重跑或假称完成。

网页关闭后也可用命令行复算该次已完成的样本，不启动服务或求解器：

```bash
python workbench/fusion/sample_verification.py \
  --check outputs/fusion_workbench/<任务编号>/sample_checks/<检查编号>/check.json \
  --output-dir outputs/check-replay-new
```

输出目录必须是新目录。复算先核对工具和原输入哈希。原检查若取消或部分失败，只复算当时已完成项，不冒称完成全部原计划。

## 支持边界

沿用 tools/verify_saved_6t_read.py 的 saved_6t_read0_3x2_v2_threshold_bound 规则：3×2 6T、mode0、无列复用、目标(1,1)、读0、理想1 V，2.5 ns基线、7.12 ns观察、19 ns结束，以及规则所列的网表和延迟事件定义。
这不是所有6T读操作，也不是10T、写、保持裕量或任意时间协议的通用检查。
材料存在不等于结果一定可确认。缺节点、缺原标签/原要求、映射冲突或保存点靠近门限，都可能无法确认。
只读演示站点不创建检查，也不假装支持包中没有的原始波形。

## 原来的任务和公开案例怎样查看

工作台自动读取所选output-root下的已有job.json。默认是启动仓库的outputs/fusion_workbench。
查看其他已存在的工作台任务，可启动时传 `--output-root /path/to/existing/jobs`；不需要重新运行电路。
输出不会因为Git切分支或合并自动搬到另一个仓库目录。要看本地旧任务，应选择它真实保存的位置，原源码/模型/求解器记录仍按原值显示。
公开保持裕量案例继续使用原[固定Release](https://github.com/Weize-Lin/OpenYield/releases/tag/hold-snm-workflow-bd09101)。SNM不属于当前读样本核验范围；能查看该案例不等于能核验其中每个样本。
只读网页演示 `python workbench/fusion/server.py --demo workbench/fusion/demo_data --port 8767` 不创建核验，也不伪造未包含的波形。

## 已有材料的可追溯登记

这是本机管理操作，不是网页任意文件读取接口。路径由管理者在终端明确指定。
原生运行目录已有完整记录时：

```bash
python workbench/fusion/tools/register_saved_read_task.py \
  --run-directory /path/to/saved/run \
  --output-root outputs/fusion_workbench --name '已有读任务'
```

旧核验材料已有清单时：

```bash
python workbench/fusion/tools/register_saved_read_task.py \
  --manifest /path/to/saved/manifest.json \
  --output-root outputs/fusion_workbench --name '历史材料适配'
```

工具复制原文件并记录来源与哈希。旧材料在页面明确标记“适配”，不补造标签、退出码或良率；缺失事实仍缺失。
登记后重启工作台加载该任务。网页接口只接收任务、轮次、样本和核验编号，不接收任意本机文件路径。

## 离线回归

仓库根目录，使用已有 Python 环境：

```bash
export PYTHONPATH="$PWD"
python -m unittest discover -s workbench/fusion/tests -p 'test_*.py' -v
python -m unittest discover -s tools -p 'test_verify_saved_6t_read.py' -v
python -m unittest discover -s tests -p 'test_forward_read_evidence.py' -v
python -m unittest discover -s tools -p 'test_saved_read_control.py' -v
```

软件夹具与真实保存记录分开计数。浏览器验收使用 Edge/Chromium 的真实页面操作，但不启动电路求解。
回归所需派生夹具已入库；不需要私人outputs、未提交文件、完整原始包、Xyce或GPU。Python依赖仍按environment.yml安装。
完整证据包与源码分开登记，见[ARTIFACTS.json](../../docs/research/sample_verification/ARTIFACTS.json)，尚未发布的包不假装已有公开链接。
公开夹具测试不是新的真实电路验收。可选浏览器回归使用Playwright和已安装Chromium，包路径与浏览器路径通过环境变量指定。
默认核验命令只读取保存材料；这里没有新增支持10T、写或SNM的判定规则。
