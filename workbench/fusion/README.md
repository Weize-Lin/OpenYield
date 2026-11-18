# OpenYield Fusion · 良率评测工作台

一个把 OpenYield 的 SRAM 良率估计算法接成“可提交、可比较、可追溯”的评测工作台。
保留林同学协作分支的 Python 实验/分析核心作为权威，重组织网页为研究工作台，
并补齐老师原始任务中要求的跨电路自动评测、电路证据查阅与建模数据留存。

> English abstract: A local-first evaluation workbench for OpenYield SRAM yield
> estimators. It wraps the upstream `evaluation.experiment` / `analyze_results`
> entry points (commit `312f137`) with a web UI for multi-circuit suites,
> method × circuit matrices, per-run evidence (samples / config / report / log /
> waveform / netlist), and parameter→measurement dataset export. Ships a
> zero-dependency read-only demo mode and a static GitHub-Pages site built from
> real recorded experiments.

## 三种运行形态

| 形态 | 命令 | 依赖 | 用途 |
|---|---|---|---|
| 完整模式 | `Start-OpenYield-Fusion.cmd`（Windows）或 `python server.py` | OpenYield 副本 + Conda 环境 + Xyce 7.4 | 校验、提交真实仿真与多电路套件、分析、证据 |
| 演示模式 | `python server.py --demo demo_data --port 8767` | 仅 Python 标准库 | 只读浏览随仓库分发的真实证据包 |
| 静态站点 | 打开 `demo_site/index.html`，或部署到 GitHub Pages | 无 | 发给老师/同学直接看的只读演示 |

完整模式默认端口 8766；演示示例用 8767。原工作台（3417）与林同学原页面（8765）不受影响。

### 完整模式环境

- `OPENYIELD_REPOSITORY`：OpenYield  checkout 路径（默认 `~/openyield-web-312f137`，固定提交 `312f137e9ab960f1178f4991d9bf4675575e2f01`）。
- Python 需能 import `yield_estimation.evaluation`（numpy / PySpice / Xyce 等，见上游 `environment.yml`）。
- 运行前会核对 git 版本与未提交修改；参考种子必须与实验种子独立。

### 重新生成演示包 / 静态站点

```bash
python tools/build_demo.py --job <已完成的任务ID>
```

从真实任务输出打包 `demo_data/` 与 `demo_site/`。演示包是精简子集：每轮保留
summary / samples / config / report / log / 建模数据集，以及**首个批次**的波形图、网表、扰动与统计文件。

## 仓库结构

```
server.py            服务端（完整 + 演示模式；套件、证据、数据集接口）
public/              前端（index.html / app.js / style.css），同一前端支持静态演示数据
tools/build_demo.py  演示包与静态站点生成器
tests/               后端 unittest 与 Playwright 浏览器回归
demo_data/           只读演示证据包（真实实验精简子集）
demo_site/           静态只读站点（可直接放 GitHub Pages）
docs/ROADMAP.md      老师原始目标 → 当前状态 → 下一步
操作手册.html / .pdf  面向零基础用户的图文手册
MILESTONES.md        关键里程碑与验收证据日志
```

## 已验证（2026-11-18）

- 后端 13 项 unittest：配置预览、证据一致性、独立参考、冻结计划缺失轮、路径/符号链接限制、
  历史恢复、分页、参考种子重合拒绝、接口保护、受控取消、套件校验与上限、电路证据/数据集、只读演示包。
- 浏览器 11 组回归：真实历史、五类证据+电路证据、下载、矩阵下钻、筛选、配置保留、异步校验、
  小屏、六轮汇总、套件按电路分列、静态演示只读。
- 真实仿真：六轮 MNIS 对照（72 条返回）+ 双电路套件（7 轮、76 条返回）。详见 `MILESTONES.md` F5/F8 与 `artifacts/`。

**边界**：小样本只验证流程与记账，不构成算法性能结论；波形/网表供人工核对，不做自动物理验收；
TabPFN 尚未接入（见 ROADMAP）。

## 许可与归属

Apache-2.0（见 `LICENSE`）。实验/分析核心来自 [OpenYield](https://github.com/ShenShan123/OpenYield)
及林同学协作分支 [Weize-Lin/OpenYield](https://github.com/Weize-Lin/OpenYield)（固定提交 `312f137`），
本仓库仅新增网页工作台、套件编排、证据/数据集接口与演示打包，不修改上游算法。见 `NOTICE`。
