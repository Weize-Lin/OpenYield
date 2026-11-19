"""Exercise configurable startup/health/validation, without submitting experiments."""
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

root=Path.cwd()
sys.path.insert(0,str(root/'workbench/fusion'))
import server
with tempfile.TemporaryDirectory() as folder:
    source=server.LocalJobs(Path(folder)/'preflight')._check_runnable()
    assert source['git_tracked_files_modified'] is False
    env=dict(os.environ, OPENYIELD_PYTHON=sys.executable, OPENYIELD_REPOSITORY=str(root))
    log=Path(folder)/'startup.log'
    with log.open('w') as stream:
        process=subprocess.Popen(['bash','workbench/fusion/start.sh','--port','0','--output-root',folder+'/jobs'],
                                 env=env,stdout=stream,stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                try:
                    address=re.search(r'http://127\.0\.0\.1:\d+',log.read_text())
                    if not address:
                        raise OSError('server is starting')
                    base=address[0]
                    with urllib.request.urlopen(base+'/api/context',timeout=1) as response:
                        context=json.load(response)
                    break
                except OSError:
                    if process.poll() is not None:
                        raise RuntimeError(log.read_text())
                    time.sleep(.05)
            else:
                raise RuntimeError('startup did not become ready')
            assert context['revision']==source['git_revision']
            config=context['presets'][1]['config']
            request=urllib.request.Request(base+'/api/validate',data=json.dumps({'config':config}).encode(),
                headers={'Content-Type':'application/json','Origin':base,'X-OpenYield-Token':context['token']})
            with urllib.request.urlopen(request,timeout=5) as response:
                result=json.load(response)
            assert result['sample_cap']==4
            assert context['jobs']==[]
            assert not list((Path(folder)/'jobs').glob('*/launch*.json'))
            print('full startup: revision='+source['git_revision']+'; clean=True; validation_cap=4; launched=0')
        finally:
            process.terminate()
            process.wait(timeout=10)
    print(log.read_text())
print('No Xyce or algorithm process was launched.')
