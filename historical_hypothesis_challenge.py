from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

import scanner_ml_ranker as sm

ROOT = Path(__file__).resolve().parent
DEFAULT_REPLAY = ROOT / "outcome_reports" / "outcomes_historical_replay.json"
DEFAULT_AUDIT = ROOT / "learning_audits" / "latest_learning_audit.json"
DEFAULT_OUTPUT_DIR = ROOT / "hypothesis_challenges"

MIN_CONFIRMATION_DAYS = 5
MIN_CONFIRMATION_SYMBOLS = 15
MIN_CONFIRMATION_SAMPLES = 100
SAME_SYMBOL_GAP_SECONDS = 60 * 60


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _parse_dt(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _day(row):
    return str(row.get("scan_time_et") or "")[:10]


def _independent_rows(rows, min_gap_seconds=SAME_SYMBOL_GAP_SECONDS):
    kept = []
    last_by_symbol = {}
    for row in sorted(
        rows,
        key=lambda item: (
            str(item.get("scan_time_et") or ""),
            str(item.get("symbol") or ""),
        ),
    ):
        symbol = str(row.get("symbol") or "").upper().strip()
        dt = _parse_dt(row.get("scan_time_et"))
        if not symbol or dt is None:
            continue
        last = last_by_symbol.get(symbol)
        if last is not None and (dt - last).total_seconds() < min_gap_seconds:
            continue
        last_by_symbol[symbol] = dt
        kept.append(row)
    return kept


def _auc(y, scores):
    return sm._auc(
        [int(value) for value in y],
        [float(value) for value in scores],
    )


def _brier(y, probabilities):
    if not y:
        return None
    return mean(
        (float(probability) - int(actual)) ** 2
        for probability, actual in zip(probabilities, y)
    )


def _ece(y, probabilities, bins=10):
    if not y:
        return None
    buckets = [[] for _ in range(bins)]
    for actual, probability in zip(y, probabilities):
        probability = max(0.0, min(1.0, float(probability)))
        idx = min(bins - 1, int(probability * bins))
        buckets[idx].append((int(actual), probability))
    total = len(y)
    error = 0.0
    for bucket in buckets:
        if not bucket:
            continue
        observed = mean(actual for actual, _ in bucket)
        predicted = mean(probability for _, probability in bucket)
        error += len(bucket) / total * abs(observed - predicted)
    return error


def _top_fraction_indices(values, fraction=0.10):
    if not values:
        return []
    count = max(1, int(round(len(values) * fraction)))
    ranked = sorted(range(len(values)), key=lambda i: values[i], reverse=True)
    return ranked[:count]


def _rate(y, indices=None):
    values = (
        [int(y[i]) for i in indices]
        if indices is not None
        else list(map(int, y))
    )
    return mean(values) if values else None


def _proxy_return(row, friction_pct=0.0):
    if row.get("opportunity_up_5_60m_before_stop") is True:
        gross = 5.0
    elif row.get("opportunity_failure_stop_60m_hit") is True:
        gross = -3.0
    else:
        ret = _num(row.get("return_60m_pct"))
        if ret is None:
            return None
        gross = max(-3.0, min(5.0, ret))
    return gross - float(friction_pct)


def _utility(rows, indices):
    selected = [rows[i] for i in indices]
    result = {}
    for friction in (0.0, 0.25, 0.50, 1.00):
        values = []
        for row in selected:
            value = _proxy_return(row, friction)
            if value is not None:
                values.append(value)
        result[f"avg_proxy_return_after_{friction:.2f}pct_friction"] = (
            round(mean(values), 4) if values else None
        )
    return result


def _hand_scores(rows):
    values = []
    for row in rows:
        score = _num(
            row.get("opportunity_score")
            if row.get("opportunity_score") is not None
            else row.get("score")
        )
        values.append(score if score is not None else 0.0)
    return values


def _matrix(rows, np):
    feature_rows = [
        sm._feature_dict(row, row.get("scan_time_et"))
        for row in rows
    ]
    return np.array(
        [
            [
                np.nan
                if features.get(name) is None
                else float(features.get(name))
                for name in sm.FEATURES
            ]
            for features in feature_rows
        ],
        dtype=float,
    )


def _path_label(row):
    if row.get("opportunity_horizon_60m_complete") is not True:
        return None
    return 1 if row.get("opportunity_up_5_60m_before_stop") is True else 0


def _endpoint_label(row):
    value = _num(row.get("return_60m_pct"))
    return None if value is None else int(value >= 3.0)


def _walk_forward_model(rows, label_fn):
    try:
        import numpy as np
        import xgboost as xgb
    except Exception as exc:
        return {
            "status": "dependency_missing",
            "error": str(exc)[:180],
        }

    labeled = []
    for row in rows:
        label = label_fn(row)
        if label is None:
            continue
        item = dict(row)
        item["_challenge_label"] = int(label)
        labeled.append(item)

    independent = _independent_rows(labeled)
    days = sorted({_day(row) for row in independent if _day(row)})
    if (
        len(independent) < MIN_CONFIRMATION_SAMPLES
        or len(days) < MIN_CONFIRMATION_DAYS + 3
    ):
        return {
            "status": "insufficient_data",
            "samples": len(independent),
            "days": len(days),
        }

    split = max(3, int(len(days) * 0.60))
    split = min(split, len(days) - MIN_CONFIRMATION_DAYS)
    discovery_days = set(days[:split])
    confirmation_days = set(days[split:])
    discovery = [
        row for row in independent if _day(row) in discovery_days
    ]
    confirmation = [
        row for row in independent if _day(row) in confirmation_days
    ]

    y_train = [int(row["_challenge_label"]) for row in discovery]
    y_test = [int(row["_challenge_label"]) for row in confirmation]
    if (
        len(discovery) < 100
        or len(confirmation) < MIN_CONFIRMATION_SAMPLES
        or len(set(y_train)) < 2
        or len(set(y_test)) < 2
    ):
        return {
            "status": "insufficient_class_balance",
            "discovery_samples": len(discovery),
            "confirmation_samples": len(confirmation),
            "discovery_positives": sum(y_train),
            "confirmation_positives": sum(y_test),
        }

    X_train = _matrix(discovery, np)
    X_test = _matrix(confirmation, np)
    model = xgb.train(
        sm._params(),
        xgb.DMatrix(
            X_train,
            label=np.array(y_train, dtype=float),
            feature_names=sm.FEATURES,
        ),
        num_boost_round=120,
        verbose_eval=False,
    )
    probabilities = [
        float(value)
        for value in model.predict(
            xgb.DMatrix(X_test, feature_names=sm.FEATURES)
        )
    ]

    base_rate = mean(y_train)
    naive_probs = [base_rate] * len(y_test)
    hand = _hand_scores(confirmation)
    model_auc = _auc(y_test, probabilities)
    hand_auc = _auc(y_test, hand)
    model_brier = _brier(y_test, probabilities)
    naive_brier = _brier(y_test, naive_probs)
    model_ece = _ece(y_test, probabilities)

    model_top = _top_fraction_indices(probabilities)
    hand_top = _top_fraction_indices(hand)
    base_rate_test = _rate(y_test)
    model_top_rate = _rate(y_test, model_top)
    hand_top_rate = _rate(y_test, hand_top)

    per_day = {}
    for day in sorted(confirmation_days):
        idx = [
            i
            for i, row in enumerate(confirmation)
            if _day(row) == day
        ]
        if not idx:
            continue
        day_base = _rate(y_test, idx)
        day_model_idx = sorted(
            idx,
            key=lambda i: probabilities[i],
            reverse=True,
        )[: max(1, int(round(len(idx) * 0.10)))]
        day_model = _rate(y_test, day_model_idx)
        per_day[day] = {
            "n": len(idx),
            "base_rate_pct": (
                round(day_base * 100.0, 2)
                if day_base is not None
                else None
            ),
            "model_top_decile_rate_pct": (
                round(day_model * 100.0, 2)
                if day_model is not None
                else None
            ),
            "lift_pp": (
                round((day_model - day_base) * 100.0, 2)
                if day_model is not None and day_base is not None
                else None
            ),
        }

    positive_lift_days = sum(
        1
        for stats in per_day.values()
        if _num(stats.get("lift_pp")) is not None
        and stats["lift_pp"] > 0
    )
    eligible_days = sum(
        1
        for stats in per_day.values()
        if _num(stats.get("lift_pp")) is not None
    )

    selected_rows = [confirmation[i] for i in model_top]
    selected_symbols = Counter(
        str(row.get("symbol") or "").upper().strip()
        for row in selected_rows
        if str(row.get("symbol") or "").strip()
    )
    symbol_count = len(selected_symbols)
    top_symbol_share = (
        max(selected_symbols.values()) / len(selected_rows)
        if selected_rows and selected_symbols
        else None
    )

    regime_groups = defaultdict(list)
    for i in model_top:
        label = str(confirmation[i].get("regime_label") or "UNKNOWN")
        regime_groups[label].append(i)
    regime_summary = {}
    for label, indices in sorted(regime_groups.items()):
        rate = _rate(y_test, indices)
        regime_summary[label] = {
            "selected_n": len(indices),
            "target_rate_pct": (
                round(rate * 100.0, 2)
                if rate is not None
                else None
            ),
        }

    test_symbols = {
        str(row.get("symbol") or "").upper().strip()
        for row in confirmation
        if str(row.get("symbol") or "").strip()
    }

    return {
        "status": "complete",
        "split_unit": "whole_trading_day",
        "target_horizon_minutes": 60,
        "same_symbol_min_gap_seconds": SAME_SYMBOL_GAP_SECONDS,
        "discovery_days": sorted(discovery_days),
        "confirmation_days": sorted(confirmation_days),
        "discovery_samples": len(discovery),
        "confirmation_samples": len(confirmation),
        "confirmation_symbols": len(test_symbols),
        "discovery_positive_rate_pct": round(mean(y_train) * 100.0, 2),
        "confirmation_positive_rate_pct": round(mean(y_test) * 100.0, 2),
        "model_auc": (
            round(model_auc, 4)
            if model_auc is not None
            else None
        ),
        "hand_score_auc": (
            round(hand_auc, 4)
            if hand_auc is not None
            else None
        ),
        "model_minus_hand_auc": (
            round(model_auc - hand_auc, 4)
            if model_auc is not None and hand_auc is not None
            else None
        ),
        "model_brier": (
            round(model_brier, 5)
            if model_brier is not None
            else None
        ),
        "naive_brier": (
            round(naive_brier, 5)
            if naive_brier is not None
            else None
        ),
        "brier_improvement": (
            round(naive_brier - model_brier, 5)
            if model_brier is not None and naive_brier is not None
            else None
        ),
        "calibration_ece": (
            round(model_ece, 5)
            if model_ece is not None
            else None
        ),
        "model_top_decile_target_rate_pct": (
            round(model_top_rate * 100.0, 2)
            if model_top_rate is not None
            else None
        ),
        "hand_top_decile_target_rate_pct": (
            round(hand_top_rate * 100.0, 2)
            if hand_top_rate is not None
            else None
        ),
        "base_target_rate_pct": (
            round(base_rate_test * 100.0, 2)
            if base_rate_test is not None
            else None
        ),
        "model_top_decile_lift_pp": (
            round((model_top_rate - base_rate_test) * 100.0, 2)
            if model_top_rate is not None and base_rate_test is not None
            else None
        ),
        "hand_top_decile_lift_pp": (
            round((hand_top_rate - base_rate_test) * 100.0, 2)
            if hand_top_rate is not None and base_rate_test is not None
            else None
        ),
        "execution_proxy": {
            "definition": (
                "+5% when +5 occurs before -3%; -3% when -3% occurs first; "
                "otherwise capped 60-minute return, then friction sensitivity."
            ),
            "not_realized_pnl": True,
            "historical_spread_missing": True,
            "model_top_decile": _utility(confirmation, model_top),
            "hand_score_top_decile": _utility(confirmation, hand_top),
        },
        "stability": {
            "per_confirmation_day": per_day,
            "positive_lift_days": positive_lift_days,
            "eligible_days": eligible_days,
            "positive_lift_day_fraction": (
                round(positive_lift_days / eligible_days, 4)
                if eligible_days
                else None
            ),
            "selected_distinct_symbols": symbol_count,
            "selected_top_symbol_share_pct": (
                round(top_symbol_share * 100.0, 2)
                if top_symbol_share is not None
                else None
            ),
            "selected_by_regime": regime_summary,
            "regimes_represented": len(
                [label for label in regime_groups if label != "UNKNOWN"]
            ),
        },
    }


def _path_target_challenge(rows):
    model = _walk_forward_model(rows, _path_label)
    comparable = []
    contradictions = []
    for row in _independent_rows(rows):
        path = _path_label(row)
        endpoint = _endpoint_label(row)
        if path is None or endpoint is None:
            continue
        comparable.append(row)
        if path == 1 and endpoint == 0:
            contradictions.append(row)

    contradiction_symbols = {
        str(row.get("symbol") or "").upper().strip()
        for row in contradictions
        if str(row.get("symbol") or "").strip()
    }
    contradiction_days = {
        _day(row) for row in contradictions if _day(row)
    }
    contradiction_rate = (
        len(contradictions) / len(comparable)
        if comparable
        else None
    )

    model_complete = model.get("status") == "complete"
    stability = model.get("stability") or {}
    pass_model = bool(
        model_complete
        and _num(model.get("model_auc")) is not None
        and model["model_auc"] >= 0.55
        and _num(model.get("model_brier")) is not None
        and _num(model.get("naive_brier")) is not None
        and model["model_brier"] < model["naive_brier"]
        and _num(model.get("model_top_decile_lift_pp")) is not None
        and model["model_top_decile_lift_pp"] > 0
    )
    pass_stability = bool(
        int(stability.get("eligible_days") or 0) >= MIN_CONFIRMATION_DAYS
        and (_num(stability.get("positive_lift_day_fraction")) or 0.0) >= 0.50
        and int(stability.get("selected_distinct_symbols") or 0) >= 10
        and (
            _num(stability.get("selected_top_symbol_share_pct"))
            or 100.0
        ) <= 20.0
        and int(stability.get("regimes_represented") or 0) >= 2
    )
    target_difference_real = bool(
        len(comparable) >= 100
        and len(contradictions) >= 5
        and len(contradiction_symbols) >= 3
        and len(contradiction_days) >= 3
        and contradiction_rate is not None
        and contradiction_rate >= 0.02
    )

    if model.get("status") in {
        "insufficient_data",
        "insufficient_class_balance",
        "dependency_missing",
    }:
        decision = "blocked_insufficient_historical_evidence"
    elif not target_difference_real:
        decision = "rejected_not_distinct_enough"
    elif not pass_model:
        decision = "rejected_no_predictive_skill"
    elif not pass_stability:
        decision = "rejected_unstable"
    else:
        decision = "historically_supported_shadow_only"

    return {
        "challenge_id": "path_target_candidate",
        "decision": decision,
        "production_influence": False,
        "target_difference": {
            "comparable_n": len(comparable),
            "path_positive_endpoint_negative_n": len(contradictions),
            "distinct_symbols": len(contradiction_symbols),
            "distinct_days": len(contradiction_days),
            "rate_pct": (
                round(contradiction_rate * 100.0, 2)
                if contradiction_rate is not None
                else None
            ),
        },
        "predictive_challenge": model,
        "gates": {
            "target_difference_real": target_difference_real,
            "model_skill": pass_model,
            "stability": pass_stability,
        },
    }


def _score_monotonicity_challenge(rows):
    independent = [
        row
        for row in _independent_rows(rows)
        if _path_label(row) is not None
    ]
    days = sorted({_day(row) for row in independent if _day(row)})
    if len(days) < 8:
        return {
            "challenge_id": "score_monotonicity_candidate",
            "decision": "blocked_insufficient_historical_evidence",
            "production_influence": False,
        }
    split = min(max(3, int(len(days) * 0.60)), len(days) - 3)
    periods = {
        "discovery": set(days[:split]),
        "confirmation": set(days[split:]),
    }

    def bucket(score):
        score = _num(score)
        if score is None:
            return "unknown"
        if score >= 80:
            return "80+"
        if score >= 70:
            return "70-79"
        if score >= 60:
            return "60-69"
        if score >= 50:
            return "50-59"
        return "<50"

    summaries = {}
    inversions = {}
    order = ["<50", "50-59", "60-69", "70-79", "80+"]
    for name, allowed_days in periods.items():
        groups = defaultdict(list)
        for row in independent:
            if _day(row) not in allowed_days:
                continue
            score = (
                row.get("opportunity_score")
                if row.get("opportunity_score") is not None
                else row.get("score")
            )
            groups[bucket(score)].append(_path_label(row))
        stats = {
            key: {
                "n": len(groups.get(key) or []),
                "target_rate_pct": (
                    round(mean(groups[key]) * 100.0, 2)
                    if groups.get(key)
                    else None
                ),
            }
            for key in order
        }
        summaries[name] = stats
        found = []
        for low, high in zip(order, order[1:]):
            a = stats[low]
            b = stats[high]
            if (
                a["n"] >= 30
                and b["n"] >= 30
                and a["target_rate_pct"] is not None
                and b["target_rate_pct"] is not None
                and a["target_rate_pct"] >= b["target_rate_pct"] + 10.0
            ):
                found.append(
                    {
                        "lower_bucket": low,
                        "higher_bucket": high,
                        "lower_rate_pct": a["target_rate_pct"],
                        "higher_rate_pct": b["target_rate_pct"],
                    }
                )
        inversions[name] = found

    discovery_pairs = {
        (item["lower_bucket"], item["higher_bucket"])
        for item in inversions["discovery"]
    }
    confirmation_pairs = {
        (item["lower_bucket"], item["higher_bucket"])
        for item in inversions["confirmation"]
    }
    repeated = discovery_pairs & confirmation_pairs
    return {
        "challenge_id": "score_monotonicity_candidate",
        "decision": (
            "historically_supported_shadow_only"
            if repeated
            else "rejected_not_repeated_out_of_sample"
        ),
        "production_influence": False,
        "split_unit": "whole_trading_day",
        "summaries": summaries,
        "inversions": inversions,
        "repeated_inversions": [
            list(value) for value in sorted(repeated)
        ],
    }


def _missed_explosive_challenge(rows):
    independent = [
        row
        for row in _independent_rows(rows)
        if _path_label(row) is not None
    ]
    days = sorted({_day(row) for row in independent if _day(row)})
    if len(days) < 8:
        return {
            "challenge_id": "missed_explosive_filter_candidate",
            "decision": "blocked_insufficient_historical_evidence",
            "production_influence": False,
        }
    split = min(max(3, int(len(days) * 0.60)), len(days) - 3)
    discovery_days = set(days[:split])
    confirmation_days = set(days[split:])

    def explosive(row):
        return bool(
            row.get("opportunity_up_10_60m_before_stop") is True
            or (
                _num(row.get("opportunity_mfe_60m_pct"))
                or -999.0
            ) >= 10.0
        )

    def collect(allowed_days):
        subset = [
            row
            for row in independent
            if _day(row) in allowed_days
            and (
                (_num(row.get("rank")) or 0) > 15
                or not bool(row.get("passed_base_filters"))
            )
        ]
        hits = [row for row in subset if explosive(row)]
        filters = Counter(
            str(reason)
            for row in hits
            for reason in (row.get("failed_filters") or [])
        )
        return {
            "eligible_n": len(subset),
            "explosive_n": len(hits),
            "explosive_rate_pct": (
                round(len(hits) / len(subset) * 100.0, 2)
                if subset
                else None
            ),
            "distinct_symbols": len(
                {
                    str(row.get("symbol") or "").upper().strip()
                    for row in hits
                    if str(row.get("symbol") or "").strip()
                }
            ),
            "failed_filters": dict(filters.most_common(20)),
        }

    discovery = collect(discovery_days)
    confirmation = collect(confirmation_days)
    repeated_filters = set(discovery["failed_filters"]) & set(
        confirmation["failed_filters"]
    )
    supported = bool(
        discovery["explosive_n"] >= 5
        and confirmation["explosive_n"] >= 5
        and discovery["distinct_symbols"] >= 3
        and confirmation["distinct_symbols"] >= 3
        and repeated_filters
    )
    return {
        "challenge_id": "missed_explosive_filter_candidate",
        "decision": (
            "historically_supported_shadow_only"
            if supported
            else "rejected_not_repeated_out_of_sample"
        ),
        "production_influence": False,
        "split_unit": "whole_trading_day",
        "discovery": discovery,
        "confirmation": confirmation,
        "repeated_failed_filters": sorted(repeated_filters),
    }


def _session_specific_challenge(rows):
    phases = Counter(
        str(row.get("session_phase") or "regular").lower()
        for row in rows
    )
    if len([phase for phase, n in phases.items() if n >= 100]) < 2:
        return {
            "challenge_id": "session_specific_calibration_candidate",
            "decision": "blocked_requires_extended_historical_replay",
            "production_influence": False,
            "historical_phase_counts": dict(phases),
            "reason": (
                "Current historical Scanner replay is regular-session only. "
                "Phase-specific calibration cannot be honestly confirmed from it."
            ),
        }
    return {
        "challenge_id": "session_specific_calibration_candidate",
        "decision": "blocked_multi_session_challenge_not_ready",
        "production_influence": False,
        "historical_phase_counts": dict(phases),
    }


CHALLENGERS = {
    "path_target_candidate": _path_target_challenge,
    "score_monotonicity_candidate": _score_monotonicity_challenge,
    "missed_explosive_filter_candidate": _missed_explosive_challenge,
    "session_specific_calibration_candidate": _session_specific_challenge,
}


def _load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _candidate_hypotheses(audit, include_standing=True):
    hypotheses = list((audit or {}).get("hypotheses") or [])
    ids = {str(row.get("id") or "") for row in hypotheses}
    source_ids = {
        str(row.get("id") or "")
        for row in ((audit or {}).get("source_findings") or [])
    }
    if (
        include_standing
        and "single_endpoint_primary_target" in source_ids
        and "path_target_candidate" not in ids
    ):
        hypotheses.append(
            {
                "id": "path_target_candidate",
                "status": "standing_specification_candidate",
                "statement": (
                    "A path-aware target may preserve useful momentum discoveries "
                    "that the +60m endpoint target labels negative."
                ),
                "production_influence": False,
            }
        )
    return hypotheses


def run_challenge(replay_payload, audit_payload, include_standing=True):
    rows = list((replay_payload or {}).get("observations") or [])
    replay_meta = (replay_payload or {}).get("replay") or {}
    hypotheses = _candidate_hypotheses(
        audit_payload,
        include_standing=include_standing,
    )

    results = []
    for hypothesis in hypotheses:
        hypothesis_id = str(hypothesis.get("id") or "")
        challenge = CHALLENGERS.get(hypothesis_id)

        evidence_window = hypothesis.get("evidence_window") or {}
        evidence_start = str(evidence_window.get("start_date") or "").strip()
        if evidence_start:
            # Historical challenge data must predate the earliest live/shadow
            # evidence used to invent the hypothesis. This prevents the same
            # trading day from helping both generate and "confirm" an idea.
            challenge_rows = [
                row
                for row in rows
                if _day(row) and _day(row) < evidence_start
            ]
        else:
            # Standing specification hypotheses originate from code/objective
            # review rather than empirical outcome rows, so the full causal
            # replay is an independent challenge set.
            challenge_rows = rows

        independence = {
            "hypothesis_evidence_start_date": evidence_start or None,
            "hypothesis_evidence_end_date": (
                evidence_window.get("end_date")
                if evidence_window
                else None
            ),
            "historical_replay_used_to_generate": bool(
                evidence_window.get("historical_replay_used_to_generate")
            ),
            "challenge_rows_before_evidence_window": len(challenge_rows),
            "challenge_rows_total_replay": len(rows),
            "independent": not bool(
                evidence_window.get("historical_replay_used_to_generate")
            ),
        }

        if challenge is None:
            result = {
                "challenge_id": hypothesis_id,
                "decision": "blocked_no_challenge_spec",
                "production_influence": False,
            }
        elif evidence_start and not challenge_rows:
            result = {
                "challenge_id": hypothesis_id,
                "decision": "blocked_no_pre_evidence_historical_holdout",
                "production_influence": False,
                "reason": (
                    "No replay observations predate the live/shadow evidence "
                    "window that generated this hypothesis."
                ),
            }
        elif not independence["independent"]:
            result = {
                "challenge_id": hypothesis_id,
                "decision": "blocked_independence_violation",
                "production_influence": False,
                "reason": (
                    "Historical replay was marked as part of the hypothesis "
                    "generation evidence and cannot also be used to challenge it."
                ),
            }
        else:
            result = challenge(challenge_rows)

        result["independence"] = independence
        result["hypothesis"] = hypothesis
        results.append(result)

    passed = [
        row
        for row in results
        if row.get("decision") == "historically_supported_shadow_only"
    ]
    blocked = [
        row
        for row in results
        if str(row.get("decision") or "").startswith("blocked")
    ]
    rejected = [
        row
        for row in results
        if str(row.get("decision") or "").startswith("rejected")
    ]

    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "challenge_type": "phase_6_automatic_historical_challenge",
        "production_influence": False,
        "replay": {
            "tracker_version": (replay_payload or {}).get("tracker_version"),
            "trading_days": replay_meta.get("trading_days"),
            "start_date": replay_meta.get("start_date"),
            "end_date": replay_meta.get("end_date"),
            "observations": len(rows),
            "bar_resolution": replay_meta.get("bar_resolution"),
            "known_limitations": replay_meta.get("known_limitations") or [],
        },
        "policy": {
            "whole_day_split": True,
            "same_symbol_embargo_seconds": SAME_SYMBOL_GAP_SECONDS,
            "frozen_confirmation_fraction": 0.40,
            "historical_support_never_promotes_production": True,
            "live_shadow_confirmation_required": True,
            "hypothesis_generation_excludes_historical_replay": True,
            "empirical_hypotheses_use_only_replay_days_before_evidence_start": True,
        },
        "summary": {
            "hypotheses_challenged": len(results),
            "historically_supported_shadow_only": len(passed),
            "rejected": len(rejected),
            "blocked": len(blocked),
        },
        "results": results,
    }


def render_markdown(payload):
    lines = [
        "# Phase 6 — Automatic Historical Hypothesis Challenge",
        "",
        f"Generated: **{payload.get('generated_at_utc')}**",
        "",
        "> Historical support is not production validation. Any surviving hypothesis remains shadow-only and must later pass strictly future live confirmation.",
        "",
        "## Challenge policy",
        "",
        "- Whole trading days are the split unit.",
        "- Same-symbol observations are de-correlated by the 60-minute target horizon.",
        "- Empirical hypotheses are challenged only on replay days that predate the evidence window that generated them.",
        "- The newest 40% of the remaining independent replay days are frozen confirmation data.",
        "- Candidate models are compared with the current hand score and a naive base-rate baseline.",
        "- Calibration, discrimination, top-decile lift, path-utility friction sensitivity, day stability, symbol concentration, and market-regime coverage are reported.",
        "",
        "## Summary",
        "",
    ]
    summary = payload.get("summary") or {}
    for key, value in summary.items():
        lines.append(
            f"- {key.replace('_', ' ').title()}: **{value}**"
        )

    for result in payload.get("results") or []:
        lines.extend(
            [
                "",
                f"## {result.get('challenge_id')}",
                "",
                f"**Decision:** {result.get('decision')}",
            ]
        )
        target = result.get("target_difference")
        if target:
            lines.append(
                "- Path-positive / endpoint-negative: "
                f"**{target.get('path_positive_endpoint_negative_n')} / "
                f"{target.get('comparable_n')}** "
                f"({target.get('rate_pct')}%), across "
                f"{target.get('distinct_symbols')} symbols and "
                f"{target.get('distinct_days')} days."
            )
        model = result.get("predictive_challenge") or {}
        if model.get("status") == "complete":
            lines.extend(
                [
                    f"- Model AUC: **{model.get('model_auc')}**",
                    f"- Hand-score AUC: **{model.get('hand_score_auc')}**",
                    f"- Model Brier / naive Brier: **{model.get('model_brier')} / {model.get('naive_brier')}**",
                    f"- Model top-decile lift: **{model.get('model_top_decile_lift_pp')} pp**",
                    f"- Hand-score top-decile lift: **{model.get('hand_top_decile_lift_pp')} pp**",
                    f"- Calibration ECE: **{model.get('calibration_ece')}**",
                ]
            )
        if result.get("reason"):
            lines.append(f"- Reason: {result.get('reason')}")

    lines.append("")
    return "\n".join(lines)


def write_outputs(payload, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "latest_historical_challenge.json"
    md_path = output_dir / "latest_historical_challenge.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    return json_path, md_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", default=str(DEFAULT_REPLAY))
    parser.add_argument("--audit", default=str(DEFAULT_AUDIT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument(
        "--no-standing",
        action="store_true",
        help="Challenge only hypotheses explicitly emitted by the learning audit.",
    )
    args = parser.parse_args()

    replay = _load_json(args.replay)
    audit = _load_json(args.audit) if Path(args.audit).exists() else {}
    payload = run_challenge(
        replay,
        audit,
        include_standing=not args.no_standing,
    )
    json_path, md_path = write_outputs(payload, args.output_dir)
    print(f"Historical challenge JSON: {json_path}")
    print(f"Historical challenge Markdown: {md_path}")
    summary = payload.get("summary") or {}
    print(
        "HYPOTHESES_CHALLENGED="
        + str(summary.get("hypotheses_challenged", 0))
    )
    print(
        "HISTORICALLY_SUPPORTED="
        + str(summary.get("historically_supported_shadow_only", 0))
    )
    print("REJECTED=" + str(summary.get("rejected", 0)))
    print("BLOCKED=" + str(summary.get("blocked", 0)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
