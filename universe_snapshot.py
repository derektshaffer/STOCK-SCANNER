"""Capture a dated point-in-time stock universe for future leakage-safe replay.

The snapshot is intentionally captured after the trading session and is only
eligible for replay sessions strictly *after* its capture date. That prevents
same-day quote/liquidity information from leaking into a historical decision.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import historical_scanner_replay as replay
import stock_scanner as ss


ET = ZoneInfo("America/New_York")
OUTPUT_DIR = Path(os.environ.get("UNIVERSE_SNAPSHOT_DIR", "universe_snapshots"))
SEED_SIZE = max(
    100,
    min(
        int(os.environ.get("UNIVERSE_SNAPSHOT_SEED_SIZE", "450") or 450),
        1200,
    ),
)


def _symbol_hash(symbols):
    canonical = "\n".join(sorted(set(str(s).upper().strip() for s in symbols if str(s).strip())))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_snapshot(now=None):
    now = now or datetime.now(timezone.utc)
    now_et = now.astimezone(ET)
    symbols, source = replay._load_nasdaq_symbol_directory(ss)

    token = (
        os.environ.get("TRADIER_ACCESS_TOKEN", "").strip()
        or os.environ.get("TRADIER_TOKEN", "").strip()
    )
    seed_symbols = []
    quote_eligible = 0
    status = "directory_only"
    error = None
    if token:
        try:
            seed_symbols, quote_eligible = replay._select_seed_universe_from_tradier(
                symbols,
                token,
                SEED_SIZE,
            )
            if seed_symbols:
                status = "replay_ready"
        except Exception as exc:
            error = str(exc)[:240]
            status = "quote_screen_failed"

    capture_date = now_et.date().isoformat()
    return {
        "schema_version": 1,
        "status": status,
        "replay_ready": status == "replay_ready",
        "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
        "captured_date_et": capture_date,
        "replay_eligibility": "strictly_after_capture_date",
        "same_day_replay_allowed": False,
        "source": source,
        "broad_common_stock_count": len(symbols),
        "broad_common_stock_symbols": symbols,
        "broad_symbol_sha256": _symbol_hash(symbols),
        "quote_eligible_count": int(quote_eligible or 0),
        "replay_seed_size_target": SEED_SIZE,
        "replay_seed_count": len(seed_symbols),
        "replay_seed_symbols": seed_symbols,
        "replay_seed_sha256": _symbol_hash(seed_symbols),
        "selection_method": (
            "Nasdaq Trader common-stock directory captured point-in-time, then "
            "Tradier quote metadata screened for stock type, $0.50-$60 price, "
            "positive average volume, and price-band-balanced dollar liquidity."
        ),
        "error": error,
    }


def main():
    payload = build_snapshot()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dated = OUTPUT_DIR / f"universe_{payload['captured_date_et']}.json"
    latest = OUTPUT_DIR / "latest.json"
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    dated.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    print(
        "UNIVERSE_SNAPSHOT "
        f"date={payload['captured_date_et']} status={payload['status']} "
        f"broad={payload['broad_common_stock_count']} "
        f"seed={payload['replay_seed_count']}"
    )


if __name__ == "__main__":
    main()
