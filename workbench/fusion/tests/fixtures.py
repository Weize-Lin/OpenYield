"""Small synthetic evidence, generated in temporary directories. Never SRAM data."""
import csv
import json
from pathlib import Path
import numpy as np


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def fixture(root, operation, reference=False):
    root = Path(root) / operation
    run = root / 'trial' / 'mc'
    run.mkdir(parents=True)
    is_read = operation == 'read'
    count = 4 if is_read else 12
    summary = dict(case='synthetic_fixture', operation=operation, target=[0, 0], seed=2, status='complete',
        resolved_circuit={'array': [1, 1, False], 'vdd': 1., 'corner': 'TT', 'target': [0, 0]},
        relative_std=.05, u0_positive=True, sampling_source='numpy_independent_positive_u0',
        model_sha256='synthetic-model', nominal_parameters_per_cell=[.4, .04, -.13],
        bitcell_parameter_order=['PGL.vth0', 'PGL.u0', 'PGL.voff'], driver_sizes={'driver': 'fixture'},
        timing={'mode': 'fixture'}, interconnect={'wire': 'fixture'}, equivalent={'mode': 0},
        measurement='read_delay_s' if is_read else 'hold_snm', failure_operator='>' if is_read else '<',
        outcome_rule_version='fixture_only', threshold_field='threshold_ns' if is_read else 'threshold_mv',
        threshold_ns=1., threshold_mv=327.65, git_revision='synthetic-fixture', git_tracked_files_modified=False,
        xyce='/fixture/not-executed/Xyce', xyce_version='not-executed', xyce_sha256='a'*64,
        research_cost={'budget_unit': 'xyce_samples', 'algorithm_draw_cap': count, 'xyce_cap': count},
        budget=count, algorithm='repository_MC' if is_read else 'repository_MNIS_configured_proposal',
        requested_samples=count, returned_rows=count, unclassified_samples=0, pfail_estimate=0. if is_read else 1e-8,
        diagnostics={'estimation_draws_evaluated': 7},
        sampling_diagnostics={'warnings': [] if is_read else ['low_observed_weight_mass']})
    write(run / 'run_summary.json', summary)
    write(run / 'run_config.json', {'synthetic': True})
    (run / 'report.md').write_text('Synthetic fixture; not a simulation result.\n')
    (run.parent / 'process.log').write_text('Synthetic fixture; no process executed.\n')
    batch = run / 'batch_0000'
    batch.mkdir()
    np.save(batch / 'samples.npy', np.arange(count*3, dtype=float).reshape(count, 3))
    # A valid tiny PNG; no decoder or plotting dependency needed.
    import base64
    (batch / 'waveform.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg=='))
    (batch / 'deck.sp').write_text('* Synthetic fixture, never run\n.end\n')
    write(batch / 'variation.json', {'synthetic': True})
    with (run / 'samples.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['sample', 'batch', 'xyce_run', 'outcome', 'read_delay_s'])
        writer.writeheader()
        for i in range(count):
            writer.writerow(dict(sample=i, batch=batch.name, xyce_run=i, outcome='pass', read_delay_s=7.131475e-10))
    group = dict(id='fixture', runs=[dict(run='trial/mc', estimator='mc' if is_read else 'mnis', seed=2)])
    if reference:
        ref = root / 'reference'
        ref_summary = dict(summary, seed=2026111801, algorithm='repository_MC', budget=128,
                           requested_samples=128, returned_rows=128, failure_count=10, pfail_estimate=10/128)
        write(ref / 'run_summary.json', ref_summary)
        group['reference_runs'] = ['reference']
    path = root / 'analysis_manifest.json'
    write(path, dict(schema_version=1, groups=[group]))
    return path
