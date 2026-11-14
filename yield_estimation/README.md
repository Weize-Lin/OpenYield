# Yield estimation in OpenYield V2.2.6

The current compiler can generate reproducible, independent per-device process samples. The legacy importance-sampling implementations in `model_lib/` are research code and are **not a validated V2.2.5 yield flow**. They still depend on machine-local paths, optional ML packages absent from `environment.yml`, and the older custom-variation interface. The old root debug driver was removed because it did not provide a working current-release tutorial.

## Generate current process samples

From the repository root, set `ARRAY = [8, 4, False]`, `OPERATION = "read"`, `REAL_CELL_MODE = 0`, `MC_RUNS = 100`, `MC_SEED = 3`, and `RUN_XYCE = True` in `main_sram.py`. With the OpenYield environment and Xyce available, run:

```bash
python main_sram.py
```

Use mode 0 for full device coverage and record the corner, VDD, temperature, wire model, timing table, driver baseline, model cards, and seed with the results. The main entrance writes measurement data under `outputs/main_sram/`. Set `VARIATION_MODE = "nominal"` and `MC_RUNS = 1` for a fixed-corner diagnostic reference. See the [batch runner tutorial](../sram_compiler/per_device_mc/README.md) for sample and output semantics.

This command produces circuit results, not a qualified failure probability. A yield study must define failure using waveform-based read, write, retention, sensing, and recovery checks, count numerical non-completion separately, and establish coverage across PVT and mismatch. The [V2.2.5 screen](../docs/V2_2_5_SIMULATION_SUMMARY.md) has not completed. The [open items](../docs/README.md#open-items) describe the work needed to connect samples to release checks and validate the estimators.

## Run a configured Monte Carlo read evaluation

From the repository root, activate the `openyield` environment with Xyce and
the CPU PyTorch dependency used by the existing MC module. Then run:

```bash
python -m yield_estimation.evaluation.run \
  --config yield_estimation/evaluation/cases/read_3x2_rel5.json \
  --output-dir outputs/oct01_mc_smoke
```

Use a new output directory for each run. This entry point uses the repository's
direct MC algorithm on a 3×2 6T array, reading cell `[1, 1]` (zero based)
with full transistor detail and column mux disabled.

| Setting | Meaning |
| --- | --- |
| `case` | `sram_6t_3x2_read` selects the supported circuit and operation. |
| `corner`, `vdd`, `temperature_c` | Model corner, supply in volts, and temperature in degrees Celsius. |
| `relative_std` | Gaussian scale divided by the magnitude of the nominal value, before truncation. `0.05` means 5%. |
| `u0_positive` | Must be `true`. The mobility distribution is conditioned on positive values. |
| `threshold_ns` | A valid read slower than this limit counts as a timing failure. |
| `seed`, `budget`, `batch_size` | Random seed, total samples, and maximum samples in each batch. |

Samples independently vary `vth0`, `u0`, and `voff` of the 36 cell transistors
around the selected model cards. Replica and peripheral devices use nominal
parameters. The compiler resolves and freezes the driver sizes and timing
for the run. The example's 1 ns limit and four samples are for checking the
evaluation workflow. Choose the experiment's limit and budget separately.

- `run_config.json` records the input configuration.
- `samples.csv` records the read delay and outcome for every submitted sample.
- `run_summary.json` records counts, estimate, Git revision, and circuit and solver inputs.
- `report.md` gives a readable summary with a Wilson 95% interval for a completed run.
- `batch_*/` contains the sampled parameters and the original Xyce outputs.

The measured outcomes are pass, timing failure, and confirmed read access
failure. A batch with a solver error, missing evidence, or rejected precharge
check retains its inputs and diagnostic files. The run reports an incomplete
result and leaves the overall estimate empty. See the Xyce log and raw
measurements to investigate the affected batch.

## Legacy algorithm modules

| Module | Research method |
|---|---|
| `model_lib/MC.py` | Direct Monte Carlo baseline |
| `model_lib/MNIS.py` | Mean-shifted importance sampling |
| `model_lib/AIS.py` | Adaptive importance sampling |
| `model_lib/ACS.py` | Adaptive compressed sampling |
| `model_lib/HSCS.py` | High-dimensional sparse compressed sampling |

`tool/Distribution/` contains their distribution helpers. The configured evaluation above runs MC. The other modules are available for porting and comparison. The import-time deletion of a machine-local simulation directory has been removed from `tool/delete.py`; callers still need to supply their own output directories.
