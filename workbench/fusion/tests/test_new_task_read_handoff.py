"""New-job handoff tests. Fake solver outputs are SOFTWARE fixtures, not SRAM evidence."""
from contextlib import nullcontext
import csv,json
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'workbench/fusion'));sys.path.insert(0,str(ROOT/'tests'))
import server
from yield_estimation.evaluation import experiment
from yield_estimation.evaluation import run as evaluation
from test_forward_read_evidence import FakeBench


class NewTaskHandoffTests(unittest.TestCase):
    def test_new_job_uses_production_recording_and_direct_auto_index(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);jobs=server.LocalJobs(root)
            self.addCleanup(jobs.close)
            config=server.read_config();config.update(budget_per_algorithm=2,max_algorithm_draws=2,seeds=[2026112607])
            config['experiment']['batch_size']=2
            config['execution']=dict(task_timeout_seconds=90,experiment_timeout_seconds=110,
                task_kill_grace_seconds=10,experiment_kill_grace_seconds=10,order='estimator_major')
            def fake_child(job,config,refs,limit,outdir):
                # Keep actual planner/config/recorder/MC/classifier/analysis. Replace external solve only.
                def fake_limited(command,stream,cwd,limits,deadline):
                    child_path=Path(command[command.index('--config')+1]);child=evaluation.read_config(child_path)
                    run_dir=Path(command[command.index('--output-dir')+1])
                    options=evaluation.evaluation_circuit_options(child)
                    cell=[.4106,.045,-.13]*3+[-.3842,.02,-.126]+[.4106,.045,-.13]+[-.3842,.02,-.126]
                    import numpy as np
                    means=np.tile(cell,6);process=evaluation.IndependentProcess(means,.05,child['seed'])
                    bench=FakeBench([.7e-9,1.1e-9])
                    with patch.object(evaluation.shutil,'which',return_value='SOFTWARE_FIXTURE_NO_SOLVER'),patch.object(evaluation,'xyce_fingerprint',return_value={'xyce':'SOFTWARE_FIXTURE_NO_SOLVER'}),patch.object(evaluation,'build_evaluation_case',return_value=(means,process,bench)):
                        code=evaluation.run_evaluation(child,run_dir)
                    return code,'complete' if code==0 else 'process_failed'
                with patch.object(experiment,'forward_termination',return_value=nullcontext()),patch.object(experiment,'run_limited',side_effect=fake_limited):
                    result=experiment.run_experiment(config,outdir,reference_runs=refs)
                return 0 if all(r['status']=='complete' for r in result['trials']) else 1
            with patch.object(jobs,'_check_runnable',return_value={'source':'software-fixture'}),patch.object(jobs,'_run_child',side_effect=fake_child):
                created=jobs.start(dict(name='SOFTWARE fixture new task',config=config,references=[],time_limit_seconds=120))
                jobs.threads[created['id']].join(30)
            job=jobs.snapshot(created['id']);self.assertEqual(job['status'],'complete',job.get('error'))
            self.assertNotIn('saved_evidence_runs',job)
            folder=jobs._run(created['id'],0,0)[2]
            self.assertFalse((folder/'saved_read_verification.json').exists())
            self.assertFalse((folder.parent/'registration.json').exists())
            with (folder/'samples.csv').open() as f:labels=list(csv.DictReader(f))
            self.assertEqual([r['outcome'] for r in labels],['pass','timing_failure'])
            self.assertEqual([r['sample'] for r in labels],['1','2'])
            ledger=json.loads((folder/'batch_0000/batch_evidence.json').read_text())
            self.assertEqual(ledger['requirement']['threshold_s'],1e-9)
            self.assertEqual([r['waveform_segment'] for r in ledger['samples']],[0,1])
            self.assertEqual(len(ledger['samples']),2)
            self.assertIsNotNone(ledger['solver']['record']['sha256'])
            available=jobs.checks().availability(created['id'],0,0)
            self.assertEqual(available['origin']['kind'],'saved_run')
            self.assertEqual([r['id'] for r in available['samples']],['1','2'])
            self.assertTrue(all(r['threshold_state']=='bound' for r in available['samples']))
            # FakeBench has no physical topology/usable wave: never assert electrical validity.
            self.assertTrue(all(not r['supported'] for r in available['samples']))

    def test_two_sample_budget_is_one_batch_in_existing_mc(self):
        import numpy as np
        from yield_estimation.model_lib.MC import MC
        calls=[]
        class Process:
            def sample(self,n):return np.ones((n,3))
        class Bench:
            def run_mc_simulation(self,**kwargs):
                calls.append(kwargs['mc_runs']);return np.asarray([.7e-9]*kwargs['mc_runs']),None,None,None
        with tempfile.TemporaryDirectory() as temp:
            import os
            old=Path.cwd()
            try:
                os.chdir(temp)
                mc=MC(f_norm=Process(),mc_testbench=Bench(),means=np.ones(3),feature_num=3,
                    IS_bound_num=1,initial_num=2,sample_num=2,seed=2026112607,target=[1,1],use_legacy_bounds=False,operation='read')
                mc.threshold=1e-9;mc.start_estimate(max_num=2,fixed_budget=True)
            finally:os.chdir(old)
        self.assertEqual(calls,[2])


if __name__=='__main__':unittest.main()
