"""Leakage-aware historical replay for Analyzer timeframe intelligence.

This replay accelerates learning without pretending historical data is richer
than it is. Intraday validation remains in historical_scanner_replay.py because
that pipeline has 5-minute bars. This module focuses on Swing and Longer-term
scores using end-of-day point-in-time data, the same live weighting helper, and
optional SEC facts filtered by filing date.

Historical news/catalyst text is deliberately left neutral instead of using
today's news retroactively.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import historical_patterns as hp
import stock_scanner as ss
from analyzer_v2_integration import (
    _clamp,
    _fundamental_quality,
    _fundamental_snapshot,
    _sector_from_sic,
    _timeframe_horizon_scores,
)
from historical_scanner_replay import (
    _daily_index,
    _fetch_tradier_daily_history,
    _num,
    select_daily_universe,
)
from scanner_behavior import multi_session_behavior_features
from timeframe_targets import (
    SWING_HORIZON_SESSIONS,
    SWING_STOP_PCT,
    SWING_TARGET_PCT,
    resolve_swing_path_from_bars,
)


ET = ZoneInfo("America/New_York")
REPLAY_VERSION = "historical-timeframe-replay-v2-path-target"
TIMEFRAME_SCORE_VERSION = "timeframe-fit-v1"
DEFAULT_REPLAY_DAYS = int(os.environ.get("TIMEFRAME_REPLAY_TRADING_DAYS", "240") or 240)
DEFAULT_STRIDE = int(os.environ.get("TIMEFRAME_REPLAY_STRIDE_DAYS", "5") or 5)
DEFAULT_UNIVERSE_SIZE = int(os.environ.get("TIMEFRAME_REPLAY_UNIVERSE_SIZE", "250") or 250)
DEFAULT_CANDIDATES = int(os.environ.get("TIMEFRAME_REPLAY_CANDIDATES_PER_DAY", "25") or 25)
DEFAULT_LOOKBACK_CALENDAR_DAYS = int(
    os.environ.get("TIMEFRAME_REPLAY_LOOKBACK_CALENDAR_DAYS", "1500") or 1500
)
OUTPUT_PATH = Path(
    os.environ.get(
        "TIMEFRAME_REPLAY_OUTPUT_PATH",
        "timeframe_replay/timeframe_historical_replay.json",
    )
)
SCANNER_REPLAY_PATH = Path(
    os.environ.get(
        "SCANNER_REPLAY_OUTPUT_PATH",
        "outcome_reports/outcomes_historical_replay.json",
    )
)
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "").strip()
SEC_BASE = "https://data.sec.gov"


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _pct(a, b):
    a = _num(a)
    b = _num(b)
    if a is None or b is None or b == 0:
        return None
    return (a / b - 1.0) * 100.0


def _load_seed_symbols():
    if not SCANNER_REPLAY_PATH.exists():
        raise RuntimeError(
            "Historical timeframe replay expects the scanner replay dataset to "
            "run first so both backfills use the same screened symbol universe."
        )
    payload = json.loads(SCANNER_REPLAY_PATH.read_text(encoding="utf-8"))
    replay = payload.get("replay") or {}
    symbols = [
        str(symbol).upper().strip()
        for symbol in replay.get("union_symbol_list") or []
        if str(symbol).strip()
    ]
    if not symbols:
        symbols = sorted(
            {
                str(row.get("symbol") or "").upper().strip()
                for row in payload.get("observations") or []
                if str(row.get("symbol") or "").strip()
            }
        )
    if len(symbols) < 50:
        raise RuntimeError(
            f"Historical scanner replay exposed only {len(symbols)} usable symbols."
        )
    return symbols, payload


def _liquid_dates(daily_index):
    counts = Counter()
    for rows in daily_index.values():
        for day, _bar in rows:
            counts[day] += 1
    return sorted(day for day, count in counts.items() if count >= 40)


def _date_index(rows):
    return {day: idx for idx, (day, _bar) in enumerate(rows)}


def _trend_context(rows, idx):
    if idx < 10:
        return {"status": "limited", "trend_score": 50.0}
    current = rows[idx][1]
    current_price = _num(current.get("c"))
    history = [bar for _day, bar in rows[: idx + 1]]
    closes = [_num(bar.get("c")) for bar in history]
    if current_price is None or any(value is None for value in closes[-10:]):
        return {"status": "limited", "trend_score": 50.0}

    def trailing_return(sessions):
        if idx < sessions:
            return None
        base = _num(rows[idx - sessions][1].get("c"))
        if not base:
            return None
        return round((current_price / base - 1.0) * 100.0, 1)

    def moving_average(sessions):
        if len(closes) < sessions:
            return None
        vals = [value for value in closes[-sessions:] if value is not None]
        if len(vals) != sessions:
            return None
        return round(sum(vals) / sessions, 4)

    window_52 = history[-252:]
    highs = [_num(bar.get("h")) for bar in window_52]
    lows = [_num(bar.get("l")) for bar in window_52]
    highs = [value for value in highs if value is not None]
    lows = [value for value in lows if value is not None]
    high_52w = max(highs) if highs else None
    low_52w = min(lows) if lows else None

    context = {
        "status": "ok",
        "return_5d_pct": trailing_return(5),
        "return_20d_pct": trailing_return(20),
        "return_60d_pct": trailing_return(60),
        "return_120d_pct": trailing_return(120),
        "return_250d_pct": trailing_return(250),
        "ma_20": moving_average(20),
        "ma_50": moving_average(50),
        "ma_200": moving_average(200),
        "high_52w": high_52w,
        "low_52w": low_52w,
    }
    if high_52w:
        context["from_52w_high_pct"] = round((current_price / high_52w - 1.0) * 100.0, 1)
    if low_52w:
        context["above_52w_low_pct"] = round((current_price / low_52w - 1.0) * 100.0, 1)

    score = 50.0
    r20 = _num(context.get("return_20d_pct"))
    r60 = _num(context.get("return_60d_pct"))
    r120 = _num(context.get("return_120d_pct"))
    if r20 is not None:
        score += 10 if r20 >= 10 else 5 if r20 > 0 else -10 if r20 <= -10 else -4
    if r60 is not None:
        score += 12 if r60 >= 20 else 6 if r60 > 5 else -12 if r60 <= -15 else -4 if r60 < 0 else 0
    if r120 is not None:
        score += 10 if r120 >= 30 else 4 if r120 > 0 else -10 if r120 <= -20 else -3

    for sessions, points in ((20, 5), (50, 6), (200, 8)):
        ma = _num(context.get(f"ma_{sessions}"))
        if ma is not None:
            score += points if current_price >= ma else -points

    from_high = _num(context.get("from_52w_high_pct"))
    if from_high is not None:
        if from_high >= -10:
            score += 5
        elif from_high <= -50:
            score -= 10

    context["trend_score"] = round(_clamp(score), 1)
    return context


def _stair_context(rows, idx):
    current_day, current_bar = rows[idx]
    prior = [bar for _day, bar in rows[max(0, idx - 20) : idx]]
    current = {
        "date": current_day.isoformat(),
        "o": _num(current_bar.get("o")),
        "h": _num(current_bar.get("h")),
        "l": _num(current_bar.get("l")),
        "c": _num(current_bar.get("c")),
        "v": _num(current_bar.get("v")),
    }
    try:
        features = multi_session_behavior_features(
            prior,
            current_day=current,
            atr_pct=None,
        )
    except Exception:
        features = {}
    score = _num(features.get("stair_structure_score"))
    return round(score if score is not None else 50.0, 1), features


def _historical_context(symbol, rows, idx, day_pct, gap_pct, rvol):
    replay_day = rows[idx][0]
    cutoff_bars = [bar for _day, bar in rows[: idx + 1]]

    def local_fetch(_symbol, timeframe, start, end, limit=1000):
        if timeframe == "1Day":
            return cutoff_bars[-limit:], "historical-replay-point-in-time"
        return [], "historical-replay-no-intraday-analogs"

    hp._CACHE.clear()
    now = datetime(
        replay_day.year,
        replay_day.month,
        replay_day.day,
        16,
        0,
        tzinfo=ET,
    )
    result = hp.analyze_historical_patterns(
        symbol,
        now,
        day_pct,
        gap_pct,
        rvol,
        local_fetch,
        ET,
    )
    score = 50.0
    if result.get("status") == "ok":
        score += (_num(result.get("bias_score")) or 0.0) * 1.6
        next_day = _num(result.get("next_day_up_pct"))
        if next_day is not None:
            score += (next_day - 50.0) * 0.15
    return round(_clamp(score), 1), result


def _future_returns(rows, idx, entry_price):
    outcomes = {}
    for sessions in (1, 3, 5, 20, 60):
        future_idx = idx + sessions
        if future_idx >= len(rows):
            continue
        close = _num(rows[future_idx][1].get("c"))
        if close is not None and entry_price:
            outcomes[f"return_{sessions}d_pct"] = round(
                (close / entry_price - 1.0) * 100.0,
                3,
            )
    return outcomes


def _swing_path_outcomes(
    rows,
    idx,
    entry_price,
    *,
    target_pct=SWING_TARGET_PCT,
    stop_pct=SWING_STOP_PCT,
    horizon_sessions=SWING_HORIZON_SESSIONS,
):
    future_bars = [
        bar
        for _day, bar in rows[idx + 1 : idx + 1 + int(horizon_sessions)]
    ]
    return resolve_swing_path_from_bars(
        entry_price,
        future_bars,
        target_pct=target_pct,
        stop_pct=stop_pct,
        horizon_sessions=horizon_sessions,
    )

def _future_close_return(daily_index, symbol, replay_day, sessions):
    rows = daily_index.get(symbol) or []
    idx = _date_index(rows).get(replay_day)
    if idx is None or idx + sessions >= len(rows):
        return None
    entry = _num(rows[idx][1].get("c"))
    future = _num(rows[idx + sessions][1].get("c"))
    return _pct(future, entry)


def _daily_move_for(daily_index, symbol, replay_day):
    rows = daily_index.get(symbol) or []
    idx = _date_index(rows).get(replay_day)
    if idx is None or idx < 1:
        return None
    current = _num(rows[idx][1].get("c"))
    previous = _num(rows[idx - 1][1].get("c"))
    return _pct(current, previous)


def _market_context(daily_index, replay_day, sector_etf=None):
    moves = [
        _daily_move_for(daily_index, symbol, replay_day)
        for symbol in ("SPY", "QQQ", "IWM")
    ]
    moves = [value for value in moves if value is not None]
    broad = sum(moves) / len(moves) if moves else None
    sector = (
        _daily_move_for(daily_index, sector_etf, replay_day)
        if sector_etf
        else None
    )
    score = 50.0
    if broad is not None:
        score += _clamp(broad * 5.0, -10, 10)
    if sector is not None:
        score += _clamp(sector * 5.0, -10, 10)
    return {
        "score": round(_clamp(score), 1),
        "broad_market_avg_pct": round(broad, 3) if broad is not None else None,
        "sector_move_pct": round(sector, 3) if sector is not None else None,
        "sector_etf": sector_etf,
    }


def _sec_request(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": SEC_USER_AGENT,
            "Accept": "application/json",
        },
    )
    delay = 1.0
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt >= 4:
                raise
        except urllib.error.URLError:
            if attempt >= 4:
                raise
        time.sleep(delay)
        delay = min(8.0, delay * 2.0)
    return {}


def _sec_payloads(symbols):
    if not SEC_USER_AGENT:
        return {}, {"enabled": False, "reason": "SEC_USER_AGENT not configured in GitHub Actions"}

    mapping_raw = _sec_request("https://www.sec.gov/files/company_tickers.json")
    mapping = {}
    for item in (mapping_raw or {}).values():
        ticker = str(item.get("ticker") or "").upper().strip()
        cik = item.get("cik_str")
        if ticker and cik is not None:
            mapping[ticker] = int(cik)

    payloads = {}
    symbols = sorted(set(symbols))
    for index, symbol in enumerate(symbols, start=1):
        cik = mapping.get(symbol)
        if cik is None:
            payloads[symbol] = {"status": "unavailable", "reason": "CIK not found"}
            continue
        try:
            submissions = _sec_request(
                f"{SEC_BASE}/submissions/CIK{cik:010d}.json"
            )
            time.sleep(0.13)
            facts = _sec_request(
                f"{SEC_BASE}/api/xbrl/companyfacts/CIK{cik:010d}.json"
            )
            time.sleep(0.13)
            payloads[symbol] = {
                "status": "ok",
                "cik": cik,
                "submissions": submissions,
                "facts": facts,
            }
        except Exception as exc:
            payloads[symbol] = {
                "status": "unavailable",
                "reason": str(exc)[:160],
            }
        if index % 25 == 0 or index == len(symbols):
            print(f"SEC point-in-time cache: {index}/{len(symbols)} symbols.")
    return payloads, {
        "enabled": True,
        "symbols_requested": len(symbols),
        "symbols_loaded": sum(
            item.get("status") == "ok" for item in payloads.values()
        ),
    }


def _dilution_as_of(submissions, replay_day):
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    risk_forms = {"S-1", "S-3", "424B3", "424B4", "424B5", "EFFECT"}
    ages = []
    for form, date_text in zip(forms, dates):
        if str(form or "") not in risk_forms:
            continue
        try:
            filed = datetime.fromisoformat(str(date_text)).date()
        except Exception:
            continue
        if filed > replay_day:
            continue
        age = (replay_day - filed).days
        if 0 <= age <= 180:
            ages.append(age)
    recent_30 = [age for age in ages if age <= 30]
    if len(recent_30) >= 2:
        return "HIGH", len(ages)
    if recent_30:
        return "MODERATE", len(ages)
    if ages:
        return "LOW", len(ages)
    return "NONE FOUND", 0


def _sec_context_for(payload, replay_day):
    if not payload or payload.get("status") != "ok":
        return {
            "status": "unavailable",
            "dilution_risk": "UNKNOWN",
            "fundamentals": {"coverage_count": 0},
        }
    facts = payload.get("facts") or {}
    submissions = payload.get("submissions") or {}
    fundamentals = _fundamental_snapshot(facts, as_of=replay_day.isoformat())
    dilution, form_count = _dilution_as_of(submissions, replay_day)
    sector, sector_etf = _sector_from_sic(submissions.get("sic"))
    return {
        "status": "ok",
        "dilution_risk": dilution,
        "recent_offering_form_count": form_count,
        "sector": sector,
        "sector_etf": sector_etf,
        "fundamentals": fundamentals,
    }


def _bucket(score):
    score = _num(score)
    if score is None:
        return None
    if score >= 80:
        return "80-100"
    if score >= 65:
        return "65-79"
    if score >= 50:
        return "50-64"
    return "0-49"


def _calibration(rows, score_field, outcome_field):
    groups = {}
    for row in rows:
        score = _num(row.get(score_field))
        outcome = _num((row.get("outcomes") or {}).get(outcome_field))
        bucket = _bucket(score)
        if bucket is None or outcome is None:
            continue
        groups.setdefault(bucket, []).append(outcome)
    out = {}
    for bucket, values in groups.items():
        out[bucket] = {
            "n": len(values),
            "higher_rate": round(
                sum(value > 0 for value in values) / len(values) * 100.0,
                1,
            ),
            "avg_return_pct": round(sum(values) / len(values), 3),
            "median_return_pct": round(statistics.median(values), 3),
        }
    return out


def _directional_lift(rows, score_field, outcome_field):
    high = []
    low = []
    for row in rows:
        score = _num(row.get(score_field))
        outcome = _num((row.get("outcomes") or {}).get(outcome_field))
        if score is None or outcome is None:
            continue
        if score >= 72:
            high.append(outcome)
        elif score < 55:
            low.append(outcome)

    def stats(values):
        return {
            "n": len(values),
            "higher_rate": (
                round(sum(value > 0 for value in values) / len(values) * 100.0, 1)
                if values
                else None
            ),
            "avg_return_pct": (
                round(sum(values) / len(values), 3) if values else None
            ),
        }

    high_stats = stats(high)
    low_stats = stats(low)
    lift = None
    if high_stats["higher_rate"] is not None and low_stats["higher_rate"] is not None:
        lift = round(high_stats["higher_rate"] - low_stats["higher_rate"], 1)
    return {
        "high_score_72_plus": high_stats,
        "low_score_below_55": low_stats,
        "higher_rate_lift_pp": lift,
    }



def _binary_calibration(rows, score_field, outcome_field):
    groups = {}
    for row in rows:
        score = _num(row.get(score_field))
        outcome = (row.get("outcomes") or {}).get(outcome_field)
        bucket = _bucket(score)
        if bucket is None or outcome not in (0, 1):
            continue
        groups.setdefault(bucket, []).append(int(outcome))
    out = {}
    for bucket, values in groups.items():
        out[bucket] = {
            "n": len(values),
            "target_before_stop_rate_pct": round(
                sum(values) / len(values) * 100.0,
                1,
            ),
        }
    return out


def _binary_lift(rows, score_field, outcome_field):
    high = []
    low = []
    for row in rows:
        score = _num(row.get(score_field))
        outcome = (row.get("outcomes") or {}).get(outcome_field)
        if score is None or outcome not in (0, 1):
            continue
        if score >= 72:
            high.append(int(outcome))
        elif score < 55:
            low.append(int(outcome))

    def stats(values):
        return {
            "n": len(values),
            "target_before_stop_rate_pct": (
                round(sum(values) / len(values) * 100.0, 1)
                if values else None
            ),
        }

    high_stats = stats(high)
    low_stats = stats(low)
    lift = None
    if (
        high_stats["target_before_stop_rate_pct"] is not None
        and low_stats["target_before_stop_rate_pct"] is not None
    ):
        lift = round(
            high_stats["target_before_stop_rate_pct"]
            - low_stats["target_before_stop_rate_pct"],
            1,
        )
    return {
        "high_score_72_plus": high_stats,
        "low_score_below_55": low_stats,
        "target_rate_lift_pp": lift,
    }


def _candidate_rows(daily_index, replay_dates, universe_size, candidates_per_day):
    staged = []
    candidate_symbols = set()
    for day_number, replay_day in enumerate(replay_dates, start=1):
        universe, prior_metrics = select_daily_universe(
            daily_index,
            replay_day,
            universe_size,
        )
        rows = []
        for symbol in universe:
            history = daily_index.get(symbol) or []
            idx = _date_index(history).get(replay_day)
            if idx is None or idx < 20:
                continue
            current = history[idx][1]
            previous = history[idx - 1][1]
            price = _num(current.get("c"))
            prev_close = _num(previous.get("c"))
            volume = _num(current.get("v"))
            if price is None or prev_close is None or volume is None:
                continue
            if not 0.50 <= price <= 60.0:
                continue
            day_pct = _pct(price, prev_close)
            if day_pct is None or day_pct < 2.0:
                continue
            prior = prior_metrics.get(symbol) or {}
            median_volume = _num(prior.get("median_volume"))
            median_dollar = _num(prior.get("median_dollar"))
            if not median_volume or not median_dollar:
                continue
            rvol = volume / median_volume
            current_dollar = price * volume
            if current_dollar < 500_000:
                continue
            rank_score = (
                day_pct * 1.0
                + min(12.0, max(0.0, rvol - 1.0) * 3.0)
                + min(8.0, math.log10(max(1.0, current_dollar)) - 5.0)
            )
            rows.append(
                {
                    "symbol": symbol,
                    "replay_day": replay_day,
                    "idx": idx,
                    "price": price,
                    "day_pct": day_pct,
                    "rvol": rvol,
                    "current_dollar_volume": current_dollar,
                    "rank_score": rank_score,
                    "history": history,
                }
            )
        rows.sort(
            key=lambda row: (
                row["rank_score"],
                row["day_pct"],
                row["current_dollar_volume"],
            ),
            reverse=True,
        )
        chosen = rows[:candidates_per_day]
        staged.extend(chosen)
        candidate_symbols.update(row["symbol"] for row in chosen)
        print(
            f"Timeframe replay day {day_number}/{len(replay_dates)} "
            f"{replay_day}: candidates={len(chosen)} cumulative={len(staged)}"
        )
    return staged, candidate_symbols


def main():
    replay_days = max(60, min(DEFAULT_REPLAY_DAYS, 500))
    stride = max(1, min(DEFAULT_STRIDE, 20))
    universe_size = max(100, min(DEFAULT_UNIVERSE_SIZE, 600))
    candidates_per_day = max(5, min(DEFAULT_CANDIDATES, 50))

    seed_symbols, scanner_replay = _load_seed_symbols()
    benchmark_symbols = [
        "SPY", "QQQ", "IWM",
        "XLE", "XLV", "XLF", "XLK", "XLC", "XLU",
        "XLRE", "XLP", "XLY", "XLI", "XLB",
    ]
    fetch_symbols = sorted(set(seed_symbols + benchmark_symbols))

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=max(900, DEFAULT_LOOKBACK_CALENDAR_DAYS))
    token = (
        os.environ.get("TRADIER_ACCESS_TOKEN", "").strip()
        or os.environ.get("TRADIER_TOKEN", "").strip()
    )
    if not token:
        raise RuntimeError(
            "Historical timeframe replay requires TRADIER_ACCESS_TOKEN or TRADIER_TOKEN."
        )
    feed = "tradier_consolidated_daily"
    print(
        f"Historical timeframe replay {REPLAY_VERSION}: "
        f"symbols={len(fetch_symbols)} days={replay_days} stride={stride} "
        f"feed={feed}"
    )
    # Use Tradier here as well as in the intraday replay. The GitHub-hosted
    # Alpaca credentials are not guaranteed to carry historical SIP entitlement,
    # while the connected Tradier feed already powers the leakage-safe replay.
    daily_bars = _fetch_tradier_daily_history(
        fetch_symbols,
        token,
        start,
        now,
    )
    daily_index = _daily_index(daily_bars)
    dates = _liquid_dates(daily_index)
    if len(dates) < 340:
        raise RuntimeError(
            f"Only {len(dates)} liquid daily sessions were available; need >=340."
        )

    # Five future sessions guarantee resolved Swing labels. Longer-term 20d/60d
    # outcomes resolve wherever enough future history exists inside the same
    # point-in-time daily dataset.
    replay_pool = dates[:-5]
    requested = replay_pool[-replay_days:]
    replay_dates = requested[::stride]
    # Require at least 252 sessions of pre-history for stable trend context.
    first_allowed = dates[252]
    replay_dates = [day for day in replay_dates if day >= first_allowed]
    if len(replay_dates) < 12:
        raise RuntimeError("Insufficient replay dates after warmup/stride filtering.")

    staged, candidate_symbols = _candidate_rows(
        daily_index,
        replay_dates,
        universe_size,
        candidates_per_day,
    )
    sec_payloads, sec_status = _sec_payloads(candidate_symbols)

    observations = []
    for row in staged:
        symbol = row["symbol"]
        replay_day = row["replay_day"]
        history = row["history"]
        idx = row["idx"]
        current = history[idx][1]
        previous = history[idx - 1][1]
        price = row["price"]
        prev_close = _num(previous.get("c"))
        open_price = _num(current.get("o"))
        gap_pct = _pct(open_price, prev_close) or 0.0

        trend = _trend_context(history, idx)
        stair_score, stair = _stair_context(history, idx)
        history_score, analog = _historical_context(
            symbol,
            history,
            idx,
            row["day_pct"],
            gap_pct,
            row["rvol"],
        )

        sec = _sec_context_for(sec_payloads.get(symbol), replay_day)
        fundamental_score, fundamental_label, fundamental_reasons, fundamental_components, coverage = _fundamental_quality(sec)
        market = _market_context(
            daily_index,
            replay_day,
            sec.get("sector_etf"),
        )
        catalyst_score = 50.0
        swing_score, long_term_score = _timeframe_horizon_scores(
            _num(trend.get("trend_score")) or 50.0,
            stair_score,
            history_score,
            catalyst_score,
            market["score"],
            fundamental_score,
            coverage,
        )
        outcomes = _future_returns(history, idx, price)
        outcomes.update(_swing_path_outcomes(history, idx, price))
        spy_return_5d = _future_close_return(
            daily_index,
            "SPY",
            replay_day,
            SWING_HORIZON_SESSIONS,
        )
        if spy_return_5d is not None:
            outcomes["spy_return_5d_pct"] = round(spy_return_5d, 3)
            stock_return_5d = _num(outcomes.get("return_5d_pct"))
            if stock_return_5d is not None:
                outcomes["excess_return_vs_spy_5d_pct"] = round(
                    stock_return_5d - spy_return_5d,
                    3,
                )

        if max(swing_score, long_term_score) < 55 or abs(swing_score - long_term_score) < 4:
            best_fit = "MIXED SWING/LONG"
        elif swing_score > long_term_score:
            best_fit = "SWING"
        else:
            best_fit = "LONGER-TERM"

        observations.append(
            {
                "observation_id": f"timeframe-replay:{replay_day}:{symbol}",
                "observation_source": "historical_timeframe_replay",
                "replay_version": REPLAY_VERSION,
                "timeframe_score_version": TIMEFRAME_SCORE_VERSION,
                "as_of": f"{replay_day.isoformat()}T16:00:00-04:00",
                "symbol": symbol,
                "price": round(price, 4),
                "day_pct": round(row["day_pct"], 3),
                "gap_pct": round(gap_pct, 3),
                "relative_volume": round(row["rvol"], 3),
                "current_dollar_volume": round(row["current_dollar_volume"], 2),
                "swing_score": swing_score,
                "long_term_score": long_term_score,
                "best_fit_between_swing_long": best_fit,
                "trend_score": _num(trend.get("trend_score")),
                "stair_score": stair_score,
                "history_score": history_score,
                "catalyst_score": catalyst_score,
                "market_score": market["score"],
                "fundamental_score": fundamental_score,
                "fundamental_label": fundamental_label,
                "fundamental_coverage_count": coverage,
                "dilution_risk": sec.get("dilution_risk"),
                "sector": sec.get("sector"),
                "sector_etf": sec.get("sector_etf"),
                "trend_context": trend,
                "stair_context": {
                    key: value
                    for key, value in stair.items()
                    if key.startswith("stair_")
                },
                "historical_context": {
                    "status": analog.get("status"),
                    "bias_score": analog.get("bias_score"),
                    "next_day_up_pct": analog.get("next_day_up_pct"),
                    "sample_count": analog.get("sample_count"),
                },
                "fundamental_context": sec.get("fundamentals") or {},
                "fundamental_components": fundamental_components,
                "fundamental_reasons": fundamental_reasons,
                "market_context": market,
                "outcomes": outcomes,
                "replay_missing_features": [
                    "historical catalyst/news sentiment",
                    "intraday VWAP-reclaim contribution inside historical analog score",
                ],
            }
        )

    swing_3 = _calibration(observations, "swing_score", "return_3d_pct")
    swing_5 = _calibration(observations, "swing_score", "return_5d_pct")
    long_20 = _calibration(observations, "long_term_score", "return_20d_pct")
    long_60 = _calibration(observations, "long_term_score", "return_60d_pct")
    swing_path_calibration = _binary_calibration(
        observations,
        "swing_score",
        "swing_target_before_stop_5d",
    )

    fundamental_resolved = [
        row for row in observations
        if int(row.get("fundamental_coverage_count") or 0) >= 3
    ]
    scanner_summary = scanner_replay.get("summary") or {}

    payload = {
        "schema_version": 2,
        "replay_version": REPLAY_VERSION,
        "timeframe_score_version": TIMEFRAME_SCORE_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "historical_timeframe_replay",
        "replay": {
            "observation_time": "historical end-of-day",
            "replay_dates": len(replay_dates),
            "start_date": replay_dates[0].isoformat(),
            "end_date": replay_dates[-1].isoformat(),
            "stride_trading_days": stride,
            "seed_symbols": len(seed_symbols),
            "daily_universe_size": universe_size,
            "candidates_per_day": candidates_per_day,
            "historical_feed": feed.upper(),
            "sec_point_in_time": sec_status,
            "selection_note": (
                "Current listed-stock survivorship remains a known limitation. "
                "Replay-day universe ranking uses only information available on "
                "or before each historical date."
            ),
            "known_limitations": [
                "current listed-stock survivorship bias",
                "historical catalyst/news sentiment is neutral rather than reconstructed",
                "historical analog score omits its intraday VWAP-reclaim subcomponent",
                "SEC dilution replay uses filing-form recency and does not re-download old filing text keywords",
                "daily replay validates Swing/Longer-term at EOD; intraday validation stays in the 5-minute scanner replay",
                "when a +5% target and -4% stop are both touched on the same daily bar, ordering is unknowable and that row is excluded from the path-target ML label",
            ],
        },
        "summary": {
            "observations": len(observations),
            "unique_symbols": len({row["symbol"] for row in observations}),
            "fundamental_coverage_3plus": len(fundamental_resolved),
            "existing_intraday_replay_observations": scanner_summary.get("observations"),
            "existing_intraday_replay_target_before_stop_rate_pct": scanner_summary.get("target_before_stop_rate_pct"),
            "swing_5d_resolved": sum(
                (row.get("outcomes") or {}).get("return_5d_pct") is not None
                for row in observations
            ),
            "swing_path_target": {
                "target_pct": SWING_TARGET_PCT,
                "stop_pct": SWING_STOP_PCT,
                "horizon_sessions": SWING_HORIZON_SESSIONS,
                "labeled": sum(
                    (row.get("outcomes") or {}).get(
                        "swing_target_before_stop_5d"
                    ) is not None
                    for row in observations
                ),
                "target_first": sum(
                    (row.get("outcomes") or {}).get(
                        "swing_target_before_stop_5d"
                    ) == 1
                    for row in observations
                ),
                "ambiguous_same_day": sum(
                    bool(
                        (row.get("outcomes") or {}).get(
                            "swing_ambiguous_same_day_5d"
                        )
                    )
                    for row in observations
                ),
                "avg_mfe_pct": round(
                    statistics.mean(
                        [
                            value
                            for row in observations
                            for value in [
                                _num(
                                    (row.get("outcomes") or {}).get(
                                        "swing_mfe_5d_pct"
                                    )
                                )
                            ]
                            if value is not None
                        ]
                    ),
                    3,
                )
                if any(
                    _num(
                        (row.get("outcomes") or {}).get("swing_mfe_5d_pct")
                    ) is not None
                    for row in observations
                )
                else None,
                "avg_mae_pct": round(
                    statistics.mean(
                        [
                            value
                            for row in observations
                            for value in [
                                _num(
                                    (row.get("outcomes") or {}).get(
                                        "swing_mae_5d_pct"
                                    )
                                )
                            ]
                            if value is not None
                        ]
                    ),
                    3,
                )
                if any(
                    _num(
                        (row.get("outcomes") or {}).get("swing_mae_5d_pct")
                    ) is not None
                    for row in observations
                )
                else None,
            },
            "long_term_20d_resolved": sum(
                (row.get("outcomes") or {}).get("return_20d_pct") is not None
                for row in observations
            ),
            "long_term_60d_resolved": sum(
                (row.get("outcomes") or {}).get("return_60d_pct") is not None
                for row in observations
            ),
            "swing_5d_directional_lift": _directional_lift(
                observations, "swing_score", "return_5d_pct"
            ),
            "swing_path_target_lift": _binary_lift(
                observations,
                "swing_score",
                "swing_target_before_stop_5d",
            ),
            "long_term_20d_directional_lift": _directional_lift(
                observations, "long_term_score", "return_20d_pct"
            ),
            "training_eligibility": {
                "swing_historical_dataset_ready": len(observations) >= 300,
                "long_term_historical_dataset_ready": (
                    bool(sec_status.get("enabled"))
                    and len(fundamental_resolved) >= 200
                ),
                "weights_still_tracking_only": True,
            },
        },
        "calibration": {
            "swing_3d": swing_3,
            "swing_5d": swing_5,
            "swing_path_target_5d": swing_path_calibration,
            "long_term_20d": long_20,
            "long_term_60d": long_60,
        },
        "observations": observations,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote historical timeframe replay: {OUTPUT_PATH}")
    print("TIMEFRAME_REPLAY_SUMMARY=" + json.dumps(payload["summary"], sort_keys=True))
    print("TIMEFRAME_REPLAY_SWING_5D=" + json.dumps(swing_5, sort_keys=True))
    print(
        "TIMEFRAME_REPLAY_SWING_PATH="
        + json.dumps(payload["summary"]["swing_path_target"], sort_keys=True)
    )
    print(
        "TIMEFRAME_REPLAY_SWING_PATH_CALIBRATION="
        + json.dumps(swing_path_calibration, sort_keys=True)
    )
    print("TIMEFRAME_REPLAY_LONG_20D=" + json.dumps(long_20, sort_keys=True))


if __name__ == "__main__":
    main()
