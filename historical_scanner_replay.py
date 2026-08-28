"""Historical replay backfill for the Momentum Scanner ML model.

The replay uses only information available at each historical timestamp.
It intentionally does not fabricate historical bid/ask spreads or news.
Those fields remain missing and are handled as NaN by scanner_ml_ranker.

Output is a normal outcome_reports/outcomes_*.json payload so the existing
scanner ML loader discovers it automatically.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

from tradier_live import get_history_bars, get_timesales_bars, post_quotes
from multi_bounce import bounce_feature_values, detect_bounce_sequence
from stair_step import detect_stair_step, stair_step_feature_values

REPLAY_VERSION = "historical-scanner-replay-v4.1-sequence-regimes"
ET = ZoneInfo("America/New_York")

DEFAULT_TRADING_DAYS = int(os.environ.get("REPLAY_TRADING_DAYS", "20") or 20)
DEFAULT_UNIVERSE_SIZE = int(os.environ.get("REPLAY_UNIVERSE_SIZE", "300") or 300)
DEFAULT_CANDIDATES_PER_SCAN = int(
    os.environ.get("REPLAY_CANDIDATES_PER_SCAN", "20") or 20
)
DEFAULT_SCAN_STEP_MINUTES = int(
    os.environ.get("REPLAY_SCAN_STEP_MINUTES", "10") or 10
)
MAX_UNION_SYMBOLS = int(os.environ.get("REPLAY_MAX_UNION_SYMBOLS", "450") or 450)
TRADIER_QUOTE_BATCH_SIZE = int(
    os.environ.get("REPLAY_TRADIER_QUOTE_BATCH_SIZE", "300") or 300
)
TRADIER_REQUEST_DELAY_SECONDS = float(
    os.environ.get("REPLAY_TRADIER_REQUEST_DELAY_SECONDS", "0.55") or 0.55
)
OUTPUT_PATH = Path(
    os.environ.get(
        "REPLAY_OUTPUT_PATH",
        "outcome_reports/outcomes_historical_replay.json",
    )
)

ASSET_BASES = [
    value.strip().rstrip("/")
    for value in (
        os.environ.get("ALPACA_TRADING_BASE_URL", "").strip(),
        "https://paper-api.alpaca.markets",
        "https://api.alpaca.markets",
    )
    if value.strip()
]


def _chunks(values, size):
    for i in range(0, len(values), size):
        yield values[i : i + size]


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _bar_dt(bar):
    raw = bar.get("t")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None


def _bar_date_et(bar):
    dt = _bar_dt(bar)
    return dt.astimezone(ET).date() if dt is not None else None


def _regular_minute(bar):
    dt = _bar_dt(bar)
    if dt is None:
        return None
    et = dt.astimezone(ET)
    minute = et.hour * 60 + et.minute
    if 570 <= minute < 960:
        return minute
    return None


def _request_json(url, headers, timeout=45):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _security_name_looks_common(name):
    text = str(name or "").upper()
    excluded = (
        " WARRANT",
        " WARRANTS",
        " WT EXP",
        " UNIT",
        " UNITS",
        " RIGHT",
        " RIGHTS",
        " PREFERRED",
        " PREFERENCE",
    )
    return not any(marker in text for marker in excluded)


def _load_nasdaq_symbol_directory(ss):
    """Public exchange symbol directory fallback; no brokerage permission needed."""
    sources = (
        (
            "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
            "nasdaqlisted",
        ),
        (
            "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
            "otherlisted",
        ),
    )
    symbols = []
    seen = set()
    for url, kind in sources:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "stock-scanner-historical-replay/1.0"},
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            text = response.read().decode("utf-8", errors="replace")
        lines = [line for line in text.splitlines() if "|" in line]
        if not lines:
            continue
        header = lines[0].split("|")
        index = {name: i for i, name in enumerate(header)}

        for line in lines[1:]:
            if line.startswith("File Creation Time"):
                continue
            fields = line.split("|")
            try:
                if kind == "nasdaqlisted":
                    symbol = fields[index["Symbol"]].strip().upper()
                    name = fields[index["Security Name"]].strip()
                    test_issue = fields[index["Test Issue"]].strip().upper()
                    etf = fields[index["ETF"]].strip().upper()
                else:
                    symbol = fields[index["ACT Symbol"]].strip().upper()
                    name = fields[index["Security Name"]].strip()
                    test_issue = fields[index["Test Issue"]].strip().upper()
                    etf = fields[index["ETF"]].strip().upper()
            except (KeyError, IndexError):
                continue

            if test_issue == "Y" or etf == "Y":
                continue
            if not symbol or symbol in seen:
                continue
            if not _security_name_looks_common(name):
                continue
            if not ss.likely_common_stock(symbol):
                continue
            seen.add(symbol)
            symbols.append(symbol)

    if not symbols:
        raise RuntimeError("Nasdaq Trader symbol directory returned no usable symbols.")
    return sorted(symbols), "nasdaqtrader_public_symbol_directory"


def load_active_assets(ss):
    """Load a broad US-equity universe without requiring a trading-account key."""
    last_error = None
    params = urllib.parse.urlencode(
        {"status": "active", "asset_class": "us_equity"}
    )
    for base in ASSET_BASES:
        try:
            data = _request_json(f"{base}/v2/assets?{params}", ss.HEADERS)
            if not isinstance(data, list):
                continue
            symbols = []
            seen = set()
            for asset in data:
                symbol = str(asset.get("symbol") or "").upper().strip()
                exchange = str(asset.get("exchange") or "").upper()
                if not symbol or symbol in seen:
                    continue
                if not bool(asset.get("tradable", True)):
                    continue
                if exchange in {"OTC", "OTCQX", "OTCQB", "PINK"}:
                    continue
                if not ss.likely_common_stock(symbol):
                    continue
                seen.add(symbol)
                symbols.append(symbol)
            if symbols:
                return sorted(symbols), base
        except Exception as exc:
            last_error = exc

    try:
        return _load_nasdaq_symbol_directory(ss)
    except Exception as public_exc:
        raise RuntimeError(
            "Could not load an active stock universe from Alpaca or the "
            f"public exchange directory. Alpaca: {last_error}; "
            f"public directory: {public_exc}"
        ) from public_exc


def _tradier_call(fn, *args, **kwargs):
    """Retry rate-limit/transient Tradier failures without hiding auth errors."""
    delay = 1.5
    for attempt in range(5):
        try:
            return fn(*args, **kwargs)
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt >= 4:
                raise
        except urllib.error.URLError:
            if attempt >= 4:
                raise
        time.sleep(delay)
        delay = min(12.0, delay * 2.0)
    return None


def _select_seed_universe_from_tradier(symbols, token, target_size):
    """Choose a broad current stock universe without using future replay-day data.

    Current quote metadata is used only to control request volume. Replay-day
    ranking still uses only information from days before each historical date.
    """
    quote_rows = {}
    batches = list(_chunks(symbols, TRADIER_QUOTE_BATCH_SIZE))
    for index, batch in enumerate(batches, start=1):
        result = _tradier_call(post_quotes, batch, token) or {}
        quote_rows.update(result)
        if index % 5 == 0 or index == len(batches):
            print(
                f"Tradier quote screening: {index}/{len(batches)} batches "
                f"({len(quote_rows)} quotes)."
            )
        time.sleep(TRADIER_REQUEST_DELAY_SECONDS)

    eligible = []
    for symbol, row in quote_rows.items():
        if str(row.get("type") or "").lower() != "stock":
            continue
        price = (
            _num(row.get("last"))
            or _num(row.get("close"))
            or _num(row.get("prevclose"))
        )
        avg_volume = _num(row.get("average_volume"))
        if price is None or avg_volume is None:
            continue
        if not 0.50 <= price <= 60.0 or avg_volume <= 0:
            continue
        eligible.append(
            {
                "symbol": symbol,
                "price": price,
                "average_volume": avg_volume,
                "average_dollar_volume": price * avg_volume,
            }
        )

    # Preserve small/mid-price momentum names instead of letting mega-caps fill
    # a pure dollar-volume ranking.
    bands = (
        (0.50, 5.0, 0.40),
        (5.0, 20.0, 0.35),
        (20.0, 60.01, 0.25),
    )
    selected = []
    seen = set()
    for low, high, share in bands:
        band = [
            row for row in eligible
            if low <= row["price"] < high
        ]
        band.sort(
            key=lambda row: row["average_dollar_volume"],
            reverse=True,
        )
        quota = max(1, int(round(target_size * share)))
        for row in band[:quota]:
            if row["symbol"] in seen:
                continue
            seen.add(row["symbol"])
            selected.append(row["symbol"])

    if len(selected) < target_size:
        eligible.sort(
            key=lambda row: row["average_dollar_volume"],
            reverse=True,
        )
        for row in eligible:
            if row["symbol"] in seen:
                continue
            seen.add(row["symbol"])
            selected.append(row["symbol"])
            if len(selected) >= target_size:
                break

    return selected[:target_size], len(eligible)


def _fetch_tradier_daily_history(symbols, token, start, end):
    merged = {}
    total = len(symbols)
    for index, symbol in enumerate(symbols, start=1):
        bars = _tradier_call(
            get_history_bars,
            symbol,
            token,
            start,
            end,
            "daily",
        ) or []
        if bars:
            merged[symbol] = bars
        if index % 25 == 0 or index == total:
            print(
                f"Tradier daily history: {index}/{total} symbols "
                f"({len(merged)} with data)."
            )
        time.sleep(TRADIER_REQUEST_DELAY_SECONDS)
    return merged


def _fetch_tradier_intraday_history(symbols, token, start, end):
    merged = {}
    total = len(symbols)
    for index, symbol in enumerate(symbols, start=1):
        bars = _tradier_call(
            get_timesales_bars,
            symbol,
            token,
            start,
            end,
            interval="5min",
            session_filter="open",
        ) or []
        if bars:
            merged[symbol] = bars
        if index % 25 == 0 or index == total:
            print(
                f"Tradier 5-minute history: {index}/{total} symbols "
                f"({len(merged)} with data)."
            )
        time.sleep(TRADIER_REQUEST_DELAY_SECONDS)
    return merged


def fetch_multi_bars_complete(
    ss,
    symbols,
    timeframe,
    start,
    end,
    *,
    feed="sip",
    chunk_size=20,
):
    """Fetch all pages for symbol chunks instead of stock_scanner's 10-page cap."""
    merged = defaultdict(list)
    symbols = list(dict.fromkeys(symbols))
    for chunk_index, chunk in enumerate(_chunks(symbols, chunk_size), start=1):
        page_token = None
        pages = 0
        while True:
            query = {
                "symbols": ",".join(chunk),
                "timeframe": timeframe,
                "start": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "end": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "limit": 10000,
                "adjustment": "raw",
                "feed": feed,
                "sort": "asc",
            }
            if page_token:
                query["page_token"] = page_token
            data = ss.get_json(
                f"{ss.DATA_BASE}/v2/stocks/bars?{urllib.parse.urlencode(query)}",
                timeout=45,
            )
            for symbol, bars in (data.get("bars") or {}).items():
                merged[str(symbol).upper()].extend(bars or [])
            page_token = data.get("next_page_token")
            pages += 1
            if not page_token:
                break
            if pages >= 100:
                raise RuntimeError(
                    f"Replay pagination exceeded 100 pages for chunk {chunk_index}."
                )
        if chunk_index % 10 == 0:
            print(
                f"Fetched {timeframe} history for "
                f"{min(chunk_index * chunk_size, len(symbols))}/{len(symbols)} symbols."
            )
        time.sleep(0.03)
    return dict(merged)


def _daily_index(daily_bars):
    index = {}
    for symbol, bars in daily_bars.items():
        rows = []
        for bar in bars:
            day = _bar_date_et(bar)
            close = _num(bar.get("c"))
            volume = _num(bar.get("v"))
            if day is None or close is None or close <= 0 or volume is None:
                continue
            rows.append((day, bar))
        rows.sort(key=lambda item: item[0])
        if rows:
            index[symbol] = rows
    return index


def replay_trading_dates(daily_index, count, today=None):
    today = today or datetime.now(ET).date()
    counts = Counter()
    for rows in daily_index.values():
        for day, _ in rows:
            if day < today:
                counts[day] += 1
    liquid_dates = sorted(day for day, n in counts.items() if n >= 50)
    return liquid_dates[-max(1, count) :]


def _prior_daily_rows(rows, replay_day, limit=20):
    prior = [bar for day, bar in rows if day < replay_day]
    return prior[-limit:]


def _universe_metrics(rows, replay_day):
    prior = _prior_daily_rows(rows, replay_day, 21)
    if len(prior) < 7:
        return None
    last = prior[-1]
    prev_close = _num(last.get("c"))
    if prev_close is None or not 0.50 <= prev_close <= 60.0:
        return None

    window = prior[-20:]
    dollar_values = []
    volumes = []
    for bar in window:
        close = _num(bar.get("c"))
        volume = _num(bar.get("v"))
        if close is not None and volume is not None and close > 0 and volume > 0:
            dollar_values.append(close * volume)
            volumes.append(volume)
    if len(dollar_values) < 7:
        return None

    median_dollar = median(dollar_values)
    median_volume = median(volumes)
    if median_dollar < 100_000:
        return None

    prior_move = 0.0
    if len(prior) >= 2:
        before = _num(prior[-2].get("c"))
        if before:
            prior_move = (prev_close / before - 1.0) * 100.0

    last_volume = _num(last.get("v")) or 0.0
    prior_rvol = last_volume / median_volume if median_volume > 0 else 0.0
    return {
        "prev_close": prev_close,
        "median_dollar": median_dollar,
        "median_volume": median_volume,
        "prior_move": prior_move,
        "prior_rvol": prior_rvol,
    }


def select_daily_universe(daily_index, replay_day, size):
    rows = []
    for symbol, history in daily_index.items():
        metrics = _universe_metrics(history, replay_day)
        if metrics is not None:
            rows.append((symbol, metrics))
    if not rows:
        return [], {}

    liquidity = sorted(
        rows, key=lambda item: item[1]["median_dollar"], reverse=True
    )
    momentum = sorted(
        rows, key=lambda item: item[1]["prior_move"], reverse=True
    )
    rvol = sorted(
        rows, key=lambda item: item[1]["prior_rvol"], reverse=True
    )

    quotas = (
        (liquidity, max(1, int(size * 0.55))),
        (momentum, max(1, int(size * 0.225))),
        (rvol, max(1, int(size * 0.225))),
    )
    selected = []
    seen = set()
    metrics_by_symbol = {}
    for ranked, quota in quotas:
        for symbol, metrics in ranked[:quota]:
            if symbol in seen:
                continue
            seen.add(symbol)
            selected.append(symbol)
            metrics_by_symbol[symbol] = metrics

    if len(selected) < size:
        for symbol, metrics in liquidity:
            if symbol in seen:
                continue
            seen.add(symbol)
            selected.append(symbol)
            metrics_by_symbol[symbol] = metrics
            if len(selected) >= size:
                break

    return selected[:size], metrics_by_symbol


def group_intraday(bars_by_symbol):
    grouped = defaultdict(lambda: defaultdict(list))
    for symbol, bars in bars_by_symbol.items():
        for bar in bars:
            day = _bar_date_et(bar)
            minute = _regular_minute(bar)
            if day is None or minute is None:
                continue
            grouped[symbol][day].append((minute, bar))
    for day_map in grouped.values():
        for day, rows in day_map.items():
            rows.sort(key=lambda item: item[0])
    return grouped


def _profile_for_checkpoint(day_map, replay_day, completed_bars):
    prior_days = sorted(day for day in day_map if day < replay_day)[-20:]
    sessions = []
    for day in prior_days:
        rows = day_map.get(day) or []
        volumes = [_num(bar.get("v")) or 0.0 for _, bar in rows]
        total = sum(volumes)
        if total <= 0:
            continue
        cumulative = sum(volumes[: min(completed_bars, len(volumes))])
        sessions.append((total, min(1.0, max(0.0, cumulative / total))))
    if len(sessions) < 7:
        return None
    typical_daily = median(total for total, _ in sessions)
    expected_fraction = median(fraction for _, fraction in sessions)
    expected_volume = typical_daily * expected_fraction
    if expected_volume <= 0:
        return None
    return {
        "sample_count": len(sessions),
        "typical_daily_volume": typical_daily,
        "expected_fraction": expected_fraction,
        "expected_volume": expected_volume,
    }


def _impulse_snapshot(rows, idx):
    """Leakage-safe impulse/pullback features from replay bars through idx."""
    if idx < 5:
        return {}
    data=[]
    for _minute,b in rows[:idx+1]:
        h=_num(b.get("h")); l=_num(b.get("l")); cc=_num(b.get("c")); v=_num(b.get("v")) or 0.0
        if h is None or l is None or cc is None or h<=0 or l<=0:
            continue
        data.append({"h":h,"l":l,"c":cc,"v":v})
    if len(data)<6:
        return {}

    current=data[-1]["c"]
    candidates=[]
    n=len(data)
    for peak_idx in range(3,n-1):
        start=max(0,peak_idx-24)
        low_idx=min(range(start,peak_idx),key=lambda i:data[i]["l"])
        low=data[low_idx]["l"]; peak=data[peak_idx]["h"]
        if peak<=low:
            continue
        move=(peak/low-1)*100.0
        if move<6:
            continue
        after=data[peak_idx+1:]
        if not after:
            continue
        trough_rel=min(range(len(after)),key=lambda i:after[i]["l"])
        trough_idx=peak_idx+1+trough_rel
        trough=data[trough_idx]["l"]
        run=peak-low
        max_retrace=(peak-trough)/run*100.0
        current_retrace=(peak-current)/run*100.0
        recovery=max_retrace-current_retrace
        age=n-1-peak_idx
        recency=max(.35,1.0-age/max(12.0,n*.8))
        score=move*recency*(1.0 if 15<=max_retrace<=75 else .75)
        candidates.append((score,low_idx,peak_idx,trough_idx,move,current_retrace,max_retrace,recovery))
    if not candidates:
        return {}

    _,low_idx,peak_idx,trough_idx,move,current_retrace,max_retrace,recovery=max(candidates,key=lambda x:x[0])
    iv=[data[i]["v"] for i in range(low_idx,peak_idx+1) if data[i]["v"]>0]
    pv=[data[i]["v"] for i in range(peak_idx+1,trough_idx+1) if data[i]["v"]>0]
    iva=sum(iv)/len(iv) if iv else None
    pva=sum(pv)/len(pv) if pv else None
    ratio=pva/iva if iva and pva is not None else None
    return {
        "impulse_move_pct":round(move,3),
        "impulse_retracement_pct":round(current_retrace,3),
        "impulse_max_retracement_pct":round(max_retrace,3),
        "impulse_bounce_recovery_pct":round(recovery,3),
        "pullback_volume_ratio":round(ratio,4) if ratio is not None else None,
    }


def _current_snapshot(ss, symbol, rows, idx, prev_close, avg_daily, day_map, replay_day):
    if idx < 3 or idx >= len(rows):
        return None
    current = rows[idx][1]
    price = _num(current.get("c"))
    if price is None or price <= 0 or prev_close <= 0:
        return None

    completed = rows[: idx + 1]
    highs = [_num(bar.get("h")) for _, bar in completed]
    lows = [_num(bar.get("l")) for _, bar in completed]
    highs = [v for v in highs if v is not None and v > 0]
    lows = [v for v in lows if v is not None and v > 0]
    if not highs or not lows:
        return None

    session_high = max(highs)
    session_low = min(lows)
    session_volume = 0.0
    session_dollar = 0.0
    for _, bar in completed:
        volume = _num(bar.get("v")) or 0.0
        bar_price = _num(bar.get("vw")) or _num(bar.get("c")) or 0.0
        if volume > 0 and bar_price > 0:
            session_volume += volume
            session_dollar += volume * bar_price
    if session_volume <= 0:
        return None

    vwap = session_dollar / session_volume
    previous_5m = _num(rows[idx - 1][1].get("c"))
    previous_15m = _num(rows[idx - 3][1].get("c"))
    if previous_5m is None or previous_15m is None:
        return None

    profile = _profile_for_checkpoint(day_map, replay_day, idx + 1)
    if profile is None:
        return None
    pace = session_volume / profile["expected_volume"]
    checkpoint_minute = rows[idx][0] + 5
    checkpoint = datetime.combine(
        replay_day,
        dtime(checkpoint_minute // 60, checkpoint_minute % 60),
        tzinfo=ET,
    )

    impulse = _impulse_snapshot(rows, idx)
    completed_bars = [bar for _, bar in completed]
    sequence = detect_bounce_sequence(
        completed_bars,
        current_price=price,
        atr_pct=None,
    )
    bounce_features = bounce_feature_values(sequence)

    # Multi-session context built strictly from days before replay_day plus the
    # partial current day visible at this historical checkpoint.
    prior_daily=[]
    for prior_day in sorted(d for d in day_map if d < replay_day)[-20:]:
        day_rows=day_map.get(prior_day) or []
        if not day_rows:
            continue
        bars_only=[bar for _,bar in day_rows]
        opens=[_num(b.get("o")) for b in bars_only if _num(b.get("o")) is not None]
        highs_day=[_num(b.get("h")) for b in bars_only if _num(b.get("h")) is not None]
        lows_day=[_num(b.get("l")) for b in bars_only if _num(b.get("l")) is not None]
        closes=[_num(b.get("c")) for b in bars_only if _num(b.get("c")) is not None]
        vols_day=[_num(b.get("v")) or 0.0 for b in bars_only]
        if opens and highs_day and lows_day and closes:
            prior_daily.append({
                "t":prior_day.isoformat(),
                "o":opens[0],
                "h":max(highs_day),
                "l":min(lows_day),
                "c":closes[-1],
                "v":sum(vols_day),
            })
    current_open=_num(completed_bars[0].get("o")) if completed_bars else price
    stair=detect_stair_step(
        prior_daily,
        current_day={
            "date":replay_day.isoformat(),
            "o":current_open or price,
            "h":session_high,
            "l":session_low,
            "c":price,
            "v":session_volume,
        },
        atr_pct=None,
    )
    stair_features=stair_step_feature_values(stair)

    c = {
        "symbol": symbol,
        **impulse,
        **bounce_features,
        **stair_features,
        "market_session": "regular",
        "session_date": replay_day.isoformat(),
        "price": round(price, 4),
        "prev_close": round(prev_close, 4),
        "day_pct": round((price / prev_close - 1.0) * 100.0, 3),
        "dollar_volume": round(session_dollar, 2),
        "liquidity_source": "historical_tradier_replay",
        "liquidity_dollar_volume": round(session_dollar, 2),
        "live_quote_source": "historical_replay_no_quote",
        "live_intraday_source": "tradier_historical_5min_open",
        "spread_pct": None,
        "intraday_range_pct": round(
            (session_high / session_low - 1.0) * 100.0, 3
        ),
        "distance_from_high_pct": round(
            (session_high - price) / session_high * 100.0, 3
        ),
        "vwap": round(vwap, 4),
        "distance_from_vwap_pct": round(
            (price / vwap - 1.0) * 100.0, 3
        ),
        "above_vwap": bool(price > vwap),
        "momentum_5m": round((price / previous_5m - 1.0) * 100.0, 3),
        "momentum_15m": round((price / previous_15m - 1.0) * 100.0, 3),
        "volume_pace": round(pace, 4),
        "volume_pace_source": "historical_replay_tod_profile",
        "expected_volume_by_now": round(profile["expected_volume"], 2),
        "expected_volume_fraction_pct": round(
            profile["expected_fraction"] * 100.0, 3
        ),
        "volume_vs_expected_pct": round((pace - 1.0) * 100.0, 3),
        "volume_profile_samples": profile["sample_count"],
        "volume_pace_display": None,
        "volume_pace_display_source": "analyzer_aligned_regular",
        "news_bonus": None,
        "news_status": "not_reconstructed",
    }

    aligned = ss.analyzer_aligned_volume_pace(
        session_volume,
        avg_daily,
        checkpoint,
    )
    if aligned is not None:
        c["volume_pace_display"] = round(aligned, 4)

    failed = ss.evaluate_base_filters(c)
    c["failed_filters"] = failed
    c["failed_count"] = len(failed)
    c["passed_base_filters"] = not failed
    c["critical_fail_count"] = ss.critical_fail_count(c)
    c["base_score"] = ss.base_quality_score(c)
    c["live_bonus"] = ss.live_bonus_score(c)
    c["score"] = round(
        max(0.0, min(100.0, c["base_score"] + c["live_bonus"])),
        1,
    )
    c["setup_flags"] = ss.setup_risk_flags(c)
    c["warning_count"] = None

    confirmations = [
        c["momentum_5m"] >= ss.LIVE_A_MIN_5M,
        c["momentum_15m"] >= ss.LIVE_A_MIN_15M,
        c["volume_pace"] >= ss.LIVE_A_MIN_VOLUME_PACE,
        c["distance_from_high_pct"] <= ss.LIVE_A_MAX_FROM_HIGH_PCT,
        c["above_vwap"],
    ]
    c["live_confirmation_count"] = sum(confirmations)
    c["checkpoint"] = checkpoint
    return c


def _future_price(rows, idx, minutes=60):
    if idx < 0 or idx >= len(rows):
        return None
    target_minute = rows[idx][0] + minutes
    for minute, bar in rows[idx + 1 :]:
        if minute >= target_minute:
            return _num(bar.get("c"))
    return None


def build_replay_observations(
    ss,
    daily_index,
    intraday,
    replay_dates,
    daily_universes,
    daily_metrics,
    *,
    candidates_per_scan,
    scan_step_minutes,
):
    observations = []
    checkpoint_minutes = list(
        range(10 * 60 + 5, 15 * 60 + 1, max(5, scan_step_minutes))
    )

    for day_index, replay_day in enumerate(replay_dates, start=1):
        universe = daily_universes.get(replay_day) or []
        snapshots_by_checkpoint = defaultdict(list)

        for symbol in universe:
            rows = (intraday.get(symbol) or {}).get(replay_day) or []
            if len(rows) < 20:
                continue
            minute_to_idx = {minute: idx for idx, (minute, _) in enumerate(rows)}
            metrics = (daily_metrics.get(replay_day) or {}).get(symbol)
            if not metrics:
                continue
            prev_close = metrics["prev_close"]
            avg_daily = metrics["median_volume"]
            day_map = intraday.get(symbol) or {}

            for checkpoint_minute in checkpoint_minutes:
                bar_start_minute = checkpoint_minute - 5
                idx = minute_to_idx.get(bar_start_minute)
                if idx is None:
                    continue
                future_price = _future_price(rows, idx, 60)
                if future_price is None:
                    continue
                snap = _current_snapshot(
                    ss,
                    symbol,
                    rows,
                    idx,
                    prev_close,
                    avg_daily,
                    day_map,
                    replay_day,
                )
                if snap is None:
                    continue
                if not (ss.MIN_PRICE <= snap["price"] <= ss.MAX_PRICE):
                    continue
                if snap["day_pct"] < 2.0:
                    continue
                if snap["liquidity_dollar_volume"] < 500_000:
                    continue
                snapshots_by_checkpoint[checkpoint_minute].append(
                    (snap, future_price)
                )

        for checkpoint_minute, values in snapshots_by_checkpoint.items():
            values.sort(
                key=lambda item: (
                    item[0]["day_pct"],
                    item[0]["score"],
                    item[0]["liquidity_dollar_volume"],
                ),
                reverse=True,
            )
            chosen = values[:candidates_per_scan]
            checkpoint = datetime.combine(
                replay_day,
                dtime(checkpoint_minute // 60, checkpoint_minute % 60),
                tzinfo=ET,
            )
            scan_id = (
                f"historical-replay:{replay_day.isoformat()}:"
                f"{checkpoint:%H%M}"
            )

            for rank, (snap, future_price) in enumerate(chosen, start=1):
                entry_price = snap["price"]
                return_60 = (future_price / entry_price - 1.0) * 100.0
                observations.append(
                    {
                        "observation_id": (
                            f"replay:{replay_day.isoformat()}:"
                            f"{checkpoint:%H%M}:{snap['symbol']}"
                        ),
                        "observation_source": "historical_replay",
                        "replay_version": REPLAY_VERSION,
                        "feature_version": ss.SCANNER_FEATURE_VERSION,
                        "scan_id": scan_id,
                        "scan_time_et": checkpoint.isoformat(),
                        "rank": rank,
                        "symbol": snap["symbol"],
                        "entry_price": entry_price,
                        "day_pct": snap["day_pct"],
                        "score": snap["score"],
                        "base_score": snap["base_score"],
                        "live_bonus": snap["live_bonus"],
                        "news_bonus": None,
                        "opportunity_score": snap["score"],
                        "intraday_range_pct": snap["intraday_range_pct"],
                        "expected_volume_fraction_pct": snap[
                            "expected_volume_fraction_pct"
                        ],
                        "volume_vs_expected_pct": snap[
                            "volume_vs_expected_pct"
                        ],
                        "live_confirmation_count": snap[
                            "live_confirmation_count"
                        ],
                        "ml_continuation_prob_pct": None,
                        "ml_validated": False,
                        "ml_status": "historical_replay",
                        "setup_grade": None,
                        "setup_label": "HISTORICAL REPLAY",
                        "alert_tier": None,
                        "alert_ready": False,
                        "passed_base_filters": snap[
                            "passed_base_filters"
                        ],
                        "momentum_5m": snap["momentum_5m"],
                        "momentum_15m": snap["momentum_15m"],
                        "volume_pace": snap["volume_pace"],
                        "volume_pace_display": snap[
                            "volume_pace_display"
                        ],
                        "volume_pace_display_source": snap[
                            "volume_pace_display_source"
                        ],
                        "liquidity_dollar_volume": snap[
                            "liquidity_dollar_volume"
                        ],
                        "liquidity_source": "historical_tradier_replay",
                        "live_quote_source": "historical_replay_no_quote",
                        "live_intraday_source": "tradier_historical_5min_open",
                        "spread_pct": None,
                        "distance_from_high_pct": snap[
                            "distance_from_high_pct"
                        ],
                        "distance_from_vwap_pct": snap[
                            "distance_from_vwap_pct"
                        ],
                        "impulse_move_pct": snap.get("impulse_move_pct"),
                        "impulse_retracement_pct": snap.get("impulse_retracement_pct"),
                        "impulse_max_retracement_pct": snap.get("impulse_max_retracement_pct"),
                        "impulse_bounce_recovery_pct": snap.get("impulse_bounce_recovery_pct"),
                        "pullback_volume_ratio": snap.get("pullback_volume_ratio"),
                        "bounce_count": snap.get("bounce_count"),
                        "last_bounce_pct": snap.get("last_bounce_pct"),
                        "bounce_decay_ratio": snap.get("bounce_decay_ratio"),
                        "bounce_volume_decay_ratio": snap.get("bounce_volume_decay_ratio"),
                        "lower_high_streak": snap.get("lower_high_streak"),
                        "higher_low_streak": snap.get("higher_low_streak"),
                        "sequence_health_score": snap.get("sequence_health_score"),
                        "current_pullback_pct": snap.get("current_pullback_pct"),
                        "ongoing_bounce_pct": snap.get("ongoing_bounce_pct"),
                        "bounce_leg_code": snap.get("bounce_leg_code"),
                        "reference_peak_pct_above_dip": snap.get("reference_peak_pct_above_dip"),
                        "stair_step_count": snap.get("stair_step_count"),
                        "stair_last_step_pct": snap.get("stair_last_step_pct"),
                        "stair_step_acceleration_ratio": snap.get("stair_step_acceleration_ratio"),
                        "stair_plateau_days": snap.get("stair_plateau_days"),
                        "stair_plateau_range_pct": snap.get("stair_plateau_range_pct"),
                        "stair_plateau_retention_pct": snap.get("stair_plateau_retention_pct"),
                        "stair_plateau_volume_ratio": snap.get("stair_plateau_volume_ratio"),
                        "stair_higher_plateau_count": snap.get("stair_higher_plateau_count"),
                        "stair_structure_score": snap.get("stair_structure_score"),
                        "stair_reaccelerating": snap.get("stair_reaccelerating"),
                        "stair_breakdown": snap.get("stair_breakdown"),
                        "above_vwap": snap["above_vwap"],
                        "failed_filters": snap["failed_filters"],
                        "failed_count": snap["failed_count"],
                        "warning_count": None,
                        "setup_flags": snap["setup_flags"],
                        "news_status": "not_reconstructed",
                        "historical_status": "replay_source",
                        "return_60m_pct": round(return_60, 4),
                        "replay_resolution": "5Min",
                        "replay_missing_features": [
                            "spread_pct",
                            "news_bonus",
                            "historical_live_quote_freshness",
                        ],
                    }
                )

        print(
            f"Replay day {day_index}/{len(replay_dates)} "
            f"{replay_day}: cumulative observations={len(observations)}"
        )
    return observations


def main():
    import stock_scanner as ss

    trading_days = max(3, min(DEFAULT_TRADING_DAYS, 90))
    universe_size = max(100, min(DEFAULT_UNIVERSE_SIZE, 1200))
    candidates_per_scan = max(5, min(DEFAULT_CANDIDATES_PER_SCAN, 50))
    scan_step = max(5, min(DEFAULT_SCAN_STEP_MINUTES, 30))

    print(
        f"Historical scanner replay {REPLAY_VERSION}: "
        f"days={trading_days} universe={universe_size} "
        f"candidates/scan={candidates_per_scan} step={scan_step}m"
    )

    token = (
        os.environ.get("TRADIER_ACCESS_TOKEN", "").strip()
        or os.environ.get("TRADIER_TOKEN", "").strip()
    )
    if not token:
        raise RuntimeError(
            "Historical replay requires TRADIER_ACCESS_TOKEN or TRADIER_TOKEN."
        )

    symbols, asset_base = _load_nasdaq_symbol_directory(ss)
    print(f"Public US-stock directory: {len(symbols)} candidate symbols.")

    seed_size = min(
        MAX_UNION_SYMBOLS,
        max(universe_size, int(round(universe_size * 1.35))),
    )
    seed_symbols, quote_eligible = _select_seed_universe_from_tradier(
        symbols,
        token,
        seed_size,
    )
    if len(seed_symbols) < 100:
        raise RuntimeError(
            f"Tradier quote screening returned only {len(seed_symbols)} usable stocks."
        )
    print(
        f"Tradier replay seed universe: {len(seed_symbols)} stocks "
        f"from {quote_eligible} quote-eligible common stocks."
    )

    now_et = datetime.now(ET)
    daily_start = now_et - timedelta(days=max(120, trading_days * 4 + 50))
    daily_end = now_et
    daily_bars = _fetch_tradier_daily_history(
        seed_symbols,
        token,
        daily_start,
        daily_end,
    )
    daily_index = _daily_index(daily_bars)
    replay_dates = replay_trading_dates(
        daily_index,
        trading_days,
        now_et.date(),
    )
    if len(replay_dates) < 3:
        raise RuntimeError("Insufficient historical trading dates for replay.")

    daily_universes = {}
    daily_metrics = {}
    frequency = Counter()
    for replay_day in replay_dates:
        selected, metrics = select_daily_universe(
            daily_index,
            replay_day,
            universe_size,
        )
        daily_universes[replay_day] = selected
        daily_metrics[replay_day] = metrics
        frequency.update(selected)

    union = [
        symbol
        for symbol, _ in frequency.most_common(MAX_UNION_SYMBOLS)
    ]
    allowed = set(union)
    for day in replay_dates:
        daily_universes[day] = [
            symbol
            for symbol in daily_universes[day]
            if symbol in allowed
        ]

    all_dates = sorted(
        {
            day
            for rows in daily_index.values()
            for day, _ in rows
            if day < replay_dates[0]
        }
    )
    # Seven prior sessions are required by the volume-profile logic. Keep one
    # extra session when available while staying inside Tradier's 40-day 5m window.
    warmup_candidates = all_dates[-8:]
    warmup_day = (
        warmup_candidates[0]
        if warmup_candidates
        else replay_dates[0] - timedelta(days=12)
    )
    earliest_allowed = (now_et - timedelta(days=39)).date()
    if warmup_day < earliest_allowed:
        warmup_day = earliest_allowed

    intraday_start = datetime.combine(
        warmup_day,
        dtime(9, 30),
        tzinfo=ET,
    )
    intraday_end = datetime.combine(
        replay_dates[-1],
        dtime(16, 0),
        tzinfo=ET,
    )

    print(
        f"Fetching Tradier 5-minute open-session bars for {len(union)} "
        f"replay symbols from {warmup_day} through {replay_dates[-1]}."
    )
    intraday_bars = _fetch_tradier_intraday_history(
        union,
        token,
        intraday_start,
        intraday_end,
    )
    intraday = group_intraday(intraday_bars)

    observations = build_replay_observations(
        ss,
        daily_index,
        intraday,
        replay_dates,
        daily_universes,
        daily_metrics,
        candidates_per_scan=candidates_per_scan,
        scan_step_minutes=scan_step,
    )

    positives = sum(
        1 for row in observations
        if (_num(row.get("return_60m_pct")) or -999) >= 3.0
    )
    negatives = len(observations) - positives
    unique_scans = len({row.get("scan_id") for row in observations})

    payload = {
        "schema_version": 2,
        "tracker_version": REPLAY_VERSION,
        "feature_version": ss.SCANNER_FEATURE_VERSION,
        "source": "historical_scanner_replay",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "replay": {
            "trading_days": len(replay_dates),
            "start_date": replay_dates[0].isoformat(),
            "end_date": replay_dates[-1].isoformat(),
            "daily_universe_size": universe_size,
            "union_symbols": len(union),
            "candidates_per_scan": candidates_per_scan,
            "scan_step_minutes": scan_step,
            "bar_resolution": "5Min",
            "historical_feed": "TRADIER CONSOLIDATED HISTORICAL",
            "asset_universe_source": asset_base,
            "universe_method": (
                "public exchange symbol directory narrowed by current Tradier "
                "stock/liquidity metadata; each replay day then selected using "
                "only prior-day liquidity, prior-day momentum and prior-day relative volume"
            ),
            "known_limitations": [
                "current listed/liquid stock survivorship bias",
                "historical bid/ask spread not reconstructed",
                "historical news/catalyst score not reconstructed",
                "5-minute bars approximate live 1-minute momentum and impulse/retracement inputs",
            ],
        },
        "summary": {
            "observations": len(observations),
            "unique_scans": unique_scans,
            "positive_3pct_60m": positives,
            "non_positive_3pct_60m": negatives,
        },
        "observations": observations,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote replay dataset: {OUTPUT_PATH}")
    print(
        f"Observations={len(observations)} scans={unique_scans} "
        f"positive={positives} negative={negatives}"
    )


if __name__ == "__main__":
    main()
