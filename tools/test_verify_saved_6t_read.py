"""Software fixtures only. Mutations below do not represent circuit experiments."""
import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
import numpy as np
import verify_saved_6t_read as v

PACK = Path(__file__).resolve().parents[1]/"workbench/fusion/tests/fixtures/historical_read_contracts.zip"


class SavedReadVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        with zipfile.ZipFile(PACK) as archive:archive.extractall(self.root)
        self.manifest=json.loads((self.root/"manifest.json").read_text())
        self.case=copy.deepcopy(self.manifest["cases"][2])
        # Mutated fixtures test semantics, not original packet checksums.
        self.case["sha256"]={}

    def run_case(self):
        return v.verify(self.case,self.root,{})

    def waveform(self, mutate):
        path=self.root/self.case["files"]["waveform"]
        header,blocks=v.io.parse_prn(path)
        header,blocks=mutate(header,blocks)
        header=["Index" if k=="INDEX" else k for k in header]
        with path.open("w") as stream:
            stream.write(" ".join(header)+"\n")
            for block in blocks:
                for row in block:stream.write(" ".join(format(float(x),".17g") for x in row)+"\n")
            stream.write("End of Xyce(TM) Parameter Sweep\n")

    def test_complete_fixture_retains_label_and_source_lines(self):
        r=self.run_case()
        self.assertEqual(r["comparison"],"支持原判断")
        self.assertEqual(r["original"]["label"],"pass")
        self.assertTrue(r["evidence"]["observation"]["out"]["lines"])
        self.assertIsNone(r["evidence"]["simulation"]["solver_exit_code"])

    def test_three_segments_select_third_matching_measurement(self):
        self.case=copy.deepcopy(self.manifest["cases"][4])
        r=self.run_case()
        self.assertEqual(r["comparison"],"支持原判断")
        self.assertEqual(r["evidence"]["waveform"]["segments"],3)
        self.assertEqual(r["evidence"]["waveform"]["selected_segment"],2)
        self.assertEqual(r["original"]["record"]["sample"],"3")

    def test_missing_output_event_does_not_imply_physical_failure(self):
        def mutate(h,bs):
            for b in bs:b[:,h.index("V(OUT)")]=0
            return h,bs
        self.waveform(mutate)
        r=self.run_case()
        self.assertEqual(r["circuit_observation"],"functional_pass")
        self.assertEqual(r["timing_observation"],"event_missing")
        self.assertEqual(r["comparison"],"证据不足")

    def test_failed_measurement_without_failed_voltage_is_not_failure(self):
        p=self.root/self.case["files"]["measurement"]
        p.write_text("TREAD_TOTAL = FAILED\n")
        r=self.run_case()
        self.assertEqual(r["circuit_observation"],"functional_pass")
        self.assertEqual(r["comparison"],"存在冲突")
        self.assertTrue(any(i["kind"]=="measurement_conflict" for i in r["issues"]))

    def test_sense_enabled_before_read_window_is_not_control_confirmation(self):
        def mutate(h,bs):
            columns=[i for i,key in enumerate(h) if key=="V(S_EN_LINE_TAP1)"]
            self.assertEqual(len(columns),1)
            for b in bs:b[:,columns[0]]=1
            return h,bs
        self.waveform(mutate)
        r=self.run_case()
        self.assertFalse(r["evidence"]["controls"]["sense_after_wordline"])
        self.assertEqual(r["comparison"],"证据不足")

    def test_missing_node_is_insufficient(self):
        def mutate(h,bs):
            index=h.index("V(OUT)")
            return h[:index]+h[index+1:],[np.delete(b,index,axis=1) for b in bs]
        self.waveform(mutate)
        r=self.run_case()
        self.assertEqual(r["comparison"],"证据不足")
        self.assertTrue(any("Missing waveform node" in i["detail"] for i in r["issues"]))

    def test_truncated_time_coverage_is_not_completed(self):
        self.waveform(lambda h,bs:(h,[b[b[:,h.index("TIME")]<10e-9] for b in bs]))
        r=self.run_case()
        self.assertEqual(r["evidence"]["simulation"]["state"],"not_confirmed")
        self.assertEqual(r["comparison"],"证据不足")

    def test_dcop_error_cannot_be_hidden_by_end_marker(self):
        p=self.root/self.case["files"]["solver_log"]
        p.write_text(p.read_text()+"\nDC Operating Point Failed\n")
        r=self.run_case()
        self.assertEqual(r["evidence"]["simulation"]["state"],"not_confirmed")
        self.assertEqual(r["comparison"],"证据不足")

    def test_missing_segment_not_silently_reused(self):
        self.waveform(lambda h,bs:(h,bs[:2]))
        r=self.run_case()
        self.assertEqual(r["comparison"],"证据不足")

    def test_wrong_measurement_row_conflicts(self):
        self.case["files"]["measurement"]=self.manifest["cases"][3]["files"]["measurement"]
        r=self.run_case()
        self.assertEqual(r["comparison"],"存在冲突")

    def test_wrong_original_row_conflicts(self):
        self.case["row"]=1
        r=self.run_case()
        self.assertEqual(r["comparison"],"存在冲突")
        self.assertTrue(any("row disagree" in i["detail"] for i in r["issues"]))

    def test_unexplained_parameter_export_change_conflicts(self):
        p=self.root/self.case["files"]["matrix"]
        a=np.load(p);a[0,0]+=.1;np.save(p,a)
        self.assertEqual(self.run_case()["comparison"],"存在冲突")

    def test_explicit_replay_rounding_preserves_actual_table(self):
        self.case=copy.deepcopy(self.manifest["cases"][0])
        r=self.run_case()
        self.assertEqual(r["comparison"],"支持原判断")
        self.assertFalse(r["evidence"]["mapping"]["table_matches"])
        self.assertTrue(any(i["kind"]=="recorded_export_precision_difference" for i in r["issues"]))

    def test_conflicting_repeated_probe_columns_rejected(self):
        def mutate(h,bs):
            indices=[i for i,key in enumerate(h) if key=="V(S_EN)"]
            self.assertGreater(len(indices),1)
            for b in bs:b[:,indices[1]]+=.1
            return h,bs
        self.waveform(mutate)
        self.assertEqual(self.run_case()["comparison"],"存在冲突")

    def test_read_one_and_10t_are_explicitly_unsupported(self):
        for field,value in [("expected_bit",1),("cell_type","SRAM_10T_CELL")]:
            with self.subTest(field=field):
                self.case=copy.deepcopy(self.manifest["cases"][2])
                self.case["contract"][field]=value
                self.assertEqual(self.run_case()["comparison"],"不支持")

    def test_changed_delay_event_definition_is_unsupported(self):
        p=self.root/self.case["files"]["deck"]
        s=p.read_text().replace("V(OUT)=0.5 FALL=1","V(OUT)=0.5 FALL=2")
        self.assertNotEqual(s,p.read_text());p.write_text(s)
        self.assertEqual(self.run_case()["comparison"],"不支持")

    def test_wrong_input_hash_conflicts(self):
        self.case["sha256"]={"matrix":"0"*64}
        self.assertEqual(self.run_case()["comparison"],"存在冲突")

    def test_nonzero_saved_solver_exit_prevents_support(self):
        self.case=copy.deepcopy(self.manifest["cases"][0])
        p=self.root/self.case["solver_exit"]["file"]
        d=json.loads(p.read_text());d["tasks"][0]["attempts"][0]["exit_code"]=1
        p.write_text(json.dumps(d))
        r=self.run_case()
        self.assertEqual(r["evidence"]["simulation"]["solver_exit_code"],1)
        self.assertEqual(r["comparison"],"证据不足")

    def test_missing_original_judgment_not_fabricated(self):
        self.case.pop("original")
        r=self.run_case()
        self.assertIsNone(r["original"]["label"])
        self.assertEqual(r["comparison"],"证据不足")

    def test_conflicting_original_label_is_preserved(self):
        p=self.root/self.case["original"]["file"]
        with p.open() as f:rows=list(csv.DictReader(f))
        rows[0]["outcome"]="timing_failure"
        with p.open("w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
        r=self.run_case()
        self.assertEqual(r["original"]["label"],"timing_failure")
        self.assertEqual(r["comparison"],"存在冲突")

    def test_old_absolute_paths_never_used_as_fallback(self):
        self.case["include_aliases"]={}
        self.assertEqual(self.run_case()["comparison"],"存在冲突")

    def test_threshold_matches_both_original_saved_records(self):
        r=self.run_case()
        b=r["evidence"]["threshold_binding"]
        self.assertEqual(b["state"],"bound")
        self.assertEqual(b["effective_s"],1e-9)
        self.assertEqual(len(b["sources"]),2)
        self.assertEqual(b["sources"][0]["json_pointer"],["threshold_ns"])
        self.assertEqual(r["comparison"],"支持原判断")

    def test_manifest_threshold_changes_are_condition_conflicts(self):
        for changed in [.8e-9,.6e-9]:
            with self.subTest(changed=changed):
                self.case["threshold_s"]=changed
                r=self.run_case()
                self.assertEqual(r["comparison"],"存在冲突")
                self.assertEqual(r["conflict_categories"],["verification_condition"])
                self.assertEqual(r["circuit_observation"],"functional_pass")
                self.assertEqual(r["timing_observation"],"condition_conflict")
                self.assertIsNone(r["evidence"]["delay"]["threshold_s"])
                self.assertEqual(r["evidence"]["threshold_binding"]["original_s"],1e-9)

    def test_missing_threshold_source_preserves_function_only(self):
        self.case.pop("threshold_sources")
        r=self.run_case()
        self.assertEqual(r["circuit_observation"],"functional_pass")
        self.assertEqual(r["timing_observation"],"threshold_unavailable")
        self.assertEqual(r["comparison"],"证据不足")
        self.assertTrue(any(i["kind"]=="threshold_source_missing" for i in r["issues"]))

    def test_missing_original_config_file_does_not_use_manifest_threshold(self):
        (self.root/self.case["threshold_sources"][0]["file"]).unlink()
        r=self.run_case()
        self.assertEqual(r["circuit_observation"],"functional_pass")
        self.assertEqual(r["timing_observation"],"threshold_unavailable")
        self.assertEqual(r["comparison"],"证据不足")

    def test_original_sources_disagree_is_condition_not_circuit(self):
        p=self.root/self.case["threshold_sources"][0]["file"]
        d=json.loads(p.read_text());d["threshold_ns"]=.8;p.write_text(json.dumps(d))
        r=self.run_case()
        self.assertEqual(r["conflict_categories"],["verification_condition"])
        self.assertEqual(r["timing_observation"],"condition_conflict")

    def test_manifest_hash_does_not_substitute_for_original_condition(self):
        self.case.pop("threshold_sources")
        self.case["manifest_sha256"]="a"*64
        self.case["sha256"]={k:v.io.sha(self.root/p) for k,p in self.case["files"].items()}
        r=self.run_case()
        self.assertEqual(r["evidence"]["threshold_binding"]["state"],"unavailable")

    def test_condition_source_from_other_run_rejected(self):
        spec=self.case["threshold_sources"][0]
        other=self.root/"other/run_config.json";other.parent.mkdir();other.write_text('{"threshold_ns":1}')
        spec["file"]="other/run_config.json"
        r=self.run_case()
        self.assertEqual(r["conflict_categories"],["verification_condition"])




if __name__=="__main__":
    unittest.main(verbosity=2)
