import copy
import hashlib
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
sys.path.insert(0, str(server.ROOT))
from yield_estimation.evaluation.analysis_plan import analysis_manifest

from fixtures import fixture


class FusionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.jobs = server.LocalJobs(self.folder)
        self.read = fixture(self.folder, 'read')
        self.hold = fixture(self.folder, 'hold_snm', reference=True)
        # Executor tests below never launch a circuit; provenance is tested separately.
        runnable = patch.object(self.jobs, '_check_runnable', return_value={})
        runnable.start()
        self.addCleanup(runnable.stop)

    def tearDown(self):
        self.jobs.close()
        self.tmp.cleanup()

    def load(self, manifest):
        job = self.jobs.start(dict(manifest=str(manifest)), offline=True)
        self.jobs.threads[job['id']].join(timeout=30)
        result = self.jobs.snapshot(job['id'])
        self.assertEqual(result['status'], 'complete', result.get('error'))
        return result

    def test_preview_six_rounds_and_strategy_identity(self):
        original, choices = server.presets()
        config = choices[0]['config']
        before = copy.deepcopy(config)
        result = server.preview(config)
        self.assertEqual(config, before)
        self.assertEqual((result['tasks'], result['sample_cap']), (6, 72))
        self.assertEqual([p['id'] for p in result['config']['estimators']], ['mnis-base', 'mnis-mixed-50'])
        self.assertEqual([p['proposal']['target_fraction'] for p in result['config']['estimators']], [0, .5])
        self.assertEqual(len(result['plan']), 6)
        self.assertEqual(server.preview(original)['sample_cap'], 1920)

    def test_reference_seed_overlap_rejected_before_run(self):
        config = server.presets()[1][1]['config']
        config['seeds'] = [2026111801]
        reference = self.hold.parent / 'reference'
        with self.assertRaisesRegex(ValueError, '参考种子不能'):
            self.jobs.start(dict(config=config, references=[str(reference)]))
        self.assertEqual(self.jobs.jobs, {})

    def test_read_evidence_matches_original(self):
        before = hashlib.sha256(self.read.read_bytes()).hexdigest()
        job = self.load(self.read)
        row = job['analysis']['groups'][0]['runs'][0]
        self.assertEqual(row['cost']['xyce_returned'], 4)
        self.assertIsNone(row['absolute_error'])
        evidence = self.jobs.evidence(job['id'], 0, 0, 'samples')
        self.assertEqual(evidence['total'], 4)
        self.assertAlmostEqual(float(evidence['rows'][0]['read_delay_s']) * 1e9, .7131475, places=7)
        for kind in ('summary', 'config', 'report', 'log'):
            self.assertTrue(self.jobs.evidence(job['id'], 0, 0, kind)['available'], kind)
        self.assertEqual(before, hashlib.sha256(self.read.read_bytes()).hexdigest())
        with self.assertRaises(ValueError):
            self.jobs.evidence(job['id'], -1, 0, 'summary')
        with self.assertRaises(ValueError):
            self.jobs.asset(job['id'], 0, 0, '../../job.json')

    def test_hold_reference_and_weight_warning(self):
        job = self.load(self.hold)
        group = job['analysis']['groups'][0]
        self.assertEqual(group['reference']['samples'], 128)
        self.assertEqual(group['reference']['failure_count'], 10)
        row = group['runs'][0]
        self.assertIn('low_observed_weight_mass', row['warnings'])
        self.assertEqual(row['cost']['formal_draws'], 7)
        self.assertEqual(self.jobs.evidence(job['id'], 0, 0, 'samples')['total'], 12)

    def test_missing_rounds_stay_in_frozen_plan(self):
        source = self.folder / 'frozen'
        source.mkdir()
        config = server.preview(server.presets()[1][0]['config'])['config']
        server.write_json(source / 'experiment.json', config)
        manifest = analysis_manifest(config, source / 'experiment.json', [])
        server.write_json(source / 'analysis_manifest.json', manifest)
        job = self.load(source / 'analysis_manifest.json')
        group = job['analysis']['groups'][0]
        self.assertEqual(group['checks']['total_runs'], 6)
        self.assertEqual([m['total_runs'] for m in group['methods']], [3, 3])
        self.assertEqual([m['estimates_available'] for m in group['methods']], [0, 0])
        self.assertEqual(job['finished_tasks'], 0)
        self.assertFalse(self.jobs.evidence(job['id'], 0, 0, 'samples')['available'])
        self.assertTrue(self.jobs.evidence(job['id'], 0, 0, 'config')['available'])
        manifest['groups'][0]['runs'].pop()
        server.write_json(source / 'analysis_manifest.json', manifest)
        bad = self.jobs.start(dict(manifest=str(source / 'analysis_manifest.json')), offline=True)
        self.jobs.threads[bad['id']].join(timeout=30)
        self.assertEqual(self.jobs.snapshot(bad['id'])['status'], 'error')

    def test_paths_and_symlinks_are_restricted(self):
        with self.assertRaises(ValueError):
            self.jobs._validate_manifest('/etc/passwd')
        source = self.folder / 'malicious'
        source.mkdir()
        manifest = dict(schema_version=1, groups=[dict(id='test', runs=[dict(estimator='mc', seed=1, run='/etc')])])
        server.write_json(source / 'analysis_manifest.json', manifest)
        with self.assertRaises(ValueError):
            self.jobs._validate_manifest(source / 'analysis_manifest.json')
        (source / 'link').symlink_to('/etc', target_is_directory=True)
        manifest['groups'][0]['runs'][0]['run'] = 'link'
        server.write_json(source / 'analysis_manifest.json', manifest)
        with self.assertRaises(ValueError):
            self.jobs._validate_manifest(source / 'analysis_manifest.json')

    def test_history_restores_without_reexecution(self):
        job = self.load(self.read)
        restored = server.LocalJobs(self.folder)
        self.assertEqual(restored.snapshot(job['id'])['analysis'], job['analysis'])
        self.assertEqual(restored.listing()[0]['status'], 'complete')
        self.assertEqual(restored.processes, {})
        self.jobs.jobs[job['id']]['status'] = 'running'
        self.jobs._save(self.jobs.jobs[job['id']])
        interrupted = server.LocalJobs(self.folder)
        self.assertEqual(interrupted.snapshot(job['id'])['status'], 'interrupted')

    def test_sample_pagination_and_changed_summary(self):
        folder = self.folder / 'sample-run'
        folder.mkdir()
        summary = folder / 'run_summary.json'
        server.write_json(summary, {'seed': 1})
        (folder / 'samples.csv').write_text('sample,value\n' + ''.join(f'{i},{i * 2}\n' for i in range(121)), encoding='utf-8')
        row = dict(source=dict(summary=str(summary), summary_sha256=hashlib.sha256(summary.read_bytes()).hexdigest()), status='complete')
        self.jobs.jobs['fixture'] = dict(id='fixture', status='complete', offline=True, directory=str(folder), analysis=dict(groups=[dict(runs=[row])]))
        data = self.jobs.evidence('fixture', 0, 0, 'samples', page=3)
        self.assertEqual((data['total'], len(data['rows']), data['rows'][0]['sample']), (121, 21, '100'))
        server.write_json(summary, {'seed': 2})
        with self.assertRaisesRegex(ValueError, '摘要已变化'):
            self.jobs.evidence('fixture', 0, 0, 'samples')

    def test_cancel_stops_only_the_registered_child(self):
        folder = self.folder / 'cancellation'
        folder.mkdir()
        job = dict(id='cancellation', name='controlled child', status='running', offline=False,
                   directory=str(folder), config=server.presets()[1][1]['config'], references=[],
                   time_limit_seconds=60, manifest=str(folder / 'execution/analysis_manifest.json'))
        self.jobs.jobs[job['id']] = job
        started = threading.Event()
        original_popen = server.subprocess.Popen
        children = []
        def controlled_child(command, **kwargs):
            process = original_popen([sys.executable, '-c', 'import time; time.sleep(60)'], **kwargs)
            children.append(process)
            started.set()
            return process
        with patch.object(server.subprocess, 'Popen', side_effect=controlled_child):
            worker = threading.Thread(target=self.jobs._work, args=(job, lambda: self.jobs._execute(job)))
            self.jobs.threads[job['id']] = worker
            worker.start()
            self.assertTrue(started.wait(timeout=10))
            self.jobs.cancel(job['id'])
            worker.join(timeout=15)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.jobs.snapshot(job['id'])['status'], 'cancelled')
        self.assertLess(children[0].returncode, 0)
        self.assertTrue((folder / 'request.json').is_file())
        self.assertFalse((folder / 'execution').exists())

    def test_suite_validate_and_cap(self):
        _, choices = server.presets()
        suite = next(p for p in choices if p['id'] == 'suite')['suite']
        httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.make_handler(self.jobs, 't'))
        worker = threading.Thread(target=httpd.serve_forever, daemon=True); worker.start()
        port = httpd.server_port
        try:
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=20)
            headers = {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{port}', 'X-OpenYield-Token': 't'}
            conn.request('POST', '/api/suite/validate', body=json.dumps({'experiments': [{'config': c} for c in suite]}), headers=headers)
            response = conn.getresponse(); body = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual((body['tasks'], body['sample_cap']), (7, 76))
            self.assertEqual([p['label'] for p in body['plans']], ['1×1 · 保持裕量', '3×2 · 读延迟'])
            big = copy.deepcopy(suite[0]); big['budget_per_algorithm'] = 200; big['max_algorithm_draws'] = 200
            with self.assertRaisesRegex(ValueError, '400'):
                self.jobs.start_suite({'experiments': [{'config': big}]})
            conn.close()
        finally:
            httpd.shutdown(); httpd.server_close(); worker.join()

    def test_circuit_evidence_and_dataset(self):
        job = self.load(self.hold)
        circuit = self.jobs.evidence(job['id'], 0, 0, 'circuit')
        self.assertTrue(circuit['available'])
        first = circuit['content']['batches'][0]
        self.assertIn('waveform', first)
        png = self.jobs.batch_file(job['id'], 0, 0, first['name'], Path(first['waveform'].split('name=')[1]).name)
        self.assertEqual(png.read_bytes()[:8], b'\x89PNG\r\n\x1a\n')
        with self.assertRaises(ValueError):
            self.jobs.batch_file(job['id'], 0, 0, first['name'], '../../job.json')
        data = self.jobs.dataset(job['id'], 0, 0).decode('utf-8')
        lines = data.strip().split('\n')
        self.assertIn('param_000', lines[0])
        self.assertEqual(len(lines) - 1, 12)

    def test_demo_bundle_is_readonly_and_selfcontained(self):
        job = self.load(self.hold)
        bundle = self.folder / 'demo'
        run_dir = bundle / job['id'] / 'runs' / '0_0'
        (run_dir / 'batches').mkdir(parents=True)
        source = Path(job['analysis']['groups'][0]['runs'][0]['source']['summary']).parent
        for name in ('run_summary.json', 'samples.csv', 'run_config.json', 'report.md'):
            (run_dir / name).write_bytes((source / name).read_bytes())
        (run_dir / 'process.log').write_bytes((source.parent / 'process.log').read_bytes())
        batch = sorted(source.glob('batch_*'))[0]
        target = run_dir / 'batches' / batch.name
        target.mkdir()
        for path in batch.iterdir():
            if path.suffix in ('.png', '.sp', '.json'):
                (target / path.name).write_bytes(path.read_bytes())
        (run_dir / 'dataset.csv').write_bytes(self.jobs.dataset(job['id'], 0, 0))
        record = {k: job[k] for k in ('id', 'name', 'status', 'source', 'created_at', 'plan_count')}
        record.update(offline=True, demo_evidence={'0/0': 'runs/0_0'}, analysis=job['analysis'])
        server.write_json(bundle / job['id'] / 'job.json', record)
        (bundle / job['id'] / 'analysis').mkdir()
        server.write_json(bundle / job['id'] / 'analysis' / 'analysis.json', job['analysis'])
        server.write_json(bundle / 'context.json', dict(template={}, presets=[], revision='synthetic-fixture'))
        demo = server.LocalJobs(None, demo_root=bundle)
        self.assertEqual(demo.listing()[0]['finished_tasks'], 1)
        self.assertEqual(demo.evidence(job['id'], 0, 0, 'samples')['total'], 12)
        self.assertTrue(demo.evidence(job['id'], 0, 0, 'circuit')['available'])
        self.assertEqual(len(demo.dataset(job['id'], 0, 0).decode().strip().split('\n')) - 1, 12)
        with self.assertRaisesRegex(ValueError, '演示模式只读'):
            demo.start({'config': server.presets()[1][1]['config']})
        with self.assertRaisesRegex(ValueError, '演示模式只读'):
            demo.start({'manifest': str(self.hold)}, offline=True)

    def test_loopback_and_request_protection(self):
        httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.make_handler(self.jobs, 'test-token'))
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        port = httpd.server_port
        try:
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
            conn.request('GET', '/api/context')
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())['token'], 'test-token')
            conn.request('GET', '/api/health', headers={'Host': 'evil.example'})
            response = conn.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
            payload = json.dumps(dict(config=server.presets()[1][0]['config']))
            conn.request('POST', '/api/validate', body=payload, headers={'Content-Type': 'application/json'})
            response = conn.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
            headers = {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{port}', 'X-OpenYield-Token': 'test-token'}
            conn.request('POST', '/api/validate', body=payload, headers=headers)
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())['tasks'], 6)
            conn.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            worker.join()


if __name__ == '__main__':
    suite = unittest.main(verbosity=2, exit=False)
    sys.exit(0 if suite.result.wasSuccessful() else 1)
