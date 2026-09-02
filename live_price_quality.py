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
