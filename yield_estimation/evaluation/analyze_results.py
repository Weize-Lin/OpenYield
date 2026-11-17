"""Analyze explicitly listed OpenYield runs and independent MC references offline."""
import argparse
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path
from statistics import mean, stdev

from .analysis_plan import load_planned_trials
from .run import wilson_interval


TARGET_FIELDS = ('resolved_circuit', 'operation', 'target', 'relative_std', 'u0_positive',
                 'sampling_source', 'model_sha256', 'nominal_parameters_per_cell', 'bitcell_parameter_order',
                 'driver_sizes', 'timing', 'interconnect', 'equivalent', 'measurement', 'failure_operator',
                 'outcome_rule_version')
STRATEGY_FIELDS = ('algorithm', 'settings', 'initialization', 'initialization_sigma', 'proposal_covariance',
                   'mnis_proposal_covariance', 'mnis_target_fraction', 'search_origin', 'hscs_search_origin',
                   'batch_size')
COMPLETED = {'complete', 'budget_stopped', 'stopped'}


def signature(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def integer(value):
    return type(value) is int and value >= 0


def read_summary(directory):
    file = directory / 'run_summary.json'
    try:
        raw = file.read_bytes()
    except FileNotFoundError:
        return {}, None, 'missing_summary'
    digest = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw, parse_constant=str)
    except (ValueError, UnicodeError):
        return {}, digest, 'invalid_summary'
    if not isinstance(data, dict):
        return {}, digest, 'invalid_summary'
    return data, digest, None


def target_evidence(summary):
    fields = {key: summary.get(key) for key in TARGET_FIELDS}
    threshold_field = summary.get('threshold_field')
    fields['threshold_field'] = threshold_field
    fields['threshold'] = summary.get(threshold_field) if threshold_field in ('threshold_mv', 'threshold_ns') else None
    missing = [key for key, value in fields.items() if value is None or value == {} or value == []]
    return fields, missing


def cost_evidence(summary):
    context = summary.get('research_cost') or {}
    protocol = context.get('budget_unit', 'legacy_unspecified')
    requested = summary.get('algorithm_requested_samples', summary.get('requested_samples'))
    submitted = summary.get('simulator_submitted_samples', summary.get('requested_samples'))
    returned = summary.get('simulator_returned_samples', summary.get('returned_rows'))
    formal = (returned if summary.get('algorithm') == 'repository_MC' and summary.get('sampling_source') != 'deterministic_nominal'
              else (summary.get('diagnostics') or {}).get('estimation_draws_evaluated'))
    return dict(budget_protocol=protocol, budget=summary.get('budget'),
                draw_cap=context.get('algorithm_draw_cap'), xyce_cap=context.get('xyce_cap'),
                estimation_cap=context.get('fixed_estimation_draw_cap'),
                time_limit_s=summary.get('time_limit_s'), wall_seconds=summary.get('wall_seconds'),
                legacy_budget_basis=summary.get('budget_basis'),
                algorithm_requests=requested, xyce_submitted=submitted, xyce_returned=returned,
                formal_draws=formal, unclassified_samples=summary.get('unclassified_samples'),
                auxiliary_writes=summary.get('auxiliary_write_submitted'),
                phase_cost=context.get('phases', summary.get('phase_samples')))


def source_evidence(directory, summary, digest, expected_digest, read_issue):
    problems = [read_issue] if read_issue else []
    if expected_digest is not None and expected_digest != digest:
        problems.append('summary_sha256_mismatch')
    revision = summary.get('git_revision')
    dirty = summary.get('git_tracked_files_modified')
    if not revision or type(dirty) is not bool:
        problems.append('missing_revision_or_dirty_flag')
    if dirty is True:
        problems.append('modified_source_not_reconstructable_from_commit')
    if not summary.get('model_sha256'):
        problems.append('missing_model_hash')
    if not summary.get('xyce') or not summary.get('xyce_version'):
        problems.append('missing_solver_path_or_version')
    return dict(summary=str(directory / 'run_summary.json'), summary_sha256=digest,
                expected_summary_sha256=expected_digest, git_revision=revision, git_dirty=dirty,
                model_sha256=summary.get('model_sha256'), solver=summary.get('xyce'),
                solver_version=summary.get('xyce_version'), compiler_version=summary.get('compiler_version'),
                issues=problems)


def analyze_run(entry, base, planned=None):
    directory = (base / entry['run']).resolve()
    summary, digest, read_issue = read_summary(directory)
    target, missing = target_evidence(summary)
    source = source_evidence(directory, summary, digest, entry.get('sha256'), read_issue)
    profile = (summary.get('research') or {}).get('estimator') or {}
    strategy = {key: summary[key] for key in STRATEGY_FIELDS if key in summary}
    if profile:
        strategy['estimator_profile'] = profile
    plan_issues = []
    plan_target_differences = []
    if planned and summary:
        observed_target = dict(target, case=summary.get('case'))
        plan_target_differences = [key for key, expected in planned['target'].items()
                                   if observed_target[key] != expected]
        if plan_target_differences:
            plan_issues.append('target_differs_from_plan')
        if profile != planned['profile']:
            plan_issues.append('estimator_profile_differs_from_plan')
        if any(cost_value != planned[key] for key, cost_value in cost_evidence(summary).items()
               if key in planned):
            plan_issues.append('budget_differs_from_plan')
    status = read_issue or summary.get('status', 'missing_status')
    integrity = bool(summary) and (entry.get('sha256') is None or entry['sha256'] == digest)
    seed_matches = type(summary.get('seed')) is int and entry['seed'] == summary['seed']
    cost = cost_evidence(summary)
    cost_issues = []
    if not all(integer(cost[key]) for key in ('algorithm_requests', 'xyce_submitted', 'xyce_returned')):
        cost_issues.append('missing_or_invalid_cost_counts')
    elif not cost['algorithm_requests'] >= cost['xyce_submitted'] >= cost['xyce_returned']:
        cost_issues.append('inconsistent_cost_counts')
    if status in COMPLETED and (cost['unclassified_samples'] != 0 or cost['xyce_submitted'] != cost['xyce_returned']):
        cost_issues.append('unclassified_or_unreturned_samples')
    estimate = summary.get('pfail_estimate')
    log_estimate = summary.get('log10_pfail')
    numeric = (summary.get('diagnostics') or {}).get('numeric_status')
    warnings = list((summary.get('sampling_diagnostics') or {}).get('warnings', []))
    if finite(estimate) and estimate > 1 and 'estimate_above_one' not in warnings:
        warnings.append('estimate_above_one')
    status_allows = (status in COMPLETED and integrity and seed_matches and not cost_issues and not plan_issues
                     and integer(cost['formal_draws']) and cost['formal_draws'] > 0)
    linear = status_allows and finite(estimate) and estimate >= 0 and numeric not in ('underflow', 'overflow')
    log_only = status_allows and numeric in ('underflow', 'overflow') and finite(log_estimate)
    return dict(estimator=entry['estimator'], seed=entry['seed'], observed_seed=summary.get('seed'),
                case=summary.get('case'), implementation=summary.get('algorithm'), mode=profile.get('mode'),
                status=status, detail=summary.get('detail', summary.get('error')), numeric_status=numeric,
                pfail_estimate=estimate, log10_pfail=log_estimate, warnings=warnings,
                estimate_available=linear or log_only, linear_usable=linear, source_integrity=integrity,
                seed_matches=seed_matches, target=target, missing_target_fields=missing,
                strategy=strategy, cost=cost, cost_issues=cost_issues, source=source,
                planned=planned, plan_issues=plan_issues, plan_target_differences=plan_target_differences)


def evaluate_reference(entries, base, anchor, trial_seeds, trial_paths):
    if entries is None:
        return dict(status='not_specified', estimate=None, interval=None, sources=[])
    sources, issues, seeds = [], [], set()
    failures = draws = 0
    for entry in entries:
        item = {'run': entry} if isinstance(entry, str) else entry
        path = (base / item['run']).resolve()
        summary, digest, read_issue = read_summary(path)
        expected = item.get('sha256')
        record = source_evidence(path, summary, digest, expected, read_issue)
        if expected is None:
            record['issues'].append('reference_hash_not_pinned')
        sources.append(record)
        target, missing = target_evidence(summary)
        seed = summary.get('seed')
        cost = cost_evidence(summary)
        n, k = cost['xyce_returned'], summary.get('failure_count')
        errs = []
        if expected is not None and expected != digest:
            errs.append('reference_sha256_mismatch')
        if 'seed' in item and item['seed'] != seed:
            errs.append('reference_seed_differs_from_plan')
        if missing or anchor is None or target != anchor:
            errs.append('reference_target_mismatch_or_incomplete')
        if path in trial_paths or not integer(seed) or seed in (trial_seeds | seeds):
            errs.append('reference_not_seed_disjoint_or_duplicate')
        if integer(seed):
            seeds.add(seed)
        if (summary.get('algorithm') != 'repository_MC' or summary.get('status') != 'complete'
                or summary.get('sampling_source') != 'numpy_independent_positive_u0'
                or not integer(n) or n == 0 or n != cost['xyce_submitted'] or n != cost['budget']
                or cost['unclassified_samples'] != 0 or not integer(k) or k > n
                or not finite(summary.get('pfail_estimate')) or summary['pfail_estimate'] != k / n):
            errs.append('reference_not_complete_direct_mc')
        record.update(seed=seed, failure_count=k, returned=n, validation_issues=errs)
        issues.extend(errs)
        if not errs:
            failures += k
            draws += n
    if issues:
        return dict(status='invalid', issues=list(dict.fromkeys(issues)), estimate=None, interval=None, sources=sources)
    revisions = {s['git_revision'] for s in sources}
    solvers = {(s['solver'], s['solver_version']) for s in sources}
    complete = all(not s['issues'] for s in sources) and len(revisions) == len(solvers) == 1
    return dict(status='available', all_hashes_pinned=all(s['expected_summary_sha256'] for s in sources),
                provenance_complete=complete, estimate=failures / draws,
                failure_count=failures, samples=draws, interval=wilson_interval(failures, draws),
                confidence=.95, interval_method='Wilson', sources=sources)


def protocol_key(cost):
    unit = cost['budget_protocol']
    if unit in ('algorithm_draws', 'xyce_samples') and integer(cost['budget']):
        return (unit, cost['budget'], cost['time_limit_s'])
    if unit == 'wall_time' and finite(cost['time_limit_s']) and cost['time_limit_s'] > 0:
        return (unit, cost['time_limit_s'], None)
    return None


def agreement(values):
    known = {signature(value) for value in values if value is not None}
    if len(known) > 1:
        return 'different'
    return 'same' if known and all(value is not None for value in values) else 'unknown'


def eligible_for_metrics(row):
    return row['target_status'] == 'same' and row['linear_usable']


def summarize_methods(rows, reference):
    totals = Counter(row['estimator'] for row in rows)
    without_estimate = Counter(row['estimator'] for row in rows if not row['estimate_available'])
    cohorts = {}
    budget_fields = ('budget_protocol', 'budget', 'draw_cap', 'xyce_cap', 'estimation_cap',
                     'time_limit_s', 'legacy_budget_basis')
    for row in rows:
        if row['planned'] is not None:
            planned = row['planned']
            budget = {key: planned.get(key) for key in budget_fields}
            strategy = {'estimator_profile': planned['profile']}
            target_group = 'different' if row['target_status'] == 'different' else 'planned_target'
        else:
            budget = {key: row['cost'][key] for key in budget_fields}
            strategy = row['strategy']
            target_group = row['target_status']
        key = row['estimator'], target_group, signature(budget), signature(strategy)
        cohorts.setdefault(key, []).append(row)
    for (estimator, target_group, budget_id, strategy_id), trials in cohorts.items():
        matched = [row for row in trials if row['target_status'] == 'same']
        usable = [row for row in matched if eligible_for_metrics(row)]
        budget = json.loads(budget_id)
        # Unrecorded budget tiers stay visible but cannot substantiate a cross-seed error claim.
        known_tier = (integer(budget['budget']) and budget['budget'] > 0
                      or finite(budget['time_limit_s']) and budget['time_limit_s'] > 0)
        values = [r['pfail_estimate'] for r in usable] if known_tier else []
        center, interval = reference['estimate'], reference['interval']
        errors = ([abs(value - center) for value in values] if center is not None else [])
        n = len(trials)
        missing = Counter(row['estimate_status'] for row in trials if not row['estimate_available'])
        yield dict(estimator=estimator, target_cohort=target_group, budget_tier=budget,
                   strategy=json.loads(strategy_id), total_runs=n,
                   estimator_planned_runs=totals[estimator],
                   estimator_no_estimate_runs=without_estimate[estimator],
                   estimator_no_estimate_fraction=without_estimate[estimator] / totals[estimator],
                   target_matched_runs=len(matched),
                   estimates_available=sum(row['estimate_available'] for row in trials),
                   no_estimate_runs=sum(missing.values()), no_estimate_fraction=sum(missing.values()) / n,
                   no_estimate_reasons=dict(missing), linear_metrics_runs=len(values),
                   log_only_matched_runs=sum(row['estimate_available'] and not row['linear_usable'] for row in matched),
                   weight_warning_runs=sum('low_observed_weight_mass' in row['warnings'] for row in trials),
                   above_one_runs=sum('estimate_above_one' in row['warnings'] for row in trials),
                   mean_estimate=mean(values) if values else None,
                   budget_protocol_check=agreement([protocol_key(row['cost']) for row in matched]),
                   xyce_submitted_check=agreement([row['cost']['xyce_submitted'] for row in matched]),
                   seed_std=stdev(values) if len(values) > 1 else None,
                   reference_estimate=center, reference_interval=interval, error_runs=len(errors),
                   errors_with_unverified_or_different_source=sum(r['reference_source_match'] != 'same'
                                                                  for r in usable) if errors else 0,
                   mean_absolute_error=mean(errors) if errors else None,
                   rmse=math.hypot(*(error / math.sqrt(len(errors)) for error in errors)) if errors else None)


def analyze_group(group, base, planned):
    rows = [analyze_run(entry, base, planned.get((entry['estimator'], entry['seed'])))
            for entry in group['runs']]
    # The first declared run with an intact definition anchors the group. A failed
    # simulation can still define the target; success and weight quality play no role.
    anchor_row = next((r for r in rows if r['source_integrity'] and r['seed_matches']
                       and not r['missing_target_fields'] and not r['plan_target_differences']), None)
    anchor = anchor_row['target'] if anchor_row else None
    for row in rows:
        row['target_differences'] = [key for key, value in row['target'].items() if anchor and value != anchor[key]]
        row['target_status'] = ('different' if row['plan_target_differences']
                                else 'unknown' if anchor is None or row['missing_target_fields']
                                or not row['source_integrity'] or not row['seed_matches']
                                else 'different' if row['target_differences'] else 'same')
        row['estimate_status'] = ('linear' if row['linear_usable'] else 'log_only' if row['estimate_available']
                                  else row['status'] if row['status'] in ('missing_summary', 'invalid_summary')
                                  else 'integrity_or_seed' if not row['source_integrity'] or not row['seed_matches']
                                  else 'differs_from_plan' if row['plan_issues']
                                  else 'invalid_cost_evidence' if row['status'] in COMPLETED and row['cost_issues']
                                  else row['status'] if row['status'] not in COMPLETED else 'no_numeric_estimate')
    ref = evaluate_reference(group.get('reference_runs'), base, anchor, {r['seed'] for r in rows},
                             {(base / entry['run']).resolve() for entry in group['runs']})
    target_counts = Counter(row['target_status'] for row in rows)
    same = [row for row in rows if row['target_status'] == 'same']
    if ref['status'] == 'available':
        ref_sources = {(s['git_revision'], s['solver_version']) for s in ref['sources']}
    else:
        ref_sources = set()
    for row in rows:
        usable = eligible_for_metrics(row) and ref['status'] == 'available'
        row['absolute_error'] = abs(row['pfail_estimate'] - ref['estimate']) if usable else None
        key = (row['source']['git_revision'], row['source']['solver_version'])
        if not ref_sources:
            row['reference_source_match'] = 'not_available'
        elif None in key or row['source']['issues'] or not ref['provenance_complete']:
            row['reference_source_match'] = 'unverified'
        else:
            row['reference_source_match'] = 'same' if ref_sources == {key} else 'different'
    checks = dict(total_runs=len(rows), target_anchor=dict(estimator=anchor_row['estimator'], seed=anchor_row['seed'])
                  if anchor_row else None, target_same=target_counts['same'], target_different=target_counts['different'],
                  target_unknown=target_counts['unknown'], source_incomplete=sum(bool(r['source']['issues']) for r in rows),
                  cost_incomplete=sum(bool(r['cost_issues']) for r in rows),
                  plan_mismatch=sum(bool(r['plan_issues']) for r in rows),
                  plan_target_mismatch=sum(bool(r['plan_target_differences']) for r in rows),
                  run_list_basis='frozen_plan' if planned else 'manual_manifest',
                  source_revision_check=agreement([r['source']['git_revision'] for r in same]),
                  solver_version_check=agreement([r['source']['solver_version'] for r in same]),
                  budget_protocol_check=agreement([protocol_key(r['cost']) for r in same]),
                  draw_cap_check=agreement([r['cost']['draw_cap'] for r in same]),
                  formal_draw_cap_check=agreement([r['cost']['estimation_cap'] for r in same]),
                  xyce_submitted_check=agreement([r['cost']['xyce_submitted'] for r in same]),
                  xyce_returned_check=agreement([r['cost']['xyce_returned'] for r in same]),
                  time_limit_check=agreement([r['cost']['time_limit_s'] for r in same]),
                  reference_status=ref['status'], ranking_eligible=False)
    return dict(id=group['id'], checks=checks, runs=rows, reference=ref, methods=list(summarize_methods(rows, ref)))


def validate_manifest(manifest, base, output):
    if not isinstance(manifest, dict) or type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1:
        raise ValueError('Expected analysis manifest schema_version 1')
    groups = manifest.get('groups')
    if not isinstance(groups, list) or not groups:
        raise ValueError('Declare at least one analysis group')
    ids = set()
    for group in groups:
        if (not isinstance(group, dict) or not isinstance(group.get('id'), str)
                or not group['id'] or group['id'] in ids):
            raise ValueError('Each analysis group needs a unique id')
        ids.add(group['id'])
        entries, references = group.get('runs'), group.get('reference_runs')
        if not isinstance(entries, list) or not entries:
            raise ValueError('Each group needs a runs list')
        if 'reference_runs' in group and (not isinstance(references, list) or not references
                or not all(isinstance(ref, str) and ref or isinstance(ref, dict)
                           and isinstance(ref.get('run'), str) and ref['run']
                           and isinstance(ref.get('sha256'), str) and len(ref['sha256']) == 64
                           for ref in references)):
            raise ValueError('reference_runs must be a nonempty list of run directories')
        pairs, paths = set(), set()
        for entry in entries:
            if (not isinstance(entry, dict) or not isinstance(entry.get('run'), str) or not entry['run']
                    or not isinstance(entry.get('estimator'), str) or not entry['estimator']
                    or not integer(entry.get('seed'))):
                raise ValueError('Each run needs run, estimator and seed')
            pair, path = (entry['estimator'], entry['seed']), (base / entry['run']).resolve()
            if pair in pairs or path in paths:
                raise ValueError('Duplicate estimator/seed or run directory within a group')
            pairs.add(pair)
            paths.add(path)
        all_inputs = paths | {(base / (ref if isinstance(ref, str) else ref['run'])).resolve()
                             for ref in references or []}
        if output in all_inputs or any(path in output.parents for path in all_inputs):
            raise ValueError('Use a separate analysis output directory')


def write_table(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: signature(value) if isinstance(value, (list, dict)) else value
                             for key, value in row.items()})


def analyze(manifest_path, output):
    manifest_path, output = Path(manifest_path).resolve(), Path(output).resolve()
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    validate_manifest(manifest, manifest_path.parent, output)
    planned = load_planned_trials(manifest, manifest_path.parent)
    groups = [analyze_group(group, manifest_path.parent, planned) for group in manifest['groups']]
    result = dict(kind='offline_comparison', manifest_path=str(manifest_path),
                  manifest_sha256=hashlib.sha256(raw).hexdigest(),
                  ranking_eligible=False, groups=groups)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'input_manifest.json').write_bytes(raw)
    (output / 'analysis.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    write_table(output / 'runs.csv', [dict(group=g['id'], **row) for g in groups for row in g['runs']])
    write_table(output / 'groups.csv', [dict(group=g['id'], **g['checks']) for g in groups])
    write_table(output / 'references.csv', [dict(group=g['id'], **g['reference']) for g in groups])
    write_table(output / 'methods.csv', [dict(group=g['id'], **row) for g in groups for row in g['methods']])
    for group in groups:
        print(group['id'], group['checks'], flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        analyze(args.manifest, args.output_dir)
    except (OSError, ValueError) as exc:
        parser.exit(2, f'{exc}\n')


if __name__ == '__main__':
    main()
