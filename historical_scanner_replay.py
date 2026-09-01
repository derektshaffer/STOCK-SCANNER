"""Historical replay backfill for the Momentum Scanner ML model.

The replay uses only information available at each historical timestamp.
It intentionally does not fabricate historical bid/ask spreads or news.
Those fields remain missing and are handled as NaN by scanner_ml_ranker.

Output is a normal outcome_reports/outcomes_*.json payload so the existing
scanner ML loader discovers it automatically.
"""

from __future__ import annotations

import gzip
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
from market_structure import impulse_pullback_context as shared_impulse_pullback_context
from market_regime import BENCHMARKS, market_regime_features
from stair_step import detect_stair_step, stair_step_feature_values
from scanner_behavior import (
    BEHAVIOR_FEATURE_VERSION,
    intraday_behavior_features,
    multi_session_behavior_features,
)
from sequence_features import (
    SEQUENCE_BAR_FEATURES,
    SEQUENCE_INPUT_VERSION,
    SEQUENCE_MAX_BARS,
    build_causal_candle_sequence,
)
from historical_listing_universe import (
    exact_historical_universe,
    history_seed_candidates as historical_listing_seed_candidates,
    load_cached_historical_universes,
)

REPLAY_VERSION = "historical-scanner-replay-v5.0-path-target"
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
MAX_HISTORY_SEED_SYMBOLS = int(
    os.environ.get("REPLAY_MAX_HISTORY_SEED_SYMBOLS", "1200") or 1200
)
HISTORICAL_LISTING_SEED_BUDGET = max(
    0,
    min(
        int(os.environ.get("REPLAY_HISTORICAL_LISTING_SEED_BUDGET", "250") or 250),
        MAX_HISTORY_SEED_SYMBOLS,
    ),
)
UNIVERSE_SNAPSHOT_DIR = Path(
    os.environ.get("UNIVERSE_SNAPSHOT_DIR", "universe_snapshots")
)
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
SEQUENCE_OUTPUT_PATH = Path(
    os.environ.get(
        "REPLAY_SEQUENCE_OUTPUT_PATH",
        "outcome_reports/sequence_replay_training.json.gz",
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


def load_point_in_time_universe_snapshots(directory=None):
    """Load replay-ready universe snapshots in capture-date order."""
    directory = Path(directory or UNIVERSE_SNAPSHOT_DIR)
    rows = []
    if not directory.exists():
        return rows
    for path in sorted(directory.glob("universe_????-??-??.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            captured = date.fromisoformat(str(payload.get("captured_date_et") or ""))
        except Exception:
            continue
        if payload.get("replay_ready") is not True:
            continue
        seeds = [
            str(symbol).upper().strip()
            for symbol in payload.get("replay_seed_symbols") or []
            if str(symbol).strip()
        ]
        if not seeds:
            continue
        rows.append((captured, {**payload, "replay_seed_symbols": seeds}))
    rows.sort(key=lambda item: item[0])
    return rows


def point_in_time_seed_for_replay_day(replay_day, snapshots):
    """Use only a snapshot captured strictly before the replay session."""
    eligible = [
        item for item in (snapshots or [])
        if item[0] < replay_day
    ]
    if not eligible:
        return None
    return eligible[-1]


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


def select_daily_universe(daily_index, replay_day, size, allowed_symbols=None):
    rows = []
    allowed = (
        {str(symbol).upper().strip() for symbol in allowed_symbols}
        if allowed_symbols is not None
        else None
    )
    for symbol, history in daily_index.items():
        if allowed is not None and symbol not in allowed:
            continue
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
    """Leakage-safe shared-structure features from replay bars through idx."""
    if idx<5:
        return {}
    bars=[bar for _minute,bar in rows[:idx+1]]
    if len(bars)<6:
        return {}
    current=_num((bars[-1] or {}).get("c"))
    ctx=shared_impulse_pullback_context(bars,current_price=current)
    if not ctx.get("detected"):
        return {}
    return {
        "impulse_move_pct":_num(ctx.get("impulse_move_pct")),
        "impulse_retracement_pct":_num(ctx.get("current_retracement_pct")),
        "impulse_max_retracement_pct":_num(ctx.get("max_retracement_pct")),
        "impulse_bounce_recovery_pct":_num(ctx.get("bounce_recovery_pct")),
        "pullback_volume_ratio":_num(ctx.get("pullback_volume_ratio")),
        "structure_version":ctx.get("structure_version"),
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

    completed_bars = [bar for _, bar in completed]
    behavior_features = intraday_behavior_features(
        completed_bars,
        current_price=price,
        atr_pct=None,
        as_of=checkpoint.astimezone(timezone.utc),
        completed_only=True,
    )

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
    stair_features = multi_session_behavior_features(
        prior_daily,
        current_day={
            "date": replay_day.isoformat(),
            "o": current_open or price,
            "h": session_high,
            "l": session_low,
            "c": price,
            "v": session_volume,
        },
        atr_pct=None,
    )

    c = {
        "symbol": symbol,
        **behavior_features,
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


def _future_trade_quality(
    rows,
    idx,
    entry_price,
    *,
    minutes=60,
    target_pct=1.0,
    stop_pct=0.75,
):
    """Causal path outcome after a replay checkpoint.

    Same-bar target+stop touches are resolved stop-first to avoid flattering
    results when 5-minute OHLC cannot reveal the true intrabar order.
    """
    if idx < 0 or idx >= len(rows) or not entry_price:
        return {}
    end_minute = rows[idx][0] + minutes
    target = entry_price * (1.0 + target_pct / 100.0)
    stop = entry_price * (1.0 - stop_pct / 100.0)
    mfe = 0.0
    mae = 0.0
    barrier = "neither"
    bars_seen = 0

    for minute, bar in rows[idx + 1 :]:
        if minute > end_minute:
            break
        high = _num(bar.get("h"))
        low = _num(bar.get("l"))
        if high is None or low is None:
            continue
        bars_seen += 1
        mfe = max(mfe, (high / entry_price - 1.0) * 100.0)
        mae = min(mae, (low / entry_price - 1.0) * 100.0)
        hit_target = high >= target
        hit_stop = low <= stop
        if hit_stop:
            barrier = "stop_first"
            break
        if hit_target:
            barrier = "target_first"
            break

    decisive = barrier in {"target_first", "stop_first"}
    return {
        "trade_quality_target_pct": target_pct,
        "trade_quality_stop_pct": stop_pct,
        "trade_quality_horizon_minutes": minutes,
        "trade_quality_barrier": barrier,
        "trade_quality_decisive": decisive,
        "target_before_stop": (
            barrier == "target_first"
            if decisive
            else None
        ),
        "mfe_60m_pct": round(mfe, 4),
        "mae_60m_pct": round(mae, 4),
        "trade_quality_bars_seen": bars_seen,
    }


def _future_opportunity_path(
    rows,
    idx,
    entry_price,
    *,
    minutes=60,
    thresholds=(3.0, 5.0, 10.0, 20.0),
    failure_stop_pct=3.0,
):
    """Full future path for research-only learning-objective challenges.

    Unlike _future_trade_quality, this does not stop at the first 1%/-0.75%
    barrier. It preserves the complete 60-minute path so a large interim winner
    cannot be hidden by a weaker endpoint. Same-bar threshold/stop ordering is
    conservative: the failure stop wins ties.
    """
    result = {
        "opportunity_horizon_60m_complete": False,
        "opportunity_mfe_60m_pct": None,
        "opportunity_mae_60m_pct": None,
        "opportunity_time_to_peak_60m": None,
        "opportunity_failure_stop_pct": float(failure_stop_pct),
        "opportunity_failure_stop_60m_hit": False,
        "opportunity_failure_stop_60m_time": None,
        "opportunity_bars_seen_60m": 0,
    }
    for threshold in thresholds:
        key = str(int(threshold))
        result[f"opportunity_up_{key}_60m_hit"] = False
        result[f"opportunity_up_{key}_60m_time"] = None
        result[f"opportunity_up_{key}_60m_before_stop"] = None

    if idx < 0 or idx >= len(rows) or not entry_price:
        return result

    start_minute = rows[idx][0]
    end_minute = start_minute + minutes
    entry_price = float(entry_price)
    mfe = 0.0
    mae = 0.0
    peak_minute = None
    stop_minute = None
    threshold_minutes = {float(value): None for value in thresholds}
    last_minute = None

    for minute, bar in rows[idx + 1 :]:
        if minute > end_minute:
            break
        high = _num(bar.get("h"))
        low = _num(bar.get("l"))
        if high is None or low is None:
            continue
        last_minute = minute
        result["opportunity_bars_seen_60m"] += 1

        high_ret = (high / entry_price - 1.0) * 100.0
        low_ret = (low / entry_price - 1.0) * 100.0
        if high_ret > mfe:
            mfe = high_ret
            peak_minute = minute
        if low_ret < mae:
            mae = low_ret

        if stop_minute is None and low_ret <= -float(failure_stop_pct):
            stop_minute = minute

        for threshold in thresholds:
            threshold = float(threshold)
            if (
                threshold_minutes[threshold] is None
                and high_ret >= threshold
            ):
                threshold_minutes[threshold] = minute

    if result["opportunity_bars_seen_60m"] == 0:
        return result

    result["opportunity_horizon_60m_complete"] = bool(
        last_minute is not None and last_minute >= end_minute
    )
    result["opportunity_mfe_60m_pct"] = round(mfe, 4)
    result["opportunity_mae_60m_pct"] = round(mae, 4)
    if peak_minute is not None:
        result["opportunity_time_to_peak_60m"] = round(
            max(0.0, peak_minute - start_minute),
            2,
        )
    if stop_minute is not None:
        result["opportunity_failure_stop_60m_hit"] = True
        result["opportunity_failure_stop_60m_time"] = round(
            max(0.0, stop_minute - start_minute),
            2,
        )

    for threshold in thresholds:
        threshold = float(threshold)
        key = str(int(threshold))
        hit_minute = threshold_minutes[threshold]
        if hit_minute is None:
            continue
        result[f"opportunity_up_{key}_60m_hit"] = True
        result[f"opportunity_up_{key}_60m_time"] = round(
            max(0.0, hit_minute - start_minute),
            2,
        )
        if stop_minute is None:
            result[f"opportunity_up_{key}_60m_before_stop"] = True
        else:
            result[f"opportunity_up_{key}_60m_before_stop"] = (
                hit_minute < stop_minute
            )

    return result


def _benchmark_regimes(benchmark_bars, replay_dates):
    """Causal regime map using only completed benchmark days before replay_day."""
    regimes = {}
    for replay_day in replay_dates:
        histories = {}
        for symbol in BENCHMARKS:
            histories[symbol] = [
                bar
                for bar in (benchmark_bars.get(symbol) or [])
                if (
                    _bar_date_et(bar) is not None
                    and _bar_date_et(bar) < replay_day
                )
            ]
        regimes[replay_day] = market_regime_features(histories)
    return regimes


def _action_outcome_stats(rows):
    rows = list(rows or [])
    decisive = [
        row for row in rows
        if row.get("trade_quality_decisive")
    ]
    target_first = sum(
        row.get("target_before_stop") is True
        for row in decisive
    )
    returns = [
        float(row["return_60m_pct"])
        for row in rows
        if row.get("return_60m_pct") is not None
    ]
    mfe = [
        float(row["mfe_60m_pct"])
        for row in rows
        if row.get("mfe_60m_pct") is not None
    ]
    mae = [
        float(row["mae_60m_pct"])
        for row in rows
        if row.get("mae_60m_pct") is not None
    ]
    return {
        "n": len(rows),
        "decisive_n": len(decisive),
        "target_first_n": target_first,
        "stop_first_n": len(decisive) - target_first,
        "target_before_stop_rate_pct": (
            round(target_first / len(decisive) * 100.0, 2)
            if decisive
            else None
        ),
        "hit_3pct_60m_rate_pct": (
            round(
                sum((row.get("return_60m_pct") or 0) >= 3.0 for row in rows)
                / len(rows)
                * 100.0,
                2,
            )
            if rows
            else None
        ),
        "median_return_60m_pct": round(median(returns), 3) if returns else None,
        "median_mfe_60m_pct": round(median(mfe), 3) if mfe else None,
        "median_mae_60m_pct": round(median(mae), 3) if mae else None,
    }


def _action_priority(label):
    return {
        "NO TRADE": -2,
        "CAUTION": -1,
        "WAIT": -1,
        "WAIT PULLBACK": 0,
        "WATCH": 1,
        "EXTENDED WATCH": 1,
        "BOUNCE WATCH": 2,
        "BREAKOUT WATCH": 2,
        "ANALYZE NOW": 3,
    }.get(str(label or "").upper(), 0)


def _action_system_summary(observations, label_key):
    groups = defaultdict(list)
    for row in observations:
        groups[str(row.get(label_key) or "UNKNOWN")].append(row)

    entry = groups.get("ANALYZE NOW", [])
    non_entry = [
        row for row in observations
        if str(row.get(label_key) or "") != "ANALYZE NOW"
    ]
    entry_stats = _action_outcome_stats(entry)
    non_entry_stats = _action_outcome_stats(non_entry)
    entry_rate = entry_stats.get("target_before_stop_rate_pct")
    non_entry_rate = non_entry_stats.get("target_before_stop_rate_pct")

    return {
        "by_label": {
            label: _action_outcome_stats(rows)
            for label, rows in sorted(groups.items())
        },
        "analyze_now": entry_stats,
        "not_entry_ready": non_entry_stats,
        "analyze_now_quality_lift_pp": (
            round(entry_rate - non_entry_rate, 2)
            if entry_rate is not None and non_entry_rate is not None
            else None
        ),
        "analyze_now_share_pct": (
            round(len(entry) / len(observations) * 100.0, 2)
            if observations
            else None
        ),
    }


def _action_benchmark(observations):
    changed = [
        row for row in observations
        if row.get("behavior_action_label") != row.get("legacy_action_label")
    ]
    upgraded = [
        row for row in changed
        if _action_priority(row.get("behavior_action_label"))
        > _action_priority(row.get("legacy_action_label"))
    ]
    downgraded = [
        row for row in changed
        if _action_priority(row.get("behavior_action_label"))
        < _action_priority(row.get("legacy_action_label"))
    ]
    neutral_change = [
        row for row in changed
        if _action_priority(row.get("behavior_action_label"))
        == _action_priority(row.get("legacy_action_label"))
    ]
    return {
        "comparison": "current behavior-aware ACTION vs simpler pre-behavior ACTION",
        "observations": len(observations),
        "current": _action_system_summary(
            observations,
            "behavior_action_label",
        ),
        "legacy": _action_system_summary(
            observations,
            "legacy_action_label",
        ),
        "paired_changes": {
            "changed_n": len(changed),
            "changed_pct": (
                round(len(changed) / len(observations) * 100.0, 2)
                if observations
                else None
            ),
            "upgraded_n": len(upgraded),
            "downgraded_n": len(downgraded),
            "neutral_label_change_n": len(neutral_change),
            "upgraded_outcomes": _action_outcome_stats(upgraded),
            "downgraded_outcomes": _action_outcome_stats(downgraded),
            "neutral_change_outcomes": _action_outcome_stats(neutral_change),
        },
        "limitations": [
            "Historical bid/ask spread is not reconstructed, so both action systems are compared without historical spread warnings.",
            "Historical catalysts/news are not reconstructed; missing news is treated as neutral for both systems.",
            "Both systems see identical causal price, volume, VWAP, bounce, breakout and stair-step information at each checkpoint.",
        ],
    }


def build_replay_observations(
    ss,
    daily_index,
    intraday,
    replay_dates,
    daily_universes,
    daily_metrics,
    daily_regimes,
    *,
    candidates_per_scan,
    scan_step_minutes,
):
    observations = []
    sequence_records = []
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
                symbol_rows = (intraday.get(snap["symbol"]) or {}).get(replay_day) or []
                symbol_index = {
                    minute: i
                    for i, (minute, _bar) in enumerate(symbol_rows)
                }
                replay_idx = symbol_index.get(checkpoint_minute - 5, -1)
                quality = _future_trade_quality(
                    symbol_rows,
                    replay_idx,
                    entry_price,
                )
                opportunity_path = _future_opportunity_path(
                    symbol_rows,
                    replay_idx,
                    entry_price,
                )
                path_before_stop = opportunity_path.get(
                    "opportunity_up_3_60m_before_stop"
                )
                path_hit = opportunity_path.get("opportunity_up_3_60m_hit")
                path_complete = opportunity_path.get(
                    "opportunity_horizon_60m_complete"
                )
                if path_complete is True and path_before_stop is True:
                    research_path_success_60m = 1
                elif (
                    path_complete is True
                    and (path_before_stop is False or path_hit is False)
                ):
                    research_path_success_60m = 0
                else:
                    research_path_success_60m = None
                research_endpoint_success_60m = int(return_60 >= 3.0)
                research_endpoint_path_disagreement_60m = (
                    research_path_success_60m is not None
                    and research_endpoint_success_60m
                    != research_path_success_60m
                )
                regime = daily_regimes.get(replay_day) or {}

                action_row = dict(snap)
                action_row["news_bonus"] = 0.0
                action_row["tradability_warnings"] = []
                action_row["warning_count"] = 0
                ss.assign_setup_grade(action_row, checkpoint)
                behavior_action = ss.scanner_action_signal(
                    action_row,
                    checkpoint,
                    use_behavior=True,
                )
                legacy_action = ss.scanner_action_signal(
                    action_row,
                    checkpoint,
                    use_behavior=False,
                )

                observation_id=(
                    f"replay:{replay_day.isoformat()}:"
                    f"{checkpoint:%H%M}:{snap['symbol']}"
                )
                sequence_idx=symbol_index.get(checkpoint_minute - 5, -1)
                sequence_payload=build_causal_candle_sequence(
                    symbol_rows,
                    sequence_idx,
                    max_bars=SEQUENCE_MAX_BARS,
                )
                if sequence_payload:
                    sequence_records.append(
                        {
                            "observation_id":observation_id,
                            "session_date":replay_day.isoformat(),
                            "scan_time_et":checkpoint.isoformat(),
                            "symbol":snap["symbol"],
                            "bars_available":sequence_payload.get("bars_available"),
                            "sequence":sequence_payload.get("sequence"),
                        }
                    )

                observations.append(
                    {
                        **quality,
                        **opportunity_path,
                        "research_path_success_60m": research_path_success_60m,
                        "research_endpoint_success_60m": research_endpoint_success_60m,
                        "research_endpoint_path_disagreement_60m": (
                            research_endpoint_path_disagreement_60m
                        ),
                        "research_path_target_description": (
                            ">= +3% within 60m before -3% failure stop"
                        ),
                        "regime_label": regime.get("regime_label"),
                        "regime_score": regime.get("regime_score"),
                        "spy_return_5d_pct": regime.get("spy_return_5d_pct"),
                        "spy_return_20d_pct": regime.get("spy_return_20d_pct"),
                        "spy_realized_vol_20d_pct": regime.get("spy_realized_vol_20d_pct"),
                        "iwm_minus_spy_20d_pct": regime.get("iwm_minus_spy_20d_pct"),
                        "observation_id": observation_id,
                        "observation_source": "historical_replay",
                        "replay_version": REPLAY_VERSION,
                        "feature_version": ss.SCANNER_FEATURE_VERSION,
                        "behavior_feature_version": BEHAVIOR_FEATURE_VERSION,
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
                        "benchmark_setup_grade": action_row.get("setup_grade"),
                        "behavior_action_label": behavior_action.get("label"),
                        "behavior_action_tier": behavior_action.get("tier"),
                        "behavior_action_reason": behavior_action.get("reason"),
                        "legacy_action_label": legacy_action.get("label"),
                        "legacy_action_tier": legacy_action.get("tier"),
                        "legacy_action_reason": legacy_action.get("reason"),
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
                        "pullback_quality_score": snap.get("pullback_quality_score"),
                        "vwap_hold_ratio_10": snap.get("vwap_hold_ratio_10"),
                        "vwap_reclaim": snap.get("vwap_reclaim"),
                        "vwap_rejection": snap.get("vwap_rejection"),
                        "vwap_state_code": snap.get("vwap_state_code"),
                        "vwap_crosses_10": snap.get("vwap_crosses_10"),
                        "volume_acceleration_ratio": snap.get("volume_acceleration_ratio"),
                        "volume_accelerating": snap.get("volume_accelerating"),
                        "volume_contracting": snap.get("volume_contracting"),
                        "breakout_recent": snap.get("breakout_recent"),
                        "breakout_holding": snap.get("breakout_holding"),
                        "failed_breakout": snap.get("failed_breakout"),
                        "breakout_extension_pct": snap.get("breakout_extension_pct"),
                        "breakout_bars_since": snap.get("breakout_bars_since"),
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
                        "stair_reacceleration_developing": snap.get("stair_reacceleration_developing"),
                        "stair_breakdown": snap.get("stair_breakdown"),
                        "stair_breakdown_confirmed": snap.get("stair_breakdown_confirmed"),
                        "stair_breakdown_developing": snap.get("stair_breakdown_developing"),
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
    return observations, sequence_records


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
    current_seed_symbols, quote_eligible = _select_seed_universe_from_tradier(
        symbols,
        token,
        seed_size,
    )
    if len(current_seed_symbols) < 100:
        raise RuntimeError(
            f"Tradier quote screening returned only {len(current_seed_symbols)} usable stocks."
        )

    point_in_time_snapshots = load_point_in_time_universe_snapshots()
    historical_listing_snapshots = load_cached_historical_universes()

    # Reserve a bounded slice of the history-fetch budget for symbols that were
    # members of exact historical listing universes but are absent from today's
    # public directory. This gives delisted/transient names a real chance to
    # enter replay instead of using historical membership as metadata only.
    historical_listing_seed = historical_listing_seed_candidates(
        historical_listing_snapshots,
        exclude_symbols=symbols,
        budget=HISTORICAL_LISTING_SEED_BUDGET,
    )

    snapshot_frequency = Counter()
    for _captured, snapshot in point_in_time_snapshots:
        snapshot_frequency.update(snapshot.get("replay_seed_symbols") or [])

    history_seed_symbols = list(historical_listing_seed)
    history_seen = set(history_seed_symbols)
    for symbol, _count in snapshot_frequency.most_common(
        max(0, MAX_HISTORY_SEED_SYMBOLS)
    ):
        if symbol in history_seen:
            continue
        history_seen.add(symbol)
        history_seed_symbols.append(symbol)
        if len(history_seed_symbols) >= MAX_HISTORY_SEED_SYMBOLS:
            break
    for symbol in current_seed_symbols:
        if symbol in history_seen:
            continue
        history_seen.add(symbol)
        history_seed_symbols.append(symbol)
        if len(history_seed_symbols) >= MAX_HISTORY_SEED_SYMBOLS:
            break
    history_seed_symbols = history_seed_symbols[:MAX_HISTORY_SEED_SYMBOLS]

    print(
        f"Tradier current replay seed: {len(current_seed_symbols)} stocks "
        f"from {quote_eligible} quote-eligible common stocks; "
        f"history seed={len(history_seed_symbols)} with "
        f"{len(point_in_time_snapshots)} prospective snapshots and "
        f"{len(historical_listing_snapshots)} exact historical listing snapshots "
        f"({len(historical_listing_seed)} historical-only seed slots)."
    )

    now_et = datetime.now(ET)
    daily_start = now_et - timedelta(days=max(120, trading_days * 4 + 50))
    daily_end = now_et
    daily_bars = _fetch_tradier_daily_history(
        history_seed_symbols,
        token,
        daily_start,
        daily_end,
    )
    daily_index = _daily_index(daily_bars)

    benchmark_start = now_et - timedelta(days=420)
    benchmark_bars = _fetch_tradier_daily_history(
        list(BENCHMARKS),
        token,
        benchmark_start,
        daily_end,
    )
    replay_dates = replay_trading_dates(
        daily_index,
        trading_days,
        now_et.date(),
    )
    if len(replay_dates) < 3:
        raise RuntimeError("Insufficient historical trading dates for replay.")

    daily_regimes = _benchmark_regimes(benchmark_bars, replay_dates)

    daily_universes = {}
    daily_metrics = {}
    daily_universe_sources = {}
    frequency = Counter()
    snapshot_covered_dates = []
    historical_listing_covered_dates = []
    for replay_day in replay_dates:
        exact_listing = exact_historical_universe(
            replay_day,
            historical_listing_snapshots,
        )
        snapshot_match = point_in_time_seed_for_replay_day(
            replay_day,
            point_in_time_snapshots,
        )
        if exact_listing is not None:
            allowed_symbols = exact_listing.get("symbols") or []
            historical_listing_covered_dates.append(replay_day)
            daily_universe_sources[replay_day] = {
                "mode": "exact_historical_listing_snapshot",
                "as_of_date": replay_day.isoformat(),
                "source": exact_listing.get("source"),
                "seed_count": len(allowed_symbols),
            }
        elif snapshot_match is not None:
            captured_date, snapshot = snapshot_match
            allowed_symbols = snapshot.get("replay_seed_symbols") or []
            snapshot_covered_dates.append(replay_day)
            daily_universe_sources[replay_day] = {
                "mode": "point_in_time_snapshot",
                "captured_date": captured_date.isoformat(),
                "seed_count": len(allowed_symbols),
            }
        else:
            # Keep fallback semantics honest: only today's screened seed is
            # eligible when no historical membership evidence exists.
            allowed_symbols = current_seed_symbols
            daily_universe_sources[replay_day] = {
                "mode": "current_universe_fallback",
                "captured_date": None,
                "seed_count": len(current_seed_symbols),
            }

        selected, metrics = select_daily_universe(
            daily_index,
            replay_day,
            universe_size,
            allowed_symbols=allowed_symbols,
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

    observations, sequence_records = build_replay_observations(
        ss,
        daily_index,
        intraday,
        replay_dates,
        daily_universes,
        daily_metrics,
        daily_regimes,
        candidates_per_scan=candidates_per_scan,
        scan_step_minutes=scan_step,
    )

    positives = sum(
        1 for row in observations
        if (_num(row.get("return_60m_pct")) or -999) >= 3.0
    )
    negatives = len(observations) - positives
    unique_scans = len({row.get("scan_id") for row in observations})
    quality_decisive = [
        row for row in observations
        if row.get("trade_quality_decisive")
    ]
    quality_target_first = sum(
        row.get("target_before_stop") is True
        for row in quality_decisive
    )
    quality_stop_first = len(quality_decisive) - quality_target_first
    quality_neither = sum(
        row.get("trade_quality_barrier") == "neither"
        for row in observations
    )
    action_benchmark = _action_benchmark(observations)

    payload = {
        "schema_version": 3,
        "tracker_version": REPLAY_VERSION,
        "path_target": ">= +3% within 60m before -3% failure stop",
        "feature_version": ss.SCANNER_FEATURE_VERSION,
        "source": "historical_scanner_replay",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "replay": {
            "trading_days": len(replay_dates),
            "start_date": replay_dates[0].isoformat(),
            "end_date": replay_dates[-1].isoformat(),
            "daily_universe_size": universe_size,
            "union_symbols": len(union),
            "union_symbol_list": union,
            "candidates_per_scan": candidates_per_scan,
            "scan_step_minutes": scan_step,
            "bar_resolution": "5Min",
            "historical_feed": "TRADIER CONSOLIDATED HISTORICAL",
            "asset_universe_source": asset_base,
            "historical_listing_snapshot_count": len(historical_listing_snapshots),
            "historical_listing_replay_dates": len(historical_listing_covered_dates),
            "historical_listing_seed_budget": HISTORICAL_LISTING_SEED_BUDGET,
            "historical_listing_seed_requested": len(historical_listing_seed),
            "historical_listing_seed_with_daily_history": sum(
                symbol in daily_index for symbol in historical_listing_seed
            ),
            "point_in_time_snapshot_count": len(point_in_time_snapshots),
            "point_in_time_replay_dates": len(snapshot_covered_dates),
            "current_universe_fallback_dates": (
                len(replay_dates)
                - len(historical_listing_covered_dates)
                - len(snapshot_covered_dates)
            ),
            "survivorship_mitigated_coverage_pct": round(
                (
                    len(historical_listing_covered_dates)
                    + len(snapshot_covered_dates)
                )
                / len(replay_dates)
                * 100.0,
                1,
            ) if replay_dates else 0.0,
            "daily_universe_sources": {
                day.isoformat(): daily_universe_sources.get(day)
                for day in replay_dates
            },
            "universe_method": (
                "Universe precedence is exact historical-date listing membership "
                "when cached, otherwise the latest replay-ready snapshot captured "
                "strictly before the session, otherwise today's screened universe. "
                "Within the eligible membership, ranking uses only prior-day "
                "liquidity, prior-day momentum and prior-day relative volume."
            ),
            "known_limitations": (
                [
                    "historical bid/ask spread not reconstructed",
                    "historical news/catalyst score not reconstructed",
                    "5-minute bars approximate live 1-minute momentum and impulse/retracement inputs",
                ]
                + (
                    ["current listed/liquid stock survivorship bias remains on replay dates without exact historical or prior prospective membership coverage"]
                    if (
                        len(historical_listing_covered_dates)
                        + len(snapshot_covered_dates)
                    ) < len(replay_dates)
                    else []
                )
                + (
                    ["historical membership can include delisted symbols that the market-history provider may not return; coverage metadata reports how many bounded historical seed symbols actually produced daily history"]
                    if historical_listing_covered_dates
                    else []
                )
            ),
        },
        "summary": {
            "observations": len(observations),
            "unique_scans": unique_scans,
            "positive_3pct_60m": positives,
            "non_positive_3pct_60m": negatives,
            "trade_quality_decisive": len(quality_decisive),
            "trade_quality_target_first": quality_target_first,
            "trade_quality_stop_first": quality_stop_first,
            "trade_quality_neither": quality_neither,
            "target_before_stop_rate_pct": (
                round(quality_target_first / len(quality_decisive) * 100.0, 2)
                if quality_decisive
                else None
            ),
            "action_benchmark": action_benchmark,
        },
        "observations": observations,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote replay dataset: {OUTPUT_PATH}")

    sequence_artifact={
        "schema_version":1,
        "sequence_version":SEQUENCE_INPUT_VERSION,
        "source":"historical_scanner_replay",
        "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "bar_resolution":"5Min",
        "max_bars":SEQUENCE_MAX_BARS,
        "bar_feature_names":list(SEQUENCE_BAR_FEATURES),
        "observations":len(sequence_records),
        "records":sequence_records,
        "integrity":{
            "causal_cutoff":"every sequence ends at the matching replay decision candle",
            "future_bars_in_sequence":False,
            "labels_stored_separately":True,
        },
    }
    SEQUENCE_OUTPUT_PATH.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(SEQUENCE_OUTPUT_PATH,"wt",encoding="utf-8") as handle:
        json.dump(sequence_artifact,handle,separators=(",",":"))
    print(
        f"Wrote sequence research dataset: {SEQUENCE_OUTPUT_PATH} "
        f"records={len(sequence_records)}"
    )
    print(
        f"Observations={len(observations)} scans={unique_scans} "
        f"positive={positives} negative={negatives} "
        f"quality_decisive={len(quality_decisive)} "
        f"target_first={quality_target_first} stop_first={quality_stop_first}"
    )
    print("ACTION_BENCHMARK=" + json.dumps(action_benchmark, sort_keys=True))


if __name__ == "__main__":
    main()
