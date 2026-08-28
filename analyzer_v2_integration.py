import json
import os
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from prediction_tracker import record_prediction, resolve_symbol_predictions
from live_market_stream import ensure_live_stream, get_live_overlay
from float_data import get_public_float


SEC_BASE = "https://data.sec.gov"
SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT",
    "StockAnalyzer-v2 research application"
)


def _num(value):
    try:
        return float(value)
    except Exception:
        return None


def _clamp(value, lo=0.0, hi=100.0):
    return max(lo, min(hi, value))


def _label(score, high=72, moderate=52):
    if score >= high:
        return "HIGH"
    if score >= moderate:
        return "MODERATE"
    return "LOW"


def _http_json(url, timeout=6):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": SEC_USER_AGENT,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_text(url, timeout=6):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": SEC_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


@lru_cache(maxsize=4)
def _ticker_map(bucket):
    payload = _http_json("https://www.sec.gov/files/company_tickers.json", timeout=8)
    out = {}
    for item in payload.values():
        ticker = str(item.get("ticker") or "").upper().strip()
        cik = item.get("cik_str")
        if ticker and cik is not None:
            out[ticker] = int(cik)
    return out


@lru_cache(maxsize=256)
def _sec_submissions(cik, bucket):
    return _http_json(f"{SEC_BASE}/submissions/CIK{int(cik):010d}.json")


@lru_cache(maxsize=256)
def _company_facts(cik, bucket):
    return _http_json(
        f"{SEC_BASE}/api/xbrl/companyfacts/CIK{int(cik):010d}.json"
    )


def _latest_shares_outstanding(facts):
    try:
        rows = (
            facts.get("facts", {})
            .get("dei", {})
            .get("EntityCommonStockSharesOutstanding", {})
            .get("units", {})
            .get("shares", [])
        )
    except Exception:
        rows = []
    candidates = []
    for row in rows:
        value = _num(row.get("val"))
        end = row.get("end")
        filed = row.get("filed")
        if value and value > 0 and end:
            candidates.append((str(end), str(filed or ""), value))
    if not candidates:
        return None, None
    candidates.sort(reverse=True)
    end, _filed, value = candidates[0]
    return value, end


def _sector_from_sic(sic):
    try:
        sic = int(sic)
    except Exception:
        return None, None
    if 1000 <= sic < 1500 or 2900 <= sic < 3000:
        return "Energy", "XLE"
    if 2800 <= sic < 2900 or 3800 <= sic < 3900:
        return "Healthcare", "XLV"
    if 6000 <= sic < 6800:
        return "Financials", "XLF"
    if 3500 <= sic < 3700 or 7300 <= sic < 7400:
        return "Technology", "XLK"
    if 4800 <= sic < 4900:
        return "Communication Services", "XLC"
    if 4900 <= sic < 5000:
        return "Utilities", "XLU"
    if 6500 <= sic < 6600:
        return "Real Estate", "XLRE"
    if 2000 <= sic < 2400:
        return "Consumer Staples", "XLP"
    if 2500 <= sic < 2800 or 5000 <= sic < 6000:
        return "Consumer Discretionary", "XLY"
    if 1500 <= sic < 1800 or 3400 <= sic < 3500:
        return "Industrials", "XLI"
    if 100 <= sic < 1000 or 2400 <= sic < 2500 or 3200 <= sic < 3400:
        return "Materials", "XLB"
    return None, None


def _recent_sec_risk(symbol):
    bucket = int(time.time() // 21600)
    try:
        cik = _ticker_map(bucket).get(symbol)
        if cik is None:
            return {"status": "unavailable"}

        submissions = _sec_submissions(cik, bucket)
        facts = _company_facts(cik, bucket)
        shares, shares_as_of = _latest_shares_outstanding(facts)

        sic = submissions.get("sic")
        sic_description = submissions.get("sicDescription")
        sector, sector_etf = _sector_from_sic(sic)

        recent = (submissions.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        dates = recent.get("filingDate") or []
        accessions = recent.get("accessionNumber") or []
        docs = recent.get("primaryDocument") or []

        now_date = datetime.now(timezone.utc).date()
        risk_forms = []
        keywords = set()
        high_risk_forms = {"S-1", "S-3", "424B3", "424B4", "424B5", "EFFECT"}

        for form, date_text, acc, doc in zip(forms[:80], dates[:80], accessions[:80], docs[:80]):
            try:
                filed = datetime.fromisoformat(str(date_text)).date()
                age = (now_date - filed).days
            except Exception:
                continue
            if age > 180:
                continue
            form = str(form or "")
            if form in high_risk_forms:
                risk_forms.append({"form": form, "filed": str(date_text), "age_days": age})
                if len(risk_forms) <= 3 and acc and doc:
                    try:
                        acc_clean = str(acc).replace("-", "")
                        url = f"{SEC_ARCHIVES}/{int(cik)}/{acc_clean}/{doc}"
                        text = re.sub(r"<[^>]+>", " ", _http_text(url, timeout=5)).lower()
                        for kw in (
                            "warrant",
                            "at-the-market",
                            "at the market",
                            "registered direct",
                            "public offering",
                            "convertible",
                            "shelf registration",
                        ):
                            if kw in text:
                                keywords.add(kw)
                    except Exception:
                        pass

        recent_30 = [x for x in risk_forms if x["age_days"] <= 30]
        if recent_30 and keywords:
            dilution = "HIGH"
        elif recent_30 or len(risk_forms) >= 2:
            dilution = "MODERATE"
        elif risk_forms:
            dilution = "LOW"
        else:
            dilution = "NONE FOUND"

        return {
            "status": "ok",
            "cik": cik,
            "sic": sic,
            "sic_description": sic_description,
            "sector": sector,
            "sector_etf": sector_etf,
            "shares_outstanding": shares,
            "shares_as_of": shares_as_of,
            "dilution_risk": dilution,
            "recent_offering_forms": risk_forms[:8],
            "dilution_keywords": sorted(keywords),
        }
    except Exception as exc:
        return {"status": "unavailable", "error": str(exc)[:140]}


_MARKET_CACHE = {}
_SIP_PROBE = {"checked_at": 0.0, "available": None, "error": None}


def prefer_best_live_feed(sa, symbol="SPY"):
    """Prefer consolidated SIP automatically when the Alpaca account allows it.

    The old app often remained on IEX simply because ALPACA_LIVE_FEED was set
    that way months earlier. A successful SIP snapshot is definitive enough to
    switch the analyzer to SIP for the current process. If SIP is unavailable,
    keep IEX and expose the reason in the Analyzer UI.
    """
    now_ts = time.time()
    cached = _SIP_PROBE.get("available")
    if cached is not None and now_ts - float(_SIP_PROBE.get("checked_at") or 0) < 600:
        if cached:
            sa.LIVE_FEED = "sip"
        return {
            "available": bool(cached),
            "active_feed": str(sa.LIVE_FEED).upper(),
            "error": _SIP_PROBE.get("error"),
            "checked_at": _SIP_PROBE.get("checked_at"),
        }

    test_symbol = str(symbol or "SPY").upper().strip() or "SPY"
    try:
        snap = sa.snapshot(test_symbol, "sip")
        trade = (snap or {}).get("latestTrade") or {}
        quote = (snap or {}).get("latestQuote") or {}
        if not trade and not quote:
            raise RuntimeError("SIP snapshot returned no trade or quote data.")
        sa.LIVE_FEED = "sip"
        _SIP_PROBE.update(
            {"checked_at": now_ts, "available": True, "error": None}
        )
    except Exception as exc:
        # Preserve explicit fallback behavior rather than breaking analysis.
        if str(sa.LIVE_FEED).lower() != "sip":
            sa.LIVE_FEED = "iex"
        _SIP_PROBE.update(
            {
                "checked_at": now_ts,
                "available": False,
                "error": str(exc)[:180],
            }
        )

    return {
        "available": bool(_SIP_PROBE.get("available")),
        "active_feed": str(sa.LIVE_FEED).upper(),
        "error": _SIP_PROBE.get("error"),
        "checked_at": _SIP_PROBE.get("checked_at"),
    }


def _snapshot_day_pct(sa, symbol):
    try:
        snap = sa.snapshot(symbol, sa.LIVE_FEED)
        day = snap.get("dailyBar") or {}
        prev = snap.get("prevDailyBar") or {}
        close = _num(day.get("c"))
        prev_close = _num(prev.get("c"))
        if close is not None and prev_close:
            return round((close / prev_close - 1.0) * 100.0, 2)
    except Exception:
        pass
    return None


def _market_context(sa, sector_etf=None):
    bucket = int(time.time() // 60)
    key = (bucket, sector_etf)
    if key in _MARKET_CACHE:
        return _MARKET_CACHE[key]

    symbols = ["SPY", "QQQ", "IWM"]
    if sector_etf and sector_etf not in symbols:
        symbols.append(sector_etf)
    moves = {symbol: _snapshot_day_pct(sa, symbol) for symbol in symbols}

    vals = [moves.get(x) for x in ("SPY", "QQQ", "IWM") if moves.get(x) is not None]
    breadth_proxy = sum(vals) / len(vals) if vals else None
    if breadth_proxy is None:
        label = "UNKNOWN"
    elif breadth_proxy >= 0.75:
        label = "RISK-ON"
    elif breadth_proxy <= -0.75:
        label = "RISK-OFF"
    else:
        label = "MIXED"

    result = {
        "label": label,
        "broad_market_avg_pct": round(breadth_proxy, 2) if breadth_proxy is not None else None,
        "moves": moves,
        "sector_etf": sector_etf,
        "sector_move_pct": moves.get(sector_etf) if sector_etf else None,
    }
    _MARKET_CACHE.clear()
    _MARKET_CACHE[key] = result
    return result


def _catalyst_strength(news_rows):
    rows = [r for r in (news_rows or []) if isinstance(r, dict)]
    if not rows:
        return {"label": "NONE", "score": 0.0, "fresh_articles": 0}

    weighted = []
    fresh = 0
    strong_positive = 0
    strong_negative = 0
    for row in rows:
        score = _num(row.get("score")) or 0.0
        age = _num(row.get("age_hours"))
        if age is None:
            recency = 0.5
        elif age <= 6:
            recency = 1.0
            fresh += 1
        elif age <= 24:
            recency = 0.8
            fresh += 1
        elif age <= 72:
            recency = 0.5
        else:
            recency = 0.25
        adjusted = score * recency
        weighted.append(adjusted)
        if adjusted >= 4:
            strong_positive += 1
        if adjusted <= -4:
            strong_negative += 1

    strongest = max(weighted, key=lambda x: abs(x)) if weighted else 0.0
    if strong_positive >= 2 or strongest >= 6:
        label = "VERY STRONG POSITIVE"
        score = min(10.0, max(7.0, strongest + 1.0))
    elif strongest >= 3:
        label = "POSITIVE"
        score = min(7.0, strongest)
    elif strong_negative >= 2 or strongest <= -6:
        label = "VERY STRONG NEGATIVE"
        score = max(-10.0, min(-7.0, strongest - 1.0))
    elif strongest <= -3:
        label = "NEGATIVE"
        score = max(-7.0, strongest)
    else:
        label = "WEAK / NEUTRAL"
        score = strongest

    return {
        "label": label,
        "score": round(score, 1),
        "fresh_articles": fresh,
        "article_count": len(rows),
    }


def _potential_score(metrics, sec, market, catalyst):
    """Further-upside quality with each evidence family counted once."""
    base = 40.5
    technical = 0.0
    history_points = 0.0
    ml_points = 0.0
    catalyst_points = 0.0
    market_points = 0.0
    dilution_points = 0.0
    reasons = []

    day_pct = _num(metrics.get("day_pct")) or 0.0
    if day_pct >= 10:
        technical += min(12.0, day_pct * 0.16)
        reasons.append("strong day momentum")
    elif day_pct < 3:
        technical -= 4.0

    pace = _num(metrics.get("volume_pace"))
    if pace is not None:
        if pace >= 2:
            technical += min(10.0, 4.0 + (pace - 2.0) * 1.5)
            reasons.append("elevated volume pace")
        elif pace < 0.8:
            technical -= 4.0

    if metrics.get("vwap_position") == "ABOVE":
        technical += 6.0
        reasons.append("holding above VWAP")
    else:
        technical -= 5.0

    from_high = _num(metrics.get("from_high_pct"))
    if from_high is not None:
        if from_high <= 3:
            technical += 6.0
            reasons.append("trading near session high")
        elif from_high >= 12:
            technical -= 6.0

    impulse = metrics.get("impulse_pullback") or {}
    if impulse.get("detected"):
        retrace = _num(impulse.get("current_retracement_pct"))
        max_retrace = _num(impulse.get("max_retracement_pct"))
        if impulse.get("bounce_confirmed"):
            technical += 4.0
            reasons.append("impulse pullback has started bouncing")
        elif retrace is not None and retrace < 20:
            technical -= 3.0
        if max_retrace is not None and max_retrace > 78:
            technical -= 4.0
            reasons.append("deep impulse retracement raises failure risk")

    hist = metrics.get("historical_setup") or {}
    if hist.get("status") == "ok":
        bias = _num(hist.get("bias_score")) or 0.0
        n = int(hist.get("sample_count") or 0)
        weight = min(1.0, n / 20.0)
        history_points = _clamp(bias * 0.8 * weight, -8.0, 8.0)
        if bias >= 5:
            reasons.append("bullish same-ticker analogs")
        elif bias <= -5:
            reasons.append("bearish same-ticker analogs")

    ml = metrics.get("ml_prediction") or {}
    edge = _num(ml.get("ml_edge_score"))
    validated = int(ml.get("validated_edge_model_count") or 0)
    if edge is not None and validated:
        ml_points = _clamp(
            (edge - 50.0) * 0.22 * min(1.0, validated / 2.0),
            -8.0,
            8.0,
        )
        reasons.append(f"{validated} validated ML model(s)")

    catalyst_points = _num(catalyst.get("score")) or 0.0
    if catalyst_points >= 3:
        reasons.append("fresh positive catalyst")
    elif catalyst_points <= -3:
        reasons.append("negative catalyst pressure")

    market_label = market.get("label")
    if market_label == "RISK-ON":
        market_points += 4.0
        reasons.append("supportive broad market")
    elif market_label == "RISK-OFF":
        market_points -= 4.0

    sector_move = _num(market.get("sector_move_pct"))
    if sector_move is not None:
        sector_points = _clamp(sector_move * 1.2, -4.0, 4.0)
        market_points += sector_points
        if sector_move >= 1:
            reasons.append("sector tailwind")

    dilution = sec.get("dilution_risk")
    if dilution == "HIGH":
        dilution_points = -12.0
        reasons.append("high recent dilution/financing risk")
    elif dilution == "MODERATE":
        dilution_points = -6.0
        reasons.append("recent financing/dilution risk")

    components = {
        "base": round(base, 1),
        "technical_momentum": round(technical, 1),
        "historical_analogs": round(history_points, 1),
        "validated_ml": round(ml_points, 1),
        "catalyst": round(catalyst_points, 1),
        "market_sector": round(market_points, 1),
        "dilution": round(dilution_points, 1),
    }
    score = sum(components.values())
    return round(_clamp(score), 1), reasons[:6], components


def _entry_readiness(metrics):
    """Current entry quality from direct timing/execution inputs.

    Trade-plan status is used only as a safety cap, not as an additive score,
    so its underlying VWAP/momentum/liquidity inputs are not counted twice.
    """
    plan = metrics.get("trade_plan") or {}
    selected = plan.get("selected") or {}
    status = str(plan.get("status") or "WAIT")
    price = _num(metrics.get("price"))

    base = 50.0
    trigger_points = 0.0
    rr_points = 0.0
    vwap_points = 0.0
    momentum_points = 0.0
    execution_points = 0.0
    extension_points = 0.0
    structure_points = 0.0
    blockers = []

    entry_low = _num(selected.get("entry_low"))
    entry_high = _num(selected.get("entry_high"))
    if price is not None and entry_low is not None and entry_high is not None:
        if entry_low <= price <= entry_high:
            trigger_points = 20.0
        else:
            if price < entry_low:
                distance_pct = (entry_low - price) / price * 100.0 if price > 0 else None
            else:
                distance_pct = (price - entry_high) / price * 100.0 if price > 0 else None
            if distance_pct is not None and distance_pct <= 1.5:
                trigger_points = 10.0
            elif distance_pct is not None and distance_pct > 4.0:
                trigger_points = -8.0
    else:
        trigger_points = -8.0

    rr = _num(selected.get("risk_reward"))
    if rr is not None:
        if rr >= 2:
            rr_points = 10.0
        elif rr >= 1.3:
            rr_points = 4.0
        elif rr < 1.15:
            rr_points = -15.0
            blockers.append("weak reward/risk")

    if metrics.get("vwap_position") == "ABOVE":
        vwap_points = 6.0
    else:
        vwap_points = -8.0
        blockers.append("below VWAP")

    m5 = _num(metrics.get("momentum_5m"))
    m15 = _num(metrics.get("momentum_15m"))
    if m5 is not None and m5 > 0 and (m15 is None or m15 >= 0):
        momentum_points = 7.0
    elif m5 is not None and m5 < 0 and m15 is not None and m15 < 0:
        momentum_points = -10.0
        blockers.append("short-term momentum weakening")
    elif m5 is not None and m5 < 0:
        momentum_points = -6.0
        blockers.append("5-minute momentum weakening")
    elif m15 is not None and m15 > 0:
        momentum_points = 3.0

    # Liquidity label already incorporates spread and dollar-volume quality,
    # so spread is intentionally NOT scored again here.
    liquidity = (metrics.get("liquidity") or {}).get("label")
    if liquidity == "HIGH":
        execution_points = 6.0
    elif liquidity == "LOW":
        execution_points = -14.0
        blockers.append("low liquidity")

    ext = _num(metrics.get("vwap_extension_pct"))
    if ext is not None:
        if ext > 20:
            extension_points = -16.0
            blockers.append("severely extended above VWAP")
        elif ext > 12:
            extension_points = -10.0
            blockers.append("extended above VWAP")
        elif ext > 8:
            extension_points = -4.0

    impulse = metrics.get("impulse_pullback") or {}
    if impulse.get("detected"):
        impulse_move = _num(impulse.get("impulse_move_pct")) or 0.0
        current_retrace = _num(impulse.get("current_retracement_pct"))
        max_retrace = _num(impulse.get("max_retracement_pct"))
        recovery = _num(impulse.get("bounce_recovery_pct")) or 0.0
        volume_ratio = _num(impulse.get("pullback_volume_ratio"))

        if current_retrace is not None and current_retrace < 25 and impulse_move >= 15:
            structure_points -= 12.0
            blockers.append("initial run has not retraced enough yet")
        elif current_retrace is not None and 28 <= current_retrace <= 62:
            if impulse.get("bounce_confirmed") or recovery >= 6:
                structure_points += 9.0
            else:
                structure_points -= 5.0
                blockers.append("pullback zone reached but bounce is not confirmed")
        if max_retrace is not None and max_retrace > 78:
            structure_points -= 10.0
            blockers.append("retracement is deep enough to threaten the impulse")
        if volume_ratio is not None:
            if volume_ratio < 0.80:
                structure_points += 3.0
            elif volume_ratio > 1.20:
                structure_points -= 3.0
                blockers.append("pullback volume is expanding")

    sequence = metrics.get("bounce_sequence") or {}
    if sequence.get("detected"):
        completed = int(sequence.get("completed_bounces") or 0)
        seq_health = _num(sequence.get("sequence_health_score"))
        decay = _num(sequence.get("bounce_decay_ratio"))
        lower_highs = int(sequence.get("lower_high_streak") or 0)
        higher_lows = int(sequence.get("higher_low_streak") or 0)
        leg = str(sequence.get("current_leg") or "").upper()

        if completed >= 1:
            if seq_health is not None and seq_health >= 68:
                structure_points += 6.0
            elif seq_health is not None and seq_health < 42:
                structure_points -= 9.0
                blockers.append("multi-bounce sequence is deteriorating")
            if decay is not None and decay < 0.60:
                structure_points -= 6.0
                blockers.append("latest bounce is much weaker than the prior bounce")
            elif decay is not None and decay >= 0.90:
                structure_points += 3.0
            if lower_highs >= 2:
                structure_points -= 8.0
                blockers.append("multiple later bounces are making lower highs")
            elif lower_highs == 1:
                structure_points -= 3.0
            if higher_lows >= 2:
                structure_points += 5.0
            if leg.startswith("PULL"):
                # A later dip can be a good quick-bounce setup, but only once
                # price proves the dip is holding.
                structure_points -= 3.0
                blockers.append("later-bounce dip still needs a hold/reclaim")

    stair_points=0.0
    stair=metrics.get("stair_step") or {}
    stair_score=_num(stair.get("structure_score"))
    if stair.get("reaccelerating") and stair_score is not None and stair_score>=65:
        stair_points+=6.0
    elif stair.get("state")=="HIGHER PLATEAU / COILING" and stair_score is not None and stair_score>=58:
        stair_points+=3.0
    if stair.get("breakdown"):
        stair_points-=10.0
        blockers.append("multi-session higher plateau has broken down")

    repeat_points=0.0
    preferred=str(plan.get("preferred_plan") or "")
    if preferred=="repeat_bounce":
        rb=plan.get("repeat_bounce") or {}
        number=int(rb.get("bounce_number") or 0)
        if status=="ENTRY AVAILABLE":
            repeat_points+=7.0
        hist_rate=_num(rb.get("historical_bounce_rate_pct"))
        if hist_rate is not None:
            if hist_rate>=65:repeat_points+=4.0
            elif hist_rate<35:repeat_points-=4.0
        if number>=3:
            repeat_points-=2.0  # later bounces deserve a small maturity penalty
        if int(sequence.get("lower_high_streak") or 0)>=2:
            repeat_points-=4.0

    components = {
        "base": round(base, 1),
        "trigger_proximity": round(trigger_points, 1),
        "reward_risk": round(rr_points, 1),
        "vwap": round(vwap_points, 1),
        "momentum": round(momentum_points, 1),
        "execution_quality": round(execution_points, 1),
        "extension": round(extension_points, 1),
        "pullback_structure": round(structure_points, 1),
        "repeat_bounce_setup": round(repeat_points, 1),
        "stair_step_structure": round(stair_points, 1),
    }
    raw_score = sum(components.values())
    capped_score = raw_score
    safety_cap = None

    if status == "NO TRADE" and capped_score > 35.0:
        safety_cap = 35.0
        capped_score = 35.0
    elif status == "WAIT" and capped_score > 69.0:
        safety_cap = 69.0
        capped_score = 69.0

    if safety_cap is not None:
        components["plan_status_cap"] = round(capped_score - raw_score, 1)

    if status == "WAIT":
        blockers.insert(0, str(plan.get("action") or "waiting for confirmation"))
    elif status == "NO TRADE":
        blockers.insert(0, str(plan.get("action") or "trade rejected"))

    return round(_clamp(capped_score), 1), blockers[:5], components

def _evidence_strength(metrics, sec, market, catalyst):
    score = 0.0
    reasons = []

    hist = metrics.get("historical_setup") or {}
    n = int(hist.get("sample_count") or 0)
    hist_points = min(32.0, n * 1.3)
    score += hist_points
    if n >= 25:
        reasons.append("high historical analog sample")
    elif n >= 15:
        reasons.append("moderate historical analog sample")
    else:
        reasons.append("limited historical analog sample")

    ml = metrics.get("ml_prediction") or {}
    validated = int(ml.get("validated_edge_model_count") or 0)
    score += min(30.0, validated * 10.0)
    if validated:
        reasons.append(f"{validated}/9 ML models validated")
    else:
        reasons.append("no validated ML models yet")

    trade_age = _num(metrics.get("trade_age_seconds"))
    live_feed = str(metrics.get("live_feed") or "").upper()
    market_provider = str(
        metrics.get("market_provider")
        or metrics.get("live_provider")
        or ""
    ).lower()
    if market_provider == "tradier" or "TRADIER" in live_feed:
        score += 12.0
        reasons.append("Tradier consolidated live feed")
    elif live_feed == "SIP":
        score += 12.0
        reasons.append("consolidated SIP live feed")
    else:
        score += 5.0
        reasons.append("IEX-only live feed")
    if trade_age is not None and trade_age <= 30:
        score += 8.0
    elif trade_age is not None and trade_age > 120:
        reasons.append("stale latest trade")

    if catalyst.get("article_count", 0):
        score += 7.0
    if sec.get("status") == "ok":
        score += 6.0
    if market.get("label") != "UNKNOWN":
        score += 5.0

    return round(_clamp(score), 1), reasons[:6]


def _full_spectrum_analysis(metrics, sec, market, catalyst, turnover):
    """Synthesize the stock the way a discretionary momentum trader would.

    Scores are 0-100 quality/risk indices. Scenario percentages are RELATIVE
    WEIGHTS, not calibrated probabilities; they summarize which path currently
    has the most evidence while the separately validated ML models retain their
    own probability semantics.
    """
    def num(key, default=None):
        value=_num(metrics.get(key))
        return default if value is None else value
    def cap(x):
        return round(_clamp(float(x),0.0,100.0),1)
    def stance(score, bullish=True):
        if bullish:
            return "STRONG" if score>=72 else "POSITIVE" if score>=58 else "MIXED" if score>=42 else "WEAK"
        return "VERY HIGH" if score>=78 else "HIGH" if score>=62 else "MODERATE" if score>=42 else "LOW"

    # 1) Momentum / trend.
    momentum=50.0
    for key,mult,limit in (("momentum_5m",4.0,14),("momentum_15m",2.2,12),("momentum_30m",1.2,10)):
        v=num(key)
        if v is not None:
            momentum += _clamp(v*mult,-limit,limit)
    if metrics.get("vwap_position")=="ABOVE":momentum+=7
    elif metrics.get("vwap_position")=="BELOW":momentum-=9
    from_high=num("from_high_pct")
    if from_high is not None:
        if from_high<=3:momentum+=5
        elif from_high>=12:momentum-=8
    momentum=cap(momentum)

    # 2) Volume / participation.
    volume=50.0
    pace=num("volume_pace")
    if pace is not None:
        if pace>=3:volume+=22
        elif pace>=2:volume+=15
        elif pace>=1.25:volume+=7
        elif pace<0.7:volume-=12
    impulse=metrics.get("impulse_pullback") or {}
    sequence=metrics.get("bounce_sequence") or {}
    stair=metrics.get("stair_step") or {}
    pvr=_num(impulse.get("pullback_volume_ratio"))
    if pvr is not None:
        if pvr<0.75:volume+=7
        elif pvr>1.20:volume-=8
    ft=_num((turnover or {}).get("float_turnover"))
    if ft is not None:
        if ft>=1.0:volume+=8
        elif ft>=0.40:volume+=4
    volume=cap(volume)

    # 3) Price structure / pullback health.
    structure=50.0
    retrace=_num(impulse.get("current_retracement_pct"))
    max_retrace=_num(impulse.get("max_retracement_pct"))
    recovery=_num(impulse.get("bounce_recovery_pct")) or 0.0
    if impulse.get("detected"):
        structure+=5
        if retrace is not None and 28<=retrace<=62:structure+=8
        if impulse.get("bounce_confirmed"):structure+=14
        elif retrace is not None and 28<=retrace<=62:structure-=5
        if max_retrace is not None and max_retrace>=78:structure-=18
        elif max_retrace is not None and max_retrace>=65 and recovery<5:structure-=10
    else:
        structure-=3
    structure=cap(structure)

    # Multi-bounce sequence health is kept separate from the first-pullback
    # structure score so a tradable second bounce can coexist with a weakening
    # overall run.
    sequence_score=_num(sequence.get("sequence_health_score"))
    if sequence_score is None:
        sequence_score=50.0
    sequence_score=cap(sequence_score)

    stair_score=_num(stair.get("structure_score"))
    if stair_score is None:
        stair_score=45.0 if not stair.get("detected") else 50.0
    stair_score=cap(stair_score)

    # 4) Historical behavior.
    hist=metrics.get("historical_setup") or {}
    history=50.0
    if hist.get("status")=="ok":
        history += (_num(hist.get("bias_score")) or 0.0)*1.8
        fail=_num(hist.get("breakout_failure_pct"))
        follow=_num(hist.get("breakout_follow_through_pct"))
        if fail is not None and follow is not None:
            history += (follow-fail)*0.16
        bounce=_num(hist.get("impulse_bounce_5pct_rate"))
        if bounce is not None:
            history += (bounce-50.0)*0.12
        second_bounce_rate=_num(hist.get("second_bounce_rate_pct"))
        if second_bounce_rate is not None:
            history += (second_bounce_rate-50.0)*0.08
    history=cap(history)

    # 5) Validated ML continuation plus reversal model.
    ml=metrics.get("ml_prediction") or {}
    ml_edge=_num(ml.get("ml_edge_score"))
    ml_score=cap(ml_edge if ml_edge is not None else 50.0)
    reversal_model=(ml.get("models") or {}).get("reversal_30") or {}
    reversal_ml=_num(reversal_model.get("probability_pct"))
    reversal_ml_valid=bool(reversal_model.get("validated"))
    repeat_bounce_model=(ml.get("models") or {}).get("repeat_bounce_30") or {}
    repeat_bounce_ml=_num(repeat_bounce_model.get("probability_pct"))
    repeat_bounce_valid=bool(repeat_bounce_model.get("validated"))
    new_high_model=(ml.get("models") or {}).get("new_high_60") or {}
    new_high_ml=_num(new_high_model.get("probability_pct"))
    new_high_valid=bool(new_high_model.get("validated"))
    post_failure_model=(ml.get("models") or {}).get("post_bounce_failure_60") or {}
    post_failure_ml=_num(post_failure_model.get("probability_pct"))
    post_failure_valid=bool(post_failure_model.get("validated"))
    stair_model=(ml.get("models") or {}).get("stair_reacceleration_60") or {}
    stair_ml=_num(stair_model.get("probability_pct"))
    stair_ml_valid=bool(stair_model.get("validated"))

    # 6) Catalyst / news.
    cat_score=cap(50.0+(_num(catalyst.get("score")) or 0.0)*4.0)

    # 7) Market + sector context.
    market_score=50.0
    broad=_num(market.get("broad_market_avg_pct"))
    sector=_num(market.get("sector_move_pct"))
    if broad is not None:market_score+=_clamp(broad*6.0,-12,12)
    if sector is not None:market_score+=_clamp(sector*5.0,-10,10)
    if market.get("label")=="RISK-ON":market_score+=5
    elif market.get("label")=="RISK-OFF":market_score-=5
    market_score=cap(market_score)

    # 8) Execution / liquidity.
    execution=50.0
    liq=(metrics.get("liquidity") or {}).get("label")
    if liq=="HIGH":execution+=24
    elif liq=="MODERATE":execution+=8
    elif liq=="LOW":execution-=25
    spread=num("spread_pct")
    if spread is not None:
        if spread<=1:execution+=10
        elif spread>=5:execution-=18
        elif spread>=3:execution-=8
    execution=cap(execution)

    # 9) Fundamental/dilution risk translated into long-quality score.
    fundamental=55.0 if sec.get("status")=="ok" else 50.0
    dilution=sec.get("dilution_risk")
    if dilution=="HIGH":fundamental-=28
    elif dilution=="MODERATE":fundamental-=15
    elif dilution=="NONE FOUND":fundamental+=5
    fundamental=cap(fundamental)

    # 10) Reversal risk combines live tape/structure with historical failure and
    # a validated ML reversal probability when one exists.
    live_ex=metrics.get("run_exhaustion") or {}
    live_rev=_num(live_ex.get("score"))
    reversal_parts=[]
    if live_rev is not None:reversal_parts.append((live_rev,0.50))
    fail=_num(hist.get("breakout_failure_pct"))
    fade=_num(hist.get("gap_fade_pct"))
    hist_rev=None
    vals=[x for x in (fail,fade) if x is not None]
    if vals:hist_rev=sum(vals)/len(vals)
    if hist_rev is not None:reversal_parts.append((hist_rev,0.15))
    if reversal_ml is not None and reversal_ml_valid:reversal_parts.append((reversal_ml,0.15))
    if int(sequence.get("completed_bounces") or 0)>=2 and post_failure_valid and post_failure_ml is not None:
        reversal_parts.append((post_failure_ml,0.20))
    if reversal_parts:
        tw=sum(w for _,w in reversal_parts)
        reversal=cap(sum(v*w for v,w in reversal_parts)/tw)
    else:
        reversal=50.0

    potential=_num((metrics.get("decision_v2") or {}).get("potential_score"))
    if potential is None:
        potential=_num(metrics.get("score")) or 50.0

    # Relative scenario evidence. These deliberately combine independent
    # families rather than pretending one indicator determines the future.
    new_high_support=(new_high_ml if new_high_valid and new_high_ml is not None else 50.0)
    stair_support=(stair_ml if stair_ml_valid and stair_ml is not None else stair_score)
    continuation_raw=(
        potential*0.14+momentum*0.12+volume*0.08+structure*0.08+
        sequence_score*0.07+stair_score*0.08+history*0.10+ml_score*0.12+
        new_high_support*0.05+stair_support*0.05+cat_score*0.04+
        market_score*0.03+execution*0.02+(100-reversal)*0.02
    )
    bounce_base=50.0
    if impulse.get("detected"):
        if retrace is not None and 25<=retrace<=62:bounce_base+=18
        if impulse.get("bounce_confirmed"):bounce_base+=14
        elif retrace is not None and 25<=retrace<=62:bounce_base-=4
        hist_bounce=_num(hist.get("impulse_bounce_5pct_rate"))
        if hist_bounce is not None:bounce_base+=(hist_bounce-50)*0.20
        if pvr is not None and pvr<0.85:bounce_base+=6

    completed_bounces=int(sequence.get("completed_bounces") or 0)
    if completed_bounces>=1:
        hist_second=_num(hist.get("second_bounce_rate_pct"))
        hist_third=_num(hist.get("third_bounce_rate_pct"))
        if completed_bounces==1 and hist_second is not None:
            bounce_base+=(hist_second-50)*0.28
        elif completed_bounces>=2 and hist_third is not None:
            bounce_base+=(hist_third-50)*0.24
        if repeat_bounce_valid and repeat_bounce_ml is not None:
            bounce_base+=(repeat_bounce_ml-50)*0.45
        decay=_num(sequence.get("bounce_decay_ratio"))
        if decay is not None:
            if decay<0.55:bounce_base-=12
            elif decay<0.75:bounce_base-=6
            elif decay>=0.90:bounce_base+=4
        if int(sequence.get("lower_high_streak") or 0)>=2:
            bounce_base-=10

    if completed_bounces>=2 and post_failure_valid and post_failure_ml is not None:
        bounce_base-=(post_failure_ml-50.0)*0.22

    bounce_raw=cap(
        bounce_base*0.44
        + structure*0.18
        + sequence_score*0.18
        + history*0.10
        + (100-reversal)*0.10
    )

    stair_base=stair_score
    if stair.get("reaccelerating"):stair_base+=14
    elif stair.get("state")=="HIGHER PLATEAU / COILING":stair_base+=8
    if stair.get("volume_cooled"):stair_base+=4
    if stair.get("plateau_tight"):stair_base+=4
    if stair.get("breakdown"):stair_base-=28
    if stair_ml_valid and stair_ml is not None:
        stair_base+=(stair_ml-50.0)*0.45
    stair_raw=cap(stair_base*0.62+momentum*0.12+volume*0.10+history*0.08+(100-reversal)*0.08)

    reversal_raw=cap(reversal)
    chop_raw=cap(
        52.0
        - abs(momentum-50.0)*0.35
        - abs(ml_score-50.0)*0.20
        + (8 if 42<=reversal<=62 else 0)
        + (6 if 42<=structure<=58 else 0)
    )

    raws={
        "continuation":max(1.0,continuation_raw),
        "pullback_bounce":max(1.0,bounce_raw),
        "stair_reacceleration":max(1.0,stair_raw),
        "reversal_failure":max(1.0,reversal_raw),
        "sideways_chop":max(1.0,chop_raw),
    }
    total=sum(raws.values())
    scenarios={
        key:{
            "relative_weight_pct":round(value/total*100.0,1),
            "evidence_score":round(value,1),
        }
        for key,value in raws.items()
    }
    dominant=max(scenarios,key=lambda k:scenarios[k]["relative_weight_pct"])

    categories={
        "momentum":{"score":momentum,"stance":stance(momentum)},
        "volume_participation":{"score":volume,"stance":stance(volume)},
        "price_structure":{"score":structure,"stance":stance(structure)},
        "multi_bounce_sequence":{"score":sequence_score,"stance":stance(sequence_score)},
        "multi_session_stair_step":{"score":stair_score,"stance":stance(stair_score)},
        "historical_behavior":{"score":history,"stance":stance(history)},
        "validated_ml":{"score":ml_score,"stance":stance(ml_score)},
        "catalyst":{"score":cat_score,"stance":stance(cat_score)},
        "market_sector":{"score":market_score,"stance":stance(market_score)},
        "execution_liquidity":{"score":execution,"stance":stance(execution)},
        "fundamental_dilution":{"score":fundamental,"stance":stance(fundamental)},
        "reversal_risk":{"score":reversal,"stance":stance(reversal,bullish=False)},
    }

    available=[
        "live price/quote","VWAP","multi-horizon momentum","ATR/volatility",
        "volume pace","support/resistance","impulse/retracement","multi-bounce sequence","multi-session stair-step / plateau","run exhaustion",
        "same-ticker history","same-ticker ML","peer ML","news/catalyst",
        "market/sector","SEC dilution risk","float/turnover","spread/liquidity"
    ]
    unavailable=[]
    if str(metrics.get("market_provider") or "").lower()!="tradier" and str(metrics.get("live_feed") or "").upper()!="SIP":
        unavailable.append("full consolidated tape")
    unavailable.extend([
        "true Level-2 order-book depth unless a depth feed is connected",
        "broker-specific hidden liquidity / queue position",
        "options flow unless an options feed is connected",
        "real-time short-borrow availability unless a borrow feed is connected",
    ])

    return {
        "version":"full-spectrum-v3-sequence-regimes",
        "categories":categories,
        "reversal_risk_score":reversal,
        "reversal_risk_label":stance(reversal,bullish=False),
        "dominant_scenario":dominant,
        "scenarios":scenarios,
        "scenario_note":"Relative evidence weights, not calibrated probabilities.",
        "coverage":{"available":available,"not_currently_available":unavailable},
    }


def _turnover_context(metrics, sec, float_data):
    shares = _num(sec.get("shares_outstanding"))
    volume = _num(metrics.get("volume"))

    shares_turnover = None
    if shares and volume is not None and shares > 0:
        shares_turnover = volume / shares

    float_shares = _num((float_data or {}).get("public_float_shares"))
    float_turnover = None
    if float_shares and volume is not None and float_shares > 0:
        float_turnover = volume / float_shares

    return {
        "shares_outstanding": shares,
        "shares_as_of": sec.get("shares_as_of"),
        "shares_outstanding_turnover": (
            round(shares_turnover, 3) if shares_turnover is not None else None
        ),
        "float_shares": float_shares,
        "float_turnover": (
            round(float_turnover, 3) if float_turnover is not None else None
        ),
        "float_date": (float_data or {}).get("float_date"),
        "float_filing_date": (float_data or {}).get("filing_date"),
        "float_source": (
            (float_data or {}).get("provider")
            if (float_data or {}).get("status") == "ok"
            else None
        ),
        "float_status": (float_data or {}).get("status"),
        "float_error": (float_data or {}).get("error"),
    }


def install_v2_analysis(sa):
    """Add separate upside-potential, entry-timing and evidence scores."""
    if hasattr(sa, "_decision_v2_analyze"):
        return sa._decision_v2_analyze

    base_analyze = sa.analyze

    def enhanced_analyze(symbol):
        symbol_clean = str(symbol or "").upper().strip()
        # Probe entitlement on SPY, a continuously active SIP symbol, so an
        # illiquid target ticker cannot create a false "SIP unavailable" result.
        sip_status = prefer_best_live_feed(sa, "SPY")
        metrics = base_analyze(symbol)
        now = datetime.now(timezone.utc)

        stream_status = ensure_live_stream(
            symbol_clean,
            str(sa.LIVE_FEED or "iex").lower(),
            metrics=metrics,
        )
        live_overlay = get_live_overlay(metrics)

        sec = _recent_sec_risk(symbol_clean)
        float_context = get_public_float(symbol_clean)
        market = _market_context(sa, sec.get("sector_etf"))
        catalyst = _catalyst_strength(metrics.get("news") or [])
        turnover = _turnover_context(metrics, sec, float_context)

        potential, potential_reasons, potential_components = _potential_score(
            metrics, sec, market, catalyst
        )
        readiness, blockers, entry_components = _entry_readiness(metrics)
        evidence, evidence_reasons = _evidence_strength(metrics, sec, market, catalyst)

        # Give the full-spectrum engine the current upside score before the
        # public decision_v2 object is assembled.
        metrics["decision_v2"] = {"potential_score": potential}
        full_spectrum = _full_spectrum_analysis(
            metrics, sec, market, catalyst, turnover
        )

        # Final safety gate: a precise-looking entry should not be promoted when
        # the timing score or supporting evidence is still weak. Keep the price
        # zone visible as a watch area, but require stronger confirmation.
        plan = metrics.get("trade_plan") or {}
        if str(plan.get("status") or "") == "ENTRY AVAILABLE":
            safety_reasons = []
            if readiness < 60:
                safety_reasons.append("entry readiness is below 60/100")
            if evidence < 50:
                safety_reasons.append("evidence strength is below 50/100")
            reversal_risk=_num(full_spectrum.get("reversal_risk_score"))
            preferred_plan=str(plan.get("preferred_plan") or "")
            # A confirmed Bounce #2/#3 scalp is intentionally allowed to coexist
            # with elevated overall-run exhaustion. Only VERY high reversal risk
            # blocks the quick-bounce plan; ordinary continuation entries keep
            # the stricter 68/100 cap.
            reversal_cap=78 if preferred_plan=="repeat_bounce" else 68
            if reversal_risk is not None and reversal_risk >= reversal_cap:
                safety_reasons.append(
                    "run-exhaustion / reversal risk is very high for the later-bounce scalp"
                    if preferred_plan=="repeat_bounce"
                    else "run-exhaustion / reversal risk is high"
                )
            if safety_reasons:
                plan["status"] = "WAIT"
                plan["action"] = "WAIT FOR STRONGER CONFIRMATION"
                plan_reasons = list(plan.get("reasons") or [])
                plan_reasons.insert(
                    0,
                    "Entry withheld because " + " and ".join(safety_reasons) + "."
                )
                plan["reasons"] = plan_reasons
                metrics["trade_plan"] = plan
                blockers = ["WAIT FOR STRONGER CONFIRMATION"] + list(blockers)
                old_readiness = readiness
                readiness = min(readiness, 59.0)
                if old_readiness != readiness:
                    entry_components["evidence_safety_cap"] = round(readiness - old_readiness, 1)

        metrics["decision_v2"] = {
            "version": "decision-v2.6-sequence-regimes",
            "potential_score": potential,
            "potential_label": _label(potential, 72, 52),
            "entry_readiness": readiness,
            "entry_label": _label(readiness, 72, 52),
            "evidence_strength": evidence,
            "evidence_label": _label(evidence, 72, 52),
            "potential_reasons": potential_reasons,
            "potential_components": potential_components,
            "entry_blockers": blockers,
            "entry_components": entry_components,
            "evidence_reasons": evidence_reasons,
            "catalyst_strength": catalyst,
            "market_context": market,
            "fundamental_context": sec,
            "float_context": float_context,
            "turnover_context": turnover,
            "full_spectrum": full_spectrum,
            "sip_status": sip_status,
            "live_stream_status": stream_status,
            "live_overlay": live_overlay,
        }

        try:
            record_result = record_prediction(metrics, now)
            tracking = resolve_symbol_predictions(
                sa, symbol_clean, now, current_metrics=metrics
            )
            tracking["last_record"] = record_result
        except Exception as exc:
            tracking = {"error": str(exc)[:140], "persistence": "runtime-local"}
        metrics["decision_v2"]["tracking"] = tracking
        return metrics

    sa._decision_v2_analyze = enhanced_analyze
    sa.analyze = enhanced_analyze
    return enhanced_analyze
