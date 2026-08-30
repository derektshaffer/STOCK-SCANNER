"""Experimental Swing timeframe ML validation.

This module does NOT alter live Analyzer scores. It trains and evaluates a
shadow-only model against the leakage-aware historical timeframe replay.

Target: probability the stock closes higher 5 trading days after the replay
observation. Validation is chronological and grouped by replay date so rows
from the same market day never appear in both train and test.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

DATA_PATH = Path("timeframe_replay/timeframe_historical_replay.json")
DEFAULT_REPORT_PATH = Path("timeframe_replay/timeframe_ml_validation.json")
MODEL_VERSION = "swing-timeframe-ml-v1-shadow"

FEATURES = [
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
    "stair_breakdown",
    "historical_bias_score",
    "historical_next_day_up_pct",
    "historical_sample_count",
    "broad_market_avg_pct",
]


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
        "stair_breakdown": _num(stair.get("stair_breakdown")),
        "historical_bias_score": _num(historical.get("bias_score")),
        "historical_next_day_up_pct": _num(historical.get("next_day_up_pct")),
        "historical_sample_count": _num(historical.get("sample_count")),
        "broad_market_avg_pct": _num(market.get("broad_market_avg_pct")),
    }


def load_rows(path=DATA_PATH):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = []
    for row in payload.get("observations") or []:
        if row.get("timeframe_score_version") != "timeframe-fit-v1":
            continue
        outcome = _num((row.get("outcomes") or {}).get("return_5d_pct"))
        if outcome is None:
            continue
        date_text = str(row.get("as_of") or "")[:10]
        if not date_text:
            continue
        rows.append(
            {
                "date": date_text,
                "symbol": str(row.get("symbol") or "").upper().strip(),
                "return_5d_pct": outcome,
                "label": int(outcome > 0),
                "swing_score": _num(row.get("swing_score")),
                "features": _feature_dict(row),
            }
        )
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


def _matrix(rows, np):
    X = np.array(
        [
            [
                np.nan
                if row["features"].get(name) is None
                else float(row["features"].get(name))
                for name in FEATURES
            ]
            for row in rows
        ],
        dtype=float,
    )
    y = np.array([int(row["label"]) for row in rows], dtype=int)
    return X, y


def _model():
    from xgboost import XGBClassifier

    return XGBClassifier(
        n_estimators=260,
        max_depth=3,
        learning_rate=0.04,
        min_child_weight=6,
        subsample=0.82,
        colsample_bytree=0.82,
        reg_alpha=0.15,
        reg_lambda=2.5,
        objective="binary:logistic",
        eval_metric="logloss",
        tree_method="hist",
        n_jobs=2,
        random_state=17,
    )


def _chronological_folds(rows):
    dates = sorted({row["date"] for row in rows})
    if len(dates) < 24:
        return []
    test_block = max(4, len(dates) // 8)
    fold_count = 4
    initial_train = len(dates) - fold_count * test_block
    if initial_train < 16:
        fold_count = 3
        initial_train = len(dates) - fold_count * test_block
    folds = []
    for fold_index in range(fold_count):
        test_start = initial_train + fold_index * test_block
        test_end = test_start + test_block
        train_dates = set(dates[:test_start])
        test_dates = set(dates[test_start:test_end])
        train = [row for row in rows if row["date"] in train_dates]
        test = [row for row in rows if row["date"] in test_dates]
        if len(train) >= 250 and len(test) >= 75:
            folds.append((train, test, sorted(train_dates), sorted(test_dates)))
    return folds


def _probability_band_stats(rows, probabilities):
    pairs = sorted(zip(probabilities, rows), key=lambda item: item[0], reverse=True)
    if not pairs:
        return {}
    top_n = max(1, len(pairs) // 10)
    top = pairs[:top_n]
    bottom = pairs[-top_n:]

    def summarize(group):
        values = [row["return_5d_pct"] for _p, row in group]
        labels = [row["label"] for _p, row in group]
        probs = [float(p) for p, _row in group]
        return {
            "n": len(group),
            "avg_probability_pct": round(sum(probs) / len(probs) * 100.0, 2),
            "higher_rate_pct": round(sum(labels) / len(labels) * 100.0, 1),
            "avg_return_pct": round(sum(values) / len(values), 3),
        }

    return {
        "top_decile": summarize(top),
        "bottom_decile": summarize(bottom),
    }


def validate(rows):
    import numpy as np

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
    all_score = []
    all_test_rows = []

    for index, (train, test, train_dates, test_dates) in enumerate(folds, start=1):
        X_train, y_train = _matrix(train, np)
        X_test, y_test = _matrix(test, np)
        model = _model()
        model.fit(X_train, y_train)
        probabilities = model.predict_proba(X_test)[:, 1].tolist()
        labels = y_test.tolist()
        swing_scores = [
            50.0 if row.get("swing_score") is None else row["swing_score"]
            for row in test
        ]
        model_auc = _auc(labels, probabilities)
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
                "base_higher_rate_pct": (
                    round(base_rate * 100.0, 1) if base_rate is not None else None
                ),
                "model_auc": round(model_auc, 4) if model_auc is not None else None,
                "hand_score_auc": (
                    round(score_auc, 4) if score_auc is not None else None
                ),
                "brier": round(_brier(labels, probabilities), 4),
                "probability_bands": bands,
            }
        )
        all_y.extend(labels)
        all_prob.extend(probabilities)
        all_score.extend(swing_scores)
        all_test_rows.extend(test)

    overall_model_auc = _auc(all_y, all_prob)
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
    top = bands.get("top_decile") or {}
    top_lift = None
    if base_rate is not None and top.get("higher_rate_pct") is not None:
        top_lift = round(top["higher_rate_pct"] - base_rate * 100.0, 1)

    historical_validated = bool(
        len(rows) >= 800
        and len({row["date"] for row in rows}) >= 35
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
    X_all, y_all = _matrix(rows, np)
    final_model = _model()
    final_model.fit(X_all, y_all)
    importances = final_model.feature_importances_.tolist()
    feature_importance = sorted(
        [
            {"feature": name, "importance": round(float(value), 6)}
            for name, value in zip(FEATURES, importances)
        ],
        key=lambda item: item["importance"],
        reverse=True,
    )

    return {
        "model_version": MODEL_VERSION,
        "status": status,
        "historical_validated": historical_validated,
        "production_enabled": False,
        "target": "close_higher_after_5_trading_days",
        "samples": len(rows),
        "unique_dates": len({row["date"] for row in rows}),
        "unique_symbols": len({row["symbol"] for row in rows}),
        "features": FEATURES,
        "overall": {
            "test_samples": len(all_y),
            "base_higher_rate_pct": (
                round(base_rate * 100.0, 1) if base_rate is not None else None
            ),
            "model_auc": (
                round(overall_model_auc, 4)
                if overall_model_auc is not None
                else None
            ),
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
            "top_decile_higher_rate_lift_pp": top_lift,
            "probability_bands": bands,
        },
        "folds": fold_reports,
        "feature_importance": feature_importance[:15],
        "validation_gate": {
            "min_samples": 800,
            "min_unique_dates": 35,
            "min_mean_fold_auc": 0.55,
            "min_overall_auc": 0.55,
            "min_auc_advantage_vs_hand_score": 0.02,
            "min_folds_beating_hand_score": max(3, len(fold_reports) - 1),
            "min_top_decile_lift_pp": 5.0,
        },
        "note": (
            "This is a shadow validation model only. It cannot change live "
            "Analyzer scores until historical validation passes and later live "
            "out-of-sample confirmation also passes."
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

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("SWING_TIMEFRAME_ML_VALIDATION=" + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
