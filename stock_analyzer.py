import json, math, os, urllib.parse, urllib.request, urllib.error
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

try:
    from tradier_live import get_quotes as get_tradier_quotes
    from tradier_live import get_timesales_bars as get_tradier_timesales_bars
except Exception:
    get_tradier_quotes = None
    get_tradier_timesales_bars = None

DATA_BASE = "https://data.alpaca.markets"
API_KEY = os.environ.get("ALPACA_API_KEY", "").strip()
API_SECRET = os.environ.get("ALPACA_SECRET_KEY", "").strip()
LIVE_FEED = os.environ.get("ALPACA_LIVE_FEED", "iex").strip().lower() or "iex"
HISTORICAL_FEED = os.environ.get("ALPACA_HISTORICAL_FEED", "sip").strip().lower() or "sip"
TRADIER_TOKEN = (
    os.environ.get("TRADIER_ACCESS_TOKEN", "").strip()
    or os.environ.get("TRADIER_TOKEN", "").strip()
)
USE_TRADIER = bool(
    TRADIER_TOKEN
    and get_tradier_quotes is not None
    and get_tradier_timesales_bars is not None
)
LIVE_MARKET_PROVIDER = "tradier" if USE_TRADIER else "alpaca"
LIVE_MARKET_LABEL = "TRADIER CONSOLIDATED" if USE_TRADIER else LIVE_FEED.upper()
ANALYZER_FEATURE_VERSION = "analyzer-features-v4-full-spectrum"
ANALYZER_ENGINE_VERSION = "trade-plan-v5-full-spectrum"
ET = ZoneInfo("America/New_York")

CATALYST_RULES = [
    ("FDA / clinical", 8, ["fda", "phase 3", "phase iii", "clinical trial", "topline", "approval"]),
    ("M&A / strategic", 8, ["merger", "acquisition", "acquire", "strategic alternatives", "definitive agreement"]),
    ("contract / order", 6, ["contract", "purchase order", "award", "partnership", "customer"]),
    ("earnings / guidance", 5, ["earnings", "revenue", "guidance", "profit", "ebitda"]),
    ("financing / dilution", -7, ["offering", "registered direct", "convertible", "atm", "warrant", "dilution"]),
    ("reverse split", -6, ["reverse split", "reverse stock split"]),
    ("bankruptcy / distress", -10, ["bankruptcy", "chapter 11", "going concern", "default"]),
]

def _headers():
    if not API_KEY or not API_SECRET:
        raise RuntimeError("Missing ALPACA_API_KEY / ALPACA_SECRET_KEY")
    return {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": API_SECRET, "Accept":"application/json"}

def get_json(url):
    req = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Alpaca HTTP {e.code}: {body[:500]}")

def _iso(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def bars(symbol, timeframe, start, end, limit=1000, feed=None, adjustment="raw"):
    q = urllib.parse.urlencode({"timeframe":timeframe,"start":_iso(start),"end":_iso(end),"limit":limit,"adjustment":adjustment,"feed":feed or LIVE_FEED,"sort":"asc"})
    return get_json(f"{DATA_BASE}/v2/stocks/{urllib.parse.quote(symbol)}/bars?{q}").get("bars") or []

def snapshot(symbol, feed=None):
    q=urllib.parse.urlencode({"feed":feed or LIVE_FEED})
    return get_json(f"{DATA_BASE}/v2/stocks/{urllib.parse.quote(symbol)}/snapshot?{q}")

def _parse_market_timestamp(value):
    if not value:
        return None
    try:
        # Alpaca timestamps may include nanoseconds; Python's parser accepts
        # microseconds, so trim fractional precision safely before parsing.
        raw=str(value).strip().replace("Z", "+00:00")
        if "." in raw:
            head, tail = raw.split(".", 1)
            frac, suffix = tail, ""
            for marker in ("+", "-"):
                pos = frac.find(marker)
                if pos > 0:
                    suffix = frac[pos:]
                    frac = frac[:pos]
                    break
            frac = frac[:6]
            raw = f"{head}.{frac}{suffix}" if frac else f"{head}{suffix}"
        dt=datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt=dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def _market_age_seconds(value, now):
    dt=_parse_market_timestamp(value)
    if dt is None:
        return None
    return max(0.0, (now-dt).total_seconds())

def news(symbol, now, hours=96, limit=50):
    q=urllib.parse.urlencode({"symbols":symbol,"start":_iso(now-timedelta(hours=hours)),"end":_iso(now),"sort":"desc","limit":limit,"include_content":"false"})
    return get_json(f"{DATA_BASE}/v1beta1/news?{q}").get("news") or []

def pct(a,b): return None if not b else (a/b-1)*100

def fnum(x):
    try:return float(x)
    except:return None

def is_regular(now_et):
    m=now_et.hour*60+now_et.minute
    return now_et.weekday()<5 and 570<=m<960

def session_fraction(now_et):
    m=now_et.hour*60+now_et.minute
    return min(1,max(1/390,(m-570)/390))

def try_sip_delayed_bars(symbol, timeframe, start, end, limit=1000):
    # Free/basic Alpaca accounts can query consolidated SIP once data is delayed enough.
    safe_end=min(end, datetime.now(timezone.utc)-timedelta(minutes=16))
    if safe_end<=start:return [], "unavailable"
    try:return bars(symbol,timeframe,start,safe_end,limit,feed="sip"), "delayed SIP"
    except Exception:return bars(symbol,timeframe,start,safe_end,limit,feed=LIVE_FEED), LIVE_FEED.upper()

def session_vwap_from_bars(bs):
    pv=v=0.0
    for b in bs:
        vol=fnum(b.get("v")) or 0
        bar_vwap=fnum(b.get("vw"))
        typical=bar_vwap if bar_vwap is not None else (
            ((fnum(b.get("h")) or 0)+(fnum(b.get("l")) or 0)+(fnum(b.get("c")) or 0))/3
        )
        pv += typical*vol; v += vol
    return pv/v if v else None

def _bar_time_et(b):
    try:
        return datetime.fromisoformat(str(b.get("t", "")).replace("Z", "+00:00")).astimezone(ET)
    except Exception:
        return None


def _regular_session_bar(b):
    dt = _bar_time_et(b)
    if dt is None:
        return False
    minute = dt.hour * 60 + dt.minute
    return dt.weekday() < 5 and 570 <= minute < 960


def impulse_pullback_context(bs, current_price=None, atr_pct=None):
    """Describe the strongest recent impulse -> pullback -> bounce structure.

    The detector searches for a meaningful low-to-later-high impulse, measures
    how much of THAT move has been retraced, and tracks whether price has begun
    recovering from the deepest pullback. Retracement is therefore expressed as
    a percentage of the preceding impulse, not simply percent below the day high.
    """
    rows=[]
    for b in bs or []:
        h=fnum(b.get("h")); l=fnum(b.get("l")); close=fnum(b.get("c")); vol=fnum(b.get("v")) or 0.0
        if h is None or l is None or close is None or h <= 0 or l <= 0:
            continue
        rows.append({"h":h,"l":l,"c":close,"v":vol,"t":b.get("t")})
    if len(rows) < 8:
        return {"status":"insufficient_data","detected":False}

    price=fnum(current_price) or rows[-1]["c"]
    min_impulse=max(7.0, min(18.0, (fnum(atr_pct) or 8.0)*0.75))
    candidates=[]
    n=len(rows)
    # Local impulses are more useful than always anchoring at the session low.
    # Search up to roughly two hours behind each peak for the lowest prior low.
    for peak_idx in range(5,n-1):
        start=max(0,peak_idx-120)
        low_idx=min(range(start,peak_idx), key=lambda i: rows[i]["l"])
        low=rows[low_idx]["l"]; peak=rows[peak_idx]["h"]
        if peak <= low:
            continue
        move_pct=(peak/low-1.0)*100.0
        duration=peak_idx-low_idx
        if move_pct < min_impulse or duration < 3:
            continue
        after=rows[peak_idx+1:]
        if not after:
            continue
        post_rel=min(range(len(after)), key=lambda i: after[i]["l"])
        trough_idx=peak_idx+1+post_rel
        trough=rows[trough_idx]["l"]
        run=peak-low
        max_retrace=(peak-trough)/run*100.0
        current_retrace=(peak-price)/run*100.0
        recovery=max_retrace-current_retrace
        # Favor large, recent impulses that have actually begun a pullback.
        age=n-1-peak_idx
        recency=max(0.35,1.0-age/max(30.0,n*0.9))
        shape=1.0 if 12 <= max_retrace <= 72 else 0.72
        score=move_pct*recency*shape
        candidates.append((score,low_idx,peak_idx,trough_idx,low,peak,trough,move_pct,max_retrace,current_retrace,recovery))

    if not candidates:
        return {"status":"no_clear_impulse","detected":False}

    _,low_idx,peak_idx,trough_idx,low,peak,trough,move_pct,max_retrace,current_retrace,recovery=max(candidates,key=lambda x:x[0])
    run=peak-low

    impulse_vols=[rows[i]["v"] for i in range(low_idx,peak_idx+1) if rows[i]["v"]>0]
    pull_vols=[rows[i]["v"] for i in range(peak_idx+1,trough_idx+1) if rows[i]["v"]>0]
    impulse_avg=sum(impulse_vols)/len(impulse_vols) if impulse_vols else None
    pull_avg=sum(pull_vols)/len(pull_vols) if pull_vols else None
    vol_ratio=(pull_avg/impulse_avg) if impulse_avg and pull_avg is not None else None

    levels={}
    for label,frac in (("25%",0.25),("33%",1/3),("38.2%",0.382),("50%",0.50),("61.8%",0.618)):
        levels[label]=round(peak-run*frac,4)

    # A bounce needs actual recovery off the deepest pullback, not merely a
    # touch of a retracement level while price is still falling.
    bounce_confirmed=bool(
        20 <= max_retrace <= 68
        and recovery >= 6.0
        and price > trough*1.01
    )
    if max_retrace > 78 and current_retrace > 62:
        phase="DEEP / POSSIBLE FAILURE"
    elif bounce_confirmed:
        phase="BOUNCE CONFIRMED"
    elif current_retrace < 20:
        phase="STILL EXTENDED"
    elif current_retrace <= 62:
        phase="PULLBACK FORMING"
    else:
        phase="DEEP PULLBACK"

    return {
        "status":"ok",
        "detected":True,
        "phase":phase,
        "impulse_low":round(low,4),
        "impulse_high":round(peak,4),
        "impulse_move_pct":round(move_pct,2),
        "impulse_duration_bars":int(peak_idx-low_idx),
        "peak_bars_ago":int(len(rows)-1-peak_idx),
        "pullback_low":round(trough,4),
        "current_retracement_pct":round(current_retrace,2),
        "max_retracement_pct":round(max_retrace,2),
        "bounce_recovery_pct":round(recovery,2),
        "bounce_confirmed":bounce_confirmed,
        "pullback_volume_ratio":round(vol_ratio,3) if vol_ratio is not None else None,
        "pullback_volume_contracting":bool(vol_ratio is not None and vol_ratio < 0.85),
        "levels":levels,
        "default_zone_low":levels["50%"],
        "default_zone_high":levels["33%"],
        "run_size":round(run,4),
    }


def run_exhaustion_context(bs, current_price=None, vwap=None, atr_pct=None, impulse=None):
    """Estimate whether a momentum run is stalling or transitioning into reversal."""
    rows=[]
    for b in bs or []:
        o=fnum(b.get("o")); h=fnum(b.get("h")); l=fnum(b.get("l")); cc=fnum(b.get("c")); v=fnum(b.get("v")) or 0.0
        if h is None or l is None or cc is None or h<=0 or l<=0:
            continue
        if o is None:o=cc
        rows.append({"o":o,"h":h,"l":l,"c":cc,"v":v,"t":b.get("t")})
    if len(rows)<10:
        return {"status":"insufficient_data","score":None,"label":"UNKNOWN","factors":[]}

    price=fnum(current_price) or rows[-1]["c"]
    atrp=max(1.0,fnum(atr_pct) or 6.0)
    imp=impulse or {}
    session_high=max(r["h"] for r in rows)
    from_high=(session_high-price)/session_high*100.0 if session_high else 0.0
    vwap_ext=((price/vwap)-1.0)*100.0 if vwap and vwap>0 else None

    recent=rows[-8:]
    recent6=rows[-6:]
    recent12=rows[-12:]
    upper_wicks=[]
    rejection_count=0
    near_high_band=max(0.006,min(0.025,atrp/100.0*0.35))
    for r in recent12:
        rng=max(1e-9,r["h"]-r["l"])
        body_top=max(r["o"],r["c"])
        upper=max(0.0,r["h"]-body_top)
        wick_share=upper/rng
        upper_wicks.append(wick_share)
        near_high=r["h"]>=session_high*(1-near_high_band)
        rejected=(r["c"]<=r["h"]*(1-max(.004,near_high_band*.55))) or wick_share>=0.45
        if near_high and rejected:
            rejection_count+=1
    avg_upper_wick=sum(upper_wicks[-8:])/max(1,len(upper_wicks[-8:]))

    highs=[r["h"] for r in recent6]
    lows=[r["l"] for r in recent6]
    lower_highs=sum(1 for i in range(1,len(highs)) if highs[i] < highs[i-1]*0.999)
    lower_lows=sum(1 for i in range(1,len(lows)) if lows[i] < lows[i-1]*0.999)
    red_fraction=sum(1 for r in recent if r["c"]<r["o"])/max(1,len(recent))

    def ret(n):
        if len(rows)<=n or rows[-n-1]["c"]<=0:return None
        return (rows[-1]["c"]/rows[-n-1]["c"]-1.0)*100.0
    mom3=ret(3); mom6=ret(6); mom12=ret(12)

    vols=[r["v"] for r in rows if r["v"]>0]
    baseline=median(vols[-40:-8]) if len(vols)>=16 and vols[-40:-8] else (median(vols) if vols else None)
    recent_vols=[r["v"] for r in recent12 if r["v"]>0]
    climax=max(recent_vols) if recent_vols else None
    climax_ratio=(climax/baseline) if climax and baseline else None
    last3=[r["v"] for r in rows[-3:] if r["v"]>0]
    last3_avg=sum(last3)/len(last3) if last3 else None
    post_climax_fade=(last3_avg/climax) if climax and last3_avg is not None else None

    score=8.0
    factors=[]
    def add(points,text):
        nonlocal score
        score+=points
        factors.append({"points":round(points,1),"text":text})

    impulse_move=fnum(imp.get("impulse_move_pct")) or 0.0
    current_retrace=fnum(imp.get("current_retracement_pct"))
    max_retrace=fnum(imp.get("max_retracement_pct"))
    recovery=fnum(imp.get("bounce_recovery_pct")) or 0.0
    pull_vol=fnum(imp.get("pullback_volume_ratio"))

    if impulse_move>=80:add(12,"very large impulse move is mature")
    elif impulse_move>=40:add(8,"large impulse move")
    elif impulse_move>=20:add(4,"meaningful impulse already occurred")

    if vwap_ext is not None:
        if vwap_ext>=20:add(15,"extreme extension above VWAP")
        elif vwap_ext>=12:add(9,"large extension above VWAP")
        elif vwap_ext>=8:add(4,"moderate extension above VWAP")
        elif vwap_ext<0 and impulse_move>=15:add(15,"lost VWAP after a momentum run")

    if rejection_count>=3:add(14,f"{rejection_count} recent rejection attempts near the high")
    elif rejection_count>=2:add(9,"repeated rejection near the high")
    elif rejection_count==1:add(4,"one visible rejection near the high")

    if avg_upper_wick>=0.40:add(9,"large upper wicks show selling into strength")
    elif avg_upper_wick>=0.25:add(4,"upper-wick pressure is elevated")

    if lower_highs>=3:add(9,"recent bars are forming lower highs")
    elif lower_highs>=2:add(5,"early lower-high structure")
    if lower_lows>=3:add(6,"recent bars are also making lower lows")
    if red_fraction>=0.625:add(6,"most recent bars are closing red")

    if mom3 is not None and mom6 is not None:
        if mom3<0 and mom6<0:add(10,"short-term momentum has rolled over")
        elif mom3<0:add(5,"very short-term momentum turned negative")
    if mom12 is not None and mom12>3 and mom3 is not None and mom3<0:
        add(5,"momentum divergence: larger trend up, immediate tape down")

    if climax_ratio is not None:
        if climax_ratio>=3 and post_climax_fade is not None and post_climax_fade<0.55:
            add(10,"volume climax followed by participation fade")
        elif climax_ratio>=2.2:
            add(5,"recent volume climax")

    if impulse_move>=15:
        if from_high>=max(6.0,atrp*0.8):add(8,"price has materially rejected from the run high")
        if max_retrace is not None and max_retrace>=78:add(12,"impulse retracement is deep enough to threaten trend failure")
        elif max_retrace is not None and max_retrace>=60 and not imp.get("bounce_confirmed"):
            add(7,"deep pullback has not produced a convincing bounce")
        if pull_vol is not None and pull_vol>=1.15:add(7,"selling volume is expanding during the pullback")
        if current_retrace is not None and 30<=current_retrace<=60 and recovery>=8 and imp.get("bounce_confirmed"):
            add(-8,"healthy pullback has produced a confirmed recovery")
        if current_retrace is not None and current_retrace<20 and mom3 is not None and mom3>0:
            add(-5,"price remains near the high with positive momentum")

    score=max(0.0,min(100.0,score))
    label="VERY HIGH" if score>=78 else "HIGH" if score>=62 else "MODERATE" if score>=42 else "LOW"
    state=("LIKELY TOP / REVERSAL RISK" if score>=78 else "EXHAUSTION WARNING" if score>=62 else "WATCH FOR STALLING" if score>=42 else "RUN STILL HEALTHY")
    return {
        "status":"ok","score":round(score,1),"label":label,"state":state,
        "session_high":round(session_high,4),"from_high_pct":round(from_high,2),
        "vwap_extension_pct":round(vwap_ext,2) if vwap_ext is not None else None,
        "rejection_count":int(rejection_count),"avg_upper_wick_pct":round(avg_upper_wick*100.0,1),
        "lower_high_count":int(lower_highs),"lower_low_count":int(lower_lows),
        "red_bar_pct":round(red_fraction*100.0,1),
        "momentum_3bar_pct":round(mom3,2) if mom3 is not None else None,
        "momentum_6bar_pct":round(mom6,2) if mom6 is not None else None,
        "momentum_12bar_pct":round(mom12,2) if mom12 is not None else None,
        "volume_climax_ratio":round(climax_ratio,2) if climax_ratio is not None else None,
        "post_climax_volume_ratio":round(post_climax_fade,2) if post_climax_fade is not None else None,
        "factors":sorted(factors,key=lambda x:abs(x["points"]),reverse=True)[:8],
    }


def latest_session_bars(symbol, now):
    bs=bars(symbol,"1Min",now-timedelta(hours=10),now,1000,feed=LIVE_FEED)
    today=now.astimezone(ET).date()
    out=[]
    for b in bs:
        dt=_bar_time_et(b)
        if dt is None:continue
        if dt.date()==today and _regular_session_bar(b): out.append(b)
    return out


def _tradier_timestamp(value):
    if value in (None, ""):
        return None
    try:
        ts=float(value)
        if ts > 10_000_000_000:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        pass
    try:
        dt=datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt=dt.replace(tzinfo=ET)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def _tradier_quote_timestamp(quote):
    values=[]
    for key in ("bid_date", "ask_date", "trade_date", "timestamp"):
        parsed=_parse_market_timestamp(_tradier_timestamp((quote or {}).get(key)))
        if parsed is not None:
            values.append(parsed)
    if not values:
        return None
    return max(values).isoformat().replace("+00:00", "Z")


def _tradier_trade_timestamp(quote):
    for key in ("trade_date", "timestamp"):
        parsed=_tradier_timestamp((quote or {}).get(key))
        if parsed:
            return parsed
    return None


def _tradier_regular_session_bars(symbol, now):
    raw=get_tradier_timesales_bars(
        symbol,
        TRADIER_TOKEN,
        now-timedelta(hours=10),
        now,
        interval="1min",
        session_filter="all",
    )
    today=now.astimezone(ET).date()
    return [
        b for b in raw
        if (_bar_time_et(b) is not None)
        and _bar_time_et(b).date()==today
        and _regular_session_bar(b)
    ]


def support_resistance_touch_bars(symbol, now, live_session_bars=None):
    """Return regular-session intraday bars used to timestamp level tests.

    Recent bars use 1-minute data for precise timestamps. Older bars use 5-minute
    data as a fallback so support/resistance levels from the daily lookback can
    still receive a useful last-touch time without making many API requests.
    Delayed consolidated SIP is preferred when available, and today's live feed
    fills the most recent delayed-SIP gap.
    """
    recent_start = now - timedelta(days=10)
    older_start = now - timedelta(days=70)

    recent, recent_src = try_sip_delayed_bars(
        symbol, "1Min", recent_start, now, 10000
    )
    older, older_src = try_sip_delayed_bars(
        symbol, "5Min", older_start, recent_start, 10000
    )
    live = list(live_session_bars or [])

    merged = {}
    # Older 5-minute bars are inserted first. More precise 1-minute/live bars
    # replace them when timestamps overlap.
    for precision, source, collection in (
        (5, older_src, older),
        (1, recent_src, recent),
        (1, LIVE_MARKET_LABEL, live),
    ):
        for b in collection:
            if not _regular_session_bar(b):
                continue
            dt = _bar_time_et(b)
            if dt is None:
                continue
            key = dt.astimezone(timezone.utc).isoformat()
            merged[key] = {
                "bar": b,
                "dt_et": dt,
                "precision_min": precision,
                "source": source,
            }

    return sorted(merged.values(), key=lambda x: x["dt_et"])


def _age_label(dt_et, now):
    if dt_et is None:
        return "—"
    seconds = max(0, (now.astimezone(ET) - dt_et).total_seconds())
    minutes = int(seconds // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        rem = minutes % 60
        return f"{hours}h {rem}m ago" if rem else f"{hours}h ago"
    days = hours // 24
    rem_hours = hours % 24
    return f"{days}d {rem_hours}h ago" if rem_hours else f"{days}d ago"


def _touch_time_label(dt_et):
    if dt_et is None:
        return "—"
    clock = dt_et.strftime("%I:%M %p").lstrip("0")
    return f"{dt_et.strftime('%b')} {dt_et.day}, {clock} ET"


def annotate_level_touches(levels, intraday_bars, now, tolerance_pct=0.75):
    """Attach the most recent intraday test time to each support/resistance level.

    A bar counts as a test when its regular-session high/low range overlaps a
    narrow zone around the level. The zone defaults to +/-0.75%, which is wide
    enough for noisy low-priced momentum names without treating distant bars as
    a touch.
    """
    if not levels:
        return levels

    for level in levels:
        price = fnum(level.get("price"))
        if not price or price <= 0:
            continue
        tol = price * (tolerance_pct / 100.0)
        lower = price - tol
        upper = price + tol
        match = None

        for rec in reversed(intraday_bars):
            b = rec.get("bar") or {}
            lo = fnum(b.get("l"))
            hi = fnum(b.get("h"))
            if lo is None or hi is None:
                continue
            if hi >= lower and lo <= upper:
                match = rec
                break

        if match:
            dt_et = match["dt_et"]
            level["last_touch"] = dt_et.isoformat()
            level["last_touch_label"] = _touch_time_label(dt_et)
            level["age"] = _age_label(dt_et, now)
            level["touch_precision"] = f'{match["precision_min"]}m'
            level["touch_source"] = match.get("source")
        else:
            level["last_touch"] = None
            level["last_touch_label"] = "—"
            level["age"] = "No test in 70d"
            level["touch_precision"] = None
            level["touch_source"] = None
    return levels

def avg_daily_volume(symbol, now):
    bs,src=try_sip_delayed_bars(symbol,"1Day",now-timedelta(days=50),now,50)
    today=now.astimezone(ET).date().isoformat()
    vals=[fnum(b.get("v")) for b in bs if str(b.get("t",""))[:10]!=today and fnum(b.get("v"))]
    vals=vals[-20:]
    return (sum(vals)/len(vals) if vals else None),src

def historical_spikes(symbol, now, current_day_pct, threshold=None):
    # Daily analogs: compare the current move with the same ticker's prior
    # large up days. In addition to close-to-close outcomes, record maximum
    # favorable/adverse excursion from the spike-day close. Those excursion
    # stats are useful for realistic target/stop context in the trade planner.
    bs,src=try_sip_delayed_bars(symbol,"1Day",now-timedelta(days=450),now,450)
    if len(bs)<8:return {"status":"insufficient_history","feed":src,"samples":[]}
    rows=[]
    closes=[fnum(b.get("c")) for b in bs]
    highs=[fnum(b.get("h")) for b in bs]
    lows=[fnum(b.get("l")) for b in bs]
    if threshold is None:
        magnitude=max(10, abs(current_day_pct or 0))
        threshold=max(10,min(30,magnitude*0.45))
    for i in range(1,len(bs)-5):
        c=closes[i]; p=closes[i-1]
        if not c or not p:continue
        d=pct(c,p)
        if d is None or d<threshold:continue
        row={"date":str(bs[i].get("t",""))[:10],"spike_pct":round(d,2)}
        for n in (1,2,3,5):
            fc=closes[i+n]
            row[f"d{n}"]=round(pct(fc,c),2) if fc else None
            future_highs=[x for x in highs[i+1:i+n+1] if x]
            future_lows=[x for x in lows[i+1:i+n+1] if x]
            row[f"mfe{n}"]=round(pct(max(future_highs),c),2) if future_highs else None
            row[f"mae{n}"]=round(pct(min(future_lows),c),2) if future_lows else None
        row["similarity"]=abs(d-(current_day_pct or d))
        rows.append(row)
    rows.sort(key=lambda r:r["similarity"])
    samples=rows[:12]
    summary={}
    for n in (1,2,3,5):
        vals=[r[f"d{n}"] for r in samples if r.get(f"d{n}") is not None]
        mfes=[r[f"mfe{n}"] for r in samples if r.get(f"mfe{n}") is not None]
        maes=[r[f"mae{n}"] for r in samples if r.get(f"mae{n}") is not None]
        summary[f"d{n}"]={
            "n":len(vals),
            "up_pct":round(100*sum(x>0 for x in vals)/len(vals),1) if vals else None,
            "median":round(median(vals),2) if vals else None,
            "median_mfe":round(median(mfes),2) if mfes else None,
            "median_mae":round(median(maes),2) if maes else None,
        }
    return {"status":"ok","feed":src,"threshold_pct":round(threshold,1),"sample_count":len(samples),"summary":summary,"samples":samples}

def pivot_levels(daily_bars, current):
    levels=[]
    for b in daily_bars[-45:]:
        for kind,key in (("high","h"),("low","l"),("close","c")):
            v=fnum(b.get(key))
            if v and v>0: levels.append((v,kind,str(b.get("t",""))[:10]))
    # Cluster nearby levels within ~2.2%.
    levels.sort()
    clusters=[]
    for lv in levels:
        if not clusters or abs(lv[0]/clusters[-1][0][0]-1)>.022: clusters.append([lv])
        else: clusters[-1].append(lv)
    scored=[]
    for c in clusters:
        price=median([x[0] for x in c]); touches=len(c)
        scored.append({"price":round(price,2),"touches":touches,"side":"support" if price<current else "resistance"})
    supports=sorted([x for x in scored if x["side"]=="support"],key=lambda x:current-x["price"])[:4]
    resist=sorted([x for x in scored if x["side"]=="resistance"],key=lambda x:x["price"]-current)[:4]
    return supports,resist

def catalyst_summary(articles, now):
    out=[]
    for a in articles:
        txt=(str(a.get("headline") or "")+" "+str(a.get("summary") or "")).lower()
        best=("recent news",0,[])
        for cat,score,kws in CATALYST_RULES:
            hits=[k for k in kws if k in txt]
            if hits and abs(score)>abs(best[1]):best=(cat,score,hits[:3])
        created=a.get("created_at") or a.get("updated_at")
        age=None
        try: age=(now-datetime.fromisoformat(created.replace("Z","+00:00"))).total_seconds()/3600
        except: pass
        out.append({"headline":a.get("headline") or "","source":a.get("source") or "","url":a.get("url") or "","category":best[0],"score":best[1],"keywords":best[2],"age_hours":round(age,1) if age is not None else None})
    out.sort(key=lambda x:(abs(x["score"]),-(x["age_hours"] if x["age_hours"] is not None else 9999)),reverse=True)
    return out[:5]


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def atr_from_daily(daily_bars, now, periods=14):
    """Completed-session ATR and ATR% using true range."""
    if not daily_bars:
        return None, None
    today=now.astimezone(ET).date().isoformat()
    completed=[b for b in daily_bars if str(b.get("t", ""))[:10] != today]
    if len(completed) < 3:
        return None, None
    trs=[]
    for i in range(1, len(completed)):
        h=fnum(completed[i].get("h")); l=fnum(completed[i].get("l")); pc=fnum(completed[i-1].get("c"))
        if h is None or l is None or pc is None or pc <= 0:
            continue
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    if not trs:
        return None, None
    atr=sum(trs[-periods:])/len(trs[-periods:])
    last_close=fnum(completed[-1].get("c"))
    return atr, (atr/last_close*100 if last_close else None)


def _level_age_hours(level, now):
    raw=level.get("last_touch")
    if not raw:
        return None
    try:
        dt=datetime.fromisoformat(str(raw))
        if dt.tzinfo is None:
            dt=dt.replace(tzinfo=ET)
        return max(0.0, (now-dt.astimezone(timezone.utc)).total_seconds()/3600)
    except Exception:
        return None


def score_level_quality(levels, vwap, now):
    """Score S/R levels by touches, recency and VWAP confluence."""
    for level in levels or []:
        touches=int(level.get("touches") or 0)
        score=min(45, touches*12)
        age_h=_level_age_hours(level, now)
        if age_h is not None:
            if age_h <= 8: score += 35
            elif age_h <= 48: score += 28
            elif age_h <= 24*7: score += 20
            elif age_h <= 24*21: score += 12
            elif age_h <= 24*45: score += 6
        price=fnum(level.get("price"))
        if price and vwap and abs(price/vwap-1)*100 <= 1.5:
            score += 20
            level["vwap_confluence"] = True
        else:
            level["vwap_confluence"] = False
        score=int(_clamp(score,0,100))
        level["quality_score"]=score
        level["quality"]="Strong" if score>=70 else "Moderate" if score>=45 else "Weak"
    return levels


def liquidity_context(price, avg_volume, volume_pace, spread_pct):
    avg_dollar=(price or 0)*(avg_volume or 0)
    spread=spread_pct if spread_pct is not None else 99
    if avg_dollar >= 20_000_000 and spread <= 1.5:
        label="HIGH"
    elif avg_dollar >= 5_000_000 and spread <= 3.5:
        label="MODERATE"
    else:
        label="LOW"
    score=50
    if avg_dollar >= 50_000_000: score += 25
    elif avg_dollar >= 20_000_000: score += 18
    elif avg_dollar >= 5_000_000: score += 8
    else: score -= 12
    if spread <= 0.75: score += 18
    elif spread <= 1.5: score += 10
    elif spread <= 3.5: score += 0
    elif spread <= 6: score -= 15
    else: score -= 28
    if volume_pace is not None:
        if volume_pace >= 2: score += 10
        elif volume_pace < 0.7: score -= 8
    return {
        "label":label,
        "score":int(_clamp(score,0,100)),
        "avg_dollar_volume":round(avg_dollar) if avg_dollar else None,
    }


def _nearest_level(levels, price, direction):
    vals=[]
    for level in levels or []:
        p=fnum(level.get("price"))
        if not p: continue
        if direction == "below" and p < price:
            vals.append(level)
        elif direction == "above" and p > price:
            vals.append(level)
    if not vals:return None
    return min(vals,key=lambda x:abs((fnum(x.get("price")) or price)-price))


def _next_resistance(resistances, above_price):
    vals=[x for x in (resistances or []) if (fnum(x.get("price")) or 0) > above_price]
    return min(vals,key=lambda x:fnum(x.get("price"))) if vals else None


def _price_zone(center, half_width_pct):
    if not center:return None
    return {
        "low":round(center*(1-half_width_pct/100),4),
        "high":round(center*(1+half_width_pct/100),4),
    }


def _hist_trade_context(hist):
    out={"sample_count":0,"next_day_up_pct":None,"median_close_1d":None,"median_mfe_1d":None,"median_mfe_3d":None,"median_mae_1d":None,"median_mae_3d":None}
    if not hist or hist.get("status") != "ok":return out
    out["sample_count"]=int(hist.get("sample_count") or 0)
    d1=(hist.get("summary") or {}).get("d1") or {}
    d3=(hist.get("summary") or {}).get("d3") or {}
    out.update({
        "next_day_up_pct":d1.get("up_pct"),
        "median_close_1d":d1.get("median"),
        "median_mfe_1d":d1.get("median_mfe"),
        "median_mfe_3d":d3.get("median_mfe"),
        "median_mae_1d":d1.get("median_mae"),
        "median_mae_3d":d3.get("median_mae"),
    })
    return out


def _catalyst_bias(news_rows):
    if not news_rows:return {"label":"NEUTRAL","score":0,"headline":None}
    weighted=[]
    for a in news_rows:
        raw=float(a.get("score") or 0)
        age=a.get("age_hours")
        decay=1.0 if age is None else max(0.25, 1-(float(age)/120))
        weighted.append((raw*decay,a))
    val,a=max(weighted,key=lambda x:abs(x[0]))
    return {
        "label":"POSITIVE" if val>=3 else "NEGATIVE" if val<=-3 else "NEUTRAL",
        "score":round(val,1),
        "headline":a.get("headline") if a else None,
    }


def build_trade_plan(metrics, now):
    """Rule-based long momentum trade plan.

    This deliberately returns WAIT/NO TRADE when structure or reward/risk is
    poor. It combines current price action with S/R, VWAP, ATR, historical
    analog excursion data, liquidity, volume, momentum and catalyst context.
    """
    price=fnum(metrics.get("price"))
    if not price:return {"status":"NO TRADE","action":"NO TRADE — no current price"}
    vwap=fnum(metrics.get("vwap"))
    supports=metrics.get("supports") or []
    resistances=metrics.get("resistances") or []
    atr=fnum(metrics.get("atr_14"))
    atr_pct=fnum(metrics.get("atr_14_pct")) or 8.0
    atr=atr or price*atr_pct/100
    spread=fnum(metrics.get("spread_pct"))
    pace=fnum(metrics.get("volume_pace"))
    m5=fnum(metrics.get("momentum_5m"))
    m15=fnum(metrics.get("momentum_15m"))
    day_pct=fnum(metrics.get("day_pct")) or 0
    vwap_ext=fnum(metrics.get("vwap_extension_pct")) or 0
    setup_score=fnum(metrics.get("score")) or 50
    hist=_hist_trade_context(metrics.get("historical_analogs"))
    impulse=metrics.get("impulse_pullback") or {}
    exhaustion=metrics.get("run_exhaustion") or {}
    reversal_score=fnum(exhaustion.get("score")) or 0.0
    hist_setup=metrics.get("historical_setup") or {}
    hist_intraday=hist_setup.get("intraday") or {}
    catalyst=_catalyst_bias(metrics.get("news") or [])
    liquidity=metrics.get("liquidity") or {}

    nearest_support=_nearest_level(supports,price,"below")
    nearest_resistance=_nearest_level(resistances,price,"above")
    support_price=fnum((nearest_support or {}).get("price"))
    resistance_price=fnum((nearest_resistance or {}).get("price"))

    # Pullback anchor: use the impulse retracement structure first when a
    # meaningful run exists, then look for VWAP/support confluence inside it.
    impulse_detected=bool(impulse.get("detected"))
    impulse_low=fnum(impulse.get("impulse_low"))
    impulse_high=fnum(impulse.get("impulse_high"))
    impulse_run=(impulse_high-impulse_low) if impulse_low and impulse_high and impulse_high>impulse_low else None
    hist_retrace=fnum(hist_intraday.get("median_impulse_retracement_pct"))

    retrace_shallow=33.0
    retrace_deep=50.0
    if hist_retrace is not None and 25 <= hist_retrace <= 62:
        # Let the stock's own historical behavior shift the preferred zone,
        # while keeping it inside a sensible first-pullback envelope.
        retrace_shallow=max(25.0,hist_retrace-8.0)
        retrace_deep=min(62.0,hist_retrace+8.0)

    impulse_zone=None
    if impulse_detected and impulse_run and (fnum(impulse.get("impulse_move_pct")) or 0)>=8:
        iz_high=impulse_high-impulse_run*(retrace_shallow/100.0)
        iz_low=impulse_high-impulse_run*(retrace_deep/100.0)
        impulse_zone={"low":round(min(iz_low,iz_high),4),"high":round(max(iz_low,iz_high),4)}
        pull_anchor=(impulse_zone["low"]+impulse_zone["high"])/2
        pull_source=(
            f"historical {retrace_shallow:.0f}–{retrace_deep:.0f}% impulse retracement"
            if hist_retrace is not None
            else "33–50% impulse retracement"
        )

        confluence=[]
        if vwap and impulse_zone["low"]*0.97 <= vwap <= impulse_zone["high"]*1.03:
            confluence.append((vwap,70,"VWAP + impulse retracement"))
        for level in supports:
            lp=fnum(level.get("price"))
            if lp and impulse_zone["low"]*0.97 <= lp <= impulse_zone["high"]*1.03:
                confluence.append((lp,float(level.get("quality_score") or 45)+18,f"{level.get('quality','')} support + retracement".strip()))
        if confluence:
            pull_anchor,_,pull_source=max(confluence,key=lambda x:x[1])
    else:
        anchors=[]
        if vwap and vwap < price and (price/vwap-1)*100 <= max(18,atr_pct*1.8):
            anchors.append((vwap,55,"VWAP"))
        for level in supports:
            lp=fnum(level.get("price"))
            if lp and lp < price and (price/lp-1)*100 <= max(20,atr_pct*2):
                anchors.append((lp,float(level.get("quality_score") or 35),f"{level.get('quality','')} support".strip()))
        if anchors:
            pull_anchor,_,pull_source=max(anchors,key=lambda x:x[1]-(price/x[0]-1)*100*1.6)
        else:
            pull_anchor=price-max(atr*0.75,price*0.04)
            pull_source="volatility pullback"

    if impulse_zone:
        # Preserve the meaningful retracement width instead of collapsing a
        # large impulse to a tiny ATR-sized band.
        pull_zone=dict(impulse_zone)
    else:
        zone_half=_clamp(atr_pct*0.055,0.35,1.15)
        pull_zone=_price_zone(pull_anchor,zone_half)

    stop_buffer_pct=_clamp(atr_pct*0.16,0.9,3.5)
    structural=nearest_support
    structural_price=fnum((structural or {}).get("price"))
    retrace_618=fnum((impulse.get("levels") or {}).get("61.8%"))
    stop_candidates=[]
    if structural_price and structural_price < pull_zone["low"]:
        stop_candidates.append(structural_price*(1-stop_buffer_pct/100))
    if retrace_618 and retrace_618 < pull_zone["low"]:
        stop_candidates.append(retrace_618*(1-stop_buffer_pct/100))
    if stop_candidates:
        # Use the closest meaningful invalidation below the zone, rather than
        # an unnecessarily distant stop.
        pull_stop=max(stop_candidates)
    else:
        pull_stop=pull_zone["low"]*(1-stop_buffer_pct/100)
    pull_stop=round(pull_stop,4)
    pull_entry=(pull_zone["low"]+pull_zone["high"])/2

    # Breakout trigger: nearest overhead resistance or day high, whichever is
    # the first meaningful barrier above current price.
    day_high=fnum(metrics.get("day_high"))
    breakout_candidates=[]
    if resistance_price and resistance_price > price*1.001:breakout_candidates.append((resistance_price,"resistance"))
    if day_high and day_high > price*1.003:breakout_candidates.append((day_high,"day high"))
    if breakout_candidates:
        breakout_level,breakout_source=min(breakout_candidates,key=lambda x:x[0])
    else:
        breakout_level=max(price,day_high or price)
        breakout_source="current/day high"
    breakout_confirm_pct=_clamp(atr_pct*0.045,0.25,0.9)
    breakout_zone={
        "low":round(breakout_level*(1+breakout_confirm_pct/100),4),
        "high":round(breakout_level*(1+(breakout_confirm_pct+_clamp(atr_pct*0.07,0.35,1.1))/100),4),
    }
    breakout_entry=(breakout_zone["low"]+breakout_zone["high"])/2
    breakout_stop=round(breakout_level*(1-_clamp(atr_pct*0.18,1.0,3.8)/100),4)

    def targets_for(entry, stop, breakout=False):
        tech=[]
        for level in resistances:
            lp=fnum(level.get("price"))
            if lp and lp > entry*(1.002):tech.append(lp)
        if day_high and day_high > entry*(1.002):tech.append(day_high)
        tech=sorted(set(round(x,4) for x in tech))
        if breakout:
            # The broken level is no longer a target; look for the next one.
            tech=[x for x in tech if x > breakout_level*(1.004)]
        hist_mfe1=fnum(hist.get("median_mfe_1d"))
        hist_mfe3=fnum(hist.get("median_mfe_3d"))
        hist1=price*(1+hist_mfe1/100) if hist_mfe1 and hist_mfe1>0 else None
        hist3=price*(1+hist_mfe3/100) if hist_mfe3 and hist_mfe3>0 else None
        atr1=entry+atr*0.75
        atr2=entry+atr*1.25
        atr3=entry+atr*1.75
        candidates=[]
        target_floor=entry*1.005 if breakout else max(entry*1.005, price*1.005)
        if tech and tech[0] > target_floor:candidates.append((tech[0],"nearest technical resistance"))
        if hist1 and hist1>target_floor:candidates.append((hist1,"historical median 1-day run-up"))
        if atr1>target_floor:candidates.append((atr1,"0.75 ATR projection"))
        if candidates:
            candidates=sorted(candidates,key=lambda x:x[0])
            t1_price,t1_reason=candidates[0]
        else:
            t1_price,t1_reason=(max(target_floor*1.005,atr1),"ATR/price-structure projection")
        # Make target 2 meaningfully above target 1.
        c2=[]
        if len(tech)>1:c2.append((tech[1],"next technical resistance"))
        if hist1 and hist1>t1_price*1.01:c2.append((hist1,"historical median 1-day run-up"))
        if hist3 and hist3>t1_price*1.01:c2.append((hist3,"historical median 3-day run-up"))
        c2.append((atr2,"1.25 ATR projection"))
        c2=[x for x in c2 if x[0]>t1_price*1.005]
        t2_price,t2_reason=min(c2,key=lambda x:x[0]) if c2 else (max(t1_price*1.02,atr2),"1.25 ATR projection")
        stretch_candidates=[(atr3,"1.75 ATR projection")]
        if hist3 and hist3>t2_price:stretch_candidates.append((hist3,"historical median 3-day run-up"))
        if len(tech)>2:stretch_candidates.append((tech[2],"higher technical resistance"))
        stretch_candidates=[x for x in stretch_candidates if x[0]>t2_price*1.005]
        t3_price,t3_reason=min(stretch_candidates,key=lambda x:x[0]) if stretch_candidates else (max(t2_price*1.03,atr3),"1.75 ATR projection")
        risk=max(0.0001,entry-stop)
        rr=(t1_price-entry)/risk
        return {
            "target1":round(t1_price,4),"target1_reason":t1_reason,
            "target2":round(t2_price,4),"target2_reason":t2_reason,
            "stretch_target":round(t3_price,4),"stretch_reason":t3_reason,
            "risk_reward":round(rr,2),
        }

    def finalize_zone(zone,stop,breakout=False):
        low=float(zone["low"]); high=float(zone["high"])
        mid=(low+high)/2
        targets=targets_for(mid,stop,breakout)
        target=fnum(targets.get("target1"))
        # Do not advertise the upper part of an entry zone if its Target-1
        # reward/risk falls below the minimum acceptable 1.3:1.
        if target and target>low and stop<low:
            rr_limit=(target+1.30*stop)/2.30
            high=min(high,rr_limit)
            if high < low:
                high=low
            mid=(low+high)/2
            targets=targets_for(mid,stop,breakout)
        rr_low=(fnum(targets.get("target1"))-low)/(low-stop) if fnum(targets.get("target1")) and low>stop else None
        rr_high=(fnum(targets.get("target1"))-high)/(high-stop) if fnum(targets.get("target1")) and high>stop else None
        return {
            "entry_low":round(low,4),"entry_high":round(high,4),"entry_mid":round(mid,4),
            "rr_at_low":round(rr_low,2) if rr_low is not None else None,
            "rr_at_high":round(rr_high,2) if rr_high is not None else None,
            **targets,
        }

    pull_plan={
        **finalize_zone(pull_zone,pull_stop,False),
        "entry_source":pull_source,
        "stop":pull_stop,
        "stop_reason":"below nearby structure / deep retracement with ATR volatility buffer",
        "confirmation":"A zone touch is not an entry by itself. Prefer support hold, higher low or reclaim with positive short-term momentum and renewed volume.",
        "retracement_shallow_pct":round(retrace_shallow,1) if impulse_zone else None,
        "retracement_deep_pct":round(retrace_deep,1) if impulse_zone else None,
    }
    breakout_plan={
        "breakout_level":round(breakout_level,4),"breakout_source":breakout_source,
        **finalize_zone(breakout_zone,breakout_stop,True),
        "stop":breakout_stop,"stop_reason":"back below breakout level with ATR volatility buffer",
        "confirmation":"Require the breakout to clear and hold with positive 5m momentum and preferably ≥1.5x volume pace; avoid a thin poke above resistance.",
    }
    # Use the tightened zones for all following trigger logic.
    pull_zone={"low":pull_plan["entry_low"],"high":pull_plan["entry_high"]}
    breakout_zone={"low":breakout_plan["entry_low"],"high":breakout_plan["entry_high"]}

    in_pull=pull_zone["low"] <= price <= pull_zone["high"]
    in_break=breakout_zone["low"] <= price <= breakout_zone["high"]
    bullish_momentum=(m5 is not None and m5>0) and (m15 is None or m15>=-1.0)
    impulse_recovery=fnum(impulse.get("bounce_recovery_pct")) or 0.0
    impulse_bounce=bool(impulse.get("bounce_confirmed")) or (
        impulse_detected and impulse_recovery>=6.0 and bullish_momentum
    )
    pullback_confirm=bool(
        bullish_momentum
        and (
            not impulse_detected
            or impulse_bounce
        )
    )
    breakout_confirm=(pace is None or pace>=1.5) and (m5 is not None and m5>0)
    severe_risk=(spread is not None and spread>7) or liquidity.get("label")=="LOW" and (spread is not None and spread>4.5)
    below_vwap_weak=(vwap is not None and price<vwap and (m5 or 0)<0 and (m15 or 0)<0)
    current_retrace=fnum(impulse.get("current_retracement_pct"))
    overextended=(
        vwap_ext>max(10,atr_pct*1.1)
        or day_pct>60
        or (impulse_detected and current_retrace is not None and current_retrace<25 and (fnum(impulse.get("impulse_move_pct")) or 0)>=15)
    )
    negative_catalyst=catalyst.get("score",0)<=-5

    preferred="pullback"
    if in_break and breakout_confirm and reversal_score<62:preferred="breakout"
    elif in_pull:preferred="pullback"
    elif breakout_plan["risk_reward"] >= pull_plan["risk_reward"]+0.5 and not overextended and reversal_score<55:preferred="breakout"

    chosen=breakout_plan if preferred=="breakout" else pull_plan
    rr=fnum(chosen.get("risk_reward")) or 0
    confidence=setup_score
    if rr>=2.5:confidence+=9
    elif rr>=1.5:confidence+=4
    elif rr<1:confidence-=14
    if liquidity.get("label")=="HIGH":confidence+=5
    elif liquidity.get("label")=="LOW":confidence-=8
    if pace is not None and pace>=2:confidence+=5
    if spread is not None and spread>5:confidence-=8
    if hist.get("sample_count",0)>=6:confidence+=4
    if (hist.get("next_day_up_pct") or 0)>=65:confidence+=4
    if catalyst.get("label")=="POSITIVE":confidence+=4
    elif catalyst.get("label")=="NEGATIVE":confidence-=6
    if overextended:confidence-=10
    confidence=int(round(_clamp(confidence,0,95)))

    reasons=[]
    if severe_risk:
        status="NO TRADE"
        action="NO TRADE — liquidity/spread risk is too high"
        reasons.append("Spread/liquidity conditions make entries and exits unreliable.")
    elif negative_catalyst and setup_score<65:
        status="NO TRADE"
        action="NO TRADE — negative catalyst risk"
        reasons.append("Recent catalyst/news context is materially negative and the technical score is not strong enough to offset it.")
    elif below_vwap_weak and setup_score<65:
        status="NO TRADE"
        action="NO TRADE — momentum is weak below VWAP"
        reasons.append("Price is below VWAP with negative short-term momentum.")
    elif reversal_score>=82 and ((m5 is not None and m5<0) or (vwap is not None and price<vwap)):
        status="NO TRADE"
        action="NO TRADE — run exhaustion / reversal risk is very high"
        reasons.append("Multiple top/reversal signals are aligned; avoid treating a mature run as a fresh momentum entry.")
    elif reversal_score>=68:
        status="WAIT"
        preferred="pullback"; chosen=pull_plan
        action="WAIT — HIGH RUN-EXHAUSTION RISK"
        reasons.append("The run is showing meaningful exhaustion/reversal evidence; require a fresh base and reclaim before considering another long entry.")
    elif rr < 1.15:
        status="NO TRADE"
        action="NO TRADE — reward/risk is unattractive"
        reasons.append(f"Preferred plan offers only about {rr:.2f}:1 reward/risk to Target 1.")
    elif in_pull and pullback_confirm and setup_score>=62 and pull_plan["risk_reward"]>=1.3:
        status="ENTRY AVAILABLE"
        preferred="pullback"; chosen=pull_plan
        action="ENTRY AVAILABLE — pullback confirmed"
        reasons.append("Price is in the preferred pullback zone and has shown a bounce/reclaim rather than merely touching the zone.")
    elif in_break and breakout_confirm and setup_score>=65 and breakout_plan["risk_reward"]>=1.3:
        status="ENTRY AVAILABLE"
        preferred="breakout"; chosen=breakout_plan
        action="ENTRY AVAILABLE — confirmed breakout zone"
        reasons.append("Price is through the breakout trigger with acceptable momentum/volume confirmation.")
    else:
        status="WAIT"
        if overextended:
            preferred="pullback"; chosen=pull_plan
            action="WAIT FOR REAL PULLBACK — price is extended"
            if impulse_zone:
                reasons.append(
                    f"The last impulse has retraced only {current_retrace:.0f}% so far; the preferred structure is roughly {retrace_shallow:.0f}–{retrace_deep:.0f}% of the impulse before confirmation."
                    if current_retrace is not None
                    else "The current move is still extended; wait for a meaningful impulse retracement before considering entry."
                )
            else:
                reasons.append("Current price is stretched relative to VWAP/normal volatility; chasing raises reversal risk.")
        elif in_pull and not pullback_confirm:
            preferred="pullback"; chosen=pull_plan
            action="WAIT FOR PULLBACK TO HOLD / BOUNCE"
            reasons.append("Price reached the pullback zone, but a zone touch alone is not enough; wait for a hold, higher low or reclaim with positive momentum.")
        elif price < breakout_zone["low"] and price > pull_zone["high"]:
            if pull_plan["risk_reward"] >= breakout_plan["risk_reward"]:
                preferred="pullback"; chosen=pull_plan
                action="WAIT FOR PULLBACK"
                reasons.append("Current price is between the preferred pullback area and breakout confirmation area.")
            else:
                preferred="breakout"; chosen=breakout_plan
                action="WAIT FOR BREAKOUT CONFIRMATION"
                reasons.append("Current price is below the breakout confirmation zone; wait for the level to clear with participation.")
        elif price < pull_zone["low"]:
            action="WAIT FOR SUPPORT TO HOLD / RECLAIM"
            preferred="pullback"; chosen=pull_plan
            reasons.append("Price is below the modeled pullback entry zone; wait for support to hold or reclaim before considering entry.")
        else:
            action="WAIT FOR CONFIRMATION"
            reasons.append("The setup is not currently at a clean entry trigger.")

    # Recompute display RR/confidence if status logic changed the preferred plan.
    rr=fnum(chosen.get("risk_reward")) or 0
    support_distance=(price/support_price-1)*100 if support_price else None
    resistance_distance=(resistance_price/price-1)*100 if resistance_price else None
    return {
        "status":status,"action":action,"preferred_plan":preferred,
        "confidence":confidence,
        "confidence_label":"HIGH" if confidence>=75 else "MODERATE" if confidence>=58 else "LOW",
        "pullback":pull_plan,"breakout":breakout_plan,"selected":chosen,
        "impulse_pullback":impulse,
        "run_exhaustion":exhaustion,
        "nearest_support":support_price,"nearest_support_quality":(nearest_support or {}).get("quality"),
        "nearest_resistance":resistance_price,"support_distance_pct":round(support_distance,2) if support_distance is not None else None,
        "resistance_distance_pct":round(resistance_distance,2) if resistance_distance is not None else None,
        "historical":{**hist,"relevance":"HIGH" if abs(day_pct)>=10 else "MODERATE" if abs(day_pct)>=6 else "LOW"},"catalyst":catalyst,"liquidity":liquidity,
        "atr":round(atr,4),"atr_pct":round(atr_pct,2),
        "reasons":reasons,
        "updated":now.astimezone(ET).isoformat(),
        "method_note":"Rule-based long momentum decision support using impulse/retracement structure, confirmation, VWAP, support/resistance, ATR, momentum, volume pace, spread/liquidity, same-ticker historical behavior and catalyst context. Pullback zones are not automatic entries; targets are scenarios, not guarantees.",
    }

def score_setup(metrics):
    score=50.0; reasons=[]
    p=metrics.get("price") or 0; vwap=metrics.get("vwap")
    if vwap:
        ext=pct(p,vwap)
        if ext>=0: score+=8; reasons.append("above VWAP")
        else: score-=8; reasons.append("below VWAP")
        if ext>20: score-=16; reasons.append("severely extended >20% above VWAP")
        elif ext>12: score-=10; reasons.append("extended >12% above VWAP")
    m5=metrics.get("momentum_5m")
    m15=metrics.get("momentum_15m")
    if m5 is not None: score+=max(-8,min(8,m5*2))
    if m15 is not None: score+=max(-8,min(8,m15))
    fp=metrics.get("from_high_pct")
    if fp is not None:
        if fp<=3:score+=8
        elif fp>=15:score-=8; reasons.append("far below day high")
    exhaustion=metrics.get("run_exhaustion") or {}
    ex_score=fnum(exhaustion.get("score"))
    if ex_score is not None:
        if ex_score>=78:score-=18; reasons.append("very high run-exhaustion / reversal risk")
        elif ex_score>=62:score-=10; reasons.append("elevated run-exhaustion risk")
        elif ex_score>=42:score-=4
    pace=metrics.get("volume_pace")
    if pace is not None:
        if pace>=2:score+=10; reasons.append("strong relative volume")
        elif pace<0.7:score-=5
    spread=metrics.get("spread_pct")
    if spread is not None:
        if spread<1:score+=4
        elif spread>5:score-=7; reasons.append("wide live spread")
    day=metrics.get("day_pct") or 0
    chase=max(0, day-25)*0.16 + max(0,(pct(p,vwap) if vwap else 0)-5)*0.5
    score-=min(18,chase)
    score=max(0,min(100,score))
    grade="A" if score>=78 else "B" if score>=65 else "C" if score>=52 else "REJECT"
    entry="FAVORABLE" if score>=72 and chase<10 else "WAIT / CONFIRM" if score>=55 else "POOR / HIGH RISK"
    return round(score,1),grade,entry,reasons

def analyze(symbol):
    symbol=symbol.upper().strip()
    if not symbol or len(symbol)>10: raise ValueError("Enter a valid ticker symbol")
    now=datetime.now(timezone.utc); now_et=now.astimezone(ET)

    # Alpaca remains the history/news/fallback source. When Tradier is
    # configured, all CURRENT decision inputs come from one consolidated
    # Tradier quote + Time & Sales bundle so the tape and trade plan agree.
    snap=snapshot(symbol,LIVE_FEED)
    trade=snap.get("latestTrade") or {}
    quote=snap.get("latestQuote") or {}
    day=snap.get("dailyBar") or {}
    prev=snap.get("prevDailyBar") or {}

    live_provider="alpaca"
    live_feed_label=LIVE_FEED.upper()
    live_provider_error=None
    trade_ts=trade.get("t")
    quote_ts=quote.get("t")
    price=fnum(trade.get("p")) or fnum(day.get("c"))
    prev_close=fnum(prev.get("c"))
    bid=fnum(quote.get("bp"))
    ask=fnum(quote.get("ap"))
    intraday=None
    provider_day_high=None
    provider_day_low=None

    if USE_TRADIER:
        try:
            tradier_quote=(get_tradier_quotes([symbol],TRADIER_TOKEN) or {}).get(symbol)
            if not tradier_quote:
                raise RuntimeError("Tradier returned no quote")
            tradier_intraday=_tradier_regular_session_bars(symbol,now)
            if is_regular(now_et) and not tradier_intraday:
                raise RuntimeError("Tradier returned no regular-session Time & Sales bars")

            tprice=(
                fnum(tradier_quote.get("last"))
                or fnum(tradier_quote.get("close"))
                or (fnum(tradier_intraday[-1].get("c")) if tradier_intraday else None)
            )
            if not tprice:
                raise RuntimeError("Tradier returned no current price")

            price=tprice
            prev_close=fnum(tradier_quote.get("prevclose")) or prev_close
            bid=fnum(tradier_quote.get("bid"))
            ask=fnum(tradier_quote.get("ask"))
            provider_day_high=fnum(tradier_quote.get("high"))
            provider_day_low=fnum(tradier_quote.get("low"))
            intraday=tradier_intraday
            trade_ts=_tradier_trade_timestamp(tradier_quote)
            quote_ts=_tradier_quote_timestamp(tradier_quote)
            live_provider="tradier"
            live_feed_label="TRADIER CONSOLIDATED"
        except Exception as exc:
            # Explicit fallback keeps the Analyzer usable during a provider
            # outage while making the source change visible in the metrics.
            live_provider_error=str(exc)[:180]

    if intraday is None:
        intraday=latest_session_bars(symbol,now)

    if not price: raise RuntimeError("No current price returned")
    day_pct=pct(price,prev_close) if prev_close else None

    spread_pct=pct(ask,bid) if bid and ask else None
    if spread_pct is not None:
        spread_pct=spread_pct/(1+spread_pct/100) # approx spread / ask, percentage

    vwap=session_vwap_from_bars(intraday)
    high=max(
        [fnum(x.get("h")) or 0 for x in intraday],
        default=provider_day_high or fnum(day.get("h")) or price,
    )
    low=min(
        [fnum(x.get("l")) for x in intraday if fnum(x.get("l"))],
        default=provider_day_low or fnum(day.get("l")) or price,
    )
    from_high=(high-price)/high*100 if high else None

    def mom(n):
        if len(intraday)>=n+1:
            c=fnum(intraday[-(n+1)].get("c"))
            return pct(price,c) if c else None
        return None

    avgvol,volsrc=avg_daily_volume(symbol,now)
    session_volume=sum((fnum(x.get("v")) or 0) for x in intraday)
    if live_provider=="tradier":
        today_volume=session_volume
        volume_source="TRADIER CONSOLIDATED"
    else:
        today_volume=fnum(day.get("v")) or session_volume
        volume_source=volsrc

    pace=None
    if avgvol:
        expected=avgvol*(session_fraction(now_et) if is_regular(now_et) else 1)
        pace=today_volume/expected if expected else None

    trade_age=_market_age_seconds(trade_ts,now)
    quote_age=_market_age_seconds(quote_ts,now)

    daily,daysrc=try_sip_delayed_bars(symbol,"1Day",now-timedelta(days=180),now,180)
    supports,resist=pivot_levels(daily,price)
    try:
        touch_bars=support_resistance_touch_bars(symbol,now,intraday)
        supports=annotate_level_touches(supports,touch_bars,now)
        resist=annotate_level_touches(resist,touch_bars,now)
    except Exception:
        for level in supports + resist:
            level.update({"last_touch":None,"last_touch_label":"—","age":"Unavailable","touch_precision":None,"touch_source":None})

    supports=score_level_quality(supports,vwap,now)
    resist=score_level_quality(resist,vwap,now)
    atr14,atr14_pct=atr_from_daily(daily,now,14)
    hist=historical_spikes(symbol,now,day_pct)
    arts=[]
    try: arts=catalyst_summary(news(symbol,now),now)
    except Exception: pass

    liquidity=liquidity_context(price,avgvol,pace,spread_pct)
    impulse=impulse_pullback_context(intraday,price,atr14_pct)
    exhaustion=run_exhaustion_context(intraday,price,vwap,atr14_pct,impulse)
    metrics={
        "engine_version":ANALYZER_ENGINE_VERSION,
        "feature_version":ANALYZER_FEATURE_VERSION,
        "symbol":symbol,
        "as_of":now_et.isoformat(),
        "market_provider":live_provider,
        "live_provider":live_provider,
        "live_feed":live_feed_label,
        "live_provider_error":live_provider_error,
        "historical_provider":"alpaca",
        "historical_feed":daysrc,
        "latest_trade_time":trade_ts,
        "latest_quote_time":quote_ts,
        "trade_age_seconds":round(trade_age,2) if trade_age is not None else None,
        "quote_age_seconds":round(quote_age,2) if quote_age is not None else None,
        "price":round(price,4),
        "prev_close":prev_close,
        "day_pct":round(day_pct,2) if day_pct is not None else None,
        "bid":bid,
        "ask":ask,
        "spread_pct":round(spread_pct,2) if spread_pct is not None else None,
        "day_high":round(high,4),
        "day_low":round(low,4),
        "from_high_pct":round(from_high,2) if from_high is not None else None,
        "vwap":round(vwap,4) if vwap else None,
        "vwap_position":"ABOVE" if vwap and price>=vwap else "BELOW" if vwap else "N/A",
        "vwap_extension_pct":round(pct(price,vwap),2) if vwap else None,
        "momentum_5m":round(mom(5),2) if mom(5) is not None else None,
        "momentum_15m":round(mom(15),2) if mom(15) is not None else None,
        "momentum_30m":round(mom(30),2) if mom(30) is not None else None,
        "session_volume":round(session_volume),
        "volume":round(today_volume),
        "avg_20d_volume":round(avgvol) if avgvol else None,
        "volume_pace":round(pace,2) if pace is not None else None,
        "volume_source":volume_source,
        "atr_14":round(atr14,4) if atr14 is not None else None,
        "atr_14_pct":round(atr14_pct,2) if atr14_pct is not None else None,
        "impulse_pullback":impulse,
        "run_exhaustion":exhaustion,
        "liquidity":liquidity,
        "supports":supports,
        "resistances":resist,
        "historical_analogs":hist,
        "news":arts,
    }
    score,grade,entry,reasons=score_setup(metrics)
    metrics.update({"score":score,"grade":grade,"entry_quality":entry,"score_reasons":reasons})
    metrics["trade_plan"]=build_trade_plan(metrics,now)
    return metrics

if __name__=="__main__":
    import sys
    ticker=(sys.argv[1] if len(sys.argv)>1 else "SDOT")
    print(json.dumps(analyze(ticker),indent=2))
