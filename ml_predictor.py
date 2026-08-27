from __future__ import annotations

import math
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median

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


def _feature_row(day, prev_close, avg20_vol, atr_pct, idx):
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
            feat = _feature_row(day, prev_close, avg20, atr_pct, idx)
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
        "target_before_stop": 0.45,
        "higher_60": 0.25,
        "higher_30": 0.15,
        "breakout_hold": 0.15,
    }
    probs = []
    for name, weight in weights.items():
        model = models.get(name) or {}
        p = _fnum(model.get("probability_pct"))
        if p is None or model.get("status") != "ok":
            continue
        if name == "breakout_hold" and not plan.get("breakout_relevant"):
            continue
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

    key = (
        symbol.upper(),
        round(target_pct, 1),
        round(stop_pct, 1),
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
    for label in ("target_before_stop", "higher_30", "higher_60", "breakout_hold"):
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

    edge = _weighted_edge(
        models,
        {"breakout_relevant": breakout_relevant},
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
        "version": "ml-v1.1",
        "source": source,
        "training_samples": len(dataset),
        "target_pct": round(target_pct, 2),
        "stop_pct": round(stop_pct, 2),
        "breakout_relevant": breakout_relevant,
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
            "ML v1 only adjusts plan confidence when the validation gate passes; it does not override "
            "the rule-based entry/stop/target decision."
        ),
    }
    _CACHE[key] = {"stamp": stamp, "value": result}
    return result
