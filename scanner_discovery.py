from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tradier_live import post_quotes


ET = ZoneInfo("America/New_York")
CACHE_DIR = Path(
    os.environ.get("SCANNER_DISCOVERY_CACHE_DIR", "scan_cache").strip()
    or "scan_cache"
)
CACHE_PATH = CACHE_DIR / "tradier_discovery_universe.json"

TARGET_SIZE = int(os.environ.get("SCANNER_DISCOVERY_UNIVERSE_SIZE", "1200") or 1200)
QUOTE_BATCH_SIZE = int(os.environ.get("SCANNER_DISCOVERY_QUOTE_BATCH_SIZE", "300") or 300)
REQUEST_DELAY_SECONDS = float(
    os.environ.get("SCANNER_DISCOVERY_REQUEST_DELAY_SECONDS", "0.35") or 0.35
)
VOLATILITY_RESCUE_SHARE = float(
    os.environ.get("SCANNER_DISCOVERY_VOLATILITY_RESCUE_SHARE", "0.12") or 0.12
)
VOLATILITY_RESCUE_MIN_ABS_CHANGE_PCT = float(
    os.environ.get("SCANNER_DISCOVERY_VOLATILITY_MIN_CHANGE_PCT", "12") or 12
)


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _chunks(values, size):
    for start in range(0, len(values), size):
        yield values[start : start + size]


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
    """Load a broad US-listed common-stock directory without broker auth."""
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
            headers={"User-Agent": "stock-scanner-tradier-discovery/1.0"},
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


def _tradier_call(fn, *args):
    delay = 1.0
    for attempt in range(5):
        try:
            return fn(*args)
        except urllib.error.HTTPError as exc:
            # Authentication/entitlement errors should surface immediately.
            if exc.code not in {429, 500, 502, 503, 504} or attempt >= 4:
                raise
        except urllib.error.URLError:
            if attempt >= 4:
                raise
        time.sleep(delay)
        delay = min(8.0, delay * 2.0)
    return None


def _quote_rows(symbols, token):
    merged = {}
    batches = list(_chunks(symbols, QUOTE_BATCH_SIZE))
    for index, batch in enumerate(batches, start=1):
        rows = _tradier_call(post_quotes, batch, token) or {}
        merged.update(rows)
        if index < len(batches):
            time.sleep(REQUEST_DELAY_SECONDS)
    return merged


def _cached_symbols(market_date):
    try:
        payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    if payload.get("market_date") != market_date:
        return None
    symbols = [
        str(symbol).upper().strip()
        for symbol in payload.get("symbols") or []
        if str(symbol).strip()
    ]
    return symbols if len(symbols) >= 200 else None


def _select_seed_symbols(quote_rows, target_size):
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
        if price is None or avg_volume is None or avg_volume <= 0:
            continue
        if not 0.50 <= price <= 60.0:
            continue

        change_pct = _num(row.get("change_percentage"))
        if change_pct is None:
            prev_close = _num(row.get("prevclose"))
            if prev_close and prev_close > 0:
                change_pct = (price / prev_close - 1.0) * 100.0

        eligible.append(
            {
                "symbol": symbol,
                "price": price,
                "average_volume": avg_volume,
                "average_dollar_volume": price * avg_volume,
                "change_pct": change_pct,
                "abs_change_pct": abs(change_pct) if change_pct is not None else 0.0,
            }
        )

    # Reserve part of the daily universe for recent/current extreme movers.
    # This prevents a suddenly explosive microcap from being invisible merely
    # because its prior average dollar volume was not high enough for the
    # liquidity-ranked core universe.
    rescue_quota = max(1, int(round(target_size * VOLATILITY_RESCUE_SHARE)))
    rescue = [
        row for row in eligible
        if row["abs_change_pct"] >= VOLATILITY_RESCUE_MIN_ABS_CHANGE_PCT
    ]
    rescue.sort(
        key=lambda row: (
            row["abs_change_pct"],
            row["average_dollar_volume"],
        ),
        reverse=True,
    )

    selected = []
    seen = set()
    for row in rescue[:rescue_quota]:
        seen.add(row["symbol"])
        selected.append(row["symbol"])

    # Keep substantial coverage of low-price names instead of allowing
    # mega-caps to dominate a simple dollar-volume sort. Allocate the
    # remaining capacity across the same price-band proportions.
    bands = (
        (0.50, 5.0, 0.40),
        (5.0, 20.0, 0.35),
        (20.0, 60.01, 0.25),
    )
    remaining = max(0, target_size - len(selected))
    for low, high, share in bands:
        band = [
            row for row in eligible
            if low <= row["price"] < high and row["symbol"] not in seen
        ]
        band.sort(
            key=lambda row: row["average_dollar_volume"],
            reverse=True,
        )
        quota = max(1, int(round(remaining * share))) if remaining else 0
        for row in band[:quota]:
            symbol = row["symbol"]
            if symbol in seen:
                continue
            seen.add(symbol)
            selected.append(symbol)

    if len(selected) < target_size:
        eligible.sort(
            key=lambda row: row["average_dollar_volume"],
            reverse=True,
        )
        for row in eligible:
            symbol = row["symbol"]
            if symbol in seen:
                continue
            seen.add(symbol)
            selected.append(symbol)
            if len(selected) >= target_size:
                break

    return selected[:target_size], len(eligible)


def get_or_build_discovery_universe(token, likely_common_stock):
    """Return a daily cached Tradier discovery universe.

    The expensive broad quote screen is done once per market date in GitHub
    Actions and restored through actions/cache on subsequent 5-minute runs.
    """
    market_date = datetime.now(ET).date().isoformat()
    cached = _cached_symbols(market_date)
    if cached:
        return cached, {
            "source": "tradier_daily_cache",
            "market_date": market_date,
            "symbols": len(cached),
            "cache_hit": True,
        }

    public_symbols = _load_public_symbols(likely_common_stock)
    quote_rows = _quote_rows(public_symbols, token)
    selected, eligible_count = _select_seed_symbols(
        quote_rows,
        max(300, TARGET_SIZE),
    )
    if len(selected) < 200:
        raise RuntimeError(
            "Tradier discovery universe was unexpectedly small "
            f"({len(selected)} symbols from {len(quote_rows)} quotes)."
        )

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "market_date": market_date,
        "generated_at_et": datetime.now(ET).isoformat(),
        "source": "nasdaqtrader_public_directory_plus_tradier_quotes",
        "public_symbols": len(public_symbols),
        "tradier_quotes": len(quote_rows),
        "eligible_stocks": eligible_count,
        "symbols": selected,
    }
    CACHE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return selected, {
        "source": payload["source"],
        "market_date": market_date,
        "symbols": len(selected),
        "cache_hit": False,
        "public_symbols": len(public_symbols),
        "tradier_quotes": len(quote_rows),
    }


def discover_tradier_candidates(token, likely_common_stock, top=100):
    """Rank current movers from the cached broad Tradier stock universe."""
    symbols, meta = get_or_build_discovery_universe(
        token,
        likely_common_stock,
    )
    quote_rows = _quote_rows(symbols, token)

    ranked = []
    for symbol, row in quote_rows.items():
        if str(row.get("type") or "").lower() != "stock":
            continue
        price = (
            _num(row.get("last"))
            or _num(row.get("close"))
            or _num(row.get("prevclose"))
        )
        prev_close = _num(row.get("prevclose"))
        if not price or not prev_close or prev_close <= 0:
            continue
        if not 0.50 <= price <= 60.0:
            continue

        change_pct = _num(row.get("change_percentage"))
        if change_pct is None:
            change_pct = (price / prev_close - 1.0) * 100.0

        volume = _num(row.get("volume")) or 0.0
        avg_volume = _num(row.get("average_volume")) or 0.0
        rel_volume = volume / avg_volume if avg_volume > 0 else 0.0
        dollar_volume = price * volume

        # Discovery should favor actual price momentum first, then unusually
        # active/liquid names. Final scanner filters/scoring still decide rank.
        activity = math.log1p(max(0.0, rel_volume)) * 4.0
        liquidity = math.log10(max(1.0, dollar_volume)) * 0.35
        discovery_score = change_pct * 2.0 + activity + liquidity

        ranked.append(
            {
                "symbol": symbol,
                "discovery_change_pct": round(change_pct, 3),
                "discovery_relative_volume": round(rel_volume, 3),
                "discovery_dollar_volume": round(dollar_volume, 2),
                "_discovery_score": discovery_score,
            }
        )

    ranked.sort(
        key=lambda row: (
            row["_discovery_score"],
            row["discovery_change_pct"],
            row["discovery_dollar_volume"],
        ),
        reverse=True,
    )
    selected = ranked[: max(20, int(top))]
    for row in selected:
        row.pop("_discovery_score", None)

    meta = {
        **meta,
        "current_quotes": len(quote_rows),
        "candidates_returned": len(selected),
    }
    return selected, meta
