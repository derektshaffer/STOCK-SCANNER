# Integrity-First Audit — Momentum Scanner / Analyzer

**Audit date (PT):** 2026-08-30  
**Repository:** `derektshaffer/STOCK-SCANNER`  
**Production baseline audited:** `main@ee6dc2b8d564bc7c1eb040b5b4328a1c8e7b199d`  
**Remediation branch:** `integrity-first-audit`  
**Pull request:** #53 — Integrity-first audit: harden replay ML validation

## Audit rule

The purpose of this phase is not to maximize scores, win rates, or model output. It is to establish that every production-facing signal is:

1. causal at the historical observation time;
2. measured at the horizon it claims to measure;
3. validated on evidence not reused from training/tuning;
4. robust to missing/stale market data;
5. clearly separated into discovery/setup quality versus entry readiness;
6. prevented from influencing production when its evidence is only exploratory.

A model or score is not considered trustworthy merely because a backtest passes.

## Current integrity verdict

**Rules-based Scanner / Analyzer foundation: materially improved and generally well defended by regression tests.**

**Predictive ML: not yet entitled to a broad production-valid claim.** The original validation-boundary findings were remediated in PR #53 and merged only after the integrity suite passed. Follow-up audit PRs #68-#74 closed additional forward-target parity, advisory-ML leakage, stale-schema/source-integrity, final-contract fail-open, and untrusted-calibration contamination gaps. Live forward evidence is still too sparse to make strong performance claims.

The safest current interpretation is:

- rules-based ranking and trade-plan logic: usable as decision support, subject to the usual market/execution uncertainty;
- replay-backed ML: advisory until its separate live-confirmation gate passes;
- swing / longer-term ML: shadow/research only;
- off-hours Swing / Longer-Term discovery: candidate discovery only, not an entry signal or validated probability.

## Safeguards already passing before this audit

The current code already fixed multiple issues from earlier audits:

- Scanner +15m/+30m/+60m outcomes reject bars more than 3 minutes late.
- Historical intraday continuation uses timestamp matching rather than array offsets.
- Candidate enrichment occurs before the final display shortlist is truncated.
- `latest_scan.json` writes are atomic.
- Scanner and Analyzer use the same midpoint spread formula.
- Repeated intraday scanner observations remain available for path analysis while actionable-event reporting is deduplicated.
- Historical timeframe replay filters point-in-time SEC facts by filing date.
- Historical market-regime features ignore future benchmark bars.
- Swing ML folds are separated by replay date.
- Same-daily-bar target/stop touches are treated as ambiguous when bar data cannot establish order.
- Swing research flags are tracking-only and do not alter production scores.
- Closed/off-hours Analyzer observations are excluded from intraday calibration.
- Scanner stale snapshots are explicitly marked and cannot silently masquerade as fresh handoffs.
- Swing / Longer-Term timeframe-fit scores are kept separate from production intraday rank.
- Off-hours 5-day Swing forward outcomes now use the exact same shared +5% before -4% target as historical Swing ML; same-day target/stop touches remain ambiguous rather than guessed.
- Individually validated Analyzer reversal/bounce/new-high/stair ML submodels remain numerically advisory until the complete production ML gate passes.
- Missing `production_source_ok` metadata fails closed; legacy/stale ML payloads cannot be treated as consolidated-source production evidence.
- Peer blending and ML trade-plan confidence adjustments require the same explicit source-integrity gate rather than trusting `gate_passed` alone.
- The final Analyzer trade-plan contract treats missing live-data integrity evidence as untrusted, so omitted integrity metadata cannot preserve an actionable entry.
- Analyzer calibration and forward-learning cohorts require explicit trusted live-data integrity; stale, non-consolidated, or legacy rows missing integrity metadata remain available for diagnostics but are excluded from calibration.

## Post-audit hardening completed 2026-08-31

### PR #68 — Forward Swing target parity

The off-hours Swing tracker previously measured multi-day return/MFE/MAE but did not resolve the exact +5% before -4% within 5 sessions target used by historical Swing ML. Forward cohorts now use the shared `timeframe_targets.resolve_swing_path_from_bars` definition, backfill older 5-day rows, and report ambiguous same-day target/stop touches separately.

### PR #69 — Advisory individual ML isolation

The Analyzer's composite ML edge was correctly gated, but individually validated reversal, repeat-bounce, new-high, mature-bounce-failure, and stair-reacceleration submodels could still change full-spectrum scenario or reversal math while the overall ML gate was advisory. Those submodels may still be displayed, but their numeric production influence is now disabled until the complete gate passes.

### PR #70 — Missing source-integrity metadata fails closed

A legacy or stale ML payload with a missing `production_source_ok` field was previously treated as source-trusted by the downstream production context. Missing source-integrity evidence now evaluates as false.

### PR #71 — Earlier ML integration uses the same fail-closed gate

Peer blending and trade-plan confidence adjustment previously checked `gate_passed` before the downstream production context was evaluated. They now require status OK, the validation gate, and explicit `production_source_ok=true`. A behavioral regression verifies that a legacy payload cannot change peer blend weight or plan confidence.

### PR #73 — Final trade contract fails closed

The production Analyzer already passed a live-data integrity object into its final trade-plan contract, but the helper itself treated an omitted integrity object as trusted. Missing integrity evidence now blocks an otherwise actionable entry to `WAIT / DATA CHECK`, providing defense in depth for future or alternate callers.

### PR #74 — Calibration learns only from trusted live snapshots

Regular-session Analyzer snapshots were durably recorded even when live-data integrity failed, while the calibration selectors previously filtered only by session/time. That meant a stale or non-consolidated snapshot could be blocked from trading but still teach later calibration. Prediction rows now record live-data integrity, consolidation state, trade/quote age, and integrity reasons. Intraday calibration, Swing/Longer-Term daily calibration, and live Swing research calibration all require explicit `live_data_integrity_ok=true`; legacy rows missing that field fail closed. Calibration schema version 9 invalidates older durable calibration under the stricter sampling contract. Untrusted rows remain available for diagnostics and outcome review but cannot teach score calibration.

All six follow-up PRs (#68-#71 and #73-#74) passed compilation, import checks, provider smoke tests, app-boundary checks, consistency regressions, learning regressions, and Phase 6 historical-challenge regressions before merge.

## Critical findings and remediation

### P0-1 — Replay survivorship could support a falsely strong peer-ML validation claim

The scanner replay intentionally begins from a universe of stocks that are listed/liquid today. The replay itself already discloses this as current-listed/liquid survivorship bias. That is acceptable for accelerated research, but it is not equivalent to a point-in-time historical market universe.

Before this audit, the Analyzer peer model could return `validated=true` from replay-heavy evidence alone. The current production CI smoke test did exactly that: 2,400 peer samples across 198 symbols were reported as validated.

This matters because a validated peer model can contribute up to 30% of the Analyzer's headline ML edge when the same-ticker gate also passes.

**Remediation in PR #53:**

- replay-backed peer ML can earn historical validation but cannot earn full production validation from replay alone;
- a strictly later live holdout is required;
- same-day live rows cannot confirm a replay period ending on that same day;
- minimum live confirmation is 100 samples, 5 trading days, and at least 15 examples of each class;
- the later live holdout must beat its naive probability baseline and satisfy an AUC floor;
- until then the model reports `replay_validated_waiting_live` and stays advisory.

### P0-2 — Scanner ML reused live evidence across historical validation and live confirmation

The Scanner ML already had a live-confirmation concept, but the first walk-forward validation used the combined replay + live dataset. That meant post-replay live rows could contribute to the initial validation calculation and then be reused as the supposedly independent confirmation set.

That is evidence double-dipping.

**Remediation in PR #53:**

- when historical replay is present, the historical walk-forward gate uses replay rows only;
- strictly later live rows are isolated as the confirmation pool;
- final/advisory model fitting may use all information available after validation is measured, but production validation cannot reuse the same observations in both gates.

### P0-3 — Analyzer same-ticker ML split 5-minute rows across a future-label boundary

The Analyzer's same-ticker ML creates 30-minute, 60-minute, and same-session target/stop labels. Its previous walk-forward validation split the dataset by row number.

A training observation near a row split can derive its label from market action occurring 30–60 minutes later, or through the rest of the session. Those future bars can overlap timestamps assigned to the validation side of the split.

That is intra-session validation-boundary leakage.

**Remediation in PR #53:**

- every ML observation now carries its trading date;
- walk-forward folds split on whole trading days;
- no trading day appears in both train and validation;
- same-session labels can therefore never cross into a validation day.

### P0-4 — Repeated live scans inflated effective confirmation sample size

A raw count of live observations can overstate independent evidence when the same ticker is rescanned repeatedly. For a 60-minute target, two same-ticker observations 15 or 30 minutes apart share much of the same future price path.

**Remediation in PR #53:**

- live confirmation keeps all raw rows for diagnostics but evaluates a de-correlated confirmation set;
- same-ticker confirmation observations must be at least 60 minutes apart;
- full replay-backed promotion requires at least 100 de-correlated live observations across at least 5 trading days and at least 15 distinct symbols;
- positive/negative class-count gates still apply.


### P1-1 — Analyzer outcome tolerance was looser than Scanner outcome tolerance

Scanner outcomes allowed a maximum 3-minute delay from the requested horizon. Analyzer outcomes allowed 5 minutes.

That could make the same nominal +15m/+30m/+60m horizon mean different things for an illiquid or halted stock.

**Remediation in PR #53:**

- Analyzer horizon matching is standardized to the Scanner's 180-second maximum;
- a regression test verifies +3 minutes is accepted and +4 minutes is rejected.

### P1-2 — Outcome reports used "win rate" for gross forward price movement

The Scanner's outcome tracker measures price movement from the scanner snapshot price to a later bar close. That is useful signal-continuation evidence, but it is not realized trading P/L because spread, slippage, fees, and entry latency are not deducted.

**Remediation in PR #53:**

- human-readable reports now use **positive-return rate** instead of **win rate** for this metric;
- generated JSON includes explicit execution-adjustment metadata;
- the report warns that profitability claims require an execution-aware simulation.

### P1-3 — Validation workflow ran after pushes to main, not before merge

The repository had a substantial validation workflow, but it was triggered on pushes to `main`. The repository also has no required branch status checks.

That means a regression could reach `main` before CI objects.

**Remediation in PR #53:**

- the Analyzer / Scanner validation workflow now runs on pull requests targeting `main`;
- the audit itself is being kept on a PR branch until that validation passes.

Repository branch protection / required-status enforcement remains an administrative hardening opportunity.

## Evidence that must remain explicitly unproven

### Analyzer live calibration

The current durable calibration artifact has no resolved current-version 60-minute calibration rows. The Analyzer should therefore not claim that its setup score, entry readiness, or evidence strength has been empirically calibrated as a probability of success.

### Swing / Longer-Term forward cohorts

The new forward cohorts have not yet accumulated resolved multi-day outcomes. Their scores are classification/discovery aids until forward evidence matures.

### Swing ML

The current six-year shadow Swing ML validation does **not** pass its own production gate. It remains `experimental_not_validated` / `production_enabled=false`.

That is the correct behavior.

## Known limitations that are disclosed rather than hidden

### Current-universe survivorship in historical replay

The historical scanner/timeframe replay cannot fully reconstruct stocks that later delisted or disappeared from the present universe. The live holdout gate mitigates this before production promotion; it does not erase the bias from the historical study.

A future stronger dataset would use point-in-time constituent/security-master data including delisted names.

### Historical feature parity

Historical replay cannot perfectly reconstruct:

- historical bid/ask spread and quote freshness;
- every historical catalyst/news state;
- exact live 1-minute behavior when only 5-minute bars are available.

These missing features must stay explicit in replay metadata and are another reason replay alone cannot prove production performance.

### Off-hours coverage

The off-hours scanner obtains a broad quote universe but fetches deep daily history for a smaller balanced history pool before ranking. This is a discovery coverage compromise, not a claim that every listed stock received full multi-year analysis.

## Required evidence before stronger production claims

A model should move from advisory/research to production influence only when all applicable gates are met:

- causal feature construction;
- whole-day or otherwise properly embargoed walk-forward validation;
- probability skill versus a naive baseline, not accuracy alone;
- no reuse of validation observations in a later confirmation claim;
- sufficiently sized strictly later live holdout;
- stable behavior across multiple days/regimes rather than one favorable cohort;
- live execution/data-quality conditions reasonably matching the model's intended inputs.

## Independent-review / Codex brief

A second reviewer should try to disprove this audit rather than redesign the app.

Specifically inspect:

1. every feature for information unavailable at the signal timestamp;
2. every label for future-window overlap with training/validation boundaries;
3. every historical universe for survivorship/selection bias;
4. every ML validation gate for reused tuning/holdout evidence;
5. all deduplication for inflated effective sample size;
6. all score/calibration language for probability-like claims unsupported by calibration;
7. all stale-data/provider fallbacks for silent degradation;
8. all execution assumptions for unrealistic fills/spreads/halts;
9. all version/schema gates for stale artifact contamination;
10. all places where advisory/research evidence can accidentally change production ranking, action, entry, or confidence.

Do not optimize thresholds or add features until the integrity findings are resolved and regression validation passes.

## Current gate status

PR #53 and follow-up audit PRs #68-#71 and #73-#74 were merged only after the required validation suites passed. The code-level integrity findings documented above are therefore remediated on `main`.

This does **not** convert the remaining evidence gaps into validated performance claims. Analyzer live calibration, forward Swing/Longer-Term cohorts, survivorship limitations, and historical feature-parity limitations remain explicit blockers on stronger claims until their required evidence exists.
