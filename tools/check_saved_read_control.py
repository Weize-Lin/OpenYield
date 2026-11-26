"""Read saved evidence only. Does not import a circuit backend or launch a solver."""
import argparse
import json
from pathlib import Path
import re

import sample_audit as audit


def instance_pins(lines, name, definitions=None):
    definitions=lines if definitions is None else definitions
    matches = [(i, line.split()) for i, line in enumerate(lines, 1)
               if line.split() and line.split()[0].upper() == name.upper()]
    if len(matches) != 1:
        raise ValueError('Expected one instance: '+name)
    line, fields = matches[0]
    definition = next((i for i, text in enumerate(definitions)
                       if text.upper().startswith('.SUBCKT '+fields[-1].upper()+' ')), None)
    if definition is None:
        raise ValueError('Missing subcircuit: '+fields[-1])
    formal = definitions[definition].split()[2:]
    if len(formal) != len(fields[1:-1]):
        raise ValueError('Subcircuit pin count mismatch: '+name)
    depth=1;stop=None
    for i in range(definition+1,len(definitions)):
        command=definitions[i].strip().split()
        if command and command[0].upper()=='.SUBCKT':depth+=1
        if command and command[0].upper()=='.ENDS':depth-=1
        if depth==0:stop=i;break
    if stop is None:raise ValueError('Unclosed subcircuit: '+fields[-1])
    return dict(instance=name,line=line,definition_line=definition+1,
                definition=fields[-1],pins=dict(zip(formal,fields[1:-1])),
                body=definitions[definition+1:stop])


def node_map(deck, trace):
    lines=deck.read_text().splitlines()
    bindings={name:instance_pins(lines,name) for name in
              ['XPRECHARGE_1','XSENSEAMP_1','XWL_DRV_1','XWRITEDRIVER_1','XD_LATCH','XSRAM_6T_CORE_3x2']}
    core=bindings['XSRAM_6T_CORE_3x2']
    cell=instance_pins(lines,'XSRAM_6T_CELL_1_1')
    bindings['XSRAM_6T_CORE_3x2:XSRAM_6T_CELL_1_1']=cell
    header,_=audit.parse_prn(trace)
    prefix='XSRAM_6T_CORE_3x2:'
    roles=[
        ('参考与外部操作',['VDD','VSS','CLK','CSB','WEB','A_DFF0','A_DFF1']),
        ('预充电控制及到达端',['PRE',bindings['XPRECHARGE_1']['pins']['ENB'],'XPRECHARGE_1:ENB_end']),
        ('译码与目标字线',['DEC_WL1','WL_EN',bindings['XWL_DRV_1']['pins']['B'],'WL1',prefix+cell['pins']['WL']]),
        ('感放使能与隔离',['S_EN',bindings['XSENSEAMP_1']['pins']['EN'],'XSENSEAMP_1:EN_end',
                          'SA_ISO',bindings['XSENSEAMP_1']['pins']['ISO'],'XSENSEAMP_1:ISO_end']),
        ('目标单元位线',[prefix+cell['pins']['BL'],prefix+cell['pins']['BLB']]),
        ('感放入口位线',[bindings['XSENSEAMP_1']['pins']['IN'],bindings['XSENSEAMP_1']['pins']['INB'],
                        'XSENSEAMP_1:IN_end','XSENSEAMP_1:INB_end']),
        ('预充电器位线',[bindings['XPRECHARGE_1']['pins']['BL'],bindings['XPRECHARGE_1']['pins']['BLB'],
                        'XPRECHARGE_1:BL_end','XPRECHARGE_1:BLB_end']),
        ('目标存储状态',[prefix+'XSRAM_6T_CELL_1_1:'+n for n in ['Q','QB','Q_end','QB_end']]),
        ('感放输出与输出锁存',[bindings['XSENSEAMP_1']['pins']['Q'],bindings['XSENSEAMP_1']['pins']['QB'],
                              bindings['XD_LATCH']['pins']['D'],bindings['XD_LATCH']['pins']['EN'],'OUT','OUT_B']),
        ('非预期写控制',['W_EN',bindings['XWRITEDRIVER_1']['pins']['EN'],'XWRITEDRIVER_1:EN_end']),
    ]
    # Resolve hierarchy against the actual instance/subcircuit body, not a label on a plot.
    def declared(node):
        pieces=node.split(':');body=lines
        for part in pieces[:-1]:
            body=instance_pins(body,part,definitions=lines)['body']
        name=pieces[-1].upper()
        return any(name in line.upper().split()[1:] for line in body
                   if line and not line.lstrip().startswith(('.', '*')))
    signals=[]
    for role,nodes in roles:
        for node in dict.fromkeys(nodes):
            exists=declared(node)
            if not exists:
                raise ValueError('Node not established by saved deck: '+node)
            signals.append(dict(role=role,node=node,declared_in_deck=exists,
                                present_in_saved_prn=('V('+node.upper()+')') in header))
    return dict(deck=str(deck),deck_sha256=audit.sha(deck),signals=signals,
                instance_bindings={name:{k:v for k,v in record.items() if k!='body'} for name,record in bindings.items()})


def historical_provenance(folder):
    sidecar=next(folder.glob('*.variation.json'))
    data=audit.load(sidecar);deck=next(folder.glob('*.sp'))
    includes=re.findall(r'(?im)^\.include\s+(.+)$',deck.read_text())
    files=[]
    for path in includes:
        p=Path(path.strip().strip('"\''))
        if not p.is_absolute():p=folder/p
        files.append(dict(path=str(p),readable=p.is_file(),current_sha256=audit.sha(p) if p.is_file() else None))
    logs='\n'.join(p.read_text(errors='replace') for p in folder.glob('*.log'))
    version=re.search(r'^\*+ This is version (.+)$',logs,re.M)
    model=next((p for p in files if p['path'].endswith('.spice')),None)
    return dict(sidecar=str(sidecar),sidecar_sha256=audit.sha(sidecar),
                saved_model_sha256=data.get('model_sha256'),
                model_matches_saved_record=bool(model and model['current_sha256']==data.get('model_sha256')),
                saved_solver_path=data.get('xyce'),saved_solver_version_from_log=version[1] if version else None,
                historical_solver_binary_sha256=data.get('xyce_sha256'),historical_source_commit=data.get('git_revision'),
                includes=files,
                restriction='Recorded model hash and version string do not reconstruct missing historical binary or source identity.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--control-run',type=Path,required=True)
    p.add_argument('--legacy-run',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.exists():p.error('Output exists; preserve it and choose another path')
    inventory=audit.inventory([('control',args.control_run)])
    if len(inventory['runs'])!=1:p.error('Expected exactly one saved control run')
    run=inventory['runs'][0]
    if run['operation']!='read':p.error('Control must be a read run')
    chosen=min((r for r in run['samples'] if r.get('outcome')=='pass'),key=lambda r:int(r['sample']))
    result=audit.audit(inventory,dict(selected=[dict(run=run['run'],id=chosen['id'],sample=chosen,
                     reasons=['first_saved_pass_in_fixed_evidence_complete_run'])]))
    control=result['results'][0]
    if control['categories']!=['corroborated_within_scope'] or control['independent_outcome']!='performance_pass':
        raise ValueError('Control failed independent rules; retain failure, do not substitute another point')
    meta=audit.load(args.control_run/'run_summary.json')
    if meta.get('git_tracked_files_modified') is not False:
        raise ValueError('Control source was modified or has no saved modification flag')
    result['control_source']={k:meta.get(k) for k in ['git_revision','git_tracked_files_modified','model_path','model_sha256',
                                                   'xyce','xyce_version','xyce_sha256','threshold_ns','resolved_circuit']}
    result['node_maps']={}
    sources={'legacy38':args.legacy_run/'batch_0037','legacy19':args.legacy_run/'batch_0018',
             'success_control':args.control_run/chosen['batch']}
    result['historical_provenance']={}
    evidence={}
    for name,folder in sources.items():
        for file in folder.iterdir():
            if file.is_file():evidence[str(file)]=audit.sha(file)
        result['node_maps'][name]=node_map(next(folder.glob('*.sp')),next(folder.glob('*.prn')))
        if name!='success_control':result['historical_provenance'][name]=historical_provenance(folder)
    assert all(audit.sha(path)==value for path,value in evidence.items()),'Saved evidence changed'
    result['inspected_file_sha256']=evidence
    result['no_new_xyce_calls']=True
    result['same_rules_not_same_physical_experiment']=True
    result['restriction']='Read-control diagnostic only; retain each original threshold, do not pool samples or infer a failed transistor.'
    args.output.parent.mkdir(parents=True,exist_ok=True)
    audit.dump(args.output,result)
    print('control_sample='+str(chosen['sample'])+'; outcome='+control['independent_outcome'])
    print('native_delay_ns='+str(control['raw_delay_s']*1e9))
    print('independent_delay_bracket_ns='+str([x*1e9 for x in control['independent_delay']['bracket_s']]))
    print('read_state='+json.dumps(control['read_state'],ensure_ascii=False))
    print('evidence_unchanged=true; no_new_xyce_calls=true')
    print('output='+str(args.output))


if __name__=='__main__':main()
