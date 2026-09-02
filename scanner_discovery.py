"""Full-market, lightweight discovery for the Momentum / Explosive Scanner.

The expensive Analyzer-style work is deliberately *not* performed here.  This
module sweeps every supported US exchange-listed common stock with batched
Tradier quotes, keeps a compact rolling observation state, and returns only the
strongest ignition candidates for deeper enrichment.

Detection and tradeability are separate:

* ``explosion_score`` asks whether price/participation are accelerating.
* ``tradeability_score`` asks whether the quote/liquidity look executable.

A low tradeability score never hides a genuine explosion; it changes the risk
label and downstream action instead.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from live_price_quality import parse_market_timestamp
from tradier_live import post_quotes


ET = ZoneInfo("America/New_York")
CACHE_DIR = Path(
    os.environ.get("SCANNER_DISCOVERY_CACHE_DIR", "scan_cache").strip()
    or "scan_cache"
)
UNIVERSE_CACHE_PATH = CACHE_DIR / "tradier_full_market_universe.json"
STATE_PATH = CACHE_DIR / "tradier_full_market_radar_state.json"
CACHE_SCHEMA_VERSION = 3
STATE_SCHEMA_VERSION = 1

# Tradier's POST quote endpoint is intended for larger symbol batches.  Keep
# batches comfortably bounded and leave a small delay so a full sweep coexists
# with deeper Time & Sales requests inside the provider's rate limits.
QUOTE_BATCH_SIZE = int(
    os.environ.get("SCANNER_DISCOVERY_QUOTE_BATCH_SIZE", "300") or 300
)
REQUEST_DELAY_SECONDS = float(
    os.environ.get("SCANNER_DISCOVERY_REQUEST_DELAY_SECONDS", "0.10") or 0.10
)

MIN_RADAR_PRICE = float(
    os.environ.get("SCANNER_RADAR_MIN_PRICE", "0.10") or 0.10
)
MAX_QUOTE_AGE_SECONDS = float(
    os.environ.get("SCANNER_RADAR_MAX_QUOTE_AGE_SECONDS", "180") or 180
)
STATE_RETENTION_SECONDS = float(
    os.environ.get("SCANNER_RADAR_STATE_RETENTION_SECONDS", "900") or 900
)
STATE_POINTS_PER_SYMBOL = max(
    3,
    int(os.environ.get("SCANNER_RADAR_STATE_POINTS", "8") or 8),
)
MIN_COVERAGE_PCT = float(
    os.environ.get("SCANNER_RADAR_MIN_COVERAGE_PCT", "75") or 75
)
DEFAULT_RETURNED_CANDIDATES = int(
    os.environ.get("SCANNER_RADAR_CANDIDATES", "180") or 180
)

_LAST_DISCOVERY_META: dict = {}


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def _scale(value, start, end, points):
    value = _num(value)
    if value is None or end <= start:
        return 0.0
    return _clamp((value - start) / (end - start)) * points


def _pct_change(new, old):
    new = _num(new)
    old = _num(old)
    if new is None or old in (None, 0):
        return None
    return (new / old - 1.0) * 100.0


def _chunks(values, size):
    for start in range(0, len(values), max(1, int(size))):
        yield values[start : start + max(1, int(size))]


def _atomic_json_write(path: Path, payload, *, indent=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp.write_text(
        json.dumps(payload, indent=indent, separators=None if indent else (",", ":")),
        encoding="utf-8",
    )
    os.replace(tmp, path)


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


def _load_public_symbols(likely_common_stock):
    """Load the broad US-listed common-stock directory without broker auth."""
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
            headers={"User-Agent": "stock-scanner-full-market-radar/1.0"},
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
            if not likely_common_stock(symbol):
                continue

            seen.add(symbol)
            symbols.append(symbol)

    if not symbols:
        raise RuntimeError("Public Nasdaq Trader directory returned no stock symbols.")
    return sorted(symbols)


def _read_universe_cache(*, require_today=True):
    try:
        payload = json.loads(UNIVERSE_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    if int(payload.get("schema_version") or 0) != CACHE_SCHEMA_VERSION:
        return None
    if require_today and payload.get("market_date") != datetime.now(ET).date().isoformat():
        return None
    symbols = [
        str(symbol).upper().strip()
        for symbol in payload.get("symbols") or []
        if str(symbol).strip()
    ]
    return (symbols, payload) if len(symbols) >= 1000 else None


def get_or_build_discovery_universe(token, likely_common_stock):
    """Return the *full* exchange-listed common-stock directory.

    ``token`` is accepted for backward compatibility; building the directory
    itself does not consume a broker request.  Quotes are fetched in the radar
    sweep that follows.
    """
    del token
    market_date = datetime.now(ET).date().isoformat()
    cached = _read_universe_cache(require_today=True)
    if cached:
        symbols, payload = cached
        return symbols, {
            "source": "nasdaqtrader_full_market_cache",
            "market_date": market_date,
            "symbols": len(symbols),
            "cache_hit": True,
            "directory_generated_at_et": payload.get("generated_at_et"),
            "directory_stale_fallback": False,
        }

    try:
        symbols = _load_public_symbols(likely_common_stock)
        payload = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "market_date": market_date,
            "generated_at_et": datetime.now(ET).isoformat(),
            "source": "nasdaqtrader_public_full_market_directory",
            "symbols": symbols,
        }
        _atomic_json_write(UNIVERSE_CACHE_PATH, payload, indent=2)
        return symbols, {
            "source": payload["source"],
            "market_date": market_date,
            "symbols": len(symbols),
            "cache_hit": False,
            "directory_generated_at_et": payload["generated_at_et"],
            "directory_stale_fallback": False,
        }
    except Exception:
        stale = _read_universe_cache(require_today=False)
        if not stale:
            raise
        symbols, payload = stale
        return symbols, {
            "source": "nasdaqtrader_stale_directory_fallback",
            "market_date": market_date,
            "symbols": len(symbols),
            "cache_hit": True,
            "directory_generated_at_et": payload.get("generated_at_et"),
            "directory_stale_fallback": True,
        }


def _tradier_call(fn, *args):
    delay = 1.0
    for attempt in range(5):
        try:
            return fn(*args)
        except urllib.error.HTTPError as exc:
            # Authentication/entitlement errors must surface immediately.
            if exc.code not in {429, 500, 502, 503, 504} or attempt >= 4:
                raise
        except urllib.error.URLError:
            if attempt >= 4:
                raise
        time.sleep(delay)
        delay = min(8.0, delay * 2.0)
    return None


def _quote_rows(symbols, token):
    """Quote every symbol, preserving partial coverage and batch diagnostics."""
    merged = {}
    errors = []
    batches = list(_chunks(symbols, QUOTE_BATCH_SIZE))
    started = time.perf_counter()
    for index, batch in enumerate(batches, start=1):
        try:
            rows = _tradier_call(post_quotes, batch, token) or {}
            merged.update(rows)
        except Exception as exc:
            errors.append(
                {
                    "batch": index,
                    "symbols": len(batch),
                    "error": str(exc)[:240],
                }
            )
        if index < len(batches) and REQUEST_DELAY_SECONDS > 0:
            time.sleep(REQUEST_DELAY_SECONDS)
    return merged, {
        "batches": len(batches),
        "failed_batches": len(errors),
        "batch_errors": errors[:5],
        "quote_sweep_seconds": round(time.perf_counter() - started, 2),
    }


def _timestamp_epoch(value):
    parsed = parse_market_timestamp(value)
    return parsed.timestamp() if parsed is not None else None


def _quote_timestamp(row):
    timestamps = [
        _timestamp_epoch(row.get("trade_date")),
        _timestamp_epoch(row.get("ask_date")),
        _timestamp_epoch(row.get("bid_date")),
        _timestamp_epoch(row.get("timestamp")),
    ]
    timestamps = [value for value in timestamps if value is not None]
    return max(timestamps) if timestamps else None


def _quote_midpoint(row):
    bid = _num(row.get("bid"))
    ask = _num(row.get("ask"))
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return None
    return (bid + ask) / 2.0


def _quote_price(row):
    return (
        _num(row.get("last"))
        or _quote_midpoint(row)
        or _num(row.get("close"))
        or _num(row.get("prevclose"))
    )


def _risk_lane(price):
    price = _num(price) or 0.0
    if price < 1.0:
        return "SUB-$1"
    if price <= 50.0:
        return "$1-$50"
    return "ABOVE-$50"


def _spread_pct(row):
    bid = _num(row.get("bid"))
    ask = _num(row.get("ask"))
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return None
    midpoint = (bid + ask) / 2.0
    return ((ask - bid) / midpoint) * 100.0 if midpoint > 0 else None


def _load_state():
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}, False
    if int(payload.get("schema_version") or 0) != STATE_SCHEMA_VERSION:
        return {}, False
    observations = payload.get("observations")
    return (observations if isinstance(observations, dict) else {}), True


def _nearest_history(history, now_ts, target_age):
    if not history:
        return None
    eligible = []
    for observation in history:
        observed_at = _num(observation.get("t"))
        if observed_at is None:
            continue
        age = now_ts - observed_at
        if age < max(20.0, target_age * 0.55):
            continue
        if age > max(target_age * 2.8, target_age + 240.0):
            continue
        eligible.append((abs(age - target_age), observation))
    if not eligible:
        return None
    eligible.sort(key=lambda item: item[0])
    return eligible[0][1]


def _latest_history(history, now_ts):
    valid = [
        row
        for row in history or []
        if _num(row.get("t")) is not None
        and 0 < now_ts - float(row["t"]) <= STATE_RETENTION_SECONDS
    ]
    return valid[-1] if valid else None


def _prior_interval_rate(history):
    valid = [
        row
        for row in history or []
        if _num(row.get("t")) is not None
        and _num(row.get("v")) is not None
    ]
    if len(valid) < 2:
        return None
    first, second = valid[-2], valid[-1]
    elapsed_minutes = (float(second["t"]) - float(first["t"])) / 60.0
    if elapsed_minutes <= 0:
        return None
    return max(0.0, float(second["v"]) - float(first["v"])) / elapsed_minutes


def _explosion_score(row):
    day_pct = max(0.0, _num(row.get("discovery_change_pct")) or 0.0)
    last_pct = max(0.0, _num(row.get("radar_change_since_last_pct")) or 0.0)
    three_pct = max(0.0, _num(row.get("radar_change_3m_pct")) or 0.0)
    five_pct = max(0.0, _num(row.get("radar_change_5m_pct")) or 0.0)
    rel_volume = max(0.0, _num(row.get("discovery_relative_volume")) or 0.0)
    velocity_ratio = max(0.0, _num(row.get("radar_volume_velocity_ratio")) or 0.0)
    acceleration = max(0.0, _num(row.get("radar_volume_acceleration_ratio")) or 0.0)
    dollar_velocity = max(0.0, _num(row.get("radar_dollar_velocity_per_min")) or 0.0)
    range_pct = max(0.0, _num(row.get("radar_session_range_pct")) or 0.0)

    score = 0.0
    score += _scale(day_pct, 1.0, 30.0, 25.0)
    score += _scale(day_pct, 30.0, 80.0, 8.0)
    score += _scale(last_pct, 0.2, 6.0, 17.0)
    score += _scale(three_pct, 0.5, 10.0, 13.0)
    score += _scale(five_pct, 1.0, 15.0, 9.0)
    score += _scale(rel_volume, 0.25, 5.0, 8.0)
    score += _scale(velocity_ratio, 1.5, 20.0, 8.0)
    score += _scale(acceleration, 1.2, 6.0, 5.0)
    score += _scale(dollar_velocity, 5_000.0, 250_000.0, 4.0)
    score += _scale(range_pct, 2.0, 15.0, 3.0)

    if row.get("radar_quiet_to_active"):
        score += 5.0
    if row.get("radar_near_session_high"):
        score += 3.0

    # A stale quote may explain a historical day move but cannot be a live
    # ignition alert.  Keep it visible at a heavily reduced score for diagnosis.
    if not row.get("radar_quote_fresh"):
        score *= 0.35
    if (_num(row.get("discovery_change_pct")) or 0.0) < -1.0:
        score *= 0.35
    return round(max(0.0, min(100.0, score)), 1)


def _tradeability_score(row):
    score = 0.0
    if row.get("radar_quote_fresh"):
        score += 25.0

    spread = _num(row.get("radar_spread_pct"))
    if spread is not None:
        if spread <= 0.5:
            score += 25.0
        elif spread <= 1.0:
            score += 22.0
        elif spread <= 2.0:
            score += 17.0
        elif spread <= 4.0:
            score += 10.0
        elif spread <= 8.0:
            score += 4.0

    dollar_volume = max(0.0, _num(row.get("discovery_dollar_volume")) or 0.0)
    score += _scale(math.log10(max(1.0, dollar_volume)), 4.5, 7.3, 25.0)

    rel_volume = max(0.0, _num(row.get("discovery_relative_volume")) or 0.0)
    score += _scale(rel_volume, 0.2, 4.0, 12.0)

    if row.get("radar_bid_ask_available"):
        score += 5.0

    lane = row.get("risk_lane")
    if lane == "$1-$50":
        score += 8.0
    elif lane == "ABOVE-$50":
        score += 6.0
    else:
        # Sub-dollar names remain discoverable but receive a deliberate
        # execution-risk haircut rather than a visibility ban.
        score += 2.0

    return round(max(0.0, min(100.0, score)), 1)


def _radar_row(symbol, quote, history, now_ts):
    price = _quote_price(quote)
    prev_close = _num(quote.get("prevclose"))
    if price is None or price < MIN_RADAR_PRICE:
        return None

    volume = max(0.0, _num(quote.get("volume")) or 0.0)
    average_volume = max(0.0, _num(quote.get("average_volume")) or 0.0)
    day_pct = _num(quote.get("change_percentage"))
    if day_pct is None:
        day_pct = _pct_change(price, prev_close)

    quote_ts = _quote_timestamp(quote)
    quote_age = max(0.0, now_ts - quote_ts) if quote_ts is not None else None
    quote_fresh = quote_age is not None and quote_age <= MAX_QUOTE_AGE_SECONDS

    last = _latest_history(history, now_ts)
    ref_3m = _nearest_history(history, now_ts, 180.0)
    ref_5m = _nearest_history(history, now_ts, 300.0)
    change_last = _pct_change(price, last.get("p")) if last else None
    change_3m = _pct_change(price, ref_3m.get("p")) if ref_3m else None
    change_5m = _pct_change(price, ref_5m.get("p")) if ref_5m else None

    volume_rate = None
    dollar_velocity = None
    if last:
        elapsed_minutes = (now_ts - float(last["t"])) / 60.0
        if elapsed_minutes > 0:
            volume_rate = max(0.0, volume - float(last.get("v") or 0.0)) / elapsed_minutes
            dollar_velocity = volume_rate * price

    prior_rate = _prior_interval_rate(history)
    volume_acceleration = None
    if volume_rate is not None and prior_rate is not None:
        volume_acceleration = (
            volume_rate / max(1.0, prior_rate)
            if prior_rate > 0
            else (10.0 if volume_rate > 0 else 0.0)
        )

    normal_per_minute = average_volume / 390.0 if average_volume > 0 else None
    velocity_ratio = (
        volume_rate / normal_per_minute
        if volume_rate is not None and normal_per_minute and normal_per_minute > 0
        else None
    )
    quiet_to_active = bool(
        volume_rate is not None
        and normal_per_minute is not None
        and volume_rate >= max(500.0, normal_per_minute * 4.0)
        and (prior_rate is None or prior_rate <= max(250.0, normal_per_minute * 1.5))
    )

    high = _num(quote.get("high"))
    low = _num(quote.get("low"))
    range_pct = _pct_change(high, low) if high and low else None
    from_high_pct = ((high - price) / high) * 100.0 if high and high > 0 else None
    spread = _spread_pct(quote)
    lane = _risk_lane(price)

    row = {
        "symbol": symbol,
        "risk_lane": lane,
        "sub_dollar": lane == "SUB-$1",
        "discovery_price": round(price, 6),
        "discovery_prev_close": round(prev_close, 6) if prev_close else None,
        "discovery_change_pct": round(day_pct, 3) if day_pct is not None else None,
        "discovery_relative_volume": (
            round(volume / average_volume, 3) if average_volume > 0 else None
        ),
        "discovery_volume": round(volume, 0),
        "discovery_average_volume": round(average_volume, 0) if average_volume else None,
        "discovery_dollar_volume": round(price * volume, 2),
        "radar_change_since_last_pct": (
            round(change_last, 3) if change_last is not None else None
        ),
        "radar_change_3m_pct": round(change_3m, 3) if change_3m is not None else None,
        "radar_change_5m_pct": round(change_5m, 3) if change_5m is not None else None,
        "radar_volume_per_min": round(volume_rate, 2) if volume_rate is not None else None,
        "radar_dollar_velocity_per_min": (
            round(dollar_velocity, 2) if dollar_velocity is not None else None
        ),
        "radar_volume_velocity_ratio": (
            round(velocity_ratio, 3) if velocity_ratio is not None else None
        ),
        "radar_volume_acceleration_ratio": (
            round(volume_acceleration, 3) if volume_acceleration is not None else None
        ),
        "radar_quiet_to_active": quiet_to_active,
        "radar_session_range_pct": round(range_pct, 3) if range_pct is not None else None,
        "radar_from_high_pct": (
            round(from_high_pct, 3) if from_high_pct is not None else None
        ),
        "radar_near_session_high": (
            from_high_pct is not None and from_high_pct <= 3.0
        ),
        "radar_spread_pct": round(spread, 4) if spread is not None else None,
        "radar_bid_ask_available": spread is not None,
        "radar_quote_timestamp": (
            datetime.fromtimestamp(quote_ts, tz=timezone.utc).isoformat()
            if quote_ts is not None
            else None
        ),
        "radar_quote_age_seconds": round(quote_age, 2) if quote_age is not None else None,
        "radar_quote_fresh": quote_fresh,
        # Reuse the exact quote in stock_scanner.py so the full-market sweep is
        # not immediately followed by a redundant provider request.
        "_tradier_quote": quote,
    }
    row["explosion_score"] = _explosion_score(row)
    row["tradeability_score"] = _tradeability_score(row)
    row["radar_rank_score"] = round(
        row["explosion_score"] * 0.74 + row["tradeability_score"] * 0.26,
        2,
    )

    reasons = []
    short_acceleration = max(
        _num(change_last) or -999.0,
        _num(change_3m) or -999.0,
        _num(change_5m) or -999.0,
    )
    if short_acceleration >= 3.0:
        reasons.append(f"short-term price acceleration {short_acceleration:+.1f}%")
    if day_pct is not None and day_pct >= 10.0:
        reasons.append(f"session move {day_pct:+.1f}%")
    if velocity_ratio is not None and velocity_ratio >= 4.0:
        reasons.append(f"volume velocity {velocity_ratio:.1f}× normal/min")
    if quiet_to_active:
        reasons.append("quiet-to-active ignition")
    if lane == "SUB-$1":
        reasons.append("sub-$1 extreme-risk lane")
    if not quote_fresh:
        reasons.append("quote not fresh enough for an actionable alert")
    row["radar_reasons"] = reasons[:5]
    return row


def _candidate_trigger(row):
    if not row.get("radar_quote_fresh"):
        return row.get("explosion_score", 0) >= 35
    day_pct = _num(row.get("discovery_change_pct")) or 0.0
    short = max(
        _num(row.get("radar_change_since_last_pct")) or -999.0,
        _num(row.get("radar_change_3m_pct")) or -999.0,
        _num(row.get("radar_change_5m_pct")) or -999.0,
    )
    rel_volume = _num(row.get("discovery_relative_volume")) or 0.0
    return bool(
        day_pct >= 1.5
        or short >= 0.75
        or rel_volume >= 1.5
        or row.get("radar_quiet_to_active")
        or row.get("explosion_score", 0) >= 28
    )


def _select_candidates(rows, top):
    top = max(20, int(top or DEFAULT_RETURNED_CANDIDATES))
    candidates = [row for row in rows if _candidate_trigger(row)]
    candidates.sort(
        key=lambda row: (
            row.get("radar_quote_fresh") is True,
            row.get("explosion_score", 0),
            row.get("tradeability_score", 0),
            row.get("discovery_change_pct") or -999,
            row.get("discovery_dollar_volume") or 0,
        ),
        reverse=True,
    )

    selected = []
    seen = set()

    def add(pool, quota):
        for row in pool:
            if len(selected) >= top or quota <= 0:
                break
            symbol = row["symbol"]
            if symbol in seen:
                continue
            selected.append(row)
            seen.add(symbol)
            quota -= 1

    # First preserve every true ignition candidate, regardless of price lane or
    # tradeability.  This is the key protection against another BIAF-style miss.
    ignition = [
        row
        for row in candidates
        if row.get("explosion_score", 0) >= 65
        or max(
            _num(row.get("radar_change_since_last_pct")) or -999,
            _num(row.get("radar_change_3m_pct")) or -999,
            _num(row.get("radar_change_5m_pct")) or -999,
        )
        >= 3.0
    ]
    add(ignition, min(top, 60))

    # Reserve explicit room for the previously hidden price lanes.
    add([row for row in candidates if row.get("risk_lane") == "SUB-$1"], min(35, top))
    add([row for row in candidates if row.get("risk_lane") == "ABOVE-$50"], min(20, top))
    add([row for row in candidates if row.get("risk_lane") == "$1-$50"], min(110, top))
    add(candidates, top)

    for index, row in enumerate(selected, start=1):
        row["radar_rank"] = index
    return selected


def _save_state(rows, previous_state, now_ts):
    observations = {}
    for row in rows:
        symbol = row["symbol"]
        history = [
            item
            for item in previous_state.get(symbol, [])
            if _num(item.get("t")) is not None
            and 0 <= now_ts - float(item["t"]) <= STATE_RETENTION_SECONDS
        ]
        history.append(
            {
                "t": round(now_ts, 3),
                "p": row.get("discovery_price"),
                "v": row.get("discovery_volume") or 0.0,
            }
        )
        observations[symbol] = history[-STATE_POINTS_PER_SYMBOL:]

    payload = {
        "schema_version": STATE_SCHEMA_VERSION,
        "updated_at_utc": datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat(),
        "observations": observations,
    }
    _atomic_json_write(STATE_PATH, payload)


def get_last_discovery_meta():
    return dict(_LAST_DISCOVERY_META)


def discover_tradier_candidates(token, likely_common_stock, top=None):
    """Sweep the full stock directory and return the strongest radar candidates."""
    global _LAST_DISCOVERY_META

    top = int(top or DEFAULT_RETURNED_CANDIDATES)
    started = time.perf_counter()
    symbols, universe_meta = get_or_build_discovery_universe(
        token,
        likely_common_stock,
    )
    quote_rows, quote_meta = _quote_rows(symbols, token)
    now_ts = time.time()
    state, state_loaded = _load_state()

    radar_rows = []
    stock_quote_count = 0
    below_floor = 0
    sub_dollar_count = 0
    fresh_quote_count = 0
    for symbol, quote in quote_rows.items():
        if str((quote or {}).get("type") or "").lower() != "stock":
            continue
        stock_quote_count += 1
        row = _radar_row(symbol, quote or {}, state.get(symbol, []), now_ts)
        if row is None:
            price = _quote_price(quote or {})
            if price is not None and price < MIN_RADAR_PRICE:
                below_floor += 1
            continue
        if row.get("sub_dollar"):
            sub_dollar_count += 1
        if row.get("radar_quote_fresh"):
            fresh_quote_count += 1
        radar_rows.append(row)

    try:
        _save_state(radar_rows, state, now_ts)
        state_write_error = None
    except Exception as exc:
        state_write_error = str(exc)[:240]

    selected = _select_candidates(radar_rows, top)
    coverage_pct = (
        len(quote_rows) / len(symbols) * 100.0 if symbols else 0.0
    )
    warnings = []
    if coverage_pct < MIN_COVERAGE_PCT:
        warnings.append(
            f"Full-market quote coverage degraded to {coverage_pct:.1f}% "
            f"({len(quote_rows)}/{len(symbols)} symbols)."
        )
    if universe_meta.get("directory_stale_fallback"):
        warnings.append("Using the most recent cached listing directory.")
    if quote_meta.get("failed_batches"):
        warnings.append(
            f"{quote_meta['failed_batches']} full-market quote batch(es) failed."
        )
    if state_write_error:
        warnings.append("Rolling ignition state could not be saved: " + state_write_error)

    _LAST_DISCOVERY_META = {
        **universe_meta,
        **quote_meta,
        "mode": "full_market_radar",
        "full_market_enabled": True,
        "minimum_detection_price": MIN_RADAR_PRICE,
        "requested_symbols": len(symbols),
        "quotes_received": len(quote_rows),
        "stock_quotes_received": stock_quote_count,
        "eligible_stocks": len(radar_rows),
        "fresh_quotes": fresh_quote_count,
        "sub_dollar_eligible": sub_dollar_count,
        "below_detection_floor": below_floor,
        "coverage_pct": round(coverage_pct, 2),
        "coverage_ok": coverage_pct >= MIN_COVERAGE_PCT,
        "candidates_returned": len(selected),
        "state_loaded": state_loaded,
        "state_symbols_before": len(state),
        "warnings": warnings,
        "total_discovery_seconds": round(time.perf_counter() - started, 2),
    }

    # A near-empty response is not a legitimate no-idea result.  Surface a
    # provider failure before the old snapshot can be replaced.
    if symbols and coverage_pct < 20.0:
        raise RuntimeError(
            "FULL-MARKET RADAR UNAVAILABLE — Tradier returned quotes for only "
            f"{len(quote_rows)}/{len(symbols)} requested symbols."
        )

    return selected, get_last_discovery_meta()
