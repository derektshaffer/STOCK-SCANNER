"""Multi-session stair-step / plateau -> reacceleration structure.

This module is intentionally pure: no Streamlit, network, or model imports.
It can be reused by the live analyzer, historical replay, and ML training.
All calculations use only bars supplied by the caller.
"""

from __future__ import annotations

import math
from statistics import median

from multi_session_structure import detect_multi_session_structure


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


def detect_stair_step(
    daily_bars,
    current_day=None,
    atr_pct=None,
    current_day_completed=False,
):
    """Canonical multi-session structure with explicit completion lifecycle."""
    return detect_multi_session_structure(
        daily_bars or [],
        current_day=current_day,
        current_day_completed=current_day_completed,
        atr_pct=atr_pct,
    )

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
        "stair_reacceleration_developing":1.0 if ctx.get("reacceleration_developing") else 0.0,
        "stair_breakdown":1.0 if ctx.get("breakdown") else 0.0,
        "stair_breakdown_confirmed":1.0 if ctx.get("breakdown_confirmed") else 0.0,
        "stair_breakdown_developing":1.0 if ctx.get("breakdown_developing") else 0.0,
    }
