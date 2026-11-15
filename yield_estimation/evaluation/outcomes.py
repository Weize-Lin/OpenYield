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


class MeasurementRule:
    """One physical measurement and one failure event per configured operation."""

    DELAYS = {
        "read": ("TREAD_TOTAL", "read_delay_s", FUNCTIONAL_FAILURE, RULE_VERSION),
        "write": ("TWRITE_TOTAL", "write_delay_s", WRITE_FUNCTIONAL_FAILURE, WRITE_RULE_VERSION),
        "read&write": ("TVOUT_PERIOD", "output_period_s", "read_write_functional_failure", "read_write_outcomes_v1"),
    }
    SNM_OPERATIONS = ("hold_snm", "read_snm", "write_snm")

    @classmethod
    def threshold_key_for(cls, operation):
        return "threshold_mv" if operation in cls.SNM_OPERATIONS else "threshold_ns"

    def __init__(self, operation, config):
        self.is_snm = operation in self.SNM_OPERATIONS
        self.threshold_key = self.threshold_key_for(operation)
        self.threshold_value = config[self.threshold_key]
        if self.is_snm:
            if (type(self.threshold_value) not in (int, float)
                    or not math.isfinite(self.threshold_value) or self.threshold_value <= 0):
                raise ValueError("threshold_mv must be positive and finite")
            self.threshold = self.threshold_value * 1e-3
            if not math.isfinite(self.threshold) or self.threshold <= 0:
                raise ValueError("threshold_mv must be representable in volts")
            self.metric, self.column = operation, operation + "_v"
            self.functional_failure, self.version = None, "snm_outcomes_v1"
            self.numeric_failure, self.unit, self.operator = "margin_failure", "mV", "<"
        else:
            self.threshold = threshold_seconds(self.threshold_value)
            self.metric, self.column, self.functional_failure, self.version = self.DELAYS[operation]
            self.numeric_failure, self.unit, self.operator = TIMING_FAILURE, "ns", ">"

    def classify(self, measurement):
        if self.is_snm:
            if not math.isfinite(measurement) or measurement < 0:
                raise ValueError("A classified SNM sample needs a finite, nonnegative margin")
            return self.numeric_failure if measurement < self.threshold else PASS
        return _classify_delay(measurement, self.threshold, "access", self.functional_failure,
                               math.isnan(measurement))

    def failures(self, measurements):
        import numpy as np

        values = np.asarray(measurements)
        exceeded = values < self.threshold if self.is_snm else values > self.threshold
        return ~np.isfinite(values) | (values < 0) | exceeded

    def metadata(self):
        return dict(measurement=self.metric, measurement_column=self.column,
                    failure_operator=self.operator, threshold_field=self.threshold_key,
                    outcome_rule_version=self.version)
