"""Leakage-safe ordered candle inputs for sequence ML research.

The sequence representation is deliberately data-first rather than image-first:
each row is a five-minute candle encoded in chronological order. Every feature
uses only bars at or before the observation timestamp.

This module contains no model code. It is shared by historical replay and, once
validated, can later be reused by a live shadow predictor.
"""

from __future__ import annotations

import math
from statistics import median


SEQUENCE_INPUT_VERSION = "sequence-input-v1-5m-60bars"
SEQUENCE_MAX_BARS = 60
SEQUENCE_BAR_FEATURES = (
    "open_rel_pct",
    "high_rel_pct",
    "low_rel_pct",
    "close_rel_pct",
    "return_1bar_pct",
    "range_pct",
    "body_pct",
    "volume_rel",
    "session_vwap_rel_pct",
    "close_vs_session_vwap_pct",
    "time_fraction",
    "mask",
)


def _num(value):
    try:
        value=float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _bar_parts(item):
    if isinstance(item,(list,tuple)) and len(item)>=2 and isinstance(item[1],dict):
        return item[0],item[1]
    if isinstance(item,dict):
        return item.get("minute"),item
    return None,None


def _pct(value,base):
    if value is None or base is None or base==0:
        return None
    return (value/base-1.0)*100.0


def _median_positive(values):
    clean=[float(v) for v in values if v is not None and v>0]
    return float(median(clean)) if clean else None


def build_causal_candle_sequence(rows, idx, max_bars=SEQUENCE_MAX_BARS):
    """Return a fixed-width causal candle sequence ending exactly at idx.

    rows may be historical replay (minute, bar) pairs or bar dicts.
    The output is left-padded with None feature rows. Consumers should
    translate those values to model-native missing values.

    Normalization uses only the prefix through idx. Earlier candles may be
    scaled by statistics observed later in the same prefix, which is valid at
    the decision timestamp and never reaches beyond it.
    """
    if idx is None:
        return None
    try:
        idx=int(idx)
    except Exception:
        return None
    if idx<0 or idx>=len(rows or []):
        return None

    prefix=[]
    for source_idx,item in enumerate((rows or [])[:idx+1]):
        minute,bar=_bar_parts(item)
        if not isinstance(bar,dict):
            continue
        o=_num(bar.get("o"))
        h=_num(bar.get("h"))
        l=_num(bar.get("l"))
        c=_num(bar.get("c"))
        v=_num(bar.get("v")) or 0.0
        if c is None or c<=0 or h is None or l is None:
            continue
        if o is None:
            o=c
        if h<l:
            h,l=l,h
        prefix.append({
            "source_index":source_idx,
            "minute":_num(minute),
            "o":o,"h":h,"l":l,"c":c,"v":v,
        })

    if not prefix:
        return None

    ref_price=prefix[-1]["c"]
    volume_scale=_median_positive(row["v"] for row in prefix)
    if volume_scale is None or volume_scale<=0:
        volume_scale=1.0

    pv=0.0
    total_volume=0.0
    enriched=[]
    previous_close=None
    for ordinal,row in enumerate(prefix):
        typical=(row["h"]+row["l"]+row["c"])/3.0
        pv+=typical*row["v"]
        total_volume+=row["v"]
        session_vwap=pv/total_volume if total_volume>0 else row["c"]
        bar_range=max(0.0,row["h"]-row["l"])
        body=row["c"]-row["o"]
        minute=row.get("minute")
        time_fraction=(
            max(0.0,min(1.0,(minute-570.0)/390.0))
            if minute is not None
            else ordinal/max(1,len(prefix)-1)
        )
        enriched.append([
            _pct(row["o"],ref_price),
            _pct(row["h"],ref_price),
            _pct(row["l"],ref_price),
            _pct(row["c"],ref_price),
            _pct(row["c"],previous_close) if previous_close else 0.0,
            (bar_range/row["c"]*100.0) if row["c"] else None,
            (body/row["c"]*100.0) if row["c"] else None,
            row["v"]/volume_scale,
            _pct(session_vwap,ref_price),
            _pct(row["c"],session_vwap),
            time_fraction,
            1.0,
        ])
        previous_close=row["c"]

    max_bars=max(1,int(max_bars))
    selected=enriched[-max_bars:]
    missing=max_bars-len(selected)
    pad=[[None]*(len(SEQUENCE_BAR_FEATURES)-1)+[0.0] for _ in range(missing)]
    sequence=pad+selected

    return {
        "sequence_version":SEQUENCE_INPUT_VERSION,
        "bar_feature_names":list(SEQUENCE_BAR_FEATURES),
        "max_bars":max_bars,
        "bars_available":min(len(enriched),max_bars),
        "sequence":sequence,
        "reference_price":round(ref_price,6),
    }


def flatten_sequence(sequence_payload):
    """Flatten a sequence payload while preserving lag order."""
    if not isinstance(sequence_payload,dict):
        return []
    rows=sequence_payload.get("sequence") or []
    flat=[]
    for row in rows:
        if not isinstance(row,(list,tuple)):
            flat.extend([None]*len(SEQUENCE_BAR_FEATURES))
            continue
        values=list(row)[:len(SEQUENCE_BAR_FEATURES)]
        if len(values)<len(SEQUENCE_BAR_FEATURES):
            values.extend([None]*(len(SEQUENCE_BAR_FEATURES)-len(values)))
        flat.extend(values)
    return flat


def flat_feature_names(max_bars=SEQUENCE_MAX_BARS):
    names=[]
    max_bars=max(1,int(max_bars))
    for pos in range(max_bars):
        lag=max_bars-1-pos
        for feature in SEQUENCE_BAR_FEATURES:
            names.append(f"lag_{lag:02d}_{feature}")
    return names
