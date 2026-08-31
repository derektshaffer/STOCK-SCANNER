import json
import os
import statistics
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from timeframe_targets import (
    SWING_HORIZON_SESSIONS,
    resolve_swing_path_from_bars,
)
from swing_research_flags import FLAG_VERSION as SWING_RESEARCH_FLAG_VERSION
from tradier_live import get_history_bars, get_timesales_bars


ET = ZoneInfo("America/New_York")
DATA_BASE = "https://data.alpaca.markets"
API_KEY = os.environ.get("ALPACA_API_KEY", "").strip()
API_SECRET = os.environ.get("ALPACA_SECRET_KEY", "").strip()
TRADIER_TOKEN = (
    os.environ.get("TRADIER_ACCESS_TOKEN", "").strip()
    or os.environ.get("TRADIER_TOKEN", "").strip()
)
OUTCOME_DATE = os.environ.get("OUTCOME_DATE", "").strip()
OUT_DIR = Path(os.environ.get("ANALYZER_OUTCOME_DIR", "analyzer_outcomes"))
from analyzer_versions import (
    ANALYZER_FEATURE_VERSION,
    CALIBRATION_SCHEMA_VERSION,
    DECISION_SCORE_VERSION,
    TIMEFRAME_SCORE_VERSION,
)

OUTCOME_MAX_BAR_DELAY_SECONDS = 180


def _headers():
    if not API_KEY or not API_SECRET:
        raise RuntimeError("Missing Alpaca API keys for Analyzer outcome scoring.")
    return {
        "APCA-API-KEY-ID": API_KEY,
        "APCA-API-SECRET-KEY": API_SECRET,
        "Accept": "application/json",
    }


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _request_json(url, timeout=60):
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_dt(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _num(value):
    try:
        return float(value)
    except Exception:
        return None


def _target_date():
    if OUTCOME_DATE:
        return datetime.strptime(OUTCOME_DATE, "%Y-%m-%d").date()
    now = datetime.now(ET)
    if now.weekday() < 5 and now.time() >= dtime(16, 15):
        return now.date()
    day = now.date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _tradier_retry(fn, *args, **kwargs):
    delay = 1.0
    for attempt in range(4):
        try:
            return fn(*args, **kwargs)
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt >= 3:
                raise
        except urllib.error.URLError:
            if attempt >= 3:
                raise
        import time as _time
        _time.sleep(delay)
        delay = min(6.0, delay * 2.0)
    return []


def _fetch_symbol_bars(symbol, start, end):
    """Prefer Tradier consolidated 1-minute bars; fall back to Alpaca SIP."""
    if TRADIER_TOKEN:
        try:
            bars = _tradier_retry(
                get_timesales_bars,
                symbol,
                TRADIER_TOKEN,
                start,
                end,
                interval="1min",
                session_filter="open",
            ) or []
            if bars:
                return bars
        except Exception as exc:
            print(f"WARN Tradier Analyzer intraday outcome bars {symbol}: {exc}")

    if not API_KEY or not API_SECRET:
        return []

    rows = []
    page_token = None
    while True:
        q = {
            "timeframe": "1Min",
            "start": _iso(start),
            "end": _iso(end),
            "limit": 10000,
            "adjustment": "raw",
            "feed": "sip",
            "sort": "asc",
        }
        if page_token:
            q["page_token"] = page_token
        url = (
            f"{DATA_BASE}/v2/stocks/{urllib.parse.quote(symbol)}/bars?"
            + urllib.parse.urlencode(q)
        )
        payload = _request_json(url)
        rows.extend(payload.get("bars") or [])
        page_token = payload.get("next_page_token")
        if not page_token:
            break
    return rows


def _bar_dt(bar):
    return _parse_dt(bar.get("t"))


def _price_at_or_after(
    bars,
    target,
    tolerance_seconds=OUTCOME_MAX_BAR_DELAY_SECONDS,
):
    best = None
    best_delta = None
    for bar in bars:
        dt = _bar_dt(bar)
        close = _num(bar.get("c"))
        if dt is None or close is None or dt < target:
            continue
        delta = (dt - target).total_seconds()
        if best_delta is None or delta < best_delta:
            best = close
            best_delta = delta
    if best_delta is None or best_delta > tolerance_seconds:
        return None
    return best


def _first_touch(bars, target, stop, created):
    target = _num(target)
    stop = _num(stop)
    if target is None or stop is None:
        return None
    for bar in bars:
        dt = _bar_dt(bar)
        if dt is None or dt <= created:
            continue
        high = _num(bar.get("h"))
        low = _num(bar.get("l"))
        if high is None or low is None:
            continue
        hit_target = high >= target
        hit_stop = low <= stop
        if hit_target and hit_stop:
            return "ambiguous"
        if hit_target:
            return "target"
        if hit_stop:
            return "stop"
    return None


def _window_excursions(bars, created, price, minutes):
    if created is None or price is None or price <= 0:
        return None, None
    end = created + timedelta(minutes=minutes)
    window=[]
    for bar in bars:
        dt=_bar_dt(bar)
        if dt is None or dt <= created or dt > end:
            continue
        window.append(bar)
    highs=[_num(b.get("h")) for b in window]
    lows=[_num(b.get("l")) for b in window]
    highs=[v for v in highs if v is not None]
    lows=[v for v in lows if v is not None]
    mfe=((max(highs)/price-1.0)*100.0) if highs else None
    mae=((min(lows)/price-1.0)*100.0) if lows else None
    return mfe,mae


def _resolve_rows(rows, day):
    by_symbol = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").upper().strip()
        created = _parse_dt(row.get("timestamp"))
        if not symbol or created is None:
            continue
        by_symbol.setdefault(symbol, []).append(row)

    session_end = datetime.combine(day, dtime(16, 1), tzinfo=ET).astimezone(timezone.utc)

    for symbol, symbol_rows in by_symbol.items():
        earliest = min(_parse_dt(r["timestamp"]) for r in symbol_rows)
        try:
            bars = _fetch_symbol_bars(
                symbol,
                earliest - timedelta(minutes=2),
                session_end,
            )
        except Exception as exc:
            print(f"WARN: could not score {symbol}: {exc}")
            continue

        for row in symbol_rows:
            created = _parse_dt(row.get("timestamp"))
            price = _num(row.get("price"))
            if created is None or price is None:
                continue
            outcomes = row.setdefault("outcomes", {})

            for mins in (15, 30, 60):
                key = f"return_{mins}m_pct"
                if outcomes.get(key) is not None:
                    continue
                target_time = created + timedelta(minutes=mins)
                if target_time > session_end:
                    continue
                close = _price_at_or_after(bars, target_time)
                if close is not None:
                    outcomes[key] = round((close / price - 1.0) * 100.0, 3)
            if outcomes.get("return_60m_pct") is not None:
                outcomes["resolved_60m"] = True

            if "target1_first_touch" not in outcomes:
                touch = _first_touch(
                    bars,
                    row.get("target1"),
                    row.get("stop"),
                    created,
                )
                if touch:
                    outcomes["target1_first_touch"] = touch

            if row.get("repeat_bounce_plan_available"):
                if "repeat_bounce_target1_first_touch" not in outcomes:
                    touch = _first_touch(
                        bars,
                        row.get("repeat_bounce_target1"),
                        row.get("repeat_bounce_stop"),
                        created,
                    )
                    if touch:
                        outcomes["repeat_bounce_target1_first_touch"] = touch
                if "repeat_bounce_target2_first_touch" not in outcomes:
                    touch2 = _first_touch(
                        bars,
                        row.get("repeat_bounce_target2"),
                        row.get("repeat_bounce_stop"),
                        created,
                    )
                    if touch2:
                        outcomes["repeat_bounce_target2_first_touch"] = touch2

                for mins in (30,60):
                    if created + timedelta(minutes=mins) <= session_end:
                        mfe,mae=_window_excursions(bars,created,price,mins)
                        if mfe is not None:
                            outcomes[f"repeat_bounce_mfe_{mins}m_pct"]=round(mfe,3)
                        if mae is not None:
                            outcomes[f"repeat_bounce_mae_{mins}m_pct"]=round(mae,3)

                ref_peak=_num(row.get("bounce_reference_peak"))
                if ref_peak is not None and created + timedelta(minutes=60) <= session_end:
                    within60=[]
                    for bar in bars:
                        dt=_bar_dt(bar)
                        if dt is not None and created < dt <= created + timedelta(minutes=60):
                            within60.append(bar)
                    highs=[_num(b.get("h")) for b in within60]
                    highs=[v for v in highs if v is not None]
                    if highs:
                        outcomes["repeat_bounce_reference_peak_reclaimed_60m"]=bool(max(highs)>=ref_peak)

            if int(row.get("bounce_count") or 0)>=2:
                for mins in (30,60):
                    if created + timedelta(minutes=mins) <= session_end:
                        mfe,mae=_window_excursions(bars,created,price,mins)
                        if mae is not None:
                            outcomes[f"post_bounce_max_drop_{mins}m_pct"]=round(mae,3)
                        if mfe is not None:
                            outcomes[f"post_bounce_max_rise_{mins}m_pct"]=round(mfe,3)
    return rows



def _fetch_daily_bars(symbols, start, end):
    """Prefer Tradier daily history; use Alpaca SIP only for unresolved symbols."""
    symbols = sorted({str(symbol).upper().strip() for symbol in symbols if symbol})
    result = {symbol: [] for symbol in symbols}

    if TRADIER_TOKEN:
        for symbol in symbols:
            try:
                bars = _tradier_retry(
                    get_history_bars,
                    symbol,
                    TRADIER_TOKEN,
                    start,
                    end,
                    "daily",
                ) or []
                if bars:
                    result[symbol] = bars
            except Exception as exc:
                print(f"WARN Tradier Analyzer daily outcome bars {symbol}: {exc}")

    missing = [symbol for symbol in symbols if not result.get(symbol)]
    if not missing or not API_KEY or not API_SECRET:
        return result

    for offset in range(0, len(missing), 40):
        chunk = missing[offset:offset + 40]
        if not chunk:
            continue
        page_token = None
        while True:
            q = {
                "symbols": ",".join(chunk),
                "timeframe": "1Day",
                "start": _iso(start),
                "end": _iso(end),
                "limit": 10000,
                "adjustment": "raw",
                "feed": "sip",
                "sort": "asc",
            }
            if page_token:
                q["page_token"] = page_token
            payload = _request_json(
                f"{DATA_BASE}/v2/stocks/bars?{urllib.parse.urlencode(q)}"
            )
            for symbol, bars in (payload.get("bars") or {}).items():
                if bars:
                    result.setdefault(str(symbol).upper(), []).extend(bars or [])
            page_token = payload.get("next_page_token")
            if not page_token:
                break
    return result

def _daily_bar_date(bar):
    dt = _bar_dt(bar)
    if dt is None:
        return None
    return dt.astimezone(ET).date()


def _resolve_trading_day_returns(row, daily_bars):
    created = _parse_dt(row.get("timestamp"))
    price = _num(row.get("price"))
    if created is None or price is None or price <= 0:
        return False
    signal_day = created.astimezone(ET).date()
    future = []
    for bar in daily_bars or []:
        bar_day = _daily_bar_date(bar)
        close = _num(bar.get("c"))
        if bar_day is not None and bar_day > signal_day and close is not None and close > 0:
            future.append((bar_day, bar))
    future.sort(key=lambda item: item[0])

    outcomes = row.setdefault("outcomes", {})
    changed = False
    for sessions in (1, 3, 5, 20, 60):
        key = f"return_{sessions}d_pct"
        if outcomes.get(key) is not None or len(future) < sessions:
            continue
        close = _num(future[sessions - 1][1].get("c"))
        if close is None:
            continue
        outcomes[key] = round((close / price - 1.0) * 100.0, 3)
        outcomes[f"resolved_{sessions}d"] = True
        changed = True

    if (
        row.get("timeframe_score_version") == TIMEFRAME_SCORE_VERSION
        and "swing_first_event_5d" not in outcomes
        and len(future) >= SWING_HORIZON_SESSIONS
    ):
        path = resolve_swing_path_from_bars(
            price,
            [bar for _day, bar in future[:SWING_HORIZON_SESSIONS]],
        )
        for key, value in path.items():
            outcomes[key] = value
        if path:
            changed = True
    return changed


def _resolve_multiday_history():
    """Backfill due swing/longer-term outcomes across recent prediction files."""
    if not OUT_DIR.exists():
        return 0
    paths = sorted(OUT_DIR.glob("predictions_*.json"))[-100:]
    payloads = {}
    pending = []
    earliest = None
    for path in paths:
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(rows, list):
            continue
        payloads[path] = rows
        for row in rows:
            if row.get("timeframe_score_version") != TIMEFRAME_SCORE_VERSION:
                continue
            outcomes = row.get("outcomes") or {}
            closes_done = all(
                outcomes.get(f"return_{sessions}d_pct") is not None
                for sessions in (1, 3, 5, 20, 60)
            )
            path_done = "swing_first_event_5d" in outcomes
            if closes_done and path_done:
                continue
            created = _parse_dt(row.get("timestamp"))
            symbol = str(row.get("symbol") or "").upper().strip()
            if created is None or not symbol:
                continue
            pending.append((path, row))
            earliest = created if earliest is None else min(earliest, created)

    if not pending or earliest is None:
        return 0

    symbols = {str(row.get("symbol") or "").upper().strip() for _path, row in pending}
    end = datetime.now(timezone.utc)
    bars_by_symbol = _fetch_daily_bars(
        symbols,
        earliest - timedelta(days=3),
        end,
    )

    changed_paths = set()
    resolved_fields = 0
    for path, row in pending:
        before = len(row.get("outcomes") or {})
        symbol = str(row.get("symbol") or "").upper().strip()
        if _resolve_trading_day_returns(row, bars_by_symbol.get(symbol) or []):
            changed_paths.add(path)
            after = len(row.get("outcomes") or {})
            resolved_fields += max(0, after - before)

    for path in changed_paths:
        path.write_text(json.dumps(payloads[path], indent=2), encoding="utf-8")
    return resolved_fields


def _bucket(value):
    value = _num(value)
    if value is None:
        return None
    if value >= 80:
        return "80-100"
    if value >= 65:
        return "65-79"
    if value >= 50:
        return "50-64"
    return "0-49"


def _horizon_stats(values):
    if not values:
        return {
            "n": 0,
            "higher_rate": None,
            "hit_3pct_rate": None,
            "avg_return_pct": None,
            "median_return_pct": None,
        }
    return {
        "n": len(values),
        "higher_rate": round(sum(v > 0 for v in values) / len(values) * 100.0, 1),
        "hit_3pct_rate": round(sum(v >= 3 for v in values) / len(values) * 100.0, 1),
        "avg_return_pct": round(sum(values) / len(values), 3),
        "median_return_pct": round(statistics.median(values), 3),
    }


def _row_market_session(row):
    explicit = str((row or {}).get("market_session") or "").lower().strip()
    if explicit in {"regular", "regular_intraday"}:
        return "regular"
    if explicit in {"premarket", "afterhours", "closed"}:
        return explicit

    dt = _parse_dt((row or {}).get("timestamp"))
    if dt is None:
        return "unknown"
    et = dt.astimezone(ET)
    if et.weekday() >= 5:
        return "closed"
    minute = et.hour * 60 + et.minute
    if 4 * 60 <= minute < 9 * 60 + 30:
        return "premarket"
    if 9 * 60 + 30 <= minute < 16 * 60:
        return "regular"
    if 16 * 60 <= minute < 20 * 60:
        return "afterhours"
    return "closed"


def _independent_calibration_rows(rows):
    """Keep one regular-session observation per ticker per ET clock hour.

    Raw five-minute rows remain durable for lifecycle/path analysis. Intraday
    calibration excludes pre/post-market, overnight and weekend observations,
    and keys by New York time so UTC midnight cannot split one trading session.
    """
    chosen = {}
    ordered = sorted(rows, key=lambda row: str(row.get("timestamp") or ""))
    for row in ordered:
        if _row_market_session(row) != "regular":
            continue
        symbol = str(row.get("symbol") or "").upper().strip()
        dt = _parse_dt(row.get("timestamp"))
        if not symbol or dt is None:
            continue
        et = dt.astimezone(ET)
        key = (symbol, et.date().isoformat(), et.hour)
        if key not in chosen:
            chosen[key] = row
    return list(chosen.values())


def _timeframe_daily_calibration_rows(rows):
    """Keep the latest regular-session observation per ticker per ET day.

    Swing and longer-term outcomes share the same future path for every
    same-day refresh. Counting hourly copies would inflate n. The latest
    regular-session snapshot is also the closest live analogue to the EOD
    historical timeframe study.
    """
    chosen = {}
    ordered = sorted(rows, key=lambda row: str(row.get("timestamp") or ""))
    for row in ordered:
        if _row_market_session(row) != "regular":
            continue
        symbol = str(row.get("symbol") or "").upper().strip()
        dt = _parse_dt(row.get("timestamp"))
        if not symbol or dt is None:
            continue
        et = dt.astimezone(ET)
        key = (symbol, et.date().isoformat())
        chosen[key] = row
    return list(chosen.values())


def _calibrate(rows, score_field):
    groups = {}
    for row in rows:
        bucket = _bucket(row.get(score_field))
        if bucket is None:
            continue
        g = groups.setdefault(
            bucket,
            {
                "15m": [],
                "30m": [],
                "60m": [],
                "target_wins": 0,
                "target_losses": 0,
                "target_ambiguous": 0,
            },
        )
        outcomes = row.get("outcomes") or {}
        for mins in (15, 30, 60):
            value = _num(outcomes.get(f"return_{mins}m_pct"))
            if value is not None:
                g[f"{mins}m"].append(value)
        touch = outcomes.get("target1_first_touch")
        if touch == "target":
            g["target_wins"] += 1
        elif touch == "stop":
            g["target_losses"] += 1
        elif touch == "ambiguous":
            # Same-bar order is unknowable in OHLC data. Count it in the
            # conservative lower-bound denominator as a failure rather than
            # discarding a volatile observation and inflating hit rates.
            g["target_losses"] += 1
            g["target_ambiguous"] += 1

    out = {}
    for bucket, g in groups.items():
        s15 = _horizon_stats(g["15m"])
        s30 = _horizon_stats(g["30m"])
        s60 = _horizon_stats(g["60m"])
        target_n = g["target_wins"] + g["target_losses"]
        out[bucket] = {
            # Backward-compatible headline fields.
            "n": s60["n"],
            "higher_60m_rate": s60["higher_rate"],
            "hit_3pct_60m_rate": s60["hit_3pct_rate"],
            "avg_return_60m_pct": s60["avg_return_pct"],
            "median_return_60m_pct": s60["median_return_pct"],
            # Richer calibration for deciding whether a score is genuinely useful.
            "return_15m": s15,
            "return_30m": s30,
            "return_60m": s60,
            "target_stop_n": target_n,
            "target_ambiguous_count": g["target_ambiguous"],
            "target_before_stop_rate": (
                round(g["target_wins"] / target_n * 100.0, 1)
                if target_n else None
            ),
            "target_ambiguity_policy": (
                "same-bar target+stop counted as failure for conservative calibration"
            ),
        }
    return out


def _calibration_stage(resolved_60m):
    n = int(resolved_60m or 0)
    if n < 30:
        return {
            "stage": "COLLECTING",
            "next_threshold": 30,
            "remaining": 30 - n,
            "message": "Not enough resolved observations to tune score weights yet.",
        }
    if n < 100:
        return {
            "stage": "EARLY READ",
            "next_threshold": 100,
            "remaining": 100 - n,
            "message": "Enough for an early read, but keep weights unchanged unless separation is very clear.",
        }
    if n < 300:
        return {
            "stage": "USEFUL",
            "next_threshold": 300,
            "remaining": 300 - n,
            "message": "Useful calibration sample; score-band comparisons are becoming meaningful.",
        }
    return {
        "stage": "STRONGER SAMPLE",
        "next_threshold": None,
        "remaining": 0,
        "message": "Several hundred resolved observations are available for evidence-based tuning.",
    }


def _all_rows():
    rows = []
    if not OUT_DIR.exists():
        return rows
    files = sorted(OUT_DIR.glob("predictions_*.json"))[-100:]
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                rows.extend(payload)
        except Exception:
            continue
    return rows



def _swing_research_flag_calibration(rows):
    """Calibrate only context-comparable live exploratory Swing samples.

    Historical research used end-of-day observations. Live Analyzer matches
    occur intraday, so they are never direct parity. To reduce selection drift,
    only regular-session matches that also pass the historical replay's basic
    price/day-move/dollar-volume universe proxy are counted here.
    """
    chosen = {}
    excluded_context = 0
    ordered = sorted(rows, key=lambda row: str(row.get("timestamp") or ""))
    for row in ordered:
        if row.get("swing_research_flag_version") != SWING_RESEARCH_FLAG_VERSION:
            continue
        flag_ids = row.get("swing_research_flag_ids") or []
        if (
            row.get("swing_research_sampling_context") != "regular_intraday"
            or row.get("swing_research_universe_proxy_pass") is not True
        ):
            if flag_ids:
                excluded_context += 1
            continue
        symbol = str(row.get("symbol") or "").upper().strip()
        dt = _parse_dt(row.get("timestamp"))
        if not symbol or dt is None:
            continue
        for flag_id in flag_ids:
            flag_id = str(flag_id or "").strip()
            if not flag_id:
                continue
            key = (flag_id, symbol, dt.astimezone(ET).date().isoformat())
            if key not in chosen:
                chosen[key] = row

    groups = {}
    for (flag_id, _symbol, _day), row in chosen.items():
        g = groups.setdefault(
            flag_id,
            {"signals": 0, "resolved": 0, "wins": 0, "mfe": [], "mae": []},
        )
        g["signals"] += 1
        outcomes = row.get("outcomes") or {}
        label = outcomes.get("swing_target_before_stop_5d")
        if label in (0, 1):
            g["resolved"] += 1
            g["wins"] += int(label)
        mfe = _num(outcomes.get("swing_mfe_5d_pct"))
        mae = _num(outcomes.get("swing_mae_5d_pct"))
        if mfe is not None:
            g["mfe"].append(mfe)
        if mae is not None:
            g["mae"].append(mae)

    out = {}
    for flag_id, g in sorted(groups.items()):
        resolved = int(g["resolved"])
        if resolved < 10:
            stage = "COLLECTING"
            next_threshold = 10
        elif resolved < 30:
            stage = "EARLY READ"
            next_threshold = 30
        elif resolved < 100:
            stage = "USEFUL"
            next_threshold = 100
        else:
            stage = "STRONGER SAMPLE"
            next_threshold = None
        out[flag_id] = {
            "signals": int(g["signals"]),
            "resolved": resolved,
            "target_before_stop_rate_pct": (
                round(g["wins"] / resolved * 100.0, 1)
                if resolved else None
            ),
            "avg_mfe_5d_pct": (
                round(sum(g["mfe"]) / len(g["mfe"]), 3)
                if g["mfe"] else None
            ),
            "avg_mae_5d_pct": (
                round(sum(g["mae"]) / len(g["mae"]), 3)
                if g["mae"] else None
            ),
            "stage": stage,
            "next_threshold": next_threshold,
            "sampling": (
                "first regular-session historical-universe-proxy match "
                "per flag/ticker/day"
            ),
            "context": "intraday_exploratory",
            "direct_historical_parity": False,
            "excluded_context_rows": excluded_context,
        }
    return out

def _repeat_bounce_calibration(rows):
    candidates=[
        row for row in rows
        if row.get("repeat_bounce_plan_available")
        and row.get("preferred_plan")=="repeat_bounce"
    ]
    entry=[
        row for row in candidates
        if row.get("plan_status")=="ENTRY AVAILABLE"
    ]
    touches=[
        row for row in entry
        if (row.get("outcomes") or {}).get("repeat_bounce_target1_first_touch") in {"target","stop","ambiguous"}
    ]
    wins=[
        row for row in touches
        if (row.get("outcomes") or {}).get("repeat_bounce_target1_first_touch")=="target"
    ]
    mfe30=[
        _num((row.get("outcomes") or {}).get("repeat_bounce_mfe_30m_pct"))
        for row in entry
    ]
    mfe30=[v for v in mfe30 if v is not None]
    mae30=[
        _num((row.get("outcomes") or {}).get("repeat_bounce_mae_30m_pct"))
        for row in entry
    ]
    mae30=[v for v in mae30 if v is not None]
    by_number={}
    for row in entry:
        number=int(row.get("repeat_bounce_plan_number") or 0)
        if not number:
            continue
        g=by_number.setdefault(number,{"signals":0,"wins":0,"losses":0})
        g["signals"]+=1
        touch=(row.get("outcomes") or {}).get("repeat_bounce_target1_first_touch")
        if touch=="target":g["wins"]+=1
        elif touch in {"stop","ambiguous"}:g["losses"]+=1
    for number,g in by_number.items():
        resolved=g["wins"]+g["losses"]
        g["target_before_stop_rate"]=round(g["wins"]/resolved*100.0,1) if resolved else None

    return {
        "candidate_rows":len(candidates),
        "entry_signals":len(entry),
        "resolved_target_stop":len(touches),
        "ambiguous_count":sum(
            (row.get("outcomes") or {}).get("repeat_bounce_target1_first_touch")=="ambiguous"
            for row in touches
        ),
        "target_before_stop_rate":round(len(wins)/len(touches)*100.0,1) if touches else None,
        "ambiguity_policy":"same-bar target+stop counted as failure",
        "avg_mfe_30m_pct":round(sum(mfe30)/len(mfe30),3) if mfe30 else None,
        "avg_mae_30m_pct":round(sum(mae30)/len(mae30),3) if mae30 else None,
        "by_bounce_number":{str(k):v for k,v in sorted(by_number.items())},
    }


def _mature_bounce_failure_calibration(rows):
    mature=[row for row in rows if int(row.get("bounce_count") or 0)>=2]
    drops=[
        _num((row.get("outcomes") or {}).get("post_bounce_max_drop_60m_pct"))
        for row in mature
    ]
    drops=[v for v in drops if v is not None]
    rises=[
        _num((row.get("outcomes") or {}).get("post_bounce_max_rise_60m_pct"))
        for row in mature
    ]
    rises=[v for v in rises if v is not None]
    return {
        "observations":len(mature),
        "resolved_60m_excursions":min(len(drops),len(rises)),
        "avg_max_drop_60m_pct":round(sum(drops)/len(drops),3) if drops else None,
        "median_max_drop_60m_pct":round(statistics.median(drops),3) if drops else None,
        "avg_max_rise_60m_pct":round(sum(rises)/len(rises),3) if rises else None,
        "drop_5pct_rate":round(sum(v<=-5 for v in drops)/len(drops)*100.0,1) if drops else None,
        "drop_10pct_rate":round(sum(v<=-10 for v in drops)/len(drops)*100.0,1) if drops else None,
    }



def _timeframe_calibrate(rows, score_field, outcome_field):
    groups = {}
    for row in rows:
        if row.get("timeframe_score_version") != TIMEFRAME_SCORE_VERSION:
            continue
        bucket = _bucket(row.get(score_field))
        value = _num((row.get("outcomes") or {}).get(outcome_field))
        if bucket is None or value is None:
            continue
        g = groups.setdefault(bucket, [])
        g.append(value)
    out = {}
    for bucket, values in groups.items():
        out[bucket] = {
            "n": len(values),
            "higher_rate": round(sum(value > 0 for value in values) / len(values) * 100.0, 1),
            "avg_return_pct": round(sum(values) / len(values), 3),
            "median_return_pct": round(statistics.median(values), 3),
        }
    return out


def _timeframe_best_fit_calibration(intraday_rows, daily_rows=None):
    daily_rows = intraday_rows if daily_rows is None else daily_rows
    specs = {
        "INTRADAY": ("return_60m_pct", "60m", intraday_rows),
        "SWING": ("return_5d_pct", "5 trading days", daily_rows),
        "LONGER-TERM": ("return_20d_pct", "20 trading days", daily_rows),
    }
    out = {}
    for fit, (field, horizon, source_rows) in specs.items():
        selected = [
            row for row in source_rows
            if row.get("timeframe_score_version") == TIMEFRAME_SCORE_VERSION
            and row.get("timeframe_best_fit") == fit
        ]
        values = [
            _num((row.get("outcomes") or {}).get(field))
            for row in selected
        ]
        values = [value for value in values if value is not None]
        out[fit] = {
            "signals": len(selected),
            "resolved": len(values),
            "horizon": horizon,
            "higher_rate": (
                round(sum(value > 0 for value in values) / len(values) * 100.0, 1)
                if values else None
            ),
            "avg_return_pct": (
                round(sum(values) / len(values), 3) if values else None
            ),
            "median_return_pct": (
                round(statistics.median(values), 3) if values else None
            ),
        }
    return out


def _timeframe_learning_progress(intraday_rows, daily_rows=None):
    daily_rows = intraday_rows if daily_rows is None else daily_rows
    intraday_tf = [
        row for row in intraday_rows
        if row.get("timeframe_score_version") == TIMEFRAME_SCORE_VERSION
    ]
    daily_tf = [
        row for row in daily_rows
        if row.get("timeframe_score_version") == TIMEFRAME_SCORE_VERSION
    ]
    counts = {
        "intraday": sum(
            _num((row.get("outcomes") or {}).get("return_60m_pct")) is not None
            for row in intraday_tf
        ),
        "swing": sum(
            _num((row.get("outcomes") or {}).get("return_5d_pct")) is not None
            for row in daily_tf
        ),
        "long_term": sum(
            _num((row.get("outcomes") or {}).get("return_20d_pct")) is not None
            for row in daily_tf
        ),
    }
    return {key: _calibration_stage(value) for key, value in counts.items()}


def _write_calibration():
    all_rows = _all_rows()
    feature_rows = [
        row for row in all_rows
        if row.get("feature_version") == ANALYZER_FEATURE_VERSION
    ]
    legacy_rows_excluded = len(all_rows) - len(feature_rows)
    rows = [
        row for row in feature_rows
        if row.get("decision_score_version") == DECISION_SCORE_VERSION
    ]
    legacy_decision_rows_excluded = len(feature_rows) - len(rows)
    calibration_rows = _independent_calibration_rows(rows)
    timeframe_daily_rows = _timeframe_daily_calibration_rows(rows)
    resolved = [
        row for row in calibration_rows
        if _num((row.get("outcomes") or {}).get("return_60m_pct")) is not None
    ]
    touches = [
        row for row in calibration_rows
        if (row.get("outcomes") or {}).get("target1_first_touch") in {
            "target", "stop", "ambiguous"
        }
    ]
    target_wins = [
        row for row in touches
        if (row.get("outcomes") or {}).get("target1_first_touch") == "target"
    ]

    entry_signals = [
        row for row in calibration_rows
        if row.get("plan_status") == "ENTRY AVAILABLE"
    ]
    entry_signal_touches = [
        row for row in entry_signals
        if (row.get("outcomes") or {}).get("target1_first_touch") in {
            "target", "stop", "ambiguous"
        }
    ]
    entry_signal_wins = [
        row for row in entry_signal_touches
        if (row.get("outcomes") or {}).get("target1_first_touch") == "target"
    ]
    entry_signal_60m = [
        _num((row.get("outcomes") or {}).get("return_60m_pct"))
        for row in entry_signals
        if _num((row.get("outcomes") or {}).get("return_60m_pct")) is not None
    ]

    payload = {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "feature_version": ANALYZER_FEATURE_VERSION,
        "decision_score_version": DECISION_SCORE_VERSION,
        "timeframe_score_version": TIMEFRAME_SCORE_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "prediction_rows": len(rows),
        "legacy_prediction_rows_excluded": legacy_rows_excluded,
        "legacy_decision_rows_excluded": legacy_decision_rows_excluded,
        "calibration_rows": len(calibration_rows),
        "calibration_sampling": "regular-session one observation per ticker per ET hour",
        "timeframe_calibration_sampling": (
            "Intraday: regular-session ticker-hour; Swing/Longer-term: "
            "latest regular-session observation per ticker per ET day"
        ),
        "timeframe_daily_rows": len(timeframe_daily_rows),
        "resolved_60m": len(resolved),
        "calibration_ready": len(resolved) >= 30,
        "calibration_progress": _calibration_stage(len(resolved)),
        "potential_calibration": _calibrate(calibration_rows, "potential_score"),
        "entry_calibration": _calibrate(calibration_rows, "entry_readiness"),
        "evidence_calibration": _calibrate(calibration_rows, "evidence_strength"),
        "timeframe_calibration": {
            "intraday_60m": _timeframe_calibrate(
                calibration_rows, "timeframe_intraday_score", "return_60m_pct"
            ),
            "swing_3d": _timeframe_calibrate(
                timeframe_daily_rows, "timeframe_swing_score", "return_3d_pct"
            ),
            "swing_5d": _timeframe_calibrate(
                timeframe_daily_rows, "timeframe_swing_score", "return_5d_pct"
            ),
            "long_term_20d": _timeframe_calibrate(
                timeframe_daily_rows, "timeframe_long_term_score", "return_20d_pct"
            ),
            "long_term_60d": _timeframe_calibrate(
                timeframe_daily_rows, "timeframe_long_term_score", "return_60d_pct"
            ),
        },
        "timeframe_best_fit_calibration": _timeframe_best_fit_calibration(
            calibration_rows, timeframe_daily_rows
        ),
        "timeframe_learning_progress": _timeframe_learning_progress(
            calibration_rows, timeframe_daily_rows
        ),
        "swing_research_flag_version": SWING_RESEARCH_FLAG_VERSION,
        "swing_research_flag_calibration": _swing_research_flag_calibration(
            feature_rows
        ),
        "target_before_stop_rate": (
            round(len(target_wins) / len(touches) * 100.0, 1)
            if touches else None
        ),
        "target_stop_resolved": len(touches),
        "target_stop_ambiguous": sum(
            (row.get("outcomes") or {}).get("target1_first_touch") == "ambiguous"
            for row in touches
        ),
        "target_ambiguity_policy": "same-bar target+stop counted as failure",
        "repeat_bounce_calibration": _repeat_bounce_calibration(calibration_rows),
        "mature_bounce_failure_calibration": _mature_bounce_failure_calibration(calibration_rows),
        "entry_signal_calibration": {
            "signals": len(entry_signals),
            "resolved_target_stop": len(entry_signal_touches),
            "ambiguous_count": sum(
                (row.get("outcomes") or {}).get("target1_first_touch") == "ambiguous"
                for row in entry_signal_touches
            ),
            "target_before_stop_rate": (
                round(len(entry_signal_wins) / len(entry_signal_touches) * 100.0, 1)
                if entry_signal_touches else None
            ),
            "ambiguity_policy": "same-bar target+stop counted as failure",
            "resolved_60m": len(entry_signal_60m),
            "higher_60m_rate": (
                round(sum(v > 0 for v in entry_signal_60m) / len(entry_signal_60m) * 100.0, 1)
                if entry_signal_60m else None
            ),
            "avg_return_60m_pct": (
                round(sum(entry_signal_60m) / len(entry_signal_60m), 3)
                if entry_signal_60m else None
            ),
        },
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "calibration.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    print(
        f"Analyzer calibration: rows={len(rows)} legacy_feature={legacy_rows_excluded} "
        f"legacy_decision={legacy_decision_rows_excluded} "
        f"resolved60={len(resolved)} ready={payload['calibration_ready']}"
    )


def main():
    day = _target_date()
    path = OUT_DIR / f"predictions_{day.isoformat()}.json"
    if path.exists():
        rows = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(rows, list) and rows:
            rows = _resolve_rows(rows, day)
            path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
            print(f"Scored Analyzer predictions: {path}")
    else:
        print(f"No Analyzer prediction file for {day}; calibration will still rebuild.")

    resolved_multiday = _resolve_multiday_history()
    if resolved_multiday:
        print(f"Resolved {resolved_multiday} timeframe outcome fields across recent history.")

    _write_calibration()


if __name__ == "__main__":
    main()
