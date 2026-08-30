# Swing / Longer-Term Historical Outcome Audit — 2026-08-30

## Scope

- Historical end-of-day replay observations: **5657**
- Swing path-labeled observations (+5% before -4% within 5 sessions): **5323**
- Replay dates: **250** across **6 calendar years (2021, 2022, 2023, 2024, 2025, 2026)**
- This is research-only. It does **not** promote any shadow ML or automatically change live Scanner ACTION.

## Main finding: old Swing score is not monotonic enough

| Historical Swing score | N | Avg 5D | Median 5D | 5D higher | +5 before -4 | Avg 20D |
|---|---:|---:|---:|---:|---:|---:|
| 80+ | 183 | -0.75% | -1.56% | 40.4% | 42.8% | -0.99% |
| 65–79 | 1983 | 0.31% | -0.76% | 46.3% | 41.5% | 1.73% |

The 80+ group did **not** outperform the 65–79 group on close-to-close outcomes. That means the old score should not be treated as a calibrated probability or a simple “higher is always better” ranking.

## Extension/chase risk is real

| Prior move before signal | N | Avg 5D | Median 5D | 5D higher | Avg 20D | Median 20D | 20D higher |
|---|---:|---:|---:|---:|---:|---:|---:|
| Prior 20D >= 100% | 163 | -1.38% | -6.08% | 38.7% | -7.73% | -19.94% | 34.6% |
| Prior 20D 60–100% | 253 | 1.83% | -2.69% | 43.9% | 4.21% | -5.92% | 44.7% |
| Prior 5D >= 40% | 294 | -1.84% | -5.20% | 37.8% | -3.03% | -12.78% | 36.6% |

The clearest danger zone is a stock that has **already run 100%+ over the prior 20 sessions**. Its historical median next-20-session return was strongly negative. The current off-hours list should therefore be interpreted with explicit extension/chase caution until the newer daily rank has its own forward sample.

## Stronger Swing research pattern: ignition after prior weakness

Frozen research candidate:

**trend_return_20d_pct <= -1.77 AND day_pct >= 6.797**

- Full history: **N=675**, target-before-stop **51.0%** vs **39.9%** for the rest, lift **11.10 percentage points**
- Confirmation split: **N=296**, target-before-stop **52.0%** vs **38.9%**, lift **13.10 percentage points**
- Discovery split: **N=379**, target-before-stop **50.1%** vs **40.4%**
- Research flag remains **shadow/research-only**. It is evidence that “fresh ignition after prior weakness” may be more useful than simply rewarding the strongest already-extended trend.

## Longer-Term status

The older historical replay's Longer-Term score is **not suitable for production calibration**: its values were capped by limited historical feature coverage, and the replay itself marks the Longer-Term historical dataset as not ready. The newer off-hours daily classifier is materially different (completed daily candles, 10/20/40-session averages, 40-session return, multi-session structure), so it needs its own forward cohort tracking rather than borrowing confidence from the older score.

## Action taken

- Added **offhours_outcome_tracker.py**
- Wired it into the nightly **Momentum Outcome Tracker**
- Each daily off-hours cohort is frozen and evaluated at **1, 3, 5, 10, 20, and 40 trading sessions**
- Tracks close-to-close return, MFE, MAE, SPY return, and excess-vs-SPY
- Summarizes outcomes by **grade, Best Fit, and setup archetype**
- Does not modify intraday ranking, Scanner ACTION, or ML

### Interpretation rule for now

Use the off-hours Swing/Longer-Term list as a **candidate discovery/review tool**, not a validated probability ranking. Pay special attention to extension risk, and compare the new forward cohorts before promoting any score or research flag.
