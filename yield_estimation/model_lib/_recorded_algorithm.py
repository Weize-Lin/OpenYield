"""Opt-in request accounting; no sampling distribution changes."""
import numpy as np


class AlgorithmBudgetReached(RuntimeError):
    pass


class RecordedAlgorithm:
    read_target = (1, 1)
    operation = "read"

    def _budget_setup(self, max_num, strict_budget):
        if strict_budget and (type(max_num) is not int or max_num < 1):
            raise ValueError("strict budget must be a positive integer")
        self.strict_budget = strict_budget
        self.budget = max_num
        self.submitted_samples = 0
        self.returned_samples = 0
        self.phase_samples = {}
        self.phase = "initialization"
        self.request_records = []
        self.estimation_records = []
        self.last_estimate = None
        if strict_budget and self.IS_bound_on:
            raise ValueError("Strict diagnostic mode requires unclipped proposal samples")

    def _research_proposal(self, proposal):
        research = getattr(self, "research", None)
        return proposal if research is None else research.wrap_proposal(proposal)

    def _check_budget(self, n):
        research = getattr(self, "research", None)
        if research:
            research.check_draw_limit(self, n)
        if getattr(self, "strict_budget", False) and self.submitted_samples + n > self.budget:
            raise AlgorithmBudgetReached(
                f"Used {self.submitted_samples}/{self.budget}; next {self.phase} batch needs {n}")

    def _simulate(self, x, operation=None, target_row=None, target_col=None):
        operation = self.operation if operation is None else operation
        if target_row is None and target_col is None:
            target_row, target_col = self.read_target
        if not hasattr(self, "submitted_samples"):
            self._budget_setup(None, False)
        n = len(x)
        self._check_budget(n)
        self.submitted_samples += n
        self.phase_samples[self.phase] = self.phase_samples.get(self.phase, 0) + n
        record = dict(samples=np.array(x, copy=True), phase=self.phase,
                      operation=operation, target_row=target_row, target_col=target_col,
                      status="submitted")
        self.request_records.append(record)
        try:
            research = getattr(self, "research", None)
            invoke = self.mc_testbench.run_mc_simulation if research is None else research.simulate
            phase = {} if research is None else {"phase": self.phase}
            y, _, _, _ = invoke(**phase,
                operation=operation, target_row=target_row, target_col=target_col,
                mc_runs=n, vars=x)
            y = np.asarray(y).reshape(-1, 1)
            if len(y) != n:
                raise RuntimeError("Simulator returned a different number of rows")
            if research and research.profile["mode"] == "variant" and self.phase == "recheck" and not self.indicator(y).all():
                raise RuntimeError("Selected failed points did not all fail on recheck")
            self.returned_samples += n
            record.update(status="returned", delays=y.ravel().copy())
            return y
        except BaseException:
            record["status"] = "unresolved"
            raise

    def _estimate_batch(self, x, proposal):
        self._check_budget(len(x))
        log_f = np.asarray(self.f_norm.log_pdf(x)).reshape(-1)
        log_g = np.asarray(proposal.log_pdf(x)).reshape(-1)
        support = (self.f_norm.in_support(x) if hasattr(self.f_norm, "in_support")
                   else np.isfinite(log_f))
        record = dict(samples=np.array(x, copy=True), support=np.asarray(support, dtype=bool),
                      log_f=log_f, log_g=log_g, status="pending",
                      delays=np.full(len(x), np.nan))
        research = getattr(self, "research", None)
        if research:
            research.capture_proposal(record, proposal)
            if hasattr(proposal, "last_target_mask"):
                record["from_target"] = proposal.last_target_mask.copy()
        self.estimation_records.append(record)
        try:
            # Unlike non-adaptive MNIS, these algorithms use physical labels to update
            # centers. Do not fabricate those labels for nonphysical samples.
            if not np.all(support):
                if research:
                    self.submitted_samples += len(x)
                    self.phase_samples[self.phase] = self.phase_samples.get(self.phase, 0) + len(x)
                raise ValueError("Adaptive proposal contains samples outside target support; "
                                 "no redraw, clipping or fabricated labels were applied")
            if not np.isfinite(log_f).all() or not np.isfinite(log_g).all():
                raise ValueError("Non-finite density on supported proposal samples")
            y = self._simulate(x)
            indicator = self.indicator(y).reshape(-1)
            record.update(status="evaluated", delays=y.ravel().copy(), indicator=indicator)
            return y, log_f, log_g, indicator
        except BaseException:
            record["status"] = "unresolved"
            raise
