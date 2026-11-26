# 样本核验首版的待整合内容

## 当前状态

目标是日常开发分支codex/benchmark-oct01。实时审查的远程目标为fc739098bbc6c0d0baed05aa750758031ee2daaa，同学fusion-workbench为45a295c27dcb7d7ca69090a24f12582c3b94c21c；同学提交已经是目标分支的祖先，没有新增分支内容遗漏。
本地codex/deliver-sample-verification从该最新目标出发，承接7a0a834中核验首版必要文件；没有在目标或同学分支上直接改动。
原codex/web-sample-verification仍停在7a0a834，历史研究和完整证据保留。当前未推送、未创建新PR、未合并。

原核验分支在目标前方13个提交，含125个变化文件和多份重复完整波形包。这里准备的是可单独审查的精简交付，不直接推送那13个历史提交。
完整历史材料已在本地另行保留，公开测试只带约1.1MB派生夹具；不是删除或改造原始数据。

## 必须一起交付的依赖链

| 层次 | 文件 | 用途 |
|---|---|---|
| 原始执行记录 | utils/xyce.py | 每次调用/尝试、原生/有效退出码、数值重试归档、命令及文件哈希；不推断未知完成行数。 |
| 样本记录 | evaluation/batch_evidence.py、evaluation/run.py | 记录配置、实际输入表/矩阵、批次行号、原标签、合格要求及波形/测量对应；原分类和估计分母不改变。 |
| 独立核验 | tools/verify_saved_6t_read.py、sample_audit.py、check_saved_read_control.py | 读取原合格要求来源、参数映射、原始波形、电压与事件，分开原标签与独立结论；不调用生产分类器。 |
| 网页适配 | fusion/sample_verification.py、server.py | 从正常任务自动定位文件，只读离线调用、状态/取消/历史、CSV/详细JSON及按需波形；限制任务范围内路径。 |
| 用户界面 | fusion/public中的详情页、sample-verification.js、样式 | 同一任务的入口、筛选、详情、中文节点与单位、用户主动导出；不在JS重写判定。 |
| 旧材料入口 | fusion/tools/register_saved_read_task.py | 本机显式登记保存材料，保留来源；正常新任务无需此步。 |
| 软件回归 | Fusion测试、tools独立核验/节点测试、tests/test_forward_read_evidence.py | 原条件来源、缺失/冲突、多段、输入映射、尝试记录、未完成状态和正常新任务自动索引。 |

不能只挑网页文件合入；server的check服务依赖独立核验模块，新任务的完整对应记录依赖前向记录器和utils。整个候选必须整体审查。
良率算法文件和生产outcomes判据没有改；旧的returned_rows语义和未知统计字段也没有在此次被偷偷调整。

## 使用入口与日常目录

普通研究者只需在其包含本功能的仓库根目录激活环境，运行 `bash workbench/fusion/start.sh`，打开http://127.0.0.1:8766。
新建→运行→同任务核验、旧结果、原公开案例、路径配置及不支持/证据不足解释均在[公开使用指南](../../../workbench/fusion/SAMPLE_VERIFICATION.md)，不依赖私人日志。

本机当前仍在原有的 `~/projects/OpenYield-web-sample-verification` 目录，现选择codex/deliver-sample-verification；日常的 `~/projects/OpenYield-fusion` 目录仍是尚未合入核验首版的开发分支。
批准合入后，计划统一使用已有OpenYield-fusion日常目录和codex/benchmark-oct01，不再创建新代码目录。合入/更新前必须重新核对该目录未提交文件，不覆盖它们。
已经保存的结果仍在它们原来的输出目录，不自动搬家。当前看本次真实新任务，可在原接入目录启动时指定 `--output-root outputs/new_task_read_web_20261126/jobs`；日常默认目录为其自己仓库的outputs/fusion_workbench。

## 证据与未验证事项

真实验收见[ACCEPTANCE.md](ACCEPTANCE.md)。完整档案作为独立附件安排，本轮未发布；源码里是精简摘要与明确标为软件夹具的数据。
本轮变更主要是挑选完整必要模块、调整公开夹具位置和文档入口，没有重写生产核验或算法。代码/测试候选dd99645148c2ffe9531b1351eac0cc1dae82c339已从只包含提交文件的临时干净检出复跑：Fusion45、独立核验28、记录15、节点映射2，共90项通过。测试子进程禁止读取原私人项目目录及调用Xyce，无违规尝试，检出后工作区仍干净。记录见[offline_validation.json](offline_validation.json)。这是复用已有依赖的干净源码检查，不是重新安装或冷缓存环境验证。后续只补本说明、测试命令和验收摘要，没有修改生产或测试代码。
合并冲突的判断以实时远程为准：当前目标是候选基线，且同学代码已包含；若后续有新提交，必须重新比较，不能自动沿用此结论。

## 等待确认的Git操作

拟仅推送codex/deliver-sample-verification，PR目标为codex/benchmark-oct01；不推送原13个历史核验提交，不改main，不移动公开案例标签，不删除或重置同学分支。
具体候选提交、离线检查和diff应先交用户审查。推送、PR和最终合并分别按用户后续确认执行，本轮没有执行这些动作。

## 草稿后的集中审查

f375eba已推送并建立草稿PR #2，尚未合并。本地针对记录故障、任务文件访问及已有证据锚点完成必要修复，待用户确认推送；之前“未推送”段落保留为准备时记录。具体反例、代码范围和离线结果见[集中审查](PR_REVIEW.md)。修复后不再声称全部生产核心与真实验收7a0a834字节相同。
