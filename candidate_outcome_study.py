"""Passive, immutable outcome observations. Importing this module starts nothing.

A caller may archive a completed publication and later attach outcome receipts.
Legacy outputs remain UNKNOWN for decision returns: this module cannot confer
whole-path certification. Future prices never enter the frozen publication.
"""
import hashlib
import json
import math
import os
import tempfile
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta, timezone
from pathlib import Path

MINUTE = timedelta(minutes=1)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def instant(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timezone required')
    return result.astimezone(timezone.utc)


def persist(root, kind, identity, body):
    """First receipt is immutable; conflicting reuse fails instead of replacing."""
    path = Path(root) / kind / (digest(identity) + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical(body)
    # Publish a fully flushed inode without replacing an existing identity.
    # A crash before link leaves only a temporary file, never truncated evidence.
    fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_text() != data:
                raise ValueError('immutable identity conflict')
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(temporary)
    return path


def freeze_scan(root, payload, *, recorded_at, source_sha256):
    # Archive exactly the supplied population/order, before receiving outcomes.
    payload = json.loads(canonical(payload))
    recorded = instant(recorded_at)
    started = instant(payload['scan_time_utc'])
    if recorded < started:
        raise ValueError('publication predates scan')
    decision = payload.get('decision_time_utc')
    if decision and not started <= instant(decision) <= recorded:
        raise ValueError('invalid decision ordering')
    snapshots = []
    for position, candidate in enumerate(payload['candidates'], 1):
        body = dict(scan_id=payload['scan_id'], position=position,
                    symbol=candidate['symbol'], candidate=candidate,
                    scan_time_utc=payload['scan_time_utc'],
                    decision_time_utc=decision, recorded_at=recorded_at,
                    source_sha256=source_sha256, publication_hash=digest(payload),
                    input_manifest=payload.get('decision_manifest'),
                    evidence_status='UNKNOWN',
                    reason='No recognized whole-path decision certificate',
                    canonical_entry=False)
        body['snapshot_id'] = digest([body['scan_id'], position, body['symbol']])
        persist(root, 'snapshots', body['snapshot_id'], body)
        snapshots.append(body)
    return snapshots


def attach_analyzer(root, snapshot, analyzer):
    if analyzer.get('source_scan_id') != snapshot['scan_id'] or analyzer.get('symbol') != snapshot['symbol']:
        raise ValueError('analyzer snapshot identity mismatch')
    if instant(analyzer['timestamp']) < instant(snapshot['scan_time_utc']):
        raise ValueError('analyzer predates source scan')
    # An Analyzer can run before the later artifact upload, so compare the
    # source decision when present, not the archive's upload timestamp.
    decision = snapshot.get('decision_time_utc')
    if decision and instant(analyzer['timestamp']) < instant(decision):
        raise ValueError('analyzer predates scanner decision')
    body = dict(snapshot_id=snapshot['snapshot_id'], analyzer=json.loads(canonical(analyzer)),
                causal_order='VERIFIED_TIMES' if decision else 'UNKNOWN_EXACT_SCANNER_DECISION')
    persist(root, 'analyzers', [snapshot['snapshot_id'], analyzer['id']], body)
    return body


def manual_observation(symbol, price, timestamp=None):
    if timestamp:
        instant(timestamp)
    return dict(symbol=symbol, price=price, timestamp=timestamp,
                origin='USER_CONSIDERED_ENTRY', canonical_entry=False,
                decision_state='UNKNOWN')


def valid_bars(rows, *, symbol, cutoff):
    selected = {}
    for row in rows:
        if row.get('symbol') != symbol:
            continue
        start = instant(row['t'])
        if start.second or start.microsecond:
            raise ValueError('outcome bars must have minute-aligned starts')
        if start + MINUTE > cutoff:
            continue
        values = [row.get(k) for k in ('o', 'h', 'l', 'c')]
        if any(type(x) not in (float, int) or not math.isfinite(x) or x <= 0 for x in values):
            raise ValueError('invalid outcome bar')
        if not row['l'] <= min(row['o'], row['c']) <= max(row['o'], row['c']) <= row['h']:
            raise ValueError('invalid outcome range')
        if start in selected and selected[start] != row:
            raise ValueError('conflicting outcome revision requires separate receipt')
        selected[start] = row
    return [(t, selected[t]) for t in sorted(selected)]


def window_observation(bars, start, end, cutoff):
    rows = [(t, b) for t, b in bars if t >= start and t + MINUTE <= min(end, cutoff)]
    result = dict(start=start.isoformat(), end=end.isoformat(), bars=len(rows),
                  return_pct='UNKNOWN', reference_status='UNKNOWN',
                  high='UNKNOWN', low='UNKNOWN', close='UNKNOWN',
                  observed_high='UNKNOWN', observed_low='UNKNOWN',
                  time_to_observed_high_seconds='UNKNOWN', time_to_observed_low_seconds='UNKNOWN')
    if not rows:
        result['status'] = 'UNKNOWN_NO_COMPLETE_BARS'
        return result
    first = start.replace(second=0, microsecond=0)
    if first < start:
        first += MINUTE
    expected = max(0, int((end - first).total_seconds() // 60))
    complete = cutoff >= end and len(rows) == expected and expected > 0
    high = max(rows, key=lambda pair: pair[1]['h'])
    low = min(rows, key=lambda pair: pair[1]['l'])
    result.update(status='COMPLETE_MINUTE_COVERAGE' if complete else 'PARTIAL_OBSERVED_ONLY',
                  observed_high=high[1]['h'], observed_low=low[1]['l'],
                  high_bar_start=high[0].isoformat(), low_bar_start=low[0].isoformat(),
                  time_to_observed_high_seconds=(high[0]-start).total_seconds(),
                  time_to_observed_low_seconds=(low[0]-start).total_seconds())
    if complete:
        result.update(high=high[1]['h'], low=low[1]['l'])
    # A completed-minute close, never an interpolated exact-time fill.
    last_t, last = rows[-1]
    if cutoff >= end and timedelta(0) <= end-(last_t+MINUTE) < MINUTE:
        result.update(close=last['c'], close_bar_end=(last_t+MINUTE).isoformat())
    return result


def label_observations(snapshot, rows, *, sessions, cutoff, receipt_sha256):
    """Sessions are explicit provider/calendar boundaries, with stable names.

    Without an exact decision, begin AFTER the known publication. These are
    post-publication observations, never retroactively certified signal returns.
    Missing intervals remain partial; Monday is never merged into Friday.
    """
    cutoff = instant(cutoff)
    anchor = instant(snapshot.get('decision_time_utc') or snapshot['recorded_at'])
    bars = valid_bars(rows, symbol=snapshot['symbol'], cutoff=cutoff)
    body = dict(snapshot_id=snapshot['snapshot_id'], snapshot_hash=digest(snapshot),
                symbol=snapshot['symbol'], receipt_sha256=receipt_sha256,
                cutoff=cutoff.isoformat(), anchor=anchor.isoformat(),
                anchor_semantics='DECISION' if snapshot.get('decision_time_utc') else 'POST_PUBLICATION_ONLY',
                decision_returns='UNKNOWN', reason=snapshot['reason'], windows={})
    for minutes in (5, 15, 30, 60):
        end = anchor + minutes * MINUTE
        body['windows'][f'+{minutes}m'] = window_observation(bars, anchor, end, cutoff)
    prior_end = None
    for name, bounds in sessions.items():
        start, end = map(instant, bounds)
        if start >= end or (prior_end is not None and start < prior_end):
            raise ValueError('session ordering/overlap')
        prior_end = end
        if end <= anchor:
            continue
        body['windows'][name] = window_observation(bars, max(start, anchor), end, cutoff)
        # Caller supplies explicit, ordered session boundaries. Only the first
        # subsequent RTH session receives next-session labels (never a weekend).
        if (name.endswith('_RTH') and 'next_session' not in body
                and start.astimezone(ZoneInfo('America/New_York')).date()
                > anchor.astimezone(ZoneInfo('America/New_York')).date()):
            body['next_session'] = name
            for minutes in (1, 15, 30):
                body['windows'][f'next_open+{minutes}m'] = window_observation(
                    bars, start, min(end, start + minutes * MINUTE), cutoff)
            opening = next((b for t, b in bars if t == start), None)
            body['next_session_open_price'] = opening['o'] if opening else 'UNKNOWN'
    return body


def record_outcomes(root, snapshot, rows, *, sessions, cutoff, receipt_sha256):
    body = label_observations(snapshot, rows, sessions=sessions, cutoff=cutoff,
                              receipt_sha256=receipt_sha256)
    persist(root, 'outcomes', [snapshot['snapshot_id'], receipt_sha256, cutoff, sessions], body)
    return body


def adjusted_price_return(start_price, end_price, *, price_scale=None):
    """Outcome-only share adjustment. Unknown corporate-action scale fails closed."""
    values = (start_price, end_price, price_scale)
    if any(type(x) not in (int, float) or not math.isfinite(x) or x <= 0 for x in values):
        return 'UNKNOWN'
    return round(100 * (end_price / (start_price * price_scale) - 1), 4)
