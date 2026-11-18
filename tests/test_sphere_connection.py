"""Shared ACS initialization and real estimation paths with analytic measurements."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from yield_estimation.evaluation.experiment import normalize_experiment, child_configs
from yield_estimation.evaluation.outcomes import MeasurementRule
from yield_estimation.evaluation.research import InitializationInsufficient, ResearchRun, sphere_surface_samples
from yield_estimation.evaluation.run import IndependentProcess, normalize_config
from yield_estimation.evaluation.mnis_run import weight_summary
from yield_estimation.model_lib.ACS import ACS
from yield_estimation.model_lib._recorded_algorithm import AlgorithmBudgetReached

ROOT = Path(__file__).resolve().parents[1]


def configured(method='sphere_surface', budget=132):
    proposal = json.loads((ROOT / 'docs/research/acs_initialization/sram_plan.json').read_text())
    search = dict(method=method, max_simulations=64, min_failed_candidates=4, on_insufficient='stop')
    search.update(radii=[1., 2., 3., 4.]) if method == 'sphere_surface' else search.update(radius_sigma=4.)
    profile = dict(id=method, algorithm='acs', mode='variant', initialization=search,
                   initial_batch=16, initial_failures=4, estimation_batch=8,
                   initial_pool=dict(count=4, rule='discovery_order'),
                   proposal=proposal['formal_proposal'])
    return dict(schema_version=1, experiment=proposal['target'], seeds=[7], estimators=[profile],
                budget_unit='xyce_samples', budget_per_algorithm=budget, max_algorithm_draws=budget)


class SphereConnection(unittest.TestCase):
    def test_public_and_flat_validation_reject_invalid_schedules(self):
        for radii in ([], [0.], [-1.], [2., 1.], [1., 1.], [True], [float('nan')], [float('inf')]):
            raw = configured()
            raw['estimators'][0]['initialization']['radii'] = radii
            with self.subTest(radii=radii), self.assertRaises(ValueError):
                normalize_experiment(raw)
        raw = configured()
        raw['estimators'][0]['initialization']['max_simulations'] = 63
        with self.assertRaisesRegex(ValueError, 'radius count'):
            normalize_experiment(raw)
        raw = configured()
        raw['estimators'][0]['algorithm'] = 'ais'
        with self.assertRaisesRegex(ValueError, 'only for ACS'):
            normalize_experiment(raw)
        raw = configured()
        raw['experiment']['relative_std'] = .25
        with self.assertRaisesRegex(ValueError, 'positive u0'):
            normalize_experiment(raw)
        flat = next(child_configs(normalize_experiment(configured())))[2]
        flat['relative_std'] = .25
        with self.assertRaisesRegex(ValueError, 'positive u0'):
            normalize_config(flat)

    def target(self):
        cell = [.4106, .045, -.13] * 3 + [-.3842, .02, -.126, .4106, .045, -.13, -.3842, .02, -.126]
        return IndependentProcess(np.array(cell), .05, 7)

    def test_18d_affine_surface_and_pre_measurement_support_guard(self):
        target = self.target()
        x = sphere_surface_samples(target, 4., 16, np.random.default_rng(7))
        np.testing.assert_allclose(np.linalg.norm((x - target.means) / target.sigma, axis=1), 4., atol=1e-14)
        self.assertTrue((x[:, 1::3] >= .8 * target.means[1::3]).all())
        self.assertTrue(target.in_support(x).all())
        with self.assertRaisesRegex(ValueError, 'positive u0'):
            sphere_surface_samples(target, 20., 16, np.random.default_rng(7))

    def execute(self, root, budget, *, always_pass=False, method='sphere_surface'):
        raw = configured('uniform_box' if method == 'legacy' else method, budget)
        if method == 'legacy':
            raw['estimators'] = [dict(id='legacy', algorithm='acs', mode='legacy',
                                      initial_batch=16, initial_failures=4, estimation_batch=8)]
        resolved = normalize_experiment(raw)
        profile, seed, config = next(child_configs(resolved))
        target = self.target()
        rule = MeasurementRule('hold_snm', config)
        summary = {key: {} for key in ('resolved_circuit', 'model_sha256', 'driver_sizes', 'timing',
                                       'interconnect', 'equivalent', 'bitcell_parameter_order')}
        class Bench:
            submitted = returned = 0
            def run_mc_simulation(self, *, mc_runs, vars, **kwargs):
                self.submitted += mc_runs
                self.returned += mc_runs
                radius = np.linalg.norm((vars - target.means) / target.sigma, axis=1)
                values = np.full(mc_runs, .4) if always_pass else np.where(radius > 1.5, .3, .4)
                return values, None, None, None
        bench = Bench()
        algorithm = ACS(bench, 18, 1, False, target, profile.get("proposal", {}).get("legacy_variance", .00001), 4, 16, 8, 10, seed,
                        initialization_bounds=np.array([target.means + 4*target.sigma, target.means - 4*target.sigma]))
        algorithm.operation = 'hold_snm'
        algorithm.indicator = rule.failures
        research = ResearchRun(config, root, target, summary, rule)
        research.attach(algorithm, bench)
        error = None
        with patch('subprocess.Popen', side_effect=AssertionError('No solver or subprocess allowed')), \
             patch.object(algorithm, '_save_result'), redirect_stdout(io.StringIO()):
            try:
                algorithm.start_estimate(budget, strict_budget=True)
            except (AlgorithmBudgetReached, InitializationInsufficient) as found:
                error = found
        return algorithm, research, bench, error

    def test_real_acs_phases_completed_denominator_and_next_batch_rejection(self):
        for budget in (68, 69):
            with self.subTest(budget=budget), TemporaryDirectory() as directory:
                algorithm, research, bench, error = self.execute(Path(directory), budget)
                self.assertIsInstance(error, AlgorithmBudgetReached)
                self.assertEqual(algorithm.phase_samples, dict(initialization=32, recheck=4, estimation=32))
                self.assertEqual((algorithm.submitted_samples, bench.submitted, bench.returned), (68, 68, 68))
                initial = research.initialization
                self.assertEqual(initial['selection']['selected_ids'], ['sample-17', 'sample-18', 'sample-19', 'sample-20'])
                np.testing.assert_allclose([r['standardized_radius'] for r in initial['candidates']], [1.]*16 + [2.]*16, atol=1e-14)
                self.assertEqual(initial['sphere_layers'], [dict(radius=1., generated=16), dict(radius=2., generated=16)])
                self.assertEqual(initial['stop_reason'], 'required_candidates_found')
                stats = weight_summary(algorithm.estimation_records)
                self.assertEqual(stats['estimation_draws_evaluated'], 32)
                self.assertEqual(len(algorithm.estimation_records), 4)
                for record in algorithm.estimation_records:
                    snapshot = json.loads(record['proposal_json'])
                    np.testing.assert_array_equal(snapshot['variances'],
                        np.full(np.array(snapshot['variances']).shape, float(np.float32(.00001))))
                ledger = research.ledger(algorithm)['phases']
                self.assertEqual(ledger['recheck']['xyce_submitted'], 4)
                self.assertEqual(ledger['estimation']['xyce_returned'], 32)
                print(f'ACS shared sphere: budget={budget}; submitted/returned=68/68; phases=32+4+32; formal=32')

    def test_radius_exhaustion_keeps_all_rows_and_no_estimate(self):
        with TemporaryDirectory() as directory:
            algorithm, research, bench, error = self.execute(Path(directory), 132, always_pass=True)
            self.assertIsInstance(error, InitializationInsufficient)
            self.assertEqual((bench.submitted, bench.returned), (64, 64))
            self.assertEqual(research.initialization['status'], 'insufficient')
            self.assertEqual(research.initialization['stop_reason'], 'radius_schedule_exhausted')
            self.assertEqual(len(research.initialization['sphere_layers']), 4)
            self.assertEqual(len(research.initialization['candidates']), 64)
            self.assertIsNone(weight_summary(algorithm.estimation_records)['log_domain_pfail'])

    def test_legacy_initialization_without_strategy_block_remains_supported(self):
        with TemporaryDirectory() as directory:
            algorithm, research, bench, error = self.execute(Path(directory), 132, method='legacy')
            self.assertIsInstance(error, AlgorithmBudgetReached)
            self.assertEqual(research.initialization['method'], 'legacy')
            self.assertEqual(research.initialization['status'], 'selected')
            self.assertEqual(algorithm.phase_samples['initialization'], 16)
            self.assertTrue(all('standardized_radius' not in row for row in research.initialization['candidates']))

    def test_existing_box_retains_full_radial_information(self):
        with TemporaryDirectory() as directory:
            algorithm, research, bench, error = self.execute(Path(directory), 132, method='uniform_box')
            points = np.array([r['parameters'] for r in research.initialization['candidates']])
            target = self.target()
            z = (points-target.means)/target.sigma
            expected = np.random.default_rng(7).uniform(target.means-4*target.sigma, target.means+4*target.sigma, size=points.shape)
            np.testing.assert_array_equal(points, expected)
            self.assertTrue((np.abs(z) <= 4 + 1e-14).all())
            self.assertGreater(np.linalg.norm(z, axis=1).max(), 4.)
            self.assertEqual(research.initialization['sphere_layers'], [])


if __name__ == '__main__':
    unittest.main()
