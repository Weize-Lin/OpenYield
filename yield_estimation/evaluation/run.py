"""Evaluate direct Monte Carlo on the configured 3×2 SRAM read case."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
from statistics import NormalDist
import subprocess
import sys

import numpy as np
from scipy.stats import truncnorm, norm
from scipy.special import log_ndtr

from main_sram import build_testbench, circuit_defaults, configure_circuit, normalize_circuit_options
from sram_compiler.per_device_mc.netlist import MC_PARAMS, parse_spice_models
from sram_compiler.version import VERSION
from yield_estimation.model_lib.MC import MC
from yield_estimation.evaluation.outcomes import classify_read, threshold_seconds, RULE_VERSION

ROOT = Path(__file__).resolve().parents[2]
CASE = "sram_6t_3x2_read"
CONFIGURABLE_CASE = "sram_6t_read"
ROWS, COLS = 3, 2
TARGET = (1, 1)
PARAMETER_FORMAT = ".17g"
FIELDS = {
    "case", "corner", "vdd", "temperature_c", "relative_std", "u0_positive",
    "threshold_ns", "seed", "budget", "batch_size",
}


def read_config(path):
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.keys() - {"circuit"} != FIELDS:
        raise ValueError(f"Configuration needs the optional circuit block and the following required fields: {', '.join(sorted(FIELDS))}")
    if config["case"] not in (CASE, CONFIGURABLE_CASE):
        raise ValueError(f"Supported cases: {CASE}, {CONFIGURABLE_CASE}")
    if config["corner"] not in ("TT", "FF", "SS", "FS", "SF"):
        raise ValueError("corner must be TT, FF, SS, FS or SF")
    if config["u0_positive"] is not True:
        raise ValueError("u0_positive must be true for the supported process distribution")
    for key in ("vdd", "temperature_c", "relative_std", "threshold_ns"):
        value = config[key]
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"{key} must be a finite number")
        if key != "temperature_c" and value <= 0:
            raise ValueError(f"{key} must be positive")
    if config["temperature_c"] <= -273.15:
        raise ValueError("temperature_c must be above absolute zero")
    threshold_seconds(config["threshold_ns"])
    if type(config["seed"]) is not int or not 0 <= config["seed"] < 2**32:
        raise ValueError("seed must be an integer from 0 to 4294967295")
    for key in ("budget", "batch_size"):
        if type(config[key]) is not int or config[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    if "circuit" in config or config["case"] == CONFIGURABLE_CASE:
        config["circuit"] = evaluation_circuit_options(config)
    return config


def evaluation_circuit_options(config):
    supplied = config.get("circuit", {})
    if not isinstance(supplied, dict):
        raise ValueError("circuit must be a JSON object")
    defaults = circuit_defaults()
    default_target = list(TARGET) if config["case"] == CASE else None
    defaults.update(array=[ROWS, COLS, False], target=default_target, cell_type="SRAM_6T_CELL",
                    cell_6t=None, operation="read", variation_mode="custom",
                    real_cell_mode=0, corner=config["corner"], vdd=config["vdd"],
                    temperature_c=config["temperature_c"])
    options = normalize_circuit_options(supplied, defaults)
    for key in ("corner", "vdd", "temperature_c"):
        if options[key] != config[key]:
            raise ValueError(f"Top-level {key} disagrees with circuit.{key}")
    if config["case"] == CASE and (options["array"] != [ROWS, COLS, False]
                                   or options["target"] != list(TARGET)):
        raise ValueError(f"Use case {CONFIGURABLE_CASE} to change the array or target")
    for key, value in (("operation", "read"), ("cell_type", "SRAM_6T_CELL"),
                       ("variation_mode", "custom"), ("real_cell_mode", 0)):
        if options[key] != value:
            raise ValueError(f"Current yield evaluation requires circuit.{key}={value!r}")
    return options


def read_case_dimensions(options):
    rows, cols, _ = options["array"]
    return rows * cols * 6 * len(MC_PARAMS)


def validate_algorithm_dimensions(options, algorithm):
    dimensions = read_case_dimensions(options)
    # The four importance samplers initialize from the checked-in box bounds.
    # MNIS also uses dimension-specific proposal variances for these sizes.
    if algorithm != "mc" and dimensions not in (18, 108, 576):
        raise ValueError(f"{algorithm.upper()} does not support {dimensions} parameters; "
                         "available dimensions: 18, 108, 576")


def build_evaluation_case(config, options, output, summary):
    circuit = configure_circuit(options)
    model = Path(getattr(circuit.global_config, "pdk_path_" + options["corner"]))
    rows, cols, _ = options["array"]
    means = nominal_parameters(circuit.sram_6t_cell, model, rows * cols)
    process = IndependentProcess(means, config["relative_std"], config["seed"])
    testbench = build_testbench(circuit, str(output), variation_mode=options["variation_mode"],
                                mc_seed=None, real_cell_mode=options["real_cell_mode"],
                                w_rc=options["w_rc"])
    summary.update(resolved_circuit=options, rows=rows, cols=cols, target=options["target"],
                   dimensions=means.size, model_path=str(model),
                   model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),
                   nominal_parameters_per_cell=means[:6 * len(MC_PARAMS)].tolist(),
                   driver_sizes=testbench.driver_sizes.to_dict(), timing=testbench.timing_config.to_dict(),
                   interconnect=testbench.interconnect.to_dict(), equivalent=testbench.equivalent.to_dict())
    return means, process, testbench


def nominal_parameters(cell, model_path, cells=ROWS * COLS):
    cards = parse_spice_models(model_path)
    pd, pg = cell.nmos_model.value
    pu = cell.pmos_model.value
    # This is the row/column order of gen_process_params in the 6T testbench.
    one_cell = [cards[name]["parameters"][parameter]
                for name in (pg, pg, pd, pu, pd, pu) for parameter in MC_PARAMS]
    means = np.tile(np.asarray(one_cell, dtype=float), cells)
    if not np.isfinite(means).all() or (means[1::3] <= 0).any():
        raise ValueError("Model cards must have finite nominal parameters and positive u0")
    return means


class IndependentProcess:
    """Independent Gaussian process parameters with u0 conditioned on being positive."""

    def __init__(self, means, relative_std, seed):
        self.means = means
        self.sigma = np.abs(means) * relative_std
        if not np.isfinite(self.sigma).all() or (self.sigma <= 0).any():
            raise ValueError("The model and relative_std must give positive, finite standard deviations")
        self.rng = np.random.default_rng(seed)

    def sample(self, n):
        values = self.rng.normal(self.means, self.sigma, size=(n, self.means.size))
        mean = self.means[1::3]
        sigma = self.sigma[1::3]
        values[:, 1::3] = truncnorm.rvs(
            -mean / sigma, np.inf, loc=mean, scale=sigma,
            size=(n, mean.size), random_state=self.rng,
        )
        return values


    def in_support(self, values):
        """Physical target support, separate from numerical density underflow."""
        values = np.atleast_2d(np.asarray(values, dtype=float))
        if values.shape[1] != self.means.size or not np.isfinite(values).all():
            raise ValueError("Support input must be finite and have the configured dimensions")
        return (values[:, 1::3] > 0).all(axis=1)

    def log_pdf(self, values):
        """Log density of exactly the distribution used by sample(), including u0 support."""
        values = np.atleast_2d(np.asarray(values, dtype=float))
        if values.shape[1] != self.means.size or not np.isfinite(values).all():
            raise ValueError("Density input must be finite and have the configured dimensions")
        terms = norm.logpdf(values, loc=self.means, scale=self.sigma)
        # Conditioning on u0 > 0 divides its Gaussian density by P(u0 > 0).
        terms[:, 1::3] -= log_ndtr(self.means[1::3] / self.sigma[1::3])
        result = terms.sum(axis=1)
        result[(values[:, 1::3] <= 0).any(axis=1)] = -np.inf
        return result


class RecordedReadTestbench:
    """Keep each submitted batch, and return only outcomes with usable evidence."""

    def __init__(self, testbench, output, config):
        self.testbench = testbench
        self.options = evaluation_circuit_options(config)
        self.target = tuple(self.options["target"])
        self.dimensions = read_case_dimensions(self.options)
        self.output = output
        self.budget = config["budget"]
        self.temperature = config["temperature_c"]
        self.threshold_s = threshold_seconds(config["threshold_ns"])
        self.submitted = 0
        self.returned = 0
        self.functional_failures = 0
        self.timing_failures = 0

    def write_rows(self, rows):
        with (self.output / "samples.csv").open("a", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerows(rows)

    def run_mc_simulation(self, *, operation, target_row, target_col, mc_runs, vars):
        if operation != "read" or (target_row, target_col) != self.target:
            raise ValueError(f"The evaluation supports reading configured cell {self.target}")
        if self.submitted + mc_runs > self.budget:
            raise RuntimeError(f"Budget exceeded: {self.submitted} + {mc_runs} > {self.budget}")
        samples = np.asarray(vars)
        if (samples.shape != (mc_runs, self.dimensions)
                or not np.isfinite(samples).all() or (samples[:, 1::3] <= 0).any()):
            raise ValueError("Sample parameters must be finite with positive u0 and the expected shape")

        first = self.submitted
        batch = self.output / f"batch_{first:04d}"
        batch.mkdir()
        np.save(batch / "samples.npy", samples)
        self.testbench.sim_path = str(batch)
        self.submitted += mc_runs
        print(f"MC: submitted {self.submitted}/{self.budget} samples", flush=True)
        try:
            result = self.testbench.run_mc_simulation(
                operation=operation, target_row=target_row, target_col=target_col,
                mc_runs=mc_runs, temperature=self.temperature, vars=samples,
                process_param_format=PARAMETER_FORMAT, retain_read_failures=True,
            )
            delays = np.asarray(result[0], dtype=float).ravel()
            if len(delays) != mc_runs or np.isinf(delays).any() or (delays < 0).any():
                raise RuntimeError("Unexpected read delay values or sample count")
        except (Exception, KeyboardInterrupt):
            self.write_rows((first + i + 1, batch.name, i, "", "unclassified")
                            for i in range(mc_runs))
            raise

        rows = []
        for index, delay in enumerate(delays):
            # The retained-failure simulator returns NaN only after electrical checks.
            outcome = classify_read(delay, self.threshold_s, functional_failure=np.isnan(delay))
            self.functional_failures += outcome == "read_functional_failure"
            self.timing_failures += outcome == "timing_failure"
            rows.append((first + index + 1, batch.name, index,
                         "" if np.isnan(delay) else float(delay), outcome))
        self.returned += len(delays)
        self.write_rows(rows)
        return result


def wilson_interval(failures, samples):
    p = failures / samples
    z = NormalDist().inv_cdf(.975)
    denominator = 1 + z*z / samples
    center = (p + z*z / (2*samples)) / denominator
    half = z * math.sqrt(p*(1-p) / samples + z*z / (4*samples*samples)) / denominator
    return [max(0.0, center - half), min(1.0, center + half)]


def write_report(output, summary):
    text = [
        "# Monte Carlo SRAM read evaluation", "",
        f"- Circuit: {summary['rows']}×{summary['cols']} 6T SRAM, target cell {summary['target']}, OpenYield {VERSION}",
        f"- Conditions: {summary['corner']}, {summary['vdd']} V, {summary['temperature_c']} °C",
        f"- Process: {summary['relative_std']:.2%} relative Gaussian scale, conditioned on positive u0",
        f"- Read delay limit: {summary['threshold_ns']:g} ns",
        f"- Seed: {summary['seed']}",
        f"- Revision: {summary['git_revision'] or 'unavailable'}",
        f"- Requested: {summary['requested_samples']} / {summary['budget']}",
        f"- Classified: {summary['returned_rows']}",
        f"- Read functional failures: {summary['read_functional_failures']}",
        f"- Timing failures: {summary['timing_failures']}",
        f"- Unclassified: {summary['unclassified_samples']}", "",
    ]
    if summary["pfail_estimate"] is None:
        text.extend(["The run is incomplete.", "", summary["error"]])
    else:
        low, high = summary["pfail_interval"]
        text.extend([
            f"Estimated failure probability: **{summary['pfail_estimate']:.4%}** "
            f"({summary['failure_count']}/{summary['returned_rows']}).",
            f"95% Wilson interval: {low:.4%}–{high:.4%}.", "",
            "For this case the result applies to the selected cell and read operation.",
        ])
    (output / "report.md").write_text("\n".join(text) + "\n", encoding="utf-8")


def source_revision():
    try:
        status = subprocess.run(
            ["git", "--no-optional-locks", "status", "--porcelain=v2", "--branch",
             "--untracked-files=no"],
            cwd=ROOT, capture_output=True, text=True,
        )
    except OSError:
        return None, None
    if status.returncode != 0:
        return None, None
    lines = status.stdout.splitlines()
    revision = next((line.split()[-1] for line in lines if line.startswith("# branch.oid ")), None)
    return revision, any(line and not line.startswith("# ") for line in lines)


def run_evaluation(config, output):
    options = evaluation_circuit_options(config)
    output.mkdir(parents=True, exist_ok=False)
    (output / "run_config.json").write_text(
        json.dumps(config, indent=2, allow_nan=False) + "\n", encoding="utf-8",
    )
    (output / "samples.csv").write_text(
        "sample,batch,xyce_run,read_delay_s,outcome\n", encoding="utf-8",
    )
    revision, modified = source_revision()
    summary = dict(
        config, algorithm="repository_MC", compiler_version=VERSION,
        outcome_rule_version=RULE_VERSION,
        git_revision=revision, git_tracked_files_modified=modified,
        rows=options["array"][0], cols=options["array"][1], target=options["target"],
        process_param_format=PARAMETER_FORMAT, variation_mode="custom",
    )
    recorder = None
    error = None
    original_dir = Path.cwd()
    try:
        xyce = shutil.which("Xyce")
        if xyce is None:
            raise RuntimeError("Activate the openyield environment with Xyce on PATH")
        summary["xyce"] = xyce
        means, process, testbench = build_evaluation_case(config, options, output, summary)
        recorder = RecordedReadTestbench(testbench, output, config)
        algorithm = MC(
            f_norm=process, mc_testbench=recorder, means=means, feature_num=means.size,
            IS_bound_num=1, initial_num=config["batch_size"], sample_num=config["batch_size"],
            seed=config["seed"], target=options["target"], use_legacy_bounds=False,
        )
        algorithm.threshold = threshold_seconds(config["threshold_ns"])
        os.chdir(output)
        estimates, _ = algorithm.start_estimate(max_num=config["budget"], fixed_budget=True)
        failures = recorder.functional_failures + recorder.timing_failures
        if recorder.returned != config["budget"] or not math.isclose(
            estimates[-1], failures / recorder.returned,
        ):
            raise RuntimeError("MC estimate and recorded outcomes are inconsistent")
    except KeyboardInterrupt:
        error = "KeyboardInterrupt: evaluation interrupted"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        os.chdir(original_dir)

    submitted = recorder.submitted if recorder else 0
    returned = recorder.returned if recorder else 0
    functional = recorder.functional_failures if recorder else 0
    timing = recorder.timing_failures if recorder else 0
    failures = functional + timing
    complete = error is None
    summary.update(
        status="complete" if complete else "incomplete", error=error,
        requested_samples=submitted, returned_rows=returned,
        unclassified_samples=submitted - returned, passed_samples=returned - failures,
        read_functional_failures=functional, timing_failures=timing, failure_count=failures,
        pfail_estimate=failures / returned if complete else None,
        pfail_interval=wilson_interval(failures, returned) if complete else None,
    )
    (output / "run_summary.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8",
    )
    write_report(output, summary)
    for key in ("status", "requested_samples", "returned_rows", "unclassified_samples",
                "failure_count", "pfail_estimate"):
        print(f"{key}={summary[key]}")
    print(f"report={output / 'report.md'}")
    if error is not None:
        print(error, file=sys.stderr)
    return 0 if complete else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        config = read_config(args.config)
        return run_evaluation(config, args.output_dir.resolve())
    except (OSError, ValueError, RuntimeError, KeyError, OverflowError) as exc:
        parser.exit(2, f"MC evaluation: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
