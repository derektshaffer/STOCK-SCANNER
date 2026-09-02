"""Offline regressions for full-market discovery and sub-$1 visibility.

This script deliberately avoids network calls.  It reproduces the scanner's
important contracts with synthetic Tradier quote payloads, including a
BIAF-style quiet-to-explosive transition.
"""

from __future__ import annotations

import importlib
import os
import tempfile
import time
from pathlib import Path


def quote(
    symbol,
    *,
    price,
    prev_close,
    volume,
    average_volume,
    high=None,
    low=None,
    spread_pct=0.8,
    timestamp_ms=None,
):
    timestamp_ms = timestamp_ms or int(time.time() * 1000)
    midpoint = float(price)
    half = midpoint * float(spread_pct) / 200.0
    return {
        "symbol": symbol,
        "type": "stock",
        "last": price,
        "prevclose": prev_close,
        "change_percentage": (price / prev_close - 1.0) * 100.0,
        "volume": volume,
        "average_volume": average_volume,
        "open": prev_close,
        "high": high if high is not None else max(price, prev_close),
        "low": low if low is not None else min(price, prev_close),
        "bid": midpoint - half,
        "ask": midpoint + half,
        "trade_date": timestamp_ms,
        "bid_date": timestamp_ms,
        "ask_date": timestamp_ms,
    }


def main():
    os.environ.setdefault("TRADIER_ACCESS_TOKEN", "offline-test-token")
    discovery = importlib.import_module("scanner_discovery")

    now = time.time()
    biaf_history = [
        {"t": now - 360, "p": 6.57, "v": 2_000},
        {"t": now - 180, "p": 6.60, "v": 5_000},
    ]
    biaf = discovery._radar_row(
        "BIAF",
        quote(
            "BIAF",
            price=7.85,
            prev_close=6.57,
            volume=180_000,
            average_volume=224_302,
            high=7.85,
            low=6.55,
            spread_pct=0.7,
            timestamp_ms=int(now * 1000),
        ),
        biaf_history,
        now,
    )
    assert biaf is not None
    assert biaf["explosion_score"] >= 55, biaf
    assert (biaf["radar_change_3m_pct"] or 0) >= 18, biaf
    assert discovery._candidate_trigger(biaf), biaf

    sub_dollar = discovery._radar_row(
        "PENNY",
        quote(
            "PENNY",
            price=0.42,
            prev_close=0.31,
            volume=1_500_000,
            average_volume=120_000,
            high=0.43,
            low=0.30,
            spread_pct=2.0,
            timestamp_ms=int(now * 1000),
        ),
        [{"t": now - 180, "p": 0.32, "v": 50_000}],
        now,
    )
    assert sub_dollar is not None
    assert sub_dollar["risk_lane"] == "SUB-$1"
    assert sub_dollar["explosion_score"] >= 65

    expensive = discovery._radar_row(
        "PRICEY",
        quote(
            "PRICEY",
            price=120.0,
            prev_close=108.0,
            volume=900_000,
            average_volume=600_000,
            high=121.0,
            low=107.0,
            spread_pct=0.15,
            timestamp_ms=int(now * 1000),
        ),
        [{"t": now - 180, "p": 110.0, "v": 150_000}],
        now,
    )
    assert expensive is not None
    assert expensive["risk_lane"] == "ABOVE-$50"

    below_floor = discovery._radar_row(
        "TOOLOW",
        quote(
            "TOOLOW",
            price=0.09,
            prev_close=0.05,
            volume=5_000_000,
            average_volume=100_000,
            timestamp_ms=int(now * 1000),
        ),
        [],
        now,
    )
    assert below_floor is None

    selected = discovery._select_candidates([expensive, sub_dollar, biaf], top=20)
    assert {row["symbol"] for row in selected} == {"BIAF", "PENNY", "PRICEY"}

    # The full-market sweep must not silently truncate at the old 1,200 names.
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        discovery.UNIVERSE_CACHE_PATH = temp / "universe.json"
        discovery.STATE_PATH = temp / "state.json"
        symbols = [f"S{i:04d}" for i in range(1_505)] + ["BIAF", "PENNY", "PRICEY"]
        quotes = {
            symbol: quote(
                symbol,
                price=10.0,
                prev_close=9.98,
                volume=10_000,
                average_volume=1_000_000,
                high=10.02,
                low=9.95,
                timestamp_ms=int(now * 1000),
            )
            for symbol in symbols
        }
        quotes["BIAF"] = biaf["_tradier_quote"]
        quotes["PENNY"] = sub_dollar["_tradier_quote"]
        quotes["PRICEY"] = expensive["_tradier_quote"]

        original_universe = discovery.get_or_build_discovery_universe
        original_quotes = discovery._quote_rows
        try:
            discovery.get_or_build_discovery_universe = lambda token, predicate: (
                symbols,
                {
                    "source": "synthetic_full_market",
                    "market_date": "test",
                    "symbols": len(symbols),
                    "cache_hit": False,
                    "directory_stale_fallback": False,
                },
            )
            discovery._quote_rows = lambda requested, token: (
                {symbol: quotes[symbol] for symbol in requested},
                {
                    "batches": 6,
                    "failed_batches": 0,
                    "batch_errors": [],
                    "quote_sweep_seconds": 0.01,
                },
            )
            found, meta = discovery.discover_tradier_candidates(
                "token",
                lambda symbol: True,
                top=20,
            )
        finally:
            discovery.get_or_build_discovery_universe = original_universe
            discovery._quote_rows = original_quotes

        assert meta["requested_symbols"] == len(symbols), meta
        assert meta["requested_symbols"] > 1_200, meta
        assert meta["coverage_pct"] == 100.0, meta
        found_symbols = {row["symbol"] for row in found}
        assert {"BIAF", "PENNY", "PRICEY"}.issubset(found_symbols), found_symbols

    # The main scanner's base filter must allow a sub-$1 stock into analysis;
    # risk/liquidity can still make it a near miss or rejection.
    scanner = importlib.import_module("stock_scanner")
    sample = {
        "symbol": "PENNY",
        "price": 0.42,
        "day_pct": 20.0,
        "market_session": "regular",
        "liquidity_source": "tradier_consolidated",
        "liquidity_dollar_volume": 2_000_000,
        "dollar_volume": 2_000_000,
        "intraday_range_pct": 20.0,
        "distance_from_high_pct": 1.0,
        "above_vwap": True,
        "spread_pct": 1.5,
        "live_price_available": True,
        "live_price_age_seconds": 10.0,
        "live_bonus": 0.0,
        "news_bonus": 0.0,
    }
    failures = scanner.evaluate_base_filters(sample)
    assert not any("price < $1" in reason for reason in failures), failures
    warnings = scanner.evaluate_tradability_warnings(sample)
    assert any("SUB-$1 EXTREME RISK" in warning for warning in warnings), warnings

    # Large candidate quote lists must use POST batches rather than one GET URL.
    tradier = importlib.import_module("tradier_live")
    calls = []
    original_post = tradier.post_quotes
    try:
        tradier.post_quotes = lambda batch, token: (
            calls.append(list(batch))
            or {symbol: {"symbol": symbol} for symbol in batch}
        )
        output = tradier.get_quotes([f"Q{i}" for i in range(605)], "token", batch_size=300)
    finally:
        tradier.post_quotes = original_post
    assert len(calls) == 3, len(calls)
    assert len(output) == 605, len(output)

    print("Full-market radar regressions passed.")
    print("- full universe is not capped at 1,200")
    print("- BIAF-style ignition is detected")
    print("- $0.10-$0.99 and >$50 lanes remain visible")
    print("- tradeability stays separate from detection")
    print("- large quote lists use POST batching")


if __name__ == "__main__":
    main()
