from __future__ import annotations

import hashlib
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
# Historical replay is intentionally useful for fast learning, but its seed
# universe is based on stocks listed/liquid today. That creates survivorship
# risk in older replay periods. Require a materially larger, strictly later
# live holdout before replay-backed ML may change production ranking.
MIN_LIVE_CONFIRMATION_SAMPLES = 100
MIN_LIVE_CONFIRMATION_DAYS = 5
MIN_LIVE_CONFIRMATION_CLASS_COUNT = 15
MIN_LIVE_CONFIRMATION_SYMBOLS = 15
LIVE_CONFIRMATION_MIN_GAP_SECONDS = 60 * 60
MAX_ARTIFACTS = 12
ARTIFACT_PREFIX = "outcome-report-"
MODEL_CACHE_DIR = Path(
    os.environ.get("SCANNER_ML_CACHE_DIR", ".scanner_cache").strip()
    or ".scanner_cache"
)
MODEL_CACHE_PATH = MODEL_CACHE_DIR / "scanner_ml_model.json"
MODEL_CACHE_META_PATH = MODEL_CACHE_DIR / "scanner_ml_model_meta.json"

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
        # Extra analyzer/peer features. The scanner's own FEATURES list can
        # ignore these, while peer_ml_predictor can consume them from the same
        # historical observation loader when present.
        "impulse_move_pct": _num(row.get("impulse_move_pct")),
        "impulse_retracement_pct": _num(row.get("impulse_retracement_pct")),
        "impulse_max_retracement_pct": _num(row.get("impulse_max_retracement_pct")),
        "impulse_bounce_recovery_pct": _num(row.get("impulse_bounce_recovery_pct")),
        "pullback_volume_ratio": _num(row.get("pullback_volume_ratio")),
        "bounce_count": _num(row.get("bounce_count")),
        "last_bounce_pct": _num(row.get("last_bounce_pct")),
        "bounce_decay_ratio": _num(row.get("bounce_decay_ratio")),
        "bounce_volume_decay_ratio": _num(row.get("bounce_volume_decay_ratio")),
        "lower_high_streak": _num(row.get("lower_high_streak")),
        "higher_low_streak": _num(row.get("higher_low_streak")),
        "sequence_health_score": _num(row.get("sequence_health_score")),
        "current_pullback_pct": _num(row.get("current_pullback_pct")),
        "ongoing_bounce_pct": _num(row.get("ongoing_bounce_pct")),
        "bounce_leg_code": _num(row.get("bounce_leg_code")),
        "reference_peak_pct_above_dip": _num(row.get("reference_peak_pct_above_dip")),
        "pullback_quality_score": _num(row.get("pullback_quality_score")),
        "vwap_hold_ratio_10": _num(row.get("vwap_hold_ratio_10")),
        "vwap_reclaim": _num(row.get("vwap_reclaim")),
        "vwap_rejection": _num(row.get("vwap_rejection")),
        "vwap_state_code": _num(row.get("vwap_state_code")),
        "vwap_crosses_10": _num(row.get("vwap_crosses_10")),
        "volume_acceleration_ratio": _num(row.get("volume_acceleration_ratio")),
        "volume_accelerating": _num(row.get("volume_accelerating")),
        "volume_contracting": _num(row.get("volume_contracting")),
        "breakout_recent": _num(row.get("breakout_recent")),
        "breakout_holding": _num(row.get("breakout_holding")),
        "failed_breakout": _num(row.get("failed_breakout")),
        "breakout_extension_pct": _num(row.get("breakout_extension_pct")),
        "breakout_bars_since": _num(row.get("breakout_bars_since")),
        "stair_step_count": _num(row.get("stair_step_count")),
        "stair_last_step_pct": _num(row.get("stair_last_step_pct")),
        "stair_step_acceleration_ratio": _num(row.get("stair_step_acceleration_ratio")),
        "stair_plateau_days": _num(row.get("stair_plateau_days")),
        "stair_plateau_range_pct": _num(row.get("stair_plateau_range_pct")),
        "stair_plateau_retention_pct": _num(row.get("stair_plateau_retention_pct")),
        "stair_plateau_volume_ratio": _num(row.get("stair_plateau_volume_ratio")),
        "stair_higher_plateau_count": _num(row.get("stair_higher_plateau_count")),
        "stair_structure_score": _num(row.get("stair_structure_score")),
        "stair_reaccelerating": _num(row.get("stair_reaccelerating")),
        "stair_reacceleration_developing": _num(row.get("stair_reacceleration_developing")),
        "stair_breakdown": _num(row.get("stair_breakdown")),
        "stair_breakdown_confirmed": _num(row.get("stair_breakdown_confirmed")),
        "stair_breakdown_developing": _num(row.get("stair_breakdown_developing")),
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


def _consolidated_observation_source(row, payload):
    source = str(
        row.get("observation_source")
        or payload.get("source")
        or "live_scan"
    ).lower()

    if source == "historical_replay":
        markers = " ".join(
            str(value or "")
            for value in (
                row.get("liquidity_source"),
                row.get("live_intraday_source"),
                (payload.get("replay") or {}).get("historical_feed"),
                (payload.get("replay") or {}).get("intraday_source"),
                payload.get("historical_feed"),
            )
        ).lower()
        if "iex" in markers or "mixed" in markers:
            return False
        return (
            "tradier" in markers
            or "sip" in markers
            or "consolidated" in markers
        )

    provider = str(row.get("market_provider") or "").lower()
    feed = str(row.get("live_feed") or "").lower()
    quote_source = str(row.get("live_quote_source") or "").lower()
    combined = " ".join((provider, feed, quote_source))
    if "iex" in combined or "mixed" in combined:
        return False
    return (
        provider == "tradier"
        or "tradier" in combined
        or "sip" in combined
        or "consolidated" in combined
    )


def _extract_observations(payload):
    if not isinstance(payload, dict):
        return []

    out = []
    for row in payload.get("observations") or []:
        if row.get("feature_version") != CURRENT_FEATURE_VERSION:
            continue
        if not _consolidated_observation_source(row, payload):
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
                "symbol": str(row.get("symbol") or "").upper().strip(),
                "entry_price": _num(
                    row.get("entry_price")
                    if row.get("entry_price") is not None
                    else row.get("price")
                ),
                "observation_source": (
                    row.get("observation_source")
                    or payload.get("source")
                    or "live_scan"
                ),
                "behavior_feature_version": (
                    row.get("behavior_feature_version")
                    or payload.get("behavior_feature_version")
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
        "training_data_requirement": (
            "consolidated live + consolidated historical only"
        ),
        "historical_replay_samples": source_counts.get("historical_replay", 0),
        "live_samples": sum(
            count
            for source, count in source_counts.items()
            if source != "historical_replay"
        ),
    }


def independent_confirmation_rows(
    rows,
    min_gap_seconds=LIVE_CONFIRMATION_MIN_GAP_SECONDS,
):
    """De-correlate same-ticker confirmation rows by the target horizon.

    The Scanner target is 60-minute continuation. Two rows for the same ticker
    inside the same 60-minute window share much of the same future price path
    and must not count as independent confirmation evidence.
    """
    selected = []
    last_timestamp_by_symbol = {}
    ordered = sorted(
        rows,
        key=lambda row: float(row.get("timestamp") or 0.0),
    )
    for row in ordered:
        symbol = str(row.get("symbol") or "").upper().strip()
        timestamp = _num(row.get("timestamp"))
        if not symbol or timestamp is None or timestamp <= 0:
            continue
        last = last_timestamp_by_symbol.get(symbol)
        if last is not None and timestamp - last < min_gap_seconds:
            continue
        selected.append(row)
        last_timestamp_by_symbol[symbol] = timestamp
    return selected


def _training_fingerprint(rows):
    digest = hashlib.sha256()
    digest.update(MODEL_VERSION.encode("utf-8"))
    digest.update(CURRENT_FEATURE_VERSION.encode("utf-8"))
    for row in rows:
        digest.update(
            (
                str(row.get("observation_id"))
                + "|"
                + str(row.get("timestamp"))
                + "|"
                + str(row.get("label"))
                + "\n"
            ).encode("utf-8")
        )
    return digest.hexdigest()


def _load_cached_model(fingerprint):
    if not MODEL_CACHE_PATH.exists() or not MODEL_CACHE_META_PATH.exists():
        return None, None
    try:
        meta = json.loads(
            MODEL_CACHE_META_PATH.read_text(encoding="utf-8")
        )
        if meta.get("training_fingerprint") != fingerprint:
            return None, None
        if meta.get("model_version") != MODEL_VERSION:
            return None, None
        if meta.get("feature_version") != CURRENT_FEATURE_VERSION:
            return None, None

        import xgboost as xgb

        model = xgb.Booster()
        model.load_model(str(MODEL_CACHE_PATH))
        cached_meta = dict(meta.get("validation_meta") or {})
        cached_meta["model_cache_hit"] = True
        return model, cached_meta
    except Exception:
        return None, None


def _save_cached_model(model, fingerprint, meta):
    if model is None or not meta.get("historical_validated"):
        return
    try:
        MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        model.save_model(str(MODEL_CACHE_PATH))
        payload = {
            "model_version": MODEL_VERSION,
            "feature_version": CURRENT_FEATURE_VERSION,
            "training_fingerprint": fingerprint,
            "validation_meta": {
                key: value
                for key, value in meta.items()
                if isinstance(
                    value,
                    (str, int, float, bool, list, dict, type(None)),
                )
            },
        }
        MODEL_CACHE_META_PATH.write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


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
    replay_days = sorted(
        {
            row.get("trading_date")
            for row in replay_rows
            if row.get("trading_date")
        }
    )
    replay_end_day = replay_days[-1] if replay_days else None
    # A live-confirmation holdout must occur strictly after the replay period.
    # Otherwise the same symbol/day can appear in replay training and the live
    # holdout, making confirmation look stronger than it really is.
    live_confirmation_rows_raw = [
        row
        for row in live_rows
        if (
            not replay_end_day
            or (
                row.get("trading_date")
                and row["trading_date"] > replay_end_day
            )
        )
    ]
    live_confirmation_rows = independent_confirmation_rows(
        live_confirmation_rows_raw
    )
    live_confirmation_days = sorted(
        {
            row.get("trading_date")
            for row in live_confirmation_rows
            if row.get("trading_date")
        }
    )
    live_confirmation_symbols = sorted(
        {
            row.get("symbol")
            for row in live_confirmation_rows
            if row.get("symbol")
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
        "live_confirmation_raw_samples": len(live_confirmation_rows_raw),
        "live_confirmation_samples": len(live_confirmation_rows),
        "live_confirmation_days": len(live_confirmation_days),
        "live_confirmation_symbols": len(live_confirmation_symbols),
        "live_confirmation_min_gap_seconds": LIVE_CONFIRMATION_MIN_GAP_SECONDS,
        "live_confirmation_after_replay_day": replay_end_day,
        "live_confirmation_min_samples": MIN_LIVE_CONFIRMATION_SAMPLES,
        "live_confirmation_min_days": MIN_LIVE_CONFIRMATION_DAYS,
        "live_confirmation_min_symbols": MIN_LIVE_CONFIRMATION_SYMBOLS,
    }

    # When replay evidence exists, keep the historical validation pool
    # completely separate from the later live confirmation pool. Otherwise a
    # live row could help earn the first validation badge and then be reused as
    # supposedly independent confirmation.
    #
    # Replay snapshots are produced much more frequently than the 60-minute
    # target horizon. Consecutive observations for the same ticker therefore
    # share most of the same future price path and are not independent evidence.
    # Validate on one effective observation per ticker per target horizon, just
    # like the later live-confirmation gate.
    validation_rows_raw = replay_rows if replay_rows else rows
    validation_rows = independent_confirmation_rows(validation_rows_raw)
    validation_samples = len(validation_rows)
    validation_positives = sum(row["label"] for row in validation_rows)
    validation_negatives = validation_samples - validation_positives
    validation_scans = sorted({
        row.get("scan_id") or row.get("scan_time_et")
        for row in validation_rows
    })
    validation_days = sorted({
        row.get("trading_date")
        for row in validation_rows
        if row.get("trading_date")
    })
    base_meta.update(
        {
            "historical_validation_source": (
                "historical_replay" if replay_rows else "live_only"
            ),
            "historical_validation_raw_samples": len(validation_rows_raw),
            "historical_validation_samples": validation_samples,
            "historical_validation_min_gap_seconds": LIVE_CONFIRMATION_MIN_GAP_SECONDS,
            "historical_validation_unique_scans": len(validation_scans),
            "historical_validation_trading_days": len(validation_days),
            "historical_validation_positives": validation_positives,
            "historical_validation_negatives": validation_negatives,
        }
    )

    if (
        validation_samples < MIN_SAMPLES
        or len(validation_scans) < MIN_UNIQUE_SCANS
        or len(validation_days) < MIN_TRADING_DAYS
        or validation_positives < MIN_CLASS_COUNT
        or validation_negatives < MIN_CLASS_COUNT
    ):
        return None, base_meta

    # Fit the served model on the same de-correlated evidence unit used for
    # integrity validation. Dense 10-minute replay snapshots must not dominate
    # learning when the target itself spans 60 minutes.
    fit_rows = independent_confirmation_rows(rows)
    X, y = _matrix(fit_rows, np)
    validation_X, validation_y = _matrix(validation_rows, np)
    base_meta["effective_training_samples"] = len(fit_rows)
    base_meta["effective_training_min_gap_seconds"] = LIVE_CONFIRMATION_MIN_GAP_SECONDS

    # Validate on whole trading days, not intraday timestamps. The target uses
    # the price 60 minutes after each observation, so an intraday cutoff can
    # leak outcome information from the validation period into training labels.
    # Whole-day boundaries provide a natural embargo because replay targets are
    # resolved within the same regular-market session.

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
            len(validation_days) - 1,
            max(0, int(len(validation_days) * train_frac) - 1),
        )
        val_pos = min(
            len(validation_days) - 1,
            max(0, int(len(validation_days) * val_frac) - 1),
        )
        train_cut_day = validation_days[train_pos]
        val_cut_day = validation_days[val_pos]
        if val_cut_day <= train_cut_day:
            continue
        train_idx = [
            i
            for i, row in enumerate(validation_rows)
            if row.get("trading_date")
            and row["trading_date"] <= train_cut_day
        ]
        val_idx = [
            i
            for i, row in enumerate(validation_rows)
            if row.get("trading_date")
            and train_cut_day < row["trading_date"] <= val_cut_day
        ]
        if len(train_idx) < 100 or len(val_idx) < 20:
            continue

        ytr = validation_y[train_idx]
        yv = validation_y[val_idx]
        if (
            len(set(ytr.tolist())) < 2
            or len(set(yv.tolist())) < 2
        ):
            continue

        model = xgb.train(
            _params(),
            xgb.DMatrix(
                validation_X[train_idx],
                label=ytr,
                feature_names=FEATURES,
            ),
            num_boost_round=120,
            verbose_eval=False,
        )
        probs = model.predict(
            xgb.DMatrix(
                validation_X[val_idx],
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
        "validation_split_unit": "trading_day",
        "validation_target_horizon_minutes": 60,
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
        live_positives = sum(
            row["label"] for row in live_confirmation_rows
        )
        live_negatives = len(live_confirmation_rows) - live_positives
        enough_live = bool(
            len(live_confirmation_rows) >= MIN_LIVE_CONFIRMATION_SAMPLES
            and len(live_confirmation_days) >= MIN_LIVE_CONFIRMATION_DAYS
            and len(live_confirmation_symbols) >= MIN_LIVE_CONFIRMATION_SYMBOLS
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
            live_X, live_y = _matrix(live_confirmation_rows, np)
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
    fingerprint = _training_fingerprint(training_rows)
    model, meta = _load_cached_model(fingerprint)
    if model is None or meta is None:
        model, meta = _validation_and_model(
            training_rows
        )
        meta["model_cache_hit"] = False
        _save_cached_model(
            model,
            fingerprint,
            meta,
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
