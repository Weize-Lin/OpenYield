# Local checks for V2.2.6 and preserved V2.2.4 manifests

The [evaluation weight contract test](test_evaluation_weight_contract.py) is public. From the repository root:

```bash
python -m unittest discover -s tests -p test_evaluation_weight_contract.py -v
```

Other Python regression and waveform scripts are kept in local workspaces and ignored by Git. A fresh clone includes this guide and the JSON case manifests under `spice/`, but does not include the Python runner. The manifests preserve the planned V2.2.4 cases; they are not results or qualification evidence.

If your workspace retains the local scripts, run software checks from the repository root:

```bash
python -m pytest -q tests size_optimization/openyield_v2/tests
```

Most compiler checks generate decks or inspect source structure without Xyce. Equivalent-model extraction checks require Xyce even during deck construction. `python -m unittest discover -s dev/tests -v` checks optional ignored development tools when they are present. For a fresh clone, use the compiler's nominal mode 0 smoke command in the [root tutorial](../README.md#validate-changes).

The local compiler checks cover configuration loading, timing and driver lookup, distributed RC connectivity, per-device model specialization, startup conditions, sense and precharge ordering, runner retries, and output parsing. They are useful regression checks, but generated netlists or passing measures do not prove read, write, hold, sense margin, or recovery waveform correctness.

The [SPICE guide](spice/README.md) describes the optional local waveform runner and these tracked manifests:

| Manifest | Planned cases |
|---|---:|
| `spice/v224_cases.json` | 334 main cases, including four dynamic mux reads |
| `spice/v224_mc_cases.json` | 1,444 per-device draws |
| `spice/v224_negative_cases.json` | 6 negative controls |

The V2.2.4 manifests were partially run as an unfinished V2.2.5 screen, and 256x256 still lacks a working operating point. The [V2.2.5 simulation summary](../docs/V2_2_5_SIMULATION_SUMMARY.md) records the stopped campaign and its old model hashes; the [V2.2.2 record](../docs/design/PHASED_CONTROL_V2_2_2.md) describes the last assembled screen, which certifies only that older tree.

The [local web contracts](test_local_web.py) exercise HTTP validation and task orchestration with replacement processes, without Xyce.

```bash
python -m unittest discover -s tests -p test_local_web.py -v
```

The dependency-free browser form regression exercises the production JavaScript handlers and imported strategy IDs.

~~~bash
node tests/test_local_form.cjs
~~~

The [ACS initialization study contracts](test_acs_initialization_study.py) check
surface coordinates, positive-mobility mapping and the real production ACS
budget/estimation path with analytic measurements. No Xyce or GPU is used.
The [frozen protocol](../docs/research/acs_initialization/PROTOCOL.md) separates
project choices from claims about the paper.

```bash
CUDA_VISIBLE_DEVICES='' OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
  python -m unittest discover -s tests -p test_acs_initialization_study.py -v
```

The [shared sphere initialization contracts](test_sphere_connection.py) exercise
18-dimensional physical mapping, the current ACS estimation loop, legacy
compatibility, and candidate/budget exhaustion with analytic measurements.
The [execution limit contracts](test_execution_limits.py) use Python fixture
processes to check task/global deadlines, parent termination, grandchild cleanup,
and preservation of all planned tasks in offline analysis. Neither calls Xyce.

```bash
CUDA_VISIBLE_DEVICES='' OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 PYTHONPATH="$PWD/tests:$PWD" \
  python -m unittest test_sphere_connection test_execution_limits -v
```

Fusion integration regression commands and scope are documented in
[the workbench guide](../workbench/fusion/README.md). Its unittest fixtures are
generated in temporary directories; suite deadlines use fake clocks/executors.
No private output tree, Xyce, or GPU is required.

## 保存样本的独立核验与前向记录

公开CPU回归不需要Xyce或私人实验目录，执行命令和覆盖边界见[样本核验指南](../workbench/fusion/SAMPLE_VERIFICATION.md)。
包括原合格要求来源、一致/冲突/缺失、必要波形/多样本对应、原标签保留、求解尝试记录、新任务自动衔接、取消/中断和路径限制。
完整原始数据不作为测试依赖；仓库只含派生软件夹具。测试通过不等于电路精度、求解精度或通用良率算法已验证。
