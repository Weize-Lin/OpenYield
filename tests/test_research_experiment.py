from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import numpy as np
from scipy.stats import multivariate_normal

from yield_estimation.evaluation.experiment import normalize_experiment, result_row
from yield_estimation.evaluation.outcomes import MeasurementRule
from yield_estimation.evaluation.research import InitializationInsufficient, ResearchRun, SimulationBudgetReached, select_candidates
from yield_estimation.tool.Distribution.gmm_v2 import mixture_gaussian


def experiment():
    return dict(schema_version=1, budget_per_algorithm=16, seeds=[42], experiment=dict(
        case="sram_6t_hold_snm", corner="TT", vdd=1., temperature_c=27.,
        relative_std=.05, u0_positive=True, batch_size=4, threshold_mv=340.),
        estimators=[dict(id="mc", algorithm="mc"), dict(id="mnis", algorithm="mnis", mode="variant",
            initialization=dict(method="target", min_failed_candidates=2, max_simulations=6))])


class ExperimentContract(unittest.TestCase):
    def test_conflicting_corners_rejected_before_any_runner(self):
        raw = experiment()
        raw["experiment"]["circuit"] = {"corner": "FF"}
        with self.assertRaisesRegex(ValueError, r"corner.*circuit.corner"):
            normalize_experiment(raw)
        from yield_estimation.evaluation.run import normalize_config
        flat = dict(raw["experiment"], seed=0, budget=16)
        with self.assertRaisesRegex(ValueError, r"corner.*circuit.corner"):
            normalize_config(flat)

    def test_single_corner_reaches_frozen_target_and_compiler(self):
        from yield_estimation.evaluation.run import configure_circuit
        from yield_estimation.evaluation.analysis_plan import analysis_manifest, load_planned_trials
        for inner_only in (False, True):
            raw = experiment()
            raw["experiment"]["corner"] = "FF"
            if inner_only:
                del raw["experiment"]["corner"]
                raw["experiment"]["circuit"] = {"corner": "FF"}
            resolved = normalize_experiment(raw)
            self.assertEqual(resolved["experiment"]["corner"], "FF")
            circuit = resolved["experiment"]["circuit"]
            self.assertEqual(circuit["corner"], "FF")
            self.assertEqual(configure_circuit(circuit).global_config.corner, "FF")
            with TemporaryDirectory() as directory:
                path = Path(directory) / "experiment.json"
                path.write_text(json.dumps(resolved))
                manifest = analysis_manifest(resolved, path, [])
                targets = load_planned_trials(manifest, path.parent)
                self.assertTrue(all(item["target"]["resolved_circuit"]["corner"] == "FF"
                                    for item in targets.values()))

    def test_dict_json_roundtrip(self):
        raw = experiment()
        with TemporaryDirectory() as folder:
            path = Path(folder) / "experiment.json"
            path.write_text(json.dumps(raw))
            self.assertEqual(normalize_experiment(path), normalize_experiment(raw))
            self.assertEqual(normalize_experiment(normalize_experiment(raw)), normalize_experiment(raw))

    def test_rejects_duplicate_id_nonreference_and_implicit_budget_unit(self):
        raw = experiment()
        raw['estimators'].append(dict(raw['estimators'][1], id='MNIS'))
        with self.assertRaisesRegex(ValueError, 'unique'):
            normalize_experiment(raw)
        raw = experiment()
        raw['estimators'][1] = dict(id='mnis', algorithm='mnis', mode='reference')
        with self.assertRaisesRegex(ValueError, 'reference'):
            normalize_experiment(raw)
        raw = experiment()
        raw['budget_unit'] = 'xyce_samples'
        with self.assertRaisesRegex(ValueError, 'max_algorithm_draws'):
            normalize_experiment(raw)

    def test_fails_instead_of_applying_mnis_center_option_to_acs(self):
        raw = experiment()
        raw['estimators'][1].update(algorithm='acs', center_selection={'rule': 'nearest_raw'})
        with self.assertRaisesRegex(ValueError, 'initial_pool'):
            normalize_experiment(raw)

    def test_raw_and_standardized_can_choose_different_rows(self):
        target = SimpleNamespace(means=np.zeros(2), sigma=np.array([20., .1]))
        rows = [{"parameters": [2., 0.]}, {"parameters": [0., 1.]}]
        self.assertEqual(select_candidates(rows, 'nearest_raw', target)[0][0], 1)
        self.assertEqual(select_candidates(rows, 'nearest_standardized', target)[0][0], 0)

    def test_table_keeps_out_of_range_estimates_and_warns(self):
        row = result_row({'id': 'raw', 'mode': 'variant'}, 7, Path('raw'), 'mnis', 0,
                         {'status': 'budget_stopped', 'pfail_estimate': 1.2,
                          'diagnostics': {'estimation_draws_evaluated': 4}})
        self.assertEqual(row['estimated_pfail'], 1.2)
        self.assertEqual(row['estimation_draws'], 4)
        self.assertIn('estimate_above_one', row['warnings'])

    def test_vector_covariance_matches_gaussian_density(self):
        mu = np.array([.4, .045, -.13])
        variances = np.array([4e-4, 4e-6, 4e-5])
        proposal = mixture_gaussian(np.array([1.]), mu[None, :], variances)
        x = np.array([mu, mu + [.01, .001, -.005]])
        np.testing.assert_allclose(proposal.log_pdf(x).ravel(), multivariate_normal(mu, np.diag(variances)).logpdf(x))


class CandidateContract(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.target = SimpleNamespace(means=np.array([.4, .04, -.13]), sigma=np.array([.02, .002, .006]),
            sample=lambda n: np.tile([.4, .04, -.13], (n, 1)),
            in_support=lambda rows: np.isfinite(rows).all(axis=1) & (rows[:, 1] > 0))
        self.base = experiment()['experiment']
        self.summary = {key: {} for key in ('resolved_circuit', 'model_sha256', 'driver_sizes',
                                           'timing', 'interconnect', 'equivalent', 'bitcell_parameter_order')}

    def create_run(self, settings, threshold=340.):
        rule = MeasurementRule('hold_snm', {'threshold_mv': threshold})
        profile = dict(id='mnis', algorithm='mnis', mode='variant', initialization=settings,
                       center_selection={'rule': 'discovery_order'})
        run = ResearchRun(dict(self.base, seed=0, budget=2, research=dict(
            estimator=profile, max_algorithm_draws=3, budget_unit='xyce_samples')),
            self.path, self.target, self.summary, rule)
        bench = SimpleNamespace(submitted=0, returned=0)

        def run_simulator(mc_runs, vars):
            bench.submitted += mc_runs
            bench.returned += mc_runs
            return np.array([.333] * mc_runs), None, None, None

        bench.run_mc_simulation = run_simulator
        algorithm = SimpleNamespace(submitted_samples=0, indicator=rule.failures)
        run.attach(algorithm, bench)

        def simulate(samples):
            algorithm.submitted_samples += len(samples)
            return run.simulate('initialization', mc_runs=len(samples), vars=samples)

        algorithm._simulate = simulate
        return run, algorithm

    def test_import_rejects_changed_threshold_and_rechecks_matching_row(self):
        import hashlib
        settings = dict(method='target', min_failed_candidates=1, max_simulations=1)
        run, alg = self.create_run(settings)
        run.acquire(alg, 1, 1, None)
        source = self.path / 'source.json'
        manifest = deepcopy(run.initialization)
        manifest['candidates'][0]['failed'] = False
        source.write_text(json.dumps(manifest))
        imported = dict(settings, method='from_file', path=str(source),
                        sha256=hashlib.sha256(source.read_bytes()).hexdigest())
        mismatch, alg = self.create_run(imported, threshold=330.)
        with self.assertRaisesRegex(ValueError, 'identity'):
            mismatch.acquire(alg, 1, 1, None)
        self.assertEqual(mismatch.recorder.submitted, 0)
        matching, alg = self.create_run(imported)
        matching.acquire(alg, 1, 1, None)
        self.assertEqual(matching.recorder.submitted, 1)
        self.assertTrue(matching.initialization['candidates'][0]['failed'])
        self.assertFalse(matching.initialization['source']['prior_cost_charged'])

    def test_shortage_keeps_factual_counts_and_physical_cap(self):
        settings = dict(method='target', min_failed_candidates=1, max_simulations=2)
        run, alg = self.create_run(settings, threshold=1.)
        with self.assertRaisesRegex(InitializationInsufficient, '0/1'):
            run.acquire(alg, 1, 1, None)
        self.assertEqual(run.initialization['status'], 'insufficient')
        self.assertEqual(run.ledger(alg)['phases']['initialization']['xyce_submitted'], 2)
        with self.assertRaisesRegex(SimulationBudgetReached, 'Xyce cap'):
            run.simulate('estimation', mc_runs=1, vars=self.target.sample(1))


if __name__ == '__main__':
    unittest.main()
