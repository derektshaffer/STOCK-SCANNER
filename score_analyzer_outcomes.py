import json
import os
import statistics
import urllib.parse
import urllib.request
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
DATA_BASE = "https://data.alpaca.markets"
API_KEY = os.environ.get("ALPACA_API_KEY", "").strip()
API_SECRET = os.environ.get("ALPACA_SECRET_KEY", "").strip()
OUTCOME_DATE = os.environ.get("OUTCOME_DATE", "").strip()
OUT_DIR = Path(os.environ.get("ANALYZER_OUTCOME_DIR", "analyzer_outcomes"))


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


def _fetch_symbol_bars(symbol, start, end):
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


def _price_at_or_after(bars, target, tolerance_minutes=5):
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
    if best_delta is None or best_delta > tolerance_minutes * 60:
        return None
    return best


def _first_touch(bars, target, stop, created):
    target = _num(target)
    stop = _num(stop)
    if target is None or stop is None:
        return None
    for bar in bars:
        dt = _bar_dt(bar)
        if dt is None or dt < created:
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
    return rows


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


def _calibrate(rows, score_field):
    groups = {}
    for row in rows:
        ret = _num((row.get("outcomes") or {}).get("return_60m_pct"))
        bucket = _bucket(row.get(score_field))
        if ret is None or bucket is None:
            continue
        g = groups.setdefault(bucket, [])
        g.append(ret)

    out = {}
    for bucket, values in groups.items():
        out[bucket] = {
            "n": len(values),
            "higher_60m_rate": round(sum(v > 0 for v in values) / len(values) * 100.0, 1),
            "hit_3pct_60m_rate": round(sum(v >= 3 for v in values) / len(values) * 100.0, 1),
            "avg_return_60m_pct": round(sum(values) / len(values), 3),
            "median_return_60m_pct": round(statistics.median(values), 3),
        }
    return out


def _all_rows():
    rows = []
    if not OUT_DIR.exists():
        return rows
    files = sorted(OUT_DIR.glob("predictions_*.json"))[-60:]
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                rows.extend(payload)
        except Exception:
            continue
    return rows


def _write_calibration():
    rows = _all_rows()
    resolved = [
        row for row in rows
        if _num((row.get("outcomes") or {}).get("return_60m_pct")) is not None
    ]
    touches = [
        row for row in rows
        if (row.get("outcomes") or {}).get("target1_first_touch") in {"target", "stop"}
    ]
    target_wins = [
        row for row in touches
        if (row.get("outcomes") or {}).get("target1_first_touch") == "target"
    ]

    payload = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "prediction_rows": len(rows),
        "resolved_60m": len(resolved),
        "calibration_ready": len(resolved) >= 30,
        "potential_calibration": _calibrate(rows, "potential_score"),
        "entry_calibration": _calibrate(rows, "entry_readiness"),
        "evidence_calibration": _calibrate(rows, "evidence_strength"),
        "target_before_stop_rate": (
            round(len(target_wins) / len(touches) * 100.0, 1)
            if touches else None
        ),
        "target_stop_resolved": len(touches),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "calibration.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    print(
        f"Analyzer calibration: rows={len(rows)} resolved60={len(resolved)} "
        f"ready={payload['calibration_ready']}"
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

    _write_calibration()


if __name__ == "__main__":
    main()
