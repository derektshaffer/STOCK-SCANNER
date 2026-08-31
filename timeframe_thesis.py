"""Persistent setup-horizon continuity for Analyzer timeframe reasoning.

This tracker does NOT alter trade entries, stops, targets, scanner ranking, or
ML features. It keeps the displayed setup horizon from flipping among INTRADAY,
SWING, LONGER-TERM, and MIXED on one noisy refresh while preserving the raw
timeframe scores for auditability.

Daily/monthly information is still recomputed from point-in-time market and SEC
data. This layer only remembers how the horizon thesis has evolved.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


VERSION = "timeframe-thesis-v1"
VALID_FITS = {"INTRADAY", "SWING", "LONGER-TERM", "MIXED"}
REPLACEMENT_CONFIRMATIONS = max(
    2,
    int(os.environ.get("ANALYZER_TIMEFRAME_REPLACEMENT_CONFIRMATIONS", "3") or 3),
)
MAX_STATE_AGE_DAYS = max(
    5,
    int(os.environ.get("ANALYZER_TIMEFRAME_STATE_DAYS", "60") or 60),
)
STATE_PATH = Path(
    os.environ.get(
        "ANALYZER_TIMEFRAME_STATE_PATH",
        str(Path(tempfile.gettempdir()) / "stock-analyzer-timeframe-thesis.json"),
    )
)


def _num(value):
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _parse_dt(value):
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _state_path(path=None):
    if path is not None:
        return Path(path)
    namespace = (
        os.environ.get("ANALYZER_THESIS_NAMESPACE", "").strip()
        or "standalone"
    )
    digest = hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:16]
    return STATE_PATH.with_name(
        f"{STATE_PATH.stem}-{digest}{STATE_PATH.suffix}"
    )


def _load(path=None):
    target = _state_path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _save(payload, path=None):
    target = _state_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, separators=(",", ":"), default=str),
            encoding="utf-8",
        )
        os.replace(tmp, target)
        return True
    except Exception:
        return False


def _score_for_fit(scores, fit):
    key = {
        "INTRADAY": "intraday",
        "SWING": "swing",
        "LONGER-TERM": "long_term",
    }.get(str(fit or "").upper())
    if key is None:
        values = [_num(v) for v in (scores or {}).values()]
        values = [v for v in values if v is not None]
        return sum(values) / len(values) if values else None
    return _num((scores or {}).get(key))


def _new_state(symbol, fit, scores, now, reason, revision=1, prior=None):
    prior = prior if isinstance(prior, dict) else {}
    transitions = list(prior.get("transitions") or [])[-20:]
    previous_fit = str(prior.get("active_fit") or "").upper().strip()
    if previous_fit and previous_fit != fit:
        transitions.append(
            {
                "timestamp": now.isoformat(),
                "from_fit": previous_fit,
                "to_fit": fit,
                "reason": reason,
                "from_revision": int(prior.get("revision") or 0),
                "to_revision": int(revision),
            }
        )
    return {
        "version": VERSION,
        "symbol": str(symbol or "").upper().strip(),
        "active_fit": fit,
        "anchored_at": now.isoformat(),
        "last_updated": now.isoformat(),
        "revision": int(revision),
        "pending_fit": None,
        "pending_count": 0,
        "change_reason": reason,
        "last_scores": dict(scores or {}),
        "history": list(prior.get("history") or [])[-119:],
        "transitions": transitions[-20:],
    }


def track_timeframe_thesis(
    symbol,
    timeframe,
    *,
    now=None,
    store_path=None,
    replacement_confirmations=REPLACEMENT_CONFIRMATIONS,
):
    """Return a stable setup horizon while preserving the raw current fit."""
    symbol = str(symbol or "").upper().strip()
    timeframe = timeframe if isinstance(timeframe, dict) else {}
    raw_fit = str(timeframe.get("best_fit") or "MIXED").upper().strip()
    if raw_fit not in VALID_FITS:
        raw_fit = "MIXED"
    scores = dict(timeframe.get("scores") or {})
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    if not symbol:
        return {
            "version": VERSION,
            "status": "UNAVAILABLE",
            "stable_best_fit": raw_fit,
            "raw_best_fit": raw_fit,
            "production_influence": False,
            "change_reason": "ticker unavailable",
        }

    store = _load(store_path)
    state = store.get(symbol)
    last_updated = _parse_dt((state or {}).get("last_updated"))
    expired = bool(
        last_updated is not None
        and now - last_updated > timedelta(days=MAX_STATE_AGE_DAYS)
    )
    if not isinstance(state, dict) or expired:
        reason = (
            "new setup-horizon thesis"
            if not expired
            else f"prior setup-horizon thesis expired after {MAX_STATE_AGE_DAYS} days"
        )
        state = _new_state(symbol, raw_fit, scores, now, reason, revision=1)
        state["history"].append(
            {
                "timestamp": now.isoformat(),
                "raw_fit": raw_fit,
                "stable_fit": raw_fit,
                "scores": scores,
            }
        )
        store[symbol] = state
        _save(store, store_path)
        return {
            "version": VERSION,
            "status": "NEW HORIZON THESIS",
            "stable_best_fit": raw_fit,
            "raw_best_fit": raw_fit,
            "held": False,
            "revision": 1,
            "pending_fit": None,
            "pending_count": 0,
            "change_reason": reason,
            "production_influence": False,
            "history_points": len(state["history"]),
            "transition_count": len(state["transitions"]),
        }

    active = str(state.get("active_fit") or "MIXED").upper().strip()
    status = "HORIZON STABLE"
    reason = "same setup horizon remains strongest"
    held = True

    if raw_fit == active:
        state["pending_fit"] = None
        state["pending_count"] = 0
    else:
        if state.get("pending_fit") == raw_fit:
            pending_count = int(state.get("pending_count") or 0) + 1
        else:
            pending_count = 1
        state["pending_fit"] = raw_fit
        state["pending_count"] = pending_count

        active_score = _score_for_fit(scores, active)
        proposed_score = _score_for_fit(scores, raw_fit)
        decisive = bool(
            active_score is not None
            and proposed_score is not None
            and active_score <= 42
            and proposed_score >= 65
            and proposed_score - active_score >= 15
        )

        if decisive or pending_count >= int(replacement_confirmations):
            reason = (
                "current horizon evidence decisively invalidated the prior fit"
                if decisive
                else f"new horizon persisted across {pending_count} consecutive analyses"
            )
            prior = state
            state = _new_state(
                symbol,
                raw_fit,
                scores,
                now,
                reason,
                revision=int(prior.get("revision") or 0) + 1,
                prior=prior,
            )
            active = raw_fit
            status = "HORIZON CHANGED"
            held = False
        else:
            reason = (
                f"raw fit is {raw_fit}, but it has persisted only "
                f"{pending_count}/{int(replacement_confirmations)} required analyses; "
                f"holding {active}"
            )
            status = "HOLDING PRIOR HORIZON"

    state["last_updated"] = now.isoformat()
    state["last_scores"] = scores
    history = list(state.get("history") or [])
    history.append(
        {
            "timestamp": now.isoformat(),
            "raw_fit": raw_fit,
            "stable_fit": active,
            "scores": scores,
        }
    )
    state["history"] = history[-120:]
    state["change_reason"] = reason
    store[symbol] = state
    _save(store, store_path)

    return {
        "version": VERSION,
        "status": status,
        "stable_best_fit": active,
        "raw_best_fit": raw_fit,
        "held": held,
        "revision": int(state.get("revision") or 1),
        "pending_fit": state.get("pending_fit"),
        "pending_count": int(state.get("pending_count") or 0),
        "required_confirmations": int(replacement_confirmations),
        "anchored_at": state.get("anchored_at"),
        "change_reason": reason,
        "production_influence": False,
        "history_points": len(state.get("history") or []),
        "transition_count": len(state.get("transitions") or []),
    }
