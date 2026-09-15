"""Exact Scanner publication identity. No recorder, providers, or state writes."""
from candidate_outcome_study import digest

def launch_publication(payload, symbol):
    """UI-only side channel from the exact rendered payload, never a latest lookup."""
    if not isinstance(payload, dict):
        return None
    kind = 'offhours_daily_timeframe_discovery' if payload.get('mode') == 'offhours_daily_timeframe_discovery' else 'live_momentum'
    if kind == 'live_momentum' and not payload.get('scan_id'):
        return None
    if symbol not in {r.get('symbol') for r in payload.get('candidates', [])}:
        return None
    return {'scan_id': payload.get('scan_id'), 'payload_sha256': digest(payload), 'symbol': symbol, 'publication_kind': kind}

def validate_launch_publication(binding, symbol):
    import re
    if not isinstance(binding, dict) or set(binding) != {'scan_id', 'payload_sha256', 'symbol', 'publication_kind'}:
        return None
    scan_id = binding['scan_id']
    valid_scan = isinstance(scan_id, str) and 0 < len(scan_id) <= 256
    if binding['publication_kind'] == 'offhours_daily_timeframe_discovery' and scan_id is None:
        valid_scan = True
    if (binding['symbol'] != symbol or not valid_scan
            or binding['publication_kind'] not in {'live_momentum', 'offhours_daily_timeframe_discovery'}
            or not isinstance(binding['payload_sha256'], str)
            or not re.fullmatch('[0-9a-f]{64}', binding['payload_sha256'])):
        return None
    return dict(binding)
