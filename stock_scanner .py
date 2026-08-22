import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

# ============================================================
# MOMENTUM STOCK SCANNER - v2.5
#
# v2.5 includes:
#   1) Near-miss / watchlist ranking even when nothing fully passes.
#   2) 5m + 15m momentum and volume-pace enrichment during market hours.
#   3) First-pass historical continuation analysis during market hours.
#   4) Alpaca news/catalyst detection with a small positive/negative score.
#   5) Tradability-first ranking so news cannot outrank bad liquidity/spreads.
#   6) Generic mover articles are separated from real identifiable catalysts.
#   7) IEX-aware liquidity estimates so free-feed volume is not treated as full-market volume.
#   8) IEX spread is a warning, not a hard NBBO-style rejection.
#   9) Historical comparisons prefer delayed historical SIP data and exclude the current day.
#  10) Liquidity prefers real consolidated SIP volume delayed ~15 minutes, with IEX scaling only as fallback.
#  11) Setup grades, chase-risk flags, and alert-readiness labels.
#
# Historical analysis compares each ticker with its OWN past intraday
# setups at roughly the same time of day, then measures the next
# 15 / 30 / 60 minutes. This is not yet a cross-market backtest.
#
# Catalyst scoring is intentionally conservative and keyword-based.
# It helps explain a move; it should not be treated as a prediction.
# ============================================================

DATA_BASE = "https://data.alpaca.markets"
LIVE_FEED = "iex"
HISTORICAL_FEED = "sip"

TOP_MOVERS = 50
WATCHLIST_SIZE = 10
ENRICH_TOP = WATCHLIST_SIZE
NEWS_TOP = 10
HISTORICAL_TOP = 5

MIN_PRICE = 1.00
MAX_PRICE = 50.00
MIN_DAY_PCT = 3.0
MIN_TOTAL_DOLLAR_VOLUME = 5_000_000
MIN_RAW_IEX_DOLLAR_VOLUME_FALLBACK = 25_000
IEX_MARKET_SHARE_ESTIMATE = 0.025
SIP_LIQUIDITY_DELAY_MINUTES = 16
SIP_LIQUIDITY_TIMEFRAME = "5Min"
MIN_INTRADAY_RANGE_PCT = 3.0
MAX_DISTANCE_FROM_HIGH_PCT = 8.0
MAX_IEX_SPREAD_WARNING_PCT = 10.0
MAX_VWAP_EXTENSION_PCT = 15.0
EXTREME_DAY_MOVE_PCT = 80.0

LIVE_A_MIN_5M = 0.5
LIVE_A_MIN_15M = 1.0
LIVE_A_MIN_VOLUME_PACE = 1.5
LIVE_A_MAX_FROM_HIGH_PCT = 3.0

NEWS_LOOKBACK_HOURS = 72
NEWS_LIMIT = 50

HISTORY_LOOKBACK_DAYS = 180
HISTORY_MAX_SAMPLES = 10

API_KEY = os.environ.get("ALPACA_API_KEY", "").strip()
API_SECRET = os.environ.get("ALPACA_SECRET_KEY", "").strip()

if not API_KEY or not API_SECRET:
    print("ERROR: Missing ALPACA_API_KEY or ALPACA_SECRET_KEY GitHub secret.")
    sys.exit(1)

HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
    "Accept": "application/json",
    "User-Agent": "github-stock-scanner/2.5",
}

# (category, base score, keywords)
# Positive and negative events are both included. The final news adjustment
# is capped by these modest values and then decayed by article age.
CATALYST_RULES = [
    (
        "bankruptcy / severe distress",
        -10.0,
        (
            "bankruptcy",
            "chapter 11",
            "chapter 7",
            "going concern",
            "liquidation",
        ),
    ),
    (
        "offering / dilution risk",
        -8.0,
        (
            "public offering",
            "registered direct",
            "at-the-market",
            "atm offering",
            "private placement",
            "warrant exercise",
            "securities purchase agreement",
            "stock offering",
        ),
    ),
    (
        "delisting / reverse split risk",
        -8.0,
        (
            "reverse stock split",
            "reverse split",
            "delisting",
            "nasdaq deficiency",
            "noncompliance",
            "minimum bid price",
        ),
    ),
    (
        "legal / regulatory risk",
        -6.0,
        (
            "investigation",
            "subpoena",
            "lawsuit",
            "class action",
            "sec charges",
            "fraud",
        ),
    ),
    (
        "FDA / clinical catalyst",
        9.0,
        (
            "fda approval",
            "fda clears",
            "fda clearance",
            "breakthrough therapy",
            "fast track designation",
            "phase 1",
            "phase 2",
            "phase 3",
            "clinical trial",
            "trial results",
            "primary endpoint",
        ),
    ),
    (
        "merger / acquisition",
        9.0,
        (
            "merger",
            "acquisition",
            "acquire",
            "acquired by",
            "buyout",
            "takeover",
            "strategic alternatives",
        ),
    ),
    (
        "major commercial deal",
        7.0,
        (
            "purchase order",
            "contract award",
            "awarded contract",
            "selected by",
            "strategic partnership",
            "collaboration agreement",
            "licensing agreement",
        ),
    ),
    (
        "earnings / guidance",
        6.0,
        (
            "earnings",
            "quarterly results",
            "financial results",
            "raises guidance",
            "raised guidance",
            "revenue guidance",
            "beats estimates",
            "record revenue",
            "profit",
        ),
    ),
    (
        "regulatory / patent milestone",
        4.0,
        (
            "510(k)",
            "patent granted",
            "receives patent",
            "regulatory approval",
            "regulatory clearance",
        ),
    ),
    (
        "analyst catalyst",
        3.0,
        (
            "upgraded",
            "upgrade",
            "price target raised",
            "raises price target",
            "initiates coverage",
        ),
    ),
]


def get_json(url, timeout=25):
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
    if new is None or old in (None, 0):
        return None
    return (new / old - 1.0) * 100.0


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def scale(value, start, end, points):
    if value is None or end == start:
        return 0.0
    return clamp((value - start) / (end - start)) * points


def fmt(value, digits=2):
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def shorten(text, limit=150):
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def parse_timestamp(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def likely_common_stock(symbol):
    s = symbol.upper().strip()
    if not s or len(s) > 6:
        return False
    if len(s) < 5:
        return True
    if s.endswith(("WS", "WT", "RT", "UN")):
        return False
    if s.endswith(("W", "R", "U")):
        return False
    return True


def get_movers():
    url = f"{DATA_BASE}/v1beta1/screener/stocks/movers?top={TOP_MOVERS}"
    return get_json(url).get("gainers", [])


def get_snapshot(symbol):
    q = urllib.parse.urlencode({"feed": LIVE_FEED})
    url = f"{DATA_BASE}/v2/stocks/{urllib.parse.quote(symbol)}/snapshot?{q}"
    return get_json(url)


def get_bars(symbol, timeframe, start, end, limit, feed=None):
    params = urllib.parse.urlencode(
        {
            "timeframe": timeframe,
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "limit": limit,
            "adjustment": "raw",
            "feed": feed or LIVE_FEED,
            "sort": "asc",
        }
    )
    url = f"{DATA_BASE}/v2/stocks/{urllib.parse.quote(symbol)}/bars?{params}"
    return get_json(url).get("bars") or []


def get_multi_bars(symbols, timeframe, start, end, limit=10000, feed=None):
    """Fetch bars for many symbols, following Alpaca pagination."""
    if not symbols:
        return {}

    merged = defaultdict(list)
    page_token = None
    pages = 0

    while True:
        query = {
            "symbols": ",".join(symbols),
            "timeframe": timeframe,
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "limit": limit,
            "adjustment": "raw",
            "feed": feed or LIVE_FEED,
            "sort": "asc",
        }
        if page_token:
            query["page_token"] = page_token

        params = urllib.parse.urlencode(query)
        data = get_json(f"{DATA_BASE}/v2/stocks/bars?{params}")
        for symbol, bars in (data.get("bars") or {}).items():
            merged[str(symbol).upper()].extend(bars or [])

        page_token = data.get("next_page_token")
        pages += 1
        if not page_token or pages >= 10:
            break

    return dict(merged)


def get_news_for_symbols(symbols, now_utc):
    if not symbols:
        return []
    params = urllib.parse.urlencode(
        {
            "symbols": ",".join(symbols),
            "start": (now_utc - timedelta(hours=NEWS_LOOKBACK_HOURS))
            .isoformat()
            .replace("+00:00", "Z"),
            "end": now_utc.isoformat().replace("+00:00", "Z"),
            "sort": "desc",
            "limit": NEWS_LIMIT,
            "include_content": "false",
        }
    )
    url = f"{DATA_BASE}/v1beta1/news?{params}"
    return get_json(url).get("news") or []


def is_regular_session(now_et):
    if now_et.weekday() >= 5:
        return False
    minutes = now_et.hour * 60 + now_et.minute
    return (9 * 60 + 30) <= minutes <= (16 * 60)


def session_fraction(now_et):
    open_minutes = 9 * 60 + 30
    close_minutes = 16 * 60
    current_minutes = now_et.hour * 60 + now_et.minute
    if current_minutes <= open_minutes:
        return 1.0 / 390.0
    if current_minutes >= close_minutes:
        return 1.0
    return max(1.0 / 390.0, (current_minutes - open_minutes) / 390.0)


def base_quality_score(c):
    score = 0.0
    if MIN_PRICE <= c["price"] <= MAX_PRICE:
        score += 5.0
    score += scale(c.get("day_pct"), 0.0, 20.0, 15.0)
    score += scale(c.get("liquidity_dollar_volume"), 1_000_000, 50_000_000, 12.0)
    score += scale(c.get("intraday_range_pct"), 1.0, 12.0, 8.0)
    d = c.get("distance_from_high_pct")
    if d is not None:
        score += clamp((12.0 - d) / 12.0) * 10.0

    # IEX is one exchange, not the consolidated NBBO. A tight IEX spread can
    # add confidence, but a wide/missing IEX quote is handled as a warning
    # rather than a hard rejection.
    s = c.get("spread_pct")
    if s is not None:
        score += clamp((MAX_IEX_SPREAD_WARNING_PCT - s) / MAX_IEX_SPREAD_WARNING_PCT) * 10.0

    if c.get("above_vwap"):
        score += 10.0
    return round(score, 1)


def live_bonus_score(c):
    score = 0.0
    score += scale(c.get("volume_pace"), 1.0, 5.0, 12.0)
    score += scale(c.get("momentum_5m"), 0.0, 3.0, 10.0)
    score += scale(c.get("momentum_15m"), 0.0, 6.0, 8.0)
    return round(score, 1)


def critical_fail_count(c):
    """Count strongest liquidity/price failures, preferring delayed SIP when available."""
    count = 0
    price = c.get("price") or 0
    if price < MIN_PRICE or price > MAX_PRICE:
        count += 1

    if c.get("liquidity_source") == "delayed_sip":
        if c.get("liquidity_dollar_volume", 0) < MIN_TOTAL_DOLLAR_VOLUME:
            count += 1
    else:
        if c.get("dollar_volume", 0) < MIN_RAW_IEX_DOLLAR_VOLUME_FALLBACK:
            count += 1
        if c.get("liquidity_dollar_volume", 0) < MIN_TOTAL_DOLLAR_VOLUME:
            count += 1
    return count


GRADE_RANK = {"A": 4, "B": 3, "C": 2, "REJECT": 1}


def ranking_key(c):
    """
    Rank tradability before excitement. Once setup grades exist, grade comes first.
    """
    return (
        GRADE_RANK.get(c.get("setup_grade"), 0),
        1 if c.get("passed_base_filters") else 0,
        -c.get("critical_fail_count", critical_fail_count(c)),
        -c.get("failed_count", 99),
        -c.get("warning_count", 99),
        -len(c.get("setup_flags", [])),
        c.get("score", 0),
        c.get("day_pct") or -999,
    )


def setup_risk_flags(c):
    flags = []
    dist_vwap = c.get("distance_from_vwap_pct")
    if dist_vwap is not None and dist_vwap >= MAX_VWAP_EXTENSION_PCT:
        flags.append(f"extended > {MAX_VWAP_EXTENSION_PCT:.0f}% above VWAP")
    day_pct = c.get("day_pct")
    if day_pct is not None and day_pct >= EXTREME_DAY_MOVE_PCT:
        flags.append(f"extreme day move > {EXTREME_DAY_MOVE_PCT:.0f}%")
    return flags


def assign_setup_grade(c, now_et):
    """Assign a research grade. A grades require live-session confirmation."""
    live = is_regular_session(now_et)
    flags = setup_risk_flags(c)
    c["setup_flags"] = flags
    c["grade_mode"] = "live" if live else "off_hours_preview"

    critical = c.get("critical_fail_count", critical_fail_count(c))
    failed = c.get("failed_count", 99)
    warnings = c.get("warning_count", 0)
    reasons = []

    if critical > 0:
        grade, label = "REJECT", "REJECT"
        reasons.append("critical price/liquidity failure")
    elif failed >= 2:
        grade, label = "REJECT", "REJECT"
        reasons.append(f"{failed} base-filter failures")
    elif failed == 1:
        grade, label = "C", "NEAR MISS"
        reasons.append(c.get("failed_filters", ["one base-filter failure"])[0])
    elif not live:
        # Off-hours cannot earn an A because 5m/15m momentum and volume pace are absent.
        grade, label = "B", "PREVIEW"
        reasons.append("base filters pass; awaiting live confirmation")
        if warnings:
            reasons.append("execution warning present")
        if flags:
            reasons.append("chase/extension risk present")
    else:
        m5 = c.get("momentum_5m")
        m15 = c.get("momentum_15m")
        pace = c.get("volume_pace")
        from_high = c.get("distance_from_high_pct")
        confirmations = [
            m5 is not None and m5 >= LIVE_A_MIN_5M,
            m15 is not None and m15 >= LIVE_A_MIN_15M,
            pace is not None and pace >= LIVE_A_MIN_VOLUME_PACE,
            from_high is not None and from_high <= LIVE_A_MAX_FROM_HIGH_PCT,
            bool(c.get("above_vwap")),
        ]
        confirmation_count = sum(confirmations)
        c["live_confirmation_count"] = confirmation_count

        severe_negative_news = c.get("news_bonus", 0) <= -4.0
        if (
            confirmation_count >= 4
            and warnings == 0
            and not flags
            and not severe_negative_news
            and c.get("score", 0) >= 65
        ):
            grade, label = "A", "HIGH"
            reasons.append(f"{confirmation_count}/5 live confirmations")
        else:
            grade, label = "B", "WATCH"
            reasons.append(f"{confirmation_count}/5 live confirmations")
            if warnings:
                reasons.append("execution warning present")
            if flags:
                reasons.append("chase/extension risk present")
            if severe_negative_news:
                reasons.append("negative catalyst risk")

    c["setup_grade"] = grade
    c["setup_label"] = label
    c["grade_reasons"] = reasons

    if live and grade == "A":
        c["alert_tier"] = "HIGH"
        c["alert_ready"] = True
    elif live and grade == "B" and failed == 0:
        c["alert_tier"] = "WATCH"
        c["alert_ready"] = False
    elif not live and grade == "B":
        c["alert_tier"] = "PREVIEW"
        c["alert_ready"] = False
    else:
        c["alert_tier"] = None
        c["alert_ready"] = False
    return c


def assign_setup_grades(rows, now_et):
    for c in rows:
        assign_setup_grade(c, now_et)


def evaluate_base_filters(c):
    reasons = []
    if c["price"] < MIN_PRICE:
        reasons.append("price < $1")
    if c["price"] > MAX_PRICE:
        reasons.append("price > $50")
    if c.get("day_pct") is None or c["day_pct"] < MIN_DAY_PCT:
        reasons.append("day gain < 3%")

    if c.get("liquidity_source") == "delayed_sip":
        if c.get("liquidity_dollar_volume", 0) < MIN_TOTAL_DOLLAR_VOLUME:
            reasons.append("delayed SIP dollar volume < $5M")
    else:
        if c.get("dollar_volume", 0) < MIN_RAW_IEX_DOLLAR_VOLUME_FALLBACK:
            reasons.append("IEX dollar volume < $25k (fallback)")
        if c.get("liquidity_dollar_volume", 0) < MIN_TOTAL_DOLLAR_VOLUME:
            reasons.append("est. total dollar volume < $5M (fallback)")

    if c.get("intraday_range_pct") is None or c["intraday_range_pct"] < MIN_INTRADAY_RANGE_PCT:
        reasons.append("range < 3%")
    if c.get("distance_from_high_pct") is None or c["distance_from_high_pct"] > MAX_DISTANCE_FROM_HIGH_PCT:
        reasons.append("> 8% below high")
    if not c.get("above_vwap"):
        reasons.append("below VWAP")
    return reasons


def evaluate_tradability_warnings(c):
    warnings = []
    spread = c.get("spread_pct")
    if spread is None:
        warnings.append("IEX spread unavailable")
    elif spread > MAX_IEX_SPREAD_WARNING_PCT:
        warnings.append(f"IEX spread > {MAX_IEX_SPREAD_WARNING_PCT:.0f}%")
    return warnings


def refresh_quality(c):
    """Recompute filters and scoring after better liquidity data arrives."""
    reasons = evaluate_base_filters(c)
    warnings = evaluate_tradability_warnings(c)
    c["failed_filters"] = reasons
    c["tradability_warnings"] = warnings
    c["failed_count"] = len(reasons)
    c["warning_count"] = len(warnings)
    c["passed_base_filters"] = len(reasons) == 0
    c["critical_fail_count"] = critical_fail_count(c)
    c["base_score"] = base_quality_score(c)
    c["score"] = round(
        max(0.0, min(100.0, c["base_score"] + c.get("live_bonus", 0) + c.get("news_bonus", 0))),
        1,
    )
    return c


def enrich_delayed_sip_liquidity(rows, now_utc, now_et):
    """Use consolidated SIP bars through ~15 minutes ago for liquidity when possible."""
    if not rows:
        return

    et = ZoneInfo("America/New_York")
    session_dates = [c.get("session_date") for c in rows if c.get("session_date")]
    if not session_dates:
        return

    session_date = Counter(session_dates).most_common(1)[0][0]
    try:
        session_day = datetime.strptime(session_date, "%Y-%m-%d").date()
    except ValueError:
        return

    start_et = datetime(
        session_day.year, session_day.month, session_day.day, 9, 30, tzinfo=et
    )
    close_et = datetime(
        session_day.year, session_day.month, session_day.day, 16, 0, tzinfo=et
    )

    if session_day == now_et.date() and is_regular_session(now_et):
        end_et = min(close_et, now_et - timedelta(minutes=SIP_LIQUIDITY_DELAY_MINUTES))
    else:
        end_et = close_et

    if end_et <= start_et:
        for c in rows:
            c["sip_liquidity_status"] = "not_available_yet"
        return

    symbols = [c["symbol"] for c in rows]
    try:
        bars_by_symbol = get_multi_bars(
            symbols,
            SIP_LIQUIDITY_TIMEFRAME,
            start_et.astimezone(timezone.utc),
            end_et.astimezone(timezone.utc),
            limit=10000,
            feed=HISTORICAL_FEED,
        )
    except Exception as exc:
        for c in rows:
            c["sip_liquidity_status"] = "fallback_iex_estimate"
            c["sip_liquidity_error"] = str(exc)
        print(f"WARN delayed SIP liquidity enrichment: {exc}")
        return

    for c in rows:
        bars = bars_by_symbol.get(c["symbol"], [])
        sip_volume = 0.0
        sip_dollar = 0.0
        for b in bars:
            vol = float(b.get("v") or 0)
            px = float(b.get("vw") or b.get("c") or 0)
            if vol > 0 and px > 0:
                sip_volume += vol
                sip_dollar += vol * px

        if sip_volume > 0 and sip_dollar > 0:
            c["sip_delayed_volume"] = int(sip_volume)
            c["sip_delayed_dollar_volume"] = round(sip_dollar, 2)
            c["sip_liquidity_asof"] = end_et.isoformat()
            c["sip_liquidity_status"] = "available"
            c["liquidity_source"] = "delayed_sip"
            c["liquidity_dollar_volume"] = round(sip_dollar, 2)
        else:
            c["sip_liquidity_status"] = "no_bars_fallback_iex_estimate"

        refresh_quality(c)


def analyze_snapshot(symbol):
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
    daily_ts = daily.get("t")
    session_date = None
    if daily_ts:
        try:
            session_date = (
                datetime.fromisoformat(str(daily_ts).replace("Z", "+00:00"))
                .astimezone(ZoneInfo("America/New_York"))
                .date()
                .isoformat()
            )
        except ValueError:
            session_date = None

    if not price or not prev_close or not high or not low:
        return None

    day_pct = pct_change(price, prev_close)
    dollar_volume = price * volume
    estimated_total_dollar_volume = (
        dollar_volume / IEX_MARKET_SHARE_ESTIMATE
        if IEX_MARKET_SHARE_ESTIMATE > 0
        else dollar_volume
    )
    intraday_range_pct = pct_change(high, low)
    distance_from_high_pct = ((high - price) / high) * 100 if high else None
    distance_from_vwap_pct = pct_change(price, vwap) if vwap else None
    above_vwap = bool(vwap and price > vwap)

    spread_pct = None
    if bid > 0 and ask > 0 and ask >= bid:
        midpoint = (bid + ask) / 2.0
        spread_pct = ((ask - bid) / midpoint) * 100 if midpoint else None

    c = {
        "symbol": symbol,
        "price": round(price, 4),
        "prev_close": round(prev_close, 4),
        "day_pct": round(day_pct, 2) if day_pct is not None else None,
        "volume": int(volume),
        "dollar_volume": round(dollar_volume, 2),
        "estimated_total_dollar_volume": round(estimated_total_dollar_volume, 2),
        "liquidity_source": "iex_estimate",
        "liquidity_dollar_volume": round(estimated_total_dollar_volume, 2),
        "session_date": session_date,
        "spread_pct": round(spread_pct, 3) if spread_pct is not None else None,
        "intraday_range_pct": round(intraday_range_pct, 2)
        if intraday_range_pct is not None
        else None,
        "distance_from_high_pct": round(distance_from_high_pct, 2)
        if distance_from_high_pct is not None
        else None,
        "vwap": round(vwap, 4) if vwap else None,
        "distance_from_vwap_pct": round(distance_from_vwap_pct, 2)
        if distance_from_vwap_pct is not None
        else None,
        "above_vwap": above_vwap,
    }

    c["live_bonus"] = 0.0
    c["news_bonus"] = 0.0
    refresh_quality(c)
    return c


def avg_daily_volume(symbol, now_utc):
    bars = get_bars(symbol, "1Day", now_utc - timedelta(days=45), now_utc, 35)
    if not bars:
        return None

    today_et = now_utc.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    completed = [
        b for b in bars if str(b.get("t", ""))[:10] != today_et and b.get("v")
    ]
    completed = completed[-20:]
    if not completed:
        return None
    return sum(float(b["v"]) for b in completed) / len(completed)


def recent_momentum(symbol, now_utc, current_price):
    bars = get_bars(symbol, "1Min", now_utc - timedelta(minutes=60), now_utc, 60)
    if len(bars) < 2:
        return None, None

    m5 = None
    m15 = None
    if len(bars) >= 6 and bars[-6].get("c"):
        m5 = pct_change(current_price, float(bars[-6]["c"]))
    if len(bars) >= 16 and bars[-16].get("c"):
        m15 = pct_change(current_price, float(bars[-16]["c"]))
    return m5, m15


def enrich_live(c, now_utc, now_et):
    try:
        m5, m15 = recent_momentum(c["symbol"], now_utc, c["price"])
        avg_vol = avg_daily_volume(c["symbol"], now_utc)
    except Exception as exc:
        c["enrichment_error"] = str(exc)
        return c

    pace = None
    if avg_vol and avg_vol > 0:
        expected_so_far = avg_vol * session_fraction(now_et)
        if expected_so_far > 0:
            pace = c["volume"] / expected_so_far

    c["momentum_5m"] = round(m5, 2) if m5 is not None else None
    c["momentum_15m"] = round(m15, 2) if m15 is not None else None
    c["avg_20d_volume"] = round(avg_vol, 0) if avg_vol is not None else None
    c["volume_pace"] = round(pace, 2) if pace is not None else None
    c["live_bonus"] = live_bonus_score(c)
    c["score"] = round(c["base_score"] + c["live_bonus"] + c.get("news_bonus", 0), 1)
    return c


def article_catalyst_score(article, now_utc):
    headline = str(article.get("headline") or "")
    summary = str(article.get("summary") or "")
    text = f"{headline} {summary}".lower()

    category = "recent news"
    raw_score = 0.0
    matched_keywords = []

    for rule_category, rule_score, keywords in CATALYST_RULES:
        hits = [kw for kw in keywords if kw in text]
        if hits and abs(rule_score) > abs(raw_score):
            category = rule_category
            raw_score = rule_score
            matched_keywords = hits[:3]

    created = parse_timestamp(article.get("created_at") or article.get("updated_at"))
    age_hours = None
    recency_factor = 0.5
    if created is not None:
        age_hours = max(0.0, (now_utc - created.astimezone(timezone.utc)).total_seconds() / 3600)
        if age_hours <= 6:
            recency_factor = 1.0
        elif age_hours <= 24:
            recency_factor = 0.75
        elif age_hours <= NEWS_LOOKBACK_HOURS:
            recency_factor = 0.5
        else:
            recency_factor = 0.0

    adjusted = round(raw_score * recency_factor, 1)

    return {
        "headline": headline,
        "summary": shorten(summary, 240),
        "source": article.get("source"),
        "url": article.get("url"),
        "created_at": article.get("created_at"),
        "age_hours": round(age_hours, 1) if age_hours is not None else None,
        "category": category,
        "raw_catalyst_score": raw_score,
        "catalyst_score": adjusted,
        "matched_keywords": matched_keywords,
    }


def enrich_news(rows, now_utc):
    targets = rows[:NEWS_TOP]
    symbols = [c["symbol"] for c in targets]
    if not symbols:
        return

    try:
        articles = get_news_for_symbols(symbols, now_utc)
    except Exception as exc:
        for c in targets:
            c["news_status"] = "error"
            c["news_error"] = str(exc)
        print(f"WARN news enrichment: {exc}")
        return

    by_symbol = defaultdict(list)
    for article in articles:
        for symbol in article.get("symbols") or []:
            by_symbol[str(symbol).upper()].append(article)

    for c in targets:
        relevant = by_symbol.get(c["symbol"], [])
        if not relevant:
            c["news_status"] = "no_recent_news"
            c["news_bonus"] = 0.0
            continue

        scored = [article_catalyst_score(article, now_utc) for article in relevant]
        # Prefer a meaningful catalyst by absolute score. If no rules match,
        # prefer the newest article.
        scored.sort(
            key=lambda a: (
                abs(a.get("catalyst_score") or 0),
                -(a.get("age_hours") if a.get("age_hours") is not None else 9999),
            ),
            reverse=True,
        )
        best = scored[0]

        c["news_article_count"] = len(relevant)
        c["catalyst"] = best

        if best.get("raw_catalyst_score", 0) == 0:
            # A generic "stocks moving" mention is useful context, but it is
            # not evidence of a specific company catalyst and gets no bonus.
            c["news_status"] = "generic_news_only"
            c["news_bonus"] = 0.0
        else:
            c["news_status"] = "catalyst_found"
            c["news_bonus"] = best["catalyst_score"]

        c["score"] = round(
            max(
                0.0,
                min(
                    100.0,
                    c.get("base_score", 0)
                    + c.get("live_bonus", 0)
                    + c.get("news_bonus", 0),
                ),
            ),
            1,
        )


def regular_session_bars_by_day(bars):
    et = ZoneInfo("America/New_York")
    grouped = defaultdict(list)
    for b in bars:
        ts = b.get("t")
        if not ts:
            continue
        try:
            dt_utc = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        dt_et = dt_utc.astimezone(et)
        minutes = dt_et.hour * 60 + dt_et.minute
        if dt_et.weekday() >= 5:
            continue
        if not ((9 * 60 + 30) <= minutes < (16 * 60)):
            continue
        grouped[dt_et.date()].append((dt_et, b))

    for day in grouped:
        grouped[day].sort(key=lambda x: x[0])
    return dict(sorted(grouped.items()))


def closest_bar_index(day_bars, target_minutes):
    best_idx = None
    best_delta = None
    for i, (dt_et, _) in enumerate(day_bars):
        bar_minutes = dt_et.hour * 60 + dt_et.minute
        delta = abs(bar_minutes - target_minutes)
        if best_delta is None or delta < best_delta:
            best_idx = i
            best_delta = delta
    return best_idx


def safe_forward_return(day_bars, setup_idx, bars_forward):
    target_idx = setup_idx + bars_forward
    if target_idx >= len(day_bars):
        return None

    setup_close = float(day_bars[setup_idx][1].get("c") or 0)
    future_close = float(day_bars[target_idx][1].get("c") or 0)
    if setup_close <= 0 or future_close <= 0:
        return None
    return pct_change(future_close, setup_close)


def summarize_returns(samples, key):
    vals = [s[key] for s in samples if s.get(key) is not None]
    if not vals:
        return {
            "n": 0,
            "up_pct": None,
            "median_return_pct": None,
            "gain_5pct_or_more_pct": None,
        }
    return {
        "n": len(vals),
        "up_pct": round(sum(v > 0 for v in vals) / len(vals) * 100, 1),
        "median_return_pct": round(median(vals), 2),
        "gain_5pct_or_more_pct": round(sum(v >= 5 for v in vals) / len(vals) * 100, 1),
    }


def historical_continuation(symbol, current_day_pct, now_utc, now_et):
    if not is_regular_session(now_et):
        return {
            "status": "skipped_off_hours",
            "message": "Historical intraday comparison activates during regular market hours.",
        }

    if current_day_pct is None:
        return {"status": "no_current_day_pct"}

    start = now_utc - timedelta(days=HISTORY_LOOKBACK_DAYS)
    historical_end = now_utc - timedelta(minutes=16)

    history_feed = HISTORICAL_FEED
    try:
        bars = get_bars(
            symbol,
            "15Min",
            start,
            historical_end,
            10000,
            feed=HISTORICAL_FEED,
        )
    except Exception:
        # If delayed historical SIP is unavailable on the account, fall back
        # to IEX rather than losing the historical module entirely.
        history_feed = LIVE_FEED
        bars = get_bars(
            symbol,
            "15Min",
            start,
            historical_end,
            10000,
            feed=LIVE_FEED,
        )

    grouped = regular_session_bars_by_day(bars)
    grouped = {day: day_bars for day, day_bars in grouped.items() if day < now_et.date()}
    days = list(grouped.keys())

    if len(days) < 5:
        return {"status": "insufficient_history", "days_available": len(days)}

    target_minutes = now_et.hour * 60 + now_et.minute
    possible = []

    for i in range(1, len(days)):
        day = days[i]
        prev_day = days[i - 1]
        day_bars = grouped[day]
        prev_bars = grouped[prev_day]

        if not day_bars or not prev_bars:
            continue

        prev_close = float(prev_bars[-1][1].get("c") or 0)
        if prev_close <= 0:
            continue

        setup_idx = closest_bar_index(day_bars, target_minutes)
        if setup_idx is None:
            continue

        setup_close = float(day_bars[setup_idx][1].get("c") or 0)
        if setup_close <= 0:
            continue

        setup_day_pct = pct_change(setup_close, prev_close)
        if setup_day_pct is None or setup_day_pct < 2.0:
            continue

        possible.append(
            {
                "date": str(day),
                "setup_day_pct": round(setup_day_pct, 2),
                "similarity_diff_pct": round(abs(setup_day_pct - current_day_pct), 2),
                "return_15m_pct": safe_forward_return(day_bars, setup_idx, 1),
                "return_30m_pct": safe_forward_return(day_bars, setup_idx, 2),
                "return_60m_pct": safe_forward_return(day_bars, setup_idx, 4),
            }
        )

    if not possible:
        return {"status": "no_comparable_positive_days"}

    possible.sort(key=lambda s: s["similarity_diff_pct"])
    samples = possible[:HISTORY_MAX_SAMPLES]
    avg_diff = sum(s["similarity_diff_pct"] for s in samples) / len(samples)

    strong_threshold = max(3.0, abs(current_day_pct) * 0.35)
    moderate_threshold = max(6.0, abs(current_day_pct) * 0.60)

    if len(samples) >= 5 and avg_diff <= strong_threshold:
        quality = "strong"
    elif len(samples) >= 5 and avg_diff <= moderate_threshold:
        quality = "moderate"
    else:
        quality = "weak"

    return {
        "status": "ok",
        "method": "same ticker, same time-of-day, nearest historical day-gain setups",
        "data_feed": history_feed,
        "sample_count": len(samples),
        "quality": quality,
        "avg_setup_difference_pct": round(avg_diff, 2),
        "closest_setup_day_pct": samples[0]["setup_day_pct"],
        "farthest_selected_setup_day_pct": samples[-1]["setup_day_pct"],
        "next_15m": summarize_returns(samples, "return_15m_pct"),
        "next_30m": summarize_returns(samples, "return_30m_pct"),
        "next_60m": summarize_returns(samples, "return_60m_pct"),
        "samples": samples,
    }


def print_watchlist(rows):
    print("\nTOP WATCHLIST / NEAR-MISS CANDIDATES")
    print("-" * 168)
    print(
        f"{'SYM':<7}{'GRADE':>7}{'SCORE':>7}{'FAILS':>7}{'PRICE':>9}{'DAY%':>9}"
        f"{'5M%':>8}{'15M%':>8}{'VOLPACE':>9}{'IEX$K':>9}{'LIQ$M':>9}"
        f"{'SRC':>7}{'IEXSPR%':>10}{'FROMHI%':>10}{'VWAP':>7}  FILTERS / WARNINGS / FLAGS"
    )
    print("-" * 168)

    for c in rows[:WATCHLIST_SIZE]:
        failed = ", ".join(c.get("failed_filters", [])) or "PASS"
        warnings = ", ".join(c.get("tradability_warnings", []))
        flags = ", ".join(c.get("setup_flags", []))
        notes = [failed]
        if warnings:
            notes.append(f"WARN: {warnings}")
        if flags:
            notes.append(f"FLAG: {flags}")
        note = " | ".join(notes)
        src = "SIP15" if c.get("liquidity_source") == "delayed_sip" else "IEXx"
        print(
            f"{c['symbol']:<7}"
            f"{c.get('setup_grade', '-'):>7}"
            f"{c.get('score', 0):>7.1f}"
            f"{c.get('failed_count', 0):>7}"
            f"{c['price']:>9.2f}"
            f"{fmt(c.get('day_pct')):>9}"
            f"{fmt(c.get('momentum_5m')):>8}"
            f"{fmt(c.get('momentum_15m')):>8}"
            f"{fmt(c.get('volume_pace')):>9}"
            f"{(c.get('dollar_volume', 0) / 1_000):>9.0f}"
            f"{(c.get('liquidity_dollar_volume', 0) / 1_000_000):>9.1f}"
            f"{src:>7}"
            f"{fmt(c.get('spread_pct'), 3):>10}"
            f"{fmt(c.get('distance_from_high_pct')):>10}"
            f"{'YES' if c.get('above_vwap') else 'NO':>7}  "
            f"{note}"
        )


def print_catalysts(rows):
    print("\nNEWS / CATALYST CHECK - TOP CANDIDATES")
    print("-" * 105)

    for c in rows[:NEWS_TOP]:
        status = c.get("news_status")

        if status == "error":
            print(f"{c['symbol']}: news lookup error")
            continue

        if status == "no_recent_news" or not status:
            print(f"{c['symbol']}: no Alpaca news in last {NEWS_LOOKBACK_HOURS}h")
            continue

        cat = c.get("catalyst") or {}
        age = cat.get("age_hours")
        age_text = f"{age:.1f}h ago" if age is not None else "age unknown"

        if status == "generic_news_only":
            print(
                f"{c['symbol']}: no specific catalyst identified | "
                f"{age_text} | {c.get('news_article_count', 0)} recent mention(s)"
            )
            continue

        bonus = c.get("news_bonus", 0.0)
        print(
            f"{c['symbol']}: {cat.get('category', 'recent news')} | "
            f"news score {bonus:+.1f} | {age_text} | "
            f"{c.get('news_article_count', 0)} article(s)"
        )
        print(f"  {shorten(cat.get('headline'), 150)}")


def print_setup_grades(rows, now_et):
    mode = "LIVE" if is_regular_session(now_et) else "OFF-HOURS PREVIEW"
    print(f"\nSETUP GRADES / ALERT READINESS - {mode}")
    print("-" * 110)
    for c in rows[:WATCHLIST_SIZE]:
        flags = ", ".join(c.get("setup_flags", [])) or "none"
        reasons = "; ".join(c.get("grade_reasons", [])) or "-"
        tier = c.get("alert_tier") or "NONE"
        print(
            f"{c['symbol']}: grade {c.get('setup_grade', '-')} {c.get('setup_label', '')} "
            f"| alert {tier} | flags: {flags}"
        )
        print(f"  {reasons}")

    if not is_regular_session(now_et):
        print(
            "No A/HIGH grade is allowed off-hours. Live 5m/15m momentum and volume pace "
            "must confirm the setup during the regular session."
        )


def print_passers(rows):
    passed = [c for c in rows if c.get("passed_base_filters")]
    print("\nFULL BASE-FILTER PASSES")
    if not passed:
        print("No stocks passed every base filter on this scan.")
        return

    for c in passed[:10]:
        print(
            f"{c['symbol']}: score {c['score']:.1f} | "
            f"${c['price']:.2f} | day {fmt(c.get('day_pct'))}% | "
            f"5m {fmt(c.get('momentum_5m'))}% | "
            f"15m {fmt(c.get('momentum_15m'))}% | "
            f"vol pace {fmt(c.get('volume_pace'))}x | "
            f"news {c.get('news_bonus', 0):+.1f}"
        )


def print_historical(rows):
    print("\nHISTORICAL CONTINUATION - TOP CANDIDATES")
    any_history = False

    for c in rows[:HISTORICAL_TOP]:
        hist = c.get("historical")
        if not hist:
            continue

        if hist.get("status") == "skipped_off_hours":
            print(
                "Historical intraday continuation is skipped outside regular "
                "market hours; it will activate automatically during the next session."
            )
            return

        any_history = True
        print(f"\n{c['symbol']} | score {c['score']:.1f}")

        if hist.get("status") != "ok":
            print(f"  Historical status: {hist.get('status')}")
            continue

        print(
            f"  Similar past setups: {hist['sample_count']} "
            f"| similarity quality: {hist['quality']} "
            f"| avg setup difference: {hist['avg_setup_difference_pct']} pts "
            f"| historical feed: {hist.get('data_feed', 'unknown').upper()}"
        )

        for label, key in [
            ("15m", "next_15m"),
            ("30m", "next_30m"),
            ("60m", "next_60m"),
        ]:
            h = hist[key]
            print(
                f"  Next {label}: "
                f"up {fmt(h.get('up_pct'), 1)}% | "
                f"median {fmt(h.get('median_return_pct'))}% | "
                f"+5% or more {fmt(h.get('gain_5pct_or_more_pct'), 1)}% "
                f"(n={h.get('n', 0)})"
            )

    if not any_history:
        print("No historical continuation output available on this scan.")


def main():
    now_utc = datetime.now(timezone.utc)
    now_et = now_utc.astimezone(ZoneInfo("America/New_York"))

    print(f"Momentum scan v2.5 started: {now_et:%Y-%m-%d %H:%M:%S %Z}")
    print(
        "Mode: "
        + ("REGULAR MARKET SESSION" if is_regular_session(now_et) else "OFF-HOURS / TEST")
    )
    print(
        "Data: live momentum = IEX; liquidity prefers consolidated SIP volume delayed ~15m; "
        "2.5% IEX scaling is fallback only; IEX spread is a warning only."
    )

    movers = get_movers()
    print(f"Alpaca returned {len(movers)} gainers.")

    rows = []
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
            c = analyze_snapshot(symbol)
        except Exception as exc:
            print(f"WARN {symbol}: snapshot error: {exc}")
            rejection_counts["API/data error"] += 1
            continue

        if c is None:
            rejection_counts["missing market data"] += 1
            continue

        rows.append(c)
        time.sleep(0.03)

    # Replace the rough IEX market-share liquidity estimate with real consolidated
    # SIP volume through ~15 minutes ago whenever the Basic plan allows it.
    enrich_delayed_sip_liquidity(rows, now_utc, now_et)

    rejection_counts = Counter(
        reason for c in rows for reason in c.get("failed_filters", [])
    )

    rows.sort(key=ranking_key, reverse=True)

    if is_regular_session(now_et):
        for c in rows[:ENRICH_TOP]:
            enrich_live(c, now_utc, now_et)
            time.sleep(0.05)
    else:
        for c in rows:
            c["live_data_status"] = "skipped_off_hours"

    rows.sort(key=ranking_key, reverse=True)

    # One batched news call for the displayed watchlist.
    enrich_news(rows, now_utc)

    rows.sort(key=ranking_key, reverse=True)

    for c in rows[:HISTORICAL_TOP]:
        try:
            c["historical"] = historical_continuation(
                c["symbol"],
                c.get("day_pct"),
                now_utc,
                now_et,
            )
        except Exception as exc:
            c["historical"] = {"status": "error", "message": str(exc)}

        if c["historical"].get("status") == "skipped_off_hours":
            break

        time.sleep(0.05)

    # Grades are intentionally separate from historical continuation for now.
    # Monday's live test will tell us whether history deserves score/grade weight.
    assign_setup_grades(rows, now_et)
    rows.sort(key=ranking_key, reverse=True)

    print_watchlist(rows)
    print_catalysts(rows)
    print_setup_grades(rows, now_et)
    print_passers(rows)
    print_historical(rows)

    print("\nSCAN SUMMARY")
    print(f"Analyzed common-stock candidates: {len(rows)}")
    print(f"Passed every base filter: {sum(c['passed_base_filters'] for c in rows)}")
    grade_counts = Counter(c.get("setup_grade", "unknown") for c in rows)
    print(
        "Setup grades: "
        + ", ".join(f"{grade}={grade_counts.get(grade, 0)}" for grade in ["A", "B", "C", "REJECT"])
    )
    print(f"Likely warrants/rights/units excluded: {len(excluded_symbols)}")

    if excluded_symbols:
        print("Excluded symbols: " + ", ".join(excluded_symbols[:25]))

    if rejection_counts:
        print("Most common rejection reasons:")
        for reason, count in rejection_counts.most_common():
            print(f"  - {reason}: {count}")

    liquidity_counts = Counter(c.get("liquidity_source", "unknown") for c in rows)
    print("Liquidity source usage:")
    for source, count in liquidity_counts.most_common():
        print(f"  - {source}: {count}")

    warning_counts = Counter(
        warning
        for c in rows
        for warning in c.get("tradability_warnings", [])
    )
    if warning_counts:
        print("Most common IEX tradability warnings:")
        for warning, count in warning_counts.most_common():
            print(f"  - {warning}: {count}")

    print("\nJSON RESULTS - TOP WATCHLIST")
    print(json.dumps(rows[:WATCHLIST_SIZE], indent=2))


if __name__ == "__main__":
    main()
