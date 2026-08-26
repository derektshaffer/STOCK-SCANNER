from __future__ import annotations

import json
import math
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

TRADIER_BASE = "https://api.tradier.com/v1"
ALPACA_DATA_BASE = "https://data.alpaca.markets"
ET = ZoneInfo("America/New_York")
OUT_DIR = Path(os.environ.get("PROVIDER_COMPARE_DIR", "provider_comparison"))

MAX_SYMBOLS = 15


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _request_json(url, headers, method="GET", data=None, timeout=30):
    req = urllib.request.Request(url, headers=headers, method=method, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _tradier_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "stock-scanner-provider-compare/1.0",
    }


def _alpaca_headers(key, secret):
    return {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Accept": "application/json",
        "User-Agent": "stock-scanner-provider-compare/1.0",
    }


def _tradier_quote_list(payload):
    quotes = ((payload or {}).get("quotes") or {}).get("quote")
    if quotes is None:
        return []
    if isinstance(quotes, list):
        return quotes
    return [quotes]


def get_tradier_quotes(symbols, token):
    params = urllib.parse.urlencode({"symbols": ",".join(symbols)})
    payload = _request_json(
        f"{TRADIER_BASE}/markets/quotes?{params}",
        _tradier_headers(token),
    )
    return {
        str(q.get("symbol") or "").upper(): q
        for q in _tradier_quote_list(payload)
        if q.get("symbol")
    }


def get_tradier_timesales(symbol, token, interval="1min", minutes=70):
    now_et = datetime.now(ET)
    start_et = now_et - timedelta(minutes=minutes)
    params = urllib.parse.urlencode(
        {
            "symbol": symbol,
            "interval": interval,
            "start": start_et.strftime("%Y-%m-%d %H:%M"),
            "end": now_et.strftime("%Y-%m-%d %H:%M"),
            "session_filter": "all",
        }
    )
    payload = _request_json(
        f"{TRADIER_BASE}/markets/timesales?{params}",
        _tradier_headers(token),
    )
    data = ((payload or {}).get("series") or {}).get("data")
    if data is None:
        return []
    return data if isinstance(data, list) else [data]


def get_alpaca_snapshot(symbol, key, secret):
    params = urllib.parse.urlencode({"feed": "iex"})
    return _request_json(
        f"{ALPACA_DATA_BASE}/v2/stocks/{urllib.parse.quote(symbol)}/snapshot?{params}",
        _alpaca_headers(key, secret),
    )


def get_alpaca_bars(symbol, key, secret, minutes=70):
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=minutes)
    params = urllib.parse.urlencode(
        {
            "timeframe": "1Min",
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": now.isoformat().replace("+00:00", "Z"),
            "limit": 1000,
            "adjustment": "raw",
            "feed": "iex",
            "sort": "asc",
        }
    )
    payload = _request_json(
        f"{ALPACA_DATA_BASE}/v2/stocks/{urllib.parse.quote(symbol)}/bars?{params}",
        _alpaca_headers(key, secret),
    )
    return payload.get("bars") or []


def _weighted_vwap(rows, price_key, volume_key):
    pv = 0.0
    volume = 0.0
    for row in rows:
        px = _num(row.get(price_key))
        vol = _num(row.get(volume_key))
        if px is None or vol is None or vol <= 0:
            continue
        pv += px * vol
        volume += vol
    return (pv / volume) if volume > 0 else None


def _pct_change(new, old):
    new = _num(new)
    old = _num(old)
    if new is None or old in (None, 0):
        return None
    return (new / old - 1.0) * 100.0


def _spread_pct(bid, ask):
    bid = _num(bid)
    ask = _num(ask)
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    return ((ask - bid) / mid) * 100.0 if mid else None


def _momentum_from_series(rows, current_price, close_key):
    if not rows:
        return None, None
    m5 = None
    m15 = None
    if len(rows) >= 6:
        m5 = _pct_change(current_price, rows[-6].get(close_key))
    if len(rows) >= 16:
        m15 = _pct_change(current_price, rows[-16].get(close_key))
    return m5, m15


def _tradier_metrics(symbol, quote, bars):
    last = _num(quote.get("last"))
    bid = _num(quote.get("bid"))
    ask = _num(quote.get("ask"))
    prevclose = _num(quote.get("prevclose"))
    high = _num(quote.get("high"))
    low = _num(quote.get("low"))
    volume = _num(quote.get("volume"))

    m5, m15 = _momentum_from_series(bars, last, "close")
    session_volume = sum(_num(x.get("volume")) or 0.0 for x in bars)
    session_vwap = _weighted_vwap(bars, "vwap", "volume")

    return {
        "symbol": symbol,
        "price": last,
        "bid": bid,
        "ask": ask,
        "spread_pct": _spread_pct(bid, ask),
        "prev_close": prevclose,
        "day_pct": _pct_change(last, prevclose),
        "day_volume": volume,
        "day_high": high,
        "day_low": low,
        "session_volume_70m": session_volume,
        "session_vwap_70m": session_vwap,
        "momentum_5m": m5,
        "momentum_15m": m15,
        "average_volume_90d": _num(quote.get("average_volume")),
        "source": "Tradier consolidated",
    }


def _alpaca_metrics(symbol, snapshot, bars):
    trade = snapshot.get("latestTrade") or {}
    quote = snapshot.get("latestQuote") or {}
    daily = snapshot.get("dailyBar") or {}
    prev = snapshot.get("prevDailyBar") or {}

    last = _num(trade.get("p"))
    bid = _num(quote.get("bp"))
    ask = _num(quote.get("ap"))
    prevclose = _num(prev.get("c"))
    high = _num(daily.get("h"))
    low = _num(daily.get("l"))
    volume = _num(daily.get("v"))

    m5, m15 = _momentum_from_series(bars, last, "c")
    session_volume = sum(_num(x.get("v")) or 0.0 for x in bars)
    session_vwap = _weighted_vwap(
        [
            {
                "px": (
                    _num(x.get("vw"))
                    if _num(x.get("vw")) is not None
                    else _num(x.get("c"))
                ),
                "vol": _num(x.get("v")),
            }
            for x in bars
        ],
        "px",
        "vol",
    )

    return {
        "symbol": symbol,
        "price": last,
        "bid": bid,
        "ask": ask,
        "spread_pct": _spread_pct(bid, ask),
        "prev_close": prevclose,
        "day_pct": _pct_change(last, prevclose),
        "day_volume": volume,
        "day_high": high,
        "day_low": low,
        "session_volume_70m": session_volume,
        "session_vwap_70m": session_vwap,
        "momentum_5m": m5,
        "momentum_15m": m15,
        "source": "Alpaca IEX",
    }


def _delta_pct(tradier_value, alpaca_value):
    tradier_value = _num(tradier_value)
    alpaca_value = _num(alpaca_value)
    if tradier_value is None or alpaca_value in (None, 0):
        return None
    return (tradier_value / alpaca_value - 1.0) * 100.0


def compare_symbol(symbol, tradier_quote, tradier_token, alpaca_key, alpaca_secret):
    tradier_bars = get_tradier_timesales(
        symbol,
        tradier_token,
        interval="1min",
        minutes=70,
    )
    alpaca_snapshot = get_alpaca_snapshot(
        symbol,
        alpaca_key,
        alpaca_secret,
    )
    alpaca_bars = get_alpaca_bars(
        symbol,
        alpaca_key,
        alpaca_secret,
        minutes=70,
    )

    tradier = _tradier_metrics(symbol, tradier_quote, tradier_bars)
    alpaca = _alpaca_metrics(symbol, alpaca_snapshot, alpaca_bars)

    return {
        "symbol": symbol,
        "tradier": tradier,
        "alpaca_iex": alpaca,
        "difference": {
            "price_diff_pct": _delta_pct(tradier.get("price"), alpaca.get("price")),
            "day_volume_diff_pct": _delta_pct(
                tradier.get("day_volume"),
                alpaca.get("day_volume"),
            ),
            "session_volume_diff_pct": _delta_pct(
                tradier.get("session_volume_70m"),
                alpaca.get("session_volume_70m"),
            ),
            "spread_diff_pct_points": (
                (_num(tradier.get("spread_pct")) or 0.0)
                - (_num(alpaca.get("spread_pct")) or 0.0)
                if tradier.get("spread_pct") is not None
                and alpaca.get("spread_pct") is not None
                else None
            ),
            "momentum_5m_diff_points": (
                (_num(tradier.get("momentum_5m")) or 0.0)
                - (_num(alpaca.get("momentum_5m")) or 0.0)
                if tradier.get("momentum_5m") is not None
                and alpaca.get("momentum_5m") is not None
                else None
            ),
            "momentum_15m_diff_points": (
                (_num(tradier.get("momentum_15m")) or 0.0)
                - (_num(alpaca.get("momentum_15m")) or 0.0)
                if tradier.get("momentum_15m") is not None
                and alpaca.get("momentum_15m") is not None
                else None
            ),
        },
    }


def _summary(rows):
    volume_multiples = []
    spread_improvements = []
    price_diffs = []

    for row in rows:
        tradier = row["tradier"]
        alpaca = row["alpaca_iex"]

        tv = _num(tradier.get("session_volume_70m"))
        av = _num(alpaca.get("session_volume_70m"))
        if tv is not None and av not in (None, 0):
            volume_multiples.append(tv / av)

        ts = _num(tradier.get("spread_pct"))
        aps = _num(alpaca.get("spread_pct"))
        if ts is not None and aps is not None:
            spread_improvements.append(aps - ts)

        pd = _num(row["difference"].get("price_diff_pct"))
        if pd is not None:
            price_diffs.append(abs(pd))

    return {
        "symbols_compared": len(rows),
        "avg_consolidated_to_iex_session_volume_multiple": (
            round(mean(volume_multiples), 2) if volume_multiples else None
        ),
        "median_like_price_difference_abs_pct": (
            round(mean(price_diffs), 4) if price_diffs else None
        ),
        "avg_spread_improvement_pct_points": (
            round(mean(spread_improvements), 4)
            if spread_improvements
            else None
        ),
    }


def run_provider_comparison(symbols):
    tradier_token = os.environ.get("TRADIER_ACCESS_TOKEN", "").strip()
    alpaca_key = os.environ.get("ALPACA_API_KEY", "").strip()
    alpaca_secret = os.environ.get("ALPACA_SECRET_KEY", "").strip()

    if not tradier_token:
        return {
            "status": "missing_tradier_token",
            "message": "Add TRADIER_ACCESS_TOKEN from a live Tradier Brokerage account.",
            "rows": [],
        }
    if not alpaca_key or not alpaca_secret:
        return {
            "status": "missing_alpaca_credentials",
            "message": "Alpaca credentials are missing.",
            "rows": [],
        }

    symbols = [
        str(symbol).upper().strip()
        for symbol in symbols
        if str(symbol).strip()
    ][:MAX_SYMBOLS]
    if not symbols:
        return {
            "status": "no_symbols",
            "message": "No symbols were supplied.",
            "rows": [],
        }

    try:
        tradier_quotes = get_tradier_quotes(symbols, tradier_token)
    except Exception as exc:
        return {
            "status": "tradier_error",
            "message": str(exc)[:400],
            "rows": [],
        }

    rows = []
    errors = []
    for symbol in symbols:
        quote = tradier_quotes.get(symbol)
        if not quote:
            errors.append({"symbol": symbol, "error": "Tradier quote unavailable"})
            continue
        try:
            rows.append(
                compare_symbol(
                    symbol,
                    quote,
                    tradier_token,
                    alpaca_key,
                    alpaca_secret,
                )
            )
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)[:300]})

    payload = {
        "status": "ok" if rows else "no_comparisons",
        "generated_at_et": datetime.now(ET).isoformat(),
        "summary": _summary(rows),
        "rows": rows,
        "errors": errors,
        "field_coverage": {
            "last_price": "both",
            "bid_ask": "both",
            "current_day_volume": "both",
            "current_session_high_low": "both",
            "previous_close": "both",
            "1m_5m_15m_intraday_bars": "both",
            "vwap": "both (Tradier Time & Sales provides interval VWAP)",
            "pre_post_market_bars": "both (Tradier session_filter=all)",
            "market_wide_movers_discovery": "Alpaca retained",
            "long_intraday_history": (
                "Alpaca stronger; Tradier Time & Sales is limited to 20d open/10d all "
                "for 1m and 40d open/18d all for 5m/15m"
            ),
        },
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ET).strftime("%Y%m%d_%H%M%S")
    path = OUT_DIR / f"tradier_vs_iex_{stamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    payload["saved_report"] = str(path)
    return payload
