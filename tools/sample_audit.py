"""Read-only evidence audit. Imports no OpenYield module and never invokes a solver.

The raster square check, voltage checks and event interpolation are independent
diagnostics, not a new production failure rule or a yield estimator.
"""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import random
import re

import numpy as np
from scipy.ndimage import distance_transform_cdt

ROLES = ('PGL', 'PGR', 'PDL', 'PUL', 'PDR', 'PUR')
PARAMS = ('vth0', 'u0', 'voff')


def load(p):
    return json.loads(Path(p).read_text(encoding='utf-8-sig'))


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def dump(p, value):
    Path(p).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def measures(path):
    result = {}
    if path.is_file():
        for line in path.read_text(errors='replace').splitlines():
            match = re.match(r'^\s*(\w+)\s*=\s*(\S+)', line)
            if match:
                result[match[1].upper()] = number(match[2])
    return result


def inventory(sources):
    runs = []; native_cases = []; unreadable_summaries = []
    for alias, base in sources:
        base = Path(base).resolve()
        for path in sorted(base.rglob('run_summary.json')):
            try:
                summary = load(path)
                config = load(path.parent/'run_config.json') if (path.parent/'run_config.json').is_file() else {}
            except (OSError, ValueError) as exc:
                unreadable_summaries.append(dict(path=str(path),error=str(exc)))
                continue
            meta = dict(config, **summary)
            op = meta.get('operation')
            if op not in ('hold_snm', 'read'):
                continue
            folder = path.parent
            c = meta.get('resolved_circuit', {})
            array = c.get('array', [meta.get('rows', 1), meta.get('cols', 1), False])
            threshold = meta.get('threshold_mv', 0)*1e-3 if op == 'hold_snm' else (
                meta.get('threshold_s') or meta.get('threshold_ns', 0)*1e-9)
            batches = {}
            for batch in sorted(folder.glob('batch_*')):
                if not batch.is_dir():
                    continue
                matrices = list(batch.glob('samples.npy'))
                try:
                    shape = list(np.load(matrices[0], mmap_mode='r', allow_pickle=False).shape) if matrices else None
                except (ValueError, OSError):
                    shape = None
                types = {'netlist': '*.sp', 'parameter_table': '*.data', 'raw_waveform': '*.prn',
                         'raw_measurement': '*.mt*', 'derived_measurement': '*.data.csv', 'plot': '*.png',
                         'solver_log': '*.log', 'snm_debug': '*.debug.csv'}
                files = {k:[dict(path=str(p), bytes=p.stat().st_size) for p in sorted(batch.glob(pattern)) if p.is_file()]
                         for k,pattern in types.items()}
                batches[batch.name] = dict(shape=shape, files=files)
            sample_path = folder/'samples.csv'
            if sample_path.is_file():
                with sample_path.open(newline='') as stream:
                    samples = list(csv.DictReader(stream))
                label_basis = 'saved_sample_labels'
            else:
                samples = []
                # Legacy files contain parameters and native measures, but no per-sample labels.
                for name,b in batches.items():
                    if not b['shape']:
                        continue
                    for index in range(b['shape'][0]):
                        p = next(iter((folder/name).glob(f'*.mt{index}')), None)
                        value = measures(p).get('TREAD_TOTAL') if p else None
                        samples.append(dict(sample=len(samples)+1,batch=name,xyce_run=index,
                                            read_delay_s=value,outcome=None))
                label_basis = 'legacy_raw_metric_only_no_saved_labels'
            relative = str(path.relative_to(base))
            signature = dict(operation=op, threshold=threshold, array=array,
                target=c.get('target',meta.get('target')), corner=c.get('corner',meta.get('corner')),
                vdd=c.get('vdd',meta.get('vdd')), temperature_c=c.get('temperature_c',meta.get('temperature_c')),
                cell_type=c.get('cell_type',meta.get('cell_type')), variation_mode=meta.get('variation_mode'),
                real_cell_mode=c.get('real_cell_mode'), interconnect=c.get('interconnect_config'),
                w_rc=c.get('w_rc'), relative_std=meta.get('relative_std'), u0_positive=meta.get('u0_positive'),
                sampling_source=meta.get('sampling_source'), nominal=meta.get('nominal_parameters_per_cell'),
                algorithm=meta.get('algorithm'), source=meta.get('git_revision'),
                profile=(meta.get('research') or {}).get('estimator'),
                legacy_strategy={k:v for k,v in config.items() if k.startswith('mnis_') or k in ('process','initial_failures','initial_batch','estimation_batch')})
            cohort = hashlib.sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest()[:16]
            for row in samples:
                row['id'] = alias+'/'+relative+'#'+str(row.get('sample'))
                field = 'hold_snm_v' if op == 'hold_snm' else 'read_delay_s'
                value = number(row.get(field))
                row['metric'] = value
                label = row.get('outcome')
                if not label and value is not None and threshold:
                    label = 'pass' if (value >= threshold if op == 'hold_snm' else value <= threshold) else 'reconstructed_performance_failure'
                row['selection_label'] = label or 'unknown'
                row['distance_to_limit'] = abs(value-threshold) if value is not None and threshold else None
            runs.append(dict(alias=alias,run=str(folder),relative=relative,summary_sha256=sha(path),
                samples_sha256=sha(sample_path) if sample_path.is_file() else None,
                operation=op,threshold=threshold,cohort=cohort,target_signature=signature,
                status=meta.get('status'),source_revision=meta.get('git_revision'),
                sample_label_basis=label_basis,samples=samples,batches=batches))
    # Native one-off compiler cases have summary.json instead of run_summary.json.
    # They may have no frozen failure threshold or explicit stochastic samples.
    for alias,base in sources:
        for path in sorted(Path(base).resolve().rglob('summary.json')):
            try:meta=load(path)
            except (OSError,ValueError) as exc:
                unreadable_summaries.append(dict(path=str(path),error=str(exc)));continue
            if not isinstance(meta,dict) or meta.get('operation') not in ('read','hold_snm'):continue
            artifacts=sorted({p for pattern in ('*.sp','*.prn','*.mt*','*.data','*.npy','*.png','*.log','*.csv')
                              for p in path.parent.glob(pattern) if p.is_file()})
            native_cases.append(dict(alias=alias,path=str(path),operation=meta['operation'],
                                     summary_sha256=sha(path),artifacts=[dict(path=str(p),bytes=p.stat().st_size) for p in artifacts],
                                     note='Native case; no sample labels or frozen threshold inferred. Not pooled with run_summary experiments.'))
    # Exact copied reports are not independent experiments; prefer the richest saved copy.
    seen = {}
    for run in sorted(runs,key=lambda r:(-sum(bool(b['files']['raw_waveform']) for b in r['batches'].values()),r['run'])):
        key = (run['summary_sha256'],run['samples_sha256'])
        run['duplicate_of'] = seen.get(key)
        seen.setdefault(key,run['run'])
    return dict(schema_version=1, sources=[dict(alias=a,path=str(Path(p).resolve())) for a,p in sources],runs=runs,native_cases=native_cases,unreadable_summaries=unreadable_summaries)


def select(data, scopes, seed, random_each, boundary_each, anomaly_cap):
    groups = defaultdict(list)
    for run in data['runs']:
        if run['duplicate_of'] or scopes and not any(s in run['run'] for s in scopes):
            continue
        for sample in run['samples']:
            groups[run['cohort']].append((run,sample))
    chosen, population = {}, []
    for key, pairs in sorted(groups.items()):
        pairs.sort(key=lambda x:x[1]['id'])
        rng = random.Random(int(hashlib.sha256(f'{seed}:{key}'.encode()).hexdigest(),16))
        strata = dict(pass_samples=[p for p in pairs if p[1]['selection_label']=='pass'],
                      failed_samples=[p for p in pairs if 'failure' in p[1]['selection_label']],
                      unknown=[p for p in pairs if p[1]['selection_label']=='unknown'])
        def add(pair, reason):
            run,sample=pair
            item=chosen.setdefault(sample['id'],dict(run=run['run'],id=sample['id'],sample=sample,reasons=[]))
            item['reasons'].append(reason)
        for stratum in ('pass_samples','failed_samples'):
            pool=strata[stratum]
            for pair in rng.sample(pool,min(random_each,len(pool))):add(pair,'random_'+stratum)
        bounded=[p for p in pairs if p[1]['distance_to_limit'] is not None]
        for pair in sorted(bounded,key=lambda p:(p[1]['distance_to_limit'],p[1]['id']))[:boundary_each]:add(pair,'nearest_frozen_threshold')
        for pair in strata['unknown'][:anomaly_cap]:add(pair,'missing_or_nonfinite_metric')
        population.append(dict(cohort=key,total=len(pairs),**{k:len(v) for k,v in strata.items()}))
    return dict(schema_version=1,seed=seed,scopes=scopes,random_per_saved_stratum=random_each,
                boundary_per_cohort=boundary_each,anomaly_cap_per_cohort=anomaly_cap,
                population=population,selected=list(chosen.values()),
                restriction='Diagnostic selection only; never use this selected set to estimate yield.')


def parse_prn(path, *, with_locations=False):
    """Independent strict text reader: header width and run index/time resets."""
    header=None;current=[];runs=[];last=None;locations=[];current_locations=[]
    for source_line,line in enumerate(path.read_text(errors='strict').splitlines(),1):
        parts=line.split()
        if not parts:continue
        if parts[0]=='Index' or parts[0] in ('TIME','{U}'):
            if header and parts!=header:raise ValueError('inconsistent repeated waveform header')
            header=parts;continue
        if line.startswith('End of Xyce'):continue
        if header is None:raise ValueError('missing waveform header')
        if len(parts)!=len(header):raise ValueError('waveform row width differs from header')
        values=[float(x) for x in parts]
        axis=values[0 if header[0]!='Index' else 1]
        if last is not None and (axis<last or header[0]=='Index' and values[0]==0 and current):
            runs.append(np.asarray(current));locations.append(current_locations);current=[];current_locations=[]
        if not np.isfinite(values).all():raise ValueError('nonfinite waveform row')
        current.append(values);current_locations.append(source_line);last=axis
    if current:runs.append(np.asarray(current));locations.append(current_locations)
    result=([x.upper() for x in header or []],runs)
    return (*result,locations) if with_locations else result


def crossing(t,y,level,rising=True,after=-math.inf):
    indices=np.flatnonzero(((y[:-1]<level)&(y[1:]>=level)) if rising else ((y[:-1]>level)&(y[1:]<=level)))
    for i in indices:
        value=t[i]+(level-y[i])*(t[i+1]-t[i])/(y[i+1]-y[i])
        if value>=after:return dict(value=float(value),lo=float(t[i]),hi=float(t[i+1]))
    return None


def polygon_square(vertices,step):
    """Rasterize polygon in original voltage axes; L-infinity erosion finds squares."""
    vertices=np.asarray(vertices)
    low=vertices.min(axis=0)-2*step;high=vertices.max(axis=0)+2*step
    shape=np.ceil((high-low)/step).astype(int)+1
    if max(shape)>5000:raise ValueError('curve geometry exceeds raster bounds')
    xs=low[0]+np.arange(shape[0])*step
    ys=low[1]+np.arange(shape[1])*step
    mask=np.zeros((len(ys),len(xs)),dtype=bool)
    a=vertices;b=np.roll(vertices,-1,axis=0)
    for j,y in enumerate(ys):
        use=((a[:,1]<=y)&(b[:,1]>y))|((b[:,1]<=y)&(a[:,1]>y))
        intersections=np.sort(a[use,0]+(y-a[use,1])*(b[use,0]-a[use,0])/(b[use,1]-a[use,1]))
        if len(intersections)%2:raise ValueError('open or ambiguous raster polygon')
        for left,right in zip(intersections[::2],intersections[1::2]):mask[j]|=(xs>=left)&(xs<=right)
    return float(2*distance_transform_cdt(mask,metric='chessboard').max()*step)


def geometric_snm(u,v1,v2,vdd):
    gap=v1-v2
    roots=list(u[gap==0])
    for i in np.flatnonzero(gap[:-1]*gap[1:]<0):roots.append(float(u[i]-gap[i]*(u[i+1]-u[i])/(gap[i+1]-gap[i])))
    roots=sorted(set(round(float(x),12) for x in roots))
    if len(roots)!=3:return dict(status='not_two_closed_lobes',crossings=roots)
    sizes=[]
    for left,right in zip(roots[:-1],roots[1:]):
        uu=np.r_[left,u[(u>left)&(u<right)],right]
        upper=np.c_[uu,np.interp(uu,u,v1)]
        lower=np.c_[uu[::-1],np.interp(uu[::-1],u,v2)]
        polygon=np.r_[upper,lower]
        xy=np.c_[(polygon[:,0]+polygon[:,1])/math.sqrt(2),(-polygon[:,0]+polygon[:,1])/math.sqrt(2)]
        fine=polygon_square(xy,vdd/800);coarse=polygon_square(xy,vdd/400)
        sizes.append(dict(fine=fine,coarse=coarse))
    value=min(x['fine'] for x in sizes)
    tolerance=max(vdd/200,max(abs(x['fine']-x['coarse']) for x in sizes))
    return dict(status='geometric_diagnostic',crossings=roots,lobes=sizes,volts=value,
                resolution_tolerance_v=tolerance,note='Raster diagnostic tolerance, not a rigorous physical error bound.')


def check_mapping(batch,deck,sample_index,meta,findings, *, matrix_path=None, table_path=None, resolve_include=None):
    matrix=Path(matrix_path) if matrix_path is not None else batch/'samples.npy'
    tables=[Path(table_path)] if table_path is not None else list(batch.glob('*.data'))
    if not matrix.is_file() or len(tables)!=1:
        findings.append(dict(kind='evidence_insufficient',detail='missing explicit parameter matrix or unique table'));return {}
    values=np.load(matrix,allow_pickle=False)
    parts=[line[1:].split() for line in tables[0].read_text().splitlines() if line.startswith('+')]
    if len(parts)<2:raise ValueError('empty parameter table')
    names=parts[0];table=np.asarray(parts[1:],dtype=float)
    if values.shape!=table.shape or sample_index>=len(table) or not np.isfinite(values).all():
        findings.append(dict(kind='input_mapping_problem',detail='matrix/table shape, row index or finite-value mismatch'));return {}
    matching=bool(np.allclose(values[sample_index],table[sample_index],rtol=2e-15,atol=1e-18))
    if not matching:findings.append(dict(kind='input_mapping_problem',detail='saved draw differs from actual .data export'))
    includes=re.findall(r'(?im)^\s*\.include\s+(.+)$',deck)
    referenced=[]
    for name in includes:
        target=Path(name.strip().strip('\"\''))
        if target.name!=tables[0].name:continue
        if resolve_include is not None:target=resolve_include(str(target))
        elif not target.is_absolute():target=batch/target
        referenced.append(target)
    if len(referenced)!=1 or not referenced[0].is_file():
        findings.append(dict(kind='evidence_insufficient',detail='saved deck table include cannot be resolved uniquely'))
    elif sha(referenced[0])!=sha(tables[0]):
        findings.append(dict(kind='input_mapping_problem',detail='deck includes different parameter-table bytes than audited sidecar'))
    expected=[]
    if meta.get('cell_type','SRAM_6T_CELL')!='SRAM_6T_CELL':
        findings.append(dict(kind='evidence_insufficient',detail='10T model-role binding not covered by this audit version'));return dict(table_matches=matching)
    rows,cols=meta.get('resolved_circuit',{}).get('array',[meta.get('rows',1),meta.get('cols',1)])[:2]
    for r in range(rows):
        for c in range(cols):
            for role in ROLES:
                for param in PARAMS:expected.append((param,role,r,c))
    labels=[];binding=0
    folded=re.sub(r'\n\s*\+',' ',deck)
    cards={m.group(1):m.group(2) for m in re.finditer(r'(?im)^\.model\s+(\S+)\s+([^\n]+)',folded)}
    for name in names:
        match=re.fullmatch(r'(vth0|u0|voff)_(.+)_(PGL|PGR|PDL|PUL|PDR|PUR)_(\d+)_(\d+)',name)
        if not match:labels.append(None);continue
        param,base,role,r,c=match.groups();labels.append((param,role,int(r),int(c)))
        model=f'{base}_{role}_{r}_{c}'
        if re.search(r'\b'+param+r"\s*=\s*['{]?"+re.escape(name)+r"['}]?",cards.get(model,'')) and re.search(r'(?im)^M'+role+r'\s+[^\n]*\b'+re.escape(model)+r'\b',deck):binding+=1
    if labels!=expected or binding!=len(names):findings.append(dict(kind='input_mapping_problem',detail='role/coordinate order or model-to-MOS bindings differ',bound=binding,total=len(names)))
    if np.any(values[sample_index,1::3]<=0):findings.append(dict(kind='input_mapping_problem',detail='nonpositive mobility in saved draw'))
    if np.any(table[sample_index,1::3]<=0):findings.append(dict(kind='input_mapping_problem',detail='nonpositive mobility in exported solver input'))
    return dict(table_matches=matching,shape=list(values.shape),model_bindings=binding,columns=len(names),
                max_export_absolute_difference=float(np.max(np.abs(values[sample_index]-table[sample_index]))),sample_sha256=sha(matrix))


def audit_one(run,sample,cache):
    folder=Path(run['run']);batch=folder/str(sample['batch']);index=int(sample['xyce_run'])
    meta=load(folder/'run_summary.json')
    if (folder/'run_config.json').is_file():meta=dict(load(folder/'run_config.json'),**meta)
    findings=[];out=dict(id=sample['id'],run=str(folder),sample=sample,findings=findings,independent_outcome='unknown')
    decks=list(batch.glob('*.sp'));traces=list(batch.glob('*.prn'))
    if len(decks)!=1:
        findings.append(dict(kind='evidence_insufficient',detail='missing or ambiguous saved netlist'));return out
    deck=decks[0].read_text()
    try:out['mapping']=check_mapping(batch,deck,index,meta,findings)
    except (OSError,ValueError,IndexError) as exc:
        findings.append(dict(kind='input_mapping_problem',detail='parameter input cannot be parsed or indexed: '+str(exc)))
    logs=list(batch.glob('*.log'))
    if not logs:findings.append(dict(kind='evidence_insufficient',detail='no final solver log'))
    for p in logs:
        text=p.read_text(errors='replace')
        if any(x in text for x in ('DC Operating Point Failed','Time step too small','Xyce timed out')):
            findings.append(dict(kind='solver_anomaly',detail=p.name+' reports numerical failure; retry provenance requires review'))
    if len(traces)!=1:
        findings.append(dict(kind='evidence_insufficient',detail='missing raw numerical waveform; a PNG or derived CSV is not a substitute'));return out
    key=str(traces[0])
    try:
        if key not in cache:cache[key]=parse_prn(traces[0])
    except (OSError,ValueError) as exc:
        findings.append(dict(kind='measurement_parse_error',detail='raw waveform parse: '+str(exc)))
        return out
    header,runs=cache[key]
    if index>=len(runs):
        findings.append(dict(kind='evidence_insufficient',detail='waveform run count is smaller than mapped sample index'))
        return out
    data=runs[index];columns={name:i for i,name in enumerate(header)}
    out['waveform']=dict(path=str(traces[0]),points=len(data),runs=len(runs),sha256=sha(traces[0]))
    if run['operation']=='hold_snm':
        if not all(k in columns for k in ('{U}','V(V1)','V(V2)')):raise ValueError('SNM waveform header missing required columns')
        if 'EV1 V1' not in deck or 'EV2 V2' not in deck:
            findings.append(dict(kind='evidence_insufficient',detail='rotated-curve netlist construction not recognized'));return out
        u=data[:,columns['{U}']]
        if not np.all(np.diff(u)>0):raise ValueError('nonmonotonic DC sweep inside one run')
        result=geometric_snm(u,data[:,columns['V(V1)']],data[:,columns['V(V2)']],meta.get('vdd',1.))
        out['independent_snm']=result
        if result['status']!='geometric_diagnostic':
            findings.append(dict(kind='evidence_insufficient',detail='two physically closed lobes not established; no endpoint fallback permitted'));return out
        value=result['volts'];tol=result['resolution_tolerance_v'];record=sample.get('metric')
        if record is not None and abs(record-value)>tol:findings.append(dict(kind='evidence_insufficient',detail='saved SNM disagrees with independent geometric diagnostic beyond raster tolerance; metric definition review required before attributing a parser bug'))
        if value+tol<run['threshold']:out['independent_outcome']='performance_failure'
        elif value-tol>=run['threshold']:out['independent_outcome']='performance_pass'
        else:findings.append(dict(kind='evidence_insufficient',detail='frozen threshold lies within raster diagnostic resolution band'))
        out['functional_scope']='DC margin only; does not certify transient hold functionality'
    else:
        metric=measures(next(iter(batch.glob(f'*.mt{index}')),Path('/nonexistent'))).get('TREAD_TOTAL')
        out['raw_delay_s']=metric
        if sample.get('metric') is not None and metric is not None and not math.isclose(sample['metric'],metric,rel_tol=1e-7,abs_tol=1e-16):findings.append(dict(kind='measurement_parse_error',detail='sample CSV differs from native TREAD_TOTAL'))
        t=data[:,columns['TIME']];vdd=float(meta.get('vdd',1.))
        event=re.search(r'(?im)^\.meas\s+TRAN\s+TREAD_TOTAL\s+TRIG\s+V\(WL_EN\)\s*=\s*([\deE+.-]+)\s+RISE=1\s+TARG\s+V\(OUT\)\s*=\s*([\deE+.-]+)\s+FALL=1\s*$',deck)
        if not event:
            findings.append(dict(kind='evidence_insufficient',detail='TREAD_TOTAL event definition is outside this audit scope'))
            return out
        trigger=crossing(t,data[:,columns['V(WL_EN)']],float(event[1]))
        target=crossing(t,data[:,columns['V(OUT)']],float(event[2]),rising=False,after=trigger['value'] if trigger else -math.inf)
        at=re.search(r'(?im)^\.meas\s+TRAN\s+VACCESS_ERROR_0[^\n]*\bAT=([\deE+.-]+)',deck)
        q=next((i for k,i in columns.items() if k.endswith(':Q)') and ':XSRAM_6T_CELL_' in k),None)
        qb=next((i for k,i in columns.items() if k.endswith(':QB)') and ':XSRAM_6T_CELL_' in k),None)
        if not at or q is None or qb is None:
            findings.append(dict(kind='evidence_insufficient',detail='target node or independent read deadline unavailable'));return out
        when=float(at[1])
        if when>t[-1]:findings.append(dict(kind='evidence_insufficient',detail='waveform ends before access deadline'));return out
        # Supported read stimulus stores Q=0; require that evidence in the actual deck.
        qname=header[q][2:-1]
        if not re.search(r'(?i)V\('+re.escape(qname)+r'\)\s*=\s*0(?:V|\s)',deck):
            findings.append(dict(kind='evidence_insufficient',detail='initial stored zero not proven by .ic; read-1 audit not implemented'));return out
        outv=float(np.interp(when,t,data[:,columns['V(OUT)']]))
        qv=float(np.interp(when,t,data[:,q]));qbv=float(np.interp(when,t,data[:,qb]))
        enabled=np.max(data[(t<when),columns['V(S_EN)']])>=.8*vdd
        out['read_state']=dict(deadline_s=when,q=qv,qb=qbv,out=outv,sense_enabled_before_deadline=bool(enabled),logic_low=.2*vdd,logic_high=.8*vdd)
        if not enabled:findings.append(dict(kind='evidence_insufficient',detail='sense-enable sequence not established'));return out
        if qv>.8*vdd or qbv<.2*vdd or outv>.8*vdd:
            out['independent_outcome']='functional_failure';return out
        if not(qv<.2*vdd and qbv>.8*vdd and outv<.2*vdd):
            findings.append(dict(kind='evidence_insufficient',detail='voltage lies between independent 20/80 logic bands'));return out
        if not trigger or not target:
            findings.append(dict(kind='measurement_parse_error',detail='native delay cannot be corroborated by stored edge samples'));return out
        lo=target['lo']-trigger['hi'];hi=target['hi']-trigger['lo']
        out['independent_delay']=dict(seconds=target['value']-trigger['value'],bracket_s=[lo,hi],trigger=trigger,target=target)
        if metric is None:findings.append(dict(kind='measurement_parse_error',detail='waveform shows completed read but native measure missing or FAILED'))
        elif not lo-1e-15<=metric<=hi+1e-15:findings.append(dict(kind='measurement_parse_error',detail='native measure outside raw waveform event bracket'))
        if lo>run['threshold']:out['independent_outcome']='performance_failure'
        elif hi<=run['threshold'] or math.isclose(hi,run['threshold'],rel_tol=8*np.finfo(float).eps,abs_tol=0.):out['independent_outcome']='performance_pass'
        else:findings.append(dict(kind='evidence_insufficient',detail='saved time grid cannot resolve this threshold boundary'))
    return out


def audit(data,selection):
    lookup={r['run']:r for r in data['runs']};result=[];cache={};files={}
    for item in selection['selected']:
        run=lookup[item['run']]
        if sha(Path(run['run'])/'run_summary.json')!=run['summary_sha256']:raise ValueError('inventory source summary changed')
        if run.get('samples_sha256') and sha(Path(run['run'])/'samples.csv')!=run['samples_sha256']:
            raise ValueError('inventory sample-label CSV changed')
        batch=Path(run['run'])/item['sample']['batch']
        for p in batch.iterdir() if batch.is_dir() else []:
            if p.is_file():files.setdefault(str(p),sha(p))
        try:row=audit_one(run,item['sample'],cache)
        except (OSError,ValueError,KeyError,IndexError,TypeError) as exc:
            row=dict(id=item['id'],run=item['run'],sample=item['sample'],independent_outcome='unknown',
                     findings=[dict(kind='evidence_insufficient' if isinstance(exc,(OSError,KeyError,IndexError)) else 'measurement_parse_error',detail=f'{type(exc).__name__}: {exc}')])
        meta=load(Path(run['run'])/'run_summary.json')
        if not meta.get('git_revision') or type(meta.get('git_tracked_files_modified')) is not bool:
            row['findings'].append(dict(kind='evidence_insufficient',detail='source revision or modification flag absent'))
        for path_key,hash_key in (('model_path','model_sha256'),('xyce','xyce_sha256')):
            path=Path(meta[path_key]) if meta.get(path_key) else None
            if path is None or not meta.get(hash_key) or not path.is_file():
                row['findings'].append(dict(kind='evidence_insufficient',detail=hash_key+' cannot be independently checked'))
            elif sha(path)!=meta[hash_key]:
                row['findings'].append(dict(kind='input_mapping_problem',detail=hash_key+' differs from saved source file'))
        row['selection_reasons']=item['reasons']
        kinds={f['kind'] for f in row['findings']}
        if row['independent_outcome']=='functional_failure':kinds.add('functional_failure')
        if row['independent_outcome']=='performance_failure':kinds.add('performance_failure')
        row['categories']=sorted(kinds) or ['corroborated_within_scope']
        result.append(row)
    assert all(sha(p)==digest for p,digest in files.items()),'Original evidence changed'
    return dict(schema_version=1,checked=len(result),categories=dict(Counter(k for r in result for k in r['categories'])),
                outcomes=dict(Counter(r['independent_outcome'] for r in result)),evidence_unchanged=True,
                evidence_sha256=files,results=result,yield_estimate=None,note='Selected diagnostics are not an independent MC population.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('inventory');p.add_argument('--source',action='append',required=True,help='alias=/saved/output/root');p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('select');p.add_argument('--inventory',type=Path,required=True);p.add_argument('--scope',action='append',default=[]);p.add_argument('--seed',type=int,default=20261122);p.add_argument('--random-each',type=int,default=2);p.add_argument('--boundary-each',type=int,default=2);p.add_argument('--anomaly-cap',type=int,default=2);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('check');p.add_argument('--inventory',type=Path,required=True);p.add_argument('--selection',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('Output exists; keep earlier evidence and choose a new output')
    if args.command=='inventory':value=inventory([s.split('=',1) for s in args.source])
    elif args.command=='select':
        if min(args.random_each,args.boundary_each,args.anomaly_cap)<0:parser.error('Selection quotas cannot be negative')
        value=select(load(args.inventory),args.scope,args.seed,args.random_each,args.boundary_each,args.anomaly_cap)
        value['inventory_sha256']=sha(args.inventory)
    else:
        selection=load(args.selection)
        if selection.get('inventory_sha256')!=sha(args.inventory):parser.error('Selection inventory hash mismatch')
        value=audit(load(args.inventory),selection)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    dump(args.output,value)
    print(json.dumps({k:v for k,v in value.items() if k in ('checked','categories','outcomes','evidence_unchanged')},ensure_ascii=False))
    print('output='+str(args.output))
    if 'runs' in value:print('runs='+str(len(value['runs']))+'; sample_rows='+str(sum(len(r['samples']) for r in value['runs'])))
    if 'selected' in value:print('selected='+str(len(value['selected']))+'; cohorts='+str(len(value['population'])))


if __name__=='__main__':main()
