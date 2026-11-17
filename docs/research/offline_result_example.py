"""Generate and analyze a small, entirely synthetic OpenYield report example."""
import argparse
import hashlib
import json
from pathlib import Path

from yield_estimation.evaluation.analyze_results import analyze


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    root = args.output_dir
    root.mkdir(parents=True, exist_ok=False)
    target = dict(case='sram_6t_hold_snm', resolved_circuit={'example': 'synthetic', 'target': [0, 0]},
        operation='hold_snm', target=[0, 0], relative_std=.05, u0_positive=True,
        sampling_source='numpy_independent_positive_u0', model_sha256='synthetic-example',
        nominal_parameters_per_cell=[.4, .04, -.13], bitcell_parameter_order=['vth0', 'u0', 'voff'],
        driver_sizes={'example': True}, timing={'example': True}, interconnect={'example': True},
        equivalent={'mode': 0}, measurement='hold_snm', failure_operator='<',
        outcome_rule_version='snm_outcomes_v1', threshold_field='threshold_mv', threshold_mv=327.65)

    def save(name, seed, **fields):
        path = root / name
        path.mkdir()
        raw = (json.dumps(dict(target, seed=seed, unclassified_samples=0, **fields), indent=2) + '\n').encode()
        (path / 'run_summary.json').write_bytes(raw)
        return dict(run=name, seed=seed, sha256=hashlib.sha256(raw).hexdigest())

    def trial(seed, cap, submitted, estimate, warnings):
        run = save(f'example-{seed}', seed, algorithm='example_importance_sampling', status='budget_stopped',
            budget=cap, research_cost=dict(budget_unit='xyce_samples', xyce_cap=cap),
            algorithm_requested_samples=submitted, simulator_submitted_samples=submitted,
            simulator_returned_samples=submitted, pfail_estimate=estimate,
            diagnostics=dict(estimation_draws_evaluated=submitted-2), sampling_diagnostics=dict(warnings=warnings))
        return dict(estimator='example-is', **run)

    reference = save('example-reference', 10, status='complete', algorithm='repository_MC',
                     budget=100, requested_samples=100, returned_rows=100, failure_count=5, pfail_estimate=.05)
    runs = [trial(1, 20, 20, .0001, ['low_observed_weight_mass']), trial(2, 20, 19, 1.2, []),
            trial(3, 30, 30, .08, []), dict(estimator='example-is', seed=4, run='planned-but-missing')]
    manifest = root / 'manifest.json'
    manifest.write_text(json.dumps(dict(schema_version=1, groups=[dict(
        id='synthetic-example', runs=runs, reference_runs=[reference])]), indent=2) + '\n', encoding='utf-8')
    analyze(manifest, root / 'report')
    print('Synthetic example complete:', root / 'report')


if __name__ == '__main__':
    main()
