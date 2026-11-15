"""Outcome rules shared by live MC evaluation and offline threshold reassessment."""
import math

PASS = "pass"
TIMING_FAILURE = "timing_failure"
FUNCTIONAL_FAILURE = "read_functional_failure"
WRITE_FUNCTIONAL_FAILURE = "write_functional_failure"
UNCLASSIFIED = "unclassified"
RULE_VERSION = "read_outcomes_v1"
WRITE_RULE_VERSION = "write_outcomes_v1"


def threshold_seconds(threshold_ns):
    if type(threshold_ns) not in (int, float) or not math.isfinite(threshold_ns):
        raise ValueError("threshold_ns must be a finite number")
    seconds = threshold_ns * 1e-9
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("threshold_ns must be positive and representable in seconds")
    return seconds


def _classify_delay(delay_s, threshold_s, operation, failure_outcome, functional_failure):
    if not math.isfinite(threshold_s) or threshold_s <= 0:
        raise ValueError(f"{operation.capitalize()} threshold must be positive and finite")
    if functional_failure:
        return failure_outcome
    if delay_s is None or not math.isfinite(delay_s) or delay_s < 0:
        raise ValueError(f"A classified {operation} needs a finite, nonnegative delay")
    return TIMING_FAILURE if delay_s > threshold_s else PASS


def classify_read(delay_s, threshold_s, *, functional_failure=False):
    """Require electrical evidence before labeling a read functional failure."""
    return _classify_delay(delay_s, threshold_s, "read", FUNCTIONAL_FAILURE, functional_failure)


def classify_write(delay_s, threshold_s, *, functional_failure=False):
    """Require electrical evidence before labeling a write functional failure."""
    return _classify_delay(delay_s, threshold_s, "write", WRITE_FUNCTIONAL_FAILURE, functional_failure)
