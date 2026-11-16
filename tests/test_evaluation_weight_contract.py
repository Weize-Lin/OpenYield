"""Reproducible analytic tests for the importance weights used by evaluation."""

import math
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np
import torch

from yield_estimation.evaluation.mnis_run import sampling_diagnostics, weight_summary
from yield_estimation.evaluation.hold_snm_study import method_rows
from yield_estimation.evaluation.run import evaluation_circuit_options, read_config, validate_algorithm_case
from yield_estimation.evaluation.run_diagnostics import MODULES
from yield_estimation.model_lib._recorded_algorithm import RecordedAlgorithm
from yield_estimation.tool.Distribution.gmm_v2 import mixture_gaussian
from yield_estimation.tool.Distribution.normal_v1 import norm_dist


def as_measurements(*, vars, **kwargs):
    return vars[:, 0], None, None, None


class ImportanceWeightChecks(unittest.TestCase):
    def setUp(self):
        np.random.seed(812)
        torch.manual_seed(812)
        self.target = norm_dist(np.array([0.]), np.array([[1.]]))
        self.estimate = RecordedAlgorithm()
        self.estimate.mc_testbench = SimpleNamespace(run_mc_simulation=as_measurements)
        self.estimate.f_norm = self.target
        self.estimate.indicator = lambda values: values > 1
        self.estimate.IS_bound_on = False
        self.estimate._budget_setup(24000, True)
        self.exact = math.erfc(1 / math.sqrt(2)) / 2

    def recorded_batch(self, proposal, x):
        self.estimate.phase = "estimation"
        self.estimate._estimate_batch(x, proposal)
        return self.estimate.estimation_records

    def test_target_proposal_gives_unit_weights(self):
        samples = np.array([[-1.], [0.], [1.], [2.]])
        records = self.recorded_batch(self.target, samples)
        np.testing.assert_array_equal(records[0]["log_f"], records[0]["log_g"])
        self.assertAlmostEqual(weight_summary(records)["log_domain_pfail"], .25)
        weights = sampling_diagnostics(records)
        self.assertAlmostEqual(weights["log10_mean_weight"], 0)
        self.assertAlmostEqual(weights["weight_ess"], 4)

    def test_shifted_normal_estimates_known_tail(self):
        proposal = norm_dist(np.array([1.]), np.array([[1.]]))
        samples = proposal.sample(24000)
        records = self.recorded_batch(proposal, samples)
        np.testing.assert_allclose(
            records[0]["log_f"] - records[0]["log_g"], .5 - samples[:, 0], atol=1e-14
        )
        self.assertAlmostEqual(weight_summary(records)["log_domain_pfail"], self.exact, delta=.005)
        self.assertEqual(self.estimate.returned_samples, 24000)

    def test_gaussian_mixture_estimates_known_tail(self):
        coefficients = np.array([.35, .65])
        means = np.array([[-.1], [1.2]])
        proposal = mixture_gaussian(coefficients, means, var_num=1.)
        samples = proposal.sample(24000)
        records = self.recorded_batch(proposal, samples)
        components = -.5 * ((samples - means.T) ** 2 + math.log(2 * math.pi))
        expected_log_proposal = np.logaddexp.reduce(components + np.log(coefficients), axis=1)
        np.testing.assert_allclose(records[0]["log_g"], expected_log_proposal, atol=5e-6)
        self.assertAlmostEqual(weight_summary(records)["log_domain_pfail"], self.exact, delta=.005)

    def test_zero_density_draw_still_counts_in_denominator(self):
        records = [dict(status="evaluated", samples=np.ones((2, 1)), support=np.array([True, False]),
                        indicator=np.array([True, False]), log_f=np.array([0., -np.inf]),
                        log_g=np.array([0., 0.]))]
        result = weight_summary(records)
        self.assertEqual(result["estimation_draws_evaluated"], 2)
        self.assertEqual(result["supported_estimation_draws"], 1)
        self.assertAlmostEqual(result["log_domain_pfail"], .5)


class ExplicitSettingsChecks(unittest.TestCase):
    def test_unavailable_weight_diagnostics_are_not_counted_as_clear(self):
        trials = [dict(algorithm="mnis", issues="no_estimate", weight_ess=None,
                       weight_warnings="", above_one=False),
                  dict(algorithm="mnis", issues="", weight_ess=1.2, estimate=.02, abs_error=.1,
                       weight_warnings="low_observed_weight_mass", above_one=False)]
        result = next(row for row in method_rows(trials) if row["algorithm"] == "mnis")
        self.assertEqual(result["weight_diagnostic_runs"], 1)
        self.assertEqual(result["low_weight_runs"], 1)
        self.assertIsNone(result["rmse"])

    def test_all_benchmark_entrypoints_import(self):
        for module in set(MODULES.values()):
            with self.subTest(module=module):
                self.assertTrue(callable(import_module(module).main))

    def test_search_width_no_longer_selects_algorithm_settings(self):
        case = Path("yield_estimation/evaluation/cases/hold_snm_6t_study.json")
        config = read_config(case)
        options = evaluation_circuit_options(config)
        for method, key in (("mnis", "mnis_proposal_covariance"), ("hscs", "hscs_search_origin")):
            with self.subTest(algorithm=method):
                validate_algorithm_case(options, method, config)
                implicit = dict(config)
                del implicit[key]
                with self.assertRaisesRegex(ValueError, key):
                    validate_algorithm_case(options, method, implicit)

    def test_legacy_case_without_configured_bounds_is_still_accepted(self):
        case = Path("yield_estimation/evaluation/cases/hold_snm_6t_configurable.json")
        config = read_config(case)
        options = evaluation_circuit_options(config)
        for algorithm in ("mnis", "hscs"):
            with self.subTest(algorithm=algorithm):
                validate_algorithm_case(options, algorithm, config)


if __name__ == "__main__":
    unittest.main()
