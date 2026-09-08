"""Nonblocking autocomplete directory; prices never come from this cache."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import urllib.request

import streamlit as st


def _fetch_choices(key, secret):
    if not key or not secret:
        return []
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
               "Accept": "application/json", "User-Agent": "single-stock-analyzer/1.0"}
    for host in ("paper-api.alpaca.markets", "api.alpaca.markets"):
        try:
            req = urllib.request.Request(
                f"https://{host}/v2/assets?status=active&asset_class=us_equity", headers=headers)
            with urllib.request.urlopen(req, timeout=5) as response:
                assets = json.loads(response.read().decode("utf-8"))
            if not isinstance(assets, list):
                continue
            choices = set()
            for asset in assets:
                symbol = str(asset.get("symbol") or "").upper().strip()
                name = str(asset.get("name") or "").strip()
                if symbol and str(asset.get("status") or "active").lower() == "active":
                    choices.add(f"{symbol} — {name}" if name else symbol)
            if choices:
                return sorted(choices)
        except Exception:
            continue
    return []


@st.cache_resource(ttl=21600, show_spinner=False)
def _directory_job(identity, _key, _secret):
    # At most one request per cached credential identity. No Streamlit calls
    # or session-state writes run in the background thread.
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="equity-directory")
    future = executor.submit(_fetch_choices, _key, _secret)
    executor.shutdown(wait=False)
    return future


def equity_choices():
    """Return immediately, even on cold start or an unavailable provider."""
    key = os.environ.get("ALPACA_API_KEY", "").strip()
    secret = os.environ.get("ALPACA_SECRET_KEY", "").strip()
    if key and secret:
        identity = hashlib.sha256((key + "\0" + secret).encode()).hexdigest()
        future = _directory_job(identity, key, secret)
        if future.done():
            try:
                choices = future.result()
                if choices:
                    return choices
            except Exception:
                pass
    # This persisted symbol list is only a search aid. It is never a quote,
    # price fallback, eligibility gate, or substitute for provider validation.
    try:
        payload = json.loads(Path(__file__).with_name("universe_snapshots").joinpath("latest.json").read_text())
        return sorted(set(payload.get("broad_common_stock_symbols") or []))
    except Exception:
        return []
