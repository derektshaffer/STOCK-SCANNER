import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from timeframe_targets import (
    SWING_HORIZON_SESSIONS,
    resolve_swing_path_from_bars,
)


LOG_PATH = Path(os.environ.get("ANALYZER_PREDICTION_LOG", "analysis_logs/analyzer_predictions.json"))
BUCKET_MINUTES = 5
ET = ZoneInfo("America/New_York")
ANALYZER_FEATURE_VERSION = "analyzer-features-v6-sequence-regimes"
DECISION_SCORE_VERSION = "decision-v2.6-sequence-regimes"
TIMEFRAME_SCORE_VERSION = "timeframe-fit-v1"

GITHUB_TOKEN = (
    os.environ.get("ANALYZER_GITHUB_TOKEN", "").strip()
    or os.environ.get("GITHUB_TOKEN", "").strip()
)
GITHUB_REPO = os.environ.get(
    "ANALYZER_GITHUB_REPO", "derektshaffer/STOCK-SCANNER"
).strip()
GITHUB_BRANCH = os.environ.get("ANALYZER_GITHUB_BRANCH", "main").strip() or "main"
REMOTE_DIR = os.environ.get("ANALYZER_OUTCOME_DIR", "analyzer_outcomes").strip() or "analyzer_outcomes"
REMOTE_SYNC_SECONDS = max(
    300, int(os.environ.get("ANALYZER_REMOTE_SYNC_SECONDS", "900") or 900)
)
_REMOTE_STATE = {"loaded": False, "last_sync": 0.0, "last_error": None, "last_path": None}


def _num(value):
    try:
        return float(value)
    except Exception:
        return None


def _parse_dt(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _github_headers():
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "stock-analyzer-prediction-tracker/2.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


def _github_contents_url(path):
    owner_repo = GITHUB_REPO.strip("/")
    encoded = "/".join(urllib.parse.quote(part, safe="") for part in str(path).split("/"))
    return f"https://api.github.com/repos/{owner_repo}/contents/{encoded}"


def _github_get_file(path, require_token=False):
    if require_token and not GITHUB_TOKEN:
        return None, None
    url = _github_contents_url(path) + "?" + urllib.parse.urlencode({"ref": GITHUB_BRANCH})
    req = urllib.request.Request(url, headers=_github_headers())
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, None
        raise
    content = payload.get("content")
    if payload.get("encoding") == "base64" and content:
        raw = base64.b64decode("".join(str(content).split()))
        return json.loads(raw.decode("utf-8")), payload.get("sha")
    return None, payload.get("sha")


def _github_put_json(path, payload, sha=None):
    if not GITHUB_TOKEN:
        return False
    body = {
        "message": f"Sync Analyzer predictions {datetime.now(ET).date().isoformat()}",
        "content": base64.b64encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        body["sha"] = sha
    req = urllib.request.Request(
        _github_contents_url(path),
        data=json.dumps(body).encode("utf-8"),
        headers={**_github_headers(), "Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=15):
        return True


def _remote_day_path(day):
    return f"{REMOTE_DIR}/predictions_{day.isoformat()}.json"


def _row_day(row):
    dt = _parse_dt(row.get("timestamp"))
    return dt.astimezone(ET).date() if dt else None


def _merge_rows(*groups):
    merged = {}
    for rows in groups:
        for row in rows or []:
            key = row.get("bucket_key") or row.get("id")
            if not key:
                continue
            existing = merged.get(key)
            if not existing:
                merged[key] = row
                continue
            # Prefer whichever copy has more resolved outcome fields.
            old_count = len((existing.get("outcomes") or {}))
            new_count = len((row.get("outcomes") or {}))
            merged[key] = row if new_count >= old_count else existing
    return sorted(
        merged.values(),
        key=lambda row: str(row.get("timestamp") or ""),
    )


def _load_remote_today():
    if not GITHUB_TOKEN:
        return []
    today = datetime.now(ET).date()
    try:
        payload, _sha = _github_get_file(_remote_day_path(today), require_token=True)
        if isinstance(payload, list):
            _REMOTE_STATE["last_error"] = None
            return payload
    except Exception as exc:
        _REMOTE_STATE["last_error"] = str(exc)[:180]
    return []


def _sync_remote(rows, force=False):
    if not GITHUB_TOKEN:
        return {"enabled": False, "synced": False, "reason": "missing_token"}
    now_ts = time.time()
    if (
        not force
        and _REMOTE_STATE["last_sync"]
        and now_ts - float(_REMOTE_STATE["last_sync"]) < REMOTE_SYNC_SECONDS
    ):
        return {"enabled": True, "synced": False, "reason": "interval"}

    today = datetime.now(ET).date()
    daily_rows = [row for row in rows if _row_day(row) == today]
    path = _remote_day_path(today)
    try:
        remote, sha = _github_get_file(path, require_token=True)
        merged = _merge_rows(remote if isinstance(remote, list) else [], daily_rows)
        _github_put_json(path, merged, sha=sha)
        _REMOTE_STATE.update(
            {
                "last_sync": now_ts,
                "last_error": None,
                "last_path": path,
            }
        )
        return {"enabled": True, "synced": True, "path": path, "count": len(merged)}
    except Exception as exc:
        _REMOTE_STATE["last_error"] = str(exc)[:180]
        return {
            "enabled": True,
            "synced": False,
            "reason": "error",
            "error": _REMOTE_STATE["last_error"],
        }


def _load_durable_calibration():
    path = f"{REMOTE_DIR}/calibration.json"
    try:
        payload, _sha = _github_get_file(path, require_token=False)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _load():
    local = []
    try:
        if LOG_PATH.exists():
            payload = json.loads(LOG_PATH.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                local = payload
    except Exception:
        local = []

    if not _REMOTE_STATE["loaded"]:
        _REMOTE_STATE["loaded"] = True
        remote = _load_remote_today()
        if remote:
            local = _merge_rows(local, remote)
            try:
                LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
                LOG_PATH.write_text(
                    json.dumps(local[-5000:], separators=(",", ":")),
                    encoding="utf-8",
                )
            except Exception:
                pass
    return local


def _save(rows, force_remote=False):
    local_ok = False
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = LOG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows[-5000:], separators=(",", ":")), encoding="utf-8")
        tmp.replace(LOG_PATH)
        local_ok = True
    except Exception:
        local_ok = False

    sync = _sync_remote(rows, force=force_remote) if local_ok else {
        "enabled": bool(GITHUB_TOKEN),
        "synced": False,
        "reason": "local_save_failed",
    }
    _REMOTE_STATE["last_sync_result"] = sync
    return local_ok


def _bucket_key(symbol, when):
    minute = (when.minute // BUCKET_MINUTES) * BUCKET_MINUTES
    bucket = when.replace(minute=minute, second=0, microsecond=0)
    return f"{symbol}:{bucket.isoformat()}"


def record_prediction(metrics, now=None):
    """Record one Analyzer prediction per ticker per five-minute bucket."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    symbol = str(metrics.get("symbol") or "").upper().strip()
    if not symbol:
        return {"recorded": False, "reason": "missing_symbol"}

    rows = _load()
    key = _bucket_key(symbol, now)
    if any(row.get("bucket_key") == key for row in rows[-200:]):
        return {"recorded": False, "reason": "already_recorded", "count": len(rows)}

    plan = metrics.get("trade_plan") or {}
    selected = plan.get("selected") or {}
    ml = metrics.get("ml_prediction") or {}
    hist = metrics.get("historical_setup") or {}
    v2 = metrics.get("decision_v2") or {}
    turnover = v2.get("turnover_context") or {}
    catalyst = v2.get("catalyst_strength") or {}
    market = v2.get("market_context") or {}
    fundamental = v2.get("fundamental_context") or {}
    stream = v2.get("live_stream_status") or {}
    sequence = metrics.get("bounce_sequence") or {}
    stair = metrics.get("stair_step") or {}
    repeat_plan = plan.get("repeat_bounce") or {}
    models = ml.get("models") or {}
    scenarios = (v2.get("full_spectrum") or {}).get("scenarios") or {}
    timeframe = v2.get("timeframe_analysis") or {}
    daily_trend = timeframe.get("daily_trend") or {}

    row = {
        "id": f"{key}:{len(rows)+1}",
        "bucket_key": key,
        "symbol": symbol,
        "timestamp": now.isoformat(),
        "feature_version": (
            metrics.get("feature_version")
            or ANALYZER_FEATURE_VERSION
        ),
        "market_provider": (
            metrics.get("market_provider")
            or metrics.get("live_provider")
            or "alpaca"
        ),
        "decision_score_version": (
            v2.get("version")
            or "legacy-decision-score"
        ),
        "price": _num(metrics.get("price")),
        "day_pct": _num(metrics.get("day_pct")),
        "vwap": _num(metrics.get("vwap")),
        "vwap_position": metrics.get("vwap_position"),
        "vwap_extension_pct": _num(metrics.get("vwap_extension_pct")),
        "spread_pct": _num(metrics.get("spread_pct")),
        "volume": _num(metrics.get("volume")),
        "session_volume": _num(metrics.get("session_volume")),
        "volume_pace": _num(metrics.get("volume_pace")),
        "live_feed": metrics.get("live_feed"),
        "stream_status": stream.get("status"),
        "stream_feed": stream.get("feed"),
        "setup_score": _num(metrics.get("score")),
        "plan_confidence": _num(plan.get("confidence")),
        "plan_status": plan.get("status"),
        "plan_action": plan.get("action"),
        "preferred_plan": plan.get("preferred_plan"),
        "potential_score": _num(v2.get("potential_score")),
        "entry_readiness": _num(v2.get("entry_readiness")),
        "evidence_strength": _num(v2.get("evidence_strength")),
        # Timeframe-fit scores are logged separately from the live trade-plan
        # scores so they can be validated before they are ever allowed to
        # influence production decisions.
        "timeframe_score_version": timeframe.get("version"),
        "timeframe_best_fit": timeframe.get("best_fit"),
        "timeframe_intraday_score": _num((timeframe.get("scores") or {}).get("intraday")),
        "timeframe_swing_score": _num((timeframe.get("scores") or {}).get("swing")),
        "timeframe_long_term_score": _num((timeframe.get("scores") or {}).get("long_term")),
        "timeframe_fundamental_quality_score": _num(timeframe.get("fundamental_quality_score")),
        "timeframe_fundamental_coverage_count": int(timeframe.get("fundamental_coverage_count") or 0),
        "timeframe_trend_score": _num(daily_trend.get("trend_score")),
        "timeframe_return_20d_at_signal_pct": _num(daily_trend.get("return_20d_pct")),
        "timeframe_return_60d_at_signal_pct": _num(daily_trend.get("return_60d_pct")),
        "timeframe_return_120d_at_signal_pct": _num(daily_trend.get("return_120d_pct")),
        "entry_low": _num(selected.get("entry_low")),
        "entry_high": _num(selected.get("entry_high")),
        "target1": _num(selected.get("target1")),
        "stop": _num(selected.get("stop")),
        "ml_edge": _num(ml.get("ml_edge_score")),
        "ml_same_ticker_edge": _num(ml.get("same_ticker_edge_score")),
        "ml_hybrid_edge": _num(ml.get("hybrid_ml_edge_score")),
        "ml_edge_method": ml.get("edge_method"),
        "ml_validated_models": int(ml.get("validated_edge_model_count") or 0),
        "peer_ml_probability_pct": _num(
            (ml.get("peer_model") or {}).get("probability_pct")
        ),
        "peer_ml_edge": _num(
            (ml.get("peer_model") or {}).get("peer_edge_score")
        ),
        "peer_ml_validated": bool(
            (ml.get("peer_model") or {}).get("validated")
        ),
        "peer_ml_samples": int(
            (ml.get("peer_model") or {}).get("samples") or 0
        ),
        "peer_ml_symbols": int(
            (ml.get("peer_model") or {}).get("peer_symbols") or 0
        ),
        "peer_ml_blend_weight_pct": int(
            ml.get("peer_blend_weight_pct") or 0
        ),
        # Bounce-specific live state and dedicated plan geometry. These fields
        # let us later test whether Bounce #2/#3 entries actually behaved as
        # predicted instead of only evaluating the generic Analyzer signal.
        "bounce_sequence_detected": bool(sequence.get("detected")),
        "bounce_count": int(sequence.get("completed_bounces") or 0),
        "next_bounce_number": int(sequence.get("next_bounce_number") or 0),
        "bounce_current_leg": sequence.get("current_leg"),
        "bounce_sequence_state": sequence.get("sequence_state"),
        "bounce_sequence_health": _num(sequence.get("sequence_health_score")),
        "bounce1_pct": _num(sequence.get("bounce1_pct")),
        "bounce2_pct": _num(sequence.get("bounce2_pct")),
        "bounce3_pct": _num(sequence.get("bounce3_pct")),
        "bounce_decay_ratio": _num(sequence.get("bounce_decay_ratio")),
        "bounce_volume_decay_ratio": _num(sequence.get("bounce_volume_decay_ratio")),
        "bounce_lower_high_streak": int(sequence.get("lower_high_streak") or 0),
        "bounce_higher_low_streak": int(sequence.get("higher_low_streak") or 0),
        "bounce_current_dip_low": _num(sequence.get("current_dip_low")),
        "bounce_reference_peak": _num(sequence.get("reference_peak")),
        "repeat_bounce_plan_available": bool(repeat_plan),
        "repeat_bounce_plan_number": int(repeat_plan.get("bounce_number") or 0),
        "repeat_bounce_entry_low": _num(repeat_plan.get("entry_low")),
        "repeat_bounce_entry_high": _num(repeat_plan.get("entry_high")),
        "repeat_bounce_confirmation": _num(repeat_plan.get("confirmation_level")),
        "repeat_bounce_target1": _num(repeat_plan.get("target1")),
        "repeat_bounce_target2": _num(repeat_plan.get("target2")),
        "repeat_bounce_stop": _num(repeat_plan.get("stop")),
        "repeat_bounce_rr": _num(repeat_plan.get("risk_reward")),
        "repeat_bounce_expected_pct": _num(repeat_plan.get("expected_bounce_pct")),
        "repeat_bounce_historical_rate_pct": _num(repeat_plan.get("historical_bounce_rate_pct")),
        "repeat_bounce_30_probability_pct": _num((models.get("repeat_bounce_30") or {}).get("probability_pct")),
        "repeat_bounce_30_validated": bool((models.get("repeat_bounce_30") or {}).get("validated")),
        "new_high_60_probability_pct": _num((models.get("new_high_60") or {}).get("probability_pct")),
        "new_high_60_validated": bool((models.get("new_high_60") or {}).get("validated")),
        "post_bounce_failure_60_probability_pct": _num((models.get("post_bounce_failure_60") or {}).get("probability_pct")),
        "post_bounce_failure_60_validated": bool((models.get("post_bounce_failure_60") or {}).get("validated")),
        # Multi-session stair-step / plateau state.
        "stair_step_detected": bool(stair.get("detected")),
        "stair_step_state": stair.get("state"),
        "stair_step_count": int(stair.get("step_count") or 0),
        "stair_structure_score": _num(stair.get("structure_score")),
        "stair_last_step_pct": _num(stair.get("last_step_pct")),
        "stair_plateau_days": int(stair.get("current_plateau_days") or 0),
        "stair_plateau_range_pct": _num(stair.get("current_plateau_range_pct")),
        "stair_plateau_retention_pct": _num(stair.get("current_plateau_retention_pct")),
        "stair_plateau_volume_ratio": _num(stair.get("plateau_volume_ratio")),
        "stair_reaccelerating": bool(stair.get("reaccelerating")),
        "stair_breakdown": bool(stair.get("breakdown")),
        "stair_reacceleration_60_probability_pct": _num((models.get("stair_reacceleration_60") or {}).get("probability_pct")),
        "stair_reacceleration_60_validated": bool((models.get("stair_reacceleration_60") or {}).get("validated")),
        "scenario_continuation_weight": _num((scenarios.get("continuation") or {}).get("relative_weight_pct")),
        "scenario_pullback_bounce_weight": _num((scenarios.get("pullback_bounce") or {}).get("relative_weight_pct")),
        "scenario_stair_reacceleration_weight": _num((scenarios.get("stair_reacceleration") or {}).get("relative_weight_pct")),
        "scenario_reversal_failure_weight": _num((scenarios.get("reversal_failure") or {}).get("relative_weight_pct")),
        "scenario_sideways_chop_weight": _num((scenarios.get("sideways_chop") or {}).get("relative_weight_pct")),
        "historical_bias": hist.get("bias_label"),
        "historical_bias_score": _num(hist.get("bias_score")),
        "historical_samples": int(hist.get("sample_count") or 0),
        "float_shares": _num(turnover.get("float_shares")),
        "float_turnover": _num(turnover.get("float_turnover")),
        "shares_outstanding": _num(turnover.get("shares_outstanding")),
        "shares_outstanding_turnover": _num(
            turnover.get("shares_outstanding_turnover")
        ),
        "catalyst_label": catalyst.get("label"),
        "catalyst_score": _num(catalyst.get("score")),
        "market_context": market.get("label"),
        "sector_etf": market.get("sector_etf"),
        "sector_move_pct": _num(market.get("sector_move_pct")),
        "dilution_risk": fundamental.get("dilution_risk"),
        "outcomes": {},
    }
    rows.append(row)
    force_remote = not bool(_REMOTE_STATE.get("last_sync"))
    ok = _save(rows, force_remote=force_remote)
    sync = _REMOTE_STATE.get("last_sync_result") or {}
    return {
        "recorded": ok,
        "count": len(rows),
        "path": str(LOG_PATH),
        "durable_sync": sync,
    }


def _bar_dt(bar):
    return _parse_dt(bar.get("t"))


def _closest_close(bars, target_dt, tolerance_minutes=12):
    best = None
    best_delta = None
    for bar in bars:
        dt = _bar_dt(bar)
        close = _num(bar.get("c"))
        if dt is None or close is None:
            continue
        delta = abs((dt - target_dt).total_seconds())
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best = close
    if best_delta is None or best_delta > tolerance_minutes * 60:
        return None
    return best


def _first_touch(bars, target, stop):
    if target is None or stop is None:
        return None
    for bar in bars:
        high = _num(bar.get("h"))
        low = _num(bar.get("l"))
        if high is None or low is None:
            continue
        hit_target = high >= target
        hit_stop = low <= stop
        if hit_target and hit_stop:
            return "ambiguous"
        if hit_target:
            return "target"
        if hit_stop:
            return "stop"
    return None


def _window_excursions(bars, created, price, minutes):
    if created is None or price is None or price <= 0:
        return None, None
    end = created + timedelta(minutes=minutes)
    window = []
    for bar in bars:
        dt = _bar_dt(bar)
        if dt is None or dt < created or dt > end:
            continue
        window.append(bar)
    highs = [_num(b.get("h")) for b in window]
    lows = [_num(b.get("l")) for b in window]
    highs = [v for v in highs if v is not None]
    lows = [v for v in lows if v is not None]
    mfe = ((max(highs) / price - 1.0) * 100.0) if highs else None
    mae = ((min(lows) / price - 1.0) * 100.0) if lows else None
    return mfe, mae



def _daily_bar_date(bar):
    dt = _bar_dt(bar)
    if dt is None:
        return None
    return dt.astimezone(ET).date()


def _resolve_trading_day_returns(row, daily_bars):
    """Resolve close returns plus the shared five-session Swing path target."""
    created = _parse_dt(row.get("timestamp"))
    price = _num(row.get("price"))
    if created is None or price is None or price <= 0:
        return False
    signal_day = created.astimezone(ET).date()
    future = []
    for bar in daily_bars or []:
        bar_day = _daily_bar_date(bar)
        close = _num(bar.get("c"))
        if bar_day is not None and bar_day > signal_day and close is not None and close > 0:
            future.append((bar_day, bar))
    future.sort(key=lambda item: item[0])
    outcomes = row.setdefault("outcomes", {})
    changed = False
    for sessions in (1, 3, 5, 20, 60):
        key = f"return_{sessions}d_pct"
        if key in outcomes or len(future) < sessions:
            continue
        close = _num(future[sessions - 1][1].get("c"))
        if close is None:
            continue
        outcomes[key] = round((close / price - 1.0) * 100.0, 3)
        outcomes[f"resolved_{sessions}d"] = True
        changed = True

    if (
        row.get("timeframe_score_version") == TIMEFRAME_SCORE_VERSION
        and "swing_first_event_5d" not in outcomes
        and len(future) >= SWING_HORIZON_SESSIONS
    ):
        path = resolve_swing_path_from_bars(
            price,
            [bar for _day, bar in future[:SWING_HORIZON_SESSIONS]],
        )
        for key, value in path.items():
            outcomes[key] = value
        if path:
            changed = True
    return changed


def resolve_symbol_predictions(sa, symbol, now=None, current_metrics=None):
    """Resolve older predictions opportunistically using delayed SIP bars.

    This intentionally waits for consolidated delayed data rather than scoring
    outcomes from a potentially incomplete single-exchange live feed.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    symbol = str(symbol or "").upper().strip()
    rows = _load()
    symbol_rows = [
        row for row in rows
        if row.get("symbol") == symbol
        and _parse_dt(row.get("timestamp"))
    ][-80:]
    pending = [
        row for row in symbol_rows
        if not bool((row.get("outcomes") or {}).get("resolved_60m"))
    ][-40:]
    timeframe_pending = [
        row for row in symbol_rows
        if row.get("timeframe_score_version") == TIMEFRAME_SCORE_VERSION
        and (
            any(
                (row.get("outcomes") or {}).get(
                    f"return_{sessions}d_pct"
                ) is None
                for sessions in (1, 3, 5, 20, 60)
            )
            or "swing_first_event_5d" not in (row.get("outcomes") or {})
        )
    ][-40:]
    if not pending and not timeframe_pending:
        return tracker_summary(rows, symbol, current_metrics=current_metrics)

    safe_end = now - timedelta(minutes=16)
    bars = []
    if pending:
        earliest = min(_parse_dt(row["timestamp"]) for row in pending)
        if safe_end > earliest:
            try:
                bars, _source = sa.try_sip_delayed_bars(
                    symbol,
                    "5Min",
                    earliest - timedelta(minutes=5),
                    safe_end,
                    10000,
                )
            except Exception:
                bars = []

    changed = False
    for row in pending:
        created = _parse_dt(row.get("timestamp"))
        price = _num(row.get("price"))
        if created is None or price is None:
            continue
        outcomes = row.setdefault("outcomes", {})
        future = [b for b in bars if (_bar_dt(b) or created) >= created]

        for mins in (15, 30, 60):
            key = f"return_{mins}m_pct"
            if key in outcomes or safe_end < created + timedelta(minutes=mins):
                continue
            close = _closest_close(future, created + timedelta(minutes=mins))
            if close is not None:
                outcomes[key] = round((close / price - 1.0) * 100.0, 3)
                if mins == 60:
                    outcomes["resolved_60m"] = True
                changed = True

        target = _num(row.get("target1"))
        stop = _num(row.get("stop"))
        if "target1_first_touch" not in outcomes and future:
            touch = _first_touch(future, target, stop)
            if touch:
                outcomes["target1_first_touch"] = touch
                changed = True

        # Dedicated later-bounce outcome path.
        if row.get("repeat_bounce_plan_available") and future:
            rb_target1 = _num(row.get("repeat_bounce_target1"))
            rb_target2 = _num(row.get("repeat_bounce_target2"))
            rb_stop = _num(row.get("repeat_bounce_stop"))
            if "repeat_bounce_target1_first_touch" not in outcomes:
                touch = _first_touch(future, rb_target1, rb_stop)
                if touch:
                    outcomes["repeat_bounce_target1_first_touch"] = touch
                    changed = True
            if "repeat_bounce_target2_first_touch" not in outcomes:
                touch2 = _first_touch(future, rb_target2, rb_stop)
                if touch2:
                    outcomes["repeat_bounce_target2_first_touch"] = touch2
                    changed = True

            for mins in (30, 60):
                mfe_key=f"repeat_bounce_mfe_{mins}m_pct"
                mae_key=f"repeat_bounce_mae_{mins}m_pct"
                if safe_end >= created + timedelta(minutes=mins) and (mfe_key not in outcomes or mae_key not in outcomes):
                    mfe,mae=_window_excursions(future,created,price,mins)
                    if mfe is not None:
                        outcomes[mfe_key]=round(mfe,3)
                        changed=True
                    if mae is not None:
                        outcomes[mae_key]=round(mae,3)
                        changed=True

            ref_peak=_num(row.get("bounce_reference_peak"))
            if (
                "repeat_bounce_reference_peak_reclaimed_60m" not in outcomes
                and ref_peak is not None
                and safe_end >= created + timedelta(minutes=60)
            ):
                within60=[
                    b for b in future
                    if (_bar_dt(b) is not None and _bar_dt(b) <= created + timedelta(minutes=60))
                ]
                highs=[_num(b.get("h")) for b in within60]
                highs=[v for v in highs if v is not None]
                if highs:
                    outcomes["repeat_bounce_reference_peak_reclaimed_60m"]=bool(max(highs)>=ref_peak)
                    changed=True

        # Mature-sequence falloff severity is logged even when no bounce entry
        # is offered, so failure models can be calibrated on live observations.
        if int(row.get("bounce_count") or 0) >= 2:
            for mins in (30,60):
                drop_key=f"post_bounce_max_drop_{mins}m_pct"
                rise_key=f"post_bounce_max_rise_{mins}m_pct"
                if safe_end >= created + timedelta(minutes=mins) and (drop_key not in outcomes or rise_key not in outcomes):
                    mfe,mae=_window_excursions(future,created,price,mins)
                    if mae is not None:
                        outcomes[drop_key]=round(mae,3)
                        changed=True
                    if mfe is not None:
                        outcomes[rise_key]=round(mfe,3)
                        changed=True

    # Resolve the slower timeframe labels opportunistically whenever this
    # ticker is opened again, even after its 60-minute outcome has matured.
    # The nightly scorer remains the durable sweep.
    if timeframe_pending:
        earliest_tf = min(_parse_dt(row["timestamp"]) for row in timeframe_pending)
        try:
            daily_bars, _daily_source = sa.try_sip_delayed_bars(
                symbol,
                "1Day",
                earliest_tf - timedelta(days=3),
                safe_end,
                500,
            )
        except Exception:
            daily_bars = []
        for row in timeframe_pending:
            if _resolve_trading_day_returns(row, daily_bars):
                changed = True

    if changed:
        _save(rows)
    return tracker_summary(rows, symbol, current_metrics=current_metrics)


def signal_lifecycle(symbol, current_metrics=None, rows=None):
    """Summarize how today's first real ENTRY AVAILABLE signal has evolved."""
    rows = rows if rows is not None else _load()
    symbol = str(symbol or "").upper().strip()
    if not symbol:
        return {"status": "no_symbol"}

    now = datetime.now(timezone.utc)
    today_et = now.astimezone(ET).date()
    symbol_rows = []
    for row in rows:
        if row.get("symbol") != symbol:
            continue
        dt = _parse_dt(row.get("timestamp"))
        if dt is None or dt.astimezone(ET).date() != today_et:
            continue
        symbol_rows.append(row)
    symbol_rows.sort(key=lambda row: str(row.get("timestamp") or ""))

    signal = next(
        (row for row in symbol_rows if row.get("plan_status") == "ENTRY AVAILABLE"),
        None,
    )
    if signal is None:
        return {
            "status": "no_entry_signal",
            "message": "No ENTRY AVAILABLE signal has been recorded for this ticker today.",
        }

    signal_dt = _parse_dt(signal.get("timestamp"))
    signal_price = _num(signal.get("price"))
    target1 = _num(signal.get("target1"))
    stop = _num(signal.get("stop"))
    outcomes = signal.get("outcomes") or {}
    first_touch = outcomes.get("target1_first_touch")

    current_metrics = current_metrics or {}
    current_price = _num(current_metrics.get("price"))
    current_plan = current_metrics.get("trade_plan") or {}
    current_status = str(current_plan.get("status") or "")
    current_action = str(current_plan.get("action") or current_status or "Current setup unavailable")

    change_pct = None
    if signal_price and current_price:
        change_pct = round((current_price / signal_price - 1.0) * 100.0, 2)

    thesis_status = "ACTIVE"
    thesis_message = "Original entry thesis is still unresolved."
    if first_touch == "target":
        thesis_status = "SUCCEEDED"
        thesis_message = "Target 1 was reached before the original stop."
    elif first_touch == "stop":
        thesis_status = "FAILED"
        thesis_message = "The original stop was reached before Target 1."
    elif first_touch == "ambiguous":
        thesis_status = "AMBIGUOUS"
        thesis_message = "Target and stop touched within the same bar; order cannot be determined."
    elif current_price is not None and target1 is not None and current_price >= target1:
        thesis_status = "LIKELY SUCCEEDED"
        thesis_message = "Current price is already at/above the original Target 1; final first-touch scoring is pending."
    elif current_price is not None and stop is not None and current_price <= stop:
        thesis_status = "AT RISK"
        thesis_message = "Current price is at/below the original stop; final first-touch scoring is pending."

    if current_status == "ENTRY AVAILABLE":
        current_state = "ENTRY AVAILABLE NOW"
    elif current_status == "WAIT":
        current_state = current_action
    elif current_status == "NO TRADE":
        current_state = current_action
    else:
        current_state = current_action

    return {
        "status": "ok",
        "signal_time": signal_dt.astimezone(ET).isoformat() if signal_dt else signal.get("timestamp"),
        "signal_price": signal_price,
        "signal_action": signal.get("plan_action") or "ENTRY AVAILABLE",
        "signal_preferred_plan": signal.get("preferred_plan"),
        "signal_potential": _num(signal.get("potential_score")),
        "signal_entry_readiness": _num(signal.get("entry_readiness")),
        "signal_evidence": _num(signal.get("evidence_strength")),
        "target1": target1,
        "stop": stop,
        "current_price": current_price,
        "change_since_signal_pct": change_pct,
        "thesis_status": thesis_status,
        "thesis_message": thesis_message,
        "first_touch": first_touch,
        "current_plan_status": current_status,
        "current_state": current_state,
        "observations_since_signal": sum(
            1 for row in symbol_rows
            if str(row.get("timestamp") or "") >= str(signal.get("timestamp") or "")
        ),
    }


def _score_bucket(value):
    value = _num(value)
    if value is None:
        return None
    if value >= 80:
        return "80-100"
    if value >= 65:
        return "65-79"
    if value >= 50:
        return "50-64"
    return "0-49"


def _independent_calibration_rows(rows):
    chosen = {}
    for row in sorted(rows, key=lambda item: str(item.get("timestamp") or "")):
        symbol = str(row.get("symbol") or "").upper().strip()
        dt = _parse_dt(row.get("timestamp"))
        if not symbol or dt is None:
            continue
        key = (symbol, dt.date().isoformat(), dt.hour)
        if key not in chosen:
            chosen[key] = row
    return list(chosen.values())


def _bucket_calibration(rows, score_field):
    groups = {}
    for row in rows:
        ret = _num((row.get("outcomes") or {}).get("return_60m_pct"))
        bucket = _score_bucket(row.get(score_field))
        if ret is None or bucket is None:
            continue
        g = groups.setdefault(bucket, {"n": 0, "wins": 0, "returns": []})
        g["n"] += 1
        g["wins"] += int(ret > 0)
        g["returns"].append(ret)

    out = {}
    for bucket, g in groups.items():
        values = g["returns"]
        out[bucket] = {
            "n": g["n"],
            "higher_60m_rate": round(g["wins"] / g["n"] * 100.0, 1) if g["n"] else None,
            "avg_return_60m_pct": round(sum(values) / len(values), 3) if values else None,
        }
    return out



def _timeframe_bucket_calibration(rows, score_field, outcome_field):
    groups = {}
    for row in rows:
        if row.get("timeframe_score_version") != TIMEFRAME_SCORE_VERSION:
            continue
        value = _num((row.get("outcomes") or {}).get(outcome_field))
        bucket = _score_bucket(row.get(score_field))
        if value is None or bucket is None:
            continue
        g = groups.setdefault(bucket, {"n": 0, "wins": 0, "returns": []})
        g["n"] += 1
        g["wins"] += int(value > 0)
        g["returns"].append(value)

    out = {}
    for bucket, g in groups.items():
        out[bucket] = {
            "n": g["n"],
            "higher_rate": round(g["wins"] / g["n"] * 100.0, 1) if g["n"] else None,
            "avg_return_pct": (
                round(sum(g["returns"]) / len(g["returns"]), 3)
                if g["returns"] else None
            ),
        }
    return out


def _timeframe_best_fit_summary(rows):
    specs = {
        "INTRADAY": ("return_60m_pct", "60m"),
        "SWING": ("return_5d_pct", "5 trading days"),
        "LONGER-TERM": ("return_20d_pct", "20 trading days"),
    }
    out = {}
    for fit, (field, horizon) in specs.items():
        selected = [
            row for row in rows
            if row.get("timeframe_score_version") == TIMEFRAME_SCORE_VERSION
            and row.get("timeframe_best_fit") == fit
        ]
        values = [
            _num((row.get("outcomes") or {}).get(field))
            for row in selected
        ]
        values = [value for value in values if value is not None]
        out[fit] = {
            "signals": len(selected),
            "resolved": len(values),
            "horizon": horizon,
            "higher_rate": (
                round(sum(value > 0 for value in values) / len(values) * 100.0, 1)
                if values else None
            ),
            "avg_return_pct": (
                round(sum(values) / len(values), 3) if values else None
            ),
        }
    return out


def _timeframe_learning_progress(rows):
    tf_rows = [
        row for row in rows
        if row.get("timeframe_score_version") == TIMEFRAME_SCORE_VERSION
    ]
    counts = {
        "intraday": sum(
            (row.get("outcomes") or {}).get("return_60m_pct") is not None
            for row in tf_rows
        ),
        "swing": sum(
            (row.get("outcomes") or {}).get("return_5d_pct") is not None
            for row in tf_rows
        ),
        "long_term": sum(
            (row.get("outcomes") or {}).get("return_20d_pct") is not None
            for row in tf_rows
        ),
    }

    def stage(n):
        if n < 30:
            return {"stage": "COLLECTING", "resolved": n, "next_threshold": 30}
        if n < 100:
            return {"stage": "EARLY READ", "resolved": n, "next_threshold": 100}
        if n < 300:
            return {"stage": "USEFUL", "resolved": n, "next_threshold": 300}
        return {"stage": "STRONGER SAMPLE", "resolved": n, "next_threshold": None}

    return {key: stage(value) for key, value in counts.items()}


def _repeat_bounce_summary(rows):
    candidates=[
        row for row in rows
        if row.get("repeat_bounce_plan_available")
        and row.get("preferred_plan")=="repeat_bounce"
        and row.get("plan_status")=="ENTRY AVAILABLE"
    ]
    resolved=[
        row for row in candidates
        if (row.get("outcomes") or {}).get("repeat_bounce_target1_first_touch") in {"target","stop"}
    ]
    wins=[
        row for row in resolved
        if (row.get("outcomes") or {}).get("repeat_bounce_target1_first_touch")=="target"
    ]
    mfe=[
        _num((row.get("outcomes") or {}).get("repeat_bounce_mfe_30m_pct"))
        for row in candidates
    ]
    mfe=[v for v in mfe if v is not None]
    mae=[
        _num((row.get("outcomes") or {}).get("repeat_bounce_mae_30m_pct"))
        for row in candidates
    ]
    mae=[v for v in mae if v is not None]
    return {
        "entry_signals":len(candidates),
        "resolved_target_stop":len(resolved),
        "target_before_stop_rate":(
            round(len(wins)/len(resolved)*100.0,1) if resolved else None
        ),
        "avg_mfe_30m_pct":round(sum(mfe)/len(mfe),3) if mfe else None,
        "avg_mae_30m_pct":round(sum(mae)/len(mae),3) if mae else None,
    }


def _mature_bounce_failure_summary(rows):
    mature=[row for row in rows if int(row.get("bounce_count") or 0)>=2]
    drops=[
        _num((row.get("outcomes") or {}).get("post_bounce_max_drop_60m_pct"))
        for row in mature
    ]
    drops=[v for v in drops if v is not None]
    return {
        "observations":len(mature),
        "resolved_60m_excursions":len(drops),
        "drop_5pct_rate":(
            round(sum(v<=-5.0 for v in drops)/len(drops)*100.0,1)
            if drops else None
        ),
        "drop_10pct_rate":(
            round(sum(v<=-10.0 for v in drops)/len(drops)*100.0,1)
            if drops else None
        ),
    }


def tracker_summary(rows=None, symbol=None, current_metrics=None):
    all_rows = rows if rows is not None else _load()
    current_rows = [
        r for r in all_rows
        if r.get("feature_version") == ANALYZER_FEATURE_VERSION
    ]
    legacy_excluded = len(all_rows) - len(current_rows)

    decision_rows = [
        r for r in current_rows
        if r.get("decision_score_version") == DECISION_SCORE_VERSION
    ]
    legacy_decision_excluded = len(current_rows) - len(decision_rows)

    symbol_rows = current_rows
    if symbol:
        symbol_rows = [
            r for r in current_rows
            if r.get("symbol") == str(symbol).upper().strip()
        ]

    # Score calibration requires both the current market-data feature version
    # and the current Decision score formula. Lifecycle/history may still use
    # all current-provider observations because it tracks the original signal,
    # not score-band calibration.
    calibration_rows = _independent_calibration_rows(decision_rows)
    resolved_60 = [
        r for r in calibration_rows
        if (r.get("outcomes") or {}).get("return_60m_pct") is not None
    ]
    positive_60 = [
        r for r in resolved_60
        if _num((r.get("outcomes") or {}).get("return_60m_pct")) is not None
        and _num((r.get("outcomes") or {}).get("return_60m_pct")) > 0
    ]
    touches = [
        r for r in calibration_rows
        if (r.get("outcomes") or {}).get("target1_first_touch") in {"target", "stop"}
    ]
    target_wins = [
        r for r in touches
        if (r.get("outcomes") or {}).get("target1_first_touch") == "target"
    ]

    durable = _load_durable_calibration()
    if (
        durable.get("feature_version") != ANALYZER_FEATURE_VERSION
        or durable.get("decision_score_version") != DECISION_SCORE_VERSION
    ):
        durable = {}
    durable_timeframe = (
        durable
        if durable.get("timeframe_score_version") == TIMEFRAME_SCORE_VERSION
        else {}
    )
    durable_resolved = int(durable.get("resolved_60m") or 0)
    effective_resolved = max(durable_resolved, len(resolved_60))
    progress = durable.get("calibration_progress")
    if not isinstance(progress, dict):
        if effective_resolved < 30:
            progress = {
                "stage": "COLLECTING",
                "next_threshold": 30,
                "remaining": 30 - effective_resolved,
            }
        elif effective_resolved < 100:
            progress = {
                "stage": "EARLY READ",
                "next_threshold": 100,
                "remaining": 100 - effective_resolved,
            }
        elif effective_resolved < 300:
            progress = {
                "stage": "USEFUL",
                "next_threshold": 300,
                "remaining": 300 - effective_resolved,
            }
        else:
            progress = {
                "stage": "STRONGER SAMPLE",
                "next_threshold": None,
                "remaining": 0,
            }

    lifecycle = (
        signal_lifecycle(symbol, current_metrics=current_metrics, rows=symbol_rows)
        if symbol else None
    )

    return {
        "feature_version": ANALYZER_FEATURE_VERSION,
        "decision_score_version": DECISION_SCORE_VERSION,
        "total_predictions": len(current_rows),
        "legacy_predictions_excluded": legacy_excluded,
        "legacy_decision_scores_excluded": legacy_decision_excluded,
        "calibration_rows": len(calibration_rows),
        "calibration_sampling": "one observation per ticker per hour",
        "signal_lifecycle": lifecycle,
        "resolved_60m": len(resolved_60),
        "higher_60m_rate": (
            round(len(positive_60) / len(resolved_60) * 100.0, 1)
            if resolved_60 else None
        ),
        "resolved_target_stop": len(touches),
        "target_before_stop_rate": (
            round(len(target_wins) / len(touches) * 100.0, 1)
            if touches else None
        ),
        "potential_calibration": (
            (durable.get("potential_calibration") or {})
            or _bucket_calibration(calibration_rows, "potential_score")
        ),
        "entry_calibration": (
            (durable.get("entry_calibration") or {})
            or _bucket_calibration(calibration_rows, "entry_readiness")
        ),
        "entry_signal_calibration": durable.get("entry_signal_calibration") or {},
        "timeframe_calibration": (
            durable_timeframe.get("timeframe_calibration")
            or {
                "intraday_60m": _timeframe_bucket_calibration(
                    calibration_rows, "timeframe_intraday_score", "return_60m_pct"
                ),
                "swing_3d": _timeframe_bucket_calibration(
                    calibration_rows, "timeframe_swing_score", "return_3d_pct"
                ),
                "swing_5d": _timeframe_bucket_calibration(
                    calibration_rows, "timeframe_swing_score", "return_5d_pct"
                ),
                "long_term_20d": _timeframe_bucket_calibration(
                    calibration_rows, "timeframe_long_term_score", "return_20d_pct"
                ),
                "long_term_60d": _timeframe_bucket_calibration(
                    calibration_rows, "timeframe_long_term_score", "return_60d_pct"
                ),
            }
        ),
        "timeframe_best_fit_calibration": (
            durable_timeframe.get("timeframe_best_fit_calibration")
            or _timeframe_best_fit_summary(calibration_rows)
        ),
        "timeframe_learning_progress": (
            durable_timeframe.get("timeframe_learning_progress")
            or _timeframe_learning_progress(calibration_rows)
        ),
        "repeat_bounce_calibration": (
            durable.get("repeat_bounce_calibration")
            or _repeat_bounce_summary(calibration_rows)
        ),
        "mature_bounce_failure_calibration": (
            durable.get("mature_bounce_failure_calibration")
            or _mature_bounce_failure_summary(calibration_rows)
        ),
        "calibration_ready": (
            bool(durable.get("calibration_ready"))
            or len(resolved_60) >= 30
        ),
        "durable_resolved_60m": durable_resolved,
        "effective_resolved_60m": effective_resolved,
        "calibration_progress": progress,
        "storage": str(LOG_PATH),
        "persistence": "github+local" if GITHUB_TOKEN else "runtime-local",
        "durable_enabled": bool(GITHUB_TOKEN),
        "durable_path": _REMOTE_STATE.get("last_path"),
        "durable_error": _REMOTE_STATE.get("last_error"),
    }
