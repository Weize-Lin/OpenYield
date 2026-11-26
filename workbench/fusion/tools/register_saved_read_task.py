"""Locally register existing evidence in Fusion. Does not solve, relabel or estimate yield.

Native runs use the normal shared analysis and automatic sample adapter. Historical
verifier manifests are explicitly marked as archive adapters, not Fusion executions.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import uuid

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'workbench/fusion'))
import sample_verification as sv


def register(output_root, name, run_directory=None, manifest=None):
    identifier=uuid.uuid4().hex
    folder=Path(output_root).resolve()/identifier
    folder.mkdir(parents=True,exist_ok=False)
    destination=folder/'saved_evidence'
    source=Path(run_directory).resolve() if run_directory else Path(manifest).resolve().parent
    if destination.is_relative_to(source): raise ValueError('不能把输出放在输入证据目录内。')
    # Archives with symlinks are not accepted; web reads cannot escape this copy.
    if any(p.is_symlink() for p in source.rglob('*')): raise ValueError('证据目录包含符号链接，先人工整理来源。')
    shutil.copytree(source,destination)
    origin=dict(source_directory=str(source),copied_files={str(p.relative_to(source)):sv.sha(p) for p in source.rglob('*') if p.is_file()},
        note='仅复制已有材料，没有运行仿真、生成原标签、补造退出码或推算良率。')
    if manifest:
        data=sv.load(manifest)
        data['adapter_note']='历史核验材料适配记录；原标签和条件来源按文件保留，不是工作台新执行的仿真。'
        sv.save(destination/'saved_read_verification.json',data)
        cases,_=sv.index_run(destination)
        row=dict(estimator='已保存材料（适配）',seed=None,status='saved_evidence_only',pfail_estimate=None,
            estimate_status='no_numeric_estimate',source=dict(summary=None),cost={},warnings=[],
            detail='仅检查已有材料；不产生、合并或修改良率和排名。')
        analysis=dict(manifest_path=str(destination/Path(manifest).name),groups=[dict(name=name,runs=[row],methods=[],reference={},checks=dict(total_runs=1))])
    else:
        sys.path.insert(0,str(ROOT))
        from yield_estimation.evaluation.analyze_results import analyze
        summary=sv.load(destination/'run_summary.json')
        estimator=summary.get('research',{}).get('estimator',{}).get('id')
        if not estimator or type(summary.get('seed')) is not int:
            raise ValueError('原生摘要缺少方法身份或种子，不能冒充正常分析任务。可使用明确的历史适配清单。')
        sv.save(folder/'analysis_manifest.json',dict(schema_version=1,groups=[dict(id='saved-run',name=name,runs=[dict(run='saved_evidence',estimator=estimator,seed=summary['seed'])])]))
        analysis=analyze(folder/'analysis_manifest.json',folder/'analysis')
        cases,_=sv.index_run(destination)
    job=dict(id=identifier,name=name,status='complete',source='imported',offline=True,
        kind='saved_evidence_registration',directory=str(folder),created_at=datetime.now(timezone.utc).isoformat(),
        plan_count=None,config=None,references=[],manifest=analysis['manifest_path'],
        registration='registration.json')
    if manifest:job['saved_evidence_runs']={'0/0':'saved_evidence'}
    sv.save(folder/'registration.json',origin)
    (folder/'analysis').mkdir(exist_ok=True)
    sv.save(folder/'analysis/analysis.json',analysis)
    sv.save(folder/'job.json',job)
    print(json.dumps(dict(job=identifier,directory=str(folder),samples=[c['id'] for c in cases],new_solver_calls=0),ensure_ascii=False))
    return identifier


def main():
    p=argparse.ArgumentParser(description=__doc__)
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--run-directory',type=Path)
    g.add_argument('--manifest',type=Path)
    p.add_argument('--output-root',type=Path,required=True)
    p.add_argument('--name',required=True)
    a=p.parse_args();register(a.output_root,a.name,a.run_directory,a.manifest)


if __name__=='__main__':main()
