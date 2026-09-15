"""Causal OHLCV admission at the model boundary; no fetching or training."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math


def valid_training_bars(rows, symbol, now, as_of, et):
    cutoff = now.astimezone(timezone.utc)
    if as_of:
        try:
            stamp = datetime.fromisoformat(str(as_of).replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                raise ValueError()
            cutoff = min(cutoff, stamp.astimezone(timezone.utc))
        except (ValueError, TypeError):
            raise ValueError('Analysis timestamp malformed; ML training not started') from None
    valid = {}
    for row in rows or []:
        try:
            if not isinstance(row, dict) or any(str(row.get(k) or symbol).upper() != symbol.upper() for k in ('symbol','S')):
                raise ValueError()
            dt = datetime.fromisoformat(str(row['t']).replace('Z','+00:00'))
            if dt.tzinfo is None:
                raise ValueError()
            dt = dt.astimezone(timezone.utc)
            values = {k:float(row[k]) for k in ('o','h','l','c','v')}
            if not all(math.isfinite(v) for v in values.values()) or not (0 < values['l'] <= min(values['o'],values['c']) <= max(values['o'],values['c']) <= values['h'] and values['v'] >= 0):
                raise ValueError()
            if dt.minute % 5 or dt.second or dt.microsecond:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise ValueError('Historical five-minute bars malformed or symbol mismatched; ML training not started') from None
        if dt + timedelta(minutes=5) > cutoff or dt < cutoff-timedelta(days=540):
            continue
        local = dt.astimezone(et)
        if local.weekday() >= 5 or not 570 <= local.hour*60+local.minute < 960:
            continue
        record = dict(t=dt.isoformat(), **values)
        if dt in valid and valid[dt] != record:
            raise ValueError('Conflicting historical candles; ML training not started')
        valid[dt] = record
    return [valid[t] for t in sorted(valid)]


def history_signature(rows, source):
    from analyzer_provider_config import history_identity
    return hashlib.sha256(json.dumps([history_identity(), source, rows], sort_keys=True, separators=(',',':')).encode()).hexdigest()
