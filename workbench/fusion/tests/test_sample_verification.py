"""Public offline contracts using explicitly derived software fixtures; no circuit runs."""
import copy
import hashlib
import http.client
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'workbench/fusion'))
sys.path.insert(0,str(ROOT/'workbench/fusion/tools'))
import server
import sample_verification as sv
from register_saved_read_task import register


class SampleWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.source=self.root/'input';self.source.mkdir()
        with zipfile.ZipFile(ROOT/'workbench/fusion/tests/fixtures/historical_read_contracts.zip') as z:z.extractall(self.source)
        self.output=self.root/'jobs'
        self.identifier=register(self.output,'历史材料测试',manifest=self.source/'manifest.json')
        self.jobs=server.LocalJobs(self.output);self.addCleanup(self.jobs.close)
        self.checks=self.jobs.checks()

    def start(self,selected=None):
        check=self.checks.start(self.identifier,0,0,selected)
        self.checks.threads[check['id']].join(30)
        data=self.checks.snapshot(self.identifier,check['id'])
        self.assertEqual(data['status'],'complete',data.get('reason'))
        return data

    def test_saved_five_match_cli_and_preserve_originals(self):
        v=sv.verifier(ROOT);folder=self.checks.directory(self.identifier,0,0)
        cases,_=sv.index_run(folder)
        expected=[v.verify(c,folder,{}) for c in cases]
        with patch('subprocess.Popen',side_effect=AssertionError('no external process during checks')):
            # source_snapshot uses git for identity; it is explicitly allowed before checks.
            with patch.object(self.jobs,'verification_source',return_value={'git_revision':'fixture'}):
                data=self.start()
        self.assertEqual(data['results'],expected)
        self.assertTrue(data['evidence_unchanged'])
        self.assertEqual(data['new_solver_calls'],0)

    def test_multiple_segments_select_requested_sample(self):
        cases,_=sv.index_run(self.checks.directory(self.identifier,0,0))
        data=self.start([cases[4]['id']]);wave=self.checks.wave(self.identifier,data['id'],cases[4]['id'])
        self.assertEqual((wave['segment'],wave['total_segments']),(2,3))
        self.assertEqual(wave['signals'][2]['role'],'out')
        self.assertLessEqual(wave['display_points'],wave['original_points'])

    def test_threshold_conflict_is_condition_not_circuit(self):
        folder=self.checks.directory(self.identifier,0,0);p=folder/'saved_read_verification.json';m=sv.load(p)
        m['cases'][2]['threshold_s']=.8e-9;sv.save(p,m)
        result=self.start([m['cases'][2]['id']])['results'][0]
        self.assertIn('verification_condition',result['conflict_categories'])
        self.assertEqual(result['comparison'],'存在冲突')

    def test_missing_threshold_preserves_function_observation(self):
        folder=self.checks.directory(self.identifier,0,0);p=folder/'saved_read_verification.json';m=sv.load(p)
        m['cases'][2]['threshold_sources']=[];sv.save(p,m)
        r=self.start([m['cases'][2]['id']])['results'][0]
        self.assertEqual(r['timing_observation'],'threshold_unavailable')
        self.assertEqual(r['circuit_observation'],'functional_pass')
        self.assertEqual(r['comparison'],'证据不足')

    def test_native_run_automatic_adapter_without_special_manifest(self):
        # This control is a saved native run; no archival manifest is supplied.
        ident=register(self.output,'普通原生记录',run_directory=self.source/'control')
        jobs=server.LocalJobs(self.output);self.addCleanup(jobs.close)
        checks=jobs.checks();available=checks.availability(ident,0,0)
        self.assertEqual(available['origin']['kind'],'saved_run')
        c=checks.start(ident,0,0,['1']);checks.threads[c['id']].join(20)
        result=checks.snapshot(ident,c['id'])
        self.assertEqual(result['status'],'complete')
        self.assertEqual(result['results'][0]['original']['label'],'pass')

    def test_native_basename_alone_cannot_prove_old_table_path(self):
        ident=register(self.output,'旧绝对路径不可推断',run_directory=self.source/'control')
        jobs=server.LocalJobs(self.output);self.addCleanup(jobs.close)
        folder=jobs._run(ident,0,0)[2]
        (folder.parent/'registration.json').unlink()
        cases,_=sv.index_run(folder)
        self.assertEqual(cases[0]['include_aliases'],{})

    def test_native_derived_fixtures_match_original_observation_projection(self):
        root=self.root/'two'
        with zipfile.ZipFile(ROOT/'workbench/fusion/tests/fixtures/native_read_contracts.zip') as z:z.extractall(root)
        original=sv.load(ROOT/'workbench/fusion/tests/fixtures/native_expected.json')['results']
        for point,expected in zip(('control_4','suspected_38'),original):
            ident=register(self.output,point,run_directory=root/'raw/execution'/point)
            jobs=server.LocalJobs(self.output);self.addCleanup(jobs.close);checks=jobs.checks()
            c=checks.start(ident,0,0);checks.threads[c['id']].join(30)
            r=checks.snapshot(ident,c['id'])['results'][0]
            self.assertEqual(r['comparison'],expected['comparison'])
            self.assertEqual(r['circuit_observation'],expected['circuit_observation'])
            self.assertEqual(r['timing_observation'],expected['timing_observation'])
            self.assertEqual(r['evidence']['observation'],expected['evidence']['observation'])
            self.assertEqual(r['evidence']['simulation'],expected['evidence']['simulation'])

    def test_missing_labels_cannot_start(self):
        folder=self.checks.directory(self.identifier,0,0)
        (folder/'saved_read_verification.json').unlink()
        self.assertEqual(self.checks.availability(self.identifier,0,0)['samples'],[])
        with self.assertRaises(ValueError):self.checks.start(self.identifier,0,0)

    def test_unsupported_and_missing_wave_not_circuit_fail(self):
        folder=self.checks.directory(self.identifier,0,0);p=folder/'saved_read_verification.json';m=sv.load(p)
        m['cases'][2]['contract']['rows']=10
        (folder/m['cases'][3]['files']['waveform']).unlink();sv.save(p,m)
        data=self.start([m['cases'][2]['id'],m['cases'][3]['id']])
        self.assertEqual([r['comparison'] for r in data['results']],['不支持','证据不足'])
        with self.assertRaisesRegex(ValueError,'波形段'):self.checks.wave(self.identifier,data['id'],m['cases'][3]['id'])

    def test_cancel_keeps_completed_and_not_started(self):
        v=sv.verifier(ROOT);real=v.verify;entered=threading.Event();release=threading.Event()
        def blocked(*args):entered.set();release.wait(10);return real(*args)
        with patch.object(v,'verify',side_effect=blocked):
            c=self.checks.start(self.identifier,0,0)
            self.assertTrue(entered.wait(10));self.checks.cancel(self.identifier,c['id']);release.set()
            self.checks.threads[c['id']].join(30)
        data=self.checks.snapshot(self.identifier,c['id'])
        self.assertEqual(data['status'],'cancelled')
        self.assertEqual(len(data['results']),1)
        self.assertEqual(sum(i['status']=='not_started_after_cancelled' for i in data['items']),4)

    def test_failed_retains_partial_records(self):
        v=sv.verifier(ROOT);real=v.verify;calls=[]
        def broken(*a):
            calls.append(1)
            if len(calls)>1:raise RuntimeError('software fixture failure')
            return real(*a)
        with patch.object(v,'verify',side_effect=broken):
            c=self.checks.start(self.identifier,0,0);self.checks.threads[c['id']].join(30)
        data=self.checks.snapshot(self.identifier,c['id'])
        self.assertEqual(data['status'],'failed');self.assertEqual(len(data['results']),1)
        self.assertEqual(len(data['items']),5)

    def test_restart_does_not_report_complete(self):
        directory=Path(self.jobs.snapshot(self.identifier)['directory'])/'sample_checks'/('a'*32);directory.mkdir(parents=True)
        sv.save(directory/'check.json',dict(status='processing',items=[dict(id='1',status='not_started')]))
        jobs=server.LocalJobs(self.output);self.addCleanup(jobs.close);jobs.checks()
        data=sv.load(directory/'check.json');self.assertEqual(data['status'],'interrupted')

    def test_no_arbitrary_paths_or_symlink_reads(self):
        folder=self.checks.directory(self.identifier,0,0);p=folder/'saved_read_verification.json';m=sv.load(p)
        m['cases'][0]['files']['deck']='../../outside';sv.save(p,m)
        with self.assertRaises(ValueError):self.checks.availability(self.identifier,0,0)
        with self.assertRaises(ValueError):self.checks.download(self.identifier,'../x','check.json')
        with self.assertRaises(ValueError):self.checks.download(self.identifier,'a'*32,'../../job.json')

    def test_repeated_checks_do_not_overwrite(self):
        a=self.start();b=self.start()
        self.assertNotEqual(a['id'],b['id']);self.assertEqual(len(self.checks.history(self.identifier,0,0)),2)
        for name in ('samples.csv','check.json','evidence.json','input_hashes.json'):
            self.assertTrue(self.checks.download(self.identifier,a['id'],name).is_file())


    def native_fixture(self):
        root=self.root/'native'
        with zipfile.ZipFile(ROOT/'workbench/fusion/tests/fixtures/native_read_contracts.zip') as z:z.extractall(root)
        return root/'raw/execution/control_4'

    def test_original_batch_requirement_rejects_changed_config_and_summary(self):
        folder=self.native_fixture();v=sv.verifier(ROOT)
        cases,_=sv.index_run(folder);before=v.verify(cases[0],folder,{})
        self.assertEqual(before['comparison'],'支持原判断')
        for name in ('run_config.json','run_summary.json'):
            p=folder/name;d=sv.load(p)
            for key,value in (('threshold_ns',.8),('threshold_s',.8e-9),('timing_limit_s',.8e-9)):
                if key in d:d[key]=value
            sv.save(p,d)
        cases,_=sv.index_run(folder);result=v.verify(cases[0],folder,{})
        self.assertEqual(result['comparison'],'存在冲突')
        self.assertEqual(result['conflict_categories'],['verification_condition'])
        self.assertEqual(result['timing_observation'],'condition_conflict')
        self.assertEqual(result['original']['label'],'pass')

    def test_saved_batch_judgment_conflict_preserves_current_original_label(self):
        folder=self.native_fixture();p=folder/'samples.csv'
        import csv
        with p.open() as f:rows=list(csv.DictReader(f))
        rows[0]['outcome']='timing_failure'
        with p.open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        cases,_=sv.index_run(folder);r=sv.verifier(ROOT).verify(cases[0],folder,{})
        self.assertEqual(r['original']['label'],'timing_failure')
        self.assertEqual(r['comparison'],'存在冲突')
        self.assertIn('input_identity',r['conflict_categories'])

    def test_saved_batch_measurement_hash_is_checked(self):
        folder=self.native_fixture();cases,_=sv.index_run(folder)
        p=folder/cases[0]['files']['measurement'];p.write_text(p.read_text()+'\n')
        r=sv.verifier(ROOT).verify(cases[0],folder,{})
        self.assertEqual(r['comparison'],'存在冲突')
        self.assertIn('measurement',r['conflict_categories'])

    def test_saved_batch_row_mapping_is_not_ignored(self):
        folder=self.native_fixture();p=folder/'batch_0000/batch_evidence.json';d=sv.load(p)
        d['samples'][0]['waveform_segment']=1;sv.save(p,d)
        cases,_=sv.index_run(folder);r=sv.verifier(ROOT).verify(cases[0],folder,{})
        self.assertEqual(r['comparison'],'存在冲突')
        self.assertIn('input_identity',r['conflict_categories'])

    def test_unfinalized_recording_is_insufficient_not_support(self):
        folder=self.native_fixture();p=folder/'batch_0000/batch_evidence.json';d=sv.load(p)
        d['state']='submitted';sv.save(p,d)
        cases,_=sv.index_run(folder);r=sv.verifier(ROOT).verify(cases[0],folder,{})
        self.assertEqual(r['comparison'],'证据不足')

    def test_solver_record_change_cannot_retain_support(self):
        folder=self.native_fixture();cases,_=sv.index_run(folder)
        p=folder/cases[0]['solver_exit']['file'];p.write_text(p.read_text()+'\n')
        cases,_=sv.index_run(folder);r=sv.verifier(ROOT).verify(cases[0],folder,{})
        self.assertEqual(r['comparison'],'存在冲突')
        self.assertIn('input_identity',r['conflict_categories'])

    def test_symlinked_check_directory_and_files_cannot_be_read(self):
        folder=Path(self.jobs.snapshot(self.identifier)['directory'])/'sample_checks';folder.mkdir()
        outside=self.root/'outside';outside.mkdir();(outside/'check.json').write_text('{"private":true}')
        check='b'*32;(folder/check).symlink_to(outside,target_is_directory=True)
        with self.assertRaises(ValueError):self.checks.snapshot(self.identifier,check)
        with self.assertRaises(ValueError):self.checks.download(self.identifier,check,'check.json')
        with self.assertRaises(ValueError):self.checks.history(self.identifier,0,0)
        (folder/check).unlink();(folder/check).mkdir()
        (folder/check/'check.json').symlink_to(outside/'check.json')
        with self.assertRaises(ValueError):self.checks.snapshot(self.identifier,check)
        with self.assertRaises(ValueError):self.checks.download(self.identifier,check,'check.json')

    def test_metadata_symlinks_are_rejected_before_read(self):
        folder=self.native_fixture()
        for name in ('run_config.json','samples.csv','batch_0000/batch_evidence.json','batch_0000/mc_read_3x2_rc1_tb.sp'):
            with self.subTest(name=name):
                p=folder/name;contents=p.read_bytes();outside=self.root/'outside-meta';outside.write_bytes(contents)
                p.unlink();p.symlink_to(outside)
                with self.assertRaises(ValueError):sv.index_run(folder)
                p.unlink();p.write_bytes(contents)
        attached=self.checks.directory(self.identifier,0,0)/'saved_read_verification.json'
        outside=self.root/'outside-manifest';outside.write_bytes(attached.read_bytes());attached.unlink();attached.symlink_to(outside)
        with self.assertRaises(ValueError):sv.index_run(attached.parent)

    def test_restart_rejects_symlinked_records_without_writing_outside(self):
        folder=Path(self.jobs.snapshot(self.identifier)['directory'])/'sample_checks';folder.mkdir()
        outside=self.root/'outside';outside.mkdir();p=outside/'check.json'
        contents='{"status":"processing","items":[]}'
        p.write_text(contents);(folder/('c'*32)).symlink_to(outside,target_is_directory=True)
        with self.assertRaises(ValueError):sv.SampleChecks(self.jobs,ROOT)
        self.assertEqual(p.read_text(),contents)



    def test_wave_rejects_check_input_root_from_another_task(self):
        cases,_=sv.index_run(self.checks.directory(self.identifier,0,0))
        data=self.start([cases[4]['id']])
        p=self.checks.folder(self.identifier,data['id'])/'check.json'
        d=sv.load(p);d['input_root']=str(self.root);sv.save(p,d)
        with self.assertRaisesRegex(ValueError,'所属任务'):
            self.checks.wave(self.identifier,data['id'],cases[4]['id'])


    def test_missing_measurement_and_solver_metadata_stay_insufficient(self):
        folder=self.native_fixture();p=folder/'batch_0000/batch_evidence.json';d=sv.load(p)
        d['solver']=None;d['samples'][0]['native_measurement']=None;d['state']='submitted'
        sv.save(p,d)
        (folder/'batch_0000/mc_read_3x2_rc1_tb.sp.mt0').unlink()
        cases,_=sv.index_run(folder);r=sv.verifier(ROOT).verify(cases[0],folder,{})
        self.assertEqual(r['comparison'],'证据不足')
        self.assertTrue(any(i['kind']=='evidence_missing' for i in r['issues']))


if __name__=='__main__':unittest.main()
