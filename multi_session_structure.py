"""Canonical multi-session step / accepted-level market structure.

This module distinguishes completed historical structure from the still-open
current session. A partial daily candle may create a developing reacceleration
or live breakdown warning, but it cannot retroactively become a confirmed
multi-session step until that session is complete.
"""

from __future__ import annotations

import math
from statistics import median


MULTI_SESSION_STRUCTURE_VERSION = "multi-session-structure-v1-confirmed-levels"


def _num(value):
    try:
        x=float(value)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _clamp(value, low, high):
    return max(low, min(high, value))


def _date_key(value):
    text=str(value or "")
    return text[:10] if len(text)>=10 else text


def normalize_daily_structure(
    daily_bars,
    *,
    current_day=None,
    current_day_completed=False,
):
    by_date={}
    order=[]
    for bar in daily_bars or []:
        if not isinstance(bar,dict):
            continue
        o,h,l,c=(_num(bar.get(k)) for k in ("o","h","l","c"))
        v=_num(bar.get("v")) or 0.0
        if any(x is None for x in (o,h,l,c)) or min(o,h,l,c)<=0:
            continue
        key=_date_key(bar.get("t")) or f"row-{len(order)}"
        if key not in by_date:
            order.append(key)
        by_date[key]={
            "o":o,"h":h,"l":l,"c":c,"v":v,
            "t":bar.get("t"),"date":key,"completed":True,
        }

    if isinstance(current_day,dict):
        c=_num(current_day.get("c") or current_day.get("price"))
        h=_num(current_day.get("h") or current_day.get("high") or c)
        l=_num(current_day.get("l") or current_day.get("low") or c)
        o=_num(current_day.get("o") or current_day.get("open") or c)
        v=_num(current_day.get("v") or current_day.get("volume")) or 0.0
        key=_date_key(current_day.get("t") or current_day.get("date")) or "CURRENT"
        if c and h and l and o and min(c,h,l,o)>0:
            if key not in by_date:
                order.append(key)
            by_date[key]={
                "o":o,"h":h,"l":l,"c":c,"v":v,
                "t":current_day.get("t"),"date":key,
                "completed":bool(current_day_completed),
            }
    return [by_date[key] for key in order]


def _step_threshold(completed_rows, atr_pct):
    atrp=_num(atr_pct)
    atr_component=(atrp or 8.0)*0.55
    changes=[]
    for i in range(1,len(completed_rows)):
        prev=completed_rows[i-1]["c"]
        cur=completed_rows[i]["c"]
        if prev>0:
            changes.append(abs((cur/prev-1.0)*100.0))
    local=median(changes[-12:]) if changes else None
    # Keep historical behavior broadly comparable, but do not let one huge
    # recent day redefine the entire step threshold.
    if local is not None:
        local_component=_clamp(local*1.35,3.0,10.0)
        threshold=0.70*atr_component+0.30*local_component
    else:
        threshold=atr_component
    return _clamp(threshold,4.0,12.0)


def _qualifies_step(prev,cur,threshold):
    move=(cur["c"]/prev["c"]-1.0)*100.0
    higher_low=(cur["l"]/prev["c"]-1.0)*100.0
    day_range=max(1e-9,cur["h"]-cur["l"])
    close_loc=(cur["c"]-cur["l"])/day_range
    qualifies=bool(
        move>=threshold
        or (
            move>=threshold*0.70
            and higher_low>=1.5
            and close_loc>=0.62
        )
    )
    return qualifies,move,higher_low,close_loc


def detect_multi_session_structure(
    daily_bars,
    *,
    current_day=None,
    current_day_completed=False,
    atr_pct=None,
):
    rows=normalize_daily_structure(
        daily_bars,
        current_day=current_day,
        current_day_completed=current_day_completed,
    )
    if len(rows)<4:
        return {
            "status":"insufficient_data",
            "detected":False,
            "structure_version":MULTI_SESSION_STRUCTURE_VERSION,
            "step_count":0,
            "steps":[],
            "reaccelerating":False,
            "reacceleration_developing":False,
        }

    rows=rows[-20:]
    completed=[row for row in rows if row.get("completed")]
    partial=next((row for row in reversed(rows) if not row.get("completed")),None)
    if len(completed)<3:
        return {
            "status":"insufficient_completed_data",
            "detected":False,
            "structure_version":MULTI_SESSION_STRUCTURE_VERSION,
            "step_count":0,
            "steps":[],
            "reaccelerating":False,
            "reacceleration_developing":False,
        }

    threshold=_step_threshold(completed,atr_pct)
    events=[]
    for i in range(1,len(completed)):
        qualifies,move,higher_low,close_loc=_qualifies_step(
            completed[i-1],completed[i],threshold
        )
        if qualifies:
            cur=completed[i]
            prev=completed[i-1]
            events.append({
                "index":i,
                "date":cur["date"],
                "confirmed":True,
                "pre_close":prev["c"],
                "step_close":cur["c"],
                "step_high":cur["h"],
                "step_low":cur["l"],
                "step_volume":cur["v"],
                "step_pct":move,
                "higher_low_pct":higher_low,
                "close_location":close_loc,
            })

    developing_step=None
    if partial is not None and completed:
        qualifies,move,higher_low,close_loc=_qualifies_step(
            completed[-1],partial,threshold
        )
        if qualifies:
            developing_step={
                "date":partial["date"],
                "confirmed":False,
                "pre_close":completed[-1]["c"],
                "step_close":partial["c"],
                "step_high":partial["h"],
                "step_low":partial["l"],
                "step_volume":partial["v"],
                "step_pct":move,
                "higher_low_pct":higher_low,
                "close_location":close_loc,
                "state":"DEVELOPING",
            }

    if not events:
        return {
            "status":"ok",
            "detected":False,
            "structure_version":MULTI_SESSION_STRUCTURE_VERSION,
            "state":(
                "REACCELERATION DEVELOPING"
                if developing_step
                else "NO CLEAR STAIR-STEP"
            ),
            "step_count":0,
            "steps":[],
            "developing_step":developing_step,
            "step_threshold_pct":round(threshold,2),
            "structure_score":32.0 if developing_step else 25.0,
            "reaccelerating":False,
            "reacceleration_developing":bool(developing_step),
            "breakdown":False,
            "breakdown_confirmed":False,
            "breakdown_developing":False,
        }

    enriched=[]
    for n,event in enumerate(events):
        start=event["index"]+1
        end=events[n+1]["index"] if n+1<len(events) else len(completed)
        plateau=completed[start:end]
        pre=event["pre_close"]
        step_close=event["step_close"]
        gain=max(1e-9,step_close-pre)

        if plateau:
            closes=[r["c"] for r in plateau]
            highs=[r["h"] for r in plateau]
            lows=[r["l"] for r in plateau]
            vols=[r["v"] for r in plateau if r["v"]>0]
            center=median(closes)
            range_pct=(max(highs)/min(lows)-1.0)*100.0 if min(lows)>0 else None
            retention=(center-pre)/gain*100.0
            floor_retention=(min(lows)-pre)/gain*100.0
            vol_ratio=(median(vols)/event["step_volume"]) if vols and event["step_volume"]>0 else None
        else:
            center=step_close
            range_pct=0.0
            retention=100.0
            floor_retention=100.0
            vol_ratio=None

        item=dict(event)
        item.update({
            "plateau_days":len(plateau),
            "plateau_center":center,
            "plateau_range_pct":range_pct,
            "plateau_retention_pct":retention,
            "plateau_floor_retention_pct":floor_retention,
            "plateau_volume_ratio":vol_ratio,
        })
        enriched.append(item)

    last=enriched[-1]
    previous=enriched[-2] if len(enriched)>=2 else None
    latest_completed=completed[-1]
    reaccelerating_confirmed=bool(
        last["index"]==len(completed)-1 and len(enriched)>=2
    )
    reacceleration_developing=bool(developing_step and len(enriched)>=1)

    current_plateau_days=int(last.get("plateau_days") or 0)
    current_center=_num(last.get("plateau_center"))
    current_range=_num(last.get("plateau_range_pct"))
    retention=_num(last.get("plateau_retention_pct"))
    floor_retention=_num(last.get("plateau_floor_retention_pct"))
    vol_ratio=_num(last.get("plateau_volume_ratio"))

    levels=[
        _num(e.get("plateau_center")) or _num(e.get("step_close"))
        for e in enriched
    ]
    higher_plateau_count=0
    for i in range(len(levels)-1,0,-1):
        if levels[i] and levels[i-1] and levels[i]>levels[i-1]*1.015:
            higher_plateau_count+=1
        else:
            break

    last_step=_num(last.get("step_pct"))
    prior_step=_num(previous.get("step_pct")) if previous else None
    acceleration=(
        last_step/prior_step
        if last_step is not None and prior_step and prior_step>0
        else None
    )

    denominator=max(1e-9,last["step_close"]-last["pre_close"])
    completed_retention=(
        (latest_completed["c"]-last["pre_close"])/denominator*100.0
    )
    breakdown_confirmed=bool(completed_retention<35.0)

    live_retention=None
    breakdown_developing=False
    if partial is not None:
        live_retention=(partial["c"]-last["pre_close"])/denominator*100.0
        breakdown_developing=bool(live_retention<35.0)

    atrp=_num(atr_pct) or 8.0
    plateau_tight=bool(
        current_range is not None
        and current_range<=max(7.5,atrp*0.85)
    )
    volume_cooled=bool(vol_ratio is not None and vol_ratio<=0.75)

    score=38.0
    score+=min(24.0,len(enriched)*8.0)
    score+=min(14.0,higher_plateau_count*7.0)
    if retention is not None:
        if retention>=75:score+=10
        elif retention>=55:score+=5
        elif retention<35:score-=14
    if floor_retention is not None:
        if floor_retention>=50:score+=6
        elif floor_retention<20:score-=8
    if plateau_tight and current_plateau_days>=1:score+=8
    if volume_cooled and current_plateau_days>=1:score+=6
    if reaccelerating_confirmed:score+=12
    elif reacceleration_developing:score+=4
    if acceleration is not None:
        if acceleration>=1.15:score+=5
        elif acceleration<0.65:score-=4
    if breakdown_confirmed:score-=28
    elif breakdown_developing:score-=12
    score=_clamp(score,0.0,100.0)

    if breakdown_confirmed:
        state="FAILED STAIR-STEP / LOST PLATEAU"
    elif breakdown_developing:
        state="PLATEAU BREAKDOWN DEVELOPING"
    elif reaccelerating_confirmed:
        state="REACCELERATING STAIR-STEP"
    elif reacceleration_developing:
        state="REACCELERATION DEVELOPING"
    elif current_plateau_days>=1 and retention is not None and retention>=55:
        state="HIGHER PLATEAU / COILING"
    elif len(enriched)>=2 and higher_plateau_count>=1:
        state="STAIR-STEP TREND"
    else:
        state="EARLY STEP STRUCTURE"

    return {
        "status":"ok",
        "detected":True,
        "structure_version":MULTI_SESSION_STRUCTURE_VERSION,
        "state":state,
        "structure_score":round(score,1),
        "step_threshold_pct":round(threshold,2),
        "step_count":len(enriched),
        "steps":[
            {
                "number":i+1,
                "date":e.get("date"),
                "confirmed":True,
                "step_pct":round(e["step_pct"],2),
                "step_close":round(e["step_close"],4),
                "plateau_days":int(e.get("plateau_days") or 0),
                "plateau_center":round(e["plateau_center"],4) if _num(e.get("plateau_center")) is not None else None,
                "plateau_range_pct":round(e["plateau_range_pct"],2) if _num(e.get("plateau_range_pct")) is not None else None,
                "plateau_retention_pct":round(e["plateau_retention_pct"],1) if _num(e.get("plateau_retention_pct")) is not None else None,
                "plateau_floor_retention_pct":round(e["plateau_floor_retention_pct"],1) if _num(e.get("plateau_floor_retention_pct")) is not None else None,
                "plateau_volume_ratio":round(e["plateau_volume_ratio"],3) if _num(e.get("plateau_volume_ratio")) is not None else None,
            }
            for i,e in enumerate(enriched)
        ],
        "developing_step":developing_step,
        "last_step_pct":round(last_step,2) if last_step is not None else None,
        "prior_step_pct":round(prior_step,2) if prior_step is not None else None,
        "step_acceleration_ratio":round(acceleration,3) if acceleration is not None else None,
        "current_plateau_days":current_plateau_days,
        "current_plateau_center":round(current_center,4) if current_center is not None else None,
        "current_plateau_range_pct":round(current_range,2) if current_range is not None else None,
        "current_plateau_retention_pct":round(retention,1) if retention is not None else None,
        "current_plateau_floor_retention_pct":round(floor_retention,1) if floor_retention is not None else None,
        "plateau_volume_ratio":round(vol_ratio,3) if vol_ratio is not None else None,
        "higher_plateau_count":int(higher_plateau_count),
        "plateau_tight":plateau_tight,
        "volume_cooled":volume_cooled,
        "reaccelerating":bool(reaccelerating_confirmed),
        "reacceleration_developing":bool(reacceleration_developing),
        "breakdown":bool(breakdown_confirmed or breakdown_developing),
        "breakdown_confirmed":bool(breakdown_confirmed),
        "breakdown_developing":bool(breakdown_developing),
        "latest_step_retention_pct":round(completed_retention,1),
        "live_step_retention_pct":round(live_retention,1) if live_retention is not None else None,
        "current_day_completed":bool(current_day_completed),
    }
