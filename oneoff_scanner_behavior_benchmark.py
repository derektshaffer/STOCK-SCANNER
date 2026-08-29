from __future__ import annotations

import json
import math
import os
from pathlib import Path
from statistics import mean

import numpy as np
import xgboost as xgb

import scanner_ml_ranker as sm

INPUT = Path(os.environ.get("BEHAVIOR_REPLAY_INPUT", "outcome_reports/outcomes_behavior_replay.json"))
OUTPUT = Path(os.environ.get("BEHAVIOR_BENCHMARK_OUTPUT", "outcome_reports/scanner_behavior_benchmark.json"))

BASE = list(sm.FEATURES)
BEHAVIOR_CORE = [
    "impulse_bounce_recovery_pct",
    "pullback_volume_ratio",
    "bounce_leg_code",
]
BEHAVIOR_RISK = [
    "bounce_count",
    "bounce_decay_ratio",
    "bounce_volume_decay_ratio",
    "lower_high_streak",
    "higher_low_streak",
    "sequence_health_score",
    "current_pullback_pct",
    "ongoing_bounce_pct",
]
STAIR = [
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
IMPULSE = [
    "impulse_move_pct",
    "impulse_retracement_pct",
    "impulse_max_retracement_pct",
    "impulse_bounce_recovery_pct",
    "pullback_volume_ratio",
]


def _rows():
    payload = json.loads(INPUT.read_text(encoding="utf-8"))
    out = []
    for raw in payload.get("observations") or []:
        scan_time = raw.get("scan_time_et")
        dt = sm._parse_dt(scan_time)
        if dt is None:
            continue
        feature = sm._feature_dict(raw, scan_time)
        quality = bool(raw.get("target_before_stop"))
        decisive = bool(raw.get("trade_quality_decisive"))
        ret60 = sm._num(raw.get("return_60m_pct"))
        if ret60 is None:
            continue
        out.append({
            "trading_date": dt.date().isoformat(),
            "scan_time_et": scan_time,
            "symbol": raw.get("symbol"),
            "features": feature,
            "continuation_label": int(ret60 >= 3.0),
            "quality_label": int(quality),
            "quality_decisive": decisive,
            "barrier": raw.get("trade_quality_barrier"),
            "mfe_60m_pct": sm._num(raw.get("mfe_60m_pct")),
            "mae_60m_pct": sm._num(raw.get("mae_60m_pct")),
        })
    return out


def _matrix(rows, features, label):
    X = np.array([
        [
            np.nan if row["features"].get(name) is None
            else float(row["features"].get(name))
            for name in features
        ]
        for row in rows
    ], dtype=float)
    y = np.array([int(row[label]) for row in rows], dtype=float)
    return X, y


def _bench(rows, features, label):
    rows = sorted(rows, key=lambda r: (r["trading_date"], r["scan_time_et"], str(r["symbol"])))
    days = sorted({r["trading_date"] for r in rows})
    X, y = _matrix(rows, features, label)
    probs = []
    actual = []
    baselines = []
    folds = []
    for train_frac, val_frac in ((0.55, 0.70), (0.70, 0.85), (0.85, 1.00)):
        train_pos = min(len(days)-1, max(0, int(len(days)*train_frac)-1))
        val_pos = min(len(days)-1, max(0, int(len(days)*val_frac)-1))
        train_cut = days[train_pos]
        val_cut = days[val_pos]
        train_idx = [i for i,r in enumerate(rows) if r["trading_date"] <= train_cut]
        val_idx = [i for i,r in enumerate(rows) if train_cut < r["trading_date"] <= val_cut]
        if len(train_idx) < 100 or len(val_idx) < 20:
            continue
        ytr, yv = y[train_idx], y[val_idx]
        if len(set(ytr.tolist())) < 2 or len(set(yv.tolist())) < 2:
            continue
        model = xgb.train(
            sm._params(),
            xgb.DMatrix(X[train_idx], label=ytr, feature_names=features),
            num_boost_round=120,
            verbose_eval=False,
        )
        p = model.predict(xgb.DMatrix(X[val_idx], feature_names=features))
        base_rate = float(ytr.mean())
        probs.extend(float(v) for v in p)
        actual.extend(int(v) for v in yv)
        baselines.extend([base_rate] * len(yv))
        folds.append({
            "train_through": train_cut,
            "validate_through": val_cut,
            "train_rows": len(train_idx),
            "validation_rows": len(val_idx),
            "auc": sm._auc([int(v) for v in yv], [float(v) for v in p]),
        })

    auc = sm._auc(actual, probs)
    brier = mean((p-y)**2 for p,y in zip(probs, actual))
    baseline_brier = mean((p-y)**2 for p,y in zip(baselines, actual))
    return {
        "rows": len(rows),
        "positives": int(sum(row[label] for row in rows)),
        "positive_rate_pct": round(mean(row[label] for row in rows)*100.0, 3),
        "features": len(features),
        "validation_rows": len(actual),
        "auc": auc,
        "brier": brier,
        "baseline_brier": baseline_brier,
        "brier_skill": 1.0 - brier / baseline_brier if baseline_brier > 0 else None,
        "folds": folds,
    }


def main():
    rows = _rows()
    decisive = [row for row in rows if row["quality_decisive"]]
    feature_sets = {
        "current": BASE,
        "current_plus_core_behavior": BASE + BEHAVIOR_CORE,
        "current_plus_impulse": BASE + IMPULSE,
        "current_plus_bounce_risk": BASE + BEHAVIOR_RISK,
        "current_plus_stair": BASE + STAIR,
        "current_plus_all_behavior": BASE + list(dict.fromkeys(IMPULSE + BEHAVIOR_RISK + STAIR + ["bounce_leg_code"])),
    }
    result = {
        "input_rows": len(rows),
        "decisive_quality_rows": len(decisive),
        "barrier_counts": {
            name: sum(1 for row in rows if row["barrier"] == name)
            for name in ("target_first", "stop_first", "neither")
        },
        "benchmarks": {},
    }
    for name, features in feature_sets.items():
        result["benchmarks"][name] = {
            "continuation": _bench(rows, features, "continuation_label"),
            "quality_all_rows": _bench(rows, features, "quality_label"),
            "quality_decisive_only": _bench(decisive, features, "quality_label"),
        }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
