"""Offline analysis contracts; run reports are synthetic and no simulator is used."""
from copy import deepcopy
import csv
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from yield_estimation.evaluation.analyze_results import analyze


class AnalysisContract(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def summary(self, seed):
        return dict(case='sram_6t_hold_snm', operation='hold_snm', target=[0, 0],
            resolved_circuit={'array': [1, 1, False], 'vdd': 1.0, 'corner': 'TT', 'target': [0, 0]},
            relative_std=.05, u0_positive=True, sampling_source='numpy_independent_positive_u0',
            model_sha256='fixture-model', nominal_parameters_per_cell=[.4, .04, -.13],
            bitcell_parameter_order=['PGL.vth0', 'PGL.u0', 'PGL.voff'], driver_sizes={'driver': 'fixed'},
            timing={'mode': 'fixed'}, interconnect={'wire': 'fixed'}, equivalent={'mode': 0},
            measurement='hold_snm', failure_operator='<', outcome_rule_version='snm_outcomes_v1',
            threshold_mv=327.65, threshold_field='threshold_mv',
            git_revision='fixture-revision', git_tracked_files_modified=False,
            xyce='/fake/path/Xyce', xyce_version='fixture-version', seed=seed, status='budget_stopped',
            research_cost={'budget_unit': 'xyce_samples', 'algorithm_draw_cap': 24, 'xyce_cap': 20},
            budget=20, algorithm='repository_MNIS_configured_proposal', initialization_sigma=3.,
            algorithm_requested_samples=20, simulator_submitted_samples=20, simulator_returned_samples=20,
            unclassified_samples=0, diagnostics={'numeric_status': 'representable', 'estimation_draws_evaluated': 16},
            pfail_estimate=.08)

    def save(self, name, summary, estimator='mnis', *, sha256=None):
        folder = self.root / name
        folder.mkdir()
        (folder / 'run_summary.json').write_text(json.dumps(summary), encoding='utf-8')
        row = dict(run=name, seed=summary['seed'], estimator=estimator)
        if sha256:
            row['sha256'] = sha256
        return row

    def reference(self, seed=101, name='reference'):
        summary = self.summary(seed)
        summary.update(algorithm='repository_MC', status='complete', budget=100, failure_count=5,
                       requested_samples=100, returned_rows=100, pfail_estimate=.05)
        for key in ('algorithm_requested_samples', 'simulator_submitted_samples', 'simulator_returned_samples'):
            summary.pop(key)
        return self.save(name, summary, estimator='mc')['run']

    def run_manifest(self, runs, references=()):
        group = dict(id='demo', runs=runs)
        if references:
            group['reference_runs'] = list(references)
        path = self.root / 'manifest.json'
        path.write_text(json.dumps(dict(schema_version=1, groups=[group])), encoding='utf-8')
        with redirect_stdout(io.StringIO()):
            return analyze(path, self.root / 'analysis')['groups'][0]

    def test_warning_results_stay_in_error_and_no_estimates_keep_denominator(self):
        low = self.summary(1)
        low.update(pfail_estimate=1e-8, git_tracked_files_modified=True,
                   xyce_version=None, sampling_diagnostics={'warnings': ['low_observed_weight_mass']})
        above = dict(self.summary(2), pfail_estimate=1.2)
        shortage = dict(self.summary(3), status='initialization_insufficient', pfail_estimate=None,
                        algorithm_requested_samples=4, simulator_submitted_samples=4, simulator_returned_samples=4,
                        diagnostics={'numeric_status': 'no_estimation', 'estimation_draws_evaluated': 0})
        log_only = dict(self.summary(4), pfail_estimate=None, log10_pfail=-400.,
                        diagnostics={'numeric_status': 'underflow', 'estimation_draws_evaluated': 16})
        group = self.run_manifest([self.save('low', low), self.save('above', above), self.save('shortage', shortage),
                                   self.save('underflow', log_only)], [self.reference()])
        self.assertEqual(group['checks']['target_same'], 4)
        self.assertEqual(group['checks']['budget_protocol_check'], 'same')
        self.assertEqual(group['checks']['xyce_submitted_check'], 'different')
        self.assertEqual(group['checks']['source_incomplete'], 1)
        self.assertFalse(group['checks']['ranking_eligible'])
        rows = group['runs']
        self.assertEqual(rows[0]['pfail_estimate'], 1e-8)
        self.assertEqual(rows[1]['pfail_estimate'], 1.2)
        self.assertIn('estimate_above_one', rows[1]['warnings'])
        self.assertIsNone(rows[2]['pfail_estimate'])
        self.assertEqual(rows[3]['estimate_status'], 'log_only')
        self.assertEqual(rows[3]['log10_pfail'], -400.)
        self.assertIsNone(rows[3]['absolute_error'])
        method, = group['methods']
        self.assertEqual(method['total_runs'], 4)
        self.assertEqual(method['no_estimate_fraction'], .25)
        self.assertEqual(method['no_estimate_reasons'], {'initialization_insufficient': 1})
        self.assertEqual(method['error_runs'], 2)
        self.assertEqual(method['errors_with_unverified_or_different_source'], 1)
        self.assertAlmostEqual(method['mean_absolute_error'], ((.05 - 1e-8) + (1.2 - .05)) / 2)
        self.assertAlmostEqual(method['seed_std'], (1.2 - 1e-8) / 2**.5)
        ref = group['reference']
        self.assertEqual((ref['failure_count'], ref['samples']), (5, 100))
        self.assertEqual(ref['estimate'], .05)
        self.assertLess(ref['interval'][0], .05)
        self.assertGreater(ref['interval'][1], .05)
        with (self.root / 'analysis' / 'runs.csv').open(newline='', encoding='utf-8') as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), 4)

    def test_target_strategy_cost_and_source_are_independent_checks(self):
        first = self.summary(1)
        other_strategy = dict(self.summary(2), algorithm='repository_AIS_configured_proposal',
                              initialization_sigma=1.5, hscs_search_origin='nominal', budget=21,
                              git_revision='another-revision', time_limit_s=50.)
        other_target = deepcopy(self.summary(3))
        other_target['threshold_mv'] = 340.
        other_target['resolved_circuit']['corner'] = 'SS'
        no_summary = dict(run='missing', seed=4, estimator='mnis')
        group = self.run_manifest([self.save('a', first), self.save('b', other_strategy, 'ais'),
            self.save('c', other_target), no_summary], [self.reference()])
        checks = group['checks']
        self.assertEqual((checks['target_same'], checks['target_different'], checks['target_unknown']), (2, 1, 1))
        self.assertEqual(checks['xyce_submitted_check'], 'same')
        self.assertEqual(checks['budget_protocol_check'], 'different')
        self.assertEqual(checks['source_revision_check'], 'different')
        self.assertEqual(checks['time_limit_check'], 'unknown')
        self.assertEqual(group['runs'][1]['target_differences'], [])
        self.assertEqual(group['runs'][1]['strategy']['initialization_sigma'], 1.5)
        self.assertEqual(group['runs'][1]['reference_source_match'], 'different')
        self.assertEqual(set(group['runs'][2]['target_differences']), {'threshold', 'resolved_circuit'})
        self.assertIsNone(group['runs'][2]['absolute_error'])
        self.assertEqual(group['methods'][0]['no_estimate_reasons'], {'missing_summary': 1})

    def test_reference_rejects_reused_seed(self):
        group = self.run_manifest([self.save('trial', self.summary(1))], [self.reference(seed=1)])
        self.assertEqual(group['reference']['status'], 'invalid')
        self.assertIn('reference_not_seed_disjoint_or_duplicate', group['reference']['issues'])
        self.assertIsNone(group['methods'][0]['rmse'])

    def test_hash_mismatch_or_missing_target_remain_visible(self):
        bad_hash = self.save('bad-hash', self.summary(1), sha256='0' * 64)
        unknown_target = self.summary(2)
        del unknown_target['bitcell_parameter_order']
        group = self.run_manifest([bad_hash, self.save('unknown', unknown_target)])
        self.assertEqual(group['checks']['target_unknown'], 2)
        self.assertIn('summary_sha256_mismatch', group['runs'][0]['source']['issues'])
        self.assertEqual(group['runs'][0]['pfail_estimate'], .08)
        self.assertFalse(group['runs'][0]['estimate_available'])
        self.assertIn('bitcell_parameter_order', group['runs'][1]['missing_target_fields'])
        self.assertEqual(group['methods'][0]['linear_metrics_runs'], 0)


if __name__ == '__main__':
    unittest.main()
