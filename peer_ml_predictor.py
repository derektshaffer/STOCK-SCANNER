from __future__ import annotations

import math
import time
from collections import Counter, defaultdict
from statistics import mean

from scanner_ml_ranker import (
    independent_confirmation_rows,
    load_training_observations,
)
from multi_bounce import bounce_feature_values
from scanner_behavior import BEHAVIOR_FEATURE_VERSION
from stair_step import stair_step_feature_values


PEER_MODEL_VERSION = "analyzer-peer-v6-balanced-swing-bounces"
PEER_TARGET = ">= +3% at 60 minutes"
PEER_FEATURES = [
    "day_pct",
    "score",
    "momentum_5m",
    "momentum_15m",
    "volume_pace",
    "intraday_range_pct",
    "distance_from_high_pct",
    "distance_from_vwap_pct",
    "log_liquidity",
    "time_fraction",
    "impulse_move_pct",
    "impulse_retracement_pct",
    "impulse_max_retracement_pct",
    "impulse_bounce_recovery_pct",
    "pullback_volume_ratio",
    "bounce_count",
    "last_bounce_pct",
    "bounce_decay_ratio",
    "bounce_volume_decay_ratio",
    "lower_high_streak",
    "higher_low_streak",
    "sequence_health_score",
    "current_pullback_pct",
    "ongoing_bounce_pct",
    "bounce_leg_code",
    "reference_peak_pct_above_dip",
    "stair_step_count",
    "stair_last_step_pct",
    "stair_step_acceleration_ratio",
    "stair_plateau_days",
    "stair_plateau_range_pct",
    "stair_plateau_retention_pct",
    "stair_plateau_volume_ratio",
    "stair_higher_plateau_count",
    "stair_structure_score",
    "stair_reaccelerating",
    "stair_breakdown",
]

MIN_PEER_SAMPLES = 500
MIN_PEER_SYMBOLS = 8
MIN_PEER_POSITIVES = 30
MIN_PEER_NEGATIVES = 150
MIN_VALIDATION_SAMPLES = 100
# Replay uses a current listed/liquid universe, so a replay-trained peer model
# is never allowed to become production-valid from replay evidence alone.
# Require a genuinely later live holdout before the peer layer can affect the
# Analyzer's headline ML edge.
MIN_LIVE_CONFIRMATION_SAMPLES = 100
MIN_LIVE_CONFIRMATION_DAYS = 5
MIN_LIVE_CONFIRMATION_CLASS_COUNT = 15
MIN_LIVE_CONFIRMATION_SYMBOLS = 15
MAX_PEER_SAMPLES = 2400
MAX_PER_SYMBOL = 180
MAX_PER_SYMBOL_DAY = 12

_CACHE = {}
_CACHE_TTL = 900
_TRAINING_CACHE = {"stamp": 0.0, "rows": None, "meta": None}
_TRAINING_CACHE_TTL = 1800


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _safe_log1p(value):
    value = _num(value)
    if value is None:
        return None
    return math.log1p(max(0.0, value))


def _current_features(metrics, now_et):
    price = _num(metrics.get("price"))
    high = _num(metrics.get("day_high"))
    low = _num(metrics.get("day_low"))
    intraday_range = (
        (high / low - 1.0) * 100.0
        if high and low and high >= low
        else None
    )
    from_high = _num(metrics.get("from_high_pct"))
    if from_high is None and high and price:
        from_high = (high - price) / high * 100.0

    liquidity = metrics.get("liquidity") or {}
    avg_dollar = _num(liquidity.get("avg_dollar_volume"))

    minute = now_et.hour * 60 + now_et.minute
    time_fraction = _clamp((minute - 570) / 390.0, 0.0, 1.0)

    impulse = metrics.get("impulse_pullback") or {}
    bounce_features = bounce_feature_values(metrics.get("bounce_sequence") or {})
    stair_features = stair_step_feature_values(metrics.get("stair_step") or {})

    return {
        "entry_price": price,
        "day_pct": _num(metrics.get("day_pct")),
        "score": _num(metrics.get("score")),
        "momentum_5m": _num(metrics.get("momentum_5m")),
        "momentum_15m": _num(metrics.get("momentum_15m")),
        "volume_pace": _num(metrics.get("volume_pace")),
        "intraday_range_pct": intraday_range,
        "distance_from_high_pct": from_high,
        "distance_from_vwap_pct": _num(metrics.get("vwap_extension_pct")),
        "log_liquidity": _safe_log1p(avg_dollar),
        "time_fraction": time_fraction,
        "impulse_move_pct": _num(impulse.get("impulse_move_pct")),
        "impulse_retracement_pct": _num(impulse.get("current_retracement_pct")),
        "impulse_max_retracement_pct": _num(impulse.get("max_retracement_pct")),
        "impulse_bounce_recovery_pct": _num(impulse.get("bounce_recovery_pct")),
        "pullback_volume_ratio": _num(impulse.get("pullback_volume_ratio")),
        **bounce_features,
        **stair_features,
    }


def _matching_behavior_rows(rows):
    return [
        row for row in (rows or [])
        if row.get("behavior_feature_version") == BEHAVIOR_FEATURE_VERSION
    ]


def _scaled_abs(a, b, scale):
    a = _num(a)
    b = _num(b)
    if a is None or b is None:
        return None
    return abs(a - b) / float(scale)


def _similarity_distance(row, current):
    features = row.get("features") or {}
    pieces = []

    specs = (
        ("day_pct", 10.0, 1.25),
        ("score", 16.0, 0.85),
        ("momentum_5m", 2.5, 1.15),
        ("momentum_15m", 4.5, 1.05),
        ("intraday_range_pct", 8.0, 0.85),
        ("distance_from_high_pct", 5.0, 1.05),
        ("distance_from_vwap_pct", 5.0, 1.00),
        ("log_liquidity", 1.4, 0.75),
        ("time_fraction", 0.20, 0.85),
        ("impulse_move_pct", 25.0, 1.10),
        ("impulse_retracement_pct", 18.0, 1.35),
        ("impulse_max_retracement_pct", 18.0, 1.00),
        ("impulse_bounce_recovery_pct", 12.0, 1.15),
        ("pullback_volume_ratio", 0.45, 0.85),
        ("bounce_count", 1.5, 1.25),
        ("last_bounce_pct", 8.0, 1.00),
        ("bounce_decay_ratio", 0.35, 1.25),
        ("bounce_volume_decay_ratio", 0.40, 0.90),
        ("lower_high_streak", 1.0, 1.25),
        ("higher_low_streak", 1.0, 0.85),
        ("sequence_health_score", 20.0, 1.15),
        ("current_pullback_pct", 6.0, 0.95),
        ("ongoing_bounce_pct", 6.0, 0.85),
        ("bounce_leg_code", 1.0, 0.90),
        ("reference_peak_pct_above_dip", 8.0, 0.90),
        ("stair_step_count", 1.5, 1.15),
        ("stair_last_step_pct", 8.0, 0.95),
        ("stair_step_acceleration_ratio", 0.45, 0.90),
        ("stair_plateau_days", 2.0, 0.85),
        ("stair_plateau_range_pct", 6.0, 0.95),
        ("stair_plateau_retention_pct", 25.0, 1.05),
        ("stair_plateau_volume_ratio", 0.45, 0.85),
        ("stair_higher_plateau_count", 1.0, 1.00),
        ("stair_structure_score", 20.0, 1.15),
        ("stair_reaccelerating", 1.0, 1.10),
        ("stair_breakdown", 1.0, 1.20),
    )
    for name, scale, weight in specs:
        d = _scaled_abs(features.get(name), current.get(name), scale)
        if d is not None:
            pieces.append((min(d, 4.0), weight))

    peer_pace = _num(features.get("volume_pace"))
    current_pace = _num(current.get("volume_pace"))
    if peer_pace is not None and current_pace is not None:
        d = abs(math.log1p(max(0.0, peer_pace)) - math.log1p(max(0.0, current_pace))) / 0.65
        pieces.append((min(d, 4.0), 1.15))

    peer_price = _num(row.get("entry_price"))
    current_price = _num(current.get("entry_price"))
    if peer_price and current_price:
        d = abs(math.log(peer_price / current_price)) / 0.75
        pieces.append((min(d, 4.0), 1.00))

    if len(pieces) < 6:
        return None

    total_weight = sum(weight for _, weight in pieces)
    return sum(value * weight for value, weight in pieces) / total_weight


def _select_peer_rows(rows, symbol, current):
    symbol = str(symbol or "").upper().strip()
    ranked = []
    for row in rows:
        peer_symbol = str(row.get("symbol") or "").upper().strip()
        if not peer_symbol or peer_symbol == symbol:
            continue
        distance = _similarity_distance(row, current)
        if distance is None:
            continue
        ranked.append((distance, row))

    ranked.sort(key=lambda item: (item[0], item[1].get("timestamp") or 0.0))

    selected = []
    per_symbol = Counter()
    per_symbol_day = Counter()
    for distance, row in ranked:
        peer_symbol = str(row.get("symbol") or "").upper().strip()
        day = str(row.get("trading_date") or "")
        if per_symbol[peer_symbol] >= MAX_PER_SYMBOL:
            continue
        if day and per_symbol_day[(peer_symbol, day)] >= MAX_PER_SYMBOL_DAY:
            continue

        copy = dict(row)
        copy["_peer_distance"] = float(distance)
        selected.append(copy)
        per_symbol[peer_symbol] += 1
        if day:
            per_symbol_day[(peer_symbol, day)] += 1

        if len(selected) >= MAX_PEER_SAMPLES:
            break

    selected.sort(key=lambda row: float(row.get("timestamp") or 0.0))
    return selected


def _auc(y, probabilities):
    positives = sum(int(v == 1) for v in y)
    negatives = len(y) - positives
    if positives == 0 or negatives == 0:
        return None

    pairs = sorted(zip(probabilities, y), key=lambda item: item[0])
    rank_sum_pos = 0.0
    rank = 1
    i = 0
    while i < len(pairs):
        j = i + 1
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        avg_rank = (rank + (rank + (j - i) - 1)) / 2.0
        rank_sum_pos += avg_rank * sum(
            int(pairs[k][1] == 1)
            for k in range(i, j)
        )
        rank += j - i
        i = j

    return (
        rank_sum_pos - positives * (positives + 1) / 2.0
    ) / (positives * negatives)


def _matrix(rows, np):
    X = np.array(
        [
            [
                np.nan
                if (row.get("features") or {}).get(name) is None
                else float((row.get("features") or {}).get(name))
                for name in PEER_FEATURES
            ]
            for row in rows
        ],
        dtype=float,
    )
    y = np.array([int(row.get("label") or 0) for row in rows], dtype=float)
    return X, y


def _params():
    return {
        "objective": "binary:logistic",
        "eval_metric": "logloss",
        "max_depth": 3,
        "eta": 0.045,
        "subsample": 0.82,
        "colsample_bytree": 0.82,
        "min_child_weight": 6,
        "lambda": 2.5,
        "alpha": 0.2,
        "seed": 42,
        "nthread": 2,
    }


def _source_integrity_context(rows):
    replay_rows = [
        row for row in rows
        if row.get("observation_source") == "historical_replay"
    ]
    live_rows = [
        row for row in rows
        if row.get("observation_source") != "historical_replay"
    ]
    replay_days = sorted({
        str(row.get("trading_date") or "")
        for row in replay_rows
        if row.get("trading_date")
    })
    replay_end_day = replay_days[-1] if replay_days else None

    # Only live observations strictly AFTER the replay period qualify as the
    # independent confirmation set. Same-day live copies cannot confirm a
    # replay model because that symbol/day may already exist in replay.
    live_confirmation_rows_raw = [
        row for row in live_rows
        if (
            not replay_end_day
            or (
                row.get("trading_date")
                and str(row["trading_date"]) > replay_end_day
            )
        )
    ]
    live_confirmation_rows = independent_confirmation_rows(
        live_confirmation_rows_raw
    )
    live_confirmation_days = sorted({
        str(row.get("trading_date") or "")
        for row in live_confirmation_rows
        if row.get("trading_date")
    })
    live_confirmation_symbols = sorted({
        str(row.get("symbol") or "")
        for row in live_confirmation_rows
        if row.get("symbol")
    })
    return {
        "replay_rows": replay_rows,
        "live_rows": live_rows,
        "replay_end_day": replay_end_day,
        "live_confirmation_rows_raw": live_confirmation_rows_raw,
        "live_confirmation_rows": live_confirmation_rows,
        "live_confirmation_days": live_confirmation_days,
        "live_confirmation_symbols": live_confirmation_symbols,
    }


def _validate_and_predict(rows, current):
    try:
        import numpy as np
        import xgboost as xgb
    except Exception as exc:
        return {
            "status": "dependency_missing",
            "validated": False,
            "error": str(exc)[:180],
        }

    samples = len(rows)
    positives = sum(int(row.get("label") == 1) for row in rows)
    negatives = samples - positives
    symbols = sorted({
        str(row.get("symbol") or "").upper().strip()
        for row in rows
        if row.get("symbol")
    })
    days = sorted({
        str(row.get("trading_date") or "")
        for row in rows
        if row.get("trading_date")
    })

    distances = [
        float(row.get("_peer_distance"))
        for row in rows
        if row.get("_peer_distance") is not None
    ]
    base = {
        "status": "collecting",
        "validated": False,
        "model_type": "XGBoost",
        "version": PEER_MODEL_VERSION,
        "target": PEER_TARGET,
        "samples": samples,
        "positives": positives,
        "negatives": negatives,
        "peer_symbols": len(symbols),
        "trading_days": len(days),
        "median_similarity_distance": (
            round(sorted(distances)[len(distances) // 2], 3)
            if distances
            else None
        ),
        "cohort_positive_rate_pct": (
            round(positives / samples * 100.0, 2)
            if samples
            else None
        ),
    }

    if (
        samples < MIN_PEER_SAMPLES
        or len(symbols) < MIN_PEER_SYMBOLS
        or positives < MIN_PEER_POSITIVES
        or negatives < MIN_PEER_NEGATIVES
        or len(days) < 6
    ):
        return base

    source_context = _source_integrity_context(rows)
    replay_rows = source_context["replay_rows"]
    live_confirmation_rows_raw = source_context["live_confirmation_rows_raw"]
    live_confirmation_rows = source_context["live_confirmation_rows"]
    live_confirmation_days = source_context["live_confirmation_days"]
    live_confirmation_symbols = source_context["live_confirmation_symbols"]
    replay_end_day = source_context["replay_end_day"]

    # Keep the post-replay live holdout completely outside the historical
    # walk-forward gate. Otherwise the same future evidence could influence the
    # historical validation decision and then be reused as "live confirmation."
    validation_rows = replay_rows if replay_rows else rows
    X_validation, y_validation = _matrix(validation_rows, np)
    validation_days = sorted({
        str(row.get("trading_date") or "")
        for row in validation_rows
        if row.get("trading_date")
    })
    fold_bounds = (
        (0.55, 0.70),
        (0.70, 0.85),
        (0.85, 1.00),
    )
    val_probs = []
    val_y = []
    baseline_probs = []

    for train_frac, val_frac in fold_bounds:
        train_pos = min(
            len(validation_days) - 1,
            max(0, int(len(validation_days) * train_frac) - 1),
        )
        val_pos = min(
            len(validation_days) - 1,
            max(0, int(len(validation_days) * val_frac) - 1),
        )
        train_cut = validation_days[train_pos]
        val_cut = validation_days[val_pos]
        if val_cut <= train_cut:
            continue

        train_idx = [
            i for i, row in enumerate(validation_rows)
            if row.get("trading_date") and row["trading_date"] <= train_cut
        ]
        val_idx = [
            i for i, row in enumerate(validation_rows)
            if row.get("trading_date")
            and train_cut < row["trading_date"] <= val_cut
        ]
        if len(train_idx) < 250 or len(val_idx) < 50:
            continue

        ytr = y_validation[train_idx]
        yv = y_validation[val_idx]
        if len(set(ytr.tolist())) < 2 or len(set(yv.tolist())) < 2:
            continue

        model = xgb.train(
            _params(),
            xgb.DMatrix(X_validation[train_idx], label=ytr, feature_names=PEER_FEATURES),
            num_boost_round=120,
            verbose_eval=False,
        )
        probs = model.predict(
            xgb.DMatrix(X_validation[val_idx], feature_names=PEER_FEATURES)
        )
        train_rate = float(ytr.mean())
        val_probs.extend(float(p) for p in probs)
        val_y.extend(int(v) for v in yv)
        baseline_probs.extend([train_rate] * len(yv))

    if len(val_y) < MIN_VALIDATION_SAMPLES:
        base["status"] = "insufficient_validation"
        base["validation_samples"] = len(val_y)
        return base

    auc = _auc(val_y, val_probs)
    brier = mean(
        (probability - actual) ** 2
        for probability, actual in zip(val_probs, val_y)
    )
    baseline_brier = mean(
        (probability - actual) ** 2
        for probability, actual in zip(baseline_probs, val_y)
    )

    historical_validated = bool(
        auc is not None
        and auc >= 0.56
        and brier < baseline_brier
        and len(val_y) >= MIN_VALIDATION_SAMPLES
    )

    fully_validated = historical_validated
    live_confirmation_ready = False
    live_confirmation_passed = None
    live_auc = None
    live_brier = None
    live_baseline_brier = None

    if replay_rows:
        live_positives = sum(
            int(row.get("label") == 1)
            for row in live_confirmation_rows
        )
        live_negatives = len(live_confirmation_rows) - live_positives
        live_confirmation_ready = bool(
            len(live_confirmation_rows) >= MIN_LIVE_CONFIRMATION_SAMPLES
            and len(live_confirmation_days) >= MIN_LIVE_CONFIRMATION_DAYS
            and len(live_confirmation_symbols) >= MIN_LIVE_CONFIRMATION_SYMBOLS
            and live_positives >= MIN_LIVE_CONFIRMATION_CLASS_COUNT
            and live_negatives >= MIN_LIVE_CONFIRMATION_CLASS_COUNT
        )
        fully_validated = False

        if historical_validated and live_confirmation_ready:
            # Train only on replay-era observations, then score the strictly
            # later live holdout exactly once as the production-integrity gate.
            X_replay, y_replay = _matrix(replay_rows, np)
            X_live, y_live = _matrix(live_confirmation_rows, np)
            if (
                len(set(y_replay.tolist())) >= 2
                and len(set(y_live.tolist())) >= 2
            ):
                replay_model = xgb.train(
                    _params(),
                    xgb.DMatrix(
                        X_replay,
                        label=y_replay,
                        feature_names=PEER_FEATURES,
                    ),
                    num_boost_round=145,
                    verbose_eval=False,
                )
                live_probs = replay_model.predict(
                    xgb.DMatrix(
                        X_live,
                        feature_names=PEER_FEATURES,
                    )
                )
                live_y_list = [int(value) for value in y_live.tolist()]
                live_prob_list = [float(value) for value in live_probs]
                live_auc = _auc(live_y_list, live_prob_list)
                live_brier = mean(
                    (probability - actual) ** 2
                    for probability, actual
                    in zip(live_prob_list, live_y_list)
                )
                replay_rate = float(y_replay.mean())
                live_baseline_brier = mean(
                    (replay_rate - actual) ** 2
                    for actual in live_y_list
                )
                live_confirmation_passed = bool(
                    live_auc is not None
                    and live_auc >= 0.53
                    and live_brier < live_baseline_brier
                )
                fully_validated = live_confirmation_passed

    # Advisory predictions may use all information available up to now after
    # validation is measured. They still cannot influence the trade plan unless
    # the independent source-integrity gate above passes.
    X, y = _matrix(rows, np)
    final_model = xgb.train(
        _params(),
        xgb.DMatrix(X, label=y, feature_names=PEER_FEATURES),
        num_boost_round=145,
        verbose_eval=False,
    )
    current_x = np.array(
        [[
            np.nan
            if current.get(name) is None
            else float(current.get(name))
            for name in PEER_FEATURES
        ]],
        dtype=float,
    )
    probability = float(
        final_model.predict(
            xgb.DMatrix(current_x, feature_names=PEER_FEATURES)
        )[0]
    )

    importance_raw = final_model.get_score(importance_type="gain")
    top = sorted(
        ((name, float(value)) for name, value in importance_raw.items()),
        key=lambda item: item[1],
        reverse=True,
    )[:5]
    total_gain = sum(value for _, value in top) or 1.0

    if not historical_validated:
        validation_status = "advisory"
    elif replay_rows and not live_confirmation_ready:
        validation_status = "replay_validated_waiting_live"
    elif replay_rows and not live_confirmation_passed:
        validation_status = "failed_live_confirmation"
    else:
        validation_status = "validated"

    base.update(
        {
            "status": validation_status,
            "validated": bool(fully_validated),
            "historical_validated": historical_validated,
            "probability_pct": round(probability * 100.0, 1),
            "validation_samples": len(val_y),
            "walk_forward_auc": round(auc, 3) if auc is not None else None,
            "walk_forward_brier": round(brier, 4),
            "baseline_brier": round(baseline_brier, 4),
            "replay_samples": len(replay_rows),
            "replay_end_day": replay_end_day,
            "replay_survivorship_limit": bool(replay_rows),
            "live_confirmation_raw_samples": len(live_confirmation_rows_raw),
            "live_confirmation_samples": len(live_confirmation_rows),
            "live_confirmation_days": len(live_confirmation_days),
            "live_confirmation_symbols": len(live_confirmation_symbols),
            "live_confirmation_min_symbols": MIN_LIVE_CONFIRMATION_SYMBOLS,
            "live_confirmation_ready": live_confirmation_ready,
            "live_confirmation_passed": live_confirmation_passed,
            "live_confirmation_auc": (
                round(live_auc, 3) if live_auc is not None else None
            ),
            "live_confirmation_brier": (
                round(live_brier, 4) if live_brier is not None else None
            ),
            "live_confirmation_baseline_brier": (
                round(live_baseline_brier, 4)
                if live_baseline_brier is not None
                else None
            ),
            "top_features": [
                {
                    "feature": name,
                    "share_pct": round(value / total_gain * 100.0, 1),
                }
                for name, value in top
            ],
        }
    )
    return base


def predict_peer_ml(symbol, now, metrics, et):
    """Return a separate similar-setup continuation model.

    The peer layer never mixes other tickers into the same-ticker models.
    It selects behaviorally similar historical momentum observations from
    *other* symbols, validates them on whole trading days, and predicts the
    probability of at least +3% over the next 60 minutes.
    """
    current = _current_features(metrics, now.astimezone(et))
    key = (
        str(symbol or "").upper().strip(),
        round(_num(current.get("day_pct")) or 0.0, 0),
        round(_num(current.get("volume_pace")) or 0.0, 1),
        round(_num(current.get("distance_from_vwap_pct")) or 0.0, 1),
        round(_num(current.get("entry_price")) or 0.0, 1),
        round(_num(current.get("impulse_retracement_pct")) or -1.0, 0),
        round(_num(current.get("impulse_bounce_recovery_pct")) or 0.0, 0),
        int(_num(current.get("bounce_count")) or 0),
        round(_num(current.get("bounce_decay_ratio")) or -1.0, 1),
        int(_num(current.get("lower_high_streak")) or 0),
        int(_num(current.get("bounce_leg_code")) or 0),
        int(_num(current.get("stair_step_count")) or 0),
        round(_num(current.get("stair_structure_score")) or 0.0, 0),
        int(_num(current.get("stair_reaccelerating")) or 0),
        int(_num(current.get("stair_breakdown")) or 0),
    )
    stamp = time.time()
    cached = _CACHE.get(key)
    if cached and stamp - cached["stamp"] < _CACHE_TTL:
        result = dict(cached["value"])
        result["cached"] = True
        return result

    try:
        if (
            _TRAINING_CACHE["rows"] is not None
            and stamp - float(_TRAINING_CACHE["stamp"]) < _TRAINING_CACHE_TTL
        ):
            rows = _TRAINING_CACHE["rows"]
            source_meta = _TRAINING_CACHE["meta"] or {}
        else:
            rows, source_meta = load_training_observations()
            source_meta = dict(source_meta or {})
            source_meta["pre_behavior_filter_samples"] = len(rows)
            rows = _matching_behavior_rows(rows)
            source_meta["behavior_feature_version"] = BEHAVIOR_FEATURE_VERSION
            source_meta["behavior_version_filtered_samples"] = len(rows)
            _TRAINING_CACHE.update(
                {
                    "stamp": stamp,
                    "rows": rows,
                    "meta": source_meta,
                }
            )
    except Exception as exc:
        return {
            "status": "unavailable",
            "validated": False,
            "version": PEER_MODEL_VERSION,
            "target": PEER_TARGET,
            "error": str(exc)[:180],
        }

    selected = _select_peer_rows(rows, symbol, current)
    result = _validate_and_predict(selected, current)
    result["source"] = "scanner historical replay + resolved live scanner outcomes"
    result["source_observations"] = len(rows)
    result["source_observations_before_behavior_filter"] = int(
        (source_meta or {}).get("pre_behavior_filter_samples") or len(rows)
    )
    result["behavior_feature_version"] = BEHAVIOR_FEATURE_VERSION

    probability = _num(result.get("probability_pct"))
    base_rate = _num(result.get("cohort_positive_rate_pct"))
    peer_edge = None
    if probability is not None and base_rate is not None and base_rate > 0:
        ratio = max(0.10, min(10.0, probability / base_rate))
        peer_edge = _clamp(50.0 + 25.0 * math.log(ratio), 15.0, 85.0)
    result["peer_edge_score"] = (
        round(peer_edge, 1)
        if peer_edge is not None
        else None
    )
    result["current_features"] = current
    result["cached"] = False

    symbol_counts = Counter(
        str(row.get("symbol") or "").upper().strip()
        for row in selected
        if row.get("symbol")
    )
    result["top_peer_symbols"] = [
        {"symbol": symbol_name, "samples": count}
        for symbol_name, count in symbol_counts.most_common(8)
    ]
    result["source_meta"] = {
        "historical_replay_samples": source_meta.get("historical_replay_samples"),
        "live_samples": source_meta.get("live_samples"),
    }
    result["note"] = (
        "Separate peer layer using similar historical momentum setups from other tickers. "
        "Similarity uses price band, liquidity, day move, momentum, volume pace, VWAP extension, "
        "distance from the high, intraday range, time of day, impulse size, retracement depth, bounce recovery, "
        "multi-bounce count/decay, lower-high structure, pullback-volume behavior, and multi-session stair-step / plateau context. It is validated on whole trading "
        "days and never replaces the stock's own same-ticker model. "
        "Historical replay uses a current listed/liquid universe, so replay-only "
        "peer validation remains advisory until a strictly later live holdout "
        "also beats its probability baseline."
    )

    _CACHE[key] = {"stamp": stamp, "value": result}
    return result
