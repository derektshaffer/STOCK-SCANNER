"""Forward outcome tracking for off-hours Swing / Longer-Term discovery.

This is research-only tracking. It freezes each completed-daily discovery cohort
and resolves 1/2/3/5/10/20/40 trading-session outcomes without changing live
Momentum Scanner rank, Scanner ACTION, or ML.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import urllib.error
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from tradier_live import get_history_bars
from timeframe_targets import resolve_swing_path_from_bars


ET = ZoneInfo("America/New_York")
SNAPSHOT_DIR = Path("scan_logs/offhours_timeframe")
OUT_DIR = Path(os.environ.get("OFFHOURS_OUTCOME_DIR", "offhours_outcomes"))
TRADIER_TOKEN = (
    os.environ.get("TRADIER_ACCESS_TOKEN", "").strip()
    or os.environ.get("TRADIER_TOKEN", "").strip()
)
HORIZONS = (1, 2, 3, 5, 10, 20, 40)
TRACKER_VERSION = "offhours-timeframe-outcome-v2-shared-swing-target"


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default


def _atomic_write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")
    tmp.replace(path)


def _parse_day(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _bar_day(bar):
    raw = str((bar or {}).get("t") or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ET).date()
    except Exception:
        return None


def _pct(value, base):
    value = _num(value)
    base = _num(base)
    if value is None or base is None or base <= 0:
        return None
    return round((value / base - 1.0) * 100.0, 3)


def _retry_history(symbol, start, end):
    if not TRADIER_TOKEN:
        raise RuntimeError("Missing TRADIER_ACCESS_TOKEN / TRADIER_TOKEN.")
    delay = 1.0
    for attempt in range(4):
        try:
            return get_history_bars(
                symbol,
                TRADIER_TOKEN,
                start,
                end,
                interval="daily",
            ) or []
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


def _session_rows(bars, signal_day, through_day):
    out = []
    for bar in bars or []:
        day = _bar_day(bar)
        if day is None or day <= signal_day or day > through_day:
            continue
        out.append(bar)
    out.sort(key=lambda row: str(row.get("t") or ""))
    return out


def _horizon_outcome(signal_price, sessions, horizon):
    if len(sessions) < horizon:
        return None
    window = sessions[:horizon]
    close = _num(window[-1].get("c"))
    highs = [_num(row.get("h")) for row in window]
    lows = [_num(row.get("l")) for row in window]
    highs = [x for x in highs if x is not None]
    lows = [x for x in lows if x is not None]
    outcome = {
        "return_pct": _pct(close, signal_price),
        "mfe_pct": _pct(max(highs), signal_price) if highs else None,
        "mae_pct": _pct(min(lows), signal_price) if lows else None,
        "resolved_session_date": (
            _bar_day(window[-1]).isoformat() if _bar_day(window[-1]) else None
        ),
    }
    if horizon == 5:
        # Use the exact shared Swing ML target definition so live forward
        # evidence measures the same event as historical replay.
        outcome.update(
            resolve_swing_path_from_bars(
                signal_price,
                window,
                horizon_sessions=5,
            )
        )
    return outcome


def _needs_horizon(row, horizon):
    outcome = (row.get("outcomes") or {}).get(str(horizon))
    if not isinstance(outcome, dict):
        return True
    if horizon == 5 and "swing_target_before_stop_5d" not in outcome:
        # Backfill older v1 5D rows with the shared target label.
        return True
    return False


def _benchmark_outcomes(spy_sessions, signal_price):
    result = {}
    for horizon in HORIZONS:
        item = _horizon_outcome(signal_price, spy_sessions, horizon)
        if item is not None:
            result[str(horizon)] = item
    return result


def _snapshot_day(path, payload):
    day = _parse_day((payload or {}).get("last_completed_session_date"))
    if day:
        return day
    stem = Path(path).stem
    marker = "offhours_timeframe_"
    if stem.startswith(marker):
        return _parse_day(stem[len(marker):])
    return None


def _candidate_seed(row):
    return {
        "symbol": str(row.get("symbol") or "").upper().strip(),
        "signal_price": _num(row.get("price")),
        "daily_discovery_score": _num(row.get("daily_discovery_score")),
        "trend_candidate_raw_score": _num(
            row.get("trend_candidate_raw_score")
            if row.get("trend_candidate_raw_score") is not None
            else row.get("daily_discovery_score")
        ),
        "trend_candidate_score_version": row.get("trend_candidate_score_version"),
        "daily_setup_grade": row.get("daily_setup_grade"),
        "timeframe_best_fit": row.get("timeframe_best_fit"),
        "timeframe_primary_fit": row.get("timeframe_primary_fit"),
        "timeframe_swing_score": _num(row.get("timeframe_swing_score")),
        "timeframe_longer_term_score": _num(
            row.get("timeframe_longer_term_score")
        ),
        "daily_setup_archetypes": list(row.get("daily_setup_archetypes") or []),
        "day_pct": _num(row.get("day_pct")),
        "daily_return_5d_pct": _num(row.get("daily_return_5d_pct")),
        "daily_return_20d_pct": _num(row.get("daily_return_20d_pct")),
        "daily_return_40d_pct": _num(row.get("daily_return_40d_pct")),
        "daily_volume_ratio": _num(row.get("daily_volume_ratio")),
        "relative_strength_vs_spy_20d_pct": _num(
            row.get("relative_strength_vs_spy_20d_pct")
        ),
        "outcomes": {},
    }


def _existing_candidate_map(payload):
    return {
        str(row.get("symbol") or "").upper(): row
        for row in (payload or {}).get("candidates") or []
        if str(row.get("symbol") or "").strip()
    }


def _cohort_from_snapshot(path, payload, signal_day):
    return {
        "schema_version": 1,
        "tracker_version": TRACKER_VERSION,
        "signal_date": signal_day.isoformat(),
        "source_snapshot": str(path),
        "source_snapshot_version": payload.get("version"),
        "source_snapshot_generated_at_utc": payload.get("generated_at_utc"),
        "research_only": True,
        "production_rank_impact": False,
        "candidate_count": len(payload.get("candidates") or []),
        "last_checked_utc": None,
        "elapsed_trading_sessions": 0,
        "fully_resolved": False,
        "candidates": [
            _candidate_seed(row)
            for row in (payload.get("candidates") or [])
            if str(row.get("symbol") or "").strip()
            and _num(row.get("price")) is not None
        ],
        "summary": {},
    }


def _merge_snapshot_fields(existing, snapshot):
    by_symbol = _existing_candidate_map(existing)
    merged = []
    for seed in (snapshot or {}).get("candidates") or []:
        symbol = str(seed.get("symbol") or "").upper().strip()
        old = by_symbol.get(symbol)
        if not old:
            merged.append(seed)
            continue
        old_outcomes = dict(old.get("outcomes") or {})
        seed = {**seed, **{k: v for k, v in old.items() if k not in {"outcomes"}}}
        seed["outcomes"] = old_outcomes
        merged.append(seed)
    return merged


def _summary_rows(rows, horizon):
    key = str(horizon)
    resolved = []
    for row in rows:
        outcome = (row.get("outcomes") or {}).get(key)
        if not isinstance(outcome, dict):
            continue
        ret = _num(outcome.get("return_pct"))
        if ret is None:
            continue
        resolved.append((row, ret, outcome))
    if not resolved:
        return {
            "resolved": 0,
            "avg_return_pct": None,
            "median_return_pct": None,
            "higher_rate_pct": None,
            "avg_mfe_pct": None,
            "avg_mae_pct": None,
            "avg_excess_vs_spy_pct": None,
            "swing_target_resolved": 0 if horizon == 5 else None,
            "swing_target_before_stop_rate_pct": None,
            "swing_ambiguous_same_day": 0 if horizon == 5 else None,
        }
    returns = [ret for _row, ret, _outcome in resolved]
    mfes = [_num(outcome.get("mfe_pct")) for _row, _ret, outcome in resolved]
    maes = [_num(outcome.get("mae_pct")) for _row, _ret, outcome in resolved]
    excess = [
        _num(outcome.get("excess_vs_spy_pct"))
        for _row, _ret, outcome in resolved
    ]
    swing_labels = []
    swing_ambiguous = 0
    if horizon == 5:
        for _row, _ret, outcome in resolved:
            label = outcome.get("swing_target_before_stop_5d")
            if label in {0, 1}:
                swing_labels.append(int(label))
            if bool(outcome.get("swing_ambiguous_same_day_5d")):
                swing_ambiguous += 1
    mfes = [x for x in mfes if x is not None]
    maes = [x for x in maes if x is not None]
    excess = [x for x in excess if x is not None]
    return {
        "resolved": len(resolved),
        "avg_return_pct": round(statistics.fmean(returns), 3),
        "median_return_pct": round(statistics.median(returns), 3),
        "higher_rate_pct": round(
            100.0 * sum(1 for x in returns if x > 0) / len(returns), 1
        ),
        "avg_mfe_pct": round(statistics.fmean(mfes), 3) if mfes else None,
        "avg_mae_pct": round(statistics.fmean(maes), 3) if maes else None,
        "avg_excess_vs_spy_pct": (
            round(statistics.fmean(excess), 3) if excess else None
        ),
        "swing_target_resolved": len(swing_labels) if horizon == 5 else None,
        "swing_target_before_stop_rate_pct": (
            round(100.0 * sum(swing_labels) / len(swing_labels), 1)
            if horizon == 5 and swing_labels
            else None
        ),
        "swing_ambiguous_same_day": (
            swing_ambiguous if horizon == 5 else None
        ),
    }


def _group_summary(rows, horizon, field, list_field=False):
    groups = {}
    for row in rows:
        values = row.get(field)
        if list_field:
            values = values or ["UNCLASSIFIED"]
        else:
            values = [values or "UNKNOWN"]
        for value in values:
            groups.setdefault(str(value), []).append(row)
    return {
        key: _summary_rows(group_rows, horizon)
        for key, group_rows in sorted(groups.items())
    }


def _build_summary(rows):
    return {
        "horizons": {
            str(h): _summary_rows(rows, h)
            for h in HORIZONS
        },
        "swing_5d_by_grade": _group_summary(rows, 5, "daily_setup_grade"),
        "swing_5d_by_best_fit": _group_summary(rows, 5, "timeframe_best_fit"),
        "swing_5d_by_archetype": _group_summary(
            rows, 5, "daily_setup_archetypes", list_field=True
        ),
        "longer_20d_by_grade": _group_summary(rows, 20, "daily_setup_grade"),
        "longer_20d_by_best_fit": _group_summary(
            rows, 20, "timeframe_best_fit"
        ),
        "longer_20d_by_archetype": _group_summary(
            rows, 20, "daily_setup_archetypes", list_field=True
        ),
    }


def _cohort_path(signal_day):
    return OUT_DIR / f"cohort_{signal_day.isoformat()}.json"


def _markdown_report(cohorts):
    lines = [
        "# Off-Hours Swing / Longer-Term Forward Outcomes",
        "",
        f"Updated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "Research-only. These cohorts do not change live Momentum Scanner ranking, ACTION, or ML.",
        "",
        "| Signal date | Candidates | Sessions elapsed | 5D resolved | 5D avg | 5D up | 5D +5/-4 resolved | Target-first | Ambiguous | 20D resolved | 20D avg | 20D up |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for cohort in sorted(cohorts, key=lambda x: x.get("signal_date") or "", reverse=True):
        summary = cohort.get("summary") or {}
        h5 = (summary.get("horizons") or {}).get("5") or {}
        h20 = (summary.get("horizons") or {}).get("20") or {}
        def fmt(value, suffix="%"):
            return "—" if value is None else f"{value:.2f}{suffix}"
        lines.append(
            "| {date} | {n} | {elapsed} | {r5} | {a5} | {u5} | {rt5} | {t5} | {amb5} | {r20} | {a20} | {u20} |".format(
                date=cohort.get("signal_date") or "—",
                n=len(cohort.get("candidates") or []),
                elapsed=cohort.get("elapsed_trading_sessions") or 0,
                r5=h5.get("resolved") or 0,
                a5=fmt(h5.get("avg_return_pct")),
                u5=fmt(h5.get("higher_rate_pct")),
                rt5=h5.get("swing_target_resolved") or 0,
                t5=fmt(h5.get("swing_target_before_stop_rate_pct")),
                amb5=h5.get("swing_ambiguous_same_day") or 0,
                r20=h20.get("resolved") or 0,
                a20=fmt(h20.get("avg_return_pct")),
                u20=fmt(h20.get("higher_rate_pct")),
            )
        )
    lines += [
        "",
        "Primary comparison horizons: Swing = 5 trading sessions; Longer-Term = 20 trading sessions.",
        "The 5D Swing target-first metric uses the same shared target as historical Swing ML: +5% before -4% within 5 trading sessions. Same-day target/stop touches are ambiguous and excluded from that rate.",
        "Additional 1/2/3/10/40-session outcomes plus MFE, MAE, SPY return, and excess return are stored in each cohort JSON.",
        "",
    ]
    return "\n".join(lines)


def run():
    paths = sorted(SNAPSHOT_DIR.glob("offhours_timeframe_????-??-??.json"))
    if not paths:
        print("No off-hours timeframe snapshots found.")
        return 0

    snapshots = []
    for path in paths:
        payload = _load_json(path, {})
        signal_day = _snapshot_day(path, payload)
        if signal_day is None:
            continue
        snapshots.append((signal_day, path, payload))

    if not snapshots:
        print("No dated off-hours timeframe snapshots found.")
        return 0

    today = datetime.now(ET).date()
    earliest = min(day for day, _path, _payload in snapshots)
    spy_start = datetime.combine(earliest, dtime(0, 0), tzinfo=ET)
    spy_end = datetime.combine(today + timedelta(days=1), dtime(0, 0), tzinfo=ET)
    spy_bars = _retry_history("SPY", spy_start, spy_end)
    spy_days = [_bar_day(bar) for bar in spy_bars]
    spy_days = sorted({day for day in spy_days if day is not None})

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    all_cohorts = []

    for signal_day, snapshot_path, snapshot_payload in snapshots:
        path = _cohort_path(signal_day)
        existing = _load_json(path, {}) if path.exists() else {}
        cohort = (
            existing
            if existing
            else _cohort_from_snapshot(snapshot_path, snapshot_payload, signal_day)
        )
        if existing:
            fresh = _cohort_from_snapshot(snapshot_path, snapshot_payload, signal_day)
            cohort["candidates"] = _merge_snapshot_fields(existing, fresh)
            cohort["candidate_count"] = len(cohort["candidates"])

        elapsed_days = [
            day for day in spy_days
            if signal_day < day <= today
        ]
        elapsed = len(elapsed_days)
        cohort["elapsed_trading_sessions"] = elapsed

        benchmark_sessions = _session_rows(spy_bars, signal_day, today)
        spy_signal_bar = next(
            (bar for bar in spy_bars if _bar_day(bar) == signal_day),
            None,
        )
        spy_signal_price = _num((spy_signal_bar or {}).get("c"))
        spy_outcomes = (
            _benchmark_outcomes(benchmark_sessions, spy_signal_price)
            if spy_signal_price is not None
            else {}
        )

        due_horizons = [
            h for h in HORIZONS
            if elapsed >= h
            and any(
                str(h) not in (row.get("outcomes") or {})
                for row in cohort.get("candidates") or []
            )
        ]

        if due_horizons:
            start = datetime.combine(signal_day, dtime(0, 0), tzinfo=ET)
            end = datetime.combine(today + timedelta(days=1), dtime(0, 0), tzinfo=ET)
            for row in cohort.get("candidates") or []:
                symbol = str(row.get("symbol") or "").upper().strip()
                signal_price = _num(row.get("signal_price"))
                if not symbol or signal_price is None:
                    continue
                missing_due = [
                    h for h in due_horizons
                    if _needs_horizon(row, h)
                ]
                if not missing_due:
                    continue
                try:
                    bars = _retry_history(symbol, start, end)
                except Exception as exc:
                    row["last_error"] = str(exc)[:240]
                    continue
                sessions = _session_rows(bars, signal_day, today)
                outcomes = dict(row.get("outcomes") or {})
                for horizon in HORIZONS:
                    if elapsed < horizon or not _needs_horizon(row, horizon):
                        continue
                    item = _horizon_outcome(signal_price, sessions, horizon)
                    if item is None:
                        continue
                    spy_item = spy_outcomes.get(str(horizon)) or {}
                    item["spy_return_pct"] = spy_item.get("return_pct")
                    if (
                        item.get("return_pct") is not None
                        and spy_item.get("return_pct") is not None
                    ):
                        item["excess_vs_spy_pct"] = round(
                            item["return_pct"] - spy_item["return_pct"], 3
                        )
                    else:
                        item["excess_vs_spy_pct"] = None
                    outcomes[str(horizon)] = item
                row["outcomes"] = outcomes
                row.pop("last_error", None)

        cohort["last_checked_utc"] = datetime.now(timezone.utc).isoformat()
        cohort["summary"] = _build_summary(cohort.get("candidates") or [])
        cohort["fully_resolved"] = all(
            "40" in (row.get("outcomes") or {})
            for row in cohort.get("candidates") or []
        )
        _atomic_write(path, cohort)
        all_cohorts.append(cohort)
        written += 1

    (OUT_DIR / "latest_summary.md").write_text(
        _markdown_report(all_cohorts),
        encoding="utf-8",
    )
    _atomic_write(
        OUT_DIR / "latest_summary.json",
        {
            "schema_version": 1,
            "tracker_version": TRACKER_VERSION,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "cohorts": [
                {
                    "signal_date": cohort.get("signal_date"),
                    "candidate_count": len(cohort.get("candidates") or []),
                    "elapsed_trading_sessions": cohort.get("elapsed_trading_sessions"),
                    "fully_resolved": bool(cohort.get("fully_resolved")),
                    "summary": cohort.get("summary") or {},
                }
                for cohort in sorted(
                    all_cohorts,
                    key=lambda x: x.get("signal_date") or "",
                )
            ],
        },
    )
    print(f"OFFHOURS_OUTCOME_COHORTS={written}")
    print(f"OFFHOURS_OUTCOME_DIR={OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
