"""One-off fingerprint check. No circuit measurements or algorithm execution."""
import argparse
import ast
import csv
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
from unittest.mock import patch
import zipfile

import numpy as np
import scipy
import torch

from yield_estimation.evaluation.analysis_plan import analysis_manifest, load_planned_trials
from yield_estimation.evaluation.analyze_results import TARGET_FIELDS, target_evidence
from yield_estimation.evaluation.experiment import normalize_experiment, child_configs, write_json
from yield_estimation.evaluation.outcomes import MeasurementRule
from yield_estimation.evaluation.run import ROOT, build_evaluation_case, evaluation_circuit_options, normalize_config, wilson_interval
from yield_estimation.evaluation.solver_provenance import xyce_fingerprint

EXPECTED_REVISION = '56320b44784c3334af16645a430de1865bb41d9f'
REFERENCE_REVISION = 'bd09101003e654bd41b090949b5afdc2a6cd2fc2'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--zip', type=Path, required=True)
    parser.add_argument('--published-sha', type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    assert git('rev-parse', 'HEAD') == EXPECTED_REVISION
    assert git('status', '--porcelain') == '', 'Preflight requires the exact clean execution checkout'
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(__file__, output/'preflight_driver.py')
    expected = args.published_sha.read_text().split()[0]
    assert digest(args.zip) == expected
    shutil.copyfile(args.published_sha, output/'published_SHA256SUMS.txt')
    bundle = output/'published_evidence'
    bundle.mkdir()
    with zipfile.ZipFile(args.zip) as archive:
        for name in archive.namelist():
            assert (bundle/name).resolve().is_relative_to(bundle), 'Unsafe archive member'
        archive.extractall(bundle)
    evidence = bundle/'hold_workflow_bd09101'
    checked_files = 0
    for line in (evidence/'SHA256SUMS').read_text().splitlines():
        if not line.strip():
            continue
        sha, name = line.split(maxsplit=1)
        item = (evidence/name.lstrip('*')).resolve()
        assert item.is_relative_to(evidence) and digest(item) == sha, name
        checked_files += 1

    raw_config = ROOT/'docs/research/acs_initialization/sram_comparison.json'
    config = normalize_experiment(raw_config)
    frozen_config = output/'experiment.json'
    write_json(frozen_config, config)
    jobs = list(child_configs(config))
    assert len(jobs) == 16 and config['budget_per_algorithm'] == config['max_algorithm_draws'] == 132
    assert config['seeds'] == list(range(720101, 720109))
    assert jobs[0][0]['proposal'] == jobs[1][0]['proposal'] == dict(covariance='legacy', legacy_variance=.00001, std_scale=1, target_fraction=0)
    assert jobs[0][0]['initialization']['radius_sigma'] == 4
    assert jobs[1][0]['initialization']['radii'] == [1, 2, 3, 4]
    executable = shutil.which('Xyce')
    assert executable, 'Xyce executable is required for version and binary identity only'
    solver = xyce_fingerprint(executable)  # The only permitted solver call is Xyce -v.
    assert 'xyce_version' in solver and 'xyce_sha256' in solver
    write_json(output/'solver.json', solver)
    child = normalize_config(jobs[0][2])
    options = evaluation_circuit_options(child)
    assert options['real_cell_mode'] == 0
    summary = dict(child)
    rule = MeasurementRule(options['operation'], child)
    summary.update(operation=options['operation'], **rule.metadata())
    build_path = output/'dry_build'
    build_path.mkdir()
    with patch('subprocess.Popen', side_effect=AssertionError('No child or netlist solver allowed during dry build')), \
         patch('utils.xyce.execute_xyce', side_effect=AssertionError('No circuit simulation allowed')), \
         patch('yield_estimation.evaluation.run.IndependentProcess.sample', side_effect=AssertionError('No target samples allowed')):
        means, process, testbench = build_evaluation_case(child, options, build_path, summary)
    target, missing = target_evidence(summary)
    assert not missing, missing
    # Reference summaries are JSON; compare the same transport representation.
    # This changes tuple containers to lists, not any physical parameter value.
    transported = json.loads(json.dumps(target, allow_nan=False))
    transport_changes = []
    def compare_types(memory, saved, path):
        if type(memory) is not type(saved):
            transport_changes.append(dict(field=path, memory_type=type(memory).__name__, json_type=type(saved).__name__))
        if isinstance(memory, dict):
            for key in memory:
                compare_types(memory[key], saved[key], path+'.'+key)
        elif isinstance(memory, (tuple, list)):
            for i, (left, right) in enumerate(zip(memory, saved)):
                compare_types(left, right, path+f'[{i}]')
    compare_types(target, transported, 'target')
    target = transported
    write_json(output/'transport_types.json', transport_changes)
    print('JSON transport container changes='+str(transport_changes))
    assert len(means) == 18
    assert np.all(means[1::3]-4*process.sigma[1::3] > 0)
    write_json(output/'resolved_target.json', target)

    # The target sampler is unchanged; preserve exact source-segment hashes as evidence.
    old_run = git('show', REFERENCE_REVISION+':yield_estimation/evaluation/run.py')
    new_run = (ROOT/'yield_estimation/evaluation/run.py').read_text()
    def class_segment(source):
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == 'IndependentProcess')
        return ast.get_source_segment(source, node)
    assert class_segment(old_run) == class_segment(new_run), 'Target process implementation differs from the reference'
    unchanged = not git('diff', '--name-only', REFERENCE_REVISION, 'HEAD', '--', 'sram_compiler', 'tran_models', 'config.py', 'utils/xyce.py')
    assert unchanged, 'Electrical/model/solver-wrapper sources changed'
    reference_manifest = json.loads((evidence/'reference/analysis_manifest.json').read_text())
    entries = reference_manifest['groups'][0]['runs']
    assert [entry['seed'] for entry in entries] == [8311, 8312, 8313, 8314]
    references, total_failures = [], 0
    for entry in entries:
        directory = (evidence/'reference'/entry['run']).resolve()
        native = directory/'run_summary.json'
        ref = json.loads(native.read_text())
        assert digest(native) == entry['sha256']
        ref_target, ref_missing = target_evidence(ref)
        differences = [key for key in target if target[key] != ref_target[key]]
        assert not ref_missing and not differences, differences
        assert ref['case'] == config['experiment']['case']
        assert ref['status'] == 'complete' and ref['git_revision'] == REFERENCE_REVISION and ref['git_tracked_files_modified'] is False
        assert ref['requested_samples'] == ref['returned_rows'] == 256
        assert ref['xyce_sha256'] == solver['xyce_sha256'] and ref['xyce_version'] == solver['xyce_version']
        assert ref['seed'] not in config['seeds']
        with (directory/'samples.csv').open() as stream:
            rows = list(csv.DictReader(stream))
        assert len(rows) == 256
        values = np.array([float(row['hold_snm_v']) for row in rows])
        assert np.isfinite(values).all()
        failures = int((values < config['experiment']['threshold_mv']*1e-3).sum())
        assert failures == ref['failure_count'] and failures/256 == ref['pfail_estimate']
        total_failures += failures
        references.append(dict(seed=ref['seed'], path=str(directory), sha256=digest(native),
                               measurement_sha256=digest(directory/'samples.csv'), failures=failures, samples=256,
                               model_sha256=ref['model_sha256'], target_differences=differences,
                               solver_path_same=ref['xyce'] == solver['xyce']))
    manifest = analysis_manifest(config, frozen_config, [r['path'] for r in references])
    write_json(output/'analysis_manifest.json', manifest)
    assert len(load_planned_trials(manifest, output)) == 16
    assert git('status', '--porcelain') == ''
    result = dict(status='passed_preflight_not_simulated', execution_revision=EXPECTED_REVISION,
        source_checkout=str(ROOT), source_status='', raw_config_sha256=digest(raw_config),
        frozen_config_sha256=digest(frozen_config), model_sha256=summary['model_sha256'],
        solver=solver, target=target, reference_release='hold-snm-workflow-bd09101',
        reference_zip_sha256=expected, checked_bundle_files=checked_files, references=references,
        reference_failures=total_failures, reference_samples=1024, reference_estimate=total_failures/1024,
        reference_interval=wilson_interval(total_failures,1024), reference_is_exact_truth=False,
        target_process_source_sha256=hashlib.sha256(class_segment(new_run).encode()).hexdigest(),
        electrical_model_solver_wrapper_sources_unchanged=True,
        reference_source_revision=REFERENCE_REVISION, code_revision_same=False,
        cross_revision_reference_basis='equal resolved target, model and solver fingerprints; unchanged target sampler and electrical sources',
        budgets=dict(planned_tasks=16, max_unique_submissions=2112, max_solver_attempted_rows=6336,
                     concurrent_tasks=1, **config['execution']),
        dependencies=dict(python=platform.python_version(),numpy=np.__version__,scipy=scipy.__version__,torch=torch.__version__),
        circuit_solver_calls=0, new_parameter_samples=0, solver_version_queries=1)
    write_json(output/'preflight.json', result)
    print('execution_revision='+EXPECTED_REVISION)
    print('source_status=clean; published_zip_sha256='+expected)
    print('model_sha256='+summary['model_sha256'])
    print('solver_version='+solver['xyce_version'])
    print('solver_sha256='+solver['xyce_sha256'])
    for reference in references:
        print(f"reference_seed={reference['seed']}; samples=256; failures={reference['failures']}; target_differences=0; summary_sha256={reference['sha256']}")
    print(f'reference={total_failures}/1024; estimate={total_failures/1024}; Wilson95={result["reference_interval"]}')
    print('planned_tasks=16; max_unique_submissions=2112; max_solver_attempted_rows=6336')
    print('task=180s+10s grace; experiment=1800s+30s grace; concurrency=1')
    print('circuit_solver_calls=0; new_parameter_samples=0; Xyce -v queries=1')


if __name__ == '__main__':
    main()
