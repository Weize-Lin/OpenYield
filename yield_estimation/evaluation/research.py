"""Candidate acquisition, actual simulator costs and proposal evidence for research runs."""

import hashlib
import json
from pathlib import Path

import numpy as np

from yield_estimation.tool.Distribution.defensive_mixture import DefensiveMixture


class SimulationBudgetReached(RuntimeError):
    pass


class InitializationInsufficient(RuntimeError):
    pass


class ResearchRun:
    def __init__(self, config, output, target, summary, rule):
        context = config["research"]
        self.profile = context["estimator"]
        self.draw_limit = context["max_algorithm_draws"]
        self.unit = context["budget_unit"]
        self.physical_limit = config["budget"]
        self.estimation_limit = self.profile.get("estimation_draws")
        self.output = output
        self.target = target
        self.seed = config["seed"]
        self.cost = {}
        self.initialization = None
        self.legacy_candidates = None
        self.identity = json.loads(json.dumps(dict(
            circuit=summary["resolved_circuit"], model_sha256=summary["model_sha256"],
            drivers=summary["driver_sizes"], timing=summary["timing"],
            interconnect=summary["interconnect"], equivalent=summary["equivalent"],
            parameter_order=summary["bitcell_parameter_order"],
            means=target.means.tolist(), sigma=target.sigma.tolist(),
            u0_positive=config["u0_positive"],
            failure_rule=dict(rule.metadata(), threshold=rule.threshold_value, unit=rule.unit),
        ), allow_nan=False))

    def attach(self, algorithm, recorder):
        self.recorder = recorder
        self.indicator = algorithm.indicator
        algorithm.research = self

    def check_draw_limit(self, algorithm, count):
        if self.legacy_candidates is not None and algorithm.phase == "initialization":
            remaining = min(self.profile.get("initialization", {}).get("max_simulations", self.physical_limit)
                            - self.recorder.submitted, self.draw_limit - algorithm.submitted_samples)
            if count > remaining:
                raise InitializationInsufficient("Next legacy initialization batch would exceed its simulation cap")
        if (self.estimation_limit is not None and algorithm.phase == "estimation"
                and algorithm.phase_samples.get("estimation", 0) + count > self.estimation_limit):
            raise SimulationBudgetReached(f"Fixed estimation draw cap reached ({self.estimation_limit})")

    def simulate(self, phase, **kwargs):
        submitted, returned = self.recorder.submitted, self.recorder.returned
        if submitted + kwargs["mc_runs"] > self.physical_limit:
            raise SimulationBudgetReached(f"Xyce cap: {submitted}/{self.physical_limit}; next {phase} batch needs {kwargs['mc_runs']}")
        counts = self.cost.setdefault(phase, {"xyce_submitted": 0, "xyce_returned": 0})
        if phase in ("write_search", "failure_region_search"):
            cap = self.profile.get("hscs_search_max_simulations", self.physical_limit)
            if counts["xyce_submitted"] + kwargs["mc_runs"] > cap:
                raise SimulationBudgetReached(f"HSCS {phase} cap reached ({cap})")
        try:
            result = self.recorder.run_mc_simulation(**kwargs)
            if self.legacy_candidates is not None and phase == "initialization":
                self._add_candidates(self.legacy_candidates, kwargs["vars"], result[0])
            return result
        finally:
            counts["xyce_submitted"] += self.recorder.submitted - submitted
            counts["xyce_returned"] += self.recorder.returned - returned

    def ledger(self, algorithm):
        phases = getattr(algorithm, "phase_samples", {})
        return dict(budget_unit=self.unit, xyce_cap=self.physical_limit,
                    algorithm_draw_cap=self.draw_limit, fixed_estimation_draw_cap=self.estimation_limit,
                    initialization_outcome=self.initialization["status"] if self.initialization else None,
                    phases={phase: dict(algorithm_charged_draws=phases.get(phase, 0),
                                        **self.cost.get(phase, dict(xyce_submitted=0, xyce_returned=0)))
                            for phase in dict.fromkeys([*phases, *self.cost])})

    def capture_proposal(self, record, proposal):
        record["proposal_json"] = json.dumps(proposal_snapshot(proposal), allow_nan=False)

    def wrap_proposal(self, proposal):
        fraction = self.profile.get("proposal", {}).get("target_fraction", 0.)
        if fraction:
            return DefensiveMixture(self.target, proposal, fraction, self.target.means.size)
        return proposal

    def _add_candidates(self, rows, samples, values):
        values = np.asarray(values).ravel()
        for x, y, failed in zip(samples, values, self.indicator(values).ravel()):
            rows.append(dict(id=f"sample-{len(rows) + 1}", parameters=x.tolist(),
                             measurement=float(y) if np.isfinite(y) else None, failed=bool(failed)))

    def acquire(self, algorithm, count, batch, legacy_initializer):
        if self.profile["mode"] == "legacy":
            return self._record_legacy(legacy_initializer, count, batch)
        settings = self.profile["initialization"]
        method = settings["method"]
        cap = settings["max_simulations"]
        required = settings["min_failed_candidates"]
        imported, source = None, None
        rng = np.random.default_rng(self.seed)
        rows = []
        found = []
        selected = []
        scores = []
        status = "insufficient"
        selection = self.profile.get("center_selection", self.profile.get("initial_pool"))
        try:
            if method == "from_file":
                imported, source = self._import_candidates(settings)
                cap = min(cap, len(imported))
            if method == "legacy":
                self.legacy_candidates = rows
                try:
                    legacy_initializer(required, min(batch, cap))
                except (InitializationInsufficient, SimulationBudgetReached):
                    pass
                finally:
                    self.legacy_candidates = None
                found = [row for row in rows if row["failed"]]
            while method != "legacy" and len(rows) < cap and len(found) < required:
                n = min(batch, cap - len(rows), self.physical_limit - self.recorder.submitted,
                        self.draw_limit - algorithm.submitted_samples)
                if n <= 0:
                    break
                if imported is not None:
                    samples = imported[len(rows):len(rows) + n]
                elif method == "target":
                    samples = self.target.sample(n)
                else:
                    radius = settings["radius_sigma"] * self.target.sigma
                    lower = self.target.means - radius
                    lower[1::3] = np.maximum(lower[1::3], np.finfo(float).tiny)
                    samples = rng.uniform(lower, self.target.means + radius, size=(n, self.target.means.size))
                values = algorithm._simulate(samples)
                values = np.asarray(values[0] if isinstance(values, tuple) else values).ravel()
                self._add_candidates(rows, samples, values)
                if source:
                    for row, old_id in zip(rows[-n:], source["candidate_ids"][len(rows)-n:len(rows)]):
                        row["source_candidate_id"] = old_id
                found = [row for row in rows if row["failed"]]
            if len(found) < required:
                raise InitializationInsufficient(f"Verified {len(found)}/{required} required failed candidates")
            indices, scores = select_candidates(found, selection["rule"], self.target)
            selected = [found[i] for i in indices[:count]]
            status = "selected"
            return np.asarray([row["parameters"] for row in selected]), len(rows)
        except InitializationInsufficient:
            raise
        except BaseException:
            status = "error"
            raise
        finally:
            self.initialization = dict(schema_version=1, identity=self.identity, method=method,
                source=source, status=status, required_failed_candidates=required,
                found_failed_candidates=len(found), simulated_rows=len(rows), candidates=rows,
                selection=dict(rule=selection["rule"], scores=dict(zip((row["id"] for row in found), scores)),
                               selected_ids=[row["id"] for row in selected]))
            self._write_initialization()

    def _record_legacy(self, initializer, count, batch):
        rows = []
        self.legacy_candidates = rows
        status = "error"
        try:
            result = initializer(count, batch)
            status = "selected"
            return result
        except InitializationInsufficient:
            status = "insufficient"
            raise InitializationInsufficient(
                f"Verified {sum(row['failed'] for row in rows)}/{count} required legacy failed candidates") from None
        finally:
            self.legacy_candidates = None
            self.initialization = dict(schema_version=1, identity=self.identity, method="legacy",
                source=None, status=status, required_failed_candidates=count,
                found_failed_candidates=sum(row['failed'] for row in rows), simulated_rows=len(rows),
                candidates=rows, selection=None)
            self._write_initialization()

    def _write_initialization(self):
        (self.output / "initialization.json").write_text(
            json.dumps(self.initialization, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    def _import_candidates(self, settings):
        path = Path(settings["path"])
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != settings["sha256"]:
            raise ValueError("Candidate file changed after configuration was frozen")
        manifest = json.loads(raw)
        if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
                or manifest.get("identity") != self.identity):
            raise ValueError("Candidate identity differs from the current circuit, process or failure rule")
        rows = manifest.get("candidates")
        if not isinstance(rows, list) or not all(isinstance(row, dict) and "parameters" in row
                and isinstance(row.get("id"), str) and row["id"] for row in rows):
            raise ValueError("Invalid candidate rows")
        ids = [row["id"] for row in rows]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate imported candidate ids")
        samples = (np.asarray([row["parameters"] for row in rows], dtype=float) if rows
                   else np.empty((0, self.target.means.size)))
        if samples.shape != (len(rows), self.target.means.size) or not self.target.in_support(samples).all():
            raise ValueError("Imported candidates must have finite, in-support parameters in circuit order")
        return samples, dict(path=str(path), sha256=settings["sha256"], available_rows=len(rows),
                             reported_prior_simulations=manifest.get("simulated_rows"), candidate_ids=ids,
                             label_policy="resimulate", prior_cost_charged=False)


def select_candidates(found, rule, target):
    if rule == "discovery_order":
        scores = np.arange(len(found), dtype=float)
    else:
        offsets = np.asarray([row["parameters"] for row in found]) - target.means
        if rule == "nearest_standardized":
            offsets = offsets / target.sigma
        scores = np.linalg.norm(offsets, axis=1)
    return np.argsort(scores, kind="stable"), scores.tolist()


def proposal_snapshot(proposal):
    """Serialize the distribution actually passed to log_pdf for this batch."""
    if isinstance(proposal, DefensiveMixture):
        return dict(kind="defensive_mixture", target_fraction=proposal.target_fraction,
                    target=dict(kind="independent_gaussian_positive_u0", means=proposal.target.means.tolist(),
                                sigma=proposal.target.sigma.tolist()),
                    proposal=proposal_snapshot(proposal.proposal))
    if hasattr(proposal, "model"):
        model = proposal.model
        return dict(kind="diagonal_gaussian_mixture", weights=model.pi.detach().numpy().reshape(-1).tolist(),
                    means=model.mu.detach().numpy()[0].tolist(), variances=model.var.detach().numpy()[0].tolist())
    return dict(kind="gaussian", means=proposal.mu.tolist(), covariance=proposal.var.tolist())
