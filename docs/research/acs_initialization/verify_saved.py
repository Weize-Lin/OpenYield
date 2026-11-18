import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from scipy.special import logsumexp
from scipy.stats import norm

root = Path('docs/research/acs_initialization')
folder = root / 'results'
hashes = json.loads((folder / 'SHA256.json').read_text())
for name, expected in hashes.items():
    if name == 'local_full_records.jsonl':
        continue
    assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == expected, name
with (folder / 'runs.csv').open() as stream:
    rows = list(csv.DictReader(stream))
assert len(rows) == 128
assert {int(row['seed']) for row in rows} == set(range(2026111800, 2026111864))
assert all(sum(r['seed'] == row['seed'] and r['arm'] == row['arm'] for r in rows) == 1 for row in rows)
assert sum(int(row['submitted']) for row in rows) == 33280
assert sum(int(row['returned']) for row in rows) == 33280
assert sum(int(row['initialization']) for row in rows) == 4096
assert sum(int(row['recheck']) for row in rows) == 512
assert sum(int(row['estimation']) for row in rows) == 28672
first = json.loads((folder / 'first_seed.json').read_text())
assert len(first) == 2
for run in first:
    assert run['row']['seed'] == 2026111800
    formal = run['formal']
    assert len(formal) == len(run['proposals'])
    weights = []
    for record, proposal in zip(formal, run['proposals']):
        points = np.array(record['samples'])
        centers = np.array(proposal['centers'])
        variance = np.array(proposal['variances'])
        beta = np.array(proposal['weights'])
        expected_q = logsumexp(norm.logpdf(points[:, None, :], centers[None, :, :],
                                         np.sqrt(variance)[None, :, :]).sum(axis=2) + np.log(beta), axis=1)
        expected_f = -.5 * np.square(points).sum(axis=1) - math.log(2 * math.pi)
        np.testing.assert_allclose(record['log_g'], expected_q, rtol=1e-12, atol=2e-12)
        np.testing.assert_allclose(record['log_f'], expected_f, rtol=1e-12, atol=2e-12)
        np.testing.assert_array_equal(record['indicator'], np.abs(points[:, 0]) > 2)
        weights.extend(np.exp(expected_f - expected_q) * (np.abs(points[:, 0]) > 2))
    np.testing.assert_allclose(run['row']['pfail'], np.mean(weights), rtol=2e-12)
print('Public hashes checked; 128 unique planned runs retained; submitted/returned=33280/33280.')
print('Initialization/recheck/formal=4096/512/28672; first preregistered seed independently recomputed.')
print('No access to private outputs or solver; no new samples generated.')
