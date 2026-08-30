"""Shared subprocess runner for the live Momentum Scanner.

Kept UI-free so both scanner_app.py and the combined app shell can invoke the
same scanner without duplicating provider/env/timeout logic.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

SCAN_FILE = Path("scan_logs/latest_scan.json")


def run_scanner_process(
    *,
    alpaca_key="",
    alpaca_secret="",
    alpaca_live_feed="iex",
    tradier_token="",
    discovery_universe_size="1200",
    timeout_seconds=180,
):
    alpaca_key = str(alpaca_key or "").strip()
    alpaca_secret = str(alpaca_secret or "").strip()
    tradier_token = str(tradier_token or "").strip()
    feed = str(alpaca_live_feed or "iex").strip().lower()
    if feed not in {"iex", "sip"}:
        feed = "iex"

    has_alpaca = bool(alpaca_key and alpaca_secret)
    if not has_alpaca and not tradier_token:
        return {
            "ok": False,
            "message": (
                "No market-data provider is configured. Add either "
                "TRADIER_ACCESS_TOKEN (preferred) or both ALPACA_API_KEY and "
                "ALPACA_SECRET_KEY in Streamlit Secrets."
            ),
            "stdout": "",
            "stderr": "",
            "runtime_seconds": None,
        }

    env = os.environ.copy()
    if has_alpaca:
        env["ALPACA_API_KEY"] = alpaca_key
        env["ALPACA_SECRET_KEY"] = alpaca_secret
    else:
        env.pop("ALPACA_API_KEY", None)
        env.pop("ALPACA_SECRET_KEY", None)
    env["ALPACA_LIVE_FEED"] = feed

    if tradier_token:
        env["TRADIER_ACCESS_TOKEN"] = tradier_token
        env["SCANNER_TRADIER_DISCOVERY"] = "1"
        env["SCANNER_DISCOVERY_UNIVERSE_SIZE"] = str(discovery_universe_size)

    started = time.perf_counter()
    try:
        process = subprocess.run(
            [sys.executable, "stock_scanner.py"],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "message": (
                f"The scanner exceeded its {int(timeout_seconds)}-second timeout."
            ),
            "stdout": "",
            "stderr": "",
            "runtime_seconds": round(time.perf_counter() - started, 1),
        }

    elapsed = round(time.perf_counter() - started, 1)
    stdout = process.stdout or ""
    stderr = process.stderr or ""

    if process.returncode != 0:
        error = stderr.strip() or stdout.strip() or "Unknown scanner error"
        return {
            "ok": False,
            "message": error[-3000:],
            "stdout": stdout,
            "stderr": stderr,
            "runtime_seconds": elapsed,
        }

    ok = SCAN_FILE.exists()
    return {
        "ok": ok,
        "message": (
            f"Fresh scan complete in {elapsed:.1f}s."
            if ok
            else (
                "Scanner ran, but latest_scan.json was not created "
                f"(runtime {elapsed:.1f}s)."
            )
        ),
        "stdout": stdout,
        "stderr": stderr,
        "runtime_seconds": elapsed,
    }
