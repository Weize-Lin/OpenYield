"""Preserve numerical Xyce failures and retry the same sampled circuit."""

from pathlib import Path
import re
import shutil
import subprocess
import hashlib
import json
import os
import time
from datetime import datetime, timezone



class _SolverEvidence:
    """Record observed process facts; never infer completed parameter rows."""
    def __init__(self, deck, command, log, cwd, env):
        self.deck,self.log=deck,log
        self.path=Path(str(deck)+'.solver_attempts.json')
        self.data=json.loads(self.path.read_text()) if self.path.exists() else dict(schema_version=1,calls=[])
        if self.data.get('schema_version')!=1:raise ValueError('Unknown solver evidence schema')
        self.call=dict(index=len(self.data['calls']),command=[str(x) for x in command] if not isinstance(command,str) else command,
                       cwd=str(Path(cwd or Path.cwd()).resolve()),attempts=[],state='created')
        executable=command[0] if not isinstance(command,str) else command
        resolved=shutil.which(str(executable),path=(env or os.environ).get('PATH'))
        self.call['executable_path']=resolved
        self.call['executable_sha256']=_file_sha(Path(resolved)) if resolved and Path(resolved).is_file() else None
        self.data['calls'].append(self.call)
        self.directory=deck.parent/'solver_attempts'/f"call_{self.call['index']:04d}"
        self.directory.mkdir(parents=True,exist_ok=False)
        self.save()

    def save(self):
        temporary=self.path.with_name(self.path.name+'.tmp')
        temporary.write_text(json.dumps(self.data,indent=2,allow_nan=False)+'\n')
        temporary.replace(self.path)

    def begin(self, reason):
        self.start=time.monotonic()
        folder=self.directory/f"attempt_{len(self.call['attempts']):04d}"
        folder.mkdir()
        snapshot=folder/'deck.sp';shutil.copy2(self.deck,snapshot)
        self.attempt=dict(index=len(self.call['attempts']),trigger=reason,state='running',
            started_utc=datetime.now(timezone.utc).isoformat(),ended_utc=None,wall_seconds=None,
            native_exit_code=None,effective_exit_code=None,solver_completed_rows=None,
            deck_snapshot=str(snapshot),deck_sha256=_file_sha(snapshot),
            console_file=str(folder/'console.log'),artifacts={})
        self.call['attempts'].append(self.attempt);self.call['state']='running';self.save()

    def finish(self, state, output='', native=None, effective=None, error=None):
        self.attempt.update(state=state,ended_utc=datetime.now(timezone.utc).isoformat(),
            wall_seconds=time.monotonic()-self.start,native_exit_code=native,
            effective_exit_code=effective,error_type=type(error).__name__ if error else None)
        Path(self.attempt['console_file']).write_text(output)
        artifacts={}
        for pattern in ('.prn','.prn.*','.mt*','.ms*','.res'):
            for path in self.deck.parent.glob(self.deck.name+pattern):
                if path.is_file():artifacts[str(path)]=dict(sha256=_file_sha(path),bytes=path.stat().st_size)
        if self.log.exists():artifacts[str(self.log)]=dict(sha256=_file_sha(self.log),bytes=self.log.stat().st_size)
        self.attempt['artifacts']=artifacts;self.call['state']=state;self.save()

    def archived(self, directory):
        previous=self.attempt['artifacts']
        self.attempt['retry_archive']=str(directory)
        self.attempt['artifacts']={str(directory/Path(path).name) if (directory/Path(path).name).is_file() else path:value
                                   for path,value in previous.items()}
        self.save()


def _file_sha(path):
    result=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):result.update(block)
    return result.hexdigest()



def execute_xyce(deck_path, command, *, log_path=None, cwd=None, timeout=None, env=None):
    """One DC line-search retry and one bounded-step retry for numerical failure.

    Xyce sampling can report exit zero after a failed operating point. Treat
    that as failure too, since subsequent samples may have corrupted states.
    Keep the first attempt and preserve the native sampling seed on retry.
    Electrical failures are handled by the caller and never trigger retries.
    """
    deck_path = Path(deck_path)
    log_path = Path(log_path) if log_path is not None else Path(str(deck_path) + '.log')

    evidence=_SolverEvidence(deck_path,command,log_path,cwd,env)
    def run(reason):
        evidence.begin(reason)
        try:
            result = subprocess.run(command, cwd=cwd, env=env, timeout=timeout,
                                    capture_output=True, text=True, check=False)
        except subprocess.TimeoutExpired as exc:
            def decoded(value):
                return value.decode(errors='replace') if isinstance(value, bytes) else value or ''
            log_path.write_text(f'Xyce timed out after {timeout} seconds\n'
                                + decoded(exc.stdout) + '\n' + decoded(exc.stderr))
            evidence.finish('timeout',log_path.read_text(),error=exc)
            raise
        except BaseException as exc:
            evidence.finish('interrupted' if isinstance(exc,(KeyboardInterrupt,SystemExit)) else 'launch_error',str(exc),error=exc)
            raise
        log_path.write_text(result.stdout + '\n' + result.stderr)
        native_code=result.returncode
        if any(message in result.stdout + result.stderr for message in
               ('DC Operating Point Failed', 'Time step too small')):
            result.returncode = result.returncode or 1
        evidence.finish('process_finished' if result.returncode==0 else 'process_failed',
                        result.stdout+'\n'+result.stderr,native=native_code,effective=result.returncode)
        return result

    result = run('initial')
    retried = set()
    while result.returncode:
        output = result.stdout + result.stderr
        deck = deck_path.read_text()
        options = []
        if 'DC Operating Point Failed' in output:
            kind = 'dcop'
            if re.search(r'(?im)^\s*\.OPTIONS\s+NONLIN\b[^\n]*\bSEARCHMETHOD\s*=\s*2\b', deck):
                return result
            options.append('.OPTIONS NONLIN SEARCHMETHOD=2')
            amended = deck
        elif 'Time step too small' in output:
            kind = 'timestep'
            amended = _bound_transient_step(deck)
            if amended is None:
                return result
        else:
            return result
        if kind in retried:
            return result
        if re.search(r'(?im)^\s*\.SAMPLING\b', deck):
            if not re.search(r'(?im)^\s*\.OPTIONS\s+SAMPLES\b[^\n]*\bSEED\s*=\s*[1-9]\d*', deck):
                seed = re.search(r'Seeding random number generator with\s+(\d+)', output)
                if seed is None or int(seed[1]) <= 0:
                    # An unseeded retry would silently replace the failed sample.
                    return result
                options.append(f'.OPTIONS SAMPLES SEED={seed[1]}')
        amended, count = re.subn(r'(?im)^\s*\.END\s*$', '\n'.join(options) + '\n.END', amended, count=1)
        if count != 1:
            return result
        attempt = _preserve_attempt(deck_path, log_path, kind)
        evidence.archived(attempt)
        deck_path.write_text(amended)
        retried.add(kind)
        result = run(kind)
        log_path.write_text(f'{kind} retry; original attempt: {attempt}\n'
                            + '\n'.join(options) + '\n' + log_path.read_text())
        evidence.attempt['artifacts'][str(log_path)]=dict(sha256=_file_sha(log_path),bytes=log_path.stat().st_size)
        evidence.save()
    return result


RETRY_MAX_STEP = 2e-11
_SPICE_SCALE = {'t': 1e12, 'g': 1e9, 'meg': 1e6, 'k': 1e3, 'm': 1e-3, 'mil': 25.4e-6,
                'u': 1e-6, 'n': 1e-9, 'p': 1e-12, 'f': 1e-15}


def _spice_number(text):
    match = re.fullmatch(r'([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)(meg|mil|[tgkmunpf])?[a-z]*',
                         text, re.IGNORECASE)
    if match is None:
        return None
    return float(match[1]) * _SPICE_SCALE.get((match[2] or '').lower(), 1.0)


def _bound_transient_step(deck):
    """Cap the .TRAN step ceiling at 20 ps, or return None if nothing is tighter.

    The ceiling is the fourth positional field, after the optional start time.
    Decks with a start time or a coarser explicit ceiling need the retry as
    much as two-field decks; a deck already bounded at 20 ps has no tighter one.
    """
    match = re.search(r'(?im)^(\.TRAN)[ \t]+([^\n]*?)[ \t]*$', deck)
    if match is None:
        return None
    fields = match[2].split()
    positional = 0
    for field in fields[:4]:
        if _spice_number(field) is None:
            break
        positional += 1
    if positional < 2:
        return None
    ceiling = f'{RETRY_MAX_STEP:.4e}'
    if positional == 4:
        if _spice_number(fields[3]) <= RETRY_MAX_STEP * (1 + 1e-9):
            return None
        fields[3] = ceiling
    else:
        fields[positional:positional] = ['0', ceiling][positional - 2:]
    return deck[:match.start()] + f'{match[1]} ' + ' '.join(fields) + deck[match.end():]


def _preserve_attempt(deck_path, log_path, kind):
    attempt = deck_path.parent / f'{kind}_attempt'
    suffix = 1
    while attempt.exists():
        attempt = deck_path.parent / f'{kind}_attempt_{suffix}'
        suffix += 1
    attempt.mkdir()
    files = {deck_path, log_path, *deck_path.parent.glob(deck_path.name + '.*')}
    for path in files:
        if path.is_file():
            shutil.copy2(path, attempt / path.name)
    # The first attempt stopped part-way, so it can leave per-sample measure
    # files the retry never rewrites.  Parsers index those files by sample
    # number, so a surviving one would report the failed attempt's value as a
    # retry result.  The copies above keep the evidence.
    for pattern in ('.mt*', '.ms*', '.prn', '.prn.*'):
        for path in deck_path.parent.glob(deck_path.name + pattern):
            if path.is_file():
                path.unlink()
    return attempt
