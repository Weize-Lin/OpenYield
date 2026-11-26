"""Derive small SOFTWARE fixtures from archived saved data, never a new circuit run.

Only unneeded waveform columns are omitted. All time points, required-node
tokens, duplicate S_EN probes, sample order and original labels are retained.
Hashes in the derivative's own manifest/recording are updated and explicitly
marked fixture-only; source archives and original files are never modified.
"""
import argparse,copy,hashlib,json
from pathlib import Path
import sys,tempfile,zipfile

ROOT=Path(__file__).resolve().parents[3]
# The development staging script lives outside the repository; allow an explicit source root.
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(p,d):p.write_text(json.dumps(d,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def make(source,name,out,repository):
    sys.path.insert(0,str(repository/'tools'))
    import verify_saved_6t_read as v
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp)
        with zipfile.ZipFile(source) as z:z.extractall(root)
        manifest=json.loads((root/name).read_text());before=[v.verify(c,root,{}) for c in manifest['cases']]
        nodes={};metadata={};keep={name}
        for c,r in zip(manifest['cases'],before):
            if 'waveform' not in r.get('evidence',{}):raise ValueError('Source has no established waveform: '+c['id'])
            nodes.setdefault(c['files']['waveform'],set()).update('V('+n.upper()+')' for n in r['evidence']['waveform']['nodes'].values())
            keep.update(c['files'].values());keep.update(s['file'] for s in c.get('threshold_sources',[]))
            for role in ('original','solver_exit'):
                if c.get(role):keep.add(c[role]['file'])
            # Native auto-index tests use these already-existing saved records.
            if c.get('original',{}).get('kind')=='sample_csv':
                run=(root/c['original']['file']).parent
                for basename in ('run_config.json','run_summary.json','samples.csv'):
                    p=run/basename
                    if p.is_file():keep.add(str(p.relative_to(root)))
                ledger=(root/c['files']['deck']).parent/'batch_evidence.json'
                if ledger.is_file():keep.add(str(ledger.relative_to(root)))
        for relative,required in nodes.items():
            path=root/relative;lines=path.read_text().splitlines(keepends=True)
            header=next(line.split() for line in lines if 'TIME' in line.upper() and 'V(' in line.upper())
            keys=[x.upper() for x in header];required.update(('INDEX','TIME','V(S_EN)'))
            selected=[i for i,k in enumerate(keys) if k in required]
            assert 'TIME' in [keys[i] for i in selected]
            old=digest(path);changed=[];data_rows=0
            for line in lines:
                tokens=line.split()
                if len(tokens)==len(header) and ([t.upper() for t in tokens]==keys or tokens[0][0].isdigit()):
                    changed.append(' '.join(tokens[i] for i in selected)+'\n')
                    data_rows+=tokens[0][0].isdigit()
                else:changed.append(line)
            path.write_text(''.join(changed))
            metadata[relative]=dict(source_sha256=old,fixture_sha256=digest(path),
                original_columns=len(keys),retained_columns=len(selected),data_rows=data_rows,
                policy='All saved rows/time points and retained numeric tokens unchanged; no interpolation or decimation.')
        for c in manifest['cases']:
            c['sha256']={role:digest(root/file) for role,file in c['files'].items()}
            c.setdefault('recorded_provenance',{}).update(fixture_only=True,source_archive_sha256=digest(source))
            ledger=(root/c['files']['deck']).parent/'batch_evidence.json'
            if ledger.is_file():
                d=json.loads(ledger.read_text());d['fixture_only']=True
                wave=root/c['files']['waveform']
                if d.get('waveform'):
                    d['waveform'].update(sha256=digest(wave),bytes=wave.stat().st_size)
                dump(ledger,d)
        manifest['fixture_only']=True
        manifest['note']='Derived software contracts, not original evidence or new simulations. Full source packages are archived separately.'
        dump(root/name,manifest)
        after=[v.verify(c,root,{}) for c in manifest['cases']]
        scientific=('comparison','circuit_observation','timing_observation','conflict_categories')
        for a,b in zip(before,after):
            for key in scientific:assert a[key]==b[key],(a['id'],key)
            for key in ('observation','delay','simulation','controls','baseline'):
                assert a.get('evidence',{}).get(key)==b.get('evidence',{}).get(key),(a['id'],key)
        fixture=dict(fixture_only=True,source_archive=Path(source).name,source_archive_sha256=digest(source),waveforms=metadata,
            preserved_scientific_observations=True,warning='Not original archival provenance; never use these derivative files as new SRAM validation or yield samples.')
        dump(root/'fixture_meta.json',fixture);keep.add('fixture_meta.json')
        out.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(out,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
            for relative in sorted(keep):
                p=root/relative
                info=zipfile.ZipInfo(relative,date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
                z.writestr(info,p.read_bytes())
        expected=[{k:r[k] for k in ('comparison','circuit_observation','timing_observation')}|
            {'evidence':{k:r['evidence'][k] for k in ('observation','simulation')}} for r in before]
        # Python 3.9 supports dict union; this golden projection uses the unmodified source data.
        return fixture,expected


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repository',type=Path,default=ROOT)
    p.add_argument('--historical-archive',type=Path,required=True)
    p.add_argument('--native-archive',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args();out=a.output_dir.resolve();out.mkdir(exist_ok=False,parents=True)
    meta,_=make(a.historical_archive,'manifest.json',out/'historical_read_contracts.zip',a.repository.resolve())
    native,expected=make(a.native_archive,'verification_manifest.json',out/'native_read_contracts.zip',a.repository.resolve())
    dump(out/'native_expected.json',dict(fixture_only=True,results=expected))
    dump(out/'provenance.json',dict(historical=meta,native=native))
    for file in out.iterdir():print(file.name,file.stat().st_size,digest(file))


if __name__=='__main__':main()
