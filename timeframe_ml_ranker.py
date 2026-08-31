"""Experimental Swing timeframe ML validation.

This module does NOT alter live Analyzer scores. It trains and evaluates a
shadow-only model against the leakage-aware historical timeframe replay.

Target: probability the stock reaches +5% before -4% during the next five
trading sessions. If both levels are touched on the same daily bar, that row is
excluded because daily OHLC cannot establish intraday ordering. Validation is
chronological and grouped by replay date so rows from the same market day never
appear in both train and test.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from timeframe_targets import (
    SWING_HORIZON_SESSIONS,
    SWING_STOP_PCT,
    SWING_TARGET_PCT,
)

DATA_PATH = Path("timeframe_replay/timeframe_historical_replay.json")
DEFAULT_REPORT_PATH = Path("timeframe_replay/timeframe_ml_validation.json")
MODEL_VERSION = "swing-timeframe-ml-v4-confirmed-multisession-shadow"
TARGET_FIELD = "swing_target_before_stop_5d"

BASE_FEATURES = [
    "day_pct",
    "gap_pct",
    "relative_volume",
    "log10_dollar_volume",
    "trend_score",
    "stair_score",
    "history_score",
    "market_score",
    "trend_return_5d_pct",
    "trend_return_20d_pct",
    "trend_return_60d_pct",
    "trend_return_120d_pct",
    "trend_return_250d_pct",
    "from_52w_high_pct",
    "above_52w_low_pct",
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
    "stair_reacceleration_developing",
    "stair_breakdown",
    "stair_breakdown_confirmed",
    "stair_breakdown_developing",
    "historical_bias_score",
    "historical_next_day_up_pct",
    "historical_sample_count",
    "broad_market_avg_pct",
]

REGIME_FEATURES = [
    "market_regime_score",
    "spy_return_5d_pct",
    "spy_return_20d_pct",
    "spy_return_60d_pct",
    "qqq_return_20d_pct",
    "iwm_return_20d_pct",
    "qqq_minus_spy_20d_pct",
    "iwm_minus_spy_20d_pct",
    "spy_above_ma20",
    "spy_above_ma50",
    "spy_above_ma200",
    "benchmark_above_ma20_frac",
    "benchmark_positive_20d_frac",
    "spy_realized_vol_20d_pct",
    "spy_drawdown_20d_pct",
    "sector_move_pct",
]

FEATURES = BASE_FEATURES + REGIME_FEATURES


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _feature_dict(row):
    trend = row.get("trend_context") or {}
    stair = row.get("stair_context") or {}
    historical = row.get("historical_context") or {}
    market = row.get("market_context") or {}
    dollar = _num(row.get("current_dollar_volume"))
    return {
        "day_pct": _num(row.get("day_pct")),
        "gap_pct": _num(row.get("gap_pct")),
        "relative_volume": _num(row.get("relative_volume")),
        "log10_dollar_volume": (
            math.log10(max(1.0, dollar)) if dollar is not None else None
        ),
        "trend_score": _num(row.get("trend_score")),
        "stair_score": _num(row.get("stair_score")),
        "history_score": _num(row.get("history_score")),
        "market_score": _num(row.get("market_score")),
        "trend_return_5d_pct": _num(trend.get("return_5d_pct")),
        "trend_return_20d_pct": _num(trend.get("return_20d_pct")),
        "trend_return_60d_pct": _num(trend.get("return_60d_pct")),
        "trend_return_120d_pct": _num(trend.get("return_120d_pct")),
        "trend_return_250d_pct": _num(trend.get("return_250d_pct")),
        "from_52w_high_pct": _num(trend.get("from_52w_high_pct")),
        "above_52w_low_pct": _num(trend.get("above_52w_low_pct")),
        "stair_step_count": _num(stair.get("stair_step_count")),
        "stair_last_step_pct": _num(stair.get("stair_last_step_pct")),
        "stair_step_acceleration_ratio": _num(
            stair.get("stair_step_acceleration_ratio")
        ),
        "stair_plateau_days": _num(stair.get("stair_plateau_days")),
        "stair_plateau_range_pct": _num(stair.get("stair_plateau_range_pct")),
        "stair_plateau_retention_pct": _num(
            stair.get("stair_plateau_retention_pct")
        ),
        "stair_plateau_volume_ratio": _num(
            stair.get("stair_plateau_volume_ratio")
        ),
        "stair_higher_plateau_count": _num(
            stair.get("stair_higher_plateau_count")
        ),
        "stair_structure_score": _num(stair.get("stair_structure_score")),
        "stair_reaccelerating": _num(stair.get("stair_reaccelerating")),
        "stair_reacceleration_developing": _num(stair.get("stair_reacceleration_developing")),
        "stair_breakdown": _num(stair.get("stair_breakdown")),
        "stair_breakdown_confirmed": _num(stair.get("stair_breakdown_confirmed")),
        "stair_breakdown_developing": _num(stair.get("stair_breakdown_developing")),
        "historical_bias_score": _num(historical.get("bias_score")),
        "historical_next_day_up_pct": _num(historical.get("next_day_up_pct")),
        "historical_sample_count": _num(historical.get("sample_count")),
        "broad_market_avg_pct": _num(market.get("broad_market_avg_pct")),
        "market_regime_score": _num(market.get("regime_score")),
        "spy_return_5d_pct": _num(market.get("spy_return_5d_pct")),
        "spy_return_20d_pct": _num(market.get("spy_return_20d_pct")),
        "spy_return_60d_pct": _num(market.get("spy_return_60d_pct")),
        "qqq_return_20d_pct": _num(market.get("qqq_return_20d_pct")),
        "iwm_return_20d_pct": _num(market.get("iwm_return_20d_pct")),
        "qqq_minus_spy_20d_pct": _num(
            market.get("qqq_minus_spy_20d_pct")
        ),
        "iwm_minus_spy_20d_pct": _num(
            market.get("iwm_minus_spy_20d_pct")
        ),
        "spy_above_ma20": _num(market.get("spy_above_ma20")),
        "spy_above_ma50": _num(market.get("spy_above_ma50")),
        "spy_above_ma200": _num(market.get("spy_above_ma200")),
        "benchmark_above_ma20_frac": _num(
            market.get("benchmark_above_ma20_frac")
        ),
        "benchmark_positive_20d_frac": _num(
            market.get("benchmark_positive_20d_frac")
        ),
        "spy_realized_vol_20d_pct": _num(
            market.get("spy_realized_vol_20d_pct")
        ),
        "spy_drawdown_20d_pct": _num(
            market.get("spy_drawdown_20d_pct")
        ),
        "sector_move_pct": _num(market.get("sector_move_pct")),
    }


def load_rows(path=DATA_PATH):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = []
    skipped_ambiguous = 0
    for row in payload.get("observations") or []:
        if row.get("timeframe_score_version") != "timeframe-fit-v1":
            continue
        outcomes = row.get("outcomes") or {}
        if outcomes.get("swing_ambiguous_same_day_5d"):
            skipped_ambiguous += 1
            continue
        label = outcomes.get(TARGET_FIELD)
        if label is None:
            continue
        label_num = _num(label)
        if label_num not in (0.0, 1.0):
            continue
        date_text = str(row.get("as_of") or "")[:10]
        if not date_text:
            continue
        rows.append(
            {
                "date": date_text,
                "symbol": str(row.get("symbol") or "").upper().strip(),
                "return_5d_pct": _num(outcomes.get("return_5d_pct")),
                "mfe_5d_pct": _num(outcomes.get("swing_mfe_5d_pct")),
                "mae_5d_pct": _num(outcomes.get("swing_mae_5d_pct")),
                "excess_return_vs_spy_5d_pct": _num(
                    outcomes.get("excess_return_vs_spy_5d_pct")
                ),
                "first_event": outcomes.get("swing_first_event_5d"),
                "label": int(label_num),
                "swing_score": _num(row.get("swing_score")),
                "market_regime_label": str(
                    (row.get("market_context") or {}).get("regime_label")
                    or "UNKNOWN"
                ),
                "features": _feature_dict(row),
            }
        )
    payload["_ml_skipped_ambiguous_same_day"] = skipped_ambiguous
    return rows, payload


def _auc(y, probabilities):
    positives = sum(int(value == 1) for value in y)
    negatives = len(y) - positives
    if positives == 0 or negatives == 0:
        return None
    pairs = sorted(zip(probabilities, y), key=lambda item: item[0])
    rank_sum_pos = 0.0
    i = 0
    rank = 1
    while i < len(pairs):
        j = i + 1
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        avg_rank = (rank + (rank + (j - i) - 1)) / 2.0
        rank_sum_pos += avg_rank * sum(
            int(pairs[k][1] == 1) for k in range(i, j)
        )
        rank += j - i
        i = j
    return (
        rank_sum_pos - positives * (positives + 1) / 2.0
    ) / (positives * negatives)


def _brier(y, probabilities):
    if not y:
        return None
    return sum((float(p) - float(t)) ** 2 for p, t in zip(probabilities, y)) / len(y)


def _matrix(rows, np, feature_names=None):
    feature_names = feature_names or FEATURES
    X = np.array(
        [
            [
                np.nan
                if row["features"].get(name) is None
                else float(row["features"].get(name))
                for name in feature_names
            ]
            for row in rows
        ],
        dtype=float,
    )
    y = np.array([int(row["label"]) for row in rows], dtype=int)
    return X, y


def _params():
    return {
        "objective": "binary:logistic",
        "eval_metric": "logloss",
        "max_depth": 3,
        "eta": 0.04,
        "min_child_weight": 6,
        "subsample": 0.82,
        "colsample_bytree": 0.82,
        "alpha": 0.15,
        "lambda": 2.5,
        "tree_method": "hist",
        "seed": 17,
        "nthread": 2,
    }


def _fit_booster(train, np, xgb, feature_names=None):
    feature_names = feature_names or FEATURES
    X_train, y_train = _matrix(train, np, feature_names)
    dtrain = xgb.DMatrix(
        X_train,
        label=y_train,
        feature_names=feature_names,
        missing=np.nan,
    )
    booster = xgb.train(
        _params(),
        dtrain,
        num_boost_round=260,
    )
    return booster


def _predict_booster(booster, rows, np, xgb, feature_names=None):
    feature_names = feature_names or FEATURES
    X, y = _matrix(rows, np, feature_names)
    dmatrix = xgb.DMatrix(
        X,
        label=y,
        feature_names=feature_names,
        missing=np.nan,
    )
    probabilities = booster.predict(dmatrix).tolist()
    return y, probabilities


def _chronological_folds(rows):
    dates = sorted({row["date"] for row in rows})
    if len(dates) < 24:
        return []

    # Use more walk-forward eras once the replay spans multiple years. This
    # keeps validation sensitive to regime changes rather than letting one
    # recent block dominate the result.
    if len(dates) >= 120:
        fold_count = 6
        test_block = max(10, len(dates) // 12)
        min_train_samples = 500
        min_test_samples = 150
    elif len(dates) >= 60:
        fold_count = 5
        test_block = max(6, len(dates) // 10)
        min_train_samples = 350
        min_test_samples = 100
    else:
        fold_count = 4
        test_block = max(4, len(dates) // 8)
        min_train_samples = 250
        min_test_samples = 75

    initial_train = len(dates) - fold_count * test_block
    while fold_count > 3 and initial_train < max(16, test_block * 2):
        fold_count -= 1
        initial_train = len(dates) - fold_count * test_block

    folds = []
    for fold_index in range(fold_count):
        test_start = initial_train + fold_index * test_block
        test_end = min(len(dates), test_start + test_block)
        train_dates = set(dates[:test_start])
        test_dates = set(dates[test_start:test_end])
        train = [row for row in rows if row["date"] in train_dates]
        test = [row for row in rows if row["date"] in test_dates]
        if (
            len(train) >= min_train_samples
            and len(test) >= min_test_samples
        ):
            folds.append((train, test, sorted(train_dates), sorted(test_dates)))
    return folds


def _probability_band_stats(rows, probabilities):
    pairs = sorted(zip(probabilities, rows), key=lambda item: item[0], reverse=True)
    if not pairs:
        return {}
    top_n = max(1, len(pairs) // 10)
    top = pairs[:top_n]
    bottom = pairs[-top_n:]

    def _avg(group, field):
        values = [
            _num(row.get(field))
            for _p, row in group
        ]
        values = [value for value in values if value is not None]
        return round(sum(values) / len(values), 3) if values else None

    def summarize(group):
        labels = [row["label"] for _p, row in group]
        probs = [float(p) for p, _row in group]
        return {
            "n": len(group),
            "avg_probability_pct": round(sum(probs) / len(probs) * 100.0, 2),
            "target_before_stop_rate_pct": round(
                sum(labels) / len(labels) * 100.0,
                1,
            ),
            "avg_return_5d_pct": _avg(group, "return_5d_pct"),
            "avg_mfe_5d_pct": _avg(group, "mfe_5d_pct"),
            "avg_mae_5d_pct": _avg(group, "mae_5d_pct"),
            "avg_excess_vs_spy_5d_pct": _avg(
                group,
                "excess_return_vs_spy_5d_pct",
            ),
        }

    return {
        "top_decile": summarize(top),
        "bottom_decile": summarize(bottom),
    }


def validate(rows):
    import numpy as np
    import xgboost as xgb

    folds = _chronological_folds(rows)
    if not folds:
        return {
            "status": "insufficient_history",
            "samples": len(rows),
            "unique_dates": len({row["date"] for row in rows}),
        }

    fold_reports = []
    all_y = []
    all_prob = []
    all_baseline_prob = []
    all_score = []
    all_test_rows = []

    for index, (train, test, train_dates, test_dates) in enumerate(folds, start=1):
        booster = _fit_booster(train, np, xgb, FEATURES)
        baseline_booster = _fit_booster(
            train,
            np,
            xgb,
            BASE_FEATURES,
        )
        y_test, probabilities = _predict_booster(
            booster,
            test,
            np,
            xgb,
            FEATURES,
        )
        _baseline_y, baseline_probabilities = _predict_booster(
            baseline_booster,
            test,
            np,
            xgb,
            BASE_FEATURES,
        )
        labels = y_test.tolist()
        swing_scores = [
            50.0 if row.get("swing_score") is None else row["swing_score"]
            for row in test
        ]
        model_auc = _auc(labels, probabilities)
        baseline_model_auc = _auc(labels, baseline_probabilities)
        score_auc = _auc(labels, swing_scores)
        base_rate = sum(labels) / len(labels) if labels else None
        bands = _probability_band_stats(test, probabilities)

        fold_reports.append(
            {
                "fold": index,
                "train_samples": len(train),
                "test_samples": len(test),
                "train_start": train_dates[0],
                "train_end": train_dates[-1],
                "test_start": test_dates[0],
                "test_end": test_dates[-1],
                "base_target_before_stop_rate_pct": (
                    round(base_rate * 100.0, 1) if base_rate is not None else None
                ),
                "model_auc": round(model_auc, 4) if model_auc is not None else None,
                "baseline_model_auc": (
                    round(baseline_model_auc, 4)
                    if baseline_model_auc is not None
                    else None
                ),
                "regime_minus_baseline_auc": (
                    round(model_auc - baseline_model_auc, 4)
                    if model_auc is not None and baseline_model_auc is not None
                    else None
                ),
                "hand_score_auc": (
                    round(score_auc, 4) if score_auc is not None else None
                ),
                "brier": round(_brier(labels, probabilities), 4),
                "probability_bands": bands,
            }
        )
        all_y.extend(labels)
        all_prob.extend(probabilities)
        all_baseline_prob.extend(baseline_probabilities)
        all_score.extend(swing_scores)
        all_test_rows.extend(test)

    overall_model_auc = _auc(all_y, all_prob)
    overall_baseline_model_auc = _auc(all_y, all_baseline_prob)
    overall_score_auc = _auc(all_y, all_score)
    overall_brier = _brier(all_y, all_prob)
    bands = _probability_band_stats(all_test_rows, all_prob)
    base_rate = sum(all_y) / len(all_y) if all_y else None

    valid_fold_aucs = [
        row["model_auc"] for row in fold_reports if row.get("model_auc") is not None
    ]
    mean_fold_auc = (
        sum(valid_fold_aucs) / len(valid_fold_aucs)
        if valid_fold_aucs
        else None
    )
    better_folds = sum(
        1
        for row in fold_reports
        if row.get("model_auc") is not None
        and row.get("hand_score_auc") is not None
        and row["model_auc"] > row["hand_score_auc"]
    )
    regime_improved_folds = sum(
        1
        for row in fold_reports
        if row.get("model_auc") is not None
        and row.get("baseline_model_auc") is not None
        and row["model_auc"] > row["baseline_model_auc"]
    )
    top = bands.get("top_decile") or {}
    top_lift = None
    if (
        base_rate is not None
        and top.get("target_before_stop_rate_pct") is not None
    ):
        top_lift = round(
            top["target_before_stop_rate_pct"] - base_rate * 100.0,
            1,
        )

    unique_dates = {row["date"] for row in rows}
    unique_years = {date_text[:4] for date_text in unique_dates if date_text}

    historical_validated = bool(
        len(rows) >= 2500
        and len(unique_dates) >= 120
        and len(unique_years) >= 4
        and mean_fold_auc is not None
        and mean_fold_auc >= 0.55
        and overall_model_auc is not None
        and overall_model_auc >= 0.55
        and overall_score_auc is not None
        and overall_model_auc >= overall_score_auc + 0.02
        and better_folds >= max(3, len(fold_reports) - 1)
        and top_lift is not None
        and top_lift >= 5.0
    )

    status = (
        "historically_validated_shadow_only"
        if historical_validated
        else "experimental_not_validated"
    )

    # Fit once on all historical rows only to inspect which inputs the model
    # uses. This model is NOT wired into production scoring.
    final_model = _fit_booster(rows, np, xgb)
    raw_importance = final_model.get_score(importance_type="gain")
    total_gain = sum(float(raw_importance.get(name, 0.0)) for name in FEATURES)
    feature_importance = sorted(
        [
            {
                "feature": name,
                "importance": round(
                    (
                        float(raw_importance.get(name, 0.0)) / total_gain
                        if total_gain > 0
                        else 0.0
                    ),
                    6,
                ),
            }
            for name in FEATURES
        ],
        key=lambda item: item["importance"],
        reverse=True,
    )

    return {
        "model_version": MODEL_VERSION,
        "status": status,
        "historical_validated": historical_validated,
        "production_enabled": False,
        "target": (
            f"reach_+{SWING_TARGET_PCT:g}pct_before_-{SWING_STOP_PCT:g}pct_"
            f"within_{SWING_HORIZON_SESSIONS}_trading_sessions"
        ),
        "samples": len(rows),
        "unique_dates": len(unique_dates),
        "unique_years": len(unique_years),
        "calendar_years": sorted(unique_years),
        "unique_symbols": len({row["symbol"] for row in rows}),
        "features": FEATURES,
        "base_features": BASE_FEATURES,
        "regime_features": REGIME_FEATURES,
        "overall": {
            "test_samples": len(all_y),
            "base_target_before_stop_rate_pct": (
                round(base_rate * 100.0, 1) if base_rate is not None else None
            ),
            "model_auc": (
                round(overall_model_auc, 4)
                if overall_model_auc is not None
                else None
            ),
            "baseline_model_auc": (
                round(overall_baseline_model_auc, 4)
                if overall_baseline_model_auc is not None
                else None
            ),
            "regime_minus_baseline_auc": (
                round(
                    overall_model_auc - overall_baseline_model_auc,
                    4,
                )
                if (
                    overall_model_auc is not None
                    and overall_baseline_model_auc is not None
                )
                else None
            ),
            "regime_features_improved_folds": regime_improved_folds,
            "hand_score_auc": (
                round(overall_score_auc, 4)
                if overall_score_auc is not None
                else None
            ),
            "model_minus_hand_auc": (
                round(overall_model_auc - overall_score_auc, 4)
                if overall_model_auc is not None and overall_score_auc is not None
                else None
            ),
            "mean_fold_auc": (
                round(mean_fold_auc, 4) if mean_fold_auc is not None else None
            ),
            "brier": round(overall_brier, 4) if overall_brier is not None else None,
            "folds_beating_hand_score": better_folds,
            "fold_count": len(fold_reports),
            "top_decile_target_rate_lift_pp": top_lift,
            "probability_bands": bands,
        },
        "folds": fold_reports,
        "feature_importance": feature_importance[:15],
        "validation_gate": {
            "min_samples": 2500,
            "min_unique_dates": 120,
            "min_calendar_years": 4,
            "min_mean_fold_auc": 0.55,
            "min_overall_auc": 0.55,
            "min_auc_advantage_vs_hand_score": 0.02,
            "min_folds_beating_hand_score": max(3, len(fold_reports) - 1),
            "min_top_decile_target_rate_lift_pp": 5.0,
        },
        "note": (
            "This is a shadow validation model only. It predicts a trade-like "
            "+5% before -4% five-session path, not a simple five-day close. "
            "Same-day target/stop touches are excluded because daily OHLC cannot "
            "establish order. The same folds also train an otherwise identical "
            "baseline model without regime features, so regime value is measured "
            "directly. Multi-year validation also requires at least four calendar "
            "years and 120 independent replay dates. It cannot change live Analyzer "
            "scores until historical "
            "validation and later live out-of-sample confirmation pass."
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=str(DATA_PATH))
    parser.add_argument("--output", default=str(DEFAULT_REPORT_PATH))
    args = parser.parse_args()

    rows, payload = load_rows(args.data)
    report = validate(rows)
    report["source_replay_version"] = payload.get("replay_version")
    report["source_generated_at_utc"] = payload.get("generated_at_utc")
    report["skipped_ambiguous_same_day"] = int(
        payload.get("_ml_skipped_ambiguous_same_day") or 0
    )
    path_spec = (payload.get("summary") or {}).get("swing_path_target") or {}
    report["path_target_spec"] = path_spec
    target_spec_matches = bool(
        _num(path_spec.get("target_pct")) == float(SWING_TARGET_PCT)
        and _num(path_spec.get("stop_pct")) == float(SWING_STOP_PCT)
        and int(path_spec.get("horizon_sessions") or 0)
        == int(SWING_HORIZON_SESSIONS)
    )
    report["target_spec_matches_shared_definition"] = target_spec_matches
    if not target_spec_matches:
        report["historical_validated"] = False
        report["production_enabled"] = False
        report["status"] = "target_definition_mismatch"

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("SWING_TIMEFRAME_ML_VALIDATION=" + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
