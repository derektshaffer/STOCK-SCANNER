"""Bounded, cached display history. Never an input to scores, plans or ML."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import math
from zoneinfo import ZoneInfo

from analyzer_context_cache import get_cached_context, set_cached_context
from analyzer_history_cache import history_error_summary
from analyzer_provider_config import history_identity
from tradier_live import get_timesales_bars

ET = ZoneInfo("America/New_York")


def load_chart_history(symbol, now, session_date, *, tradier_token="", alpaca_fetch=None, feed="sip", timeframe="5m"):
    """Fetch only prior regular sessions, with a truthful failure/empty state.

    Tradier permits 40 calendar days of regular-session 5-minute history.
    Request 30 days at 5m or 14 days at 1m (below the 20-day regular-session
    retention); the separately validated active/reference session wins.
    Provider failure leaves that session and daily history usable.
    """
    if timeframe not in {"1m", "5m"}:
        raise ValueError("Unsupported chart history timeframe")
    minutes = 1 if timeframe == "1m" else 5
    source = f"Tradier consolidated · {minutes}-minute · regular sessions" if tradier_token else f"Alpaca {feed} · {minutes}-minute · regular sessions"
    try:
        end = datetime.fromisoformat(str(session_date)[:10]).replace(tzinfo=ET)
        start = end - timedelta(days=14 if timeframe == "1m" else 30)
        identity = sha256(tradier_token.encode()).hexdigest()[:16] if tradier_token else history_identity() + ":" + feed
        key = f"chart-history-v3:{symbol}:{timeframe}:{end.date()}:{identity}"
        cached = get_cached_context(key, 6 * 3600)
        if cached and cached.get("symbol") == symbol and cached.get("status") == "ok":
            return cached
        if tradier_token:
            rows = get_timesales_bars(symbol, tradier_token, start, end, interval=f"{minutes}min",
                                     session_filter="open", strict=True, timeout=12)
        elif alpaca_fetch:
            rows = alpaca_fetch(symbol, f"{minutes}Min", start, end, limit=10000, feed=feed, complete=True)
        else:
            raise RuntimeError("Missing chart history credentials")
        normalized = {}
        for row in rows:
            if any(str(row.get(field) or symbol).strip().upper() != str(symbol).strip().upper()
                   for field in ("symbol", "S")):
                raise ValueError("Historical chart symbol mismatch")
            stamp = datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("Malformed historical timestamp")
            local = stamp.astimezone(ET)
            if not start <= local < min(end, now.astimezone(ET)):
                continue
            # Alpaca returns extended hours; use the same session scope as Tradier.
            if local.weekday() >= 5 or not 570 <= local.hour * 60 + local.minute < 960:
                continue
            values = {k: float(row[k]) for k in ("o", "h", "l", "c", "v")}
            if not all(math.isfinite(v) for v in values.values()) or not (
                0 < values["l"] <= min(values["o"], values["c"]) <=
                max(values["o"], values["c"]) <= values["h"] and values["v"] >= 0
            ):
                raise ValueError("Malformed historical OHLCV")
            stamp = stamp.astimezone(timezone.utc).isoformat()
            record = dict(t=stamp, **values)
            if stamp in normalized and normalized[stamp] != record:
                raise ValueError("Conflicting historical candles")
            normalized[stamp] = record
        result = {"symbol": symbol, "status": "ok" if normalized else "empty", "source": source,
                  "timeframe": timeframe, "requested_start": start.isoformat(), "requested_end": end.isoformat(),
                  "fetched_at": now.isoformat(), "bars": [normalized[t] for t in sorted(normalized)]}
        if normalized:
            set_cached_context(key, result)
        return result
    except Exception as exc:
        return {"symbol": symbol, "status": "unavailable", "source": source, "bars": [],
                "error": history_error_summary(exc)}
