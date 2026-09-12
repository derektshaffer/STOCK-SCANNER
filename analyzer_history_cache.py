"""Shared disk-backed market-history cache for Analyzer enrichments.

The Analyzer used to download overlapping 5-minute histories independently for
same-ticker historical matching and ML on every launch. This module makes those
consumers share one causal delayed-history snapshot and refreshes only the
recent tail once the cache is warm.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path


CACHE_DIR = Path(
    os.environ.get(
        "ANALYZER_HISTORY_CACHE_DIR",
        str(Path(tempfile.gettempdir()) / "stock-analyzer-history-cache"),
    )
)
DEEP_5M_TTL_SECONDS = max(
    300,
    int(os.environ.get("ANALYZER_DEEP_HISTORY_TTL_SECONDS", "900") or 900),
)
DEEP_5M_REFRESH_DAYS = max(
    3,
    int(os.environ.get("ANALYZER_DEEP_HISTORY_REFRESH_DAYS", "10") or 10),
)
DEEP_5M_WORKERS = max(
    1,
    min(6, int(os.environ.get("ANALYZER_HISTORY_WORKERS", "4") or 4)),
)


class HistoryLoadError(RuntimeError):
    """Incomplete provider history is unavailable, not a training sample deficit."""
    def __init__(self, diagnostics):
        self.diagnostics = diagnostics
        super().__init__(diagnostics["message"])


def history_error_summary(exc):
    """Only allow known diagnostic categories; never echo provider bodies/secrets."""
    text = str(exc)
    codes = sorted(set(re.findall(r"HTTP (\d{3})", text)))
    if codes:
        return "provider HTTP " + "/".join(codes)
    if "Missing ALPACA_API_KEY" in text or "credentials missing" in text:
        return "Alpaca history credentials missing"
    if isinstance(exc, TimeoutError) or "timed out" in text.lower():
        return "provider request timed out"
    if "pagination" in text.lower():
        return "history pagination incomplete"
    if "different symbol" in text:
        return "history symbol mismatch"
    if "malformed" in text:
        return "history response malformed"
    return "history request failed"


def _raise_incomplete(payload):
    errors = payload.get("errors") or ["history request failed"]
    raise HistoryLoadError({
        "status": "history_unavailable", "bar_count": len(payload.get("rows") or []),
        "source": " + ".join(payload.get("sources") or []) or "unavailable",
        "failed_chunks": payload.get("failed_chunks", len(errors)),
        "message": "5-minute history could not be loaded completely: " + "; ".join(errors),
    })


def _parse_dt(value):
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _cache_path(symbol):
    safe = "".join(ch for ch in str(symbol or "").upper() if ch.isalnum() or ch in "._-")
    # The old cache could contain unpaginated data and undetected interior gaps.
    # Keep those files intact, but never reuse them as complete training history.
    return CACHE_DIR / f"{safe or 'UNKNOWN'}-5min-v2.json.gz"


def _load_cache(symbol):
    path = _cache_path(symbol)
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
            return None
        return payload
    except Exception:
        return None


def _write_cache(symbol, payload):
    path = _cache_path(symbol)
    tmp = None
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=CACHE_DIR)
        os.close(fd)
        tmp = Path(name)
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=5) as fh:
            json.dump(payload, fh, separators=(",", ":"), default=str)
        os.replace(tmp, path)
        return True
    except Exception:
        return False
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def _merge_rows(*groups):
    merged = {}
    for rows in groups:
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            ts = str(row.get("t") or row.get("timestamp") or "")
            if ts:
                merged[ts] = row
    return [merged[key] for key in sorted(merged)]


def _chunks(start, end, step_days):
    cursor = start
    out = []
    step = timedelta(days=max(1, int(step_days)))
    while cursor < end:
        chunk_end = min(end, cursor + step)
        out.append((cursor, chunk_end))
        cursor = chunk_end
    return out


def _fetch_chunks(fetch_bars, symbol, chunks):
    rows = []
    sources = []
    errors = []

    def one(pair):
        start, end = pair
        try:
            data, source = fetch_bars(symbol, "5Min", start, end, 10000)
            return list(data or []), str(source or "unavailable"), None
        except Exception as exc:
            return [], "unavailable", history_error_summary(exc)

    with ThreadPoolExecutor(max_workers=DEEP_5M_WORKERS) as pool:
        futures = [pool.submit(one, pair) for pair in chunks]
        for future in as_completed(futures):
            data, source, error = future.result()
            rows.extend(data)
            if data and source and source not in sources:
                sources.append(source)
            if error:
                errors.append(error)

    return _merge_rows(rows), sources, errors


def _filter_rows(rows, start, end):
    out = []
    for row in rows or []:
        dt = _parse_dt(row.get("t"))
        if dt is None:
            continue
        if start <= dt <= end:
            out.append(row)
    return out


def filter_history_rows(rows, start, end):
    """Return cached rows inside an explicit causal datetime window."""
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    return _filter_rows(rows, start, end)


def load_deep_5m_history(
    symbol,
    *,
    end,
    fetch_bars,
    days=540,
    step_days=45,
):
    """Return a shared delayed 5-minute history for historical + ML consumers.

    First load fetches the deep range in parallel chunks. Warm loads use the
    disk cache and refresh only the newest tail, so repeated Analyzer launches
    do not redownload a year of 5-minute bars.
    """
    symbol = str(symbol or "").upper().strip()
    if not symbol:
        return [], "unavailable"

    end = (end or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start = end - timedelta(days=max(30, int(days)))
    now_ts = time.time()
    cached = _load_cache(symbol) or {}
    if cached.get("symbol") != symbol:
        cached = {}
    cached_rows = list(cached.get("rows") or [])
    cached_start = _parse_dt(cached.get("coverage_start"))
    cached_end = _parse_dt(cached.get("coverage_end"))
    fetched_at = float(cached.get("fetched_at") or 0.0)
    cache_age = max(0.0, now_ts - fetched_at) if fetched_at else None

    full_coverage = bool(
        cached.get("complete") is True
        and cached_start is not None
        and cached_start <= start + timedelta(days=2)
        and cached_end is not None
        and cached_end >= end - timedelta(days=2)
    )

    sources = list(cached.get("sources") or [])
    errors = []

    # Share a short failure receipt between historical matching and ML. Retry
    # the full interval after one minute; never train on the partial receipt.
    if cached.get("complete") is False and cache_age is not None and cache_age <= 60:
        if cached_start is not None and cached_start <= start and cached_end is not None and cached_end >= end - timedelta(minutes=1):
            _raise_incomplete(cached)

    if full_coverage and cache_age is not None and cache_age <= DEEP_5M_TTL_SECONDS:
        return _filter_rows(cached_rows, start, end), " + ".join(sources) or "cached 5m history"

    if full_coverage:
        # Historical bars do not change. Refresh only the recent tail.
        refresh_start = end - timedelta(days=DEEP_5M_REFRESH_DAYS)
        fresh, fresh_sources, fresh_errors = _fetch_chunks(
            fetch_bars,
            symbol,
            _chunks(refresh_start, end, step_days),
        )
        rows = _merge_rows(cached_rows, fresh)
        for source in fresh_sources:
            if source not in sources:
                sources.append(source)
        errors.extend(fresh_errors)
    else:
        # Cold cache: one deep fetch, parallelized across bounded chunks.
        fresh, fresh_sources, fresh_errors = _fetch_chunks(
            fetch_bars,
            symbol,
            _chunks(start, end, step_days),
        )
        # A full refresh replaces the old source mix rather than relabelling
        # cached fallback rows as consolidated history.
        rows = fresh
        sources = fresh_sources
        errors.extend(fresh_errors)

    filtered = _filter_rows(rows, start, end)
    payload = {
        "symbol": symbol,
        "fetched_at": now_ts,
        # Successful exhaustive requests establish coverage even on holidays
        # or before a stock was listed. First/last bars cannot detect gaps.
        "coverage_start": start.isoformat(),
        "coverage_end": end.isoformat(),
        "complete": not errors,
        "sources": sources,
        "errors": sorted(set(errors)),
        "failed_chunks": len(errors),
        "rows": filtered,
    }
    _write_cache(symbol, payload)
    if errors:
        _raise_incomplete(payload)

    source_text = " + ".join(sources) if sources else "unavailable"
    return filtered, source_text
