"""Outcome rules shared by live MC evaluation and offline threshold reassessment."""
import math

PASS = "pass"
TIMING_FAILURE = "timing_failure"
FUNCTIONAL_FAILURE = "read_functional_failure"
UNCLASSIFIED = "unclassified"
RULE_VERSION = "read_outcomes_v1"


def threshold_seconds(threshold_ns):
    if type(threshold_ns) not in (int, float) or not math.isfinite(threshold_ns):
        raise ValueError("threshold_ns must be a finite number")
    seconds = threshold_ns * 1e-9
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("threshold_ns must be positive and representable in seconds")
    return seconds


def classify_read(delay_s, threshold_s, *, functional_failure=False):
    """NaN alone is not electrical evidence; the caller must confirm a read failure."""
    if not math.isfinite(threshold_s) or threshold_s <= 0:
        raise ValueError("Read threshold must be positive and finite")
    if functional_failure:
        return FUNCTIONAL_FAILURE
    if delay_s is None or not math.isfinite(delay_s) or delay_s < 0:
        raise ValueError("A classified read needs a finite, nonnegative delay")
    return TIMING_FAILURE if delay_s > threshold_s else PASS
