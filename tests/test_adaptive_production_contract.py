"""CPU contracts for the real AIS and mixed-search HSCS evaluation runners."""
from contextlib import redirect_stdout
import csv
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

from yield_estimation.evaluation import adaptive_run
from yield_estimation.evaluation.experiment import child_configs, normalize_experiment


def independent_process_log_density(points, means, sigma):
    terms = norm.logpdf(points, loc=means, scale=sigma)
    mu = means[1::3]
    sd = sigma[1::3]
    terms[:, 1::3] = truncnorm.logpdf(points[:, 1::3], -mu / sd, np.inf, loc=mu, scale=sd)
    return terms.sum(axis=1)


def independent_proposal_log_density(points, proposal, log_target):
    base = proposal['proposal']
    terms = norm.logpdf(points[:, None, :], loc=base['means'], scale=np.sqrt(base['variances']))
    log_base = logsumexp(terms.sum(axis=2) + np.log(base['weights']), axis=1)
    fraction = proposal['target_fraction']
    return np.logaddexp(math.log1p(-fraction) + log_base, math.log(fraction) + log_target)


class AdaptiveProductionContracts(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def run_case(self, label, algorithm, budget, *, unit='algorithm_draws', max_draws=None):
        # Reuse an existing fixture seed. Measurement functions must give two distinct
        # outcomes for read vs auxiliary write without using the current phase or the budget.
        seed = 9101
        settings = dict(case='sram_6t_hold_snm', corner='TT', vdd=1., temperature_c=27.,
                        relative_std=.05, u0_positive=True, batch_size=2, threshold_mv=340.)
        if algorithm == 'hscs':
            settings.pop('threshold_mv')
            settings.update(case='sram_6t_read', threshold_ns=.7,
                            circuit={'array': [2, 2, False], 'target': [0, 0]})
        batch = 16 if algorithm == 'ais' else 2
        profile = dict(id=algorithm, algorithm=algorithm, mode='variant', initial_batch=2,
                       estimation_batch=batch,
                       initialization=dict(method='target', min_failed_candidates=1, max_simulations=2),
                       initial_pool=dict(count=1, rule='discovery_order'),
                       proposal=dict(covariance='target_diagonal', std_scale=.8, target_fraction=.25))
        if algorithm == 'hscs':
            profile.update(allow_hscs_mixed_search=True, hscs_search_origin='nominal')
        envelope = normalize_experiment(dict(schema_version=1, experiment=settings, budget_per_algorithm=budget,
            budget_unit=unit, max_algorithm_draws=budget if max_draws is None else max_draws,
            seeds=[seed], estimators=[profile]))
        (_, _, config), = child_configs(envelope)
        calls = []
        build_case = adaptive_run.build_evaluation_case

        def replace_measurements(*args):
            means, target, bench = build_case(*args)

            def measure(*, operation, target_row, target_col, mc_runs, vars, **kwargs):
                offset = np.tanh((vars[:, 0] - means[0]) / target.sigma[0])
                values = {'hold_snm': .31 + .01 * offset,
                          'read': (.9 + .05 * offset) * 1e-9,
                          'write': (.4 + .05 * offset) * 1e-9}[operation]
                calls.append(dict(operation=operation, target=(target_row, target_col),
                                  submitted=mc_runs, returned=len(values), samples=vars.copy(),
                                  values=values.copy(), batch=Path(bench.sim_path).name))
                return values if operation == 'hold_snm' else (values, None, None, None)

            bench.run_mc_simulation = measure
            return means, target, bench

        output = self.directory / label
        console = io.StringIO()
        # Satisfy the runner's availability guard and replace only the backend
        # measurement call. Testbench construction and both recorders stay real.
        with patch.object(adaptive_run, 'shutil', SimpleNamespace(which=lambda _: 'synthetic-measurements')), \
                patch.object(adaptive_run, 'build_evaluation_case', side_effect=replace_measurements), \
                redirect_stdout(console):
            code = adaptive_run.run(config, output, initial_batch=2, initial_failures=1,
                                    estimation_batch=batch, algorithm_name=algorithm,
                                    allow_hscs_mixed_search=(algorithm == 'hscs'))
        summary = json.loads((output / 'run_summary.json').read_text())
        self.assertEqual(code, 0, (summary['status'], summary['detail'], console.getvalue()[-4000:]))
        return output, summary, calls

    def verify_records(self, output, summary, calls, phases, *, rejected_estimation=0):
        charged = summary['algorithm_requested_samples']
        self.assertEqual(summary['simulator_submitted_samples'], sum(call['submitted'] for call in calls))
        self.assertEqual(summary['simulator_returned_samples'], sum(call['returned'] for call in calls))
        self.assertEqual(summary['unclassified_samples'], 0)
        request_paths = sorted(output.glob('request_*.npz'))
        self.assertEqual(len(request_paths), len(calls) + bool(rejected_estimation))
        self.assertEqual(len(calls), len(phases))
        request_rows = 0
        for path, call, phase in zip(request_paths, calls, phases):
            with np.load(path, allow_pickle=False) as record:
                request_rows += len(record['samples'])
                self.assertEqual(str(record['status']), 'returned')
                self.assertEqual(str(record['phase']), phase)
                self.assertEqual(str(record['operation']), call['operation'])
                self.assertEqual((int(record['target_row']), int(record['target_col'])), call['target'])
                np.testing.assert_array_equal(record['samples'], call['samples'])
                np.testing.assert_array_equal(record['delays'], call['values'])
        if rejected_estimation:
            with np.load(request_paths[-1], allow_pickle=False) as blocked:
                self.assertEqual(str(blocked['phase']), 'estimation')
                self.assertEqual(str(blocked['status']), 'unresolved')
                self.assertEqual(len(blocked['samples']), rejected_estimation)
                self.assertNotIn('delays', blocked)
        self.assertEqual(charged, request_rows + rejected_estimation)
        ledger_by_phase = summary['research_cost']['phases']
        self.assertEqual(set(ledger_by_phase), set(phases))
        self.assertEqual(summary['phase_samples'], {phase: counts['algorithm_charged_draws']
                                                   for phase, counts in ledger_by_phase.items()})
        for phase, ledger in ledger_by_phase.items():
            actual = [call for call, recorded_phase in zip(calls, phases) if phase == recorded_phase]
            extra = rejected_estimation if phase == 'estimation' else 0
            self.assertEqual(ledger, dict(algorithm_charged_draws=sum(c['submitted'] for c in actual) + extra,
                                          xyce_submitted=sum(c['submitted'] for c in actual),
                                          xyce_returned=sum(c['returned'] for c in actual)))

    def verify_estimates(self, output, summary, formal_calls, *, rejected_estimation=0):
        paths = sorted(output.glob('estimation_*.npz'))
        self.assertEqual(len(paths), len(formal_calls) + bool(rejected_estimation))
        means = np.array(summary['nominal_parameters'])
        sigma = np.abs(means) * summary['relative_std']
        proposals = []
        contributions = []
        for path, call in zip(paths, formal_calls):
            with np.load(path, allow_pickle=False) as record:
                self.assertEqual(str(record['status']), 'evaluated')
                np.testing.assert_array_equal(record['samples'], call['samples'])
                np.testing.assert_array_equal(record['delays'], call['values'])
                self.assertTrue(record['support'].all())
                failed = call['values'] < .340 if call['operation'] == 'hold_snm' else call['values'] > .7e-9
                np.testing.assert_array_equal(record['indicator'], failed)
                proposal = json.loads(str(record['proposal_json']))
                self.assertEqual(proposal['kind'], 'defensive_mixture')
                self.assertEqual(proposal['target_fraction'], .25)
                self.assertEqual(proposal['proposal']['kind'], 'diagonal_gaussian_mixture')
                variances = np.array(proposal['proposal']['variances'])
                expected = np.broadcast_to((sigma * .8) ** 2, variances.shape)
                np.testing.assert_allclose(variances, expected, rtol=1e-14, atol=0.)
                log_f = independent_process_log_density(call['samples'], means, sigma)
                log_g = independent_proposal_log_density(call['samples'], proposal, log_f)
                # Float64 densities, allowing accumulated rounding over up to 72 coordinates.
                np.testing.assert_allclose(record['log_f'], log_f, atol=2e-10, rtol=0.)
                np.testing.assert_allclose(record['log_g'], log_g, atol=2e-10, rtol=0.)
                contributions.extend(failed * np.exp(log_f - log_g))
                proposals.append(proposal)
        if rejected_estimation:
            with np.load(paths[-1], allow_pickle=False) as rejected:
                self.assertEqual(str(rejected['status']), 'unresolved')
                self.assertEqual(len(rejected['samples']), rejected_estimation)
                self.assertNotIn('indicator', rejected)
        count = sum(call['returned'] for call in formal_calls)
        self.assertEqual(summary['diagnostics']['estimation_draws_evaluated'], count)
        if count:
            self.assertAlmostEqual(summary['pfail_estimate'], float(np.mean(contributions)), places=11)
        else:
            self.assertIsNone(summary['pfail_estimate'])
        return proposals

    def test_ais_updates_proposal_and_only_counts_completed_formal_samples(self):
        for budget in (35, 36):
            with self.subTest(budget=budget):
                output, summary, calls = self.run_case(f'ais-{budget}', 'ais', budget)
                phases = ['initialization', 'recheck', 'estimation', 'estimation']
                self.assertEqual(summary['status'], 'budget_stopped')
                self.assertIn(f'Used 35/{budget}; next estimation batch needs 16', summary['detail'])
                self.assertEqual([call['submitted'] for call in calls], [2, 1, 16, 16])
                self.verify_records(output, summary, calls, phases)
                old, new = self.verify_estimates(output, summary, calls[2:])
                np.testing.assert_array_equal(old['proposal']['means'], calls[1]['samples'])
                self.assertNotEqual(old['proposal']['means'], new['proposal']['means'])
                # The former center competes for the new slot under q_old. Its
                # score is used only for selection, whereas verify_estimates sums
                # the actual 32 new samples from their respective generating q's.
                pool = np.vstack((calls[1]['samples'], calls[2]['samples']))
                means = np.array(summary['nominal_parameters'])
                log_f = independent_process_log_density(pool, means, np.abs(means) * .05)
                log_g = independent_proposal_log_density(pool, old, log_f)
                index = int(np.argmax(log_f - log_g))
                self.assertGreater(index, 0)
                np.testing.assert_array_equal(new['proposal']['means'], pool[index:index+1])
                print(f'AIS budget={budget} requests/submitted/returned=35/35/35 formal=32 proposal_changed=True')

    def test_hscs_real_mixture_and_mixed_read_write_budget(self):
        # This fixture selects one initial point, so it searches one cluster.
        # Each bisection halves its interval until it is below 5% of the start.
        search_calls = math.ceil(math.log2(1. / .05))
        complete_cost = 2 + 1 + search_calls + 2 * 2
        phases = ['initialization', 'recheck'] + ['write_search'] * search_calls + ['estimation'] * 2
        for budget, unit, max_draws, rejected in (
                (complete_cost, 'algorithm_draws', None, 0),
                (complete_cost + 1, 'algorithm_draws', None, 0),
                (complete_cost + 1, 'xyce_samples', complete_cost + 2, 2)):
            with self.subTest(budget=budget, unit=unit):
                output, summary, calls = self.run_case(f'hscs-{budget}-{unit}', 'hscs', budget,
                                                     unit=unit, max_draws=max_draws)
                self.assertEqual(summary['status'], 'budget_stopped')
                expected = (f'Xyce cap: {complete_cost}/{budget}; next estimation batch needs 2' if rejected
                            else f'Used {complete_cost}/{budget}; next estimation batch needs 2')
                self.assertIn(expected, summary['detail'])
                self.assertEqual([c['operation'] for c in calls], ['read', 'read'] + ['write'] * search_calls + ['read'] * 2)
                self.assertTrue(all(c['target'] == ((1, 1) if c['operation'] == 'write' else (0, 0)) for c in calls))
                self.verify_records(output, summary, calls, phases, rejected_estimation=rejected)
                first, second = self.verify_estimates(output, summary, calls[-2:], rejected_estimation=rejected)
                self.assertEqual(first, second)
                self.assertEqual(first['proposal']['weights'], [.1, .9])
                np.testing.assert_array_equal(first['proposal']['means'][0], summary['nominal_parameters'])
                self.assertNotEqual(first['proposal']['means'][0], first['proposal']['means'][1])
                self.assertEqual((summary['auxiliary_write_submitted'], summary['auxiliary_write_returned']),
                                 (search_calls, search_calls))
                self.assertEqual(summary['timing_failures'], 2 + 1 + 4)
                with (output / 'auxiliary_write_samples.csv').open(newline='') as file:
                    aux = list(csv.DictReader(file))
                self.assertEqual([row['batch'] for row in aux], [c['batch'] for c in calls if c['operation'] == 'write'])
                self.assertTrue(all(row['search_condition'] == 'within_search_threshold' for row in aux))
                print(f'HSCS budget={budget} unit={unit} requests={complete_cost+rejected} '
                      f'submitted={complete_cost} returned={complete_cost} writes={search_calls} formal=4')

    def test_hscs_budget_ends_during_search_without_formal_estimate(self):
        output, summary, calls = self.run_case('hscs-search-stop', 'hscs', 4)
        self.assertEqual(summary['status'], 'no_estimation_samples')
        self.assertIn('Used 4/4; next write_search batch needs 1', summary['detail'])
        self.verify_records(output, summary, calls, ['initialization', 'recheck', 'write_search'])
        self.verify_estimates(output, summary, [])
        print('HSCS stopped in auxiliary search: requests/submitted/returned=4/4/4 formal=0 estimate=None')


if __name__ == '__main__':
    unittest.main()
