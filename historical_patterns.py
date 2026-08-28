import math
from collections import defaultdict
from statistics import median

_CACHE = {}

def _fnum(x):
    try:
        return float(x)
    except Exception:
        return None

def _pct(a, b):
    return None if not b else (a / b - 1) * 100

def _clamp(x, lo, hi):
    return max(lo, min(hi, x))

def _median(rows, key):
    vals = [_fnum(r.get(key)) for r in rows]
    vals = [v for v in vals if v is not None]
    return round(median(vals), 2) if vals else None

def _rate(rows, key):
    if not rows:
        return None
    return round(100 * sum(bool(r.get(key)) for r in rows) / len(rows), 1)

def _bar_time_et(bar, et):
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(bar.get("t", "")).replace("Z", "+00:00")).astimezone(et)
    except Exception:
        return None

def _regular(bar, et):
    dt = _bar_time_et(bar, et)
    if dt is None:
        return False
    minute = dt.hour * 60 + dt.minute
    return dt.weekday() < 5 and 570 <= minute < 960

def _cached(fetch_bars, symbol, timeframe, now, days, limit, ttl=1800):
    key = (symbol.upper(), timeframe, int(days), int(limit))
    stamp = now.timestamp()
    cached = _CACHE.get(key)
    if cached and stamp - cached["stamp"] < ttl:
        return cached["bars"], cached["source"]
    from datetime import timedelta
    bars, source = fetch_bars(symbol, timeframe, now - timedelta(days=days), now, limit)
    _CACHE[key] = {"stamp": stamp, "bars": bars, "source": source}
    return bars, source

def _label(gap_pct, close_from_open, close_location, day_pct):
    gap = gap_pct or 0
    cfo = close_from_open or 0
    loc = close_location if close_location is not None else 0.5
    move = day_pct or 0
    if gap >= 2:
        if cfo >= 1 and loc >= 0.60:
            return "GAP & RUN"
        if cfo <= -1 or loc <= 0.40:
            return "GAP & FADE"
        return "GAP & HOLD"
    if move >= 10 and loc >= 0.65:
        return "MOMENTUM RUN"
    if move >= 6 and loc <= 0.40:
        return "LATE FADE"
    return "TREND / MIXED"

def _impulse_pullback_stats(daybars):
    """Measure the dominant intraday impulse and first meaningful retracement."""
    if not daybars or len(daybars) < 8:
        return {}
    rows=[]
    for b in daybars:
        h=_fnum(b.get("h")); l=_fnum(b.get("l")); cc=_fnum(b.get("c")); v=_fnum(b.get("v")) or 0.0
        if h is None or l is None or cc is None or h<=0 or l<=0:
            continue
        rows.append({"h":h,"l":l,"c":cc,"v":v})
    if len(rows)<8:
        return {}

    candidates=[]
    n=len(rows)
    for peak_idx in range(4,n-2):
        start=max(0,peak_idx-24)
        low_idx=min(range(start,peak_idx),key=lambda i:rows[i]["l"])
        low=rows[low_idx]["l"]; peak=rows[peak_idx]["h"]
        if peak<=low:
            continue
        move=(peak/low-1)*100.0
        if move<7:
            continue
        future=rows[peak_idx+1:min(n,peak_idx+13)]
        if not future:
            continue
        trough_rel=min(range(len(future)),key=lambda i:future[i]["l"])
        trough_idx=peak_idx+1+trough_rel
        trough=rows[trough_idx]["l"]
        run=peak-low
        retrace=(peak-trough)/run*100.0
        after=rows[trough_idx:]
        later_high=max((r["h"] for r in after),default=trough)
        bounce=(later_high/trough-1)*100.0 if trough else None
        iv=[rows[i]["v"] for i in range(low_idx,peak_idx+1) if rows[i]["v"]>0]
        pv=[rows[i]["v"] for i in range(peak_idx+1,trough_idx+1) if rows[i]["v"]>0]
        iva=sum(iv)/len(iv) if iv else None
        pva=sum(pv)/len(pv) if pv else None
        vr=pva/iva if iva and pva is not None else None
        score=move*(1.0 if 18<=retrace<=70 else .7)
        candidates.append((score,move,retrace,bounce,vr))
    if not candidates:
        return {}
    _,move,retrace,bounce,vr=max(candidates,key=lambda x:x[0])
    return {
        "impulse_move_pct":round(move,2),
        "impulse_retracement_pct":round(retrace,2),
        "impulse_bounce_mfe_pct":round(bounce,2) if bounce is not None else None,
        "pullback_volume_ratio":round(vr,3) if vr is not None else None,
        "bounce_5pct":bool(bounce is not None and bounce>=5.0),
    }


def _intraday_stats(symbol, now, candidate_dates, fetch_bars, et):
    try:
        bars5, source = _cached(fetch_bars, symbol, "5Min", now, 540, 10000)
    except Exception:
        return {"status": "unavailable", "sample_count": 0}

    grouped = defaultdict(list)
    for bar in bars5:
        if not _regular(bar, et):
            continue
        dt = _bar_time_et(bar, et)
        if dt:
            grouped[dt.date().isoformat()].append(bar)

    rows = []
    for date in candidate_dates:
        daybars = sorted(grouped.get(date) or [], key=lambda b: str(b.get("t", "")))
        if len(daybars) < 18:
            continue
        last_close = _fnum(daybars[-1].get("c"))
        if not last_close:
            continue

        first6 = daybars[:6]
        opening_high = max((_fnum(b.get("h")) or 0 for b in first6), default=0)
        pull_window = daybars[3:12]
        pull_low_values = [_fnum(b.get("l")) for b in pull_window]
        pull_low_values = [v for v in pull_low_values if v is not None]
        pull_low = min(pull_low_values) if pull_low_values else None
        early_pullback = _pct(pull_low, opening_high) if pull_low and opening_high else None

        pv = 0.0
        volsum = 0.0
        was_below = False
        reclaim_idx = None
        reclaim_price = None
        for idx, bar in enumerate(daybars):
            vol = _fnum(bar.get("v")) or 0
            typical = ((_fnum(bar.get("h")) or 0) + (_fnum(bar.get("l")) or 0) + (_fnum(bar.get("c")) or 0)) / 3
            pv += typical * vol
            volsum += vol
            rvwap = pv / volsum if volsum else None
            close = _fnum(bar.get("c"))
            if not rvwap or not close:
                continue
            if idx >= 2 and close < rvwap * 0.997:
                was_below = True
            elif was_below and idx >= 3 and close > rvwap * 1.002:
                reclaim_idx = idx
                reclaim_price = close
                break

        reclaim_follow = None
        reclaim_gain = None
        reclaim_mfe = None
        if reclaim_idx is not None and reclaim_price:
            after = daybars[reclaim_idx:]
            highs = [_fnum(b.get("h")) for b in after]
            highs = [v for v in highs if v is not None]
            reclaim_gain = _pct(last_close, reclaim_price)
            reclaim_mfe = _pct(max(highs), reclaim_price) if highs else None
            reclaim_follow = bool(last_close >= reclaim_price * 1.01)

        breakout_tested = False
        breakout_follow = None
        breakout_failed = None
        if opening_high:
            later = daybars[6:]
            breakout_tested = any((_fnum(b.get("h")) or 0) > opening_high * 1.002 for b in later)
            if breakout_tested:
                breakout_follow = bool(last_close > opening_high * 1.005)
                breakout_failed = bool(last_close < opening_high)

        high_bar = max(daybars, key=lambda b: _fnum(b.get("h")) or 0)
        high_dt = _bar_time_et(high_bar, et)
        high_bucket = None
        if high_dt:
            mins = high_dt.hour * 60 + high_dt.minute
            high_bucket = "MORNING" if mins < 660 else "MIDDAY" if mins < 840 else "POWER HOUR"

        impulse_stats=_impulse_pullback_stats(daybars)

        rows.append({
            "date": date,
            **impulse_stats,
            "first_pullback_pct": round(early_pullback, 2) if early_pullback is not None else None,
            "vwap_reclaimed": reclaim_idx is not None,
            "vwap_reclaim_follow": reclaim_follow,
            "vwap_reclaim_close_gain_pct": round(reclaim_gain, 2) if reclaim_gain is not None else None,
            "vwap_reclaim_mfe_pct": round(reclaim_mfe, 2) if reclaim_mfe is not None else None,
            "opening_breakout_tested": breakout_tested,
            "opening_breakout_follow": breakout_follow,
            "opening_breakout_failed": breakout_failed,
            "session_high_bucket": high_bucket,
        })
        if len(rows) >= 24:
            break

    if len(rows) < 3:
        return {"status": "insufficient_history", "source": source, "sample_count": len(rows), "samples": rows}

    reclaim_rows = [r for r in rows if r.get("vwap_reclaimed")]
    breakout_rows = [r for r in rows if r.get("opening_breakout_tested")]
    buckets = defaultdict(int)
    for row in rows:
        if row.get("session_high_bucket"):
            buckets[row["session_high_bucket"]] += 1
    peak_bucket = max(buckets, key=buckets.get) if buckets else None

    return {
        "status": "ok",
        "source": source,
        "sample_count": len(rows),
        "median_first_pullback_pct": _median(rows, "first_pullback_pct"),
        "median_impulse_move_pct": _median(rows, "impulse_move_pct"),
        "median_impulse_retracement_pct": _median(rows, "impulse_retracement_pct"),
        "median_impulse_bounce_mfe_pct": _median(rows, "impulse_bounce_mfe_pct"),
        "median_pullback_volume_ratio": _median(rows, "pullback_volume_ratio"),
        "impulse_bounce_5pct_rate": _rate(
            [r for r in rows if r.get("impulse_retracement_pct") is not None],
            "bounce_5pct",
        ),
        "vwap_reclaim_rate_pct": round(100 * len(reclaim_rows) / len(rows), 1),
        "vwap_reclaim_follow_through_pct": _rate(reclaim_rows, "vwap_reclaim_follow"),
        "median_reclaim_close_gain_pct": _median(reclaim_rows, "vwap_reclaim_close_gain_pct"),
        "median_reclaim_mfe_pct": _median(reclaim_rows, "vwap_reclaim_mfe_pct"),
        "opening_breakout_test_rate_pct": round(100 * len(breakout_rows) / len(rows), 1),
        "opening_breakout_follow_through_pct": _rate(breakout_rows, "opening_breakout_follow"),
        "opening_breakout_failure_pct": _rate(breakout_rows, "opening_breakout_failed"),
        "session_high_most_common": peak_bucket,
        "session_high_distribution": dict(buckets),
        "samples": rows,
    }

def analyze_historical_patterns(symbol, now, current_day_pct, current_gap_pct, current_volume_pace, fetch_bars, et):
    """Find same-ticker historical days that resemble today's setup.

    Similarity uses current move size, opening gap, and relative volume. It then
    measures gap-and-run vs gap-and-fade behavior, next-day outcomes, breakout
    follow-through/failure, recent VWAP-reclaim behavior, early pullback depth,
    and when matched sessions most often made their high.
    """
    try:
        daily, source = _cached(fetch_bars, symbol, "1Day", now, 900, 1000)
    except Exception:
        return {"status": "unavailable", "sample_count": 0, "matches": []}

    if len(daily) < 25:
        return {"status": "insufficient_history", "source": source, "sample_count": 0, "matches": []}

    today = now.astimezone(et).date().isoformat()
    current_move = _fnum(current_day_pct) or 0
    current_gap = _fnum(current_gap_pct) or 0
    current_rvol = max(0.15, _fnum(current_volume_pace) or 1.0)
    candidates = []

    for i in range(20, len(daily) - 1):
        bar = daily[i]
        date = str(bar.get("t", ""))[:10]
        if date == today:
            continue
        prev = daily[i - 1]
        nxt = daily[i + 1]
        o, h, l, c = (_fnum(bar.get(k)) for k in ("o", "h", "l", "c"))
        v = _fnum(bar.get("v"))
        pc = _fnum(prev.get("c"))
        if any(x is None for x in (o, h, l, c, pc)) or pc <= 0 or o <= 0 or c <= 0:
            continue

        day_ret = _pct(c, pc)
        gap = _pct(o, pc)
        close_from_open = _pct(c, o)
        close_location = (c - l) / (h - l) if h > l else 0.5

        prior_vols = [_fnum(x.get("v")) for x in daily[max(0, i - 20):i]]
        prior_vols = [x for x in prior_vols if x]
        avgvol = sum(prior_vols) / len(prior_vols) if prior_vols else None
        rvol = v / avgvol if v and avgvol else None

        min_move = max(2.5, min(12, abs(current_move) * 0.25))
        if current_move >= 3 and day_ret is not None and day_ret < min_move:
            continue
        if current_move <= -3 and day_ret is not None and day_ret > -min_move:
            continue

        day_scale = max(5, abs(current_move) * 0.45)
        gap_scale = max(1.5, abs(current_gap) * 0.55 + 1)
        day_diff = abs((day_ret or 0) - current_move) / day_scale
        gap_diff = abs((gap or 0) - current_gap) / gap_scale
        rvol_diff = abs(math.log(max(0.15, rvol or 1) / current_rvol))
        similarity = day_diff * 0.52 + gap_diff * 0.28 + rvol_diff * 0.20

        nh, nl, nc = (_fnum(nxt.get(k)) for k in ("h", "l", "c"))
        breakout_tested = bool(nh and nh > h * 1.005)
        breakout_follow = bool(breakout_tested and nc and nc > h)
        breakout_failed = bool(breakout_tested and nc and nc < h)

        candidates.append({
            "date": date,
            "day_pct": round(day_ret, 2) if day_ret is not None else None,
            "gap_pct": round(gap, 2) if gap is not None else None,
            "close_from_open_pct": round(close_from_open, 2) if close_from_open is not None else None,
            "relative_volume": round(rvol, 2) if rvol is not None else None,
            "pattern": _label(gap, close_from_open, close_location, day_ret),
            "same_day_pullback_pct": round(_pct(l, o), 2) if l else None,
            "next_day_pct": round(_pct(nc, c), 2) if nc else None,
            "next_day_mfe_pct": round(_pct(nh, c), 2) if nh else None,
            "next_day_mae_pct": round(_pct(nl, c), 2) if nl else None,
            "breakout_tested": breakout_tested,
            "breakout_follow": breakout_follow,
            "breakout_failed": breakout_failed,
            "similarity_score": round(similarity, 3),
        })

    candidates.sort(key=lambda r: r["similarity_score"])

    # Use a larger analog sample only when the additional days are still
    # reasonably close to today's setup. Otherwise keep the sample tighter
    # instead of diluting it with weak comparisons.
    close_candidates = [
        r for r in candidates
        if r.get("similarity_score") is not None and r["similarity_score"] <= 1.35
    ]
    matches = close_candidates[:30] if len(close_candidates) >= 12 else candidates[:15]
    if len(matches) < 4:
        return {"status": "insufficient_history", "source": source, "sample_count": len(matches), "matches": matches}

    sample_quality = (
        "HIGH" if len(matches) >= 25
        else "MODERATE" if len(matches) >= 15
        else "LOW"
    )
    median_similarity = (
        round(median([r["similarity_score"] for r in matches]), 3)
        if matches else None
    )

    gap_rows = [r for r in matches if (r.get("gap_pct") or 0) >= 2]
    tested = [r for r in matches if r.get("breakout_tested")]
    next_rows = [r for r in matches if r.get("next_day_pct") is not None]

    gap_run = round(100 * sum(r.get("pattern") == "GAP & RUN" for r in gap_rows) / len(gap_rows), 1) if gap_rows else None
    gap_fade = round(100 * sum(r.get("pattern") == "GAP & FADE" for r in gap_rows) / len(gap_rows), 1) if gap_rows else None
    next_up = round(100 * sum((r.get("next_day_pct") or 0) > 0 for r in next_rows) / len(next_rows), 1) if next_rows else None
    breakout_follow = _rate(tested, "breakout_follow")
    breakout_failure = _rate(tested, "breakout_failed")

    intraday = _intraday_stats(symbol, now, [r["date"] for r in candidates[:60]], fetch_bars, et)

    setup_label = _label(current_gap, current_move - current_gap, None, current_move)
    bias = 0.0
    if next_up is not None:
        bias += (next_up - 50) * 0.20
    if breakout_follow is not None and breakout_failure is not None:
        bias += (breakout_follow - breakout_failure) * 0.09
    if gap_run is not None and gap_fade is not None:
        bias += (gap_run - gap_fade) * 0.07
    reclaim_follow = _fnum(intraday.get("vwap_reclaim_follow_through_pct")) if intraday.get("status") == "ok" else None
    if reclaim_follow is not None:
        bias += (reclaim_follow - 50) * 0.08
    bias = round(_clamp(bias, -20, 20), 1)
    bias_label = "BULLISH" if bias >= 6 else "BEARISH" if bias <= -6 else "MIXED"

    notes = []
    if gap_run is not None and gap_fade is not None:
        if gap_run >= gap_fade + 15:
            notes.append(f"Similar gap days ran more often than they faded ({gap_run:.0f}% vs {gap_fade:.0f}%).")
        elif gap_fade >= gap_run + 15:
            notes.append(f"Similar gap days faded more often than they ran ({gap_fade:.0f}% vs {gap_run:.0f}%).")
    if breakout_failure is not None and breakout_follow is not None:
        if breakout_failure >= 55:
            notes.append(f"Breakout failure risk is elevated: {breakout_failure:.0f}% of tested analog breakouts closed back below the prior high.")
        elif breakout_follow >= 60:
            notes.append(f"Breakout follow-through has been strong: {breakout_follow:.0f}% of tested analogs held above the prior high.")
    if reclaim_follow is not None:
        if reclaim_follow >= 60:
            notes.append(f"Historical VWAP reclaims followed through {reclaim_follow:.0f}% of the time in the matched intraday sample.")
        elif reclaim_follow <= 40:
            notes.append(f"Historical VWAP reclaims often failed to hold; only {reclaim_follow:.0f}% followed through.")
    if intraday.get("median_first_pullback_pct") is not None:
        notes.append(f"Median early pullback after the opening push was {intraday['median_first_pullback_pct']:.1f}%.")
    if intraday.get("median_impulse_retracement_pct") is not None:
        notes.append(
            f"Dominant impulse moves historically retraced a median {intraday['median_impulse_retracement_pct']:.0f}% "
            f"before the next bounce/continuation attempt."
        )
    if intraday.get("impulse_bounce_5pct_rate") is not None:
        notes.append(
            f"After measured impulse pullbacks, price later bounced at least 5% on "
            f"{intraday['impulse_bounce_5pct_rate']:.0f}% of sampled days."
        )
    if intraday.get("session_high_most_common"):
        notes.append(f"Matched days most often made their session high in the {intraday['session_high_most_common'].lower()} window.")

    return {
        "status": "ok",
        "source": source,
        "setup_label": setup_label,
        "bias_label": bias_label,
        "bias_score": bias,
        "sample_count": len(matches),
        "sample_quality": sample_quality,
        "median_similarity_score": median_similarity,
        "history_lookback_days": 900,
        "gap_sample_count": len(gap_rows),
        "breakout_test_count": len(tested),
        "next_day_up_pct": next_up,
        "median_next_day_pct": _median(matches, "next_day_pct"),
        "median_next_day_mfe_pct": _median(matches, "next_day_mfe_pct"),
        "median_next_day_mae_pct": _median(matches, "next_day_mae_pct"),
        "gap_run_pct": gap_run,
        "gap_fade_pct": gap_fade,
        "breakout_follow_through_pct": breakout_follow,
        "breakout_failure_pct": breakout_failure,
        "median_same_day_pullback_pct": _median(matches, "same_day_pullback_pct"),
        "median_impulse_retracement_pct": intraday.get("median_impulse_retracement_pct"),
        "median_impulse_bounce_mfe_pct": intraday.get("median_impulse_bounce_mfe_pct"),
        "impulse_bounce_5pct_rate": intraday.get("impulse_bounce_5pct_rate"),
        "intraday": intraday,
        "notes": notes,
        "matches": matches[:15],
    }
