"""Run a versioned OpenYield algorithm experiment from Python or JSON."""

import argparse
import csv
import json
from pathlib import Path
import subprocess

from .research_settings import parse_research_context, positive_int, require_keys
from .run import ROOT, evaluation_circuit_options, normalize_config, source_revision, validate_algorithm_case
from .run_diagnostics import plan


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def normalize_experiment(config):
    """Resolve experiment settings and validate every estimator before starting Xyce."""
    if not isinstance(config, dict):
        config = json.loads(Path(config).read_text(encoding="utf-8"))
    require_keys(config, ("schema_version", "experiment", "budget_unit", "budget_per_algorithm",
                          "max_algorithm_draws", "estimators", "seeds"), "experiment envelope")
    if type(config.get("schema_version")) is not int or config["schema_version"] != 1:
        raise ValueError("Use schema_version 1")
    base = config.get("experiment")
    if not isinstance(base, dict) or set(base) & {
        "budget", "seed", "research", "initialization_sigma", "hscs_search_origin",
        "mnis_proposal_covariance", "mnis_target_fraction",
    }:
        raise ValueError("experiment contains only shared circuit, process and failure settings")
    budget = positive_int(config.get("budget_per_algorithm"), "budget_per_algorithm")
    unit = config.get("budget_unit", "algorithm_draws")
    max_draws = config.get("max_algorithm_draws", budget if unit == "algorithm_draws" else None)
    seeds = config.get("seeds", [0])
    if (not isinstance(seeds, list) or not seeds or any(type(seed) is not int for seed in seeds)
            or len(set(seeds)) != len(seeds)):
        raise ValueError("seeds must be a nonempty list of distinct integers")
    estimators = config.get("estimators")
    if not isinstance(estimators, list) or not estimators:
        raise ValueError("estimators must be a nonempty array")
    profiles = [parse_research_context(dict(budget_unit=unit, max_algorithm_draws=max_draws,
                                             estimator=item), budget)["estimator"]
                for item in estimators]
    if len({item["id"].casefold() for item in profiles}) != len(profiles):
        raise ValueError("Estimator ids must be unique")
    normalized = normalize_config(dict(base, budget=budget, seed=seeds[0]))
    normalized["circuit"] = evaluation_circuit_options(normalized)
    for key in ("seed", "budget"):
        normalized.pop(key)
    result = dict(schema_version=1, experiment=normalized, budget_unit=unit,
                  budget_per_algorithm=budget, max_algorithm_draws=max_draws,
                  seeds=list(seeds), estimators=profiles)
    for _, _, child in child_configs(result):
        child = normalize_config(child)
        profile = child["research"]["estimator"]
        options = evaluation_circuit_options(child)
        validate_algorithm_case(options, profile["algorithm"], child)
        if profile["algorithm"] == "hscs" and options["operation"] == "read" and not profile.get("allow_hscs_mixed_search"):
            raise ValueError("HSCS read uses auxiliary writes: set allow_hscs_mixed_search explicitly")
        if profile["mode"] == "reference" and options["variation_mode"] != "custom":
            raise ValueError("Reference direct MC requires custom sampling; use mode legacy for simulator-managed or nominal runs")
    return result


def child_configs(config):
    for profile in config["estimators"]:
        for seed in config["seeds"]:
            child = dict(config["experiment"], seed=seed, budget=config["budget_per_algorithm"],
                         research=dict(budget_unit=config["budget_unit"],
                                       max_algorithm_draws=config["max_algorithm_draws"], estimator=profile))
            if profile["algorithm"] != "mc" and profile["mode"] == "variant":
                # Candidate acquisition has its own radius. This is only the
                # default bound supplied to constructors with no historical file.
                if profile["initialization"]["method"] != "legacy":
                    child["initialization_sigma"] = 3.
                if profile["algorithm"] == "mnis":
                    child["mnis_proposal_covariance"] = profile["proposal"]["covariance"]
                    child["mnis_target_fraction"] = profile["proposal"]["target_fraction"]
                if profile["algorithm"] == "hscs":
                    child["hscs_search_origin"] = profile["hscs_search_origin"]
            yield profile, seed, child


def result_row(profile, seed, folder, algorithm, code, summary):
    status = summary.get("status", "runner_error")
    warnings = list(summary.get("sampling_diagnostics", {}).get("warnings", []))
    probability = summary.get("pfail_estimate") if not code else None
    if probability is not None and probability > 1:
        warnings.append("estimate_above_one")
    return dict(estimator_id=profile["id"], algorithm=algorithm, mode=profile["mode"], seed=seed,
                status=status, exit_code=code,
                estimated_pfail=probability,
                log10_pfail=summary.get("log10_pfail") if not code else None,
                charged_draws=summary.get("algorithm_requested_samples", summary.get("requested_samples")),
                xyce_submitted=summary.get("simulator_submitted_samples", summary.get("requested_samples")),
                xyce_returned=summary.get("simulator_returned_samples", summary.get("returned_rows")),
                estimation_draws=summary.get("diagnostics", {}).get("estimation_draws_evaluated",
                        summary.get("returned_rows") if algorithm == "mc" else None),
                warnings="; ".join(warnings),
                detail=summary.get("detail", summary.get("error")),
                run=f"{folder.as_posix()}/{algorithm}", process_log=f"{folder.as_posix()}/process.log")


def run_experiment(config, output_dir):
    """Freeze the experiment and execute each registered estimator in a fresh process."""
    config = normalize_experiment(config)
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "experiment.json", config)
    revision, dirty = source_revision()
    trials = []
    for profile, seed, child in child_configs(config):
        relative = Path(profile["id"]) / f"seed_{seed}"
        folder = output / relative
        folder.mkdir(parents=True)
        child_path = folder / "config.json"
        write_json(child_path, child)
        name = profile["algorithm"]
        (_, command), = plan(child_path, folder, [name], profile.get("initial_batch", 2),
                             profile.get("initial_failures", 1), profile.get("estimation_batch", 1),
                             profile.get("proposal", {}).get("legacy_variance", 1.),
                             profile.get("allow_hscs_mixed_search", False))
        write_json(folder / "command.json", command)
        with (folder / "process.log").open("wb") as stream:
            code = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT).returncode
        summary_file = folder / name / "run_summary.json"
        summary = json.loads(summary_file.read_text(encoding="utf-8")) if summary_file.exists() else {}
        row = result_row(profile, seed, relative, name, code, summary)
        trials.append(row)
        with (output / "trials.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(row))
            writer.writeheader()
            writer.writerows(trials)
        print(f"{profile['id']} seed={seed} {row['status']} p={row['estimated_pfail']} "
              f"draws={row['charged_draws']} xyce={row['xyce_submitted']}", flush=True)
    result = dict(git_revision=revision, git_tracked_files_modified=dirty, trials=trials,
                  ranking_eligible=False)
    write_json(output / "experiment_summary.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = run_experiment(args.config, args.output_dir)
    except (OSError, ValueError) as exc:
        parser.exit(2, str(exc) + "\n")
    return int(any(row["exit_code"] or row["status"] == "runner_error" for row in result["trials"]))


if __name__ == "__main__":
    raise SystemExit(main())
