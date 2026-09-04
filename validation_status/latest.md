# Forward Validation Status

Generated: 2026-09-04T06:05:29.420743+00:00

## Scanner ML — independent live confirmation
- **Samples:** 38/100 — 62 remaining
- **Trading days:** 1/5 — 4 remaining
- **Symbols:** 38/15 ✅
- **Positive class:** 1/15 — 14 remaining
- **Negative class:** 37/15 ✅
- **Count gate ready:** NO
- Replay end day: 2026-08-31

## Scanner path target — shadow validation
- Target: >= +3% within 60m before -3% failure stop
- Historical replay: 2477 independent rows across 20 days
- Historical endpoint/path disagreement: 131/2477 (5.3%)
- Replay end day: 2026-08-31
- Path model historical status: replay_validated_waiting_live · AUC 0.864 · Brier 0.061
- Endpoint model historical status: replay_validated_waiting_live · AUC 0.859 · Brier 0.0373
- **Independent live samples:** 529/100 ✅
- **Trading days:** 3/5 — 2 remaining
- **Symbols:** 328/15 ✅
- **Positive class:** 69/15 ✅
- **Negative class:** 460/15 ✅
- Endpoint/path disagreements: 45/424 (10.6%)
- **Ready for endpoint-vs-path model comparison:** NO
- Production influence: **OFF**

## Analyzer calibration
- Schema: 9 / required 9 ✅
- **Resolved 60m rows — early read:** 1/30 — 29 remaining
- **Resolved 60m rows — useful:** 1/100 — 99 remaining
- Untrusted rows excluded: 0
- Calibration provenance: interactive_analyzer: 1 rows/1 resolved

## Swing / Longer-Term forward cohorts
- **Swing 5-day resolved — early read:** 0/30 — 30 remaining
- **Swing 5-day resolved — useful:** 0/100 — 100 remaining
- **Longer-Term 20-day resolved — early read:** 0/30 — 30 remaining
- **Longer-Term 20-day resolved — useful:** 0/100 — 100 remaining

## Swing timeframe ML
- Status: **experimental_not_validated**
- Historical validated: NO
- Production enabled: NO
- Samples: 5331 across 250 dates / 386 symbols
- Model AUC: 0.513 · hand-score AUC: 0.5191
- Top-decile target-rate lift: -4.2 pp

## Point-in-time universe coverage
- **Replay-ready nightly snapshots:** 4/3 ✅
- First capture: 2026-08-31
- Latest capture: 2026-09-04

## Historical listing-universe backfill
- Provider: missing_key (key missing)
- Exact replay dates covered: 0/267 (0.0%)
- Missing exact-date memberships: 267
- First covered date: —
- Latest covered date: —
- Latest backfill status: not started

## Interpretation
Rules-based Scanner/Analyzer decision support remains usable. Predictive ML and Swing/Longer-Term probability claims remain gated until their independent evidence requirements pass.
