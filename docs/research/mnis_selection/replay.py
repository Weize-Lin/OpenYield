"""Replay the frozen MNIS candidate tables without SPICE or new sampling."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np

from yield_estimation.evaluation.outcomes import MeasurementRule
from yield_estimation.evaluation.research import select_candidates
from yield_estimation.evaluation.run import IndependentProcess


def replay_group(name, group, rows):
    condition = group['identity']
    rule = MeasurementRule(condition['circuit']['operation'], condition)
    assert condition['u0_positive'] is True and rule.is_snm
    assert [batch['seed'] for batch in group['batches']] == group['seeds']
    outcomes = {'same_center': 0, 'different_center': 0, 'insufficient': 0}
    decisions = []
    for batch in group['batches']:
        seed = batch['seed']
        candidates = batch['candidates']
        assert len(candidates) == group['expected_rows_per_batch'] == len({row['id'] for row in candidates})
        target = IndependentProcess(np.array(condition['nominal_parameters']), condition['relative_std'], seed)
        coordinates = np.array([item['parameters'] for item in candidates])
        assert target.in_support(coordinates).all()
        assert all(item['failed'] == (rule.classify(item['hold_snm_v']) == rule.numeric_failure)
                   for item in candidates)
        densities = dict(zip((item['id'] for item in candidates), target.log_pdf(coordinates).tolist()))
        failed = [item for item in candidates if item['failed']]
        choices = {}
        ranks = {}
        distances = {}
        for policy in ('nearest_raw', 'nearest_standardized'):
            indices, scores = select_candidates(candidates, policy, target)
            distances[policy] = dict(zip((item['id'] for item in candidates), scores))
            eligible = [candidates[i]['id'] for i in indices if candidates[i]['failed']]
            ranks[policy] = {key: rank for rank, key in enumerate(eligible, 1)}
            choices[policy] = eligible[0] if len(failed) >= group['minimum_failures'] else None
        raw, standardized = choices['nearest_raw'], choices['nearest_standardized']
        status = ('insufficient' if raw is None else 'same_center' if raw == standardized
                  else 'different_center')
        outcomes[status] += 1
        original = batch.get('actual_historical_center')
        if original is not None:
            assert original == failed[0]['id']
        decisions.append(dict(seed=seed, failed_candidates=len(failed), status=status,
            historical_center=original, nearest_raw=raw, nearest_standardized=standardized,
            log_density_gain_standardized_over_raw=None if raw is None else densities[standardized] - densities[raw]))
        for item in candidates:
            key = item['id']
            rows.append(dict(group=name, seed=seed, sample_id=key, hold_snm_mv=item['hold_snm_v'] * 1000,
                failed=item['failed'], raw_distance=distances['nearest_raw'][key],
                standardized_distance=distances['nearest_standardized'][key], log_target_density=densities[key],
                raw_rank=ranks['nearest_raw'].get(key), standardized_rank=ranks['nearest_standardized'].get(key),
                historical_center=key == original, raw_center=key == raw, standardized_center=key == standardized))
    return dict(total=len(group['seeds']), counts=outcomes, decisions=decisions)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    archive = Path(__file__).with_name('batches.json').read_bytes()
    evidence = json.loads(archive)
    assert evidence['schema_version'] == 1
    rows = []
    groups = {name: replay_group(name, group, rows) for name, group in evidence['groups'].items()}
    args.output_dir.mkdir(parents=True, exist_ok=False)
    result = dict(archive_sha256=hashlib.sha256(archive).hexdigest(), groups=groups,
                  xyce_calls=0, formal_draws=0)
    (args.output_dir / 'selection_summary.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    with (args.output_dir / 'candidate_metrics.csv').open('w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for name, group in groups.items():
        print(name, 'total=', group['total'], group['counts'])
        for decision in group['decisions']:
            print(decision)
    print('Xyce calls: 0. Formal draws: 0.')


if __name__ == '__main__':
    main()
