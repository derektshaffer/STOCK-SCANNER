"""Position-aware exit planning for stocks the user already owns.

This module is intentionally separate from the entry/trade-plan engine. It
consumes Analyzer metrics plus a user-supplied cost basis and returns a compact
exit-management plan without changing Scanner ranking or entry calibration.
"""


def _num(value):
    try:
        return float(value)
    except Exception:
        return None


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def merge_live_position_metrics(metrics, overlay):
    """Apply a same-symbol live overlay and recompute price-derived fields."""
    out = dict(metrics or {})
    overlay = overlay or {}
    overlay_received = bool(overlay)
    out["position_live_overlay_received"] = overlay_received

    for key in (
        "price",
        "bid",
        "ask",
        "spread_pct",
        "vwap",
        "session_volume",
        "day_high",
        "day_low",
    ):
        if overlay.get(key) is not None:
            out[key] = overlay.get(key)

    # Freshness fields must come from this overlay attempt. Never preserve a
    # frozen "2 seconds old" age from the earlier deep Analyzer result after a
    # live overlay failure or a provider response with no timestamp evidence.
    for key in ("trade_age_seconds", "quote_age_seconds"):
        out[key] = overlay.get(key) if overlay_received else None

    price = _num(out.get("price"))
    vwap = _num(out.get("vwap"))
    prev_close = _num(out.get("prev_close"))
    day_high = _num(out.get("day_high"))

    if price is not None and vwap is not None and vwap > 0:
        out["vwap_position"] = "ABOVE" if price >= vwap else "BELOW"
        out["vwap_extension_pct"] = round((price / vwap - 1.0) * 100.0, 3)

    if price is not None and prev_close is not None and prev_close > 0:
        out["day_pct"] = round((price / prev_close - 1.0) * 100.0, 3)

    if price is not None and day_high is not None and day_high > 0:
        out["from_high_pct"] = round((day_high - price) / day_high * 100.0, 3)

    provider = overlay.get("provider")
    if provider:
        out["position_live_provider"] = provider
    status = overlay.get("status")
    out["position_live_status"] = status or ("unavailable" if not overlay_received else "unknown")
    return out


def _position_live_integrity(metrics, max_age_seconds=120.0):
    """Fail closed before issuing live HOLD/EXIT/REDUCE position advice."""
    metrics = metrics or {}
    reasons = []
    overlay_received = metrics.get("position_live_overlay_received") is True
    trade_age = _num(metrics.get("trade_age_seconds"))
    quote_age = _num(metrics.get("quote_age_seconds"))

    if not overlay_received:
        reasons.append("live position overlay is unavailable")
    if trade_age is None:
        reasons.append("latest trade freshness is unknown")
    elif trade_age > max_age_seconds:
        reasons.append(f"latest trade is stale ({trade_age:.0f}s old)")
    if quote_age is None:
        reasons.append("latest quote freshness is unknown")
    elif quote_age > max_age_seconds:
        reasons.append(f"latest quote is stale ({quote_age:.0f}s old)")

    return {
        "ok": not reasons,
        "overlay_received": overlay_received,
        "trade_age_seconds": trade_age,
        "quote_age_seconds": quote_age,
        "max_age_seconds": float(max_age_seconds),
        "provider": metrics.get("position_live_provider"),
        "status": metrics.get("position_live_status"),
        "reasons": reasons,
    }


def _level_price(level):
    if not isinstance(level, dict):
        return None
    return _num(level.get("price"))


def _candidate_levels(metrics, price):
    resistances = []
    for level in metrics.get("resistances") or []:
        value = _level_price(level)
        if value is not None and value > price * 1.002:
            resistances.append((value, level.get("quality") or "resistance"))

    day_high = _num(metrics.get("day_high"))
    if day_high is not None and day_high > price * 1.002:
        resistances.append((day_high, "day high"))

    plan = metrics.get("trade_plan") or {}
    selected = plan.get("selected") or {}
    for key, label in (
        ("target1", "trade-plan Target 1"),
        ("target2", "trade-plan Target 2"),
        ("stretch_target", "trade-plan stretch"),
    ):
        value = _num(selected.get(key))
        if value is not None and value > price * 1.002:
            resistances.append((value, label))

    deduped = {}
    for value, label in resistances:
        key = round(value, 3)
        if key not in deduped:
            deduped[key] = (value, label)
    return sorted(deduped.values(), key=lambda item: item[0])


def _support_level(metrics, price):
    candidates = []
    for level in metrics.get("supports") or []:
        value = _level_price(level)
        if value is None or value >= price:
            continue
        quality_score = _num(level.get("quality_score")) or 0.0
        quality = str(level.get("quality") or "support")
        distance_pct = (price / value - 1.0) * 100.0
        # Prefer nearby, better-supported levels without forcing a distant
        # historical support to become the active exit.
        rank = quality_score - distance_pct * 2.0
        candidates.append((rank, value, quality))
    if not candidates:
        return None, None
    _, value, quality = max(candidates, key=lambda item: item[0])
    return value, quality


def _post_exit_rebound_watch(metrics, price, atr_pct, vwap, m5, m15, from_high):
    """Describe rebound potential after a sharp breakdown without weakening exits.

    This is intentionally a watch/re-entry context, not permission to ignore a
    stop. A broken long thesis can still produce a violent reflex bounce.
    """
    sequence = metrics.get("bounce_sequence") or {}
    impulse = metrics.get("impulse_pullback") or {}
    exhaustion = metrics.get("run_exhaustion") or {}
    day_low = _num(metrics.get("day_low"))

    vwap_below_pct = None
    if vwap is not None and vwap > 0 and price < vwap:
        vwap_below_pct = (vwap - price) / vwap * 100.0

    off_low_pct = None
    if day_low is not None and day_low > 0 and price >= day_low:
        off_low_pct = (price / day_low - 1.0) * 100.0

    observed_bounces = int(
        sequence.get("observed_bounces")
        or sequence.get("completed_bounces")
        or 0
    )
    developing = bool(sequence.get("developing_bounce"))
    bounce_pct = _num(sequence.get("developing_bounce_pct"))
    recovery_pct = _num(impulse.get("bounce_recovery_pct"))
    retracement = _num(impulse.get("current_retracement_pct"))
    exhaustion_score = _num(exhaustion.get("score"))

    severity = 0.0
    reasons = []
    if from_high is not None:
        if from_high >= 35:
            severity += 28
            reasons.append(f"price is {from_high:.1f}% below the session high")
        elif from_high >= 20:
            severity += 18
            reasons.append(f"price is {from_high:.1f}% below the session high")
    if vwap_below_pct is not None:
        if vwap_below_pct >= max(12.0, atr_pct * 1.5):
            severity += 22
            reasons.append(f"price is {vwap_below_pct:.1f}% below VWAP")
        elif vwap_below_pct >= max(7.0, atr_pct):
            severity += 12
            reasons.append(f"price is {vwap_below_pct:.1f}% below VWAP")
    if retracement is not None and retracement >= 75:
        severity += 14
        reasons.append("the prior impulse has retraced deeply")
    if exhaustion_score is not None and exhaustion_score >= 62:
        # The existing exhaustion detector is run-oriented, so this is only
        # weak corroboration; never let it dominate a downside-rebound call.
        severity += 4

    recovery = 0.0
    if m5 is not None and m5 > 0:
        recovery += 16
        reasons.append("5-minute momentum has turned positive")
    if m15 is not None and m15 > 0:
        recovery += 14
        reasons.append("15-minute momentum has turned positive")
    if developing:
        recovery += 18
        reasons.append("a rebound leg is developing")
    if observed_bounces >= 1:
        recovery += 8
        reasons.append("at least one rebound is already visible")
    if bounce_pct is not None and bounce_pct >= 5:
        recovery += 8
    if recovery_pct is not None and recovery_pct >= 8:
        recovery += 8
    if off_low_pct is not None and off_low_pct >= 8:
        recovery += 8
        reasons.append(f"price has recovered {off_low_pct:.1f}% from the session low")

    score = round(_clamp(severity + recovery, 0.0, 100.0), 1)
    if severity >= 30 and recovery >= 24:
        status = "REBOUND DEVELOPING"
    elif severity >= 30:
        status = "CAPITULATION / REBOUND WATCH"
    elif recovery >= 28:
        status = "REBOUND WATCH"
    else:
        status = "NO SPECIAL REBOUND SIGNAL"

    # Reclaim level: use the nearest overhead structure as a confirmation
    # trigger. This is deliberately not an automatic buy level.
    reclaim_candidates = []
    ref_peak = _num(sequence.get("reference_peak"))
    if ref_peak is not None and ref_peak > price * 1.003:
        reclaim_candidates.append((ref_peak, "recent rebound reference peak"))
    for value, label in _candidate_levels(metrics, price):
        if value > price * 1.003:
            reclaim_candidates.append((value, label))
    if vwap is not None and vwap > price * 1.003:
        reclaim_candidates.append((vwap, "VWAP"))
    reclaim_candidates.sort(key=lambda item: item[0])
    reclaim = reclaim_candidates[0] if reclaim_candidates else (None, None)

    return {
        "status": status,
        "score": score,
        "severity_score": round(_clamp(severity, 0.0, 100.0), 1),
        "recovery_score": round(_clamp(recovery, 0.0, 100.0), 1),
        "reasons": reasons[:5],
        "reclaim_level": round(reclaim[0], 4) if reclaim[0] is not None else None,
        "reclaim_reason": reclaim[1],
        "production_entry_signal": False,
        "note": (
            "A broken trade can still produce a sharp reflex bounce. This flag "
            "is for post-exit/re-entry monitoring only; it does not cancel a "
            "protective exit or create a new entry by itself."
        ),
    }


def build_position_exit_plan(metrics, average_cost, shares=None):
    metrics = metrics or {}
    price = _num(metrics.get("price"))
    cost = _num(average_cost)
    shares = _num(shares)

    if price is None or price <= 0:
        return {"status": "unavailable", "error": "Current price is unavailable."}
    if cost is None or cost <= 0:
        return {"status": "needs_cost", "error": "Enter your average cost to build an exit plan."}
    if shares is not None and shares <= 0:
        shares = None

    pnl_per_share = price - cost
    pnl_pct = pnl_per_share / cost * 100.0
    total_pnl = pnl_per_share * shares if shares is not None else None
    market_value = price * shares if shares is not None else None

    atr_pct = _num(metrics.get("atr_14_pct"))
    atr = _num(metrics.get("atr_14"))
    if atr is None:
        fallback_pct = atr_pct if atr_pct is not None else 6.0
        atr = price * fallback_pct / 100.0
    if atr_pct is None:
        atr_pct = atr / price * 100.0 if price else 6.0

    vwap = _num(metrics.get("vwap"))
    m5 = _num(metrics.get("momentum_5m"))
    m15 = _num(metrics.get("momentum_15m"))
    setup_score = _num(metrics.get("score")) or 50.0
    day_pct = _num(metrics.get("day_pct")) or 0.0
    vwap_ext = _num(metrics.get("vwap_extension_pct")) or 0.0
    from_high = _num(metrics.get("from_high_pct"))
    liquidity = str((metrics.get("liquidity") or {}).get("label") or "")
    potential = _num((metrics.get("decision_v2") or {}).get("potential_score"))
    live_integrity = _position_live_integrity(metrics)

    rebound_watch = _post_exit_rebound_watch(
        metrics,
        price,
        atr_pct,
        vwap,
        m5,
        m15,
        from_high,
    )

    support, support_quality = _support_level(metrics, price)
    buffer_pct = _clamp(atr_pct * 0.07, 0.4, 1.4)
    structural_exit = (
        support * (1.0 - buffer_pct / 100.0)
        if support is not None
        else None
    )
    volatility_exit = price - atr * 1.0

    protective_candidates = [
        value for value in (structural_exit, volatility_exit)
        if value is not None and 0 < value < price
    ]
    protective_exit = max(protective_candidates) if protective_candidates else price * 0.94

    # Once a position has a meaningful cushion, avoid giving all of it back
    # unless market structure itself requires a tighter floor.
    if pnl_pct >= 8.0:
        profit_floor = cost * 1.01
        if profit_floor < price * 0.997:
            protective_exit = max(protective_exit, profit_floor)

    protective_exit = min(protective_exit, price * 0.997)

    trailing_candidates = [price - atr * 0.65]
    if vwap is not None and 0 < vwap < price:
        trailing_candidates.append(vwap * 0.997)
    if pnl_pct >= 8.0 and cost * 1.005 < price:
        trailing_candidates.append(cost * 1.005)
    trailing_exit = max(
        value for value in trailing_candidates
        if value is not None and 0 < value < price
    )
    trailing_exit = min(trailing_exit, price * 0.998)
    trailing_exit = max(trailing_exit, protective_exit)

    targets = _candidate_levels(metrics, price)
    first_trim = targets[0][0] if targets else price + atr * 0.75
    first_trim_reason = targets[0][1] if targets else "0.75 ATR projection"

    second_target = None
    second_reason = None
    for value, label in targets[1:]:
        if value > first_trim * 1.005:
            second_target, second_reason = value, label
            break
    if second_target is None:
        second_target = max(price + atr * 1.25, first_trim * 1.015)
        second_reason = "1.25 ATR projection"

    stretch_target = None
    stretch_reason = None
    for value, label in targets:
        if value > second_target * 1.005:
            stretch_target, stretch_reason = value, label
            break
    if stretch_target is None:
        stretch_target = max(price + atr * 1.75, second_target * 1.02)
        stretch_reason = "1.75 ATR projection"

    above_vwap = vwap is None or price >= vwap
    short_momentum_weak = (
        (m5 is not None and m5 < 0)
        and (m15 is not None and m15 < 0)
    )
    weak_structure = (not above_vwap) and short_momentum_weak
    strong_structure = (
        above_vwap
        and (m5 is None or m5 >= 0)
        and (m15 is None or m15 >= -1.0)
        and setup_score >= 65
    )
    overextended = (
        vwap_ext > max(9.0, atr_pct)
        or day_pct > 50
        or (
            from_high is not None
            and from_high <= 1.0
            and day_pct >= 20
        )
    )
    near_first_trim = price >= first_trim * 0.992
    execution_risk = liquidity == "LOW"

    reasons = []
    if pnl_pct > 0:
        reasons.append(f"position is {pnl_pct:+.1f}% vs average cost")
    else:
        reasons.append(f"position is {pnl_pct:+.1f}% vs average cost")

    if weak_structure and setup_score < 55:
        action = "EXIT / PROTECT CAPITAL"
        read = "EXIT"
        reasons.append("price is below VWAP with weakening short-term momentum")
    elif pnl_pct < 0 and (weak_structure or setup_score < 50):
        action = "REDUCE / EXIT ON WEAKNESS"
        read = "REDUCE"
        reasons.append("the current setup is weak while the position is underwater")
    elif pnl_pct >= 5 and (near_first_trim or overextended or short_momentum_weak):
        action = "TRIM / PROTECT PROFIT"
        read = "TRIM"
        if near_first_trim:
            reasons.append("price is near the first modeled profit-taking area")
        elif overextended:
            reasons.append("price is extended enough to justify protecting gains")
        else:
            reasons.append("short-term momentum is weakening")
    elif pnl_pct > 0 and strong_structure:
        action = "HOLD / PROTECT PROFIT"
        read = "HOLD"
        reasons.append("trend structure remains constructive")
    elif pnl_pct < 0:
        action = "WATCH SUPPORT / RECOVERY"
        read = "WATCH"
        if support is not None:
            reasons.append("the position still has nearby technical support to define risk")
        else:
            reasons.append("no strong nearby support was identified")
    else:
        action = "HOLD WITH EXIT PLAN"
        read = "HOLD"
        reasons.append("no major exit trigger is active yet")

    if execution_risk:
        reasons.append("low liquidity can make exits less reliable")
    if (
        read in {"EXIT", "REDUCE"}
        and rebound_watch.get("status") in {
            "CAPITULATION / REBOUND WATCH",
            "REBOUND DEVELOPING",
        }
    ):
        reasons.append(
            "a reflex rebound is possible after this flush; exit protection "
            "and rebound/re-entry monitoring are separate decisions"
        )

    protective_return_pct = (protective_exit / cost - 1.0) * 100.0
    trailing_return_pct = (trailing_exit / cost - 1.0) * 100.0
    first_trim_return_pct = (first_trim / cost - 1.0) * 100.0
    room_to_protective_pct = (price - protective_exit) / price * 100.0
    room_to_trailing_pct = (price - trailing_exit) / price * 100.0

    confidence = 50.0
    if support is not None:
        confidence += 10
    if vwap is not None:
        confidence += 8
    if atr is not None:
        confidence += 8
    if targets:
        confidence += 8
    if potential is not None:
        confidence += 6
    if liquidity == "LOW":
        confidence -= 10

    # Reference levels are still useful when the feed is stale, but a stale
    # snapshot must not produce a live HOLD/EXIT/REDUCE instruction.
    if not live_integrity.get("ok"):
        action = "DATA CHECK — refresh live trade/quote before acting"
        read = "DATA CHECK"
        reasons = list(live_integrity.get("reasons") or []) + reasons
        confidence = min(confidence, 35.0)

    return {
        "status": "ok",
        "version": "position-exit-v1",
        "action": action,
        "read": read,
        "reasons": reasons[:4],
        "price": round(price, 4),
        "average_cost": round(cost, 4),
        "shares": shares,
        "pnl_per_share": round(pnl_per_share, 4),
        "pnl_pct": round(pnl_pct, 2),
        "total_pnl": round(total_pnl, 2) if total_pnl is not None else None,
        "market_value": round(market_value, 2) if market_value is not None else None,
        "protective_exit": round(protective_exit, 4),
        "protective_exit_return_pct": round(protective_return_pct, 2),
        "room_to_protective_pct": round(room_to_protective_pct, 2),
        "trailing_exit": round(trailing_exit, 4),
        "trailing_exit_return_pct": round(trailing_return_pct, 2),
        "room_to_trailing_pct": round(room_to_trailing_pct, 2),
        "first_trim": round(first_trim, 4),
        "first_trim_reason": first_trim_reason,
        "first_trim_return_pct": round(first_trim_return_pct, 2),
        "second_target": round(second_target, 4),
        "second_target_reason": second_reason,
        "stretch_target": round(stretch_target, 4),
        "stretch_reason": stretch_reason,
        "support": round(support, 4) if support is not None else None,
        "support_quality": support_quality,
        "vwap": round(vwap, 4) if vwap is not None else None,
        "atr": round(atr, 4),
        "confidence": round(_clamp(confidence, 0, 95)),
        "live_data_integrity": live_integrity,
        "rebound_watch": rebound_watch,
        "method_note": (
            "Position-management aid using cost basis, live price, VWAP, ATR, "
            "support/resistance, momentum, liquidity and the existing trade plan. "
            "A separate rebound-watch layer tracks post-flush recovery potential "
            "without overriding a protective exit. Levels are decision-support "
            "scenarios, not guaranteed execution prices."
        ),
    }
