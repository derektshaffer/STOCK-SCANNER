# Full-Market Explosive Radar

## Purpose

The scanner now performs a lightweight batched quote sweep across the complete
supported US exchange-listed common-stock directory. It no longer starts from a
fixed 1,200-symbol liquidity subset.

The first pass is intentionally cheap. It measures current/session change,
quote freshness, bid/ask spread, relative volume, volume added between scans,
price acceleration between scans, volume velocity, and quiet-to-active
transitions. Only the strongest candidates receive the more expensive Time &
Sales, VWAP, historical-volume, news, timeframe, and ML enrichment.

## Separate scores

- **Explosion Score** measures whether price and participation are igniting.
- **Tradeability Score** measures fresh data, spread, dollar liquidity, and
  participation quality.
- **Setup Score** remains the existing rule-based quality score.

A stock is never hidden solely because Tradeability is low. It remains in the
Explosive Radar with `DATA CHECK`, `CAUTION`, or `NO TRADE` as appropriate.

## Price lanes

- `SUB-$1`: $0.10 through $0.99, displayed with an explicit extreme-risk label.
- `$1-$50`: the primary small-account lane.
- `ABOVE-$50`: still detected so the market sweep is not artificially capped.

Warrants, rights, units, preferred shares, ETFs, test issues, and symbols below
$0.10 remain outside the default radar.

## Coverage integrity

Every snapshot records:

- listing-directory symbols requested;
- quotes received;
- coverage percentage;
- number of eligible sub-$1 stocks checked;
- failed quote batches and warnings;
- the time required for the full sweep.

The UI displays this coverage and raises a prominent warning when coverage is
degraded. Catastrophically low coverage fails the scan rather than replacing a
healthy prior snapshot with a misleading empty result.

## BIAF regression

`full_market_radar_regression_check.py` contains an offline BIAF-style sequence:
a quiet stock near $6.57 accelerates toward $7.85 with rapidly increasing
volume. The test requires that this transition enter the radar, alongside
separate checks for sub-$1 visibility, above-$50 visibility, and removal of the
old 1,200-symbol ceiling.
