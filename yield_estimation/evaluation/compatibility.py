"""Software contract for workbench clients, independent of algorithm identity.

This version certifies API shape only, not scientific algorithm validity.
Normal experiment summaries remain responsible for source/model/solver evidence.
"""
API_VERSION = 1
CAPABILITIES = frozenset({'normalize_experiment', 'run_experiment', 'analyze', 'frozen_suite_v1'})
