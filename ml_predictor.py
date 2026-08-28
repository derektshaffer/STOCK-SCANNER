from __future__ import annotations

import math
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median

from multi_bounce import bounce_feature_values, detect_bounce_sequence
from stair_step import detect_stair_step, stair_step_feature_values

FEATURES = [
    "day_pct",
    "gap_pct",
    "vwap_extension_pct",
    "momentum_5m",
    "momentum_15m",
    "momentum_30m",
    "volume_pace",
    "from_high_pct",
    "atr_pct",
    "time_fraction",
    "close_location",
    "range_pct",
    # Impulse -> pullback -> bounce structure. These are calculated using only
    # information available at each historical observation, so there is no
    # future leakage in training.
    "impulse_move_pct",
    "impulse_retracement_pct",
    "impulse_max_retracement_pct",
    "impulse_bounce_recovery_pct",
    "pullback_volume_ratio",
    # Multi-bounce sequence features shared with the live analyzer.
    "bounce_count",
    "last_bounce_pct",
    "bounce_decay_ratio",
    "bounce_volume_decay_ratio",
    "lower_high_streak",
    "higher_low_streak",
    "sequence_health_score",
    "current_pullback_pct",
    "ongoing_bounce_pct",
    "bounce_leg_code",
    "reference_peak_pct_above_dip",
    # Multi-session step -> plateau -> reacceleration context.
    "stair_step_count",
    "stair_last_step_pct",
    "stair_step_acceleration_ratio",
    "stair_plateau_days",
    "stair_plateau_range_pct",
    "stair_plateau_retention_pct",
    "stair_plateau_volume_ratio",
    "stair_higher_plateau_count",
    "stair_structure_score",
    "stair_reaccelerating",
    "stair_breakdown",
]

_CACHE = {}
_CACHE_TTL = 900


def _fnum(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _pct(a, b):
    return None if not b else (a / b - 1.0) * 100.0


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _bar_dt(bar, et):
    try:
        return datetime.fromisoformat(str(bar.get("t", "")).replace("Z", "+00:00")).astimezone(et)
    except Exception:
        return None


def _regular(bar, et):
    dt = _bar_dt(bar, et)
    if dt is None:
        return False
    minute = dt.hour * 60 + dt.minute
    return dt.weekday() < 5 and 570 <= minute < 960


def _median(values):
    vals = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    return median(vals) if vals else None


def _daily_rows(grouped_dates):
    rows = []
    for date, bars in grouped_dates:
        bars = sorted(bars, key=lambda b: str(b.get("t", "")))
        o = _fnum(bars[0].get("o")) if bars else None
        c = _fnum(bars[-1].get("c")) if bars else None
        highs = [_fnum(b.get("h")) for b in bars]
        lows = [_fnum(b.get("l")) for b in bars]
        vols = [_fnum(b.get("v")) or 0.0 for b in bars]
        highs = [x for x in highs if x is not None]
        lows = [x for x in lows if x is not None]
        if not bars or o is None or c is None or not highs or not lows:
            continue
        rows.append(
            {
                "date": date,
                "open": o,
                "high": max(highs),
                "low": min(lows),
                "close": c,
                "volume": sum(vols),
                "bars": bars,
            }
        )
    return rows


def _atr_pct(daily, i, periods=14):
    if i < 2:
        return None
    start = max(1, i - periods)
    trs = []
    for j in range(start, i):
        row = daily[j]
        prev = daily[j - 1]
        h, l, pc = row["high"], row["low"], prev["close"]
        if h and l and pc:
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs) / len(trs) if trs else None
    pc = daily[i - 1]["close"] if i > 0 else None
    return atr / pc * 100.0 if atr and pc else None


def _impulse_features(bars, idx):
    """Impulse/retracement features using bars no later than idx."""
    if idx < 6:
        return {
            "impulse_move_pct": None,
            "impulse_retracement_pct": None,
            "impulse_max_retracement_pct": None,
            "impulse_bounce_recovery_pct": None,
            "pullback_volume_ratio": None,
        }
    rows=[]
    for b in bars[:idx+1]:
        h=_fnum(b.get("h")); l=_fnum(b.get("l")); cc=_fnum(b.get("c")); v=_fnum(b.get("v")) or 0.0
        if h is None or l is None or cc is None or h<=0 or l<=0:
            continue
        rows.append({"h":h,"l":l,"c":cc,"v":v})
    if len(rows)<7:
        return {
            "impulse_move_pct": None,
            "impulse_retracement_pct": None,
            "impulse_max_retracement_pct": None,
            "impulse_bounce_recovery_pct": None,
            "pullback_volume_ratio": None,
        }

    current=rows[-1]["c"]
    candidates=[]
    n=len(rows)
    for peak_idx in range(4,n-1):
        start=max(0,peak_idx-24)
        low_idx=min(range(start,peak_idx),key=lambda i:rows[i]["l"])
        low=rows[low_idx]["l"]; peak=rows[peak_idx]["h"]
        if peak<=low:
            continue
        move=(peak/low-1)*100.0
        if move<6:
            continue
        after=rows[peak_idx+1:]
        if not after:
            continue
        trough_rel=min(range(len(after)),key=lambda i:after[i]["l"])
        trough_idx=peak_idx+1+trough_rel
        trough=rows[trough_idx]["l"]
        run=peak-low
        max_retrace=(peak-trough)/run*100.0
        current_retrace=(peak-current)/run*100.0
        recovery=max_retrace-current_retrace
        age=n-1-peak_idx
        recency=max(.35,1.0-age/max(12.0,n*.8))
        score=move*recency*(1.0 if 15<=max_retrace<=75 else .75)
        candidates.append((score,low_idx,peak_idx,trough_idx,move,max_retrace,current_retrace,recovery))

    if not candidates:
        return {
            "impulse_move_pct": None,
            "impulse_retracement_pct": None,
            "impulse_max_retracement_pct": None,
            "impulse_bounce_recovery_pct": None,
            "pullback_volume_ratio": None,
        }

    _,low_idx,peak_idx,trough_idx,move,max_retrace,current_retrace,recovery=max(candidates,key=lambda x:x[0])
    iv=[rows[i]["v"] for i in range(low_idx,peak_idx+1) if rows[i]["v"]>0]
    pv=[rows[i]["v"] for i in range(peak_idx+1,trough_idx+1) if rows[i]["v"]>0]
    iva=sum(iv)/len(iv) if iv else None
    pva=sum(pv)/len(pv) if pv else None
    volume_ratio=pva/iva if iva and pva is not None else None

    return {
        "impulse_move_pct": move,
        "impulse_retracement_pct": current_retrace,
        "impulse_max_retracement_pct": max_retrace,
        "impulse_bounce_recovery_pct": recovery,
        "pullback_volume_ratio": volume_ratio,
    }


def _feature_row(day, prev_close, avg20_vol, atr_pct, idx, prior_daily=None):
    bars = day["bars"]
    if idx < 6 or idx >= len(bars):
        return None

    current = bars[idx]
    price = _fnum(current.get("c"))
    if not price or not prev_close:
        return None

    upto = bars[: idx + 1]
    highs = [_fnum(b.get("h")) for b in upto]
    lows = [_fnum(b.get("l")) for b in upto]
    highs = [x for x in highs if x is not None]
    lows = [x for x in lows if x is not None]
    if not highs or not lows:
        return None

    pv = 0.0
    volume = 0.0
    for b in upto:
        v = _fnum(b.get("v")) or 0.0
        h, l, c = (_fnum(b.get(k)) for k in ("h", "l", "c"))
        if h is None or l is None or c is None:
            continue
        pv += ((h + l + c) / 3.0) * v
        volume += v
    vwap = pv / volume if volume else None

    def momentum(back):
        if idx - back < 0:
            return None
        old = _fnum(bars[idx - back].get("c"))
        return _pct(price, old) if old else None

    session_high = max(highs)
    session_low = min(lows)
    day_open = day["open"]
    time_fraction = _clamp((idx + 1) / 78.0, 1.0 / 78.0, 1.0)
    expected = avg20_vol * time_fraction if avg20_vol else None
    volume_pace = volume / expected if expected else None
    rng = session_high - session_low
    close_location = (price - session_low) / rng if rng > 0 else 0.5
    range_pct = rng / price * 100.0 if price else None
    impulse = _impulse_features(bars, idx)
    sequence = detect_bounce_sequence(
        upto,
        current_price=price,
        atr_pct=atr_pct,
    )
    bounce_features = bounce_feature_values(sequence)
    prior_daily = prior_daily or []
    stair = detect_stair_step(
        prior_daily,
        current_day={
            "date": day.get("date"),
            "o": day_open,
            "h": session_high,
            "l": session_low,
            "c": price,
            "v": volume,
        },
        atr_pct=atr_pct,
    )
    stair_features = stair_step_feature_values(stair)

    return {
        "day_pct": _pct(price, prev_close),
        "gap_pct": _pct(day_open, prev_close),
        "vwap_extension_pct": _pct(price, vwap) if vwap else None,
        "momentum_5m": momentum(1),
        "momentum_15m": momentum(3),
        "momentum_30m": momentum(6),
        "volume_pace": volume_pace,
        "from_high_pct": (session_high - price) / session_high * 100.0 if session_high else None,
        "atr_pct": atr_pct,
        "time_fraction": time_fraction,
        "close_location": close_location,
        "range_pct": range_pct,
        **impulse,
        **bounce_features,
        **stair_features,
        "_price": price,
        "_idx": idx,
        "_session_high": session_high,
    }


def _first_touch_outcome(future_bars, price, target_pct, stop_pct):
    """Return the first decisive same-session target/stop outcome.

    A bar that touches both levels is ambiguous because 5-minute OHLC data
    cannot reveal which level traded first. If neither level is touched before
    the session ends, the observation is unresolved rather than a loss.
    """
    target = price * (1.0 + target_pct / 100.0)
    stop = price * (1.0 + stop_pct / 100.0)
    for bar in future_bars:
        h = _fnum(bar.get("h"))
        l = _fnum(bar.get("l"))
        hit_target = h is not None and h >= target
        hit_stop = l is not None and l <= stop
        if hit_target and hit_stop:
            return "ambiguous"
        if hit_target:
            return "target"
        if hit_stop:
            return "stop"
    return "unresolved"


def _build_dataset(bars5, et, target_pct, stop_pct):
    grouped = defaultdict(list)
    for bar in bars5:
        if not _regular(bar, et):
            continue
        dt = _bar_dt(bar, et)
        if dt is not None:
            grouped[dt.date().isoformat()].append(bar)

    daily = _daily_rows(sorted(grouped.items()))
    samples = []
    for i in range(20, len(daily)):
        day = daily[i]
        bars = day["bars"]
        if len(bars) < 20:
            continue
        prev_close = daily[i - 1]["close"]
        vols = [x["volume"] for x in daily[max(0, i - 20):i] if x.get("volume")]
        avg20 = sum(vols) / len(vols) if vols else None
        atr_pct = _atr_pct(daily, i)

        # Sample every 15 minutes after the first 30 minutes. Keep a full
        # 60-minute future window for the 30m/60m continuation labels; the
        # Target 1 first-touch label separately uses the rest of the session.
        for idx in range(6, len(bars) - 12, 3):
            prior_daily_bars=[
                {
                    "t": d.get("date"),
                    "o": d.get("open"),
                    "h": d.get("high"),
                    "l": d.get("low"),
                    "c": d.get("close"),
                    "v": d.get("volume"),
                }
                for d in daily[max(0, i-20):i]
            ]
            feat = _feature_row(
                day,
                prev_close,
                avg20,
                atr_pct,
                idx,
                prior_daily=prior_daily_bars,
            )
            if not feat:
                continue
            price = feat["_price"]
            future30 = bars[idx + 1: idx + 7]
            future60 = bars[idx + 1: idx + 13]
            if len(future30) < 6 or len(future60) < 12:
                continue
            c30 = _fnum(future30[-1].get("c"))
            c60 = _fnum(future60[-1].get("c"))

            prior_window = bars[max(0, idx - 6):idx]
            prior_highs = [_fnum(b.get("h")) for b in prior_window]
            prior_highs = [x for x in prior_highs if x is not None]
            breakout_level = max(prior_highs) if prior_highs else None
            current_high = _fnum(bars[idx].get("h"))
            breakout_like = bool(
                breakout_level
                and current_high
                and (current_high >= breakout_level * 0.999 or price >= breakout_level * 0.995)
            )
            breakout_hold = None
            if breakout_like and breakout_level:
                min_future = min(
                    [x for x in (_fnum(b.get("l")) for b in future30) if x is not None],
                    default=None,
                )
                breakout_hold = int(
                    bool(c30 and c30 > breakout_level and (min_future is None or min_future >= breakout_level * 0.985))
                )

            # Reversal risk asks whether a meaningful downside move occurs
            # BEFORE a smaller continuation push during the next 30 minutes.
            # Thresholds adapt to the stock's own ATR so a 3% drop is not treated
            # the same on a 2% ATR stock and a 20% ATR stock.
            atr_here=_fnum(feat.get("atr_pct")) or 6.0
            reversal_down=_clamp(atr_here*0.60,2.5,8.0)
            reversal_up=_clamp(atr_here*0.42,2.0,6.0)
            reversal_30=None
            for fb in future30:
                fh=_fnum(fb.get("h")); fl=_fnum(fb.get("l"))
                hit_down=bool(fl is not None and fl <= price*(1.0-reversal_down/100.0))
                hit_up=bool(fh is not None and fh >= price*(1.0+reversal_up/100.0))
                if hit_down and hit_up:
                    reversal_30=None
                    break
                if hit_down:
                    reversal_30=1
                    break
                if hit_up:
                    reversal_30=0
                    break

            # Later-bounce question: after at least one completed bounce and
            # while price is pulling back / basing, does another quick rebound
            # occur before the dip breaks down? Ambiguous same-bar touches are
            # censored because 5-minute OHLC cannot reveal which traded first.
            bounce_count=int(_fnum(feat.get("bounce_count")) or 0)
            bounce_leg=_fnum(feat.get("bounce_leg_code")) or 0.0
            repeat_bounce_30=None
            bounce_up=_clamp(atr_here*0.48,2.0,7.0)
            bounce_down=_clamp(atr_here*0.48,2.0,7.0)
            bounce_eligible=bool(bounce_count>=1 and bounce_leg<=0.0)
            if bounce_eligible:
                for fb in future30:
                    fh=_fnum(fb.get("h")); fl=_fnum(fb.get("l"))
                    hit_up=bool(fh is not None and fh >= price*(1.0+bounce_up/100.0))
                    hit_down=bool(fl is not None and fl <= price*(1.0-bounce_down/100.0))
                    if hit_up and hit_down:
                        repeat_bounce_30=None
                        break
                    if hit_up:
                        repeat_bounce_30=1
                        break
                    if hit_down:
                        repeat_bounce_30=0
                        break

            # After a mature sequence, distinguish a simple scalp bounce from a
            # genuine re-expansion to a fresh session high.
            new_high_60=None
            prior_session_high=_fnum(feat.get("_session_high"))
            new_high_eligible=bool(
                bounce_count>=1
                and prior_session_high
                and price < prior_session_high*0.997
            )
            if new_high_eligible:
                new_high_level=prior_session_high*1.002
                failure_level=price*(1.0-bounce_down/100.0)
                for fb in future60:
                    fh=_fnum(fb.get("h")); fl=_fnum(fb.get("l"))
                    hit_high=bool(fh is not None and fh >= new_high_level)
                    hit_fail=bool(fl is not None and fl <= failure_level)
                    if hit_high and hit_fail:
                        new_high_60=None
                        break
                    if hit_high:
                        new_high_60=1
                        break
                    if hit_fail:
                        new_high_60=0
                        break

            # Mature-bounce failure: after two or more completed bounces,
            # does a meaningful downside break happen before another rescue push?
            post_bounce_failure_60=None
            failure_down=_clamp(atr_here*0.72,4.0,12.0)
            failure_rescue=_clamp(atr_here*0.42,2.5,7.0)
            mature_bounce=bool(bounce_count>=2)
            if mature_bounce:
                for fb in future60:
                    fh=_fnum(fb.get("h")); fl=_fnum(fb.get("l"))
                    hit_fail=bool(fl is not None and fl <= price*(1.0-failure_down/100.0))
                    hit_rescue=bool(fh is not None and fh >= price*(1.0+failure_rescue/100.0))
                    if hit_fail and hit_rescue:
                        post_bounce_failure_60=None
                        break
                    if hit_fail:
                        post_bounce_failure_60=1
                        break
                    if hit_rescue:
                        post_bounce_failure_60=0
                        break

            # Stair-step reacceleration: when a multi-session step/plateau
            # structure exists, does the next expansion threshold hit before a
            # meaningful loss of the accepted higher level?
            stair_count=int(_fnum(feat.get("stair_step_count")) or 0)
            stair_reacceleration_60=None
            stair_up=_clamp(atr_here*0.50,3.0,9.0)
            stair_down=_clamp(atr_here*0.40,2.5,7.0)
            stair_eligible=bool(stair_count>=1 and not bool(_fnum(feat.get("stair_breakdown"))))
            if stair_eligible:
                for fb in future60:
                    fh=_fnum(fb.get("h")); fl=_fnum(fb.get("l"))
                    hit_up=bool(fh is not None and fh >= price*(1.0+stair_up/100.0))
                    hit_down=bool(fl is not None and fl <= price*(1.0-stair_down/100.0))
                    if hit_up and hit_down:
                        stair_reacceleration_60=None
                        break
                    if hit_up:
                        stair_reacceleration_60=1
                        break
                    if hit_down:
                        stair_reacceleration_60=0
                        break

            # Target 1 is a day-trade first-touch question, not a 60-minute
            # continuation question. Evaluate it through the rest of this same
            # session. Timeouts are censored instead of being mislabeled losses.
            future_session = bars[idx + 1:]
            target_outcome = _first_touch_outcome(
                future_session, price, target_pct, stop_pct
            )
            target_label = (
                1 if target_outcome == "target"
                else 0 if target_outcome == "stop"
                else None
            )

            row = {k: feat.get(k) for k in FEATURES}
            dt = _bar_dt(bars[idx], et)
            row.update(
                {
                    "timestamp": dt.astimezone(timezone.utc).timestamp() if dt else 0.0,
                    "higher_30": int(bool(c30 and c30 > price)),
                    "higher_60": int(bool(c60 and c60 > price)),
                    "target_before_stop": target_label,
                    "target_before_stop_outcome": target_outcome,
                    "breakout_hold": breakout_hold,
                    "reversal_30": reversal_30,
                    "repeat_bounce_30": repeat_bounce_30,
                    "new_high_60": new_high_60,
                    "post_bounce_failure_60": post_bounce_failure_60,
                    "stair_reacceleration_60": stair_reacceleration_60,
                    "reversal_down_pct": round(reversal_down,2),
                    "failure_down_pct": round(failure_down,2),
                    "failure_rescue_pct": round(failure_rescue,2),
                    "stair_up_pct": round(stair_up,2),
                    "stair_down_pct": round(stair_down,2),
                    "reversal_up_pct": round(reversal_up,2),
                    "bounce_up_pct": round(bounce_up,2),
                    "bounce_down_pct": round(bounce_down,2),
                }
            )
            samples.append(row)

    samples.sort(key=lambda r: r["timestamp"])
    return samples


def _matrix(rows, label, np, xgb):
    clean = [r for r in rows if r.get(label) is not None]
    X = np.array(
        [[np.nan if r.get(k) is None else float(r.get(k)) for k in FEATURES] for r in clean],
        dtype=float,
    )
    y = np.array([int(r[label]) for r in clean], dtype=float)
    return clean, X, y


def _model_params():
    return {
        "objective": "binary:logistic",
        "eval_metric": "logloss",
        "max_depth": 3,
        "eta": 0.055,
        "subsample": 0.82,
        "colsample_bytree": 0.82,
        "min_child_weight": 5,
        "lambda": 2.0,
        "alpha": 0.15,
        "seed": 42,
        "nthread": 2,
    }


def _walk_forward_fit(rows, label, current_features):
    try:
        import numpy as np
        import xgboost as xgb
    except Exception as exc:
        return {
            "status": "dependency_missing",
            "label": label,
            "error": f"XGBoost is not available: {exc}",
        }

    clean, X, y = _matrix(rows, label, np, xgb)
    n = len(clean)
    positives = int(y.sum()) if n else 0
    if n < 180 or positives < 25 or (n - positives) < 25:
        return {
            "status": "insufficient_samples",
            "label": label,
            "samples": n,
            "positives": positives,
            "negatives": max(0, n - positives),
        }

    val_probs = []
    val_y = []
    baseline_preds = []
    cut_fracs = (0.55, 0.70, 0.85)
    for fold, train_frac in enumerate(cut_fracs):
        train_end = max(80, int(n * train_frac))
        val_end = int(n * (cut_fracs[fold + 1] if fold + 1 < len(cut_fracs) else 1.0))
        if val_end <= train_end + 15:
            continue
        Xtr, ytr = X[:train_end], y[:train_end]
        Xv, yv = X[train_end:val_end], y[train_end:val_end]
        if len(set(ytr.tolist())) < 2 or len(set(yv.tolist())) < 2:
            continue
        model = xgb.train(
            _model_params(),
            xgb.DMatrix(Xtr, label=ytr, feature_names=FEATURES),
            num_boost_round=110,
            verbose_eval=False,
        )
        probs = model.predict(xgb.DMatrix(Xv, feature_names=FEATURES))
        train_base = 1 if float(ytr.mean()) >= 0.5 else 0
        val_probs.extend(float(p) for p in probs)
        val_y.extend(int(v) for v in yv)
        baseline_preds.extend([train_base] * len(yv))

    if len(val_y) < 60:
        return {
            "status": "insufficient_validation",
            "label": label,
            "samples": n,
            "validation_samples": len(val_y),
        }

    correct = sum((p >= 0.5) == bool(y) for p, y in zip(val_probs, val_y))
    baseline_correct = sum(bool(p) == bool(y) for p, y in zip(baseline_preds, val_y))
    accuracy = correct / len(val_y)
    baseline_accuracy = baseline_correct / len(val_y)
    brier = sum((p - y) ** 2 for p, y in zip(val_probs, val_y)) / len(val_y)
    edge = accuracy - baseline_accuracy

    final_model = xgb.train(
        _model_params(),
        xgb.DMatrix(X, label=y, feature_names=FEATURES),
        num_boost_round=130,
        verbose_eval=False,
    )
    current = np.array(
        [[np.nan if current_features.get(k) is None else float(current_features.get(k)) for k in FEATURES]],
        dtype=float,
    )
    probability = float(
        final_model.predict(xgb.DMatrix(current, feature_names=FEATURES))[0]
    )

    importance_raw = final_model.get_score(importance_type="gain")
    important = sorted(
        ((k, float(v)) for k, v in importance_raw.items()),
        key=lambda kv: kv[1],
        reverse=True,
    )[:5]
    total = sum(v for _, v in important) or 1.0
    importance = [
        {"feature": k, "share_pct": round(v / total * 100.0, 1)}
        for k, v in important
    ]

    validated = (
        len(val_y) >= 60
        and edge >= 0.02
        and brier <= 0.25
        and accuracy >= 0.52
    )

    return {
        "status": "ok",
        "label": label,
        "probability_pct": round(probability * 100.0, 1),
        "samples": n,
        "positives": positives,
        "negatives": max(0, n - positives),
        "validation_samples": len(val_y),
        "walk_forward_accuracy_pct": round(accuracy * 100.0, 1),
        "baseline_accuracy_pct": round(baseline_accuracy * 100.0, 1),
        "accuracy_edge_pct": round(edge * 100.0, 1),
        "brier": round(brier, 3),
        "validated": bool(validated),
        "top_features": importance,
    }


def _current_features(metrics, now_et):
    price = _fnum(metrics.get("price"))
    high = _fnum(metrics.get("day_high"))
    low = _fnum(metrics.get("day_low"))
    range_pct = ((high - low) / price * 100.0) if price and high and low and high >= low else None
    close_location = (
        (price - low) / (high - low)
        if price is not None and high is not None and low is not None and high > low
        else 0.5
    )
    minute = now_et.hour * 60 + now_et.minute
    time_fraction = _clamp((minute - 570) / 390.0, 1.0 / 78.0, 1.0)

    impulse = metrics.get("impulse_pullback") or {}
    bounce_features = bounce_feature_values(metrics.get("bounce_sequence") or {})
    stair_features = stair_step_feature_values(metrics.get("stair_step") or {})

    return {
        "day_pct": _fnum(metrics.get("day_pct")),
        "gap_pct": _fnum(metrics.get("gap_pct")),
        "vwap_extension_pct": _fnum(metrics.get("vwap_extension_pct")),
        "momentum_5m": _fnum(metrics.get("momentum_5m")),
        "momentum_15m": _fnum(metrics.get("momentum_15m")),
        "momentum_30m": _fnum(metrics.get("momentum_30m")),
        "volume_pace": _fnum(metrics.get("volume_pace")),
        "from_high_pct": _fnum(metrics.get("from_high_pct")),
        "atr_pct": _fnum(metrics.get("atr_14_pct")),
        "time_fraction": time_fraction,
        "close_location": close_location,
        "range_pct": range_pct,
        "impulse_move_pct": _fnum(impulse.get("impulse_move_pct")),
        "impulse_retracement_pct": _fnum(impulse.get("current_retracement_pct")),
        "impulse_max_retracement_pct": _fnum(impulse.get("max_retracement_pct")),
        "impulse_bounce_recovery_pct": _fnum(impulse.get("bounce_recovery_pct")),
        "pullback_volume_ratio": _fnum(impulse.get("pullback_volume_ratio")),
        **bounce_features,
        **stair_features,
    }


def _plan_geometry(metrics):
    plan = metrics.get("trade_plan") or {}
    selected = plan.get("selected") or {}
    entry = _fnum(selected.get("entry_mid"))
    target = _fnum(selected.get("target1"))
    stop = _fnum(selected.get("stop"))
    if not entry or not target or not stop or target <= entry or stop >= entry:
        return None, None
    target_pct = _pct(target, entry)
    stop_pct = _pct(stop, entry)
    # Extremely wide/narrow plans make the target-before-stop label meaningless.
    if target_pct is None or stop_pct is None:
        return None, None
    target_pct = _clamp(target_pct, 0.75, 20.0)
    stop_pct = _clamp(stop_pct, -20.0, -0.75)
    return target_pct, stop_pct


def _weighted_edge(models, plan):
    weights = {
        "target_before_stop": 0.24,
        "higher_60": 0.14,
        "higher_30": 0.08,
        "breakout_hold": 0.07,
        "reversal_30": 0.11,
        "repeat_bounce_30": 0.11,
        "new_high_60": 0.07,
        "post_bounce_failure_60": 0.10,
        "stair_reacceleration_60": 0.08,
    }
    probs = []
    for name, weight in weights.items():
        model = models.get(name) or {}
        p = _fnum(model.get("probability_pct"))
        if p is None or model.get("status") != "ok":
            continue
        if name == "breakout_hold" and not plan.get("breakout_relevant"):
            continue
        if name in {"repeat_bounce_30", "new_high_60"} and not plan.get("bounce_relevant"):
            continue
        if name == "post_bounce_failure_60" and not plan.get("mature_bounce_relevant"):
            continue
        if name == "stair_reacceleration_60" and not plan.get("stair_relevant"):
            continue
        if name in {"reversal_30", "post_bounce_failure_60"}:
            p = 100.0 - p
        probs.append((p, weight))
    if not probs:
        return None
    total_w = sum(w for _, w in probs)
    return sum(p * w for p, w in probs) / total_w


def predict_ml(symbol, now, metrics, fetch_bars, et):
    """Train same-ticker XGBoost models and return live probabilities.

    Validation is expanding-window / walk-forward. Models are advisory unless
    the target-before-stop model and at least one continuation model beat their
    naive baselines on unseen chronological validation samples.
    """
    target_pct, stop_pct = _plan_geometry(metrics)
    if target_pct is None or stop_pct is None:
        target_pct, stop_pct = 5.0, -4.0

    # Sequence-regime probabilities need to react to a new 5-minute bar.
    # The older cache could stay unchanged for 15 minutes during a fast
    # Bounce #2/#3 or plateau breakout, which is too stale for this use case.
    five_minute_bucket = int(now.timestamp() // 300)
    key = (
        symbol.upper(),
        round(target_pct, 1),
        round(stop_pct, 1),
        five_minute_bucket,
    )
    stamp = time.time()
    cached = _CACHE.get(key)
    if cached and stamp - cached["stamp"] < _CACHE_TTL:
        out = dict(cached["value"])
        out["cached"] = True
        return out

    try:
        bars5, source = fetch_bars(
            symbol,
            "5Min",
            now - timedelta(days=95),
            now,
            10000,
        )
    except Exception as exc:
        return {
            "status": "unavailable",
            "error": str(exc)[:180],
            "models": {},
        }

    if len(bars5) < 700:
        return {
            "status": "insufficient_history",
            "source": source,
            "bar_count": len(bars5),
            "models": {},
        }

    dataset = _build_dataset(bars5, et, target_pct, stop_pct)
    now_et = now.astimezone(et)
    current = _current_features(metrics, now_et)

    models = {}
    for label in (
        "target_before_stop",
        "higher_30",
        "higher_60",
        "breakout_hold",
        "reversal_30",
        "repeat_bounce_30",
        "new_high_60",
        "post_bounce_failure_60",
        "stair_reacceleration_60",
    ):
        models[label] = _walk_forward_fit(dataset, label, current)

    target_outcomes = {
        "target_wins": sum(row.get("target_before_stop_outcome") == "target" for row in dataset),
        "stop_first": sum(row.get("target_before_stop_outcome") == "stop" for row in dataset),
        "unresolved": sum(row.get("target_before_stop_outcome") == "unresolved" for row in dataset),
        "ambiguous": sum(row.get("target_before_stop_outcome") == "ambiguous" for row in dataset),
    }

    plan = metrics.get("trade_plan") or {}
    selected = plan.get("selected") or {}
    target_model = models.get("target_before_stop") or {}
    target_model["horizon"] = "same_session"
    target_model["target_source"] = selected.get("target1_reason") or "Target 1"
    target_model["outcome_summary"] = target_outcomes
    models["target_before_stop"] = target_model

    reversal_model=models.get("reversal_30") or {}
    reversal_model["horizon"]="30 minutes"
    reversal_model["meaning"]="Probability downside reversal threshold is hit before a smaller continuation threshold"
    reversal_model["direction"]="higher probability = higher reversal risk"
    models["reversal_30"]=reversal_model

    bounce_model=models.get("repeat_bounce_30") or {}
    bounce_model["horizon"]="30 minutes"
    bounce_model["meaning"]="After at least one prior bounce, probability another rebound threshold is hit before an equal-sized breakdown threshold"
    bounce_model["direction"]="higher probability = stronger repeat-bounce evidence"
    models["repeat_bounce_30"]=bounce_model

    new_high_model=models.get("new_high_60") or {}
    new_high_model["horizon"]="60 minutes"
    new_high_model["meaning"]="After at least one prior bounce, probability price reaches a fresh session high before an adaptive breakdown threshold"
    new_high_model["direction"]="higher probability = stronger re-expansion / new-high evidence"
    models["new_high_60"]=new_high_model

    failure_model=models.get("post_bounce_failure_60") or {}
    failure_model["horizon"]="60 minutes"
    failure_model["meaning"]="After at least two completed bounces, probability a material downside break occurs before a smaller rescue push"
    failure_model["direction"]="higher probability = higher post-bounce failure risk"
    models["post_bounce_failure_60"]=failure_model

    stair_model=models.get("stair_reacceleration_60") or {}
    stair_model["horizon"]="60 minutes"
    stair_model["meaning"]="When a multi-session step/plateau structure exists, probability another expansion leg hits before the higher level breaks down"
    stair_model["direction"]="higher probability = stronger stair-step reacceleration evidence"
    models["stair_reacceleration_60"]=stair_model

    breakout = plan.get("breakout") or {}
    breakout_level = _fnum(breakout.get("breakout_level"))
    price = _fnum(metrics.get("price"))
    breakout_relevant = bool(
        breakout_level
        and price
        and (price >= breakout_level * 0.96)
    )

    target_valid = bool((models.get("target_before_stop") or {}).get("validated"))
    continuation_valid = any(
        bool((models.get(name) or {}).get("validated"))
        for name in ("higher_30", "higher_60")
    )
    gate_passed = target_valid and continuation_valid

    bounce_sequence=metrics.get("bounce_sequence") or {}
    bounce_count=int(bounce_sequence.get("completed_bounces") or 0)
    bounce_relevant=bool(bounce_count>=1)
    mature_bounce_relevant=bool(bounce_count>=2)
    stair_context=metrics.get("stair_step") or {}
    stair_relevant=bool(stair_context.get("detected") and not stair_context.get("breakdown"))
    edge = _weighted_edge(
        models,
        {
            "breakout_relevant": breakout_relevant,
            "bounce_relevant": bounce_relevant,
            "mature_bounce_relevant": mature_bounce_relevant,
            "stair_relevant": stair_relevant,
        },
    )
    if edge is None:
        lean = "UNAVAILABLE"
    elif edge >= 65:
        lean = "BULLISH / SUPPORTS ENTRY"
    elif edge <= 45:
        lean = "BEARISH / CAUTION"
    else:
        lean = "MIXED"

    validated_count = sum(
        1 for m in models.values() if m.get("status") == "ok" and m.get("validated")
    )

    result = {
        "status": "ok",
        "model_type": "XGBoost",
        "version": "ml-v1.6-sequence-regimes",
        "source": source,
        "training_samples": len(dataset),
        "target_pct": round(target_pct, 2),
        "stop_pct": round(stop_pct, 2),
        "breakout_relevant": breakout_relevant,
        "bounce_relevant": bounce_relevant,
        "mature_bounce_relevant": mature_bounce_relevant,
        "stair_relevant": stair_relevant,
        "models": models,
        "validated_models": validated_count,
        "validation_gate": "PASSED" if gate_passed else "ADVISORY ONLY",
        "gate_passed": gate_passed,
        "ml_edge_score": round(edge, 1) if edge is not None else None,
        "ml_lean": lean,
        "current_features": current,
        "cached": False,
        "note": (
            "Walk-forward validation uses older observations to predict later unseen observations. "
            "Target 1 uses same-session first-touch outcomes; sessions where neither target nor stop is touched are excluded. "
            "Impulse size, retracement depth, bounce recovery and pullback-volume behavior are included as leakage-safe features. "
            "A separate 30-minute reversal model estimates whether downside is hit before renewed continuation. "
            "Later-bounce models separately estimate repeat-bounce-before-breakdown, fresh-high-before-breakdown, and mature-sequence failure. "
            "A multi-session stair-step model separately estimates plateau reacceleration versus loss of the accepted higher level. "
            "ML only adjusts plan confidence when the validation gate passes; it does not override "
            "the rule-based entry/stop/target decision."
        ),
    }
    _CACHE[key] = {"stamp": stamp, "value": result}
    return result
