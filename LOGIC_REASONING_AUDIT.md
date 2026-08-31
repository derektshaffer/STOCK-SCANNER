# STOCK-SCANNER Logic & Reasoning Integrity Audit

Status: **IN PROGRESS — integrity-first audit**
Repository: STOCK-SCANNER
Scope: Scanner, Analyzer, market-structure detectors, trade-plan selection, state continuity, multi-timeframe reasoning, historical research, ML, data freshness, outcome tracking, UI semantics, and performance paths that can alter reasoning.

## Audit contract

The app must satisfy these invariants before the audit is considered green:

1. **One canonical final decision.** No UI element may say ENTRY AVAILABLE when the final safety gate says WAIT / DATA CHECK / NO TRADE.
2. **No moving goalposts without an explicit event.** Entry, stop, target, and plan family may evolve only because the prior thesis is invalidated, completed, expired, or a clearly stronger replacement is confirmed.
3. **State continuity.** Intraday reasoning must remember the current session thesis; swing/longer-term reasoning must evolve over their own horizons instead of resetting from the newest candle.
4. **Causal structure.** Confirmed pivots, bounces, breakouts, stair steps, and ML features may use only information available at that observation time.
5. **Observed vs confirmed are separate.** Visible developing events (e.g. an active bounce) must be recorded, while formal confirmation remains causal and delayed.
6. **Research-only means research-only.** Unvalidated historical analogs/replay statistics may not silently change live entries, scores, stops, targets, or action labels.
7. **Validated ML only.** Only models that pass chronological holdout gates may affect production decisions.
8. **Fresh trusted market data required for an entry call.** Stale, missing, non-consolidated, or contradictory data must block actionable entry language.
9. **Geometry sanity.** For a long plan: stop < entry zone < targets; reward/risk must match the displayed geometry; invalid geometry is NO TRADE / DATA CHECK.
10. **Timeframe separation.** Intraday evidence cannot rewrite a longer-term thesis unless it is relevant to that horizon; timeframe scores and evidence must be labeled and validated independently.
11. **Scanner/Analyzer contract.** Scanner cues are discovery/review cues; Analyzer owns the final entry/stop/target decision. Scanner must never imply a stronger action than Analyzer integrity permits.
12. **Performance cannot change semantics.** Caching/background execution may speed the app but may not serve stale analysis as a fresh entry signal.

## Current findings

### CRITICAL / HIGH

- **Final-entry contradiction:** Analyzer v2 could demote a raw ENTRY AVAILABLE plan to WAIT because of stale/missing data or weak evidence while leaving the older `entry_state=ENTRY AVAILABLE` and “ENTRY AVAILABLE NOW” instruction intact. This produced contradictory UI. **Fixed and covered by final decision-contract regression.**
- **Historical-policy contradiction:** same-ticker historical matches previously changed the live setup score/geometry even though other layers labeled them research-only. **Resolved: same-ticker analogs and stair-step historical outcomes are neutral in live production scoring/geometry, timeframe research remains shadow-only, and regression tests verify bullish vs bearish historical analogs cannot change the live plan.**
- **Plan continuity was not canonical:** The current trade plan was recomputed from the latest snapshot. **Intraday continuity is now implemented with a persistent session thesis, anchored geometry, explicit invalidation/completion/expiry events, three-observation replacement confirmation, and transition/history logging. Swing/longer-term continuity remains open.**
- **Layered plan mutation:** `stock_analyzer` builds the plan and enrichment layers can refine it. **A final decision contract now normalizes status/entry language and geometry after all layers; historical analog mutation has been removed. Continued audit is checking for remaining pre-contract semantic drift.**

### HIGH / MEDIUM

- **Bounce semantics previously swallowed visible rebounds inside a larger impulse.** Active observed bounces are now separated from confirmed bounce peaks. **Core regression now passes.**
- **Historical analog matching compares a live partial-day setup with completed historical-day statistics.** It is now explicitly descriptive/research-only and cannot clear an entry gate or move live geometry.
- **Full-spectrum historical leakage was found and removed.** Completed stair-step/history studies remain visible as research context but no longer tilt live scenario weights.
- **Scanner production ACTION intentionally ignores advanced behavior features unless validation improves.** Regression coverage now verifies timeframe/behavior labels do not rerank production and Scanner alerts remain review-only.
- **Timeframe fit is currently classification, not a persistent horizon-specific strategy state.** This does not yet meet the desired “combine information throughout the day/week/month” behavior.

## Audit phases

### Phase 1 — Decision contract and contradictions
- [x] Identify all modules that can mutate plan/action.
- [x] Fix final safety-gate ENTRY AVAILABLE contradiction.
- [x] Add final trade-plan invariant normalizer.
- [x] Add geometry sanity checks.
- [x] Add cross-field consistency tests.

### Phase 2 — Thesis continuity
- [x] Intraday persistent thesis: active plan, trigger, anchored entry/stop/targets, confidence/readiness history, transition log, explicit change reason.
- [ ] Swing persistent thesis over trading days.
- [ ] Longer-term thesis over weeks/months.
- [x] Require invalidation/completion/expiry or three consecutive replacement proposals before an intraday family switch.

### Phase 3 — Pattern logic
- [x] Causal confirmed pivots.
- [x] Active-vs-confirmed bounce distinction.
- [~] Synthetic chart suite for bounce #1/#2/#3, failed breakout, reclaim, stair-step, reversal, chop. USDE-like graph truth case added; broader matrix still in progress.
- [ ] Append-future invariance tests for every production detector.

### Phase 4 — Historical / ML integrity
- [x] Remove/gate unvalidated historical inputs from production action/geometry. Same-ticker analogs and historical stair outcomes are research-only; timeframe ML/research remains production-disabled.
- [x] Verify point-in-time SEC/fundamental replay. Company facts and dilution forms are filing-date gated; future filings are excluded by regression.
- [x] Verify scanner ML split/embargo/same-symbol correlation controls. Whole-day chronological folds, strictly later live confirmation, and 60-minute same-symbol effective-sample spacing are enforced for replay validation and served-model fitting.
- [x] Verify same-ticker and peer ML cannot affect live action without validation gates. Composite ML potential/evidence/scenario influence now requires the complete production gate; peer/replay validation uses de-correlated effective samples.
- [x] Verify model-version changes invalidate incompatible historical features. Scanner feature-version, Analyzer calibration schema, peer behavior-feature version, and bounce-semantics version gates are regression-tested.

### Phase 5 — Scanner / Analyzer consistency
- [x] Scanner action cannot outrank data-integrity state.
- [x] Scanner timeframe labels cannot silently change production ranking.
- [x] Analyzer final decision contract is the only source of actionable entry language; Scanner/alerts are review cues only.
- [x] Scanner alerts are explicitly review-only, require trusted Scanner data, and cannot emit ENTRY AVAILABLE / BUY NOW language.

### Phase 6 — Data/provider integrity
- [~] Freshness/fallback matrix: Tradier and Alpaca SIP accepted when fresh; IEX, stale quote/trade, provider/feed disagreement, missing momentum, and missing intraday source fail closed. Extended-hours edge cases remain under review.
- [x] No mixed-provider technical-source acceptance: Scanner rejects quote/technical provider disagreement, and Analyzer rejects provider/feed metadata mismatch. Existing volume-pace tests keep regular vs extended-session denominators separate.
- [~] Streaming session failures cannot create fake freshness. Tradier code-1007 / too-many-sessions now enters a 120-second session-limit cooldown instead of a reconnect loop; freshness/fallback matrix still in progress.

### Phase 7 — Outcome learning
- [x] Logged prediction contains final status/action/entry instruction, displayed geometry, thesis revision, and final decision-contract corrections.
- [x] Outcome horizons and path labels start strictly after the observation timestamp in both opportunistic and durable Analyzer scorers; the signal candle is excluded.
- [x] Active/developing patterns are logged separately from confirmed patterns (including active bounce vs confirmed bounce).
- [x] Repeated intraday samples are de-correlated for validation: same-ticker ML, Scanner replay/live confirmation, and peer ML use 60-minute same-symbol effective-sample spacing.

### Phase 8 — Performance / state safety
- [~] Cached results retain their original market-data age and final live-data gate blocks stale entries; explicit cache-age UI audit still in progress.
- [x] Scanner uses one async background process/lock across views; runtime regressions cover nonblocking start, timeout release, stale-lock recovery, and cadence health.
- [ ] No duplicate deep-history, SEC, float, or stream work on normal Analyzer launch.
- [x] Timeout/cancel paths cannot advance execution-thesis or setup-horizon state: continuity updates are staged transactionally and committed only after a complete Analyzer result.

## Exit criteria

Audit is green only when:
- all critical/high findings are fixed,
- the reasoning-contract tests are automatically run in GitHub Actions,
- no production action depends on an unvalidated research-only feature,
- synthetic scenario tests and temporal-causality tests pass,
- Scanner/Analyzer/action/entry fields are internally consistent,
- plan changes expose an explicit machine-readable reason.


## Graph-level truth cases added

The audit now includes explicit tests built from the kind of visual mistake that exposed the USDE issue:

- a large stair-step run with multiple obvious pullback/rebound cycles may not collapse into “Bounce #1 not reached”;
- once a breakout trigger is reached, the Analyzer must keep evaluating the same structural breakout reference rather than instantly switching to a deeper pullback goal;
- a final safety WAIT/DATA CHECK may never coexist with “ENTRY AVAILABLE NOW” language;
- impossible long geometry (stop above entry or Target 1 below entry) is rejected automatically.

These tests passed in the Analyzer validation workflow on the commit that introduced them.


## Intraday thesis continuity now implemented

- One accepted intraday thesis is held per ticker, trading session, and browser-session namespace.
- Entry zone, stop, targets, and structural breakout reference are anchored rather than silently recalculated underneath the user.
- A different plan family is held as a proposal until it persists across three analyses.
- Immediate replanning is allowed for explicit invalidation, Target 1 completion, failed breakout, expiry, or final-contract geometry failure.
- Stop/target touches are replayed from bars between Analyzer refreshes so a transient event is not forgotten just because the current quote moved back.
- Same-bar stop/target ambiguity is scored stop-first conservatively.
- Each final displayed decision records price, plan family, revision, action, entry state, confidence, entry readiness, evidence strength, and potential score.
- Thesis state is namespaced per browser session so one user's/ticker-session state cannot bleed into another session.


## Additional integrity findings fixed during the audit

- **Observation-candle outcome leakage:** both Analyzer outcome paths previously allowed the candle stamped at the prediction time to contribute high/low data to later first-touch and MFE/MAE labels. That could credit movement that happened before the signal. Both paths now start strictly after the observation candle.
- **Correlated replay evidence:** Scanner and peer-ML replay validation previously counted dense same-symbol observations inside the same 60-minute target window as separate evidence. Replay validation and served-model fitting now use 60-minute same-symbol effective-sample spacing.
- **Composite ML gate inconsistency:** a numeric ML edge / validated submodel count could previously boost live potential/evidence before the complete production gate passed. Composite ML influence is now neutral until the production gate and consolidated-source checks pass.
- **Mixed-provider technicals:** Scanner integrity previously checked the quote source but not the source of VWAP/momentum bars. Scanner now requires both quote and intraday technical sources to be consolidated and mutually consistent.
- **Cancelled-analysis state mutation:** execution and setup-horizon continuity could previously update before a long Analyzer run finished. Both continuity layers now stage changes and commit only after successful final decision construction.
