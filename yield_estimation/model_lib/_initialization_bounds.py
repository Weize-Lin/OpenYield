"""Read legacy bounds or validate explicit bounds for an experimental run."""

from pathlib import Path

import numpy as np


def resolve_initialization_bounds(feature_num, supplied, legacy_files):
    if supplied is None:
        if feature_num not in legacy_files:
            raise ValueError(f"No legacy initialization bounds for {feature_num} parameters")
        path = Path(legacy_files[feature_num])
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[1] / "bound_lib" / path
        values = np.loadtxt(path, dtype=np.float64)
    else:
        values = np.asarray(supplied, dtype=float)
    if (values.shape != (2, feature_num) or not np.isfinite(values).all()
            or not (values[1] < values[0]).all()):
        raise ValueError("Initialization bounds require finite upper/lower rows of the configured dimension")
    return values
