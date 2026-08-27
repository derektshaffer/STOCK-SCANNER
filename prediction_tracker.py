import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


LOG_PATH = Path(os.environ.get("ANALYZER_PREDICTION_LOG", "analysis_logs/analyzer_predictions.json"))
BUCKET_MINUTES = 5
ET = ZoneInfo("America/New_York")

GITHUB_TOKEN = (
    os.environ.get("ANALYZER_GITHUB_TOKEN", "").strip()
    or os.environ.get("GITHUB_TOKEN", "").strip()
)
GITHUB_REPO = os.environ.get(
    "ANALYZER_GITHUB_REPO", "derektshaffer/STOCK-SCANNER"
).strip()
GITHUB_BRANCH = os.environ.get("ANALYZER_GITHUB_BRANCH", "main").strip() or "main"
REMOTE_DIR = os.environ.get("ANALYZER_OUTCOME_DIR", "analyzer_outcomes").strip() or "analyzer_outcomes"
REMOTE_SYNC_SECONDS = max(
    300, int(os.environ.get("ANALYZER_REMOTE_SYNC_SECONDS", "900") or 900)
)
_REMOTE_STATE = {"loaded": False, "last_sync": 0.0, "last_error": None, "last_path": None}


def _num(value):
    try:
        return float(value)
    except Exception:
        return None


def _parse_dt(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _github_headers():
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "stock-analyzer-prediction-tracker/2.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


def _github_contents_url(path):
    owner_repo = GITHUB_REPO.strip("/")
    encoded = "/".join(urllib.parse.quote(part, safe="") for part in str(path).split("/"))
    return f"https://api.github.com/repos/{owner_repo}/contents/{encoded}"


def _github_get_file(path, require_token=False):
    if require_token and not GITHUB_TOKEN:
        return None, None
    url = _github_contents_url(path) + "?" + urllib.parse.urlencode({"ref": GITHUB_BRANCH})
    req = urllib.request.Request(url, headers=_github_headers())
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, None
        raise
    content = payload.get("content")
    if payload.get("encoding") == "base64" and content:
        raw = base64.b64decode("".join(str(content).split()))
        return json.loads(raw.decode("utf-8")), payload.get("sha")
    return None, payload.get("sha")


def _github_put_json(path, payload, sha=None):
    if not GITHUB_TOKEN:
        return False
    body = {
        "message": f"Sync Analyzer predictions {datetime.now(ET).date().isoformat()}",
        "content": base64.b64encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        body["sha"] = sha
    req = urllib.request.Request(
        _github_contents_url(path),
        data=json.dumps(body).encode("utf-8"),
        headers={**_github_headers(), "Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=15):
        return True


def _remote_day_path(day):
    return f"{REMOTE_DIR}/predictions_{day.isoformat()}.json"


def _row_day(row):
    dt = _parse_dt(row.get("timestamp"))
    return dt.astimezone(ET).date() if dt else None


def _merge_rows(*groups):
    merged = {}
    for rows in groups:
        for row in rows or []:
            key = row.get("bucket_key") or row.get("id")
            if not key:
                continue
            existing = merged.get(key)
            if not existing:
                merged[key] = row
                continue
            # Prefer whichever copy has more resolved outcome fields.
            old_count = len((existing.get("outcomes") or {}))
            new_count = len((row.get("outcomes") or {}))
            merged[key] = row if new_count >= old_count else existing
    return sorted(
        merged.values(),
        key=lambda row: str(row.get("timestamp") or ""),
    )


def _load_remote_today():
    if not GITHUB_TOKEN:
        return []
    today = datetime.now(ET).date()
    try:
        payload, _sha = _github_get_file(_remote_day_path(today), require_token=True)
        if isinstance(payload, list):
            _REMOTE_STATE["last_error"] = None
            return payload
    except Exception as exc:
        _REMOTE_STATE["last_error"] = str(exc)[:180]
    return []


def _sync_remote(rows, force=False):
    if not GITHUB_TOKEN:
        return {"enabled": False, "synced": False, "reason": "missing_token"}
    now_ts = time.time()
    if (
        not force
        and _REMOTE_STATE["last_sync"]
        and now_ts - float(_REMOTE_STATE["last_sync"]) < REMOTE_SYNC_SECONDS
    ):
        return {"enabled": True, "synced": False, "reason": "interval"}

    today = datetime.now(ET).date()
    daily_rows = [row for row in rows if _row_day(row) == today]
    path = _remote_day_path(today)
    try:
        remote, sha = _github_get_file(path, require_token=True)
        merged = _merge_rows(remote if isinstance(remote, list) else [], daily_rows)
        _github_put_json(path, merged, sha=sha)
        _REMOTE_STATE.update(
            {
                "last_sync": now_ts,
                "last_error": None,
                "last_path": path,
            }
        )
        return {"enabled": True, "synced": True, "path": path, "count": len(merged)}
    except Exception as exc:
        _REMOTE_STATE["last_error"] = str(exc)[:180]
        return {
            "enabled": True,
            "synced": False,
            "reason": "error",
            "error": _REMOTE_STATE["last_error"],
        }


def _load_durable_calibration():
    path = f"{REMOTE_DIR}/calibration.json"
    try:
        payload, _sha = _github_get_file(path, require_token=False)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _load():
    local = []
    try:
        if LOG_PATH.exists():
            payload = json.loads(LOG_PATH.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                local = payload
    except Exception:
        local = []

    if not _REMOTE_STATE["loaded"]:
        _REMOTE_STATE["loaded"] = True
        remote = _load_remote_today()
        if remote:
            local = _merge_rows(local, remote)
            try:
                LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
                LOG_PATH.write_text(
                    json.dumps(local[-5000:], separators=(",", ":")),
                    encoding="utf-8",
                )
            except Exception:
                pass
    return local


def _save(rows, force_remote=False):
    local_ok = False
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = LOG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows[-5000:], separators=(",", ":")), encoding="utf-8")
        tmp.replace(LOG_PATH)
        local_ok = True
    except Exception:
        local_ok = False

    sync = _sync_remote(rows, force=force_remote) if local_ok else {
        "enabled": bool(GITHUB_TOKEN),
        "synced": False,
        "reason": "local_save_failed",
    }
    _REMOTE_STATE["last_sync_result"] = sync
    return local_ok


def _bucket_key(symbol, when):
    minute = (when.minute // BUCKET_MINUTES) * BUCKET_MINUTES
    bucket = when.replace(minute=minute, second=0, microsecond=0)
    return f"{symbol}:{bucket.isoformat()}"


def record_prediction(metrics, now=None):
    """Record one Analyzer prediction per ticker per five-minute bucket."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    symbol = str(metrics.get("symbol") or "").upper().strip()
    if not symbol:
        return {"recorded": False, "reason": "missing_symbol"}

    rows = _load()
    key = _bucket_key(symbol, now)
    if any(row.get("bucket_key") == key for row in rows[-200:]):
        return {"recorded": False, "reason": "already_recorded", "count": len(rows)}

    plan = metrics.get("trade_plan") or {}
    selected = plan.get("selected") or {}
    ml = metrics.get("ml_prediction") or {}
    hist = metrics.get("historical_setup") or {}
    v2 = metrics.get("decision_v2") or {}
    turnover = v2.get("turnover_context") or {}

    row = {
        "id": f"{key}:{len(rows)+1}",
        "bucket_key": key,
        "symbol": symbol,
        "timestamp": now.isoformat(),
        "price": _num(metrics.get("price")),
        "day_pct": _num(metrics.get("day_pct")),
        "vwap_extension_pct": _num(metrics.get("vwap_extension_pct")),
        "volume_pace": _num(metrics.get("volume_pace")),
        "setup_score": _num(metrics.get("score")),
        "plan_confidence": _num(plan.get("confidence")),
        "plan_status": plan.get("status"),
        "plan_action": plan.get("action"),
        "preferred_plan": plan.get("preferred_plan"),
        "potential_score": _num(v2.get("potential_score")),
        "entry_readiness": _num(v2.get("entry_readiness")),
        "evidence_strength": _num(v2.get("evidence_strength")),
        "entry_low": _num(selected.get("entry_low")),
        "entry_high": _num(selected.get("entry_high")),
        "target1": _num(selected.get("target1")),
        "stop": _num(selected.get("stop")),
        "ml_edge": _num(ml.get("ml_edge_score")),
        "ml_validated_models": int(ml.get("validated_edge_model_count") or 0),
        "historical_bias": hist.get("bias_label"),
        "historical_bias_score": _num(hist.get("bias_score")),
        "historical_samples": int(hist.get("sample_count") or 0),
        "float_shares": _num(turnover.get("float_shares")),
        "float_turnover": _num(turnover.get("float_turnover")),
        "shares_outstanding": _num(turnover.get("shares_outstanding")),
        "shares_outstanding_turnover": _num(
            turnover.get("shares_outstanding_turnover")
        ),
        "outcomes": {},
    }
    rows.append(row)
    force_remote = not bool(_REMOTE_STATE.get("last_sync"))
    ok = _save(rows, force_remote=force_remote)
    sync = _REMOTE_STATE.get("last_sync_result") or {}
    return {
        "recorded": ok,
        "count": len(rows),
        "path": str(LOG_PATH),
        "durable_sync": sync,
    }


def _bar_dt(bar):
    return _parse_dt(bar.get("t"))


def _closest_close(bars, target_dt, tolerance_minutes=12):
    best = None
    best_delta = None
    for bar in bars:
        dt = _bar_dt(bar)
        close = _num(bar.get("c"))
        if dt is None or close is None:
            continue
        delta = abs((dt - target_dt).total_seconds())
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best = close
    if best_delta is None or best_delta > tolerance_minutes * 60:
        return None
    return best


def _first_touch(bars, target, stop):
    if target is None or stop is None:
        return None
    for bar in bars:
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


def resolve_symbol_predictions(sa, symbol, now=None, current_metrics=None):
    """Resolve older predictions opportunistically using delayed SIP bars.

    This intentionally waits for consolidated delayed data rather than scoring
    outcomes from a potentially incomplete single-exchange live feed.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    symbol = str(symbol or "").upper().strip()
    rows = _load()
    pending = [
        row for row in rows
        if row.get("symbol") == symbol
        and _parse_dt(row.get("timestamp"))
        and not bool((row.get("outcomes") or {}).get("resolved_60m"))
    ][-40:]
    if not pending:
        return tracker_summary(rows, symbol, current_metrics=current_metrics)

    earliest = min(_parse_dt(row["timestamp"]) for row in pending)
    safe_end = now - timedelta(minutes=16)
    if safe_end <= earliest:
        return tracker_summary(rows, symbol, current_metrics=current_metrics)

    try:
        bars, _source = sa.try_sip_delayed_bars(
            symbol, "5Min", earliest - timedelta(minutes=5), safe_end, 10000
        )
    except Exception:
        bars = []

    changed = False
    for row in pending:
        created = _parse_dt(row.get("timestamp"))
        price = _num(row.get("price"))
        if created is None or price is None:
            continue
        outcomes = row.setdefault("outcomes", {})
        future = [b for b in bars if (_bar_dt(b) or created) >= created]

        for mins in (15, 30, 60):
            key = f"return_{mins}m_pct"
            if key in outcomes or safe_end < created + timedelta(minutes=mins):
                continue
            close = _closest_close(future, created + timedelta(minutes=mins))
            if close is not None:
                outcomes[key] = round((close / price - 1.0) * 100.0, 3)
                if mins == 60:
                    outcomes["resolved_60m"] = True
                changed = True

        target = _num(row.get("target1"))
        stop = _num(row.get("stop"))
        if "target1_first_touch" not in outcomes and future:
            touch = _first_touch(future, target, stop)
            if touch:
                outcomes["target1_first_touch"] = touch
                changed = True

    if changed:
        _save(rows)
    return tracker_summary(rows, symbol, current_metrics=current_metrics)


def signal_lifecycle(symbol, current_metrics=None, rows=None):
    """Summarize how today's first real ENTRY AVAILABLE signal has evolved."""
    rows = rows if rows is not None else _load()
    symbol = str(symbol or "").upper().strip()
    if not symbol:
        return {"status": "no_symbol"}

    now = datetime.now(timezone.utc)
    today_et = now.astimezone(ET).date()
    symbol_rows = []
    for row in rows:
        if row.get("symbol") != symbol:
            continue
        dt = _parse_dt(row.get("timestamp"))
        if dt is None or dt.astimezone(ET).date() != today_et:
            continue
        symbol_rows.append(row)
    symbol_rows.sort(key=lambda row: str(row.get("timestamp") or ""))

    signal = next(
        (row for row in symbol_rows if row.get("plan_status") == "ENTRY AVAILABLE"),
        None,
    )
    if signal is None:
        return {
            "status": "no_entry_signal",
            "message": "No ENTRY AVAILABLE signal has been recorded for this ticker today.",
        }

    signal_dt = _parse_dt(signal.get("timestamp"))
    signal_price = _num(signal.get("price"))
    target1 = _num(signal.get("target1"))
    stop = _num(signal.get("stop"))
    outcomes = signal.get("outcomes") or {}
    first_touch = outcomes.get("target1_first_touch")

    current_metrics = current_metrics or {}
    current_price = _num(current_metrics.get("price"))
    current_plan = current_metrics.get("trade_plan") or {}
    current_status = str(current_plan.get("status") or "")
    current_action = str(current_plan.get("action") or current_status or "Current setup unavailable")

    change_pct = None
    if signal_price and current_price:
        change_pct = round((current_price / signal_price - 1.0) * 100.0, 2)

    thesis_status = "ACTIVE"
    thesis_message = "Original entry thesis is still unresolved."
    if first_touch == "target":
        thesis_status = "SUCCEEDED"
        thesis_message = "Target 1 was reached before the original stop."
    elif first_touch == "stop":
        thesis_status = "FAILED"
        thesis_message = "The original stop was reached before Target 1."
    elif first_touch == "ambiguous":
        thesis_status = "AMBIGUOUS"
        thesis_message = "Target and stop touched within the same bar; order cannot be determined."
    elif current_price is not None and target1 is not None and current_price >= target1:
        thesis_status = "LIKELY SUCCEEDED"
        thesis_message = "Current price is already at/above the original Target 1; final first-touch scoring is pending."
    elif current_price is not None and stop is not None and current_price <= stop:
        thesis_status = "AT RISK"
        thesis_message = "Current price is at/below the original stop; final first-touch scoring is pending."

    if current_status == "ENTRY AVAILABLE":
        current_state = "ENTRY AVAILABLE NOW"
    elif current_status == "WAIT":
        current_state = current_action
    elif current_status == "NO TRADE":
        current_state = current_action
    else:
        current_state = current_action

    return {
        "status": "ok",
        "signal_time": signal_dt.astimezone(ET).isoformat() if signal_dt else signal.get("timestamp"),
        "signal_price": signal_price,
        "signal_action": signal.get("plan_action") or "ENTRY AVAILABLE",
        "signal_preferred_plan": signal.get("preferred_plan"),
        "signal_potential": _num(signal.get("potential_score")),
        "signal_entry_readiness": _num(signal.get("entry_readiness")),
        "signal_evidence": _num(signal.get("evidence_strength")),
        "target1": target1,
        "stop": stop,
        "current_price": current_price,
        "change_since_signal_pct": change_pct,
        "thesis_status": thesis_status,
        "thesis_message": thesis_message,
        "first_touch": first_touch,
        "current_plan_status": current_status,
        "current_state": current_state,
        "observations_since_signal": sum(
            1 for row in symbol_rows
            if str(row.get("timestamp") or "") >= str(signal.get("timestamp") or "")
        ),
    }


def _score_bucket(value):
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


def _independent_calibration_rows(rows):
    chosen = {}
    for row in sorted(rows, key=lambda item: str(item.get("timestamp") or "")):
        symbol = str(row.get("symbol") or "").upper().strip()
        dt = _parse_dt(row.get("timestamp"))
        if not symbol or dt is None:
            continue
        key = (symbol, dt.date().isoformat(), dt.hour)
        if key not in chosen:
            chosen[key] = row
    return list(chosen.values())


def _bucket_calibration(rows, score_field):
    groups = {}
    for row in rows:
        ret = _num((row.get("outcomes") or {}).get("return_60m_pct"))
        bucket = _score_bucket(row.get(score_field))
        if ret is None or bucket is None:
            continue
        g = groups.setdefault(bucket, {"n": 0, "wins": 0, "returns": []})
        g["n"] += 1
        g["wins"] += int(ret > 0)
        g["returns"].append(ret)

    out = {}
    for bucket, g in groups.items():
        values = g["returns"]
        out[bucket] = {
            "n": g["n"],
            "higher_60m_rate": round(g["wins"] / g["n"] * 100.0, 1) if g["n"] else None,
            "avg_return_60m_pct": round(sum(values) / len(values), 3) if values else None,
        }
    return out


def tracker_summary(rows=None, symbol=None, current_metrics=None):
    all_rows = rows if rows is not None else _load()
    symbol_rows = all_rows
    if symbol:
        symbol_rows = [
            r for r in all_rows
            if r.get("symbol") == str(symbol).upper().strip()
        ]

    # Calibration is global across all tracked tickers. Only lifecycle/history
    # display is scoped to the ticker currently being analyzed.
    calibration_rows = _independent_calibration_rows(all_rows)
    resolved_60 = [
        r for r in calibration_rows
        if (r.get("outcomes") or {}).get("return_60m_pct") is not None
    ]
    positive_60 = [
        r for r in resolved_60
        if _num((r.get("outcomes") or {}).get("return_60m_pct")) is not None
        and _num((r.get("outcomes") or {}).get("return_60m_pct")) > 0
    ]
    touches = [
        r for r in calibration_rows
        if (r.get("outcomes") or {}).get("target1_first_touch") in {"target", "stop"}
    ]
    target_wins = [
        r for r in touches
        if (r.get("outcomes") or {}).get("target1_first_touch") == "target"
    ]

    durable = _load_durable_calibration()
    durable_resolved = int(durable.get("resolved_60m") or 0)
    effective_resolved = max(durable_resolved, len(resolved_60))
    progress = durable.get("calibration_progress")
    if not isinstance(progress, dict):
        if effective_resolved < 30:
            progress = {
                "stage": "COLLECTING",
                "next_threshold": 30,
                "remaining": 30 - effective_resolved,
            }
        elif effective_resolved < 100:
            progress = {
                "stage": "EARLY READ",
                "next_threshold": 100,
                "remaining": 100 - effective_resolved,
            }
        elif effective_resolved < 300:
            progress = {
                "stage": "USEFUL",
                "next_threshold": 300,
                "remaining": 300 - effective_resolved,
            }
        else:
            progress = {
                "stage": "STRONGER SAMPLE",
                "next_threshold": None,
                "remaining": 0,
            }

    lifecycle = (
        signal_lifecycle(symbol, current_metrics=current_metrics, rows=symbol_rows)
        if symbol else None
    )

    return {
        "total_predictions": len(all_rows),
        "calibration_rows": len(calibration_rows),
        "calibration_sampling": "one observation per ticker per hour",
        "signal_lifecycle": lifecycle,
        "resolved_60m": len(resolved_60),
        "higher_60m_rate": (
            round(len(positive_60) / len(resolved_60) * 100.0, 1)
            if resolved_60 else None
        ),
        "resolved_target_stop": len(touches),
        "target_before_stop_rate": (
            round(len(target_wins) / len(touches) * 100.0, 1)
            if touches else None
        ),
        "potential_calibration": (
            (durable.get("potential_calibration") or {})
            or _bucket_calibration(calibration_rows, "potential_score")
        ),
        "entry_calibration": (
            (durable.get("entry_calibration") or {})
            or _bucket_calibration(calibration_rows, "entry_readiness")
        ),
        "calibration_ready": (
            bool(durable.get("calibration_ready"))
            or len(resolved_60) >= 30
        ),
        "durable_resolved_60m": durable_resolved,
        "effective_resolved_60m": effective_resolved,
        "calibration_progress": progress,
        "storage": str(LOG_PATH),
        "persistence": "github+local" if GITHUB_TOKEN else "runtime-local",
        "durable_enabled": bool(GITHUB_TOKEN),
        "durable_path": _REMOTE_STATE.get("last_path"),
        "durable_error": _REMOTE_STATE.get("last_error"),
    }
