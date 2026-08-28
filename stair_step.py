"""Multi-session stair-step / plateau -> reacceleration structure.

This module is intentionally pure: no Streamlit, network, or model imports.
It can be reused by the live analyzer, historical replay, and ML training.
All calculations use only bars supplied by the caller.
"""

from __future__ import annotations

import math
from statistics import median


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


def _rows(daily_bars, current_day=None):
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
        by_date[key]={"o":o,"h":h,"l":l,"c":c,"v":v,"t":bar.get("t"),"date":key}

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
            by_date[key]={"o":o,"h":h,"l":l,"c":c,"v":v,"t":current_day.get("t"),"date":key}

    return [by_date[k] for k in order]


def detect_stair_step(daily_bars, current_day=None, atr_pct=None):
    """Detect multi-session upward step -> plateau -> reacceleration behavior.

    A "step" is a meaningful close-to-close expansion. The bars after each
    step are evaluated as a plateau: how much of the step is retained, whether
    the range compresses, whether volume cools, and whether the next expansion
    starts from a higher accepted price level.

    Output scores describe structure; they are not calibrated probabilities.
    """

    rows=_rows(daily_bars,current_day=current_day)
    if len(rows)<4:
        return {
            "status":"insufficient_data",
            "detected":False,
            "step_count":0,
            "steps":[],
        }

    rows=rows[-20:]
    atrp=_num(atr_pct)
    step_threshold=_clamp((atrp or 8.0)*0.55,4.0,12.0)

    # A close gap/expansion is the cleanest multi-day representation of a
    # "chunk" higher. We also allow a slightly smaller close jump when the day
    # establishes a clearly higher low and closes near its high.
    events=[]
    for i in range(1,len(rows)):
        prev=rows[i-1]
        cur=rows[i]
        move=(cur["c"]/prev["c"]-1.0)*100.0
        higher_low=(cur["l"]/prev["c"]-1.0)*100.0
        day_range=max(1e-9,cur["h"]-cur["l"])
        close_loc=(cur["c"]-cur["l"])/day_range
        qualifies=(
            move>=step_threshold
            or (
                move>=step_threshold*0.70
                and higher_low>=1.5
                and close_loc>=0.62
            )
        )
        if qualifies:
            events.append({
                "index":i,
                "date":cur["date"],
                "pre_close":prev["c"],
                "step_close":cur["c"],
                "step_high":cur["h"],
                "step_low":cur["l"],
                "step_volume":cur["v"],
                "step_pct":move,
                "close_location":close_loc,
            })

    if not events:
        return {
            "status":"ok",
            "detected":False,
            "state":"NO CLEAR STAIR-STEP",
            "step_count":0,
            "steps":[],
            "step_threshold_pct":round(step_threshold,2),
            "structure_score":25.0,
        }

    # Build plateau measurements between step events.
    enriched=[]
    for n,event in enumerate(events):
        start=event["index"]+1
        end=events[n+1]["index"] if n+1<len(events) else len(rows)
        plateau=rows[start:end]
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
            vol_ratio=(median(vols)/event["step_volume"]) if vols and event["step_volume"]>0 else None
            floor_retention=(min(lows)-pre)/gain*100.0
        else:
            center=step_close
            range_pct=0.0
            retention=100.0
            vol_ratio=None
            floor_retention=100.0

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
    last_event_idx=last["index"]
    latest=rows[-1]

    reaccelerating=(last_event_idx==len(rows)-1 and len(enriched)>=2)
    current_plateau_days=int(last.get("plateau_days") or 0)
    current_center=_num(last.get("plateau_center"))
    current_range=_num(last.get("plateau_range_pct"))
    retention=_num(last.get("plateau_retention_pct"))
    floor_retention=_num(last.get("plateau_floor_retention_pct"))
    vol_ratio=_num(last.get("plateau_volume_ratio"))

    # Higher accepted levels: compare plateau centers / step closes.
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
    acceleration=(last_step/prior_step) if last_step is not None and prior_step and prior_step>0 else None

    # A failed stair-step means price surrendered most of the most recent step.
    latest_retention=(latest["c"]-last["pre_close"])/max(1e-9,last["step_close"]-last["pre_close"])*100.0
    breakdown=bool(latest_retention<35.0)
    plateau_tight=bool(current_range is not None and current_range<=max(7.5,(atrp or 8.0)*0.85))
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
    if reaccelerating:score+=12
    if acceleration is not None:
        if acceleration>=1.15:score+=5
        elif acceleration<0.65:score-=4
    if breakdown:score-=28
    score=_clamp(score,0.0,100.0)

    if breakdown:
        state="FAILED STAIR-STEP / LOST PLATEAU"
    elif reaccelerating:
        state="REACCELERATING STAIR-STEP"
    elif current_plateau_days>=1 and retention is not None and retention>=55:
        state="HIGHER PLATEAU / COILING"
    elif len(enriched)>=2 and higher_plateau_count>=1:
        state="STAIR-STEP TREND"
    else:
        state="EARLY STEP STRUCTURE"

    return {
        "status":"ok",
        "detected":bool(len(enriched)>=1),
        "state":state,
        "structure_score":round(score,1),
        "step_threshold_pct":round(step_threshold,2),
        "step_count":len(enriched),
        "steps":[
            {
                "number":i+1,
                "date":e.get("date"),
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
        "reaccelerating":bool(reaccelerating),
        "breakdown":bool(breakdown),
        "latest_step_retention_pct":round(latest_retention,1),
    }


def stair_step_feature_values(context):
    ctx=context or {}
    return {
        "stair_step_count":float(ctx.get("step_count") or 0),
        "stair_last_step_pct":_num(ctx.get("last_step_pct")),
        "stair_step_acceleration_ratio":_num(ctx.get("step_acceleration_ratio")),
        "stair_plateau_days":float(ctx.get("current_plateau_days") or 0),
        "stair_plateau_range_pct":_num(ctx.get("current_plateau_range_pct")),
        "stair_plateau_retention_pct":_num(ctx.get("current_plateau_retention_pct")),
        "stair_plateau_volume_ratio":_num(ctx.get("plateau_volume_ratio")),
        "stair_higher_plateau_count":float(ctx.get("higher_plateau_count") or 0),
        "stair_structure_score":_num(ctx.get("structure_score")),
        "stair_reaccelerating":1.0 if ctx.get("reaccelerating") else 0.0,
        "stair_breakdown":1.0 if ctx.get("breakdown") else 0.0,
    }
