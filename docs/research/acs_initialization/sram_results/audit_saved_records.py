"""Read saved records only; no solver, resampling, or production estimator calls."""
import argparse
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from scipy.special import logsumexp, log_ndtr
from scipy.stats import norm


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def log_target(x, means, sigma):
    value=norm.logpdf(x,means,sigma).sum(axis=1)-log_ndtr(means[1::3]/sigma[1::3]).sum()
    value[(x[:,1::3]<=0).any(axis=1)]=-np.inf
    return value


def audit(root, reference_root, output):
    output.mkdir(parents=True,exist_ok=False)
    config=json.loads((root/'experiment.json').read_text())
    threshold=config['experiment']['threshold_mv']*1e-3
    reference_failures=reference_samples=0
    for seed in (8311,8312,8313,8314):
        directory=reference_root/'mc'/f'seed_{seed}'/'mc'
        summary=json.loads((directory/'run_summary.json').read_text())
        values=list(csv.DictReader((directory/'samples.csv').open()))
        failed=sum(float(row['hold_snm_v'])<threshold for row in values)
        assert failed==summary['failure_count'] and len(values)==summary['returned_rows']==256
        reference_failures+=failed
        reference_samples+=len(values)
    reference=reference_failures/reference_samples
    tasks=[]
    batch_audits=[]
    density_audits=[]
    max_f_error=max_q_error=0.
    for seed in config['seeds']:
        for profile in config['estimators']:
            arm=profile['id']
            folder=root/arm/f'seed_{seed}'
            native=folder/'acs'
            summary=json.loads((native/'run_summary.json').read_text())
            execution=json.loads((folder/'execution.json').read_text())
            initialization=json.loads((native/'initialization.json').read_text())
            identity=initialization['identity']
            means,sigma=np.array(identity['means']),np.array(identity['sigma'])
            np.testing.assert_array_equal(means,summary['nominal_parameters'])
            np.testing.assert_array_equal(sigma,np.abs(means)*config['experiment']['relative_std'])
            assert summary['git_revision']=='56320b44784c3334af16645a430de1865bb41d9f'
            assert summary['git_tracked_files_modified'] is False
            samples=list(csv.DictReader((native/'samples.csv').open()))
            submitted=returned=attempted_rows=solver_calls=retry_calls=0
            phases=Counter()
            all_points=[]
            for path in sorted(native.glob('request_*.npz')):
                with np.load(path,allow_pickle=False) as record:
                    points=record['samples']
                    phase=str(record['phase'].item())
                    status=str(record['status'].item())
                    count=len(points)
                    batch=native/f'batch_{submitted:04d}'
                    np.testing.assert_array_equal(points,np.load(batch/'samples.npy'))
                    phases[phase]+=count
                    all_points.extend(points)
                    if status=='returned':
                        stored=np.array([float(row['hold_snm_v']) for row in samples[submitted:submitted+count]])
                        np.testing.assert_array_equal(record['delays'].ravel(),stored)
                        returned+=count
                    logs=[p for p in batch.rglob('*.log') if 'Welcome to the Xyce' in p.read_text(errors='replace')]
                    attempts=len(logs)
                    assert 1<=attempts<=3,(batch,attempts)
                    retries=[p for p in batch.iterdir() if p.is_dir() and '_attempt' in p.name]
                    assert attempts==1+len(retries)
                    solver_calls+=attempts
                    retry_calls+=len(retries)
                    attempted_rows+=count*attempts
                    batch_audits.append(dict(arm=arm,seed=seed,batch=batch.name,phase=phase,rows=count,
                                             solver_calls=attempts,retry_calls=len(retries),attempted_rows=count*attempts))
                    submitted+=count
            assert submitted==summary['simulator_submitted_samples']<=132
            assert returned==summary['simulator_returned_samples']==len(samples)
            assert dict(phases)==summary['phase_samples']
            init_points=np.array([row['parameters'] for row in initialization['candidates']])
            z=(init_points-means)/sigma
            radii=np.linalg.norm(z,axis=1)
            np.testing.assert_allclose(radii,[row['standardized_radius'] for row in initialization['candidates']],rtol=0,atol=1e-12)
            found=sum(float(row['measurement'])<threshold for row in initialization['candidates'])
            assert found==initialization['found_failed_candidates']
            log_contributions=[]
            formal_count=0
            formal_failures=0
            expected_centers=np.array([row['parameters'] for row in initialization['candidates'] if row['id'] in initialization['selection']['selected_ids']])
            for round_id,path in enumerate(sorted(native.glob('estimation_*.npz'))):
                with np.load(path,allow_pickle=False) as record:
                    assert str(record['status'].item())=='evaluated'
                    x=record['samples']
                    proposal=json.loads(str(record['proposal_json'].item()))
                    assert proposal['kind']=='diagonal_gaussian_mixture'
                    centers,variances,weights=(np.array(proposal[key]) for key in ('means','variances','weights'))
                    np.testing.assert_array_equal(centers,expected_centers)
                    beta=log_target(centers,means,sigma)
                    expected_beta=np.exp(beta-logsumexp(beta))
                    np.testing.assert_allclose(weights,expected_beta,rtol=1e-10,atol=0)
                    assert (variances>0).all()
                    np.testing.assert_array_equal(variances,np.full(variances.shape,float(np.float32(.00001))))
                    known_f=log_target(x,means,sigma)
                    with np.errstate(divide='ignore'):
                        known_q=logsumexp(norm.logpdf(x[:,None,:],centers[None,:,:],np.sqrt(variances)[None,:,:]).sum(axis=2)+np.log(weights),axis=1)
                    f_error=float(np.max(np.abs(known_f-record['log_f'])))
                    q_error=float(np.max(np.abs(known_q-record['log_g'])))
                    np.testing.assert_allclose(record['log_f'],known_f,rtol=0,atol=1e-10)
                    q_pass=bool(np.allclose(record['log_g'],known_q,rtol=0,atol=1e-10))
                    failure=record['delays']<threshold
                    np.testing.assert_array_equal(record['indicator'],failure)
                    assert record['support'].all()
                    log_contributions.extend((known_f-known_q)[failure].tolist())
                    formal_count+=len(x)
                    formal_failures+=int(failure.sum())
                    expected_centers=np.vstack((centers,x[failure]))
                    max_f_error=max(max_f_error,f_error)
                    max_q_error=max(max_q_error,q_error)
                    density_audits.append(dict(arm=arm,seed=seed,round=round_id,rows=len(x),
                                               components=len(weights),failures=int(failure.sum()),
                                               max_log_f_error=f_error,max_log_q_error=q_error,density_pass=q_pass))
            assert formal_count==summary['diagnostics']['estimation_draws_evaluated']==phases['estimation']
            independent=float(np.exp(logsumexp(log_contributions)-np.log(formal_count))) if formal_count and log_contributions else (0. if formal_count else None)
            probability=summary['pfail_estimate']
            if probability is None:
                assert independent is None
            # Keep the predeclared tolerance and record estimate differences below.
            tasks.append(dict(arm=arm,seed=seed,status=summary['status'],execution_state=execution['state'],
                              pfail=probability,independently_recomputed_pfail=independent,
                              estimate_relative_difference=(independent/probability-1) if probability else None,
                              estimate_replay_pass=(math.isclose(probability,independent,rel_tol=1e-10,abs_tol=0) if probability is not None else None),
                              absolute_error_vs_reference=abs(probability-reference) if probability is not None else None,
                              submitted=submitted,returned=returned,initialization=phases['initialization'],
                              recheck=phases['recheck'],estimation=formal_count,formal_failures=formal_failures,
                              candidate_failures=found,initial_radius_min=float(radii.min()),
                              initial_radius_mean=float(radii.mean()),initial_radius_max=float(radii.max()),
                              solver_calls=solver_calls,retry_calls=retry_calls,attempted_rows=attempted_rows,
                              distinct_parameter_vectors=len(np.unique(np.array(all_points),axis=0)),
                              wall_seconds=execution['wall_seconds'],warnings=';'.join(summary['sampling_diagnostics'].get('warnings',[]))))
    methods=[]
    for profile in config['estimators']:
        rows=[r for r in tasks if r['arm']==profile['id']]
        p=np.array([r['pfail'] for r in rows if r['pfail'] is not None])
        methods.append(dict(arm=profile['id'],planned=8,with_estimate=len(p),no_estimate_fraction=1-len(p)/8,
                            states=dict(Counter(r['status'] for r in rows)),mean_estimate=float(p.mean()) if len(p) else None,
                            conditional_mae=float(np.abs(p-reference).mean()) if len(p) else None,
                            conditional_rmse=float(np.sqrt(np.mean((p-reference)**2))) if len(p) else None,
                            submitted=sum(r['submitted'] for r in rows),returned=sum(r['returned'] for r in rows),
                            initialization=sum(r['initialization'] for r in rows),recheck=sum(r['recheck'] for r in rows),
                            estimation=sum(r['estimation'] for r in rows),retry_calls=sum(r['retry_calls'] for r in rows),
                            attempted_rows=sum(r['attempted_rows'] for r in rows),warning_runs=sum(bool(r['warnings']) for r in rows)))
    result=dict(tasks=16,states=dict(Counter(r['status'] for r in tasks)),reference_failures=reference_failures,
                reference_samples=reference_samples,reference_estimate=reference,methods=methods,
                actual_submitted=sum(r['submitted'] for r in tasks),actual_returned=sum(r['returned'] for r in tasks),
                solver_calls=sum(r['solver_calls'] for r in tasks),retry_calls=sum(r['retry_calls'] for r in tasks),
                solver_attempted_rows=sum(r['attempted_rows'] for r in tasks),max_log_f_error=max_f_error,max_log_q_error=max_q_error,
                formal_batches_audited=len(density_audits),formal_draws=sum(r['estimation'] for r in tasks),
                density_mismatch_batches=sum(not r['density_pass'] for r in density_audits),
                density_contract_passed=all(r['density_pass'] for r in density_audits),
                tolerance_log_density=1e-10,source_revision='56320b44784c3334af16645a430de1865bb41d9f')
    assert result['actual_submitted']<=2112 and result['solver_attempted_rows']<=6336
    for name,rows in [('tasks.csv',tasks),('batch_costs.csv',batch_audits),('density_checks.csv',density_audits)]:
        with (output/name).open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    (output/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--reference-root',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    result=audit(args.root,args.reference_root,args.output)
    raise SystemExit(0 if result['density_contract_passed'] else 2)
