"""Off-hours Swing / Longer-Term discovery from completed daily market data.

This is intentionally separate from the live momentum scanner. It can run when
the market is closed and ranks multi-day / multi-week technical setups from
completed daily candles. It does not produce intraday ACTION, does not change
validated scanner ML, and does not imply an automatic entry.

The Analyzer remains the confirmation layer for fundamentals, dilution/SEC
filings, catalyst durability, execution risk, and the actual trade decision.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from scanner_discovery import get_or_build_discovery_universe
from scanner_timeframe_fit import attach_timeframe_fit
from tradier_live import get_history_bars, post_quotes

try:
    from scanner_behavior import multi_session_behavior_features
except Exception:
    multi_session_behavior_features = None

ET = ZoneInfo("America/New_York")
VERSION = "offhours-timeframe-scan-v1"
OUTPUT_PATH = Path("scan_logs/offhours_timeframe_latest.json")
HISTORY_DIR = Path("scan_logs/offhours_timeframe")
UNIVERSE_SIZE = int(
    os.environ.get("OFFHOURS_TIMEFRAME_UNIVERSE_SIZE", "1200") or 1200
)
HISTORY_POOL_SIZE = int(
    os.environ.get("OFFHOURS_TIMEFRAME_HISTORY_POOL", "120") or 120
)
RESULT_LIMIT = int(
    os.environ.get("OFFHOURS_TIMEFRAME_RESULT_LIMIT", "30") or 30
)
HISTORY_DELAY_SECONDS = float(
    os.environ.get("OFFHOURS_TIMEFRAME_HISTORY_DELAY", "0.55") or 0.55
)

TRADIER_TOKEN = (
    os.environ.get("TRADIER_ACCESS_TOKEN", "").strip()
    or os.environ.get("TRADIER_TOKEN", "").strip()
)


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _pct(new, old):
    if new is None or old in (None, 0):
        return None
    return (float(new) / float(old) - 1.0) * 100.0


def _likely_common_stock(symbol):
    s = str(symbol or "").upper().strip()
    if not s or len(s) > 6:
        return False
    if len(s) < 5:
        return True
    if s.endswith(("WS", "WT", "RT", "UN")):
        return False
    if s.endswith(("W", "R", "U")):
        return False
    return True


def _chunks(values, size):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _tradier_call(fn, *args):
    delay = 1.0
    for attempt in range(5):
        try:
            return fn(*args)
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt >= 4:
                raise
        except urllib.error.URLError:
            if attempt >= 4:
                raise
        time.sleep(delay)
        delay = min(8.0, delay * 2.0)
    return None


def _quote_universe(symbols):
    merged = {}
    batches = list(_chunks(symbols, 300))
    for index, batch in enumerate(batches):
        rows = _tradier_call(post_quotes, batch, TRADIER_TOKEN) or {}
        merged.update(rows)
        if index < len(batches) - 1:
            time.sleep(0.35)
    return merged


def _preselect_history_pool(quote_rows, target_size):
    eligible = []
    for symbol, row in quote_rows.items():
        if str(row.get("type") or "").lower() != "stock":
            continue
        price = _num(row.get("last")) or _num(row.get("close")) or _num(row.get("prevclose"))
        prev = _num(row.get("prevclose"))
        avg_volume = _num(row.get("average_volume")) or 0.0
        volume = _num(row.get("volume")) or 0.0
        if price is None or prev is None or prev <= 0 or avg_volume <= 0:
            continue
        if not 0.50 <= price <= 60.0:
            continue
        change = _num(row.get("change_percentage"))
        if change is None:
            change = _pct(price, prev) or 0.0
        eligible.append(
            {
                "symbol": symbol,
                "price": price,
                "prevclose": prev,
                "change_pct": change,
                "average_volume": avg_volume,
                "volume": volume,
                "average_dollar_volume": price * avg_volume,
                "relative_volume": volume / avg_volume if avg_volume else 0.0,
            }
        )

    selected = []
    seen = set()

    def take(rows, count):
        for row in rows:
            if row["symbol"] in seen:
                continue
            seen.add(row["symbol"])
            selected.append(row)
            if len(selected) >= target_size or count <= 1:
                if len(selected) >= target_size:
                    return
            count -= 1
            if count <= 0:
                return

    # Preserve low/mid/high price representation instead of allowing the
    # liquid $20-$60 names to dominate every off-hours list.
    per_band = max(12, int(target_size * 0.20))
    for low, high in ((0.50, 5.0), (5.0, 20.0), (20.0, 60.01)):
        rows = [r for r in eligible if low <= r["price"] < high]
        rows.sort(key=lambda r: r["average_dollar_volume"], reverse=True)
        take(rows, per_band)

    # Add current completed-session movers and unusual-volume names so newly
    # emerging swing setups are not missed by a pure liquidity screen.
    movers = sorted(eligible, key=lambda r: (r["change_pct"], r["average_dollar_volume"]), reverse=True)
    take(movers, max(15, int(target_size * 0.20)))

    unusual = sorted(eligible, key=lambda r: (r["relative_volume"], r["average_dollar_volume"]), reverse=True)
    take(unusual, max(10, int(target_size * 0.12)))

    if len(selected) < target_size:
        liquid = sorted(eligible, key=lambda r: r["average_dollar_volume"], reverse=True)
        take(liquid, target_size - len(selected))

    return selected[:target_size], len(eligible)


def _moving_average(bars, sessions):
    vals = [_num(row.get("c")) for row in bars[-sessions:]]
    vals = [value for value in vals if value is not None and value > 0]
    if len(vals) < max(5, sessions - 2):
        return None
    return sum(vals) / len(vals)


def _return_n(bars, sessions):
    if len(bars) <= sessions:
        return None
    current = _num(bars[-1].get("c"))
    old = _num(bars[-(sessions + 1)].get("c"))
    value = _pct(current, old)
    return round(value, 2) if value is not None else None


def _daily_context(symbol, quote_seed, bars, spy_return_20d=None):
    bars = [row for row in bars if _num(row.get("c")) is not None]
    if len(bars) < 42:
        return None

    current = bars[-1]
    previous = bars[-2]
    price = _num(current.get("c"))
    prev_close = _num(previous.get("c"))
    if price is None or prev_close is None or prev_close <= 0:
        return None

    ma10 = _moving_average(bars, 10)
    ma20 = _moving_average(bars, 20)
    ma40 = _moving_average(bars, 40)
    alignment = None
    if ma10 is not None and ma20 is not None and ma40 is not None:
        if ma10 > ma20 > ma40:
            alignment = "BULLISH"
        elif ma10 < ma20 < ma40:
            alignment = "BEARISH"
        else:
            alignment = "MIXED"

    recent = bars[-45:]
    recent_high = max(
        (_num(row.get("h")) or _num(row.get("c")) or 0.0)
        for row in recent
    )
    prior_20_high = max(
        (_num(row.get("h")) or _num(row.get("c")) or 0.0)
        for row in bars[-21:-1]
    )

    prior_volumes = [
        _num(row.get("v"))
        for row in bars[-21:-1]
        if (_num(row.get("v")) or 0) > 0
    ]
    avg_volume_20 = (
        sum(prior_volumes) / len(prior_volumes)
        if prior_volumes
        else None
    )
    current_volume = _num(current.get("v")) or 0.0
    volume_ratio = (
        current_volume / avg_volume_20
        if avg_volume_20
        else None
    )

    row = {
        "symbol": symbol,
        "price": round(price, 4),
        "day_pct": round(_pct(price, prev_close) or 0.0, 2),
        "average_volume": quote_seed.get("average_volume"),
        "average_dollar_volume": quote_seed.get("average_dollar_volume"),
        "daily_history_sessions": len(bars),
        "daily_context_source": "tradier_consolidated_daily",
        "daily_return_5d_pct": _return_n(bars, 5),
        "daily_return_20d_pct": _return_n(bars, 20),
        "daily_return_40d_pct": _return_n(bars, 40),
        "daily_ma_10": round(ma10, 4) if ma10 is not None else None,
        "daily_ma_20": round(ma20, 4) if ma20 is not None else None,
        "daily_ma_40": round(ma40, 4) if ma40 is not None else None,
        "daily_above_ma20": bool(price >= ma20) if ma20 is not None else None,
        "daily_above_ma40": bool(price >= ma40) if ma40 is not None else None,
        "daily_ma_alignment": alignment,
        "daily_recent_high": round(recent_high, 4) if recent_high else None,
        "daily_from_recent_high_pct": (
            round((price / recent_high - 1.0) * 100.0, 2)
            if recent_high
            else None
        ),
        "daily_volume": round(current_volume, 0),
        "daily_avg_volume_20": round(avg_volume_20, 0) if avg_volume_20 else None,
        "daily_volume_ratio": round(volume_ratio, 2) if volume_ratio is not None else None,
        "daily_breakout_20d": bool(prior_20_high and price >= prior_20_high),
        "news_bonus": 0.0,
    }

    if multi_session_behavior_features is not None:
        try:
            row.update(
                multi_session_behavior_features(
                    bars[-21:-1],
                    current,
                    atr_pct=None,
                )
            )
        except Exception:
            pass

    attach_timeframe_fit(row)

    swing = _num(row.get("timeframe_swing_score")) or 0.0
    longer = _num(row.get("timeframe_longer_term_score")) or 0.0
    r20 = _num(row.get("daily_return_20d_pct"))
    relative_20d = (
        r20 - float(spy_return_20d)
        if r20 is not None and spy_return_20d is not None
        else None
    )
    row["relative_strength_vs_spy_20d_pct"] = (
        round(relative_20d, 2) if relative_20d is not None else None
    )

    archetypes = []
    if row.get("daily_breakout_20d"):
        archetypes.append("20D BREAKOUT")
    if (
        alignment == "BULLISH"
        and r20 is not None
        and r20 >= 8
        and (row.get("daily_return_40d_pct") or 0) >= 10
    ):
        archetypes.append("TREND CONTINUATION")
    if (
        alignment == "BULLISH"
        and ma20
        and abs(price / ma20 - 1.0) <= 0.04
        and r20 is not None
        and r20 >= 5
    ):
        archetypes.append("CONSTRUCTIVE PULLBACK")
    if (
        (row.get("daily_return_20d_pct") or 0) < 0
        and row["day_pct"] >= 6
    ):
        archetypes.append("REVERSAL / IGNITION")
    row["daily_setup_archetypes"] = archetypes or ["MOMENTUM TREND"]

    # Keep the daily discovery rank separate from the raw timeframe-fit
    # scores. Those fit scores intentionally saturate when many favorable
    # conditions align, which is useful for classification but not for ranking
    # 30 strong off-hours candidates against one another.
    primary_fit = max(swing, longer)
    secondary_fit = min(swing, longer)
    score = primary_fit * 0.65 + secondary_fit * 0.20

    if relative_20d is not None:
        if relative_20d >= 15:
            score += 6
        elif relative_20d >= 8:
            score += 4
        elif relative_20d >= 3:
            score += 2
        elif relative_20d <= -10:
            score -= 5
        elif relative_20d <= -5:
            score -= 3

    if volume_ratio is not None:
        if volume_ratio >= 2.0:
            score += 4
        elif volume_ratio >= 1.3:
            score += 2
        elif volume_ratio < 0.6:
            score -= 2

    if row.get("daily_breakout_20d"):
        score += 4
    if "TREND CONTINUATION" in archetypes:
        score += 3
    if "CONSTRUCTIVE PULLBACK" in archetypes:
        score += 2
    if "REVERSAL / IGNITION" in archetypes:
        score += 2
    if alignment == "BEARISH":
        score -= 6
    if (row.get("daily_from_recent_high_pct") or 0) <= -30:
        score -= 4

    score = max(0.0, min(100.0, score))
    row["daily_discovery_score"] = round(score, 1)

    if score >= 88:
        row["daily_setup_grade"] = "A"
    elif score >= 78:
        row["daily_setup_grade"] = "B"
    else:
        row["daily_setup_grade"] = "C"

    if longer >= swing + 5:
        row["daily_review_action"] = "REVIEW LONGER-TERM"
    elif swing >= longer + 5:
        row["daily_review_action"] = "REVIEW SWING"
    else:
        row["daily_review_action"] = "REVIEW SWING / LONGER-TERM"

    row["daily_review_reason"] = (
        " · ".join(row.get("timeframe_fit", {}).get("explanation") or [])
        or "Completed daily trend structure warrants Analyzer review."
    )
    row["production_rank_impact"] = False
    return row


def _fetch_daily(symbol, now_utc):
    return _tradier_call(
        get_history_bars,
        symbol,
        TRADIER_TOKEN,
        now_utc - timedelta(days=120),
        now_utc,
        "daily",
    ) or []


def run_scan(now_utc=None):
    if not TRADIER_TOKEN:
        raise RuntimeError(
            "Off-hours timeframe discovery requires TRADIER_ACCESS_TOKEN."
        )

    started = time.perf_counter()
    now_utc = now_utc or datetime.now(timezone.utc)
    now_et = now_utc.astimezone(ET)

    os.environ["SCANNER_DISCOVERY_UNIVERSE_SIZE"] = str(UNIVERSE_SIZE)
    symbols, universe_meta = get_or_build_discovery_universe(
        TRADIER_TOKEN,
        _likely_common_stock,
    )
    quote_rows = _quote_universe(symbols)
    pool, eligible_count = _preselect_history_pool(
        quote_rows,
        HISTORY_POOL_SIZE,
    )

    spy_bars = _fetch_daily("SPY", now_utc)
    spy_return_20d = _return_n(spy_bars, 20) if spy_bars else None

    rows = []
    errors = []
    for index, seed in enumerate(pool):
        symbol = seed["symbol"]
        try:
            bars = _fetch_daily(symbol, now_utc)
            row = _daily_context(
                symbol,
                seed,
                bars,
                spy_return_20d=spy_return_20d,
            )
            if row is not None:
                rows.append(row)
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)[:180]})
        if index < len(pool) - 1:
            time.sleep(HISTORY_DELAY_SECONDS)

    # Keep only stocks with at least a useful Swing or Longer-Term technical
    # read. This is a discovery shortlist, not a claim that the setup is ready
    # to buy.
    qualified = [
        row for row in rows
        if max(
            _num(row.get("timeframe_swing_score")) or 0.0,
            _num(row.get("timeframe_longer_term_score")) or 0.0,
        ) >= 60.0
    ]
    qualified.sort(
        key=lambda row: (
            row.get("daily_discovery_score") or 0,
            max(
                row.get("timeframe_swing_score") or 0,
                row.get("timeframe_longer_term_score") or 0,
            ),
            row.get("average_dollar_volume") or 0,
        ),
        reverse=True,
    )
    selected = qualified[:RESULT_LIMIT]

    last_session_date = None
    if spy_bars:
        last_ts = spy_bars[-1].get("t")
        if last_ts:
            try:
                last_session_date = datetime.fromisoformat(
                    str(last_ts).replace("Z", "+00:00")
                ).astimezone(ET).date().isoformat()
            except Exception:
                pass

    payload = {
        "schema_version": 1,
        "version": VERSION,
        "generated_at_et": now_et.isoformat(),
        "generated_at_utc": now_utc.isoformat(),
        "last_completed_session_date": last_session_date,
        "mode": "offhours_daily_timeframe_discovery",
        "source": "Tradier consolidated completed daily candles",
        "universe": {
            **universe_meta,
            "quote_rows": len(quote_rows),
            "eligible_quotes": eligible_count,
            "history_pool": len(pool),
            "history_rows_scored": len(rows),
            "qualified": len(qualified),
            "returned": len(selected),
        },
        "benchmark": {
            "symbol": "SPY",
            "return_20d_pct": spy_return_20d,
        },
        "candidates": selected,
        "errors": errors[:20],
        "runtime_seconds": round(time.perf_counter() - started, 1),
        "note": (
            "Off-hours daily discovery is a technical Swing / Longer-Term screen. "
            "It does not change live Momentum Scanner ranking, ACTION, or ML. "
            "Open a candidate in Analyzer before deciding whether to trade."
        ),
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    stamp = last_session_date or now_et.date().isoformat()
    history_path = HISTORY_DIR / f"offhours_timeframe_{stamp}.json"
    history_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def main():
    payload = run_scan()
    print(
        "Off-hours timeframe scan complete: "
        f"{len(payload.get('candidates') or [])} results · "
        f"{payload.get('runtime_seconds')}s · "
        f"session={payload.get('last_completed_session_date')}"
    )
    for row in (payload.get("candidates") or [])[:15]:
        print(
            f"{row['symbol']}: {row.get('daily_setup_grade')} "
            f"{row.get('daily_review_action')} · "
            f"daily={row.get('daily_discovery_score')} · "
            f"swing={row.get('timeframe_swing_score')} · "
            f"longer={row.get('timeframe_longer_term_score')} · "
            f"{', '.join(row.get('daily_setup_archetypes') or [])}"
        )


if __name__ == "__main__":
    main()
