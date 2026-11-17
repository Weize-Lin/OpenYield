"""Evaluate direct Monte Carlo for a configured SRAM operation."""
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
from sram_compiler.equivalent_modeling import resolve_equivalent
from sram_compiler.per_device_mc.netlist import (
    BITCELL_PROCESS_ROLES, MC_PARAMS, bitcell_process_devices, parse_spice_models,
)
from sram_compiler.version import VERSION
from yield_estimation.model_lib.MC import MC
from yield_estimation.evaluation.outcomes import (
    MeasurementRule, threshold_seconds, UNCLASSIFIED,
)

ROOT = Path(__file__).resolve().parents[2]
CASE = "sram_6t_3x2_read"
CONFIGURABLE_CASE = "sram_6t_read"
WRITE_CASE = "sram_6t_write"
CASE_OPERATIONS = {
    CASE: "read",
    CONFIGURABLE_CASE: "read",
    WRITE_CASE: "write",
    "sram_6t_read_write": "read&write",
    **{f"sram_6t_{operation}": operation for operation in MeasurementRule.SNM_OPERATIONS},
    "sram_10t_read": "read",
    "sram_10t_write": "write",
    "sram_10t_read_write": "read&write",
    **{f"sram_10t_{operation}": operation for operation in MeasurementRule.SNM_OPERATIONS},
}
ROWS, COLS = 3, 2
TARGET = (1, 1)
PARAMETER_FORMAT = ".17g"
FIELDS = {
    "case", "corner", "vdd", "temperature_c", "relative_std", "u0_positive",
    "seed", "budget", "batch_size",
}


def read_config(path):
    return normalize_config(json.loads(path.read_text(encoding="utf-8")))


def normalize_config(config):
    if not isinstance(config, dict):
        raise ValueError("Evaluation configuration must be a JSON object")
    config = dict(config)
    case = config.get("case")
    if not isinstance(case, str) or case not in CASE_OPERATIONS:
        raise ValueError(f"Supported cases: {', '.join(CASE_OPERATIONS)}")
    operation = CASE_OPERATIONS[case]
    required = FIELDS | {MeasurementRule.threshold_key_for(operation)}
    if config.keys() - {
            "circuit", "initialization_sigma", "mnis_proposal_covariance", "hscs_search_origin",
            "mnis_target_fraction", "research",
    } != required:
        raise ValueError(f"{operation} requires an optional circuit block and these fields: {', '.join(sorted(required))}")
    if config["corner"] not in ("TT", "FF", "SS", "FS", "SF"):
        raise ValueError("corner must be TT, FF, SS, FS or SF")
    for key in ("vdd", "temperature_c", "relative_std"):
        value = config[key]
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"{key} must be a finite number")
        if key != "temperature_c" and value <= 0:
            raise ValueError(f"{key} must be positive")
    if config["temperature_c"] <= -273.15:
        raise ValueError("temperature_c must be above absolute zero")
    MeasurementRule(operation, config)
    if type(config["seed"]) is not int or not 0 <= config["seed"] < 2**32:
        raise ValueError("seed must be an integer from 0 to 4294967295")
    for key in ("budget", "batch_size"):
        if type(config[key]) is not int or config[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    if "research" in config:
        from .research_settings import parse_research_context
        config["research"] = parse_research_context(config["research"], config["budget"])
    options = evaluation_circuit_options(config)
    if "circuit" in config or config["case"] != CASE:
        config["circuit"] = options
    return config


def evaluation_circuit_options(config):
    supplied = config.get("circuit", {})
    if not isinstance(supplied, dict):
        raise ValueError("circuit must be a JSON object")
    case = config["case"]
    operation = CASE_OPERATIONS[case]
    cell_type = "SRAM_10T_CELL" if case.startswith("sram_10t_") else "SRAM_6T_CELL"
    defaults = circuit_defaults()
    default_target = list(TARGET) if config["case"] == CASE else None
    array = [1, 1, False] if operation in MeasurementRule.SNM_OPERATIONS else [ROWS, COLS, False]
    defaults.update(array=array, target=default_target, cell_type=cell_type,
                    cell_6t=None, operation=operation, variation_mode="custom",
                    real_cell_mode=0, corner=config["corner"], vdd=config["vdd"],
                    temperature_c=config["temperature_c"])
    if supplied.get("cell_type") is None:
        supplied = {**supplied, "cell_type": cell_type}
    options = normalize_circuit_options(supplied, defaults)
    for key in ("vdd", "temperature_c"):
        if options[key] is None:
            options[key] = config[key]
        elif options[key] != config[key]:
            raise ValueError(f"Top-level {key} disagrees with circuit.{key}")
    if config["case"] == CASE and (options["array"] != [ROWS, COLS, False]
                                   or options["target"] != list(TARGET)):
        raise ValueError(f"Use case {CONFIGURABLE_CASE} to change the array or target")
    for key, value in (("operation", operation), ("cell_type", cell_type)):
        if options[key] != value:
            raise ValueError(f"Current yield evaluation requires circuit.{key}={value!r}")
    mode = options["variation_mode"]
    sigma = config.get("initialization_sigma")
    if "initialization_sigma" in config and (
            type(sigma) not in (int, float) or not math.isfinite(sigma) or sigma <= 0):
        raise ValueError("initialization_sigma must be positive and finite")
    fraction = config.get("mnis_target_fraction", 0.)
    if type(fraction) not in (int, float) or not math.isfinite(fraction) or not 0 <= fraction < 1:
        raise ValueError("mnis_target_fraction must be in [0, 1)")
    for key, choices in (
            ("mnis_proposal_covariance", ("legacy", "target_diagonal")),
            ("hscs_search_origin", ("legacy", "nominal"))):
        if key in config and config[key] not in choices:
            raise ValueError(f"{key} must be one of {', '.join(choices)}")
    if options["real_cell_mode"] is None:
        inherited = configure_circuit(options).global_config.equivalent
        options["real_cell_mode"] = resolve_equivalent(inherited).mode
    if operation in MeasurementRule.SNM_OPERATIONS:
        if options["array"] != [1, 1, False]:
            raise ValueError("SNM simulates a single cell; set circuit.array to [1, 1, false]")
        if options["real_cell_mode"] != 0:
            raise ValueError("SNM has no equivalent array cells; choose real_cell_mode 0")
    positive = config.get("u0_positive", mode == "custom")
    if (type(positive) is not bool or mode == "custom" and not positive
            or mode in ("shared", "per-device") and positive):
        raise ValueError("Set u0_positive to true for custom and false for Xyce random sampling")
    if mode == "nominal" and config["budget"] != 1:
        raise ValueError("Nominal is a deterministic check; set budget to 1")
    if mode in ("shared", "per-device"):
        if mode == "shared" and config["budget"] < 2:
            raise ValueError("Xyce treats shared with one sample as nominal; choose budget >= 2")
        if config["batch_size"] < config["budget"]:
            raise ValueError("Native Xyce sampling needs one batch; set batch_size >= budget")
        if not 1 <= config["seed"] <= 2**31 - 1:
            raise ValueError("Native Xyce seeds must be positive signed 32-bit integers")
    return options


def access_case_dimensions(options):
    rows, cols, _ = options["array"]
    return rows * cols * len(BITCELL_PROCESS_ROLES[options["cell_type"]]) * len(MC_PARAMS)


def validate_algorithm_case(options, algorithm, config):
    if algorithm != "mc" and options["variation_mode"] != "custom":
        raise ValueError("Choose MC for simulator-managed sampling; importance sampling needs an explicit process density")
    if algorithm != "mc" and options["real_cell_mode"] != 0:
        raise ValueError("Approximate array modes currently use MC; choose real_cell_mode 0 for importance sampling")
    dimensions = access_case_dimensions(options)
    if "initialization_sigma" in config:
        required = {"mnis": "mnis_proposal_covariance", "hscs": "hscs_search_origin"}.get(algorithm)
        if required is not None and required not in config:
            raise ValueError(f"Set {required} explicitly alongside initialization_sigma for {algorithm.upper()}")
    if algorithm == "mnis" and config.get("mnis_proposal_covariance", "legacy") == "legacy":
        if options["cell_type"] != "SRAM_6T_CELL" or dimensions not in (18, 108, 576):
            raise ValueError("Choose mnis_proposal_covariance=target_diagonal for this circuit")
    if algorithm == "mc" or "initialization_sigma" in config:
        return
    # Legacy bounds and MNIS proposal settings were supplied for 6T only.
    if options["cell_type"] != "SRAM_6T_CELL" or dimensions not in (18, 108, 576):
        raise ValueError(f"{algorithm.upper()} has no legacy bounds for {dimensions} parameters in "
                         f"{options['cell_type']}. Set initialization_sigma to opt into a configured box.")


def configured_initialization(config, process, summary):
    width = config.get("initialization_sigma")
    if width is None:
        summary["initialization"] = {"method": "legacy_bound_file"}
        return None
    upper = process.means + width * process.sigma
    lower = process.means - width * process.sigma
    # Match the positive-mobility domain of the configured target process.
    lower[1::3] = np.maximum(lower[1::3], np.finfo(float).tiny)
    bounds = np.stack((upper, lower))
    if not np.isfinite(bounds).all() or not (lower < upper).all():
        raise ValueError("initialization_sigma produced an invalid search box")
    summary["initialization"] = {"method": "box_about_nominal", "sigma": width,
                                  "lower": lower.tolist(), "upper": upper.tolist()}
    return bounds


def build_evaluation_case(config, options, output, summary):
    circuit = configure_circuit(options)
    model = Path(getattr(circuit.global_config, "pdk_path_" + options["corner"]))
    rows, cols, _ = options["array"]
    cell_type = options["cell_type"]
    cell = circuit.sram_10t_cell if cell_type == "SRAM_10T_CELL" else circuit.sram_6t_cell
    devices = BITCELL_PROCESS_ROLES[cell_type]
    mode = options["variation_mode"]
    means = nominal_parameters(cell, model, rows * cols, cell_type=cell_type)
    process = IndependentProcess(means, config["relative_std"], config["seed"]) if mode == "custom" else None
    testbench = build_testbench(circuit, str(output), variation_mode=mode,
                                mc_seed=config["seed"] if mode in ("shared", "per-device") else None,
                                real_cell_mode=options["real_cell_mode"], w_rc=options["w_rc"],
                                vth_std=config["relative_std"])
    summary.update(sampling_source={
        "custom": "numpy_independent_positive_u0",
        "nominal": "deterministic_nominal",
        "shared": "xyce_model_card_shared_lhs",
        "per-device": "xyce_per_retained_mos_lhs",
    }[mode], sample_parameters_saved=mode == "custom",
        approximation=options["real_cell_mode"] != 0,
        bitcell_table_parameters=means.size if mode == "custom" else None)
    summary.update(resolved_circuit=options, rows=rows, cols=cols, target=options["target"],
                   dimensions=means.size if mode == "custom" else None, model_path=str(model),
                   model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),
                   cell_type=cell_type,
                   nominal_parameters_per_cell=means[:len(devices) * len(MC_PARAMS)].tolist(),
                   bitcell_parameter_order=[f"{name}.{param}"
                                            for name, _ in devices for param in MC_PARAMS],
                   driver_sizes=testbench.driver_sizes.to_dict(), timing=testbench.timing_config.to_dict(),
                   interconnect=testbench.interconnect.to_dict(), equivalent=testbench.equivalent.to_dict())
    return means, process, testbench


def nominal_parameters(cell, model_path, cells=ROWS * COLS, *, cell_type="SRAM_6T_CELL"):
    cards = parse_spice_models(model_path)
    one_cell = [cards[model]["parameters"][parameter]
                for _, model in bitcell_process_devices(cell, cell_type) for parameter in MC_PARAMS]
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


class RecordedAccessTestbench:
    """Keep each submitted batch, and return only outcomes with usable evidence."""

    def __init__(self, testbench, output, config):
        self.testbench = testbench
        self.options = evaluation_circuit_options(config)
        self.operation = self.options["operation"]
        self.rule = MeasurementRule(self.operation, config)
        self.target = tuple(self.options["target"])
        self.dimensions = access_case_dimensions(self.options)
        self.output = output
        self.budget = config["budget"]
        self.temperature = config["temperature_c"]
        self.submitted = 0
        self.returned = 0
        self.functional_failures = 0
        self.timing_failures = 0
        self.margin_failures = 0

    def write_rows(self, rows):
        with (self.output / "samples.csv").open("a", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerows(rows)

    def run_mc_simulation(self, *, operation, target_row, target_col, mc_runs, vars):
        if operation != self.operation or (target_row, target_col) != self.target:
            raise ValueError(f"The evaluation requires {self.operation} at configured cell {self.target}")
        if self.submitted + mc_runs > self.budget:
            raise RuntimeError(f"Budget exceeded: {self.submitted} + {mc_runs} > {self.budget}")
        samples = None
        if self.options["variation_mode"] == "custom":
            samples = np.asarray(vars)
            if (samples.shape != (mc_runs, self.dimensions)
                    or not np.isfinite(samples).all() or (samples[:, 1::3] <= 0).any()):
                raise ValueError("Sample parameters must be finite with positive u0 and the expected shape")
        elif vars is not None:
            raise ValueError("Simulator-managed sampling does not accept an external parameter table")

        first = self.submitted
        batch = self.output / f"batch_{first:04d}"
        batch.mkdir()
        if samples is not None:
            np.save(batch / "samples.npy", samples)
        self.testbench.sim_path = str(batch)
        self.submitted += mc_runs
        print(f"MC: submitted {self.submitted}/{self.budget} samples", flush=True)
        try:
            retain = {}
            if not self.rule.is_snm:
                flag = "retain_read_write_failures" if operation == "read&write" else f"retain_{operation}_failures"
                retain[flag] = True
            result = self.testbench.run_mc_simulation(
                operation=operation, target_row=target_row, target_col=target_col,
                mc_runs=mc_runs, temperature=self.temperature, vars=samples,
                process_param_format=PARAMETER_FORMAT, **retain,
            )
            values = np.asarray(result if self.rule.is_snm else result[0], dtype=float).ravel()
            if len(values) != mc_runs or np.isinf(values).any() or (values < 0).any():
                raise RuntimeError(f"Unexpected {operation} measurements or sample count")
            if self.rule.is_snm and not np.isfinite(values).all():
                raise RuntimeError(f"Unresolved {operation} margin; inspect the batch debug CSV and raw sweep")
        except (Exception, KeyboardInterrupt):
            self.write_rows((first + i + 1, batch.name, i, "", UNCLASSIFIED)
                            for i in range(mc_runs))
            raise

        rows = []
        for index, value in enumerate(values):
            # Transient NaN arrives only with electrical evidence of access failure.
            outcome = self.rule.classify(value)
            self.functional_failures += outcome == self.rule.functional_failure
            self.timing_failures += outcome == "timing_failure"
            self.margin_failures += outcome == "margin_failure"
            rows.append((first + index + 1, batch.name, index,
                         "" if np.isnan(value) else float(value), outcome))
        self.returned += len(values)
        self.write_rows(rows)
        return (values, None, None, None) if self.rule.is_snm else result


class RecordedReadTestbench(RecordedAccessTestbench):
    """Read recorder used by the adaptive estimators and HSCS mixed search."""

    def __init__(self, testbench, output, config):
        super().__init__(testbench, output, config)
        if self.operation != "read":
            raise ValueError("This recorder requires read evaluation")


def wilson_interval(failures, samples):
    p = failures / samples
    z = NormalDist().inv_cdf(.975)
    denominator = 1 + z*z / samples
    center = (p + z*z / (2*samples)) / denominator
    half = z * math.sqrt(p*(1-p) / samples + z*z / (4*samples*samples)) / denominator
    return [max(0.0, center - half), min(1.0, center + half)]


def write_report(output, summary):
    operation = summary.get("operation", "read")
    rule = MeasurementRule(operation, summary)
    cell_label = "10T" if summary.get("cell_type") == "SRAM_10T_CELL" else "6T"
    functional = summary.get(functional_failure_key(operation), 0)
    mode = summary.get("variation_mode", "custom")
    title = "Deterministic" if mode == "nominal" else "Monte Carlo"
    text = [
        f"# {title} SRAM {operation} evaluation", "",
        f"- Circuit: {summary['rows']}×{summary['cols']} {cell_label} SRAM, target cell {summary['target']}, OpenYield {VERSION}",
        f"- Conditions: {summary['corner']}, {summary['vdd']} V, {summary['temperature_c']} °C",
        f"- Sampling: {summary.get('sampling_source', mode)}",
        *([] if mode == "nominal" else [f"- Relative process spread: {summary['relative_std']:.2%}"]),
        *([] if not summary.get("approximation") else ["- Equivalent load model; unused cells are approximated."]),
        f"- Measured {rule.metric}. Failure threshold: value {rule.operator} {rule.threshold_value:g} {rule.unit}",
        *([] if mode == "nominal" else [f"- Seed: {summary['seed']}"]),
        f"- Revision: {summary['git_revision'] or 'unavailable'}",
        f"- Requested: {summary['requested_samples']} / {summary['budget']}",
        f"- Classified: {summary['returned_rows']}",
        (f"- Margin failures: {summary['margin_failures']}" if rule.is_snm else
         f"- {operation.capitalize()} functional failures: {functional}"),
        *([] if rule.is_snm else [f"- Timing failures: {summary['timing_failures']}"]),
        f"- Unclassified: {summary['unclassified_samples']}", "",
    ]
    if summary["status"] != "complete":
        text.extend(["The run is incomplete.", "", summary["error"]])
    elif mode == "nominal":
        text.append(f"Nominal result: **{summary['nominal_outcome']}**. This evaluates one deterministic sample.")
    else:
        text.extend([
            f"Estimated failure fraction: **{summary['pfail_estimate']:.4%}** "
            f"({summary['failure_count']}/{summary['returned_rows']}).",
        ])
        if summary["pfail_interval"] is not None:
            low, high = summary["pfail_interval"]
            text.append(f"95% Wilson interval: {low:.4%}–{high:.4%}.")
        else:
            text.append("Xyce used Latin-hypercube sampling. An independent-Bernoulli interval is not reported.")
        text.append(f"For this case the result applies to the selected cell and {operation} operation.")
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


def functional_failure_key(operation):
    return operation.replace("&", "_") + "_functional_failures"


def run_evaluation(config, output):
    options = evaluation_circuit_options(config)
    operation = options["operation"]
    mode = options["variation_mode"]
    rule = MeasurementRule(operation, config)
    output.mkdir(parents=True, exist_ok=False)
    (output / "run_config.json").write_text(
        json.dumps(config, indent=2, allow_nan=False) + "\n", encoding="utf-8",
    )
    (output / "samples.csv").write_text(
        f"sample,batch,xyce_run,{rule.column},outcome\n", encoding="utf-8",
    )
    revision, modified = source_revision()
    summary = dict(
        config, algorithm={"custom": "repository_MC", "nominal": "xyce_nominal",
                           "shared": "xyce_shared_lhs", "per-device": "xyce_per_device_lhs"}[mode],
        compiler_version=VERSION,
        operation=operation, cell_type=options["cell_type"], **rule.metadata(),
        git_revision=revision, git_tracked_files_modified=modified,
        rows=options["array"][0], cols=options["array"][1], target=options["target"],
        process_param_format=PARAMETER_FORMAT, variation_mode=mode,
    )
    if not rule.is_snm:
        summary["delay_metric"] = rule.metric
    recorder = None
    error = None
    original_dir = Path.cwd()
    try:
        xyce = shutil.which("Xyce")
        if xyce is None:
            raise RuntimeError("Activate the openyield environment with Xyce on PATH")
        summary["xyce"] = xyce
        means, process, testbench = build_evaluation_case(config, options, output, summary)
        recorder = RecordedAccessTestbench(testbench, output, config)
        if mode != "custom":
            row, col = options["target"]
            recorder.run_mc_simulation(operation=operation, target_row=row, target_col=col,
                                       mc_runs=config["budget"], vars=None)
        else:
            _run_custom_mc(config, options, rule, means, process, recorder, output)
        if recorder.returned != config["budget"]:
            raise RuntimeError("Observed sample count does not match the requested budget")
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
    margin = recorder.margin_failures if recorder else 0
    failures = functional + timing + margin
    complete = error is None
    summary.update(
        status="complete" if complete else "incomplete", error=error,
        requested_samples=submitted, returned_rows=returned,
        unclassified_samples=submitted - returned, passed_samples=returned - failures,
        timing_failures=timing, failure_count=failures,
        pfail_estimate=failures / returned if complete and mode != "nominal" else None,
        pfail_interval=wilson_interval(failures, returned) if complete and mode == "custom" else None,
    )
    if mode == "nominal":
        summary["nominal_outcome"] = ("failure" if failures else "pass") if complete else "unclassified"
    if rule.is_snm:
        summary["margin_failures"] = margin
    else:
        summary[functional_failure_key(operation)] = functional
    if "research" in config:
        context = config["research"]
        summary["research_cost"] = dict(budget_unit=context["budget_unit"], xyce_cap=config["budget"],
            algorithm_draw_cap=context["max_algorithm_draws"], phases={"estimation": dict(
                algorithm_charged_draws=submitted, xyce_submitted=submitted, xyce_returned=returned)})
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


def _run_custom_mc(config, options, rule, means, process, recorder, output):
    algorithm = MC(
        f_norm=process, mc_testbench=recorder, means=means, feature_num=means.size,
        IS_bound_num=1, initial_num=config["batch_size"], sample_num=config["batch_size"],
        seed=config["seed"], target=options["target"], use_legacy_bounds=False,
        operation=options["operation"],
    )
    algorithm.threshold = rule.threshold
    algorithm.indicator = rule.failures
    os.chdir(output)
    estimates, _ = algorithm.start_estimate(max_num=config["budget"], fixed_budget=True)
    failures = recorder.functional_failures + recorder.timing_failures + recorder.margin_failures
    if recorder.returned != config["budget"] or not math.isclose(
        estimates[-1], failures / recorder.returned,
    ):
        raise RuntimeError("MC estimate and recorded outcomes are inconsistent")


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
