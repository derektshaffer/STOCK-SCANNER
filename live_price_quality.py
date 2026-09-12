"""Shared validation for prices that may influence live trading calculations."""

from datetime import datetime, timezone
import math
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
MAX_LIVE_PRICE_AGE_SECONDS = 120.0
MAX_FUTURE_CLOCK_SKEW_SECONDS = 15.0


def normalize_symbol(value):
    return str(value or "").upper().strip()


def parse_market_timestamp(value, naive_tz=ET):
    """Parse provider ISO/epoch timestamps without treating malformed data as fresh."""
    if value in (None, ""):
        return None

    try:
        raw = float(value)
        if math.isfinite(raw):
            if raw > 10_000_000_000:
                raw /= 1000.0
            return datetime.fromtimestamp(raw, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        pass

    try:
        text = str(value).strip().replace("Z", "+00:00")
        if "." in text:
            head, tail = text.split(".", 1)
            fraction, suffix = tail, ""
            for marker in ("+", "-"):
                position = fraction.find(marker)
                if position > 0:
                    suffix = fraction[position:]
                    fraction = fraction[:position]
                    break
            text = (
                f"{head}.{fraction[:6]}{suffix}"
                if fraction
                else f"{head}{suffix}"
            )
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=naive_tz)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def select_freshest_live_price(
    expected_symbol,
    candidates,
    *,
    now=None,
    max_age_seconds=MAX_LIVE_PRICE_AGE_SECONDS,
):
    """Return the freshest positive, symbol-matched, timestamp-valid candidate.

    Each candidate is a small mapping with ``price``, ``timestamp``, ``symbol``,
    ``source`` and optionally ``kind``. Invalid rows are returned as concise
    rejection strings so callers can explain a provider fallback without using
    the rejected value in any calculation.
    """
    expected = normalize_symbol(expected_symbol)
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    valid = []
    rejected = []

    for raw in candidates or []:
        candidate = dict(raw or {})
        source = str(candidate.get("source") or "unknown source")
        observed = normalize_symbol(candidate.get("symbol"))
        if not expected or not observed or observed != expected:
            rejected.append(
                f"{source}: symbol mismatch ({observed or 'missing'} != {expected or 'missing'})"
            )
            continue

        try:
            price = float(candidate.get("price"))
        except (TypeError, ValueError):
            price = None
        if price is None or not math.isfinite(price) or price <= 0:
            rejected.append(f"{source}: price is missing or invalid")
            continue

        timestamp = parse_market_timestamp(candidate.get("timestamp"))
        if timestamp is None:
            rejected.append(f"{source}: timestamp is missing or invalid")
            continue

        sides = candidate.get("side_timestamps")
        if sides is not None:
            side_times = [parse_market_timestamp(value) for value in sides]
            if len(side_times) != 2 or any(t is None or (t-now_utc).total_seconds() > MAX_FUTURE_CLOCK_SKEW_SECONDS for t in side_times):
                rejected.append(f"{source}: quote side timestamp is missing, invalid, or in the future")
                continue

        future_seconds = (timestamp - now_utc).total_seconds()
        if future_seconds > MAX_FUTURE_CLOCK_SKEW_SECONDS:
            rejected.append(f"{source}: timestamp is {future_seconds:.0f}s in the future")
            continue
        age_seconds = max(0.0, (now_utc - timestamp).total_seconds())
        if age_seconds > float(max_age_seconds):
            rejected.append(f"{source}: price is stale ({age_seconds:.0f}s old)")
            continue

        candidate.update(
            {
                "price": price,
                "symbol": observed,
                "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "timestamp_dt": timestamp,
                "age_seconds": round(age_seconds, 2),
            }
        )
        valid.append(candidate)

    if not valid:
        return None, rejected

    kind_priority = {"trade": 3, "bar": 2, "quote_midpoint": 1}
    selected = max(
        valid,
        key=lambda row: (
            row["timestamp_dt"],
            kind_priority.get(str(row.get("kind") or ""), 0),
        ),
    )
    selected.pop("timestamp_dt", None)
    return selected, rejected


def provider_problem(error, provider):
    """Safe, durable failure categories; never persist response bodies or credentials."""
    text = str(error or "").lower()
    code = getattr(error, "code", None)
    if code in (401, 403) or any(s in text for s in ("401", "403", "auth", "entitle", "permission", "subscription", "not permitted")):
        category, message = "FEED PERMISSION ERROR", "Authentication or feed entitlement rejected"
    elif isinstance(error, TimeoutError) or any(s in text for s in ("timeout", "timed out")):
        category, message = "TIMEOUT", "Provider request timed out"
    elif any(s in text for s in ("disconnect", "connection", "closed", "reconnect", "session_limit")):
        category, message = "DISCONNECTED", "Market-data connection unavailable"
    elif any(s in text for s in ("json", "malformed", "invalid response")):
        category, message = "MALFORMED RESPONSE", "Provider returned an invalid response"
    else:
        category, message = "UNAVAILABLE", "Provider returned no usable market data"
    return {"provider": provider, "state": category, "message": message,
            "http_status": code if isinstance(code, int) else None}


def quote_timestamp(bid_time, ask_time):
    """A midpoint is only as current as its older side; both must be dated."""
    times = [parse_market_timestamp(t) for t in (bid_time, ask_time)]
    return min(times).isoformat() if all(times) else None


def tradier_price_candidates(symbol, quote, bars=()):
    quote = quote or {}
    try:
        bid, ask = float(quote.get("bid")), float(quote.get("ask"))
        midpoint = (bid+ask)/2 if 0 < bid <= ask else None
    except (TypeError, ValueError):
        midpoint = None
    candidates = [
        dict(symbol=quote.get("symbol"), price=quote.get("last"),
             timestamp=quote.get("trade_date") or quote.get("timestamp"),
             source="tradier_consolidated_trade", kind="trade"),
        dict(symbol=quote.get("symbol"), price=midpoint,
             timestamp=quote_timestamp(quote.get("bid_date"), quote.get("ask_date")),
             side_timestamps=[quote.get("bid_date"), quote.get("ask_date")],
             source="tradier_consolidated_quote_midpoint", kind="quote_midpoint"),
    ]
    candidates.extend(dict(symbol=b.get("symbol") or b.get("S") or symbol,
                           price=b.get("c"), timestamp=b.get("t"),
                           source="tradier_consolidated_timesales_bar", kind="bar") for b in list(bars)[-3:])
    return candidates


def permitted_live_source(source):
    # Explicit source families emitted by the live adapters. Historical/delayed
    # bars and undocumented legacy prices cannot acquire live provenance here.
    return str(source or "") in {
        "tradier_consolidated_trade", "tradier_consolidated_quote_midpoint",
        "tradier_consolidated_timesales_bar", "tradier_consolidated_intraday_bar",
        "tradier_stream_trade", "tradier_stream_quote_midpoint",
        "tradier_analyzer_snapshot", "tradier_analyzer_quote_midpoint",
        *{f"alpaca_{feed}_{kind}" for feed in ("iex", "sip") for kind in
          ("trade", "quote_midpoint", "intraday_bar", "stream_trade", "stream_quote_midpoint", "rest_trade", "rest_quote_midpoint")},
    }


def price_view(record, *, now=None):
    """Shared display classification, re-aged from the selected observation.

    This is a presentation copy, never a rewrite of a decision/input manifest.
    Acquisition adapters must exclude failed-source candidates before selection.
    """
    record = record or {}
    errors = list(record.get("live_price_provider_errors") or [])
    for field, provider in (("live_provider_error", "tradier"), ("alpaca_fallback_error", "alpaca")):
        if record.get(field):
            errors.append(provider_problem(record[field], provider))
    source = record.get("live_price_source")
    candidate = dict(symbol=record.get("symbol"), price=record.get("price") if record.get("price") is not None else record.get("last_known_price"),
                     timestamp=record.get("live_price_timestamp"), source=source)
    selected, rejected = select_freshest_live_price(record.get("symbol"), [candidate], now=now)
    declared = record.get("market_provider") or record.get("live_provider")
    permitted = permitted_live_source(source) and (not declared or str(source).startswith(str(declared).lower()+"_"))
    valid = selected and permitted and record.get("live_price_available") is not False
    fallback = bool(record.get("live_price_is_fallback") or errors or
                    any(s in str(source) for s in ("quote", "bar", "snapshot", "_rest_")))
    state = "FALLBACK" if valid and fallback else "LIVE" if valid else "UNAVAILABLE"
    stale = any("price is stale" in reason for reason in rejected) and permitted
    if not valid:
        if stale:
            state = "STALE"
        elif any(e.get("state") == "FEED PERMISSION ERROR" for e in errors):
            state = "FEED PERMISSION ERROR"
    timestamp = parse_market_timestamp(candidate["timestamp"])
    age = max(0, ((now or datetime.now(timezone.utc)).astimezone(timezone.utc)-timestamp).total_seconds()) if timestamp else None
    return {"state": state, "price": selected["price"] if valid else None,
            "last_known_price": float(candidate["price"]) if stale else None,
            "source": source or "unknown", "timestamp": candidate["timestamp"],
            "age_seconds": round(age, 2) if age is not None else None,
            "current": bool(valid), "errors": errors, "rejections": rejected}


def price_note(view):
    age = view.get("age_seconds")
    note = f"{view['state']} · {age:.0f}s old" if age is not None else f"{view['state']} · time unknown"
    source = str(view.get("source") or "unknown")
    for raw, label in (("tradier_consolidated", "Tradier"), ("tradier", "Tradier"),
                       ("alpaca_sip", "Alpaca SIP"), ("alpaca_iex", "Alpaca IEX")):
        source = source.replace(raw, label)
    note += " · " + source.replace("_", " ")
    if view.get("errors"):
        note += " · " + "; ".join(f"{e['provider']}: {e['state']}" for e in view["errors"])
    return note


def failed_stream(state):
    return bool(state.get("error") or state.get("status") in {
        "error", "disconnected", "closed", "reconnecting", "connection_limit", "session_limit", "disabled"})


def is_price_failure(error):
    text = str(error or "").lower()
    return any(word in text for word in ("live price", "http", "provider", "socket", "401", "403", "permission", "entitle", "auth", "timeout",
                                        "timed out", "disconnect", "connection", "feed", "malformed", "json"))


def stream_price_fields(metrics, state, provider, *, feed=None, rest=None, rest_error=None, now=None):
    """One price/quote policy for both stream adapters; no cross-source filling."""
    symbol = normalize_symbol(metrics.get("symbol"))
    prefix = "tradier_stream" if provider == "tradier" else f"alpaca_{feed}_stream"
    errors = price_view(metrics, now=now)["errors"]
    broken = failed_stream(state)
    if broken:
        errors.append(provider_problem(state.get("error") or state.get("status"), provider))
    if rest_error:
        errors.append(provider_problem(rest_error, provider+" REST"))
    candidates = []

    def add_pair(trade, quote, observed, origin, fallback=False):
        try:
            bid, ask = float(quote.get("bid")), float(quote.get("ask"))
            midpoint = (bid+ask)/2 if 0 < bid <= ask else None
        except (ValueError, TypeError):
            bid, ask, midpoint = None, None, None
        candidates.extend([
            dict(symbol=trade.get("symbol") or observed, price=trade.get("price"), timestamp=trade.get("timestamp"),
                 source=origin+"_trade", kind="trade", fallback=fallback),
            dict(symbol=quote.get("symbol") or observed, price=midpoint, timestamp=quote.get("timestamp"),
                 side_timestamps=quote.get("side_timestamps"),
                 source=origin+"_quote_midpoint", kind="quote_midpoint", bid=bid, ask=ask, fallback=True),
        ])

    if not broken:
        add_pair(state.get("last_trade") or {}, state.get("last_quote") or {}, state.get("symbol"), prefix)
    if rest is not None and not rest_error:
        observed = rest.get("symbol") or symbol  # single-symbol HTTP request scope
        t, q = rest.get("latestTrade") or {}, rest.get("latestQuote") or {}
        add_pair(dict(price=t.get("p"), timestamp=t.get("t"), symbol=t.get("S") or t.get("symbol")),
                 dict(bid=q.get("bp"), ask=q.get("ap"), timestamp=q.get("t"), symbol=q.get("S") or q.get("symbol")),
                 observed, f"alpaca_{feed}_rest", True)
    source = str(metrics.get("live_price_source") or "")
    same_source = source.startswith("tradier_") if provider == "tradier" else source.startswith(f"alpaca_{feed}_")
    declared = metrics.get("market_provider") or metrics.get("live_provider")
    if not broken and same_source and (not declared or declared == provider) and metrics.get("live_price_available") is not False:
        candidates.append(dict(symbol=metrics.get("symbol"), price=metrics.get("price"),
                               timestamp=metrics.get("live_price_timestamp"), source=source,
                               kind="quote_midpoint" if "quote" in source else "bar" if "bar" in source else "trade",
                               fallback=True))
    candidates = [c for c in candidates if permitted_live_source(c["source"])]
    selected, rejected = select_freshest_live_price(symbol, candidates, now=now)
    quote, _ = select_freshest_live_price(symbol, [c for c in candidates if c["kind"] == "quote_midpoint" and "bid" in c], now=now)
    trade, _ = select_freshest_live_price(symbol, [c for c in candidates if c["kind"] == "trade"], now=now)
    result = dict(symbol=symbol, price=(selected or {}).get("price"),
                  live_price_available=selected is not None, live_price_source=(selected or {}).get("source"),
                  live_price_timestamp=(selected or {}).get("timestamp"), live_price_age_seconds=(selected or {}).get("age_seconds"),
                  live_price_is_fallback=bool(errors or (selected or {}).get("fallback")),
                  live_price_provider_errors=errors, live_price_rejections=rejected,
                  bid=(quote or {}).get("bid"), ask=(quote or {}).get("ask"),
                  trade_age_seconds=(trade or {}).get("age_seconds"), quote_age_seconds=(quote or {}).get("age_seconds"))
    # Keep stale evidence for display only. It never enters price or breakout math.
    if not selected:
        stale = [c for c in candidates if normalize_symbol(c.get("symbol")) == symbol
                 and parse_market_timestamp(c.get("timestamp"))]
        if stale:
            last = max(stale, key=lambda c: parse_market_timestamp(c["timestamp"]))
            view = price_view(dict(symbol=symbol, price=last["price"], live_price_source=last["source"],
                                   live_price_timestamp=last["timestamp"]), now=now)
            if view["state"] == "STALE":
                result.update(last_known_price=view["last_known_price"], live_price_state="STALE",
                              live_price_source=view["source"], live_price_timestamp=view["timestamp"],
                              live_price_age_seconds=view["age_seconds"])
    result.setdefault("live_price_state", price_view(result, now=now)["state"])
    return result
