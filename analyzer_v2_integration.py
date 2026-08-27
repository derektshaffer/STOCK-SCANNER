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
        if ext > 12:
            extension_points = -10.0
            blockers.append("extended above VWAP")
        elif ext > 8:
            extension_points = -4.0

    components = {
        "base": round(base, 1),
        "trigger_proximity": round(trigger_points, 1),
        "reward_risk": round(rr_points, 1),
        "vwap": round(vwap_points, 1),
        "momentum": round(momentum_points, 1),
        "execution_quality": round(execution_points, 1),
        "extension": round(extension_points, 1),
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
        reasons.append(f"{validated}/4 ML models validated")
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

        metrics["decision_v2"] = {
            "version": "decision-v2.1-deduped",
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
