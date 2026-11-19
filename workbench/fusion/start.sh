#!/usr/bin/env bash
set -euo pipefail
fusion_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo="${OPENYIELD_REPOSITORY:-$(cd -- "$fusion_dir/../.." && pwd)}"
python="${OPENYIELD_PYTHON:-python}"
export OPENYIELD_REPOSITORY="$repo"
exec "$python" -B -u "$fusion_dir/server.py" "$@"
