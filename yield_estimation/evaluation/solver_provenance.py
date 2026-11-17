"""Record the identity of the selected solver executable without a netlist."""
import hashlib
from pathlib import Path
import subprocess


def xyce_fingerprint(executable):
    result = dict(xyce=executable)
    path = Path(executable).absolute()
    try:
        digest = hashlib.sha256()
        with path.open('rb') as binary:
            for block in iter(lambda: binary.read(1024 * 1024), b''):
                digest.update(block)
        result['xyce_sha256'] = digest.hexdigest()
        version = subprocess.run([str(path), '-v'], capture_output=True, text=True,
                                 encoding='utf-8', errors='replace', timeout=10)
        label = version.stdout.strip() or version.stderr.strip()
        if version.returncode == 0 and label:
            result['xyce_version'] = label
        else:
            result['xyce_identity_issue'] = f'version query returned {version.returncode} or no version text'
    except (OSError, subprocess.TimeoutExpired) as exc:
        result['xyce_identity_issue'] = f'{type(exc).__name__}: {exc}'
    return result
