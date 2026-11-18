"""Bounded existing experiment entry with Python-only replacement processes."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from yield_estimation.evaluation import experiment
from yield_estimation.evaluation._process_limits import run_limited
from yield_estimation.evaluation.analyze_results import analyze, analyze_run

ROOT = Path(__file__).resolve().parents[1]


def config():
    case = json.loads((ROOT/'docs/research/hold_workflow_cases/comparison.json').read_text())
    case['estimators'] = [dict(id='fixture', algorithm='mc', mode='reference')]
    case['seeds'] = [1, 2, 3]
    case['execution'] = dict(task_timeout_seconds=5, experiment_timeout_seconds=1,
                             task_kill_grace_seconds=1, experiment_kill_grace_seconds=1,
                             order='seed_major')
    return case


class ExecutionLimits(unittest.TestCase):
    def test_invalid_limits_rejected_and_optional_defaults_unchanged(self):
        plain = config()
        del plain['execution']
        resolved = experiment.normalize_experiment(plain)
        self.assertNotIn('execution', resolved)
        for value in (0, -1, True, 1.5, '3'):
            raw = config()
            raw['execution']['task_timeout_seconds'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                experiment.normalize_experiment(raw)

    def test_seed_major_is_explicit_and_keeps_same_task_identities(self):
        raw = config()
        raw['estimators'].append(dict(raw['estimators'][0], id='second'))
        normalized = experiment.normalize_experiment(raw)
        from yield_estimation.evaluation.analysis_plan import planned_trials
        expected = [(entry['estimator'], entry['seed']) for entry, _ in planned_trials(normalized)]
        self.assertEqual(expected, [(p['id'], s) for p, s, _ in experiment.child_configs(normalized)])
        self.assertEqual([(p['id'], s) for p, s, _ in experiment.child_configs(normalized)],
                         [('fixture', 1), ('second', 1), ('fixture', 2), ('second', 2), ('fixture', 3), ('second', 3)])
        normalized['execution']['order'] = 'estimator_major'
        self.assertEqual([(p['id'], s) for p, s, _ in experiment.child_configs(normalized)][:3],
                         [('fixture', 1), ('fixture', 2), ('fixture', 3)])

    def test_task_timeout_then_next_child_completes(self):
        with TemporaryDirectory() as folder:
            limits = config()['execution']
            limits.update(task_timeout_seconds=.2, experiment_timeout_seconds=5, task_kill_grace_seconds=.1)
            with (Path(folder)/'log').open('wb') as stream:
                code, state = run_limited([sys.executable, '-c', 'import time; time.sleep(10)'], stream, ROOT,
                                          limits, time.monotonic()+5)
                self.assertEqual((code, state), (124, 'task_timeout'))
                code, state = run_limited([sys.executable, '-c', 'print("CPU fixture complete")'], stream, ROOT,
                                          limits, time.monotonic()+5)
                self.assertEqual((code, state), (0, 'complete'))

    def test_global_timeout_retains_every_planned_task_and_offline_missing_fraction(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            def fake_plan(path, output, algorithms, *args):
                # Only replace electrical measurement; use the real bounded launcher.
                return [('mc', [sys.executable, '-c', 'import time; time.sleep(10)'])]
            with patch.object(experiment, 'plan', side_effect=fake_plan), redirect_stdout(io.StringIO()):
                result = experiment.run_experiment(config(), root/'run')
            self.assertEqual([r['status'] for r in result['trials']],
                             ['experiment_timeout', 'not_started_after_experiment_timeout', 'not_started_after_experiment_timeout'])
            self.assertEqual([r['estimated_pfail'] for r in result['trials']], [None]*3)
            manifest = root/'run/analysis_manifest.json'
            self.assertEqual(len(json.loads(manifest.read_text())['groups'][0]['runs']), 3)
            for seed in (1, 2, 3):
                self.assertTrue((root/f'run/fixture/seed_{seed}/config.json').is_file())
                self.assertTrue((root/f'run/fixture/seed_{seed}/execution.json').is_file())
            with redirect_stdout(io.StringIO()):
                group = analyze(manifest, root/'analysis')['groups'][0]
            self.assertEqual([r['status'] for r in group['runs']], [r['status'] for r in result['trials']])
            self.assertEqual(sum(r['no_estimate_runs'] for r in group['methods']), 3)
            self.assertTrue(all(r['no_estimate_fraction'] == 1 for r in group['methods']))
            self.assertTrue(all(r['budget_tier']['time_limit_s'] == 5 for r in group['methods']))

    def test_saved_complete_estimate_does_not_override_timeout_or_missing_audit(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            algorithm = root/'trial/mc'
            algorithm.mkdir(parents=True)
            profile = dict(id='fixture', algorithm='mc', mode='reference')
            native = dict(seed=7, status='complete', pfail_estimate=1.2,
                          algorithm_requested_samples=4, simulator_submitted_samples=4,
                          simulator_returned_samples=4, unclassified_samples=0,
                          diagnostics=dict(numeric_status='representable', estimation_draws_evaluated=4),
                          research=dict(estimator=profile))
            (algorithm/'run_summary.json').write_text(json.dumps(native))
            entry = dict(estimator='fixture', seed=7, run='trial/mc')
            baseline = analyze_run(entry, root)
            self.assertTrue(baseline['linear_usable'])
            limits = config()['execution']
            planned = dict(target={}, profile=profile, execution=limits)
            audit = dict(schema_version=1, estimator_id='fixture', seed=7, state='task_timeout',
                         limits=limits, exit_code=124, wall_seconds=1.)
            file = algorithm.parent/'execution.json'
            file.write_text(json.dumps(audit))
            row = analyze_run(entry, root, planned)
            self.assertEqual(row['status'], 'task_timeout')
            self.assertEqual(row['pfail_estimate'], 1.2)
            self.assertFalse(row['estimate_available'])
            self.assertIn('estimate_above_one', row['warnings'])
            file.write_text('[]')
            self.assertEqual(analyze_run(entry, root, planned)['status'], 'missing_or_invalid_execution_record')
            file.unlink()
            self.assertFalse(analyze_run(entry, root, planned)['estimate_available'])

    def test_parent_termination_forwards_to_separate_child_group(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            pidfile, resultfile = root/'child.pid', root/'result.json'
            raw = config()
            raw['execution'].update(task_timeout_seconds=30, experiment_timeout_seconds=30)
            child = f"import os,time; from pathlib import Path; Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(10)"
            program = (
                "import json,sys; from pathlib import Path; from unittest.mock import patch; "
                "from yield_estimation.evaluation import experiment; "
                f"raw=json.loads({json.dumps(raw)!r}); "
                f"command=[sys.executable,'-c',{child!r}]; "
                "replacement=lambda *args: [('mc',command)]; "
                "context=patch.object(experiment,'plan',side_effect=replacement); context.start(); "
                f"result=experiment.run_experiment(raw,{str(root/'execution')!r}); "
                f"Path({str(resultfile)!r}).write_text(json.dumps(result))"
            )
            with (root/'parent.log').open('wb') as stream:
                process = subprocess.Popen([sys.executable, '-c', program], cwd=ROOT, stdout=stream,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    deadline = time.monotonic()+5
                    while not pidfile.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertTrue(pidfile.exists(), (root/'parent.log').read_text())
                    os.kill(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                        if pidfile.exists():
                            try:
                                os.killpg(int(pidfile.read_text()), signal.SIGKILL)
                            except ProcessLookupError:
                                pass
            rows = json.loads(resultfile.read_text())['trials']
            self.assertEqual([r['status'] for r in rows],
                ['interrupted', 'not_started_after_interrupt', 'not_started_after_interrupt'])
            self.assertFalse(Path(f'/proc/{int(pidfile.read_text())}').exists())

    def test_timeout_cleans_a_grandchild_that_ignores_term(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            pid_file = root/'grandchild.pid'
            grandchild = f"import os,signal,time; from pathlib import Path; signal.signal(signal.SIGTERM, signal.SIG_IGN); Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(10)"
            parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{grandchild!r}]); time.sleep(10)"
            limits = config()['execution']
            limits.update(task_timeout_seconds=.5, task_kill_grace_seconds=.1)
            with (root/'log').open('wb') as stream:
                code, state = run_limited([sys.executable, '-c', parent], stream, ROOT, limits, time.monotonic()+5)
            self.assertEqual((code, state), (124, 'task_timeout'))
            self.assertTrue(pid_file.is_file(), 'The fixture grandchild must actually start')
            pid = int(pid_file.read_text())
            deadline = time.monotonic()+2
            while time.monotonic() < deadline:
                stat = Path(f'/proc/{pid}/stat')
                try:
                    state = stat.read_text().split()[2]
                except (FileNotFoundError, ProcessLookupError):
                    break
                if state == 'Z':
                    break
                time.sleep(.01)
            else:
                self.fail('Fixture grandchild is still running after group timeout')


if __name__ == '__main__':
    unittest.main()
