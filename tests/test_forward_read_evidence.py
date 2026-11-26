"""Offline recording tests: all solver invocations are replaced, not real SRAM runs."""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
from utils.xyce import execute_xyce
from yield_estimation.evaluation.run import RecordedAccessTestbench
from yield_estimation.evaluation.batch_evidence import write_batch_evidence

ROOT=Path(__file__).resolve().parents[1]


class SolverAttemptEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.deck=self.root/"deck.sp";self.log=self.root/"deck.log"
        self.deck.write_text(".TRAN 1p 19n\n.END\n")

    def records(self):
        return json.loads(Path(str(self.deck)+".solver_attempts.json").read_text())

    def call(self):
        return execute_xyce(self.deck,["fixture-solver",str(self.deck)],log_path=self.log)

    def test_success_retains_native_code_and_unknown_row_count(self):
        result=subprocess.CompletedProcess([],0,"End of Xyce(TM) Simulation","")
        with patch("utils.xyce.subprocess.run",return_value=result) as runner:self.assertEqual(self.call().returncode,0)
        self.assertEqual(runner.call_count,1)
        a=self.records()["calls"][0]["attempts"][0]
        self.assertEqual(a["native_exit_code"],0);self.assertEqual(a["effective_exit_code"],0)
        self.assertIsNone(a["solver_completed_rows"])
        self.assertTrue(Path(a["deck_snapshot"]).exists())
        self.assertEqual(Path(a["console_file"]).read_text(),"End of Xyce(TM) Simulation\n")

    def test_numeric_zero_exit_retry_archive_and_three_attempt_cap(self):
        replies=["DC Operating Point Failed","Time step too small","End of Xyce(TM) Simulation"]
        def fake(*args,**kwargs):
            text=replies.pop(0)
            Path(str(self.deck)+".prn").write_text(text)
            Path(str(self.deck)+".mt0").write_text("TREAD_TOTAL = 7e-10")
            return subprocess.CompletedProcess(args[0],0,text,"")
        with patch("utils.xyce.subprocess.run",side_effect=fake) as runner:
            self.assertEqual(self.call().returncode,0)
        self.assertEqual(runner.call_count,3)
        attempts=self.records()["calls"][0]["attempts"]
        self.assertEqual([a["trigger"] for a in attempts],["initial","dcop","timestep"])
        self.assertEqual([a["native_exit_code"] for a in attempts],[0,0,0])
        self.assertEqual([a["effective_exit_code"] for a in attempts],[1,1,0])
        self.assertIn("SEARCHMETHOD=2",Path(attempts[1]["deck_snapshot"]).read_text())
        self.assertNotIn("SEARCHMETHOD=2",Path(attempts[0]["deck_snapshot"]).read_text())
        self.assertTrue(Path(attempts[0]["retry_archive"]).exists())
        for a in attempts:
            for name,meta in a["artifacts"].items():
                self.assertEqual(hashlib.sha256(Path(name).read_bytes()).hexdigest(),meta["sha256"])

    def test_repeated_same_numerical_failure_stops_without_extra_retry(self):
        result=subprocess.CompletedProcess([],0,"DC Operating Point Failed","")
        with patch("utils.xyce.subprocess.run",side_effect=lambda *a,**k:subprocess.CompletedProcess([],0,result.stdout,"")) as runner:
            self.assertEqual(self.call().returncode,1)
        self.assertEqual(runner.call_count,2)
        self.assertEqual(len(self.records()["calls"][0]["attempts"]),2)

    def test_non_numeric_failure_does_not_retry(self):
        with patch("utils.xyce.subprocess.run",return_value=subprocess.CompletedProcess([],2,"Syntax error","")) as runner:
            self.assertEqual(self.call().returncode,2)
        self.assertEqual(runner.call_count,1)
        self.assertEqual(self.records()["calls"][0]["attempts"][0]["native_exit_code"],2)

    def test_timeout_preserves_output_without_invented_exit(self):
        with patch("utils.xyce.subprocess.run",side_effect=subprocess.TimeoutExpired("fixture",1,output=b"partial")):
            with self.assertRaises(subprocess.TimeoutExpired):self.call()
        a=self.records()["calls"][0]["attempts"][0]
        self.assertEqual(a["state"],"timeout");self.assertIsNone(a["native_exit_code"])
        self.assertIn("partial",Path(a["console_file"]).read_text())

    def test_interruption_is_recorded_and_raised(self):
        with patch("utils.xyce.subprocess.run",side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):self.call()
        a=self.records()["calls"][0]["attempts"][0]
        self.assertEqual(a["state"],"interrupted");self.assertIsNone(a["native_exit_code"])

    def test_launch_error_is_recorded_without_fake_solver_exit(self):
        with patch("utils.xyce.subprocess.run",side_effect=FileNotFoundError("fixture")):
            with self.assertRaises(FileNotFoundError):self.call()
        a=self.records()["calls"][0]["attempts"][0]
        self.assertEqual(a["state"],"launch_error");self.assertIsNone(a["effective_exit_code"])

    def test_multiple_calls_do_not_replace_prior_attempt_logs(self):
        with patch("utils.xyce.subprocess.run",return_value=subprocess.CompletedProcess([],0,"first","")):self.call()
        old=self.records()["calls"][0]["attempts"][0]
        with patch("utils.xyce.subprocess.run",return_value=subprocess.CompletedProcess([],0,"second","")):self.call()
        self.assertEqual(len(self.records()["calls"]),2)
        self.assertEqual(Path(old["console_file"]).read_text(),"first\n")


    # Each case substitutes the solver. This never starts Xyce.
    def test_evidence_failures_do_not_change_solver_returns_or_retry_cap(self):
        for phase in ('__init__','begin','finish','archived','updated_log'):
            with self.subTest(phase=phase), TemporaryDirectory() as folder:
                deck=Path(folder)/'deck.sp';deck.write_text('.TRAN 1p 19n\n.END\n')
                outputs=iter(('DC Operating Point Failed','Time step too small','End of Xyce'))
                def fake(*args,**kwargs):return subprocess.CompletedProcess(args[0],0,next(outputs),'')
                with patch('utils.xyce._SolverEvidence.'+phase,side_effect=OSError('evidence-only failure')), patch('utils.xyce.subprocess.run',side_effect=fake) as calls:
                    result=execute_xyce(deck,['fixture-solver',str(deck)])
                self.assertEqual(result.returncode,0)
                self.assertEqual(calls.call_count,3)

    def test_evidence_failure_never_masks_original_process_exception(self):
        for error in (subprocess.TimeoutExpired('fixture',1),FileNotFoundError('original launch error'),KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), TemporaryDirectory() as folder:
                deck=Path(folder)/'deck.sp';deck.write_text('.TRAN 1p 19n\n.END\n')
                with patch('utils.xyce._SolverEvidence.finish',side_effect=OSError('evidence-only failure')), patch('utils.xyce.subprocess.run',side_effect=error) as calls:
                    with self.assertRaises(type(error)) as caught:execute_xyce(deck,['fixture-solver',str(deck)])
                self.assertIs(caught.exception,error)
                self.assertEqual(calls.call_count,1)



class FakeBench:
    """Synthetic files solely for testing production recording."""
    def __init__(self,values,error=None):
        self.values=values;self.error=error;self.calls=0

    def run_mc_simulation(self,**kwargs):
        self.calls+=1
        folder=Path(self.sim_path);deck=folder/"deck.sp"
        table=folder/"mc_read_table.data"
        x=kwargs["vars"]
        if x is not None:
            table.write_text(".data table\n+ "+" ".join("p"+str(i) for i in range(x.shape[1]))+"\n"+
                "\n".join("+ "+" ".join(format(v,".17g") for v in row) for row in x)+"\n.enddata\n")
        deck.write_text((f".include {table}\n" if x is not None else "")+
            ".TRAN 1e-11 1.9e-8\n.PRINT TRAN V(WL_EN) V(OUT)\n.meas TRAN TREAD_TOTAL fake_fixture\n.END\n")
        def fake(*args,**kwargs):
            for i,v in enumerate(self.values):Path(str(deck)+f".mt{i}").write_text("TREAD_TOTAL = "+str(v)+"\n")
            Path(str(deck)+".prn").write_text("fixture; not a circuit waveform")
            Path(str(deck)+".raw.data.csv").write_text("Run,TREAD_TOTAL\n0,7e-10\n1,1.1e-9\n")
            return subprocess.CompletedProcess(args[0],0,"End of Xyce(TM) Simulation","")
        with patch("utils.xyce.subprocess.run",side_effect=fake):
            from utils.xyce import execute_xyce as recorded_execute
            recorded_execute(deck,["fixture-solver",str(deck)],log_path=deck.with_suffix(".log"))
        if self.error:raise self.error
        return (np.asarray(self.values),None,None,None)


class BatchEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        with zipfile.ZipFile(ROOT/"workbench/fusion/tests/fixtures/historical_read_contracts.zip") as z:
            self.config=json.loads(z.read("control/run_config.json"))
        self.root.joinpath("run_config.json").write_text(json.dumps(self.config))
        self.root.joinpath("samples.csv").write_text("sample,batch,xyce_run,read_delay_s,outcome\n")
        self.x=np.tile([.4106,.045,-.13]*6,(2,6))

    def run_recorded(self,bench,x=None):
        recorder=RecordedAccessTestbench(bench,self.root,self.config)
        result=recorder.run_mc_simulation(operation="read",target_row=1,target_col=1,mc_runs=2,vars=self.x if x is None else x)
        return recorder,result

    def evidence(self):
        return json.loads((self.root/"batch_0000/batch_evidence.json").read_text())

    def test_success_and_timing_failure_labels_counts_and_native_mapping_unchanged(self):
        recorder,result=self.run_recorded(FakeBench([.7e-9,1.1e-9]))
        self.assertEqual((recorder.submitted,recorder.returned,recorder.timing_failures),(2,2,1))
        np.testing.assert_array_equal(result[0],[.7e-9,1.1e-9])
        with (self.root/"samples.csv").open() as f:rows=list(csv.DictReader(f))
        self.assertEqual([r["outcome"] for r in rows],["pass","timing_failure"])
        e=self.evidence()
        self.assertEqual(e["algorithm_result_rows_recorded"],2)
        self.assertIsNone(e["solver_completed_rows"]);self.assertIsNone(e["compiler_parsed_rows"])
        self.assertEqual(e["requirement"]["threshold_s"],1e-9)
        self.assertEqual(e["solver"]["native_exit_code"],0)
        for i,s in enumerate(e["samples"]):
            self.assertEqual(s["actual_table_line"],i+3)
            self.assertEqual(s["waveform_segment"],i)
            self.assertEqual(s["original_judgment"]["csv_line"],i+2)
            self.assertEqual(s["original_judgment"]["outcome"],rows[i]["outcome"])
        self.assertIn("not prove",e["precision_note"])

    def test_functional_failure_label_is_the_original_classifier_output(self):
        recorder,result=self.run_recorded(FakeBench([.7e-9,float("nan")]))
        self.assertEqual(recorder.functional_failures,1)
        self.assertEqual(self.evidence()["samples"][1]["original_judgment"]["outcome"],"read_functional_failure")

    def test_parser_error_keeps_unclassified_rows_and_native_exit_separate(self):
        bench=FakeBench([.7e-9,1.1e-9],RuntimeError("synthetic parser rejected measurements"))
        recorder=RecordedAccessTestbench(bench,self.root,self.config)
        with self.assertRaisesRegex(RuntimeError,"synthetic parser"):
            recorder.run_mc_simulation(operation="read",target_row=1,target_col=1,mc_runs=2,vars=self.x)
        self.assertEqual((recorder.submitted,recorder.returned),(2,0))
        e=self.evidence();self.assertEqual(e["state"],"call_failed")
        self.assertEqual(e["solver"]["native_exit_code"],0)
        self.assertEqual(e["algorithm_result_rows_recorded"],0)
        self.assertEqual([s["original_judgment"]["outcome"] for s in e["samples"]],["unclassified"]*2)

    def test_budget_rejection_has_no_new_batch_or_solver_call(self):
        bench=FakeBench([.7e-9,1.1e-9]);self.config["budget"]=1
        recorder=RecordedAccessTestbench(bench,self.root,self.config)
        with self.assertRaises(RuntimeError):
            recorder.run_mc_simulation(operation="read",target_row=1,target_col=1,mc_runs=2,vars=self.x)
        self.assertEqual(bench.calls,0);self.assertFalse((self.root/"batch_0000").exists())
        self.assertEqual((recorder.submitted,recorder.returned),(0,0))

    def test_simulator_managed_values_are_not_reconstructed(self):
        self.config["circuit"]["variation_mode"]="per-device"
        self.config["u0_positive"]=False
        self.config["batch_size"]=self.config["budget"]
        recorder=RecordedAccessTestbench(FakeBench([.7e-9,1.1e-9]),self.root,self.config)
        recorder.run_mc_simulation(operation="read",target_row=1,target_col=1,mc_runs=2,vars=None)
        e=self.evidence();self.assertIsNone(e["actual_table"]);self.assertIsNone(e["draw_matrix"])
        self.assertTrue(e["mapping_notes"])

    def test_missing_solver_record_remains_unknown(self):
        batch=self.root/"bare";batch.mkdir()
        e=write_batch_evidence(batch,self.config,self.config["circuit"],{},0,1,state="submitted")
        self.assertIsNone(e["solver"]);self.assertIsNone(e["solver_completed_rows"])
        self.assertIsNone(e["samples"][0]["original_judgment"])

    def test_pre_submit_evidence_is_written_before_backend(self):
        class Inspecting(FakeBench):
            def run_mc_simulation(bench,**kwargs):
                e=json.loads((Path(bench.sim_path)/"batch_evidence.json").read_text())
                assert e["state"]=="submitted" and e["solver"] is None
                return super().run_mc_simulation(**kwargs)
        self.run_recorded(Inspecting([.7e-9,1.1e-9]))


    def test_batch_record_failures_preserve_returns_budget_and_original_errors(self):
        for phase in ('submitted','return_prepared','call_failed'):
            with self.subTest(phase=phase), TemporaryDirectory() as folder:
                root=Path(folder)
                (root/'run_config.json').write_text(json.dumps(self.config))
                (root/'samples.csv').write_text('sample,batch,xyce_run,read_delay_s,outcome\n')
                error=RuntimeError('original parser failure') if phase=='call_failed' else None
                bench=FakeBench([.7e-9,1.1e-9],error)
                recorder=RecordedAccessTestbench(bench,root,self.config)
                def fail(*args,**kwargs):
                    if kwargs['state']==phase:raise OSError('evidence-only failure')
                    return write_batch_evidence(*args,**kwargs)
                with patch('yield_estimation.evaluation.run.write_batch_evidence',side_effect=fail):
                    if error:
                        with self.assertRaises(RuntimeError) as caught:
                            recorder.run_mc_simulation(operation='read',target_row=1,target_col=1,mc_runs=2,vars=self.x)
                        self.assertIs(caught.exception,error)
                    else:
                        result=recorder.run_mc_simulation(operation='read',target_row=1,target_col=1,mc_runs=2,vars=self.x)
                        np.testing.assert_array_equal(result[0],[.7e-9,1.1e-9])
                self.assertEqual((recorder.submitted,recorder.returned),(2,0 if error else 2))
                self.assertEqual(bench.calls,1)
                self.assertEqual(recorder.evidence_recording_errors[0]['state'],phase)
                with (root/'samples.csv').open() as f:rows=list(csv.DictReader(f))
                self.assertEqual([r['outcome'] for r in rows],['unclassified']*2 if error else ['pass','timing_failure'])



if __name__=="__main__":unittest.main(verbosity=2)
