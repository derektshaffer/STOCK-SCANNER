"""Canonical Analyzer compatibility/version contract.

Any change that alters logged feature semantics, production score formulas, or
calibration outcome semantics must bump the corresponding version here. Keeping
these values in one module prevents the live Analyzer, prediction logger, and
durable outcome scorer from silently disagreeing about which rows are
comparable.
"""

ANALYZER_FEATURE_VERSION = "analyzer-features-v7-integrity-contract"
DECISION_SCORE_VERSION = "decision-v2.8-integrity-gated"
TIMEFRAME_SCORE_VERSION = "timeframe-fit-v1"
CALIBRATION_SCHEMA_VERSION = 8
