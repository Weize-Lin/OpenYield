"""Frozen cross-circuit plans; replay uses the existing per-target analyzer.

Circuit statuses describe execution only. They never fabricate run estimates,
costs, or a cross-circuit ranking. Missing runs remain in the denominator.
"""
import copy
import hashlib
import json
from pathlib import Path

from .analysis_plan import analysis_manifest, load_planned_trials

PROTOCOL = 'openyield_suite_v1'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def freeze_suite(folder, plans):
    folder = Path(folder)
    entries = []
    for plan in plans:
        index = plan['index']
        source = folder / 'plans' / str(index)
        source.mkdir(parents=True)
        config = source / 'experiment.json'
        config.write_text(json.dumps(plan['config'], indent=2) + '\n', encoding='utf-8')
        manifest = source / 'analysis_manifest.json'
        manifest.write_text(json.dumps(analysis_manifest(plan['config'], config, plan['references']), indent=2) + '\n')
        entries.append(dict(id=f'circuit{index}', label=plan['label'],
                            plan_manifest=str(manifest.relative_to(folder)), plan_sha256=digest(manifest),
                            execution_directory=f'execution_{index}'))
        plan.update(status='not_started', manifest=str(folder / f'execution_{index}/analysis_manifest.json'))
    path = folder / 'suite_manifest.json'
    definition = dict(schema_version=1, kind=PROTOCOL, circuits=entries, status_file='suite_status.json')
    plan_file = folder / 'suite_plan.json'
    plan_file.write_text(json.dumps(definition, indent=2) + '\n')
    path.write_text(json.dumps(dict(definition, frozen_plan=dict(path=plan_file.name,
                    sha256=digest(plan_file))), indent=2) + '\n')
    return path


def load_suite(path, check_path=lambda p: Path(p).resolve()):
    path = Path(path).resolve()
    data = json.loads(path.read_text())
    if data.get('schema_version') != 1 or data.get('kind') != PROTOCOL:
        raise ValueError('Unsupported suite protocol')
    plan_path = check_path(path.parent / data['frozen_plan']['path'])
    if digest(plan_path) != data['frozen_plan']['sha256']:
        raise ValueError('Frozen suite plan SHA-256 mismatch')
    definition = json.loads(plan_path.read_text())
    if definition != {k: v for k, v in data.items() if k != 'frozen_plan'}:
        raise ValueError('Suite circuit list differs from frozen plan')
    circuits = data.get('circuits')
    if not isinstance(circuits, list) or not circuits:
        raise ValueError('Suite needs a complete circuit list')
    ids, destinations, loaded = set(), set(), []
    check_path(path.parent / data['status_file'])
    for circuit in circuits:
        if circuit['id'] in ids:
            raise ValueError('Duplicate suite circuit id')
        ids.add(circuit['id'])
        manifest = check_path(path.parent / circuit['plan_manifest'])
        destination = check_path(path.parent / circuit['execution_directory'])
        if destination in destinations:
            raise ValueError('Duplicate suite execution directory')
        destinations.add(destination)
        if digest(manifest) != circuit['plan_sha256']:
            raise ValueError('Suite plan manifest SHA-256 mismatch')
        frozen = json.loads(manifest.read_text())
        plan_path = check_path(manifest.parent / frozen['execution_plan']['path'])
        plan = json.loads(plan_path.read_text())
        config_path = check_path(manifest.parent / plan['experiment'])
        for group in frozen['groups']:
            for entry in group['runs']:
                check_path(destination / entry['run'] / 'run_summary.json')
            for ref in group.get('reference_runs', []):
                check_path(manifest.parent / ref['run'] / 'run_summary.json')
        planned = load_planned_trials(frozen, manifest.parent)
        actual = check_path(destination / 'analysis_manifest.json')
        if actual.is_file():
            # Compare the runner's frozen definition as well as its run list.
            observed = json.loads(actual.read_text())
            actual_plan_path = check_path(destination / observed['execution_plan']['path'])
            actual_plan = json.loads(actual_plan_path.read_text())
            check_path(destination / actual_plan['experiment'])
            observed_planned = load_planned_trials(observed, destination)
            if (observed_planned != planned or observed['groups'] != frozen['groups']
                    or actual_plan['experiment_sha256'] != plan['experiment_sha256']):
                raise ValueError('Executed circuit differs from frozen suite plan')
        loaded.append((circuit, frozen, planned, destination))
    return data, loaded


def suite_groups(path, output):
    # Import late to avoid an analyzer/suite import cycle.
    from .analyze_results import analyze_group, validate_manifest
    path = Path(path).resolve()
    data, loaded = load_suite(path)
    status_path = path.parent / data['status_file']
    statuses = json.loads(status_path.read_text()) if status_path.is_file() else {}
    groups = []
    for circuit, frozen, planned, destination in loaded:
        for group in frozen['groups']:
            group = copy.deepcopy(group)
            for entry in group['runs']:
                entry['run'] = str(destination / entry['run'])
            # References in analysis_manifest are absolute and digest-pinned.
            validate_manifest(dict(schema_version=1, groups=[group]), path.parent, Path(output).resolve())
            result = analyze_group(group, path.parent, planned)
            result.update(id=circuit['id'] + '-' + group['id'], name=circuit['label'],
                          suite_execution=statuses.get(circuit['id'], dict(status='unknown')))
            for row in result['runs']:
                row['suite_circuit_status'] = result['suite_execution']['status']
            groups.append(result)
    return groups
