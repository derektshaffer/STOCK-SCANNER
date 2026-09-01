# Forward Validation Status

Generated: 2026-09-01T02:36:12.538309+00:00

## Scanner ML — independent live confirmation
- **Samples:** 0/100 — 100 remaining
- **Trading days:** 0/5 — 5 remaining
- **Symbols:** 0/15 — 15 remaining
- **Positive class:** 0/15 — 15 remaining
- **Negative class:** 0/15 — 15 remaining
- **Count gate ready:** NO
- Replay end day: 2026-08-28

## Analyzer calibration
- Schema: 7 / required 9 ⚠️
- **Resolved 60m rows — early read:** 0/30 — 30 remaining
- **Resolved 60m rows — useful:** 0/100 — 100 remaining
- Untrusted rows excluded: 0

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
- **Replay-ready nightly snapshots:** 1/3 — 2 remaining
- First capture: 2026-08-31
- Latest capture: 2026-08-31

## Interpretation
Rules-based Scanner/Analyzer decision support remains usable. Predictive ML and Swing/Longer-Term probability claims remain gated until their independent evidence requirements pass.
