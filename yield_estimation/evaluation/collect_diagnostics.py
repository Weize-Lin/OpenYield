"""Collect completed or failed algorithm diagnostics; never rank them."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
from .compare_diagnostics import condition_mismatches


def collect(runs, output):
    output = output.resolve()
    sources, rows = [], []
    for run in runs:
        run = run.resolve()
        if output == run or run in output.parents:
            raise ValueError('Collection output must be separate from input runs')
        path = run / 'run_summary.json'
        if not path.exists():
            rows.append(dict(algorithm=run.name, status='missing_report', detail=str(path)))
            continue
        raw = path.read_bytes()
        s = json.loads(raw)
        sources.append(dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), summary=s))
        completed = s.get('status') in ('complete', 'budget_stopped', 'stopped')
        rows.append(dict(algorithm=s['algorithm'], status=s['status'], budget=s['budget'],
            submitted=s.get('simulator_submitted_samples', s.get('requested_samples')),
            returned=s.get('simulator_returned_samples', s.get('returned_rows')),
            auxiliary_writes=s.get('auxiliary_write_submitted',0),
            phase_samples=s.get('phase_samples',{}),
            numeric_status=s.get('diagnostics',{}).get('numeric_status','direct_MC'),
            pfail=s.get('pfail_estimate') if completed else None,
            log10_pfail=s.get('log10_pfail') if completed else None,
            warnings=s.get('sampling_diagnostics',{}).get('warnings',[]),
            detail=s.get('detail',s.get('error'))))
    differences = []
    if sources:
        base = sources[0]['summary']
        for item in sources:
            differing = condition_mismatches(base, item['summary'])
            if differing: differences.append(dict(path=item['path'], fields=differing))
    result = dict(kind='diagnostic_collection', ranking_eligible=False,
                  condition_mismatches=differences, rows=rows, sources=sources)
    output.mkdir(parents=True,exist_ok=False)
    (output/'comparison.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    fields=['algorithm','status','budget','submitted','returned','auxiliary_writes','numeric_status','pfail','log10_pfail','detail']
    with (output/'summary.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    lines=['# 多算法接入诊断汇总','','不是排名；进程结束不等于估计准确。', '',
           '| 算法 | 状态 | 实际提交/返回 | 辅助写 | 数值状态 | Pfail | log10(Pfail) |',
           '| --- | --- | --- | --- | --- | --- | --- |']
    for r in rows:
        lines.append(f"| {r['algorithm']} | {r['status']} | {r.get('submitted')}/{r.get('returned')} | {r.get('auxiliary_writes')} | {r.get('numeric_status')} | {r.get('pfail')} | {r.get('log10_pfail')} |")
    lines+=['',f'工况差异：{differences or "未发现"}。',
            'HSCS的辅助写只帮助选择抽样位置，计入成本，但不计入最终读估算的失败数。',
            '下溢、未进入估算、无法判定的仿真分别保留，不能当作真实失效率为零。',
            '初始化和复测消耗预算；不同算法实际估算条数不同。每个结果的算法设置、日志和原始数据保留在其目录内。']
    (output/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(dict(rows=rows,condition_mismatches=differences),indent=2,ensure_ascii=False))
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runs',type=Path,nargs='+',required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args()
    collect(a.runs,a.output_dir)

if __name__=='__main__':main()
