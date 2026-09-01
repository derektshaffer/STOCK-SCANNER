from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

BUCKET_MINUTES = 15
REMOTE_SYNC_SECONDS = max(
    300,
    int(os.environ.get("SCANNER_LIVE_JOURNAL_SYNC_SECONDS", "1800") or 1800),
)
MAX_DAILY_ROWS = 750
# Keep the journal bounded while deliberately sampling below the top-30 durable
# artifact cutoff. The scanner's discovery pool is currently capped near 50,
# so rank 45 gives us a meaningful missed-winner/control sample.
CORE_TOP_RANKS = 5
EXTRA_VALUE_ROWS = 3
CONTROL_RANKS = (15, 30, 45)
LOCAL_DIR = Path(
    os.environ.get("SCANNER_LIVE_JOURNAL_DIR", "scanner_live_journal").strip()
    or "scanner_live_journal"
)
ENABLED = (
    os.environ.get("SCANNER_LIVE_JOURNAL_ENABLED", "").strip().lower()
    in {"1", "true", "yes", "on"}
)
GITHUB_TOKEN = (
    os.environ.get("SCANNER_LEARNING_GITHUB_TOKEN", "").strip()
    or os.environ.get("ANALYZER_GITHUB_TOKEN", "").strip()
    or os.environ.get("GITHUB_TOKEN", "").strip()
)
GITHUB_REPO = (
    os.environ.get(
        "SCANNER_LEARNING_GITHUB_REPO",
        os.environ.get("ANALYZER_GITHUB_REPO", "derektshaffer/STOCK-SCANNER"),
    ).strip()
    or "derektshaffer/STOCK-SCANNER"
)
GITHUB_BRANCH = (
    os.environ.get("SCANNER_LEARNING_GITHUB_BRANCH", "learning-journal").strip()
    or "learning-journal"
)
REMOTE_DIR = (
    os.environ.get("SCANNER_LEARNING_REMOTE_DIR", "scanner_live_journal").strip()
    or "scanner_live_journal"
)


def _num(value):
    try:
        return float(value)
    except Exception:
        return None


def _bucket_start(dt):
    minute = (dt.minute // BUCKET_MINUTES) * BUCKET_MINUTES
    return dt.replace(minute=minute, second=0, microsecond=0)


def _bucket_key(dt, symbol):
    return f"{_bucket_start(dt).isoformat()}:{str(symbol).upper().strip()}"


def _role_priority(role):
    return {
        "actionable": 4,
        "high_score": 3,
        "top": 2,
        "control": 1,
    }.get(str(role or ""), 0)


def _action_priority(row):
    action = str(row.get("scanner_action") or "").upper().strip()
    tier = str(row.get("scanner_action_tier") or "").lower().strip()
    if action == "ANALYZE NOW" or tier == "ready":
        return 5
    if "BREAKOUT" in action or tier == "breakout":
        return 4
    if "BOUNCE" in action or tier == "pullback":
        return 3
    if "WAIT" in action or tier == "watch":
        return 2
    if "CAUTION" in action:
        return 1
    return 0


def _quality_tuple(row):
    opportunity = _num(row.get("opportunity_score"))
    score = _num(row.get("score"))
    rank = _num(row.get("rank"))
    return (
        _action_priority(row),
        opportunity if opportunity is not None else -999.0,
        score if score is not None else -999.0,
        -(rank if rank is not None else 9999.0),
        _role_priority(row.get("sample_role")),
    )


def _compact_row(candidate, rank, now_et, role):
    symbol = str(candidate.get("symbol") or "").upper().strip()
    return {
        "bucket_key": _bucket_key(now_et, symbol),
        "bucket_start_et": _bucket_start(now_et).isoformat(),
        "symbol": symbol,
        "sample_role": role,
        "first_observed_at_et": now_et.isoformat(),
        "last_observed_at_et": now_et.isoformat(),
        "best_observed_at_et": now_et.isoformat(),
        "rank": int(rank),
        "rank_best": int(rank),
        "rank_worst": int(rank),
        "price": _num(candidate.get("price")),
        "day_pct": _num(candidate.get("day_pct")),
        "score": _num(candidate.get("score")),
        "opportunity_score": _num(candidate.get("opportunity_score")),
        "setup_grade": candidate.get("setup_grade"),
        "scanner_action": candidate.get("scanner_action"),
        "scanner_action_tier": candidate.get("scanner_action_tier"),
        "actions_seen": [
            str(candidate.get("scanner_action") or "").upper().strip()
        ]
        if candidate.get("scanner_action")
        else [],
        "timeframe_best_fit": candidate.get("timeframe_best_fit"),
        "feature_version": candidate.get("feature_version"),
        "behavior_feature_version": candidate.get("behavior_feature_version"),
        "live_quote_source": candidate.get("live_quote_source"),
        "live_intraday_source": candidate.get("live_intraday_source"),
        "momentum_5m": _num(candidate.get("momentum_5m")),
        "momentum_15m": _num(candidate.get("momentum_15m")),
        "volume_pace": _num(candidate.get("volume_pace")),
        "distance_from_high_pct": _num(candidate.get("distance_from_high_pct")),
        "distance_from_vwap_pct": _num(candidate.get("distance_from_vwap_pct")),
        "above_vwap": bool(candidate.get("above_vwap")),
        "spread_pct": _num(
            candidate.get("live_spread_pct")
            if candidate.get("live_spread_pct") is not None
            else candidate.get("iex_spread_pct")
        ),
        "liquidity_dollar_volume": _num(candidate.get("liquidity_dollar_volume")),
        "volume_acceleration_ratio": _num(candidate.get("volume_acceleration_ratio")),
        "pullback_quality_score": _num(candidate.get("pullback_quality_score")),
        "sequence_health_score": _num(candidate.get("sequence_health_score")),
        "stair_structure_score": _num(candidate.get("stair_structure_score")),
        "current_pullback_pct": _num(candidate.get("current_pullback_pct")),
        "ongoing_bounce_pct": _num(candidate.get("ongoing_bounce_pct")),
        "breakout_recent": candidate.get("breakout_recent"),
        "breakout_holding": candidate.get("breakout_holding"),
        "failed_breakout": candidate.get("failed_breakout"),
        "tradability_warning_count": len(candidate.get("tradability_warnings") or []),
        "failed_filter_count": len(candidate.get("failed_filters") or []),
        "failed_filters": [
            str(value)[:100]
            for value in (candidate.get("failed_filters") or [])[:4]
        ],
        "setup_flags": [
            str(value)[:100]
            for value in (candidate.get("setup_flags") or [])[:4]
        ],
    }


def select_observations(rows, now_et):
    """Select a bounded learning cohort from every live 2-minute scan.

    Every scan is inspected. Within each 15-minute symbol bucket we later retain
    the strongest/actionable state, while also keeping a few below-cutoff
    controls so false negatives can be studied.
    """
    indexed = list(enumerate(rows or [], start=1))
    selected = {}

    def add(rank, candidate, role):
        symbol = str((candidate or {}).get("symbol") or "").upper().strip()
        if not symbol:
            return
        current = selected.get(symbol)
        row = _compact_row(candidate, rank, now_et, role)
        if current is None or _quality_tuple(row) > _quality_tuple(current):
            selected[symbol] = row

    # Core live candidates.
    for rank, candidate in indexed[:CORE_TOP_RANKS]:
        add(rank, candidate, "top")

    # High-value/actionable rows deeper in the ranking. Search the entire
    # available scanner pool instead of stopping around rank 35 so a late-ranked
    # breakout can still enter the shadow dataset.
    extras = 0
    for rank, candidate in indexed[CORE_TOP_RANKS:]:
        score = _num(candidate.get("opportunity_score"))
        if score is None:
            score = _num(candidate.get("score"))
        actionable = _action_priority(candidate) >= 3
        if actionable or (score is not None and score >= 75.0):
            add(rank, candidate, "actionable" if actionable else "high_score")
            extras += 1
            if extras >= EXTRA_VALUE_ROWS:
                break

    # Deterministic below-cutoff controls for missed-winner / false-negative
    # research. Rank 45 deliberately reaches beyond SCAN_LOG_TOP=30. The total
    # selected rows stays bounded at 11 per bucket (5 + 3 + 3), so a full
    # 4am-8pm session remains below MAX_DAILY_ROWS.
    for control_rank in CONTROL_RANKS:
        if len(indexed) >= control_rank:
            rank, candidate = indexed[control_rank - 1]
            add(rank, candidate, "control")

    return list(selected.values())


def _merge_row(old, new):
    if old is None:
        return dict(new)

    merged = dict(old)
    merged["first_observed_at_et"] = min(
        str(old.get("first_observed_at_et") or new.get("first_observed_at_et") or ""),
        str(new.get("first_observed_at_et") or old.get("first_observed_at_et") or ""),
    )
    merged["last_observed_at_et"] = max(
        str(old.get("last_observed_at_et") or ""),
        str(new.get("last_observed_at_et") or ""),
    )

    old_actions = [str(x) for x in (old.get("actions_seen") or []) if str(x)]
    new_actions = [str(x) for x in (new.get("actions_seen") or []) if str(x)]
    merged["actions_seen"] = list(dict.fromkeys(old_actions + new_actions))[:12]

    old_rank = _num(old.get("rank_best"))
    new_rank = _num(new.get("rank"))
    ranks = [value for value in (old_rank, new_rank) if value is not None]
    if ranks:
        merged["rank_best"] = int(min(ranks))

    old_worst = _num(old.get("rank_worst"))
    ranks = [value for value in (old_worst, new_rank) if value is not None]
    if ranks:
        merged["rank_worst"] = int(max(ranks))

    # Keep the strongest/actionable state seen inside this 15-minute bucket,
    # not merely the last quote in the bucket.
    if _quality_tuple(new) > _quality_tuple(old):
        first = merged.get("first_observed_at_et")
        last = merged.get("last_observed_at_et")
        actions = merged.get("actions_seen")
        rank_best = merged.get("rank_best")
        rank_worst = merged.get("rank_worst")
        merged.update(new)
        merged["first_observed_at_et"] = first
        merged["last_observed_at_et"] = last
        merged["actions_seen"] = actions
        merged["rank_best"] = rank_best
        merged["rank_worst"] = rank_worst
        merged["best_observed_at_et"] = new.get("last_observed_at_et")

    return merged


def _load_local(day):
    path = LOCAL_DIR / f"live_{day.isoformat()}.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else []
    except Exception:
        return []


def _write_local(day, rows):
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    path = LOCAL_DIR / f"live_{day.isoformat()}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _merge_groups(*groups):
    merged = {}
    for rows in groups:
        for row in rows or []:
            key = str(row.get("bucket_key") or "")
            if not key:
                continue
            merged[key] = _merge_row(merged.get(key), row)
    ordered = sorted(
        merged.values(),
        key=lambda row: (
            str(row.get("bucket_start_et") or ""),
            int(row.get("rank_best") or 9999),
            str(row.get("symbol") or ""),
        ),
    )
    if len(ordered) > MAX_DAILY_ROWS:
        # Bounded size protects the GitHub Contents API. With current selection
        # rules the normal maximum is below this ceiling.
        ordered = ordered[-MAX_DAILY_ROWS:]
    return ordered


def _github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "scanner-live-learning-journal/1.0",
    }


def _remote_path(day):
    return f"{REMOTE_DIR}/live_{day.isoformat()}.json"


def _contents_url(path):
    encoded = "/".join(
        urllib.parse.quote(part, safe="") for part in str(path).split("/")
    )
    return f"https://api.github.com/repos/{GITHUB_REPO.strip('/')}/contents/{encoded}"


def _get_remote(path):
    url = _contents_url(path) + "?" + urllib.parse.urlencode({"ref": GITHUB_BRANCH})
    req = urllib.request.Request(url, headers=_github_headers())
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return [], None
        raise

    raw = payload.get("content")
    if payload.get("encoding") == "base64" and raw:
        decoded = base64.b64decode("".join(str(raw).split())).decode("utf-8")
        rows = json.loads(decoded)
        return (rows if isinstance(rows, list) else []), payload.get("sha")
    return [], payload.get("sha")


def _put_remote(path, rows, sha=None):
    body = {
        "message": (
            "Sync live Scanner learning journal "
            + datetime.now().strftime("%Y-%m-%d %H:%M")
        ),
        "content": base64.b64encode(
            json.dumps(rows, separators=(",", ":")).encode("utf-8")
        ).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        body["sha"] = sha
    req = urllib.request.Request(
        _contents_url(path),
        data=json.dumps(body).encode("utf-8"),
        headers={**_github_headers(), "Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=15):
        return True


def _last_sync_path(day):
    return LOCAL_DIR / f".last_remote_sync_{day.isoformat()}"


def _sync_due(day, force=False):
    if force:
        return True
    path = _last_sync_path(day)
    if not path.exists():
        return True
    try:
        last = float(path.read_text(encoding="utf-8"))
    except Exception:
        return True
    return time.time() - last >= REMOTE_SYNC_SECONDS


def sync_remote(day, local_rows, force=False):
    if not ENABLED:
        return {"enabled": False, "synced": False, "reason": "disabled"}
    if not GITHUB_TOKEN:
        return {"enabled": True, "synced": False, "reason": "missing_token"}
    if not _sync_due(day, force=force):
        return {"enabled": True, "synced": False, "reason": "interval"}

    path = _remote_path(day)
    last_error = None
    for attempt in range(3):
        try:
            remote, sha = _get_remote(path)
            merged = _merge_groups(remote, local_rows)
            if merged == remote:
                LOCAL_DIR.mkdir(parents=True, exist_ok=True)
                _last_sync_path(day).write_text(str(time.time()), encoding="utf-8")
                return {
                    "enabled": True,
                    "synced": False,
                    "reason": "no_change",
                    "path": path,
                    "count": len(merged),
                }
            _put_remote(path, merged, sha=sha)
            LOCAL_DIR.mkdir(parents=True, exist_ok=True)
            _last_sync_path(day).write_text(str(time.time()), encoding="utf-8")
            return {
                "enabled": True,
                "synced": True,
                "path": path,
                "count": len(merged),
                "branch": GITHUB_BRANCH,
            }
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code not in {409, 422} or attempt >= 2:
                break
            time.sleep(0.5 * (attempt + 1))
        except Exception as exc:
            last_error = str(exc)[:180]
            break

    return {
        "enabled": True,
        "synced": False,
        "reason": "error",
        "error": last_error or "unknown",
        "path": path,
    }


def capture_live_scan(rows, now_et):
    """Inspect every live app scan and retain a bounded, durable learning journal."""
    if not ENABLED:
        return {"enabled": False, "captured": 0, "reason": "disabled"}

    selected = select_observations(rows, now_et)
    if not selected:
        return {"enabled": True, "captured": 0, "reason": "no_rows"}

    day = now_et.date()
    local = _load_local(day)
    merged = _merge_groups(local, selected)
    path = _write_local(day, merged)

    critical = any(
        _action_priority(row) >= 5
        or (_num(row.get("opportunity_score")) or -999.0) >= 90.0
        for row in selected
    )
    near_session_end = now_et.hour == 19 and now_et.minute >= 45
    sync = sync_remote(day, merged, force=bool(critical or near_session_end))

    return {
        "enabled": True,
        "captured": len(selected),
        "local_count": len(merged),
        "local_path": str(path),
        "remote": sync,
    }
