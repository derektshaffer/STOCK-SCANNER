import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


LOG_PATH = Path(os.environ.get("ANALYZER_PREDICTION_LOG", "analysis_logs/analyzer_predictions.json"))
BUCKET_MINUTES = 5


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


def _load():
    try:
        if LOG_PATH.exists():
            payload = json.loads(LOG_PATH.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return payload
    except Exception:
        pass
    return []


def _save(rows):
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = LOG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows[-5000:], separators=(",", ":")), encoding="utf-8")
        tmp.replace(LOG_PATH)
        return True
    except Exception:
        return False


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

    row = {
        "id": f"{key}:{len(rows)+1}",
        "bucket_key": key,
        "symbol": symbol,
        "timestamp": now.isoformat(),
        "price": _num(metrics.get("price")),
        "day_pct": _num(metrics.get("day_pct")),
        "vwap_extension_pct": _num(metrics.get("vwap_extension_pct")),
        "volume_pace": _num(metrics.get("volume_pace")),
        "setup_score": _num(metrics.get("score")),
        "plan_confidence": _num(plan.get("confidence")),
        "plan_status": plan.get("status"),
        "potential_score": _num(v2.get("potential_score")),
        "entry_readiness": _num(v2.get("entry_readiness")),
        "evidence_strength": _num(v2.get("evidence_strength")),
        "entry_low": _num(selected.get("entry_low")),
        "entry_high": _num(selected.get("entry_high")),
        "target1": _num(selected.get("target1")),
        "stop": _num(selected.get("stop")),
        "ml_edge": _num(ml.get("ml_edge_score")),
        "ml_validated_models": int(ml.get("validated_edge_model_count") or 0),
        "historical_bias": hist.get("bias_label"),
        "historical_bias_score": _num(hist.get("bias_score")),
        "historical_samples": int(hist.get("sample_count") or 0),
        "outcomes": {},
    }
    rows.append(row)
    ok = _save(rows)
    return {"recorded": ok, "count": len(rows), "path": str(LOG_PATH)}


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


def resolve_symbol_predictions(sa, symbol, now=None):
    """Resolve older predictions opportunistically using delayed SIP bars.

    This intentionally waits for consolidated delayed data rather than scoring
    outcomes from a potentially incomplete single-exchange live feed.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    symbol = str(symbol or "").upper().strip()
    rows = _load()
    pending = [
        row for row in rows
        if row.get("symbol") == symbol
        and _parse_dt(row.get("timestamp"))
        and not bool((row.get("outcomes") or {}).get("resolved_60m"))
    ][-40:]
    if not pending:
        return tracker_summary(rows, symbol)

    earliest = min(_parse_dt(row["timestamp"]) for row in pending)
    safe_end = now - timedelta(minutes=16)
    if safe_end <= earliest:
        return tracker_summary(rows, symbol)

    try:
        bars, _source = sa.try_sip_delayed_bars(
            symbol, "5Min", earliest - timedelta(minutes=5), safe_end, 10000
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

    if changed:
        _save(rows)
    return tracker_summary(rows, symbol)


def tracker_summary(rows=None, symbol=None):
    rows = rows if rows is not None else _load()
    if symbol:
        rows = [r for r in rows if r.get("symbol") == str(symbol).upper().strip()]

    resolved_60 = [
        r for r in rows
        if (r.get("outcomes") or {}).get("return_60m_pct") is not None
    ]
    positive_60 = [
        r for r in resolved_60
        if _num((r.get("outcomes") or {}).get("return_60m_pct")) is not None
        and _num((r.get("outcomes") or {}).get("return_60m_pct")) > 0
    ]
    touches = [
        r for r in rows
        if (r.get("outcomes") or {}).get("target1_first_touch") in {"target", "stop"}
    ]
    target_wins = [
        r for r in touches
        if (r.get("outcomes") or {}).get("target1_first_touch") == "target"
    ]

    return {
        "total_predictions": len(rows),
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
        "storage": str(LOG_PATH),
        "persistence": "runtime-local",
    }
