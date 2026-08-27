import json
import os
import time
import urllib.parse
import urllib.request


INTRINIO_API_KEY = os.environ.get("INTRINIO_API_KEY", "").strip()
BASE = "https://api-v2.intrinio.com"
_CACHE = {}


def _num(value):
    try:
        return float(value)
    except Exception:
        return None


def get_public_float(symbol):
    """Return the latest reported public-float shares from Intrinio.

    This provider is optional. When no API key is configured, the Analyzer
    continues using SEC shares outstanding and clearly labels it as a proxy.
    """
    symbol = str(symbol or "").upper().strip()
    if not symbol:
        return {"status": "unavailable", "reason": "missing_symbol"}
    if not INTRINIO_API_KEY:
        return {
            "status": "unconfigured",
            "provider": "Intrinio",
            "reason": "missing_intrinio_api_key",
        }

    bucket = int(time.time() // 21600)
    key = (symbol, bucket)
    if key in _CACHE:
        return dict(_CACHE[key])

    query = urllib.parse.urlencode({"api_key": INTRINIO_API_KEY})
    url = (
        f"{BASE}/companies/{urllib.parse.quote(symbol, safe='')}/public_float?"
        + query
    )
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "StockAnalyzer-v2 float research",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        result = {
            "status": "unavailable",
            "provider": "Intrinio",
            "error": str(exc)[:180],
        }
        _CACHE[key] = result
        return dict(result)

    rows = payload.get("public_floats") or []
    valid = []
    for row in rows:
        shares = _num(row.get("public_float_shares"))
        if shares is None or shares <= 0:
            continue
        valid.append(
            {
                "date": row.get("date"),
                "filing_date": row.get("filing_date"),
                "public_float_shares": shares,
                "public_float_value": _num(row.get("public_float_value")),
            }
        )

    if not valid:
        result = {
            "status": "unavailable",
            "provider": "Intrinio",
            "reason": "no_public_float_rows",
        }
        _CACHE[key] = result
        return dict(result)

    valid.sort(
        key=lambda row: (
            str(row.get("date") or ""),
            str(row.get("filing_date") or ""),
        ),
        reverse=True,
    )
    latest = valid[0]
    result = {
        "status": "ok",
        "provider": "Intrinio",
        "public_float_shares": latest["public_float_shares"],
        "public_float_value": latest.get("public_float_value"),
        "float_date": latest.get("date"),
        "filing_date": latest.get("filing_date"),
    }
    _CACHE[key] = result
    return dict(result)
