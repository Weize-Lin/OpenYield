"""Plan-derived run identities and pinned offline reference reports."""
import hashlib
import json
from pathlib import Path


def planned_trials(experiment):
    for profile in experiment['estimators']:
        for seed in experiment['seeds']:
            estimator = profile['id']
            relative = Path(estimator) / f'seed_{seed}' / profile['algorithm']
            yield dict(estimator=estimator, seed=seed, run=relative.as_posix()), profile


def analysis_manifest(experiment, experiment_file, reference_runs):
    group = dict(id='experiment', runs=[entry for entry, _ in planned_trials(experiment)])
    references = []
    for name in reference_runs:
        directory = Path(name).resolve()
        raw = (directory / 'run_summary.json').read_bytes()
        seed = json.loads(raw).get('seed')
        if type(seed) is not int or seed in experiment['seeds']:
            raise ValueError('Reference seeds must be disjoint from the planned seeds')
        references.append(dict(run=str(directory), sha256=hashlib.sha256(raw).hexdigest(), seed=seed))
    if references:
        if len({ref['seed'] for ref in references}) != len(references):
            raise ValueError('Reference seeds must be distinct')
        group['reference_runs'] = references
    plan = dict(experiment=experiment_file.name, experiment_sha256=hashlib.sha256(experiment_file.read_bytes()).hexdigest(),
                reference_runs=references)
    plan_file = experiment_file.with_name('analysis_plan.json')
    plan_file.write_text(json.dumps(plan, indent=2) + '\n', encoding='utf-8')
    return dict(schema_version=1, groups=[group],
                execution_plan=dict(path=plan_file.name, sha256=hashlib.sha256(plan_file.read_bytes()).hexdigest()))


def load_planned_trials(manifest, base):
    if 'execution_plan' not in manifest:
        return {}
    frozen = manifest['execution_plan']
    raw = (base / frozen['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != frozen['sha256']:
        raise ValueError('Frozen execution plan SHA-256 mismatch')
    plan = json.loads(raw)
    config_raw = (base / plan['experiment']).read_bytes()
    if hashlib.sha256(config_raw).hexdigest() != plan['experiment_sha256']:
        raise ValueError('Frozen experiment SHA-256 mismatch')
    experiment = json.loads(config_raw)
    groups = manifest['groups']
    if len(groups) != 1:
        raise ValueError('A frozen execution plan defines one analysis group')
    group, = groups

    def identity(entry):
        return entry['estimator'], entry['seed'], (base / entry['run']).resolve()

    planned = list(planned_trials(experiment))
    if [identity(entry) for entry in group['runs']] != [identity(entry) for entry, _ in planned]:
        raise ValueError('Analysis runs differ from frozen execution plan: missing, added, relocated or reordered run')
    if group.get('reference_runs', []) != plan['reference_runs']:
        raise ValueError('Analysis reference selection differs from frozen execution plan')
    return {(entry['estimator'], entry['seed']): dict(profile=profile, budget=experiment['budget_per_algorithm'],
             budget_protocol=experiment['budget_unit'], xyce_cap=experiment['budget_per_algorithm'],
             draw_cap=experiment['max_algorithm_draws'], estimation_cap=profile.get('estimation_draws'))
            for entry, profile in planned}
