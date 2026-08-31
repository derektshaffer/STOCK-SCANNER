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
- **Historical-policy contradiction:** `historical_integration.py` currently says same-ticker historical matches affect the actual setup score, rebuild the trade plan, shift pullback geometry, adjust plan confidence, and can demote breakout entries. Later Analyzer v2 code explicitly describes historical analogs as research-only. These two policies conflicted. **Same-ticker historical production influence has now been removed; continuing the sweep for any remaining historical leakage.**
- **Plan continuity was not canonical:** The current trade plan was recomputed from the latest snapshot. **Intraday continuity is now implemented with a persistent session thesis, anchored geometry, explicit invalidation/completion/expiry events, three-observation replacement confirmation, and transition/history logging. Swing/longer-term continuity remains open.**
- **Layered plan mutation:** `stock_analyzer` builds the plan and enrichment layers can refine it. **A final decision contract now normalizes status/entry language and geometry after all layers; historical analog mutation has been removed. Continued audit is checking for remaining pre-contract semantic drift.**

### HIGH / MEDIUM

- **Bounce semantics previously swallowed visible rebounds inside a larger impulse.** Active observed bounces are now separated from confirmed bounce peaks. **Core regression now passes.**
- **Historical analog matching compares a live partial-day setup with completed historical-day statistics.** This is acceptable as descriptive research, but unsafe as an unvalidated live-action input. **Production influence must be gated.**
- **Full-spectrum scenario math still includes some historical stair-step study context even though the live score path calls history neutral/research-only.** It does not currently own the final entry gate, but labeling/semantics need cleanup.
- **Scanner production ACTION intentionally ignores advanced behavior features unless validation improves.** This is conservative and currently desirable, but the audit must verify that UI/ranking does not imply behavior validation that does not exist.
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
- [~] Remove or gate every unvalidated historical input from production action/geometry. Same-ticker and stair-step historical live influence removed; continuing full sweep.
- [ ] Verify point-in-time SEC/fundamental replay.
- [ ] Verify scanner ML split/embargo/same-symbol correlation controls.
- [ ] Verify same-ticker and peer ML cannot affect live action without validation gates.
- [ ] Verify model-version changes invalidate incompatible historical features.

### Phase 5 — Scanner / Analyzer consistency
- [ ] Scanner action cannot outrank data-integrity state.
- [ ] Scanner timeframe labels cannot silently change production ranking.
- [ ] Analyzer final action is the only source of “entry available.”
- [ ] Alerts use the same final decision contract as the displayed app.

### Phase 6 — Data/provider integrity
- [ ] Freshness/fallback matrix: Tradier, SIP, IEX, extended hours, stale quote, missing momentum.
- [ ] No mixed-provider timestamp or volume denominator errors.
- [~] Streaming session failures cannot create fake freshness. Tradier code-1007 / too-many-sessions now enters a 120-second session-limit cooldown instead of a reconnect loop; freshness/fallback matrix still in progress.

### Phase 7 — Outcome learning
- [ ] Ensure logged prediction contains the exact displayed plan and final gate state.
- [ ] Ensure outcome horizons start after the observation timestamp.
- [ ] Distinguish active/developing patterns from confirmed patterns in labels.
- [ ] Verify repeated intraday samples are de-correlated for validation.

### Phase 8 — Performance / state safety
- [~] Cached results retain their original market-data age and final live-data gate blocks stale entries; explicit cache-age UI audit still in progress.
- [ ] Background work cannot block Scanner cadence.
- [ ] No duplicate deep-history, SEC, float, or stream work on normal Analyzer launch.
- [ ] Timeout/cancel paths leave no stale locks or stale plan state.

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
