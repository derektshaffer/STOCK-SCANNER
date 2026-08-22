import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DATA_BASE = "https://data.alpaca.markets"
FEED = "iex"
TOP_MOVERS = 50

MIN_PRICE = 1.00
MAX_PRICE = 50.00
MIN_DAY_PCT = 3.0
MIN_DOLLAR_VOLUME = 5_000_000
MIN_INTRADAY_RANGE_PCT = 3.0
MAX_DISTANCE_FROM_HIGH_PCT = 8.0
MAX_SPREAD_PCT = 2.0

API_KEY = os.environ.get("ALPACA_API_KEY", "").strip()
API_SECRET = os.environ.get("ALPACA_SECRET_KEY", "").strip()

if not API_KEY or not API_SECRET:
    print("ERROR: Missing ALPACA_API_KEY or ALPACA_SECRET_KEY GitHub secret.")
    sys.exit(1)

HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
    "Accept": "application/json",
    "User-Agent": "github-stock-scanner/1.0",
}

def get_json(url, timeout=20):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed for {url}: {exc}") from exc

def pct_change(new, old):
    if not old:
        return None
    return (new / old - 1.0) * 100.0

def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))

def scale(value, start, end, points):
    if value is None or end == start:
        return 0.0
    return clamp((value - start) / (end - start)) * points

def likely_common_stock(symbol):
    s = symbol.upper().strip()
    if not s or len(s) > 6:
        return False
    if len(s) >= 5 and s.endswith(("W", "WS", "WT", "R", "RT", "U", "UN")):
        return False
    return True

def get_movers():
    url = f"{DATA_BASE}/v1beta1/screener/stocks/movers?top={TOP_MOVERS}"
    return get_json(url).get("gainers", [])

def get_snapshot(symbol):
    q = urllib.parse.urlencode({"feed": FEED})
    url = f"{DATA_BASE}/v2/stocks/{urllib.parse.quote(symbol)}/snapshot?{q}"
    return get_json(url)

def get_bars(symbol, timeframe, start, end, limit):
    params = urllib.parse.urlencode({
        "timeframe": timeframe,
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "limit": limit,
        "adjustment": "raw",
        "feed": FEED,
        "sort": "asc",
    })
    url = f"{DATA_BASE}/v2/stocks/{urllib.parse.quote(symbol)}/bars?{params}"
    return get_json(url).get("bars", [])

def session_fraction(now_et):
    open_minutes = 9 * 60 + 30
    close_minutes = 16 * 60
    current_minutes = now_et.hour * 60 + now_et.minute
    if current_minutes <= open_minutes:
        return 1.0 / 390.0
    if current_minutes >= close_minutes:
        return 1.0
    return max(1.0 / 390.0, (current_minutes - open_minutes) / 390.0)

def avg_daily_volume(symbol, now_utc):
    bars = get_bars(symbol, "1Day", now_utc - timedelta(days=45), now_utc, 35)
    if not bars:
        return None
    today = now_utc.date().isoformat()
    completed = [b for b in bars if str(b.get("t", ""))[:10] != today and b.get("v")]
    completed = completed[-20:]
    if not completed:
        return None
    return sum(float(b["v"]) for b in completed) / len(completed)

def recent_momentum(symbol, now_utc, current_price):
    bars = get_bars(symbol, "1Min", now_utc - timedelta(minutes=50), now_utc, 50)
    if len(bars) < 2:
        return None, None
    m5 = pct_change(current_price, float(bars[-6]["c"])) if len(bars) >= 6 and bars[-6].get("c") else None
    m15 = pct_change(current_price, float(bars[-16]["c"])) if len(bars) >= 16 and bars[-16].get("c") else None
    return m5, m15

def score_candidate(c):
    score = 0.0
    score += scale(c.get("day_pct"), 3.0, 20.0, 20.0)
    score += scale(c.get("volume_pace"), 1.0, 5.0, 25.0)
    score += scale(c.get("momentum_5m"), 0.0, 3.0, 20.0)
    score += scale(c.get("momentum_15m"), 0.0, 6.0, 10.0)
    score += 10.0 if c.get("above_vwap") else 0.0

    distance = c.get("distance_from_high_pct")
    if distance is not None:
        score += clamp((8.0 - distance) / 8.0) * 10.0

    spread = c.get("spread_pct")
    if spread is not None:
        score += clamp((2.0 - spread) / 2.0) * 5.0

    return round(score, 1)

def analyze(symbol, now_utc, now_et):
    snap = get_snapshot(symbol)
    trade = snap.get("latestTrade") or {}
    quote = snap.get("latestQuote") or {}
    daily = snap.get("dailyBar") or {}
    prev = snap.get("prevDailyBar") or {}

    price = float(trade.get("p") or 0)
    prev_close = float(prev.get("c") or 0)
    volume = float(daily.get("v") or 0)
    high = float(daily.get("h") or 0)
    low = float(daily.get("l") or 0)
    vwap = float(daily.get("vw") or 0)
    bid = float(quote.get("bp") or 0)
    ask = float(quote.get("ap") or 0)

    if not price or not prev_close or not high or not low:
        return None, ["missing market data"]

    day_pct = pct_change(price, prev_close)
    dollar_volume = price * volume
    intraday_range_pct = pct_change(high, low)
    distance_from_high_pct = ((high - price) / high) * 100 if high else None
    distance_from_vwap_pct = pct_change(price, vwap) if vwap else None
    above_vwap = bool(vwap and price > vwap)

    spread_pct = None
    if bid > 0 and ask > 0 and ask >= bid:
        midpoint = (bid + ask) / 2.0
        spread_pct = ((ask - bid) / midpoint) * 100 if midpoint else None

    reasons = []
    if price < MIN_PRICE:
        reasons.append("price < $1")
    if price > MAX_PRICE:
        reasons.append("price > $50")
    if day_pct is None or day_pct < MIN_DAY_PCT:
        reasons.append("day gain < 3%")
    if dollar_volume < MIN_DOLLAR_VOLUME:
        reasons.append("dollar volume < $5M")
    if intraday_range_pct is None or intraday_range_pct < MIN_INTRADAY_RANGE_PCT:
        reasons.append("range < 3%")
    if distance_from_high_pct is None or distance_from_high_pct > MAX_DISTANCE_FROM_HIGH_PCT:
        reasons.append("> 8% below high")
    if spread_pct is None or spread_pct > MAX_SPREAD_PCT:
        reasons.append("spread > 2% or unavailable")
    if not above_vwap:
        reasons.append("below VWAP")

    candidate = {
        "symbol": symbol,
        "price": round(price, 4),
        "day_pct": round(day_pct, 2) if day_pct is not None else None,
        "volume": int(volume),
        "dollar_volume": round(dollar_volume, 2),
        "spread_pct": round(spread_pct, 3) if spread_pct is not None else None,
        "intraday_range_pct": round(intraday_range_pct, 2) if intraday_range_pct is not None else None,
        "distance_from_high_pct": round(distance_from_high_pct, 2) if distance_from_high_pct is not None else None,
        "vwap": round(vwap, 4) if vwap else None,
        "distance_from_vwap_pct": round(distance_from_vwap_pct, 2) if distance_from_vwap_pct is not None else None,
        "above_vwap": above_vwap,
    }

    if reasons:
        return candidate, reasons

    try:
        m5, m15 = recent_momentum(symbol, now_utc, price)
        avg_vol = avg_daily_volume(symbol, now_utc)
    except Exception as exc:
        print(f"WARN {symbol}: could not load momentum/history: {exc}")
        m5, m15, avg_vol = None, None, None

    pace = None
    if avg_vol and avg_vol > 0:
        expected_so_far = avg_vol * session_fraction(now_et)
        if expected_so_far > 0:
            pace = volume / expected_so_far

    candidate.update({
        "momentum_5m": round(m5, 2) if m5 is not None else None,
        "momentum_15m": round(m15, 2) if m15 is not None else None,
        "avg_20d_volume": round(avg_vol, 0) if avg_vol is not None else None,
        "volume_pace": round(pace, 2) if pace is not None else None,
    })
    candidate["score"] = score_candidate(candidate)
    return candidate, []

def print_table(rows):
    if not rows:
        print("No stocks passed the base momentum filters on this scan.")
        return

    print("\nTOP MOMENTUM CANDIDATES")
    print("-" * 110)
    print(
        f"{'SYM':<7}{'SCORE':>7}{'PRICE':>10}{'DAY%':>9}{'5M%':>9}{'15M%':>9}"
        f"{'VOL PACE':>11}{'SPREAD%':>11}{'FROM HIGH%':>13}{'ABOVE VWAP':>13}"
    )
    print("-" * 110)

    for c in rows[:10]:
        def fmt(value, digits=2):
            return "-" if value is None else f"{value:.{digits}f}"

        print(
            f"{c['symbol']:<7}"
            f"{c['score']:>7.1f}"
            f"{c['price']:>10.2f}"
            f"{fmt(c['day_pct']):>9}"
            f"{fmt(c.get('momentum_5m')):>9}"
            f"{fmt(c.get('momentum_15m')):>9}"
            f"{fmt(c.get('volume_pace')):>11}"
            f"{fmt(c.get('spread_pct'), 3):>11}"
            f"{fmt(c.get('distance_from_high_pct')):>13}"
            f"{'YES' if c.get('above_vwap') else 'NO':>13}"
        )

def main():
    now_utc = datetime.now(timezone.utc)
    now_et = now_utc.astimezone(ZoneInfo("America/New_York"))
    print(f"Momentum scan started: {now_et:%Y-%m-%d %H:%M:%S %Z}")

    movers = get_movers()
    print(f"Alpaca returned {len(movers)} gainers.")

    candidates = []
    rejection_counts = Counter()
    excluded_symbols = []

    for mover in movers:
        symbol = str(mover.get("symbol", "")).upper().strip()
        if not symbol:
            continue
        if not likely_common_stock(symbol):
            excluded_symbols.append(symbol)
            continue

        try:
            candidate, reasons = analyze(symbol, now_utc, now_et)
        except Exception as exc:
            print(f"WARN {symbol}: {exc}")
            rejection_counts["API/data error"] += 1
            continue

        if reasons:
            for reason in reasons:
                rejection_counts[reason] += 1
        elif candidate:
            candidates.append(candidate)

        time.sleep(0.05)

    candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
    print_table(candidates)

    print("\nSCAN SUMMARY")
    print(f"Passed base filters: {len(candidates)}")
    print(f"Likely warrants/rights/units excluded: {len(excluded_symbols)}")

    if excluded_symbols:
        print("Excluded symbols: " + ", ".join(excluded_symbols[:20]))

    if rejection_counts:
        print("Most common rejection reasons:")
        for reason, count in rejection_counts.most_common():
            print(f"  - {reason}: {count}")

    print("\nJSON RESULTS")
    print(json.dumps(candidates[:10], indent=2))

if __name__ == "__main__":
    main()
