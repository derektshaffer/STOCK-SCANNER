"""Optional historical U.S. listing-universe backfill.

Alpha Vantage LISTING_STATUS can return the active U.S. equity universe as of
an explicit historical date. This module caches those exact-date membership
snapshots for replay. It is intentionally optional: without an API key it exits
successfully without inventing coverage or weakening any fallback labels.

Free-tier usage is protected by a conservative per-run request cap.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

API_BASE = "https://www.alphavantage.co/query"
OUTPUT_DIR = Path(
    os.environ.get(
        "HISTORICAL_LISTING_UNIVERSE_DIR",
        "historical_universes/alpha_vantage",
    )
)
SCANNER_REPLAY_PATH = Path("outcome_reports/outcomes_historical_replay.json")
TIMEFRAME_REPLAY_PATH = Path("timeframe_replay/timeframe_historical_replay.json")
DEFAULT_MAX_DATES = max(
    1,
    min(int(os.environ.get("HISTORICAL_LISTING_MAX_DATES_PER_RUN", "20") or 20), 24),
)


def _parse_iso_date(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except Exception:
        return None


def _symbol_hash(symbols):
    text = "\n".join(sorted(set(symbols)))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _security_name_looks_common(name):
    text = f" {str(name or '').upper()} "
    excluded = (
        " WARRANT ",
        " WARRANTS ",
        " UNIT ",
        " UNITS ",
        " RIGHT ",
        " RIGHTS ",
        " PREFERRED ",
        " PREFERENCE ",
        " DEPOSITARY SHARES ",
        " DEPOSITARY SHARE ",
    )
    return not any(marker in text for marker in excluded)


def _symbol_looks_common(symbol):
    symbol = str(symbol or "").upper().strip()
    if not symbol or len(symbol) > 10:
        return False
    if any(marker in symbol for marker in ("$", "/", "^")):
        return False
    return all(ch.isalnum() or ch in {".", "-"} for ch in symbol)


def parse_listing_status_csv(text, as_of_date):
    """Return common-stock membership from one historical active-state CSV."""
    reader = csv.DictReader(io.StringIO(str(text or "")))
    symbols = []
    records = []
    seen = set()
    for row in reader:
        symbol = str(row.get("symbol") or "").upper().strip()
        name = str(row.get("name") or "").strip()
        exchange = str(row.get("exchange") or "").upper().strip()
        asset_type = str(row.get("assetType") or "").lower().strip()
        status = str(row.get("status") or "").lower().strip()

        if asset_type != "stock":
            continue
        if status and status != "active":
            continue
        if exchange.startswith("OTC") or exchange in {"PINK", "GREY"}:
            continue
        if not _symbol_looks_common(symbol):
            continue
        if not _security_name_looks_common(name):
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        symbols.append(symbol)
        records.append(
            {
                "symbol": symbol,
                "name": name,
                "exchange": exchange,
                "ipo_date": row.get("ipoDate"),
                "delisting_date": row.get("delistingDate"),
                "status": status or "active",
            }
        )

    symbols.sort()
    records.sort(key=lambda item: item["symbol"])
    return {
        "schema_version": 1,
        "source": "alpha_vantage_listing_status",
        "state": "active",
        "as_of_date": as_of_date.isoformat(),
        "membership_semantics": "active_us_equity_universe_as_of_date",
        "symbol_count": len(symbols),
        "symbols": symbols,
        "symbol_sha256": _symbol_hash(symbols),
        "records": records,
    }


def fetch_listing_status(as_of_date, api_key, timeout=45):
    query = urllib.parse.urlencode(
        {
            "function": "LISTING_STATUS",
            "date": as_of_date.isoformat(),
            "state": "active",
            "apikey": api_key,
        }
    )
    req = urllib.request.Request(
        f"{API_BASE}?{query}",
        headers={"User-Agent": "stock-scanner-historical-universe/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")

    stripped = body.lstrip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}
        message = (
            payload.get("Information")
            or payload.get("Note")
            or payload.get("Error Message")
            or "Unexpected JSON response"
        )
        raise RuntimeError(str(message)[:300])

    parsed = parse_listing_status_csv(body, as_of_date)
    if int(parsed.get("symbol_count") or 0) < 100:
        raise RuntimeError(
            f"Historical listing response for {as_of_date} contained only "
            f"{parsed.get('symbol_count')} usable stocks."
        )
    parsed["fetched_at_utc"] = datetime.now(timezone.utc).isoformat()
    return parsed


def _dates_from_scanner_replay(path=SCANNER_REPLAY_PATH):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return set()
    out = set()
    for row in payload.get("observations") or []:
        day = _parse_iso_date(row.get("scan_time_et") or row.get("trading_date"))
        if day:
            out.add(day)
    return out


def _dates_from_timeframe_replay(path=TIMEFRAME_REPLAY_PATH):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return set()
    out = set()
    for row in payload.get("observations") or []:
        day = _parse_iso_date(row.get("as_of") or row.get("replay_day"))
        if day:
            out.add(day)
    return out


def target_replay_dates():
    explicit = os.environ.get("HISTORICAL_LISTING_DATES", "").strip()
    out = set()
    if explicit:
        for raw in explicit.replace(";", ",").split(","):
            day = _parse_iso_date(raw)
            if day:
                out.add(day)
    out.update(_dates_from_scanner_replay())
    out.update(_dates_from_timeframe_replay())
    return sorted(out)


def snapshot_path(as_of_date, directory=None):
    directory = Path(directory or OUTPUT_DIR)
    return directory / f"universe_{as_of_date.isoformat()}.json"


def load_cached_historical_universes(directory=None):
    directory = Path(directory or OUTPUT_DIR)
    rows = {}
    if not directory.exists():
        return rows
    for path in sorted(directory.glob("universe_????-??-??.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            day = _parse_iso_date(payload.get("as_of_date"))
        except Exception:
            continue
        if (
            day is None
            or payload.get("source") != "alpha_vantage_listing_status"
            or int(payload.get("symbol_count") or 0) < 100
        ):
            continue
        symbols = [
            str(symbol).upper().strip()
            for symbol in payload.get("symbols") or []
            if str(symbol).strip()
        ]
        if len(symbols) < 100:
            continue
        rows[day] = {**payload, "symbols": symbols}
    return rows


def exact_historical_universe(replay_day, snapshots=None):
    snapshots = snapshots if snapshots is not None else load_cached_historical_universes()
    return (snapshots or {}).get(replay_day)


def history_seed_candidates(
    snapshots,
    *,
    exclude_symbols=None,
    budget=250,
):
    """Bounded deterministic expansion from exact-date historical memberships.

    Frequency rewards names that occur on several covered replay dates, while a
    stable hash tie-break prevents current market capitalization/liquidity from
    deciding which historical names get a chance to load history.
    """
    exclude = {
        str(symbol).upper().strip()
        for symbol in (exclude_symbols or [])
        if str(symbol).strip()
    }
    counts = Counter()
    for payload in (snapshots or {}).values():
        counts.update(
            str(symbol).upper().strip()
            for symbol in payload.get("symbols") or []
            if str(symbol).strip() and str(symbol).upper().strip() not in exclude
        )

    ranked = sorted(
        counts,
        key=lambda symbol: (
            -counts[symbol],
            hashlib.sha256(symbol.encode("utf-8")).hexdigest(),
            symbol,
        ),
    )
    return ranked[: max(0, int(budget or 0))]


def backfill(api_key=None, max_dates=None):
    api_key = str(api_key or os.environ.get("ALPHA_VANTAGE_API_KEY", "")).strip()
    targets = target_replay_dates()
    cached = load_cached_historical_universes()
    missing = [day for day in targets if day not in cached]

    if not api_key:
        return {
            "status": "skipped_missing_key",
            "target_dates": len(targets),
            "cached_dates": len(cached),
            "missing_dates": len(missing),
            "fetched_dates": 0,
            "errors": [],
        }

    max_dates = max(1, min(int(max_dates or DEFAULT_MAX_DATES), 24))
    # Recent dates first so current validation/replay windows improve quickly.
    chosen = sorted(missing, reverse=True)[:max_dates]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fetched = []
    errors = []
    for day in chosen:
        try:
            payload = fetch_listing_status(day, api_key)
            snapshot_path(day).write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            fetched.append(
                {"date": day.isoformat(), "symbols": payload["symbol_count"]}
            )
        except Exception as exc:
            errors.append({"date": day.isoformat(), "error": str(exc)[:260]})
            # Quota/rate-limit failures should not burn the remainder of the run.
            if "frequency" in str(exc).lower() or "rate" in str(exc).lower():
                break

    cached_after = load_cached_historical_universes()
    return {
        "status": "complete" if not errors else "partial",
        "target_dates": len(targets),
        "cached_dates": len(cached_after),
        "missing_dates": max(0, len(targets) - len(cached_after)),
        "fetched_dates": len(fetched),
        "fetched": fetched,
        "errors": errors,
        "max_dates_per_run": max_dates,
    }


def main():
    result = backfill()
    print("HISTORICAL_LISTING_BACKFILL=" + json.dumps(result, sort_keys=True))
    if result.get("status") == "skipped_missing_key":
        return 0
    if int(result.get("fetched_dates") or 0) > 0 or result.get("errors"):
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUTPUT_DIR / "latest_backfill.json").write_text(
            json.dumps(
                {
                    **result,
                    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
