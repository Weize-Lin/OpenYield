"""Integration contracts: fake executor/clock, real shared analysis, no Xyce."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
sys.path.insert(0, str(server.ROOT))
from yield_estimation.evaluation import analyze_results
from yield_estimation.evaluation.analysis_plan import analysis_manifest
from yield_estimation.evaluation.suite_analysis import freeze_suite, load_suite


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.jobs = server.LocalJobs(self.root)
        self.addCleanup(self.jobs.close)

    def job(self):
        config = server.preview(server.read_config())['config']
        config['seeds'] = [1, 2]
        plans = [dict(index=i, label=f'circuit {i}', config=copy.deepcopy(config), references=[], tasks=2) for i in range(2)]
        folder = self.root/'job'
        folder.mkdir()
        manifest = freeze_suite(folder, plans)
        job = dict(id='job', directory=str(folder), suite=plans, manifest=str(manifest),
                   status='running', offline=False, time_limit_seconds=30)
        self.jobs.jobs['job'] = job
        self.jobs._save(job)
        return job

    def execute(self, job, fake, clock):
        with patch.object(self.jobs, '_run_child', side_effect=fake), patch.object(server.time, 'monotonic', side_effect=lambda: clock[0]):
            self.jobs._work(job, lambda: self.jobs._execute_suite(job))

    def test_deadline_remaining_and_full_replay(self):
        job, clock, limits = self.job(), [0.], []
        def fake(job, config, refs, limit, outdir):
            limits.append(limit)
            self.assertEqual(len(json.loads(Path(job['manifest']).read_text())['circuits']), 2)
            clock[0] += 20. if len(limits) == 1 else 8.
            outdir.mkdir()
            server.write_json(outdir/'experiment.json', config)
            server.write_json(outdir/'analysis_manifest.json', analysis_manifest(config, outdir/'experiment.json', refs))
            return 0
        self.execute(job, fake, clock)
        self.assertEqual(limits, [30., 10.])
        self.assertEqual(job['status'], 'complete')
        # No synthetic summary was produced; no zero estimates invented.
        rows = [r for g in job['analysis']['groups'] for r in g['runs']]
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(r['pfail_estimate'] is None for r in rows))
        replay = analyze_results.analyze(job['manifest'], self.root/'replay')
        self.assertEqual(replay, job['analysis'])
        offline = self.jobs.start({'manifest': job['manifest']}, offline=True)
        self.jobs.threads[offline['id']].join(10)
        self.assertEqual(self.jobs.snapshot(offline['id'])['status'], 'complete')
        self.assertEqual(self.jobs.snapshot(offline['id'])['analysis']['groups'], replay['groups'])

    def test_cancel_preserves_launched_and_unstarted(self):
        job, clock, calls = self.job(), [0.], []
        def fake(job, config, refs, limit, outdir):
            calls.append(outdir)
            self.jobs.cancel(job['id'])
            return -15
        self.execute(job, fake, clock)
        self.assertEqual(len(calls), 1)
        self.assertEqual(job['status'], 'cancelled')
        self.assertEqual([p['status'] for p in job['suite']], ['cancelled', 'not_started_after_cancelled'])
        self.assertEqual(sum(g['checks']['total_runs'] for g in job['analysis']['groups']), 4)
        self.assertTrue(all(m['no_estimate_fraction'] == 1 for g in job['analysis']['groups'] for m in g['methods']))

    def test_timeout_and_error_keep_all_tasks(self):
        for mode in ('timeout', 'error'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                previous = self.root
                self.root = Path(temp)
                self.jobs.allowed_roots.append(self.root)
                job, clock, calls = self.job(), [0.], []
                def fake(job, config, refs, limit, outdir):
                    calls.append(outdir)
                    if mode == 'error':
                        raise OSError('synthetic launch error')
                    return 124
                self.execute(job, fake, clock)
                self.assertEqual(len(calls), 1 if mode == 'timeout' else 2)
                self.assertEqual(job['status'], 'timeout' if mode == 'timeout' else 'incomplete')
                self.assertEqual(len(job['analysis']['groups']), 2)
                self.assertEqual(job['suite'][1]['status'], 'not_started_after_timeout' if mode == 'timeout' else 'error')
                self.root = previous

    def test_deadline_exhausted_between_circuits(self):
        job, clock, calls = self.job(), [0.], []
        def fake(job, config, refs, limit, outdir):
            calls.append(1)
            clock[0] = 31.
            outdir.mkdir()
            server.write_json(outdir/'experiment.json', config)
            server.write_json(outdir/'analysis_manifest.json', analysis_manifest(config, outdir/'experiment.json', refs))
            return 0
        self.execute(job, fake, clock)
        self.assertEqual(len(calls), 1)
        self.assertEqual(job['suite'][1]['status'], 'not_started_after_timeout')
        self.assertEqual(job['status'], 'timeout')

    def test_plan_hash_and_output_config_changes_rejected(self):
        job = self.job()
        manifest = Path(job['manifest'])
        plan = manifest.parent/'plans/0/experiment.json'
        plan.write_text(plan.read_text().replace('"TT"', '"SS"'))
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            load_suite(manifest)

    def test_actual_config_must_match_frozen_suite(self):
        job = self.job()
        out = Path(job['directory'])/'execution_0'
        out.mkdir()
        config = copy.deepcopy(job['suite'][0]['config'])
        config['experiment']['threshold_ns'] += .1
        server.write_json(out/'experiment.json', config)
        server.write_json(out/'analysis_manifest.json', analysis_manifest(config, out/'experiment.json', []))
        with self.assertRaisesRegex(ValueError, 'differs from frozen suite'):
            load_suite(job['manifest'])

    def test_suite_path_escape_rejected(self):
        job = self.job()
        data = server.read_json(job['manifest'])
        data['circuits'][0]['execution_directory'] = '/etc'
        server.write_json(job['manifest'], data)
        with self.assertRaisesRegex(ValueError, 'differs from frozen plan'):
            self.jobs._validate_manifest(job['manifest'])

    def test_runtime_revision_and_dirty_guards(self):
        server.upstream()
        real = server.source_snapshot()
        self.assertTrue(real['git_revision'])
        with patch.object(server, 'source_snapshot', return_value=dict(real, git_tracked_files_modified=False)), \
             patch.object(server, '_backend_revision', real['git_revision']):
            self.assertEqual(self.jobs._check_runnable()['git_revision'], real['git_revision'])
        with patch.object(server, 'source_snapshot', return_value=dict(real, git_tracked_files_modified=True)), \
             patch.object(server, '_backend_revision', real['git_revision']):
            with self.assertRaisesRegex(ValueError, '未提交'):
                self.jobs._check_runnable()
        with patch.object(server, '_backend_revision', 'different-revision'):
            with self.assertRaisesRegex(ValueError, '重启'):
                self.jobs._check_runnable()

    def test_finished_does_not_count_pending(self):
        job = self.job()
        out = Path(job['directory'])/'execution_0'
        out.mkdir()
        (out/'trials.csv').write_text('status\ncomplete\nnot_started\nrunning\nnot_started_after_interrupt\n')
        self.assertEqual(self.jobs._finished(job), 1)

    def test_backend_public_example_unchanged(self):
        script = server.ROOT/'docs/research/offline_result_example.py'
        before = hashlib.sha256(script.read_bytes()).hexdigest()
        subprocess.run([sys.executable, str(script), '--output-dir', str(self.root/'public-example')],
                       cwd=server.ROOT, check=True)
        self.assertEqual(before, hashlib.sha256(script.read_bytes()).hexdigest())


    def test_completed_bad_estimates_and_shortage_are_not_filtered(self):
        from fixtures import fixture
        from yield_estimation.evaluation.analysis_plan import load_planned_trials
        job = self.job()
        def fake(job, config, refs, limit, out):
            out.mkdir()
            server.write_json(out/'experiment.json', config)
            manifest = analysis_manifest(config, out/'experiment.json', [])
            server.write_json(out/'analysis_manifest.json', manifest)
            planned = load_planned_trials(manifest, out)
            template = server.read_json(self.root/'evidence-template/read/trial/mc/run_summary.json')
            for entry in manifest['groups'][0]['runs']:
                expectation = planned[(entry['estimator'], entry['seed'])]
                summary = dict(template, **expectation['target'], seed=entry['seed'],
                               research={'estimator': expectation['profile']}, requested_samples=4,
                               returned_rows=4, budget=4, diagnostics={'estimation_draws_evaluated': 4},
                               research_cost={'budget_unit': 'xyce_samples', 'algorithm_draw_cap': 4, 'xyce_cap': 4})
                summary['threshold_ns'] = expectation['target']['threshold']
                if out.name == 'execution_0':
                    summary['pfail_estimate'] = 1e-8 if entry['seed'] == 1 else 1.2
                    summary['sampling_diagnostics'] = {'warnings': ['low_observed_weight_mass']}
                else:
                    summary.update(status='initialization_insufficient', pfail_estimate=None)
                target = out/entry['run']
                target.mkdir(parents=True)
                server.write_json(target/'run_summary.json', summary)
            return 0
        # Reuse one synthetic template for both circuits, never a private output.
        from fixtures import fixture
        fixture(self.root/'evidence-template', 'read')
        self.execute(job, fake, [0.])
        self.assertEqual(job['status'], 'complete', job.get('error'))
        first, second = job['analysis']['groups']
        self.assertEqual([r['pfail_estimate'] for r in first['runs']], [1e-8, 1.2])
        self.assertEqual(first['checks']['plan_mismatch'], 0)
        self.assertTrue(all('low_observed_weight_mass' in r['warnings'] for r in first['runs']))
        self.assertEqual(second['methods'][0]['no_estimate_fraction'], 1.)
        replay = analyze_results.analyze(job['manifest'], self.root/'replay-completed')
        self.assertEqual(replay, job['analysis'])

    def test_restart_marks_running_and_pending_circuits(self):
        job = self.job()
        job['kind'] = 'suite'
        job['suite'][0]['status'] = 'running'
        self.jobs._save(job)
        restored = server.LocalJobs(self.root)
        record = restored.snapshot(job['id'])
        self.assertEqual(record['status'], 'interrupted')
        self.assertEqual([p['status'] for p in record['suite']], ['interrupted', 'not_started_after_interrupt'])
        saved = server.read_json(Path(job['directory'])/'suite_status.json')
        self.assertEqual(saved['circuit0']['status'], 'interrupted')

    def test_readonly_published_demo_preserved(self):
        source = server.STATIC.parent/'demo_data'
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*') if p.is_file()}
        demo = server.LocalJobs(None, source)
        jobs = demo.listing()
        self.assertTrue(jobs)
        self.assertTrue(demo.evidence(jobs[0]['id'], 0, 0, 'summary')['available'])
        self.assertTrue(demo.evidence(jobs[0]['id'], 0, 0, 'samples')['available'])
        self.assertEqual(before, {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*') if p.is_file()})

    def test_removed_circuit_is_rejected(self):
        job = self.job()
        data = server.read_json(job['manifest'])
        data['circuits'].pop()
        server.write_json(job['manifest'], data)
        with self.assertRaisesRegex(ValueError, 'differs from frozen plan'):
            load_suite(job['manifest'])

    def test_zero_exit_without_manifest_is_error(self):
        job = self.job()
        self.execute(job, lambda *a: 0, [0.])
        self.assertEqual(job['status'], 'incomplete')
        self.assertTrue(all(p['status'] == 'error' for p in job['suite']))

    def test_child_timeout_uses_given_allowance_and_grace(self):
        job = self.job()
        fake = unittest.mock.Mock()
        fake.wait.side_effect = [subprocess.TimeoutExpired('fixture', 7), 0, 0]
        fake.returncode = -15
        fake.poll.return_value = None
        fake.pid = 12345
        with patch.object(self.jobs, '_check_runnable', return_value={}), \
             patch.object(server.subprocess, 'Popen', return_value=fake), \
             patch('yield_estimation.evaluation._process_limits.os.killpg') as signal:
            code = self.jobs._run_child(job, job['suite'][0]['config'], [], 7., Path(job['directory'])/'execution_0')
        self.assertEqual(code, 124)
        self.assertEqual([c.kwargs.get('timeout') for c in fake.wait.call_args_list], [7., 30, None])
        self.assertEqual(signal.call_count, 2)
        self.assertFalse(self.jobs.processes)


if __name__ == '__main__':
    unittest.main(verbosity=2)
