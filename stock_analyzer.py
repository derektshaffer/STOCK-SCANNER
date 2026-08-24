import json, math, os, urllib.parse, urllib.request, urllib.error
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

DATA_BASE = "https://data.alpaca.markets"
API_KEY = os.environ.get("ALPACA_API_KEY", "").strip()
API_SECRET = os.environ.get("ALPACA_SECRET_KEY", "").strip()
LIVE_FEED = os.environ.get("ALPACA_LIVE_FEED", "iex").strip().lower() or "iex"
HISTORICAL_FEED = os.environ.get("ALPACA_HISTORICAL_FEED", "sip").strip().lower() or "sip"
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
        typical=((fnum(b.get("h")) or 0)+(fnum(b.get("l")) or 0)+(fnum(b.get("c")) or 0))/3
        pv += typical*vol; v += vol
    return pv/v if v else None

def latest_session_bars(symbol, now):
    bs=bars(symbol,"1Min",now-timedelta(hours=10),now,1000,feed=LIVE_FEED)
    today=now.astimezone(ET).date()
    out=[]
    for b in bs:
        try:dt=datetime.fromisoformat(b["t"].replace("Z","+00:00")).astimezone(ET)
        except:continue
        if dt.date()==today and dt.weekday()<5 and 570<=dt.hour*60+dt.minute<960: out.append(b)
    return out

def avg_daily_volume(symbol, now):
    bs,src=try_sip_delayed_bars(symbol,"1Day",now-timedelta(days=50),now,50)
    today=now.astimezone(ET).date().isoformat()
    vals=[fnum(b.get("v")) for b in bs if str(b.get("t",""))[:10]!=today and fnum(b.get("v"))]
    vals=vals[-20:]
    return (sum(vals)/len(vals) if vals else None),src

def historical_spikes(symbol, now, current_day_pct, threshold=None):
    # Daily analogs: compare current move with same ticker's prior large up days.
    bs,src=try_sip_delayed_bars(symbol,"1Day",now-timedelta(days=450),now,450)
    if len(bs)<8:return {"status":"insufficient_history","feed":src,"samples":[]}
    rows=[]
    closes=[fnum(b.get("c")) for b in bs]
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
        row["similarity"]=abs(d-(current_day_pct or d))
        rows.append(row)
    rows.sort(key=lambda r:r["similarity"])
    samples=rows[:12]
    summary={}
    for n in (1,2,3,5):
        vals=[r[f"d{n}"] for r in samples if r.get(f"d{n}") is not None]
        summary[f"d{n}"]={"n":len(vals),"up_pct":round(100*sum(x>0 for x in vals)/len(vals),1) if vals else None,"median":round(median(vals),2) if vals else None}
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

def score_setup(metrics):
    score=50.0; reasons=[]
    p=metrics.get("price") or 0; vwap=metrics.get("vwap")
    if vwap:
        ext=pct(p,vwap)
        if ext>=0: score+=8; reasons.append("above VWAP")
        else: score-=8; reasons.append("below VWAP")
        if ext>12: score-=10; reasons.append("extended >12% above VWAP")
    m5=metrics.get("momentum_5m")
    m15=metrics.get("momentum_15m")
    if m5 is not None: score+=max(-8,min(8,m5*2))
    if m15 is not None: score+=max(-8,min(8,m15))
    fp=metrics.get("from_high_pct")
    if fp is not None:
        if fp<=3:score+=8
        elif fp>=15:score-=8; reasons.append("far below day high")
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
    snap=snapshot(symbol,LIVE_FEED)
    trade=snap.get("latestTrade") or {}; quote=snap.get("latestQuote") or {}; day=snap.get("dailyBar") or {}; prev=snap.get("prevDailyBar") or {}
    price=fnum(trade.get("p")) or fnum(day.get("c"))
    if not price: raise RuntimeError("No current price returned")
    prev_close=fnum(prev.get("c")); day_pct=pct(price,prev_close) if prev_close else None
    bid=fnum(quote.get("bp")); ask=fnum(quote.get("ap")); spread_pct=pct(ask,bid) if bid and ask else None
    if spread_pct is not None: spread_pct=spread_pct/(1+spread_pct/100) # approx spread / ask, percentage
    intraday=latest_session_bars(symbol,now)
    vwap=session_vwap_from_bars(intraday)
    high=max([fnum(x.get("h")) or 0 for x in intraday],default=fnum(day.get("h")) or price)
    low=min([fnum(x.get("l")) for x in intraday if fnum(x.get("l"))],default=fnum(day.get("l")) or price)
    from_high=(high-price)/high*100 if high else None
    def mom(n):
        if len(intraday)>=n+1:
            c=fnum(intraday[-(n+1)].get("c")); return pct(price,c) if c else None
        return None
    avgvol,volsrc=avg_daily_volume(symbol,now)
    today_volume=fnum(day.get("v")) or sum((fnum(x.get("v")) or 0) for x in intraday)
    pace=None
    if avgvol:
        expected=avgvol*(session_fraction(now_et) if is_regular(now_et) else 1)
        pace=today_volume/expected if expected else None
    daily,daysrc=try_sip_delayed_bars(symbol,"1Day",now-timedelta(days=180),now,180)
    supports,resist=pivot_levels(daily,price)
    hist=historical_spikes(symbol,now,day_pct)
    arts=[]
    try:arts=catalyst_summary(news(symbol,now),now)
    except Exception: pass
    metrics={"symbol":symbol,"as_of":now_et.isoformat(),"live_feed":LIVE_FEED.upper(),"historical_feed":daysrc,"price":round(price,4),"prev_close":prev_close,"day_pct":round(day_pct,2) if day_pct is not None else None,"bid":bid,"ask":ask,"spread_pct":round(spread_pct,2) if spread_pct is not None else None,"day_high":round(high,4),"day_low":round(low,4),"from_high_pct":round(from_high,2) if from_high is not None else None,"vwap":round(vwap,4) if vwap else None,"vwap_position":"ABOVE" if vwap and price>=vwap else "BELOW" if vwap else "N/A","vwap_extension_pct":round(pct(price,vwap),2) if vwap else None,"momentum_5m":round(mom(5),2) if mom(5) is not None else None,"momentum_15m":round(mom(15),2) if mom(15) is not None else None,"momentum_30m":round(mom(30),2) if mom(30) is not None else None,"volume":round(today_volume),"avg_20d_volume":round(avgvol) if avgvol else None,"volume_pace":round(pace,2) if pace is not None else None,"volume_source":volsrc,"supports":supports,"resistances":resist,"historical_analogs":hist,"news":arts}
    score,grade,entry,reasons=score_setup(metrics)
    metrics.update({"score":score,"grade":grade,"entry_quality":entry,"score_reasons":reasons})
    return metrics

if __name__=="__main__":
    import sys
    ticker=(sys.argv[1] if len(sys.argv)>1 else "SDOT")
    print(json.dumps(analyze(ticker),indent=2))
