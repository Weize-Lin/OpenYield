# OpenYield V2.2.6

**整合预览：** [Fusion 跨电路工作台的启动、证据查看与套件复算](workbench/fusion/README.md)。当前在独立集成分支评审；历史公开案例和算法入口继续保留。

OpenYield builds transistor-level 6T and 10T SRAM netlists, runs DC and transient analyses with Xyce, and provides circuit-backed and offline sizing optimizers. The compiler models distributed RC wiring and samples independent local `vth0`, `u0`, and `voff` variation for each retained MOS by default.

**Release status:** V2.2.6 fixes custom SNM construction, model-audit false rejections, sample-count validation, and single-cell coverage metadata. See the [configuration review](docs/design/CONFIGURATION_REVIEW_V2_2_6.md) for reproductions and validation. No full V2.2.6 screen has completed. The V2.2.5 campaign stopped incomplete; the [V2.2.5 simulation summary](docs/V2_2_5_SIMULATION_SUMMARY.md) records it with the corrected-model diagnostics, which retain their original scope. The configured limit is 512 rows by 256 columns, and 256x256 still lacks a converged operating point. The completed [V2.2.2 screen](docs/design/PHASED_CONTROL_V2_2_2.md) certifies only that earlier tree.

## Quick start

Run commands from the repository root. The supplied Conda environment includes Python, PySpice, and Xyce:

```bash
conda env create -f environment.yml
conda activate openyield
command -v Xyce
```

Edit the settings at the top of [`main_sram.py`](main_sram.py). Set `ARRAY = [8, 4, False]`, `OPERATION = "read"`, and `RUN_XYCE = False` to generate a full-transistor deck without a simulator:

```bash
python main_sram.py
```

Set `RUN_XYCE = True` to simulate the configured macro. The default seed makes the local process sample repeatable; set `MC_RUNS` to draw more samples. Decks and results go under `outputs/main_sram/`. Xyce is also needed during deck construction if you select equivalent mode 1–4.

`MC_RUNS` must be a positive integer, or `None` to use the YAML setting.
SNM operations simulate one cell and do not provide full-array mismatch coverage.

The optional [command-line runner](sram_compiler/per_device_mc/README.md) supports batch generation and explicit overrides. It uses the same local variation default.

## Configure an array

`main_sram.py` is the SRAM macro entrance. Its settings block controls `ARRAY = [rows, cols, column_mux]`, corner, 6T cell widths, operation, seed, target cell, and whether to run Xyce. It loads YAML into memory; it does not rewrite the tracked configuration files. For an unvaried diagnostic corner, set `VARIATION_MODE = "nominal"`.

Circuit defaults live in `sram_compiler/config_yaml/`. `global.yaml` sets supply, temperature, geometry, cell type, timing, wiring, and equivalent mode. Cell and peripheral YAMLs define transistor sizes in **metres**. Clocks come from [timing_lookup.json](sram_compiler/sizing/timing_lookup.json), and critical driver sizes from [sizing_lookup.json](sram_compiler/sizing/sizing_lookup.json). Freeze both across candidate cells and PVT samples. The default wire geometry is illustrative rather than extracted metal; set `INTERCONNECT_CONFIG` to an interconnect YAML when you have measured geometry. The [compiler tutorial](sram_compiler/README.md) covers configuration and the Python API.

| Variation mode | Meaning |
|---|---|
| `nominal` | One fixed PDK corner, no Monte Carlo draw |
| `per-device` | Independent `vth0`, `u0`, and `voff` draws per retained MOS; automatic default |
| `shared` | One random model card per base model |
| `custom` | Explicit parameter table from the cell YAML |

`REAL_CELL_MODE = 0` keeps every transistor and is the reference for full device coverage; the YAML default is also 0. Modes 1–4 replace selected unused cells with approximate equivalent loads and need Xyce even during netlist generation. Compare them against mode 0 using the [equivalent-model guide](sram_compiler/equivalent_modeling/README.md). A write currently drives every column in the selected row; half-select writes remain open work.

## Sizing and yield workflows

Circuit-backed optimization is under [`size_optimization/`](size_optimization/README.md). For example, after installing its optional dependencies:

```bash
python size_optimization/demo_sa.py
python size_optimization/experiment.py
```

`experiment.py` offers joint architecture and transistor sizing or sizing at fixed configurations. The separate [OpenYield V2 offline package](size_optimization/openyield_v2/README.md) trains on bundled static data and does not call Xyce during optimization:

```bash
python -m pip install -r size_optimization/openyield_v2/requirements.txt
python -m size_optimization.openyield_v2.run_experiment --dry-run
```

Legacy importance-sampling estimators are retained in [`yield_estimation/`](yield_estimation/README.md), but they are not a validated V2.2.6 yield workflow. Use `MC_RUNS` in `main_sram.py` for current sampling, and read the [open items](docs/README.md#open-items) before reporting a yield estimate.

### Reproduce a yield comparison

Use the [local yield interface](docs/research/LOCAL_WEB.md) to edit and validate a configuration, run the existing experiment entry point, and inspect estimates, costs, warnings and independent references. See the [configuration map](docs/research/CONFIGURATION_MAP.md) for supported inputs and their limits.

Start with the [first-use guide](docs/research/YIELD_QUICKSTART.md) to install the environment, replay a public SRAM yield comparison offline, validate a custom configuration, and read the results.

The [published 6T hold-SNM case](https://github.com/Weize-Lin/OpenYield/releases/tag/hold-snm-workflow-bd09101) includes the independent MC reference, run records, and an offline replay. The [earlier hold-SNM study](yield_estimation/evaluation/HOLD_SNM_STUDY.md) documents the historical experiment.

## Validate changes

```bash
python -m compileall -q sram_compiler utils size_optimization yield_estimation
python main_sram.py  # set RUN_XYCE=False for deck generation without Xyce
```

The [public weight contract checks](tests/test_evaluation_weight_contract.py) exercise estimator imports,
analytic reference probabilities, and explicit settings:

```bash
python -m unittest discover -s tests -p test_evaluation_weight_contract.py -v
```

The [estimation acceptance report](yield_estimation/evaluation/ESTIMATION_ACCEPTANCE.md) documents CPU-only analytic checks, the ACS clustering audit, and reproducible test commands.

The [MNIS offline selection evidence](docs/research/mnis_selection/README.md) includes complete candidate batches and a replay that requires no circuit simulation.

See [estimator validation coverage](docs/research/ESTIMATOR_COVERAGE.md) for the AIS and HSCS CPU regression and the remaining evidence gaps across five methods.

To compare saved runs without circuit simulation, see [Offline result analysis](yield_estimation/evaluation/RESULT_ANALYSIS.md).

For configurable algorithm experiments, see [Research experiment configuration](yield_estimation/evaluation/RESEARCH_EXPERIMENT.md). The JSON and Python entry point shares one circuit and failure rule across estimators, with per-run budgets and proposal evidence.

The other regression and waveform Python scripts are retained in local, ignored `tests/` workspaces. If you have them, run `python -m pytest -q tests size_optimization/openyield_v2/tests`; some equivalent-model checks need Xyce. The tracked [SPICE manifests](tests/spice/README.md) describe the unfinished V2.2.5 screen; no V2.2.5 or V2.2.6 full screen has completed. Passing software tests or `.MEASURE` cards alone does not establish read, write, retention, sense, and recovery correctness.

## Guides and project map

| Path | Start here for |
|---|---|
| [Compiler tutorial](sram_compiler/README.md) | Configuration, testbenches, results, and common failures |
| [Sizing and timing](sram_compiler/sizing/README.md) | Frozen driver classes and clock budgets |
| [Equivalent models](sram_compiler/equivalent_modeling/README.md) | Modes 0–4 and accuracy comparison |
| [Sizing optimization](size_optimization/README.md) | Circuit-backed and offline optimizer entry points |
| [Shared utilities](utils/README.md) | Measurement, waveform, plot, area, and SPICE helpers |
| [Local checks](tests/README.md) | Retained manifests and optional local regression scripts |
| [Documentation index](docs/README.md) | Release records, evidence, and remaining work |
| [Changelog](docs/CHANGELOG.md) | Version history |

The main flow is YAML → `SRAM_CONFIG` → subcircuit factories → `Sram6TCoreMcTestbench` → Xyce measurements and waveforms → optimization or analysis. Generated decks and results belong under ignored `outputs/`.

## Cite us

```bibtex
@INPROCEEDINGS{OpenYield,
  author={Shen, Shan and Li, Xingyang and Liu, Zhuohua and Ma, Junhao and Wang, Yikai and Wu, Yiheng and Sun, Yuquan and Xing, Wei W.},
  booktitle={2025 IEEE 43rd International Conference on Computer Design (ICCD)},
  title={OpenYield: An Open-Source SRAM Yield Analysis and Optimization Benchmark Suite},
  year={2025},
  pages={167-175},
  doi={10.1109/ICCD65941.2025.00030}
}
```
