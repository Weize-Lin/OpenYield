"""HTTP and orchestration contracts for the loopback UI; never invokes Xyce."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import subprocess
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from yield_estimation.evaluation.web import LocalJobs, make_handler, preview


class WebContract(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        case = Path(__file__).resolve().parents[1] / 'docs/research/hold_workflow_cases/comparison.json'
        self.config = json.loads(case.read_text())
        self.config['estimators'] = [self.config['estimators'][0]]
        self.config['seeds'] = [42]
        self.jobs = LocalJobs(self.root)

    def finish(self, job):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            value = self.jobs.snapshot(job['id'])
            if value['status'] != 'running' and (Path(value['directory']) / 'job.json').is_file():
                return value
            time.sleep(.01)
        self.fail('Local task did not finish')

    def test_preview_uses_shared_corner_rejection_without_running(self):
        with patch('yield_estimation.evaluation.web.subprocess.Popen') as process:
            result = preview(self.config)
            self.assertEqual(result['tasks'], 1)
            self.assertEqual(result['sample_cap'], 48)
            self.config['experiment']['circuit']['corner'] = 'FF'
            with self.assertRaisesRegex(ValueError, 'corner.*circuit.corner'):
                preview(self.config)
            process.assert_not_called()

    def test_execute_keeps_failed_status_and_analyzes_partial_plan(self):
        captured = []
        def launch(command, **kwargs):
            captured.append(command)
            execution = Path(command[command.index('--output-dir') + 1])
            execution.mkdir()
            (execution / 'analysis_manifest.json').write_text('{}')
            class Process:
                def wait(self, timeout):
                    return 2
            return Process()
        with patch('yield_estimation.evaluation.web.subprocess.Popen', side_effect=launch), \
             patch('yield_estimation.evaluation.web.analyze', return_value={'groups': []}) as analysis:
            job = self.finish(self.jobs.start(dict(config=self.config, time_limit_seconds=30)))
            self.assertEqual(job['status'], 'incomplete')
            self.assertEqual(job['exit_code'], 2)
            self.assertEqual(job['analysis'], {'groups': []})
            self.assertIn('yield_estimation.evaluation.experiment', captured[0])
            analysis.assert_called_once()
            launch_data = json.loads((Path(job['directory']) / 'launch.json').read_text())
            self.assertEqual(launch_data['time_limit_seconds'], 30)

    def test_offline_analysis_reports_errors_and_does_not_launch(self):
        manifest = self.root / 'analysis_manifest.json'
        manifest.write_text('{}')
        with patch('yield_estimation.evaluation.web.analyze', side_effect=ValueError('plan mismatch')), \
             patch('yield_estimation.evaluation.web.subprocess.Popen') as process:
            job = self.finish(self.jobs.start(dict(manifest=str(manifest)), offline=True))
            self.assertEqual(job['status'], 'error')
            self.assertIn('plan mismatch', job['error'])
            process.assert_not_called()

    def test_time_limit_terminates_group_and_preserves_timeout_status(self):
        from unittest.mock import Mock
        process = Mock(pid=123456)
        process.wait.side_effect = [subprocess.TimeoutExpired('analytic-fixture', 1), -15]
        process.returncode = -15
        with patch('yield_estimation.evaluation.web.subprocess.Popen', return_value=process), \
             patch('yield_estimation.evaluation.web.os.killpg') as kill_group:
            job = self.finish(self.jobs.start(dict(config=self.config, time_limit_seconds=1)))
        self.assertEqual(job['status'], 'timeout')
        self.assertEqual(job['exit_code'], -15)
        kill_group.assert_called_once()
        self.assertEqual(kill_group.call_args.args[0], 123456)

    def test_budget_and_concurrent_requests_are_checked_before_launch(self):
        self.config['budget_per_algorithm'] = 0
        with self.assertRaisesRegex(ValueError, 'budget_per_algorithm'):
            self.jobs.start(dict(config=self.config))
        self.config['budget_per_algorithm'] = 48
        self.jobs.jobs['existing'] = dict(status='running')
        with self.assertRaisesRegex(ValueError, '已有任务'):
            self.jobs.start(dict(config=self.config))

    def test_http_rejects_untrusted_post_and_serves_real_validation(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.jobs, 'test-token'))
        self.addCleanup(server.server_close)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.shutdown)
        url = f'http://127.0.0.1:{server.server_port}'
        body = json.dumps({'config': self.config}).encode()
        with self.assertRaises(HTTPError) as raised:
            urlopen(Request(url + '/api/validate', data=body), timeout=3)
        self.assertEqual(raised.exception.code, 403)
        request = Request(url + '/api/validate', data=body,
                          headers={'Origin': url, 'X-OpenYield-Token': 'test-token'})
        with urlopen(request, timeout=3) as response:
            self.assertEqual(json.load(response)['tasks'], 1)
        with urlopen(url, timeout=3) as response:
            self.assertIn('良率算法测评', response.read().decode())


if __name__ == '__main__':
    unittest.main()
