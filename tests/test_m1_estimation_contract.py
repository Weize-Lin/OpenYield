"""CPU-only checks of the actual estimators, with analytic measurements in place of SPICE."""
from contextlib import redirect_stdout
import io
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from scipy.special import logsumexp
from scipy.stats import norm, truncnorm
import torch

from yield_estimation.evaluation import mnis_run
from yield_estimation.evaluation.run import IndependentProcess, read_config
from yield_estimation.model_lib.ACS import ACS
from yield_estimation.model_lib.MNIS import MNIS, MNISBudgetReached
from yield_estimation.model_lib._recorded_algorithm import AlgorithmBudgetReached, RecordedAlgorithm
from yield_estimation.tool.Distribution.gmm_v2 import mixture_gaussian
from yield_estimation.tool.Distribution.multi_cone_cluster import cone_cluster
from yield_estimation.tool.Distribution.normal_v1 import norm_dist


class DistributionContracts(unittest.TestCase):
    def test_positive_process_sampling_and_density(self):
        means = np.array([.4, .04, -.13])
        relative_std = .65
        sigma = np.abs(means) * relative_std
        u0 = truncnorm(-means[1] / sigma[1], np.inf, loc=means[1], scale=sigma[1])
        count = 4096
        for seed in (61, 62):
            with self.subTest(seed=seed):
                process = IndependentProcess(means, relative_std, seed)
                x = process.sample(count)
                self.assertTrue(process.in_support(x).all())
                expected = (norm.logpdf(x[:, 0], means[0], sigma[0]) + u0.logpdf(x[:, 1])
                            + norm.logpdf(x[:, 2], means[2], sigma[2]))
                np.testing.assert_allclose(process.log_pdf(x), expected, atol=1e-12)
                self.assertLess(abs(x[:, 1].mean() - u0.mean()), 6 * u0.std() / math.sqrt(count))
                outside = x[:1].copy()
                outside[0, 1] = 0.
                self.assertTrue(np.isneginf(process.log_pdf(outside)[0]))

    def test_two_component_sampler_uses_distinct_feature_variances(self):
        weights = np.array([.25, .75])
        means = np.array([[-3., .1], [3., -.1]])
        variances = np.array([.04, .16])
        count = 4096
        for seed in (81, 82):
            with self.subTest(seed=seed):
                torch.manual_seed(seed)
                proposal = mixture_gaussian(weights, means, variances)
                x = proposal.sample(count)
                component = (x[:, 0] > 0).astype(int)
                self.assertLess(abs(component.mean() - .75), 6 * math.sqrt(.25 * .75 / count))
                z = (x - means[component]) / np.sqrt(variances)
                np.testing.assert_allclose(z.var(axis=0), [1., 1.], atol=.13, rtol=0)
                terms = norm.logpdf(x[:, None, :], means, np.sqrt(variances)).sum(axis=2)
                np.testing.assert_allclose(proposal.log_pdf(x).ravel(), logsumexp(terms + np.log(weights), axis=1), atol=1e-10)

    def test_narrow_gaussian_log_density_at_one_standard_deviation(self):
        mean = np.array([[.4]])
        sigma = 1e-9
        x = np.array([[.4], [.4 + sigma], [.4 - sigma]])
        mixture = mixture_gaussian(np.array([1.]), mean, np.array([sigma ** 2]))
        np.testing.assert_allclose(mixture.log_pdf(x).ravel(), norm.logpdf(x[:, 0], .4, sigma), atol=1e-11, rtol=0)


class EstimatorContracts(unittest.TestCase):
    def test_real_mnis_analytic_median_with_fixed_draws(self):
        means = np.array([1., .04, -.2])
        bounds = np.array([[.98, .042, -.19], [.96, .038, -.21]])
        batch = 2048
        formal = 2 * batch
        budget = 3 + formal
        # With a 50% target mixture, I*f/g is in [0,2]. Hoeffding and a
        # union bound across these three fixed-seed runs give < 1.3e-5.
        tolerance = .08
        for seed in (61, 62, 63):
            with self.subTest(seed=seed):
                target = IndependentProcess(means, .05, seed)
                calls = []

                def measure(*, vars, mc_runs, **kwargs):
                    calls.append(mc_runs)
                    return vars[:, 0], None, None, None

                bench = SimpleNamespace(run_mc_simulation=measure)
                algorithm = MNIS(bench, target, 3, means, .01, 1, 2, batch, 13, .24, 1,
                                 seed=seed, initialization_bounds=bounds,
                                 proposal_variance=target.sigma ** 2, target_fraction=.5)
                algorithm.indicator = lambda y: y < 1.
                with patch.object(algorithm, '_save_result'), redirect_stdout(io.StringIO()):
                    with self.assertRaises(MNISBudgetReached):
                        algorithm.start_estimate(budget, strict_budget=True, skip_zero_target=True)
                stats = mnis_run.weight_summary(algorithm.estimation_records)
                self.assertEqual(calls, [2, 1, batch, batch])
                self.assertEqual(algorithm.submitted_samples, budget)
                self.assertEqual(stats['estimation_draws_evaluated'], formal)
                self.assertAlmostEqual(stats['log_domain_pfail'], .5, delta=tolerance)
                print(f'MNIS analytic seed={seed} exact=0.5 estimate={stats["log_domain_pfail"]:.8f} formal={formal} charged={budget}')

    def test_acs_current_proposals_and_reused_centers_under_strict_budget(self):
        def measure(*, mc_runs, **kwargs):
            return np.ones(mc_runs), None, None, None

        target = norm_dist(np.zeros(1), np.eye(1))
        algorithm = ACS(SimpleNamespace(run_mc_simulation=measure), 1, 1, False, target,
                        .5, 2, 2, 2, 10, 41, initialization_bounds=np.array([[1.2], [1.1]]))
        algorithm.indicator = lambda y: y > 0
        original = algorithm._estimate_batch
        used = []

        def observe(samples, proposal):
            used.append((proposal.model.mu.detach().numpy()[0].copy(),
                         proposal.model.pi.detach().numpy().ravel().copy()))
            return original(samples, proposal)

        with patch.object(algorithm, '_estimate_batch', side_effect=observe), patch.object(algorithm, '_save_result'), \
                patch.object(cone_cluster, 'cluster', side_effect=AssertionError('ACS executed cluster')) as cluster, \
                redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(AlgorithmBudgetReached, r'8/9'):
                algorithm.start_estimate(max_num=9, strict_budget=True)
        cluster.assert_not_called()
        records = algorithm.estimation_records
        self.assertEqual(algorithm.phase_samples, {'initialization': 2, 'recheck': 2, 'estimation': 4})
        self.assertEqual([r['phase'] for r in algorithm.request_records],
                         ['initialization', 'recheck', 'estimation', 'estimation'])
        self.assertEqual([len(centers) for centers, _ in used], [2, 4])
        np.testing.assert_array_equal(used[1][0], np.vstack([used[0][0], records[0]['samples']]))
        contributions = []
        for record, (centers, probabilities) in zip(records, used):
            points = record['samples']
            known_q = logsumexp(norm.logpdf(points, centers.T, math.sqrt(.5)) + np.log(probabilities), axis=1)
            np.testing.assert_allclose(record['log_g'], known_q, atol=1e-6)
            contributions.extend(np.exp(norm.logpdf(points[:, 0]) - known_q))
        stats = mnis_run.weight_summary(records)
        self.assertEqual(stats['estimation_draws_evaluated'], 4)
        self.assertAlmostEqual(stats['log_domain_pfail'], np.mean(contributions), places=6)
        print('ACS cluster_calls=0 components=2,4 phases=2+2+4 budget=9; leftover=1; formal_denominator=4')

    def test_unequal_completed_batches_and_extreme_log_weights(self):
        def record(log_weights, failures):
            count = len(log_weights)
            return dict(status='evaluated', samples=np.empty((count, 1)), support=np.ones(count, bool),
                        indicator=np.array(failures), log_f=np.array(log_weights), log_g=np.zeros(count))

        records = [record([0.], [True]), record([800., math.log(2), -800.], [False, True, True])]
        estimate = mnis_run.weight_summary(records)
        self.assertEqual(estimate['estimation_draws_evaluated'], 4)
        self.assertAlmostEqual(estimate['log_domain_pfail'], 3 / 4)
        for value, state in ((-800., 'underflow'), (800., 'overflow')):
            result = mnis_run.weight_summary([record([value], [True])])
            self.assertEqual(result['numeric_status'], state)
            self.assertIsNone(result['log_domain_pfail'])
            self.assertAlmostEqual(result['log10_pfail'], value / math.log(10))

    def test_adaptive_outside_support_is_unresolved_and_never_labelled(self):
        target = IndependentProcess(np.array([1., .04, -.2]), .05, 61)
        algorithm = RecordedAlgorithm()
        algorithm.IS_bound_on = False
        algorithm.f_norm = target
        algorithm._budget_setup(2, True)
        algorithm.phase = 'estimation'
        algorithm.mc_testbench = SimpleNamespace(run_mc_simulation=lambda **_: self.fail('invalid Xyce call'))
        x = np.array([[1., -.001, -.2]])
        proposal = norm_dist(np.zeros(3), np.eye(3))
        with self.assertRaisesRegex(ValueError, 'outside target support'):
            algorithm._estimate_batch(x, proposal)
        self.assertEqual(algorithm.estimation_records[0]['status'], 'unresolved')
        self.assertNotIn('indicator', algorithm.estimation_records[0])
        self.assertIsNone(mnis_run.weight_summary(algorithm.estimation_records)['log_domain_pfail'])

    def test_real_mnis_zero_target_stays_in_denominator_and_skips_measurement(self):
        means = np.array([1., .04, -.2])
        target = IndependentProcess(means, .05, 61)
        calls = []

        def measure(*, vars, mc_runs, **kwargs):
            calls.append(mc_runs)
            return vars[:, 0], None, None, None

        # Initial and recheck both fail. Inject only the two formal coordinates;
        # the estimator still evaluates the actual proposal and target densities.
        points = np.array([[.97, .04, -.2], [.97, -.01, -.2]])
        bounds = np.array([[.98, .042, -.19], [.96, .038, -.21]])
        algorithm = MNIS(SimpleNamespace(run_mc_simulation=measure), target, 3, means,
                         .01, 1, 1, 2, 13, .24, 1, seed=61,
                         initialization_bounds=bounds, proposal_variance=target.sigma ** 2)
        algorithm.indicator = lambda y: y < 1.

        with patch.object(norm_dist, 'sample', return_value=points), \
                patch.object(algorithm, '_save_result'), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(MNISBudgetReached, 'Used 4/4'):
                algorithm.start_estimate(4, strict_budget=True, skip_zero_target=True)
        record, = algorithm.estimation_records
        stats = mnis_run.weight_summary([record])
        np.testing.assert_array_equal(record['indicator'], [True, False])
        np.testing.assert_array_equal(record['support'], [True, False])
        self.assertEqual(calls, [1, 1, 1])
        self.assertEqual(algorithm.submitted_samples, 4)
        self.assertEqual(stats['estimation_draws_evaluated'], 2)
        self.assertAlmostEqual(stats['log10_pfail'],
                               (record['log_f'][0] - record['log_g'][0] - math.log(2)) / math.log(10))

    def test_mnis_runner_writes_log_domain_result_with_nonfinite_legacy_fom(self):
        case = Path('yield_estimation/evaluation/cases/hold_snm_6t_study.json')
        config = read_config(case)
        means = np.tile([.4, .04, -.13], 6)
        target = IndependentProcess(means, .05, 61)

        def finish(algorithm, *args, **kwargs):
            with np.errstate(over='ignore'):
                fom = algorithm._calculate_FOM([1e200, 1e199], 13)
            self.assertTrue(np.isposinf(fom))
            algorithm.last_estimate = dict(pfail=1e200, FOM=fom, samples=2)
            algorithm.estimation_records = [dict(status='evaluated', samples=np.array([means]),
                support=np.array([True]), indicator=np.array([True]), log_f=np.array([-800.]),
                log_g=np.array([0.]), delays=np.array([.3]))]
            raise MNISBudgetReached('synthetic boundary')

        with TemporaryDirectory() as directory:
            output = Path(directory) / 'result'
            with patch.object(mnis_run.shutil, 'which', return_value='placeholder'), \
                    patch.object(mnis_run, 'build_evaluation_case', return_value=(means, target, object())), \
                    patch.object(MNIS, 'start_estimate', new=finish), redirect_stdout(io.StringIO()):
                self.assertEqual(mnis_run.run(config, output), 0)
            result = json.loads((output / 'run_summary.json').read_text())
            self.assertEqual(result['original_estimate']['FOM'], 'inf')
            self.assertEqual(result['diagnostics']['numeric_status'], 'underflow')
            self.assertAlmostEqual(result['log10_pfail'], -800 / math.log(10))


if __name__ == '__main__':
    unittest.main()
