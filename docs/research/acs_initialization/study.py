"""Bounded analytic comparison. Only the initialization hook differs between arms."""
import argparse
from contextlib import redirect_stdout
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import platform
import subprocess
import traceback
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import scipy
from scipy.special import log_ndtr, logsumexp, ndtri_exp
from scipy.stats import norm
import torch

from yield_estimation.evaluation.mnis_run import sampling_diagnostics, weight_summary
from yield_estimation.model_lib.ACS import ACS
from yield_estimation.model_lib._recorded_algorithm import AlgorithmBudgetReached
from yield_estimation.tool.Distribution.normal_v1 import norm_dist


class InitializationCapReached(RuntimeError):
    pass


def sphere_points(radius, count, dimension, rng):
    """Uniform surface directions; no clipping, rejection or region-aware selection."""
    directions = rng.normal(size=(count, dimension))
    lengths = np.linalg.norm(directions, axis=1, keepdims=True)
    if not np.isfinite(lengths).all() or (lengths == 0).any():
        raise ValueError("Non-finite or zero direction; stop without a silent redraw")
    return radius * directions / lengths


def latent_to_process(z, process):
    """Research mapping, not wired into the platform or the ACS production sampler.

    Ordinary coordinates are affine. Positive u0 uses equal survival probability
    under the positive-conditioned Gaussian. The image is not a physical sphere.
    """
    z = np.atleast_2d(np.asarray(z, dtype=float))
    if z.shape[1] != len(process.means) or not np.isfinite(z).all():
        raise ValueError("Latent coordinates must be finite and match the process")
    x = process.means + process.sigma * z
    mu, sigma = process.means[1::3], process.sigma[1::3]
    log_tail = log_ndtr(mu / sigma) + log_ndtr(-z[:, 1::3])
    x[:, 1::3] = mu - sigma * ndtri_exp(log_tail)
    if not np.isfinite(x).all() or not process.in_support(x).all():
        raise ValueError("Mapping lost physical support in finite precision; no clipping applied")
    return x


def regions(points, threshold):
    points = np.asarray(points).reshape(-1, 2)
    return {label for label, mask in (("left", points[:, 0] < -threshold),
                                      ("right", points[:, 0] > threshold)) if mask.any()}


def run_one(arm, seed, protocol):
    """Execute the current production ACS loop and audit its actual per-batch q."""
    threshold = protocol["failure_threshold"]
    target = norm_dist(np.zeros(2), np.eye(2))

    def measure(*, vars, mc_runs, **kwargs):
        assert len(vars) == mc_runs
        return np.abs(vars[:, 0]), None, None, None

    algorithm = ACS(SimpleNamespace(run_mc_simulation=measure), 2, 1, False, target,
                    np.array(protocol["kernel_variance"]), protocol["candidate_failures"],
                    protocol["initialization_batch"], protocol["estimation_batch"], 10, seed,
                    initialization_bounds=np.array([[4., 4.], [-4., -4.]]))
    # The explicit protocol overrides the synthetic box, not any SRAM bounds file.
    algorithm.up_bounds[:] = protocol["box_radius"]
    algorithm.low_bounds[:] = -protocol["box_radius"]
    algorithm.indicator = lambda y: y > threshold
    original_simulate, original_estimate = algorithm._simulate, algorithm._estimate_batch
    rejected, snapshots = [], []
    max_log_density_error = 0.0

    def capped_simulate(points, *args, **kwargs):
        used = algorithm.phase_samples.get("initialization", 0)
        if algorithm.phase == "initialization" and used + len(points) > protocol["initialization_cap"]:
            rejected.append(dict(phase="initialization", requested=len(points), submitted=0))
            raise InitializationCapReached("Initialization cap reached before the next measurement batch")
        return original_simulate(points, *args, **kwargs)

    def spherical_initializer(required, batch):
        candidates = []
        for radius in protocol["sphere_radii"]:
            # np.random is the same seeded NumPy stream used by the legacy box.
            points = sphere_points(radius, batch, 2, np.random)
            values = algorithm._simulate(points)
            candidates.extend(points[algorithm.indicator(values).ravel()])
            if len(candidates) >= required:
                return np.array(candidates[:required]), algorithm.phase_samples["initialization"]
        raise InitializationCapReached("Frozen radius schedule exhausted before enough failures were found")

    def audited_estimate(points, proposal):
        nonlocal max_log_density_error
        centers = proposal.model.mu.detach().numpy()[0].copy()
        variances = proposal.model.var.detach().numpy()[0].copy()
        weights = proposal.model.pi.detach().numpy().reshape(-1).copy()
        expected_beta = -.5 * np.square(centers).sum(axis=1)
        expected_beta = np.exp(expected_beta - logsumexp(expected_beta))
        np.testing.assert_allclose(weights, expected_beta, rtol=1e-12, atol=0)
        np.testing.assert_array_equal(variances, np.broadcast_to(protocol["kernel_variance"], variances.shape))
        known_q = logsumexp(norm.logpdf(points[:, None, :], centers[None, :, :],
                                      np.sqrt(variances)[None, :, :]).sum(axis=2)
                            + np.log(weights), axis=1)
        values = original_estimate(points, proposal)
        record = algorithm.estimation_records[-1]
        known_f = -.5 * np.square(points).sum(axis=1) - math.log(2 * math.pi)
        np.testing.assert_allclose(record["log_f"], known_f, rtol=1e-12, atol=2e-12)
        np.testing.assert_allclose(record["log_g"], known_q, rtol=1e-12, atol=2e-12)
        max_log_density_error = max(max_log_density_error, float(np.max(np.abs(record["log_g"] - known_q))))
        snapshots.append(dict(centers=centers.tolist(), variances=variances.tolist(),
                              weights=weights.tolist(), independent_log_q=known_q.tolist()))
        return values

    status, detail = "unexpected_error", ""
    with patch.object(algorithm, "_simulate", side_effect=capped_simulate), \
            patch.object(algorithm, "_estimate_batch", side_effect=audited_estimate), \
            patch.object(algorithm, "_save_result"), redirect_stdout(io.StringIO()):
        context = patch.object(algorithm, "_initial_sampling", side_effect=spherical_initializer)
        if arm == "legacy_box":
            context = patch.object(algorithm, "_initial_sampling", wraps=algorithm._initial_sampling)
        elif arm != "sphere_surface":
            raise ValueError("Unknown arm")
        try:
            with context:
                algorithm.start_estimate(protocol["budget_per_run"], strict_budget=True)
        except InitializationCapReached as error:
            status, detail = "initialization_insufficient", str(error)
        except AlgorithmBudgetReached as error:
            status, detail = "budget_reached", str(error)
            rejected.append(dict(phase=algorithm.phase, requested=protocol["estimation_batch"], submitted=0))
        except Exception:
            detail = traceback.format_exc()

    stats = weight_summary(algorithm.estimation_records)
    records = algorithm.request_records
    init = [r["samples"] for r in records if r["phase"] == "initialization"]
    chosen = [r["samples"] for r in records if r["phase"] == "recheck"]
    formal = [r for r in algorithm.estimation_records if r["status"] == "evaluated"]
    empty = np.empty((0, 2))
    init_points, selected = np.vstack(init) if init else empty, np.vstack(chosen) if chosen else empty
    final_points = np.vstack([r["samples"] for r in formal]) if formal else empty
    assert algorithm.submitted_samples == sum(len(r["samples"]) for r in records)
    assert algorithm.returned_samples == sum(len(r["samples"]) for r in records if r["status"] == "returned")
    assert stats["estimation_draws_evaluated"] == sum(len(r["samples"]) for r in formal)
    assert algorithm.submitted_samples <= protocol["budget_per_run"]
    independent = [np.exp(r["log_f"] - np.array(s["independent_log_q"])) * r["indicator"]
                   for r, s in zip(formal, snapshots)]
    if independent:
        np.testing.assert_allclose(stats["log_domain_pfail"], np.concatenate(independent).mean(), rtol=2e-12, atol=0)
    row = dict(arm=arm, seed=seed, status=status, detail=detail,
               generated=algorithm.submitted_samples + sum(r["requested"] for r in rejected if r["phase"] == "initialization"),
               submitted=algorithm.submitted_samples, returned=algorithm.returned_samples,
               initialization=algorithm.phase_samples.get("initialization", 0),
               recheck=algorithm.phase_samples.get("recheck", 0),
               estimation=stats["estimation_draws_evaluated"],
               initialization_regions=len(regions(init_points, threshold)),
               selected_regions=len(regions(selected, threshold)),
               estimation_regions=len(regions(final_points, threshold)),
               pfail=stats["log_domain_pfail"], numeric_status=stats["numeric_status"],
               max_log_density_error=max_log_density_error,
               **sampling_diagnostics(algorithm.estimation_records))
    evidence = dict(row=row, rejected_batches=rejected, proposals=snapshots,
                    calls=[{key: value.tolist() if isinstance(value, np.ndarray) else value
                            for key, value in record.items()} for record in records],
                    formal=[{key: value.tolist() if isinstance(value, np.ndarray) else value
                             for key, value in record.items()} for record in algorithm.estimation_records])
    return row, evidence


def summarize(rows, truth):
    summary = []
    for arm in sorted({r["arm"] for r in rows}):
        group = [r for r in rows if r["arm"] == arm]
        estimates = np.array([r["pfail"] for r in group if r["pfail"] is not None])
        errors = estimates - truth
        summary.append(dict(arm=arm, planned=len(group), with_estimate=len(estimates),
                            no_estimate_fraction=1 - len(estimates) / len(group),
                            initialization_both_regions=sum(r["initialization_regions"] == 2 for r in group),
                            selected_both_regions=sum(r["selected_regions"] == 2 for r in group),
                            estimation_both_regions=sum(r["estimation_regions"] == 2 for r in group),
                            mean_estimate=float(estimates.mean()) if len(estimates) else None,
                            sd_estimate=float(estimates.std(ddof=1)) if len(estimates) > 1 else None,
                            conditional_rmse=float(np.sqrt(np.mean(errors**2))) if len(estimates) else None,
                            conditional_mae=float(np.mean(np.abs(errors))) if len(estimates) else None,
                            estimates_over_one=int((estimates > 1).sum()),
                            warned_runs=sum(bool(r["warnings"]) for r in group),
                            mean_initialization=float(np.mean([r["initialization"] for r in group])),
                            mean_formal=float(np.mean([r["estimation"] for r in group]))))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    protocol_file = Path(__file__).with_name("protocol.json")
    protocol = json.loads(protocol_file.read_text())
    args.output_dir.mkdir(parents=True, exist_ok=False)
    provenance = dict(git_revision=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                      tracked_status=subprocess.check_output(["git", "status", "--short", "--untracked-files=no"], text=True),
                      protocol_sha256=hashlib.sha256(protocol_file.read_bytes()).hexdigest(),
                      script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__, torch=torch.__version__)
    (args.output_dir / "provenance.json").write_text(json.dumps(provenance, indent=2))
    (args.output_dir / "protocol.json").write_bytes(protocol_file.read_bytes())
    truth = math.erfc(protocol["failure_threshold"] / math.sqrt(2))
    rows = []
    with patch("subprocess.Popen", side_effect=AssertionError("This study must not launch subprocesses")), \
            (args.output_dir / "records.jsonl").open("w") as evidence:
        for seed in range(protocol["seed_start"], protocol["seed_start"] + protocol["seed_count"]):
            for arm in protocol["arms"]:
                row, trace = run_one(arm, seed, protocol)
                rows.append(row)
                evidence.write(json.dumps(trace) + "\n")
            print(f"seed={seed} both arms retained", flush=True)
    with (args.output_dir / "runs.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = dict(truth=truth, summaries=summarize(rows, truth),
                   unexpected_errors=sum(row["status"] == "unexpected_error" for row in rows),
                   max_log_density_error=max(row["max_log_density_error"] for row in rows),
                   subprocess_calls_during_experiment=0, xyce_calls=0)
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return int(summary["unexpected_errors"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
