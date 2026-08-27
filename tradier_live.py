from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

TRADIER_BASE = "https://api.tradier.com/v1"
ET = ZoneInfo("America/New_York")


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "stock-scanner-tradier-live/1.0",
    }


def _request_json(url, token, timeout=30):
    req = urllib.request.Request(url, headers=_headers(token))
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _request_form_json(url, token, form, timeout=30):
    data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            **_headers(token),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_quotes(symbols, token):
    """POST quote lookup for larger symbol batches."""
    symbols = [str(s).upper().strip() for s in symbols if str(s).strip()]
    if not symbols:
        return {}
    payload = _request_form_json(
        f"{TRADIER_BASE}/markets/quotes",
        token,
        {"symbols": ",".join(symbols), "greeks": "false"},
    )
    quotes = ((payload or {}).get("quotes") or {}).get("quote")
    if quotes is None:
        return {}
    if not isinstance(quotes, list):
        quotes = [quotes]
    return {
        str(row.get("symbol") or "").upper(): row
        for row in quotes
        if row.get("symbol")
    }


def get_history_bars(symbol, token, start, end, interval="daily"):
    """Return Tradier historical daily/weekly/monthly candles in scanner bar form."""
    start_date = start.astimezone(ET).date() if isinstance(start, datetime) else start
    end_date = end.astimezone(ET).date() if isinstance(end, datetime) else end
    params = urllib.parse.urlencode(
        {
            "symbol": str(symbol).upper().strip(),
            "interval": interval,
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        }
    )
    payload = _request_json(f"{TRADIER_BASE}/markets/history?{params}", token)
    rows = ((payload or {}).get("history") or {}).get("day")
    if rows is None:
        return []
    if not isinstance(rows, list):
        rows = [rows]

    bars = []
    for row in rows:
        day = str((row or {}).get("date") or "").strip()
        close = _num((row or {}).get("close"))
        if not day or close is None:
            continue
        try:
            dt = datetime.fromisoformat(day).replace(
                hour=16, minute=0, second=0, microsecond=0, tzinfo=ET
            )
            timestamp = dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            timestamp = None
        bars.append(
            {
                "t": timestamp,
                "o": _num(row.get("open")),
                "h": _num(row.get("high")),
                "l": _num(row.get("low")),
                "c": close,
                "v": _num(row.get("volume")) or 0.0,
                "vw": None,
            }
        )
    bars.sort(key=lambda b: b.get("t") or "")
    return bars


def get_quotes(symbols, token):
    symbols = [str(s).upper().strip() for s in symbols if str(s).strip()]
    if not symbols:
        return {}
    params = urllib.parse.urlencode({"symbols": ",".join(symbols)})
    payload = _request_json(f"{TRADIER_BASE}/markets/quotes?{params}", token)
    quotes = ((payload or {}).get("quotes") or {}).get("quote")
    if quotes is None:
        return {}
    if not isinstance(quotes, list):
        quotes = [quotes]
    return {
        str(row.get("symbol") or "").upper(): row
        for row in quotes
        if row.get("symbol")
    }


def _iso_timestamp(row):
    raw = row.get("time") or row.get("date")
    if raw:
        text = str(raw)
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ET)
            return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            pass

    raw_ts = row.get("timestamp")
    if raw_ts is not None:
        try:
            ts = float(raw_ts)
            if ts > 10_000_000_000:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except Exception:
            pass
    return None


def _bar_from_timesale(row):
    close = _num(row.get("close"))
    if close is None:
        close = _num(row.get("price"))
    if close is None:
        return None

    open_ = _num(row.get("open"))
    high = _num(row.get("high"))
    low = _num(row.get("low"))
    volume = _num(row.get("volume"))
    vwap = _num(row.get("vwap"))

    return {
        "t": _iso_timestamp(row),
        "o": open_ if open_ is not None else close,
        "h": high if high is not None else close,
        "l": low if low is not None else close,
        "c": close,
        "v": volume if volume is not None else 0.0,
        "vw": vwap if vwap is not None else close,
    }


def get_timesales_bars(symbol, token, start, end, interval="1min", session_filter="all"):
    start_et = start.astimezone(ET)
    end_et = end.astimezone(ET)
    params = urllib.parse.urlencode(
        {
            "symbol": str(symbol).upper().strip(),
            "interval": interval,
            "start": start_et.strftime("%Y-%m-%d %H:%M"),
            "end": end_et.strftime("%Y-%m-%d %H:%M"),
            "session_filter": session_filter,
        }
    )
    payload = _request_json(f"{TRADIER_BASE}/markets/timesales?{params}", token)
    rows = ((payload or {}).get("series") or {}).get("data")
    if rows is None:
        return []
    if not isinstance(rows, list):
        rows = [rows]

    bars = []
    for row in rows:
        bar = _bar_from_timesale(row or {})
        if bar is not None:
            bars.append(bar)
    bars.sort(key=lambda b: b.get("t") or "")
    return bars
