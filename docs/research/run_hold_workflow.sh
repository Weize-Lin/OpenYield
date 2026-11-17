#!/usr/bin/env bash
# Preflight or run the bounded, serial hold-SNM workflow qualification.
set -euo pipefail
if (( $# != 2 )) || [[ $1 != preflight && $1 != execute ]]; then
    printf 'Usage: bash %s {preflight|execute} NEW_OUTPUT_DIR\n' "$0" >&2
    exit 2
fi
mode=$1
output=$(realpath -m -- "$2")
cd "$(git rev-parse --show-toplevel)"
config_dir=docs/research/hold_workflow_cases
solver=$(command -v Xyce)
version=$("$solver" -v)
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 CUDA_VISIBLE_DEVICES=''

if [[ $mode == execute && -n $(git status --porcelain) ]]; then
    printf 'Use a clean checkout. Archive the candidate revision before execution.\n' >&2
    exit 2
fi
mkdir -p -- "$(dirname -- "$output")"
available=$(df -Pk -- "$(dirname -- "$output")" | awk 'NR == 2 { print $4 }')
if (( available < 4194304 )); then
    printf 'At least 4 GiB of free disk space is needed before launch.\n' >&2
    exit 2
fi
mkdir -- "$output"
git rev-parse HEAD > "$output/source_revision.txt"
git status --porcelain > "$output/source_status.txt"
printf '%s\n' "$solver" "$version" > "$output/solver.txt"
sha256sum -- "$solver" tran_models/models_TT.spice environment.yml > "$output/runtime_sha256.txt"
conda list --export > "$output/packages.txt"

# Normalize and freeze both strategies before viewing any new reference results.
python - "$config_dir" "$output" <<'PY'
from pathlib import Path
import sys

from yield_estimation.evaluation.experiment import normalize_experiment, write_json

source, output = map(Path, sys.argv[1:])
reference = normalize_experiment(source / 'reference.json')
comparison = normalize_experiment(source / 'comparison.json')
assert reference['experiment'] == comparison['experiment']
assert set(reference['seeds']).isdisjoint(comparison['seeds'])
for name, config in (('reference', reference), ('comparison', comparison)):
    write_json(output / f'{name}_config.json', config)
    calls = len(config['estimators']) * len(config['seeds'])
    print(f'{name}: {calls} planned runs, {calls * config["budget_per_algorithm"]} sample rows at most')
    print('strategies:', ', '.join(profile['algorithm'] for profile in config['estimators']))
PY
sha256sum -- "$output/reference_config.json" "$output/comparison_config.json" > "$output/config_sha256.txt"
printf '%s\n' "$version"
if [[ $mode == preflight ]]; then
    printf 'Preflight complete. No simulator jobs were launched.\n'
    exit 0
fi

# The outer timeouts apply to the process group including Xyce. No failed run
# is relaunched by this script. Preserve each phase's exit status and wall time.
run_phase() {
    local name=$1 limit=$2 started=$SECONDS status=0
    shift 2
    timeout --kill-after=30s "$limit" python -m yield_estimation.evaluation.experiment \
        --config "$output/${name}_config.json" --output-dir "$output/$name" "$@" \
        > "$output/$name.log" 2>&1 || status=$?
    printf '%s\t%s\t%s\n' "$name" "$status" "$((SECONDS-started))" >> "$output/phase_status.tsv"
    return "$status"
}
printf 'phase\texit_code\twall_seconds\n' > "$output/phase_status.tsv"
sha256sum --check --status "$output/config_sha256.txt"
reference_status=0
run_phase reference 10m || reference_status=$?
if [[ -f $output/reference/analysis_manifest.json ]]; then
    python -m yield_estimation.evaluation.analyze_results --manifest "$output/reference/analysis_manifest.json" \
        --output-dir "$output/reference_check" > "$output/reference_check.log" 2>&1
fi
if (( reference_status != 0 )); then
    printf 'Reference failed or timed out. Comparison was not started.\n' >&2
    exit "$reference_status"
fi

python - "$output" <<'PY'
import json
from pathlib import Path
import sys

output = Path(sys.argv[1])
group, = json.loads((output / 'reference_check/analysis.json').read_text())['groups']
runs = group['runs']
valid = (len(runs) == 4 and group['checks']['target_same'] == 4
         and all(row['status'] == 'complete' and row['estimate_available']
                 and row['cost']['xyce_submitted'] == row['cost']['xyce_returned'] == 256
                 for row in runs))
if not valid:
    raise SystemExit('Reference evidence is incomplete. Comparison was not started.')
PY
sha256sum --check --status "$output/config_sha256.txt"
reference_args=()
for seed in 8311 8312 8313 8314; do
    reference_args+=(--reference-run "$output/reference/mc/seed_$seed/mc")
done
comparison_status=0
run_phase comparison 50m "${reference_args[@]}" || comparison_status=$?
if [[ -f $output/comparison/analysis_manifest.json ]]; then
    python -m yield_estimation.evaluation.analyze_results --manifest "$output/comparison/analysis_manifest.json" \
        --output-dir "$output/comparison_report" > "$output/comparison_report.log" 2>&1
fi
exit "$comparison_status"
