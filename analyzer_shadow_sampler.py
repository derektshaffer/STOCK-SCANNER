"""Shadow-only Analyzer sampling from the latest durable Scanner snapshot.

This collector exists only to accelerate forward calibration. It never changes
Scanner ranking or production Analyzer decisions. It runs only on a trusted
regular-session consolidated Scanner snapshot, analyzes a deterministic
stratified set of candidates, uses an isolated thesis namespace, and syncs the
resulting prediction rows only after the complete sample finishes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

SCAN_LOG_DIR = Path(os.environ.get("SCAN_LOG_DIR", "scan_logs"))
MAX_SYMBOLS = max(1, min(int(os.environ.get("ANALYZER_SHADOW_MAX_SYMBOLS", "5") or 5), 10))
TARGET_RANKS = (1, 3, 8, 15, 25)


def _latest_scan_path():
    path = SCAN_LOG_DIR / "latest_scan.json"
    if path.exists():
        return path
    candidates = sorted(SCAN_LOG_DIR.glob("scan_*.json"))
    return candidates[-1] if candidates else None


def load_latest_scan():
    path = _latest_scan_path()
    if path is None:
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return path, None
    return path, payload if isinstance(payload, dict) else None


def select_shadow_symbols(payload, max_symbols=MAX_SYMBOLS):
    """Pick deterministic strong/mid/weaker candidates without cherry-picking outcomes."""
    payload = payload or {}
    if str(payload.get("session_phase") or "").lower() != "regular":
        return []
    data = payload.get("data") or {}
    provider = str(data.get("live_provider") or "").lower()
    feed = str(data.get("live_feed") or "").lower()
    if provider != "tradier" or feed != "consolidated":
        return []

    candidates = [
        row for row in (payload.get("candidates") or [])
        if str((row or {}).get("symbol") or "").strip()
    ]
    candidates.sort(key=lambda row: int(row.get("rank") or 10_000))
    if not candidates:
        return []

    by_rank = {int(row.get("rank") or 0): row for row in candidates}
    chosen = []
    seen = set()

    for target in TARGET_RANKS:
        row = by_rank.get(target)
        if row is None:
            # Use the nearest available rank, but do so deterministically.
            row = min(
                candidates,
                key=lambda item: (
                    abs(int(item.get("rank") or 10_000) - target),
                    int(item.get("rank") or 10_000),
                ),
            )
        symbol = str(row.get("symbol") or "").upper().strip()
        if symbol and symbol not in seen:
            seen.add(symbol)
            chosen.append(symbol)
        if len(chosen) >= max_symbols:
            return chosen

    for row in candidates:
        symbol = str(row.get("symbol") or "").upper().strip()
        if symbol and symbol not in seen:
            seen.add(symbol)
            chosen.append(symbol)
        if len(chosen) >= max_symbols:
            break
    return chosen


def run_shadow_sample(symbols):
    import stock_analyzer as sa
    from historical_integration import install_historical_analysis
    from ml_integration import install_ml_analysis
    from analyzer_v2_integration import install_v2_analysis
    import prediction_tracker as tracker

    os.environ["ANALYZER_PREDICTION_SOURCE"] = "shadow_sampler"
    os.environ["ANALYZER_THESIS_NAMESPACE"] = "shadow-forward-calibration"

    install_historical_analysis(sa)
    install_ml_analysis(sa)
    install_v2_analysis(sa)

    results = []
    for symbol in symbols:
        try:
            metrics = sa.analyze(symbol)
            integrity = ((metrics.get("decision_v2") or {}).get("live_data_integrity") or {})
            results.append({
                "symbol": symbol,
                "ok": True,
                "live_data_integrity_ok": integrity.get("ok") is True,
                "integrity_reasons": list(integrity.get("reasons") or []),
                "plan_status": (metrics.get("trade_plan") or {}).get("status"),
            })
        except Exception as exc:
            results.append({
                "symbol": symbol,
                "ok": False,
                "error": str(exc)[:220],
            })

    # Each analysis writes locally immediately. Force one final merged durable
    # sync so later symbols are not stranded behind the normal sync interval.
    try:
        rows = tracker._load()
        sync = tracker._sync_remote(rows, force=True)
    except Exception as exc:
        sync = {"enabled": True, "synced": False, "reason": "error", "error": str(exc)[:220]}
    return results, sync


def main():
    path, payload = load_latest_scan()
    if not payload:
        print("ANALYZER_SHADOW_SAMPLE=skipped_no_scan")
        return 0

    symbols = select_shadow_symbols(payload)
    if not symbols:
        print(
            "ANALYZER_SHADOW_SAMPLE=skipped_untrusted_or_nonregular "
            f"scan={path}"
        )
        return 0

    print("ANALYZER_SHADOW_SYMBOLS=" + ",".join(symbols))
    results, sync = run_shadow_sample(symbols)
    print("ANALYZER_SHADOW_RESULTS=" + json.dumps(results, sort_keys=True))
    print("ANALYZER_SHADOW_SYNC=" + json.dumps(sync, sort_keys=True))

    successes = [row for row in results if row.get("ok")]
    trusted = [row for row in successes if row.get("live_data_integrity_ok")]
    print(
        f"ANALYZER_SHADOW_COMPLETE success={len(successes)} "
        f"trusted={len(trusted)} requested={len(symbols)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
