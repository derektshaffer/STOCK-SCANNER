from __future__ import annotations

import io
import json
import math
import os
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from statistics import mean

MODEL_VERSION = "scanner-ml-v2"
CURRENT_FEATURE_VERSION = "scanner-features-v2-consolidated"
MODEL_TYPE = "XGBoost"
TARGET_DESCRIPTION = ">= +3% at 60 minutes"

REPORT_DIR = Path(
    os.environ.get("OUTCOME_REPORT_DIR", "outcome_reports").strip()
    or "outcome_reports"
)
MIN_SAMPLES = 180
MIN_UNIQUE_SCANS = 24
MIN_TRADING_DAYS = 3
MIN_CLASS_COUNT = 30
MIN_VALIDATION_SAMPLES = 60
MIN_LIVE_CONFIRMATION_SAMPLES = 30
MIN_LIVE_CONFIRMATION_DAYS = 2
MIN_LIVE_CONFIRMATION_CLASS_COUNT = 5
MAX_ARTIFACTS = 12
ARTIFACT_PREFIX = "outcome-report-"

FEATURES = [
    "day_pct",
    "score",
    "base_score",
    "live_bonus",
    "news_bonus",
    "momentum_5m",
    "momentum_15m",
    "volume_pace",
    "intraday_range_pct",
    "distance_from_high_pct",
    "distance_from_vwap_pct",
    "above_vwap",
    "log_liquidity",
    "spread_pct",
    "expected_volume_fraction_pct",
    "volume_vs_expected_pct",
    "live_confirmation_count",
    "failed_count",
    "warning_count",
    "flag_count",
    "time_fraction",
]


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _time_fraction(value):
    dt = _parse_dt(value)
    if dt is None:
        return None
    minutes = dt.hour * 60 + dt.minute
    return max(0.0, min(1.0, (minutes - 570) / 390.0))


def _feature_dict(row, scan_time=None):
    liquidity = _num(row.get("liquidity_dollar_volume"))
    failed = row.get("failed_filters")
    warnings = row.get("tradability_warnings")
    flags = row.get("setup_flags")
    news_bonus = row.get("news_bonus")
    if news_bonus is None:
        news_bonus = row.get("news_score")

    return {
        "day_pct": _num(row.get("day_pct")),
        "score": _num(row.get("score")),
        "base_score": _num(row.get("base_score")),
        "live_bonus": _num(row.get("live_bonus")),
        "news_bonus": _num(news_bonus),
        "momentum_5m": _num(row.get("momentum_5m")),
        "momentum_15m": _num(row.get("momentum_15m")),
        "volume_pace": _num(row.get("volume_pace")),
        "intraday_range_pct": _num(row.get("intraday_range_pct")),
        "distance_from_high_pct": _num(row.get("distance_from_high_pct")),
        "distance_from_vwap_pct": _num(row.get("distance_from_vwap_pct")),
        "above_vwap": 1.0 if bool(row.get("above_vwap")) else 0.0,
        "log_liquidity": (
            math.log1p(max(0.0, liquidity)) if liquidity is not None else None
        ),
        "spread_pct": _num(
            row.get("spread_pct")
            if row.get("spread_pct") is not None
            else row.get("iex_spread_pct")
        ),
        "expected_volume_fraction_pct": _num(
            row.get("expected_volume_fraction_pct")
        ),
        "volume_vs_expected_pct": _num(row.get("volume_vs_expected_pct")),
        "live_confirmation_count": _num(row.get("live_confirmation_count")),
        "failed_count": (
            float(len(failed)) if isinstance(failed, list)
            else _num(row.get("failed_count"))
        ),
        "warning_count": (
            float(len(warnings)) if isinstance(warnings, list)
            else _num(row.get("warning_count"))
        ),
        "flag_count": (
            float(len(flags)) if isinstance(flags, list)
            else _num(row.get("flag_count"))
        ),
        "time_fraction": _time_fraction(scan_time or row.get("scan_time_et")),
    }


def _extract_observations(payload):
    if not isinstance(payload, dict):
        return []

    out = []
    for row in payload.get("observations") or []:
        if row.get("feature_version") != CURRENT_FEATURE_VERSION:
            continue
        return_60 = _num(row.get("return_60m_pct"))
        if return_60 is None:
            continue

        scan_time = row.get("scan_time_et")
        dt = _parse_dt(scan_time)
        if dt is None:
            continue

        features = _feature_dict(row, scan_time)
        if any(
            features.get(name) is None
            for name in ("momentum_5m", "momentum_15m", "volume_pace")
        ):
            continue

        out.append(
            {
                "observation_id": (
                    row.get("observation_id")
                    or f"{row.get('scan_id')}:{row.get('symbol')}"
                ),
                "timestamp": dt.timestamp(),
                "scan_time_et": scan_time,
                "trading_date": dt.date().isoformat(),
                "scan_id": row.get("scan_id"),
                "observation_source": (
                    row.get("observation_source")
                    or payload.get("source")
                    or "live_scan"
                ),
                "label": int(return_60 >= 3.0),
                "features": features,
            }
        )
    return out


def _read_local_reports():
    observations = []
    report_count = 0
    if not REPORT_DIR.exists():
        return observations, report_count

    for path in sorted(REPORT_DIR.glob("outcomes_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        report_count += 1
        observations.extend(_extract_observations(payload))
    return observations, report_count


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _github_json(url, token):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "momentum-scanner-ml/1.0",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _download_artifact_zip(repository, artifact_id, token):
    url = (
        f"https://api.github.com/repos/{repository}/actions/"
        f"artifacts/{artifact_id}/zip"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "momentum-scanner-ml/1.0",
    }
    req = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(_NoRedirect())
    location = None

    try:
        with opener.open(req, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code not in (301, 302, 303, 307, 308):
            raise
        location = exc.headers.get("Location")

    if not location:
        raise RuntimeError("GitHub artifact download returned no redirect URL.")

    storage_req = urllib.request.Request(
        location,
        headers={
            "Accept": "application/zip",
            "User-Agent": "momentum-scanner-ml/1.0",
        },
    )
    with urllib.request.urlopen(storage_req, timeout=45) as response:
        return response.read()


def _read_artifact_reports():
    token = (
        os.environ.get("GH_TOKEN", "").strip()
        or os.environ.get("GITHUB_TOKEN", "").strip()
    )
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token or "/" not in repository:
        return [], 0

    observations = []
    artifact_count = 0
    try:
        data = _github_json(
            f"https://api.github.com/repos/{repository}/actions/"
            "artifacts?per_page=100",
            token,
        )
        artifacts = [
            a
            for a in (data.get("artifacts") or [])
            if str(a.get("name") or "").startswith(ARTIFACT_PREFIX)
            and not a.get("expired")
        ][:MAX_ARTIFACTS]

        for artifact in reversed(artifacts):
            raw = _download_artifact_zip(
                repository, artifact.get("id"), token
            )
            artifact_count += 1
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                for name in zf.namelist():
                    base = Path(name).name
                    if (
                        not base.startswith("outcomes_")
                        or not base.endswith(".json")
                    ):
                        continue
                    try:
                        payload = json.loads(
                            zf.read(name).decode("utf-8")
                        )
                    except Exception:
                        continue
                    observations.extend(_extract_observations(payload))
    except Exception:
        return [], 0

    return observations, artifact_count


def load_training_observations():
    local, local_reports = _read_local_reports()
    artifact_rows, artifact_count = _read_artifact_reports()

    deduped = {}
    for row in local + artifact_rows:
        key = row.get("observation_id")
        if key:
            deduped[key] = row

    rows = sorted(
        deduped.values(),
        key=lambda row: row["timestamp"],
    )
    source_counts = {}
    for row in rows:
        source = str(row.get("observation_source") or "live_scan")
        source_counts[source] = source_counts.get(source, 0) + 1

    return rows, {
        "local_report_count": local_reports,
        "artifact_report_count": artifact_count,
        "observations_loaded": len(rows),
        "observation_source_counts": source_counts,
        "historical_replay_samples": source_counts.get("historical_replay", 0),
        "live_samples": sum(
            count
            for source, count in source_counts.items()
            if source != "historical_replay"
        ),
    }


def _auc(y, probabilities):
    positives = sum(int(value == 1) for value in y)
    negatives = len(y) - positives
    if positives == 0 or negatives == 0:
        return None

    pairs = sorted(
        zip(probabilities, y),
        key=lambda item: item[0],
    )
    rank_sum_pos = 0.0
    i = 0
    rank = 1

    while i < len(pairs):
        j = i + 1
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        avg_rank = (
            rank + (rank + (j - i) - 1)
        ) / 2.0
        rank_sum_pos += avg_rank * sum(
            int(pairs[k][1] == 1)
            for k in range(i, j)
        )
        rank += j - i
        i = j

    return (
        rank_sum_pos
        - positives * (positives + 1) / 2.0
    ) / (positives * negatives)


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
    y = np.array(
        [int(row["label"]) for row in rows],
        dtype=float,
    )
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


def _validation_and_model(rows):
    try:
        import numpy as np
        import xgboost as xgb
    except Exception as exc:
        return None, {
            "status": "dependency_missing",
            "validated": False,
            "error": str(exc)[:180],
        }

    samples = len(rows)
    positives = sum(row["label"] for row in rows)
    negatives = samples - positives
    scans = sorted(
        {
            row.get("scan_id") or row.get("scan_time_et")
            for row in rows
        }
    )
    days = sorted(
        {
            row.get("trading_date")
            for row in rows
            if row.get("trading_date")
        }
    )

    replay_rows = [
        row
        for row in rows
        if row.get("observation_source") == "historical_replay"
    ]
    live_rows = [
        row
        for row in rows
        if row.get("observation_source") != "historical_replay"
    ]
    live_days = sorted(
        {
            row.get("trading_date")
            for row in live_rows
            if row.get("trading_date")
        }
    )

    base_meta = {
        "status": "learning",
        "validated": False,
        "historical_validated": False,
        "model_type": MODEL_TYPE,
        "version": MODEL_VERSION,
        "target": TARGET_DESCRIPTION,
        "samples": samples,
        "positives": positives,
        "negatives": negatives,
        "unique_scans": len(scans),
        "trading_days": len(days),
        "historical_replay_samples": len(replay_rows),
        "live_samples": len(live_rows),
        "live_trading_days": len(live_days),
        "live_confirmation_min_samples": MIN_LIVE_CONFIRMATION_SAMPLES,
        "live_confirmation_min_days": MIN_LIVE_CONFIRMATION_DAYS,
    }

    if (
        samples < MIN_SAMPLES
        or len(scans) < MIN_UNIQUE_SCANS
        or len(days) < MIN_TRADING_DAYS
        or positives < MIN_CLASS_COUNT
        or negatives < MIN_CLASS_COUNT
    ):
        return None, base_meta

    X, y = _matrix(rows, np)
    unique_times = sorted(
        {row["timestamp"] for row in rows}
    )
    if len(unique_times) < MIN_UNIQUE_SCANS:
        return None, base_meta

    fold_bounds = (
        (0.55, 0.70),
        (0.70, 0.85),
        (0.85, 1.00),
    )
    val_probs = []
    val_y = []
    val_baseline_probs = []

    for train_frac, val_frac in fold_bounds:
        train_pos = min(
            len(unique_times) - 1,
            max(0, int(len(unique_times) * train_frac) - 1),
        )
        val_pos = min(
            len(unique_times) - 1,
            max(0, int(len(unique_times) * val_frac) - 1),
        )
        train_cut = unique_times[train_pos]
        val_cut = unique_times[val_pos]
        train_idx = [
            i
            for i, row in enumerate(rows)
            if row["timestamp"] <= train_cut
        ]
        val_idx = [
            i
            for i, row in enumerate(rows)
            if train_cut < row["timestamp"] <= val_cut
        ]
        if len(train_idx) < 100 or len(val_idx) < 20:
            continue

        ytr = y[train_idx]
        yv = y[val_idx]
        if (
            len(set(ytr.tolist())) < 2
            or len(set(yv.tolist())) < 2
        ):
            continue

        model = xgb.train(
            _params(),
            xgb.DMatrix(
                X[train_idx],
                label=ytr,
                feature_names=FEATURES,
            ),
            num_boost_round=120,
            verbose_eval=False,
        )
        probs = model.predict(
            xgb.DMatrix(
                X[val_idx],
                feature_names=FEATURES,
            )
        )
        base_rate = float(ytr.mean())
        val_probs.extend(float(p) for p in probs)
        val_y.extend(int(v) for v in yv)
        val_baseline_probs.extend(
            [base_rate] * len(yv)
        )

    if len(val_y) < MIN_VALIDATION_SAMPLES:
        base_meta["status"] = "insufficient_validation"
        base_meta["validation_samples"] = len(val_y)
        return None, base_meta

    auc = _auc(val_y, val_probs)
    brier = mean(
        (probability - actual) ** 2
        for probability, actual in zip(val_probs, val_y)
    )
    baseline_brier = mean(
        (probability - actual) ** 2
        for probability, actual
        in zip(val_baseline_probs, val_y)
    )
    accuracy = mean(
        (probability >= 0.5) == bool(actual)
        for probability, actual in zip(val_probs, val_y)
    )
    baseline_accuracy = mean(
        (probability >= 0.5) == bool(actual)
        for probability, actual
        in zip(val_baseline_probs, val_y)
    )

    historical_validated = bool(
        auc is not None
        and auc >= 0.55
        and brier < baseline_brier
        and len(val_y) >= MIN_VALIDATION_SAMPLES
    )

    meta = {
        **base_meta,
        "historical_validated": historical_validated,
        "validation_samples": len(val_y),
        "walk_forward_auc": (
            round(auc, 3)
            if auc is not None
            else None
        ),
        "walk_forward_brier": round(brier, 4),
        "baseline_brier": round(
            baseline_brier, 4
        ),
        "walk_forward_accuracy_pct": round(
            accuracy * 100.0, 1
        ),
        "baseline_accuracy_pct": round(
            baseline_accuracy * 100.0, 1
        ),
    }

    if not historical_validated:
        meta["status"] = "failed_validation"
        return None, meta

    # Historical replay can establish a strong model quickly, but it cannot
    # reproduce live bid/ask spreads, quote freshness or every catalyst. When
    # replay data is present, require a smaller truly-live holdout before the
    # scanner grants the full validated badge or gives ML ranking weight.
    fully_validated = True
    if replay_rows:
        live_positives = sum(row["label"] for row in live_rows)
        live_negatives = len(live_rows) - live_positives
        enough_live = bool(
            len(live_rows) >= MIN_LIVE_CONFIRMATION_SAMPLES
            and len(live_days) >= MIN_LIVE_CONFIRMATION_DAYS
            and live_positives >= MIN_LIVE_CONFIRMATION_CLASS_COUNT
            and live_negatives >= MIN_LIVE_CONFIRMATION_CLASS_COUNT
        )
        meta["live_positives"] = live_positives
        meta["live_negatives"] = live_negatives
        meta["live_confirmation_ready"] = enough_live

        if not enough_live:
            fully_validated = False
            meta["status"] = "replay_validated_waiting_live"
        else:
            replay_X, replay_y = _matrix(replay_rows, np)
            live_X, live_y = _matrix(live_rows, np)
            replay_model = xgb.train(
                _params(),
                xgb.DMatrix(
                    replay_X,
                    label=replay_y,
                    feature_names=FEATURES,
                ),
                num_boost_round=145,
                verbose_eval=False,
            )
            live_probs = replay_model.predict(
                xgb.DMatrix(
                    live_X,
                    feature_names=FEATURES,
                )
            )
            live_y_list = [int(value) for value in live_y.tolist()]
            live_prob_list = [float(value) for value in live_probs]
            live_auc = _auc(live_y_list, live_prob_list)
            live_brier = mean(
                (probability - actual) ** 2
                for probability, actual in zip(live_prob_list, live_y_list)
            )
            replay_base_rate = float(replay_y.mean())
            live_baseline_brier = mean(
                (replay_base_rate - actual) ** 2
                for actual in live_y_list
            )
            live_pass = bool(
                live_auc is not None
                and live_auc >= 0.52
                and live_brier < live_baseline_brier
            )
            meta.update(
                {
                    "live_confirmation_auc": (
                        round(live_auc, 3)
                        if live_auc is not None
                        else None
                    ),
                    "live_confirmation_brier": round(live_brier, 4),
                    "live_confirmation_baseline_brier": round(
                        live_baseline_brier, 4
                    ),
                    "live_confirmation_passed": live_pass,
                }
            )
            fully_validated = live_pass
            meta["status"] = (
                "validated"
                if live_pass
                else "failed_live_confirmation"
            )
    else:
        meta["status"] = "validated"

    meta["validated"] = fully_validated

    # A replay-validated model remains available for advisory predictions while
    # live confirmation accumulates. Advisory predictions do not affect rank.
    final_model = xgb.train(
        _params(),
        xgb.DMatrix(
            X,
            label=y,
            feature_names=FEATURES,
        ),
        num_boost_round=145,
        verbose_eval=False,
    )
    importance = final_model.get_score(
        importance_type="gain"
    )
    top = sorted(
        importance.items(),
        key=lambda item: float(item[1]),
        reverse=True,
    )[:5]
    total = (
        sum(float(value) for _, value in top)
        or 1.0
    )
    meta["top_features"] = [
        {
            "feature": key,
            "share_pct": round(
                float(value) / total * 100.0,
                1,
            ),
        }
        for key, value in top
    ]
    return final_model, meta


def apply_scanner_ml(rows, now_et):
    """Apply a validated cross-market continuation model to scanner rows.

    The label is whether a prior scanner observation was at least +3% higher
    60 minutes later. Walk-forward validation is chronological, and ML does not
    affect ranking until it beats the baseline validation gate.
    """
    training_rows, source_meta = (
        load_training_observations()
    )
    model, meta = _validation_and_model(
        training_rows
    )
    meta.update(source_meta)
    meta["feature_version"] = CURRENT_FEATURE_VERSION

    for row in rows:
        row["feature_version"] = CURRENT_FEATURE_VERSION
        row["ml_model_version"] = MODEL_VERSION
        row["ml_target"] = TARGET_DESCRIPTION
        row["ml_validated"] = bool(
            meta.get("validated")
        )
        row["ml_status"] = meta.get("status")
        row["ml_training_samples"] = meta.get(
            "samples", 0
        )
        row["ml_validation_auc"] = meta.get(
            "walk_forward_auc"
        )
        row["ml_continuation_prob_pct"] = None
        row["ml_advisory_prob_pct"] = None
        row["opportunity_score"] = row.get("score")

    if (
        model is None
        or not meta.get("historical_validated")
        or not rows
    ):
        return meta

    try:
        import numpy as np
        import xgboost as xgb

        eligible = []
        for row in rows:
            feature = _feature_dict(
                row,
                now_et.isoformat(),
            )
            if any(
                feature.get(name) is None
                for name in ("momentum_5m", "momentum_15m", "volume_pace")
            ):
                continue
            eligible.append((row, feature))

        if not eligible:
            return meta

        X = np.array(
            [
                [
                    np.nan
                    if feature.get(name) is None
                    else float(feature.get(name))
                    for name in FEATURES
                ]
                for _, feature in eligible
            ],
            dtype=float,
        )
        probabilities = model.predict(
            xgb.DMatrix(
                X,
                feature_names=FEATURES,
            )
        )
    except Exception as exc:
        meta["status"] = "prediction_error"
        meta["validated"] = False
        meta["error"] = str(exc)[:180]
        for row in rows:
            row["ml_validated"] = False
            row["ml_status"] = "prediction_error"
        return meta

    for (row, _), probability in zip(
        eligible, probabilities
    ):
        probability_pct = round(
            float(probability) * 100.0,
            1,
        )
        scanner_score = (
            _num(row.get("score")) or 0.0
        )
        opportunity = round(
            0.70 * scanner_score
            + 0.30 * probability_pct,
            1,
        )
        row["ml_advisory_prob_pct"] = probability_pct
        if meta.get("validated"):
            row["ml_continuation_prob_pct"] = probability_pct
            row["opportunity_score"] = opportunity

    return meta
