"""Acceptance-only guard: same HTTP handler/UI; block solver/experiment processes."""
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path.cwd();sys.path.insert(0,str(ROOT/'workbench/fusion'))
import server
import sample_verification as sv

out=ROOT/'outputs/sample_web_acceptance'
real_popen=subprocess.Popen
processes=[]
def guarded(argv,*args,**kwargs):
    name=Path(argv[0]).name if isinstance(argv,(list,tuple)) else str(argv)
    processes.append(dict(argv=list(argv) if isinstance(argv,(list,tuple)) else argv,allowed=name in ('git','lscpu')))
    sv.save(out/'process_guard.json',processes)
    if name not in ('git','lscpu'):raise RuntimeError('Offline browser acceptance forbids process: '+str(argv))
    return real_popen(argv,*args,**kwargs)
subprocess.Popen=guarded
jobs=server.LocalJobs(out/'jobs')
checks=jobs.checks();real_work=checks._work
ids=sv.load(out/'identities.json')
def work(folder,data,cases,root,event):
    if data['job']==ids['cancel']:
        v=sv.verifier(ROOT);real=v.verify;first=[True]
        def blocked(*a):
            if first[0]:
                first[0]=False
                event.wait(20)  # released by the actual cancel button; safety bound only
            return real(*a)
        v.verify=blocked
        try:return real_work(folder,data,cases,root,event)
        finally:v.verify=real
    return real_work(folder,data,cases,root,event)
checks._work=work
httpd=server.ThreadingHTTPServer(('127.0.0.1',8768),server.make_handler(jobs,'sample-check-browser-acceptance'))
print('Same Fusion UI/handler at http://127.0.0.1:8768; external processes restricted to git/lscpu; cancel SOFTWARE fixture uses an event barrier.',flush=True)
try:httpd.serve_forever()
finally:jobs.close();httpd.server_close()
