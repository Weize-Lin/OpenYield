"""Materialize derived SOFTWARE fixtures only; never new circuit evidence."""
import copy
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT=Path.cwd()
sys.path.insert(0,str(ROOT/'workbench/fusion/tools'))
from register_saved_read_task import register
import sample_verification as sv

out=ROOT/'outputs/sample_web_acceptance'
out.mkdir(exist_ok=False,parents=True)
jobs=out/'jobs'; inputs=out/'inputs'; inputs.mkdir()
with zipfile.ZipFile(ROOT/'workbench/fusion/tests/fixtures/native_read_contracts.zip') as z:z.extractall(inputs/'two')
with zipfile.ZipFile(ROOT/'workbench/fusion/tests/fixtures/historical_read_contracts.zip') as z:z.extractall(inputs/'five')
identities={}
for point,name in [('control_4','软件回归夹具：成功样本4（785d3bd）'),('suspected_38','软件回归夹具：疑似第38点（785d3bd）')]:
    identities[point]=register(jobs,name,run_directory=inputs/'two/raw/execution'/point)
identities['five']=register(jobs,'软件回归夹具适配：边界及三段波形（不同原条件，非良率组）',manifest=inputs/'five/manifest.json')
for key,name in [('unsupported','软件夹具：不支持设置'),('missing','软件夹具：缺少波形'),('condition','软件夹具：检查条件冲突'),('cancel','软件夹具：取消保留未启动项')]:
    folder=inputs/key;shutil.copytree(inputs/'five',folder)
    m=sv.load(folder/'manifest.json')
    if key=='unsupported':m['cases']=[m['cases'][2]];m['cases'][0]['contract']['rows']=2
    if key=='missing':m['cases']=[m['cases'][2]];(folder/m['cases'][0]['files']['waveform']).unlink()
    if key=='condition':m['cases']=[m['cases'][2]];m['cases'][0]['threshold_s']=.8e-9
    sv.save(folder/'manifest.json',m)
    identities[key]=register(jobs,name,manifest=folder/'manifest.json')
hashes={str(p.relative_to(jobs)):sv.sha(p) for p in jobs.rglob('*') if p.is_file()}
sv.save(out/'before_hashes.json',hashes)
sv.save(out/'identities.json',identities)
print(json.dumps(identities,ensure_ascii=False,indent=2))
print('Derived SOFTWARE fixtures only. Not new SRAM evidence or yield samples. No Xyce.')
