"""Persistent Analyzer strategy-thesis continuity.

The live Analyzer recalculates on every refresh, but a trading thesis must not
reset on every candle. This module keeps one intraday execution thesis per
symbol/session and only permits a material plan-family change for an explicit
reason: invalidation, objective completion, expiry, or a replacement proposal
that persists across multiple analyses.

The state is deliberately independent from ML/training labels. It is a
production state-machine / UI-consistency layer, not a predictive feature.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
THESIS_VERSION = "intraday-thesis-v1"
VALID_FAMILIES = {"breakout", "pullback", "repeat_bounce"}
DEFAULT_REPLACEMENT_CONFIRMATIONS = max(
    2,
    int(os.environ.get("ANALYZER_THESIS_REPLACEMENT_CONFIRMATIONS", "3") or 3),
)
DEFAULT_EXPIRY_MINUTES = max(
    30,
    int(os.environ.get("ANALYZER_INTRADAY_THESIS_EXPIRY_MINUTES", "360") or 360),
)
THESIS_PATH = Path(
    os.environ.get(
        "ANALYZER_THESIS_STATE_PATH",
        str(Path(tempfile.gettempdir()) / "stock-analyzer-thesis-state.json"),
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


def _load(path=None):
    target = Path(path or THESIS_PATH)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _save(payload, path=None):
    target = Path(path or THESIS_PATH)
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


def _session_key(symbol, now):
    now = now.astimezone(ET)
    return f"{str(symbol).upper().strip()}:{now.date().isoformat()}"


def _selected_geometry(plan):
    selected = dict(plan.get("selected") or {})
    return {
        "entry_low": _num(selected.get("entry_low")),
        "entry_high": _num(selected.get("entry_high")),
        "entry_mid": _num(selected.get("entry_mid")),
        "stop": _num(selected.get("stop")),
        "target1": _num(selected.get("target1")),
        "target2": _num(selected.get("target2")),
        "stretch_target": _num(selected.get("stretch_target")),
        "risk_reward": _num(selected.get("risk_reward")),
        "entry_source": selected.get("entry_source"),
        "breakout_source": selected.get("breakout_source"),
        "stop_reason": selected.get("stop_reason"),
        "target1_reason": selected.get("target1_reason"),
        "target2_reason": selected.get("target2_reason"),
        "stretch_reason": selected.get("stretch_reason"),
        "confirmation": selected.get("confirmation"),
    }


def _new_state(symbol, now, plan, reason, revision=1, prior_state=None):
    family = str(plan.get("preferred_plan") or "").lower().strip()
    geometry = _selected_geometry(plan)
    prior_state = prior_state if isinstance(prior_state, dict) else {}
    history = list(prior_state.get("history") or [])[-72:]
    transitions = list(prior_state.get("transitions") or [])[-20:]
    prior_family = str(prior_state.get("active_family") or "").lower().strip()
    if prior_family:
        transitions.append(
            {
                "timestamp": now.astimezone(timezone.utc).isoformat(),
                "from_family": prior_family,
                "to_family": family,
                "reason": reason,
                "from_revision": int(prior_state.get("revision") or 0),
                "to_revision": int(revision),
            }
        )
    return {
        "version": THESIS_VERSION,
        "symbol": str(symbol).upper().strip(),
        "session_key": _session_key(symbol, now),
        "active_family": family,
        "anchored_at": now.astimezone(timezone.utc).isoformat(),
        "last_updated": now.astimezone(timezone.utc).isoformat(),
        "revision": int(revision),
        "change_reason": reason,
        "pending_family": None,
        "pending_count": 0,
        "geometry": geometry,
        "breakout_reference_level": _num(plan.get("breakout_reference_level")),
        "breakout_reference_locked": bool(plan.get("breakout_reference_locked")),
        "status": plan.get("status"),
        "entry_state": plan.get("entry_state"),
        "action": plan.get("action"),
        "confidence": _num(plan.get("confidence")),
        "trigger_seen": bool(plan.get("breakout_trigger_reached")),
        "entry_available_seen": str(plan.get("status") or "").upper() == "ENTRY AVAILABLE",
        "history": history,
        "transitions": transitions[-20:],
    }


def _terminal_reason(state, metrics, plan, now, expiry_minutes):
    invalidated_reason = str(state.get("invalidated_reason") or "").strip()
    if invalidated_reason:
        return invalidated_reason

    price = _num(metrics.get("price"))
    geometry = state.get("geometry") or {}
    stop = _num(geometry.get("stop"))
    target1 = _num(geometry.get("target1"))
    anchored = _parse_dt(state.get("anchored_at"))

    # Do not rely only on the current quote. A target or stop can be touched
    # between Analyzer refreshes and then reverse before the next snapshot.
    # Replay the bars after the thesis anchor in time order. If both barriers
    # are inside one OHLC bar, fail conservatively to the stop because the true
    # intrabar order is unknowable.
    intraday = ((metrics.get("chart_data") or {}).get("intraday") or [])
    ordered = []
    for bar in intraday:
        dt = _parse_dt(bar.get("t") or bar.get("timestamp"))
        if dt is None:
            continue
        if anchored is not None and dt < anchored:
            continue
        ordered.append((dt, bar))
    ordered.sort(key=lambda item: item[0])
    for _dt, bar in ordered:
        high = _num(bar.get("h") or bar.get("high"))
        low = _num(bar.get("l") or bar.get("low"))
        stop_hit = bool(stop is not None and low is not None and low <= stop)
        target_hit = bool(
            target1 is not None and high is not None and high >= target1
        )
        if stop_hit and target_hit:
            return (
                "prior thesis invalidated: stop and Target 1 were both inside "
                "the same bar; scored stop-first conservatively"
            )
        if stop_hit:
            return "prior thesis invalidated: stop/invalidation level reached"
        if target_hit:
            return "prior thesis objective reached: Target 1 reached"

    if price is not None and stop is not None and price <= stop:
        return "prior thesis invalidated: stop/invalidation level reached"
    if price is not None and target1 is not None and price >= target1:
        return "prior thesis objective reached: Target 1 reached"

    family = str(state.get("active_family") or "")
    breakout = plan.get("breakout_structure") or {}
    if family == "breakout" and bool(breakout.get("failed_breakout")):
        return "prior breakout thesis invalidated: breakout failed to hold"

    if anchored is not None:
        age_minutes = max(
            0.0,
            (now.astimezone(timezone.utc) - anchored).total_seconds() / 60.0,
        )
        if age_minutes >= float(expiry_minutes):
            return f"prior intraday thesis expired after {age_minutes:.0f} minutes"
    return None


def _overlay_anchor(plan, state, metrics, *, conservative=False):
    """Keep accepted thesis geometry while new bars update evidence.

    conservative=True is used when a different family is merely being proposed;
    that state can never manufacture a fresh entry. For the same active family,
    an already-qualified ENTRY AVAILABLE may survive only when current price is
    still inside the anchored entry zone.
    """
    family = str(state.get("active_family") or "").lower().strip()
    family_plan = dict(plan.get(family) or plan.get("selected") or {})
    geometry = state.get("geometry") or {}
    for key, value in geometry.items():
        if value is not None:
            family_plan[key] = value

    if family == "breakout":
        level = _num(state.get("breakout_reference_level"))
        if level is not None:
            family_plan["breakout_level"] = level
            plan["breakout_reference_level"] = level
            plan["breakout_reference_locked"] = True

    plan["preferred_plan"] = family
    plan["selected"] = family_plan
    if family != "repeat_bounce":
        plan["primary_plan"] = family
        plan["primary_selected"] = family_plan
        plan["selected_plan_role"] = "primary"

    price = _num(metrics.get("price"))
    low = _num(family_plan.get("entry_low"))
    high = _num(family_plan.get("entry_high"))
    zone = (
        f"${low:.2f}–${high:.2f}"
        if low is not None and high is not None
        else "the anchored entry zone"
    )

    raw_status = str(plan.get("status") or "WAIT").upper().strip()
    raw_action = plan.get("action")

    in_zone = bool(
        price is not None
        and low is not None
        and high is not None
        and low <= price <= high
    )

    # A different-family proposal is always conservative. For the same active
    # family, preserve a real entry only when price is still inside the anchored
    # zone. This lets the app eventually say ENTRY AVAILABLE without allowing
    # a newly recalculated zone to move underneath the current price.
    if not conservative and raw_status == "ENTRY AVAILABLE" and in_zone:
        plan["status"] = "ENTRY AVAILABLE"
        plan["entry_state"] = "ENTRY AVAILABLE"
        plan["action"] = raw_action or f"{family.replace('_', ' ').upper()} ENTRY AVAILABLE"
        plan["entry_instruction"] = (
            f"ENTRY AVAILABLE NOW in {zone}. The entry is using the anchored "
            "thesis geometry; use the displayed stop/invalidation."
        )
    elif not conservative and raw_status == "NO TRADE":
        plan["status"] = "NO TRADE"
        plan["entry_state"] = "NO ENTRY"
        plan["action"] = raw_action or "NO TRADE"
        plan["entry_instruction"] = (
            "NO ENTRY SIGNAL while the active thesis is rejected by the current gates."
        )
    else:
        plan["status"] = "WAIT"
        if price is not None and low is not None and high is not None:
            if in_zone:
                plan["entry_state"] = "TRIGGER TESTING"
                plan["action"] = f"{family.replace('_', ' ').upper()} TRIGGER TESTING"
                plan["entry_instruction"] = (
                    f"ENTRY TRIGGER IS {zone}. The prior thesis remains active; "
                    "wait for the current confirmation/evidence gates to clear."
                )
            elif price > high:
                plan["entry_state"] = "WAIT FOR RETEST"
                plan["action"] = f"WAIT FOR {family.replace('_', ' ').upper()} RETEST"
                plan["entry_instruction"] = (
                    f"DO NOT CHASE. The active thesis still uses {zone}; wait for "
                    "a controlled retest/hold before entry."
                )
            else:
                plan["entry_state"] = "ARMED"
                plan["action"] = f"{family.replace('_', ' ').upper()} PLAN ARMED"
                plan["entry_instruction"] = (
                    f"NEXT ENTRY remains {zone}. The trigger stays fixed unless the "
                    "thesis is explicitly invalidated, completed, or expires."
                )
        else:
            plan["entry_state"] = "WAIT FOR CONFIRMATION"
            plan["action"] = f"{family.replace('_', ' ').upper()} THESIS HOLD"
            plan["entry_instruction"] = (
                "The prior plan family remains active while the current refresh "
                "re-evaluates confirmation."
            )
    return plan


def prepare_intraday_thesis(
    metrics,
    *,
    now=None,
    store_path=None,
    replacement_confirmations=DEFAULT_REPLACEMENT_CONFIRMATIONS,
    expiry_minutes=DEFAULT_EXPIRY_MINUTES,
):
    """Apply intraday plan continuity before readiness/evidence scoring."""
    metrics = metrics or {}
    plan = metrics.get("trade_plan") or {}
    symbol = str(metrics.get("symbol") or "").upper().strip()
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    proposed = str(plan.get("preferred_plan") or "").lower().strip()

    if not symbol or proposed not in VALID_FAMILIES:
        context = {
            "version": THESIS_VERSION,
            "status": "NO THESIS",
            "active_family": proposed or None,
            "proposed_family": proposed or None,
            "held": False,
            "change_reason": "no supported live plan family",
        }
        plan["thesis_continuity"] = context
        metrics["trade_plan"] = plan
        return context

    store = _load(store_path)
    key = _session_key(symbol, now)
    state = store.get(key)

    if not isinstance(state, dict) or state.get("session_key") != key:
        state = _new_state(
            symbol,
            now,
            plan,
            "new intraday thesis for this market session",
            revision=1,
        )
        store[key] = state
        _save(store, store_path)
        context = {
            "version": THESIS_VERSION,
            "status": "NEW THESIS",
            "active_family": proposed,
            "proposed_family": proposed,
            "held": False,
            "revision": 1,
            "anchored_at": state.get("anchored_at"),
            "change_reason": state.get("change_reason"),
        }
        plan["thesis_continuity"] = context
        metrics["trade_plan"] = plan
        return context

    active = str(state.get("active_family") or "").lower().strip()
    terminal = _terminal_reason(state, metrics, plan, now, expiry_minutes)
    if terminal:
        prior_state = state
        state = _new_state(
            symbol,
            now,
            plan,
            terminal,
            revision=int(prior_state.get("revision") or 0) + 1,
            prior_state=prior_state,
        )
        store[key] = state
        _save(store, store_path)
        context = {
            "version": THESIS_VERSION,
            "status": "REPLAN ACCEPTED",
            "active_family": proposed,
            "previous_family": active or None,
            "proposed_family": proposed,
            "held": False,
            "revision": state.get("revision"),
            "anchored_at": state.get("anchored_at"),
            "change_reason": terminal,
        }
        plan["thesis_continuity"] = context
        metrics["trade_plan"] = plan
        return context

    if active in VALID_FAMILIES and proposed != active:
        if state.get("pending_family") == proposed:
            pending_count = int(state.get("pending_count") or 0) + 1
        else:
            pending_count = 1
        state["pending_family"] = proposed
        state["pending_count"] = pending_count
        state["last_updated"] = now.isoformat()

        if pending_count >= int(replacement_confirmations):
            reason = (
                f"replacement plan persisted across {pending_count} consecutive analyses"
            )
            prior_state = state
            state = _new_state(
                symbol,
                now,
                plan,
                reason,
                revision=int(prior_state.get("revision") or 0) + 1,
                prior_state=prior_state,
            )
            store[key] = state
            _save(store, store_path)
            context = {
                "version": THESIS_VERSION,
                "status": "REPLAN ACCEPTED",
                "active_family": proposed,
                "previous_family": active,
                "proposed_family": proposed,
                "held": False,
                "revision": state.get("revision"),
                "anchored_at": state.get("anchored_at"),
                "change_reason": reason,
            }
            plan["thesis_continuity"] = context
            metrics["trade_plan"] = plan
            return context

        _overlay_anchor(plan, state, metrics, conservative=True)
        reason = (
            f"new {proposed.replace('_', ' ')} proposal has appeared only "
            f"{pending_count}/{int(replacement_confirmations)} required times; "
            f"holding prior {active.replace('_', ' ')} thesis"
        )
        context = {
            "version": THESIS_VERSION,
            "status": "HOLDING PRIOR THESIS",
            "active_family": active,
            "proposed_family": proposed,
            "held": True,
            "pending_count": pending_count,
            "required_confirmations": int(replacement_confirmations),
            "revision": int(state.get("revision") or 1),
            "anchored_at": state.get("anchored_at"),
            "change_reason": reason,
        }
        plan["plan_selection_note"] = reason
        plan["thesis_continuity"] = context
        metrics["trade_plan"] = plan
        store[key] = state
        _save(store, store_path)
        return context

    # Same family: preserve the accepted geometry. New bars are allowed to
    # change confidence/evidence/status later, not silently rewrite the levels.
    state["pending_family"] = None
    state["pending_count"] = 0
    state["last_updated"] = now.isoformat()
    _overlay_anchor(plan, state, metrics, conservative=False)
    context = {
        "version": THESIS_VERSION,
        "status": "THESIS STABLE",
        "active_family": active or proposed,
        "proposed_family": proposed,
        "held": True,
        "revision": int(state.get("revision") or 1),
        "anchored_at": state.get("anchored_at"),
        "change_reason": "same accepted thesis remains active",
    }
    plan["thesis_continuity"] = context
    metrics["trade_plan"] = plan
    store[key] = state
    _save(store, store_path)
    return context


def commit_intraday_thesis(metrics, context, *, now=None, store_path=None):
    """Persist the final post-safety-gate state without changing the decision."""
    metrics = metrics or {}
    plan = metrics.get("trade_plan") or {}
    symbol = str(metrics.get("symbol") or "").upper().strip()
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not symbol:
        return False

    store = _load(store_path)
    key = _session_key(symbol, now)
    state = store.get(key)
    if not isinstance(state, dict):
        return False

    state["last_updated"] = now.isoformat()
    state["status"] = plan.get("status")
    state["entry_state"] = plan.get("entry_state")
    state["action"] = plan.get("action")
    state["confidence"] = _num(plan.get("confidence"))
    contract = plan.get("decision_contract") or {}
    if contract and not bool(contract.get("ok", True)):
        state["invalidated_reason"] = (
            "prior thesis invalidated: final decision contract rejected its geometry"
        )
    elif "invalid plan geometry" in str(plan.get("action") or "").lower():
        state["invalidated_reason"] = (
            "prior thesis invalidated: final decision contract rejected its geometry"
        )
    else:
        state.pop("invalidated_reason", None)

    state["entry_available_seen"] = bool(
        state.get("entry_available_seen")
        or str(plan.get("status") or "").upper() == "ENTRY AVAILABLE"
    )

    v2 = metrics.get("decision_v2") or {}
    history = list(state.get("history") or [])
    history.append(
        {
            "timestamp": now.isoformat(),
            "price": _num(metrics.get("price")),
            "family": plan.get("preferred_plan"),
            "revision": int(state.get("revision") or 1),
            "status": plan.get("status"),
            "entry_state": plan.get("entry_state"),
            "confidence": _num(plan.get("confidence")),
            "entry_readiness": _num(v2.get("entry_readiness")),
            "evidence_strength": _num(v2.get("evidence_strength")),
            "potential_score": _num(v2.get("potential_score")),
        }
    )
    state["history"] = history[-72:]

    price = _num(metrics.get("price"))
    geometry = state.get("geometry") or {}
    low = _num(geometry.get("entry_low"))
    high = _num(geometry.get("entry_high"))
    if price is not None and low is not None and high is not None:
        state["trigger_seen"] = bool(
            state.get("trigger_seen") or low <= price <= high or price > high
        )

    store[key] = state
    ok = _save(store, store_path)
    continuity = dict(plan.get("thesis_continuity") or context or {})
    continuity.update(
        {
            "final_status": plan.get("status"),
            "final_entry_state": plan.get("entry_state"),
            "trigger_seen": bool(state.get("trigger_seen")),
            "entry_available_seen": bool(state.get("entry_available_seen")),
            "history_points": len(state.get("history") or []),
            "transition_count": len(state.get("transitions") or []),
        }
    )
    plan["thesis_continuity"] = continuity
    metrics["trade_plan"] = plan
    return ok
