"""Contracts for the bounded study, without a circuit or replacement estimator."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
from scipy.stats import norm, truncnorm

from yield_estimation.evaluation.run import IndependentProcess

DIRECTORY = Path(__file__).resolve().parents[1] / "docs/research/acs_initialization"
SPEC = importlib.util.spec_from_file_location("acs_initialization_study", DIRECTORY / "study.py")
STUDY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STUDY)
PROTOCOL = json.loads((DIRECTORY / "protocol.json").read_text())


class InitializationStudyContracts(unittest.TestCase):
    def test_surface_radius_and_reproducible_directions(self):
        points = STUDY.sphere_points(3., 100, 18, np.random.default_rng(41))
        np.testing.assert_allclose(np.linalg.norm(points, axis=1), 3., rtol=2e-15)
        np.testing.assert_array_equal(points, STUDY.sphere_points(3., 100, 18, np.random.default_rng(41)))

    def test_truncated_mapping_matches_independent_survival_probabilities(self):
        z = np.repeat(np.array([-4., -2., 0., 2., 4.])[:, None], 3, axis=1)
        for relative_std in (.05, .5, 2.):
            with self.subTest(relative_std=relative_std):
                process = IndependentProcess(np.array([.4106, .045, -.13]), relative_std, 41)
                x = STUDY.latent_to_process(z, process)
                self.assertTrue(process.in_support(x).all())
                mu, sigma = process.means[1], process.sigma[1]
                known = truncnorm.logsf(x[:, 1], -mu / sigma, np.inf, loc=mu, scale=sigma)
                np.testing.assert_allclose(known, norm.logsf(z[:, 1]), rtol=1e-10, atol=1e-11)
                expected_density = norm.logpdf(x[:, [0, 2]], process.means[[0, 2]], process.sigma[[0, 2]]).sum(axis=1)
                expected_density += truncnorm.logpdf(x[:, 1], -mu / sigma, np.inf, loc=mu, scale=sigma)
                np.testing.assert_allclose(process.log_pdf(x), expected_density, atol=1e-10)
                # A latent zero is the conditional median, generally not nominal u0.
                if relative_std == 2.:
                    self.assertGreater(x[2, 1], mu)

    def test_exact_budget_and_rejected_whole_batch(self):
        for budget in (52, 53):
            for arm in PROTOCOL["arms"]:
                with self.subTest(budget=budget, arm=arm):
                    protocol = copy.deepcopy(PROTOCOL)
                    protocol.update(budget_per_run=budget, failure_threshold=.01)
                    with patch("subprocess.Popen", side_effect=AssertionError("No subprocess")):
                        row, evidence = STUDY.run_one(arm, 7, protocol)
                    self.assertEqual(row["status"], "budget_reached")
                    self.assertEqual((row["initialization"], row["recheck"], row["estimation"]), (16, 4, 32))
                    self.assertEqual((row["submitted"], row["returned"], row["generated"]), (52, 52, 52))
                    self.assertEqual(len(evidence["formal"]), 2)
                    self.assertEqual(evidence["rejected_batches"], [dict(phase="estimation", requested=16, submitted=0)])
                    # Initial and new failure centers are reused, not old contributions.
                    old_centers = evidence["proposals"][0]["centers"]
                    first = evidence["formal"][0]
                    expected_centers = old_centers + [point for point, failed in zip(first["samples"], first["indicator"]) if failed]
                    np.testing.assert_array_equal(evidence["proposals"][1]["centers"], expected_centers)

    def test_insufficient_candidates_have_no_estimate(self):
        for arm in PROTOCOL["arms"]:
            with self.subTest(arm=arm):
                protocol = copy.deepcopy(PROTOCOL)
                protocol.update(initialization_cap=16, failure_threshold=10.)
                with patch("subprocess.Popen", side_effect=AssertionError("No subprocess")):
                    row, evidence = STUDY.run_one(arm, 7, protocol)
                self.assertEqual(row["status"], "initialization_insufficient")
                self.assertEqual((row["submitted"], row["returned"], row["estimation"]), (16, 16, 0))
                self.assertEqual(row["generated"], 32)
                self.assertIsNone(row["pfail"])
                self.assertEqual(evidence["formal"], [])
                summary = STUDY.summarize([row], .01)[0]
                self.assertEqual(summary["no_estimate_fraction"], 1.)
                self.assertIsNone(summary["conditional_rmse"])


if __name__ == "__main__":
    unittest.main()
