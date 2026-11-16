"""Sample and evaluate a defensive mixture with the original target."""

import numpy as np


class DefensiveMixture:
    """Mix target and proposal by whole parameter vectors."""

    def __init__(self, target, proposal, target_fraction, dimensions):
        if not 0 < target_fraction < 1:
            raise ValueError("Defensive mixture target fraction must be between 0 and 1")
        self.target = target
        self.proposal = proposal
        self.target_fraction = target_fraction
        self.dimensions = dimensions

    def sample(self, n):
        self.last_target_mask = np.random.random(n) < self.target_fraction
        samples = np.empty((n, self.dimensions))
        target_count = int(self.last_target_mask.sum())
        if target_count:
            samples[self.last_target_mask] = self.target.sample(target_count)
        if target_count < n:
            samples[~self.last_target_mask] = self.proposal.sample(n - target_count)
        return samples

    def log_pdf(self, samples):
        target = np.asarray(self.target.log_pdf(samples)).reshape(-1)
        proposal = np.asarray(self.proposal.log_pdf(samples)).reshape(-1)
        return np.logaddexp(
            np.log(self.target_fraction) + target,
            np.log1p(-self.target_fraction) + proposal,
        )
