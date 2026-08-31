# Learning-System Improvement Plan

Status: **ACTIVE**
Started: 2026-08-31

## Why this exists

The integrity audit is necessary, but it is not sufficient. It mainly asks whether the app is implementing its current rules correctly. This plan adds two missing questions:

1. **Are we optimizing the right objective?**
2. **What valuable outcomes are we failing to learn from?**

A change is not marked complete here until the code exists, a regression/smoke check exists when applicable, and its production-vs-shadow status is explicit.

## Permanent safety rules

- **Shadow first.** New objectives, labels, sessions, features, and hypotheses are research-only until they pass chronological validation and later live confirmation.
- **No hindsight leakage.** An observation may use only information available at the observation timestamp; future data may only be used to create labels/outcomes.
- **No silent production promotion.** A model or rule cannot affect Scanner ranking, Analyzer entry/exit geometry, or actionable language merely because an audit found a promising pattern.
- **Preserve raw outcomes.** Do not reduce a rich future path to one endpoint before the research layer has had a chance to evaluate it.
- **Separate market sessions.** Premarket, regular session, and after-hours observations must be identifiable and independently measurable.
- **Failures are training data too.** High-score failures, false breakouts, deep adverse excursions, and missed winners are all retained.
- **Every promotion has a receipt.** Production influence requires a machine-readable validation result, model/rule version, target definition, data cohort, and reason for promotion.

## Work plan

### Phase 0 — Accountability and audit coverage
- [x] Create this durable plan/checklist in the repository.
- [x] Add a machine-readable Learning Objective / Opportunity audit.
- [x] Add synthetic regression checks proving the audit can detect a USDE-like missed-opportunity label.
- [x] Add CI/nightly execution for the new audit.
- [ ] Add a compact app-facing status view showing open learning gaps and last audit time.

**Definition of done:** the repository itself tells us what remains, and CI can detect regressions in the audit logic.

### Phase 1 — Protect production while broadening research collection
- [x] Explicitly gate current Scanner ML training to regular-session observations.
- [x] Add session identity to new Scanner outcome rows.
- [x] Collect premarket and after-hours Scanner observations into the durable outcome dataset in **shadow-only** fields.
- [x] Record research outcomes through the full supported 4:00 AM–8:00 PM ET session without changing the current production target.
- [x] Verify extended-hours market-data source and timestamp integrity with synthetic/provider smoke tests.

**Definition of done:** extended-hours examples are saved and measurable, while the current production model remains behaviorally unchanged.

### Phase 2 — Replace the one-number outcome with an outcome vector
- [x] Preserve +15m / +30m / +60m and add +120m where data coverage permits.
- [x] Add full-horizon MFE and MAE.
- [x] Add +3% / +5% / +10% / +20% threshold-hit labels and time-to-hit.
- [x] Add time-to-peak and peak return.
- [x] Add target-before-stop / threshold-before-failure path labels.
- [ ] Add session-transition outcomes (regular → after-hours, premarket → regular).
- [ ] Add next-session continuation where appropriate.

**Definition of done:** a stock that rises +20% and later gives some back cannot be reduced to a misleading single 60-minute endpoint label.

### Phase 3 — Durable high-frequency learning observations
- [x] Preserve important observations from the app's 2-minute scan stream instead of relying only on the slower durable GitHub collector.
- [x] Deduplicate highly correlated same-symbol observations without deleting meaningful state transitions.
- [~] Persist rank/action/score changes and major pattern transitions. Current journal preserves first/last/best observation time, best/worst rank, strongest state, and actions seen; full transition history is still open.
- [x] Add rate/race protection so multiple browser sessions cannot corrupt the journal. The Scanner's shared process lock, bounded journal, isolated learning branch, conflict retries, and periodic merge/sync are in place.

**Definition of done:** a high-quality discovery seen in the live app cannot disappear from the learning history merely because it occurred between durable collector runs.

### Phase 4 — Opportunity audit / “what did we miss?”
- [x] Automatically identify high-ranked winners, high-ranked failures, and low-ranked explosive winners.
- [x] Detect label contradictions such as large MFE but failed endpoint label.
- [~] Compare score buckets and action states against later outcome distributions. Score/rank buckets are implemented; action-state grouping is still open.
- [x] Compare premarket, regular, and after-hours behavior separately.
- [ ] Detect data/features that are collected but never used by any research model.
- [~] Detect filters that systematically remove later winners. Failed-filter counts are now retained for low-rank/control explosive winners; broader denominator-aware filter analysis is still open.
- [~] Track false-positive and false-negative archetypes. High-rank failures and low-rank/control explosive winners are explicit archetypes; broader clustering/classification is still open.

**Definition of done:** the system can surface “we found this, but our learning target failed to credit it” and “we missed this class of winners” without a human first noticing it on a chart.

### Phase 5 — Hypothesis engine
- [x] Convert repeated empirical gaps into explicit candidate hypotheses.
- [x] Require a minimum sample count / cross-symbol support before a hypothesis is testable.
- [x] Record the exact evidence that generated each hypothesis.
- [x] Keep AI-generated hypotheses separate from production rules; generated candidates carry `production_influence=false`.
- [~] Reject hypotheses that merely restate hindsight outcomes. Minimum-sample/cross-symbol gates are in place, but the decisive out-of-sample rejection step belongs to Phase 6 and is still open.

**Example:** “Late-session high volume acceleration + VWAP retention + higher-plateau structure may predict after-hours continuation.”

**Definition of done:** the system proposes testable improvements rather than silently changing its own rules.

### Phase 6 — Automatic historical challenge
- [~] Test every candidate hypothesis on data not used to invent it. The automatic challenger now routes all emitted hypotheses to a challenge spec or an explicit blocked status; session-specific calibration is correctly blocked until extended-hours historical replay exists.
- [x] Use whole-day / embargoed walk-forward splits where future windows overlap. The newest 40% of replay trading days are frozen confirmation data, and same-symbol observations are de-correlated by the 60-minute target horizon.
- [x] Compare against the current hand score and naive baselines. Path-target challenges report candidate-model AUC vs Scanner hand-score AUC, plus model Brier vs the discovery base-rate baseline and top-decile lift comparisons.
- [~] Evaluate calibration, discrimination, MFE/MAE, and execution-aware utility. AUC, Brier, calibration ECE, full-path MFE/MAE labels, +5-before--3 barrier utility, and 0/.25/.50/1.00% friction sensitivity are implemented; true historical spread/fill reconstruction is still unavailable, so utility remains an explicitly labeled proxy.
- [~] Reject unstable rules that only work in one ticker, day, or market regime. The path-target gate rejects weak day stability, symbol concentration, or single-regime support; score-order and missed-explosive hypotheses require repetition in frozen confirmation data. Broader regime stability for every hypothesis family remains open.

**Definition of done:** the system tries to disprove its own ideas before asking for production promotion.

### Phase 7 — Live shadow confirmation
- [ ] Run passing hypotheses in shadow mode on new live data.
- [ ] Require strictly later live confirmation across multiple symbols/days/regimes.
- [ ] Keep raw and de-correlated sample counts separate.
- [ ] Track degradation versus historical expectation.

**Definition of done:** historical success alone cannot enable production influence.

### Phase 8 — Controlled promotion / rollback
- [ ] Add a promotion gate with versioned evidence.
- [ ] Allow only validated components to affect ranking/action.
- [ ] Add automatic rollback to the prior model/rule if live evidence degrades beyond a defined boundary.
- [ ] Preserve an audit trail of every promotion and rollback.

**Definition of done:** self-improvement is possible, but uncontrolled self-modification is not.

## Current known gaps this plan must close

1. Current production Scanner ML primary target is **>= +3% at 60 minutes**; richer path targets are still shadow-only.
2. Current production Scanner ML remains regular-session gated by design; the new shadow outcome path covers premarket, regular session, and after-hours separately.
3. The visible app scans more frequently than the slower durable Actions collector; the new high-frequency journal now captures bounded 15-minute symbol states from the live stream, but runtime sync health still needs accumulated evidence.
4. MFE/MAE and +3/+5/+10/+20 path labels are now collected in shadow research, but they are not yet validated production objectives.
5. The prior integrity audit did not systematically challenge whether the target/objective itself matched the desired behavior; the new Learning Objective / Opportunity Audit now does.

## Progress log

### 2026-08-31 — Learning-loop foundation
- Added `learning_system_audit.py`: source/specification checks plus empirical endpoint-vs-path contradiction detection.
- Added `learning_system_regression_check.py`, including a synthetic USDE-like case where MFE is very large even though the +60m endpoint target would label the observation negative.
- Added nightly/push Learning Objective Opportunity Audit workflow and included the learning regressions in the main integrity CI gate.
- Added `score_opportunity_outcomes.py`, a **shadow-only** 4:00 AM–8:00 PM ET outcome collector covering premarket, regular session, and after-hours.
- Shadow outcomes now preserve +15m/+30m/+60m/+120m returns, full-horizon MFE/MAE, +3/+5/+10/+20 threshold hits and time-to-hit, time-to-peak/trough, and threshold-before-3%-failure ordering.
- Current production Scanner ML was explicitly gated to regular-session observations so the broader shadow dataset cannot silently change live ranking.
- New production outcome rows now record `session_phase`.
- The nightly outcome workflow now writes durable shadow opportunity reports; the learning audit reads them separately from production outcomes.

### 2026-08-31 — High-frequency capture + guarded hypothesis layer
- Added `scanner_live_journal.py`: every enabled live app scan is inspected, then a bounded 15-minute-per-symbol journal keeps the strongest/actionable state plus deterministic below-cutoff controls.
- The journal records first/last/best observation time, best/worst rank, actions seen, score/opportunity score, key momentum/volume/VWAP/structure features, source/version metadata, and compact filter/flag context.
- Live journal data syncs to the isolated `learning-journal` branch rather than writing noisy high-frequency commits into production `main`.
- `score_opportunity_outcomes.py` now imports the high-frequency journal into nightly shadow outcome resolution.
- The opportunity audit now compares market sessions, score buckets, rank buckets, high-rank winners/failures, low-rank explosive misses, and failed-filter counts.
- The hypothesis layer now requires minimum sample counts and cross-symbol evidence before emitting path-target, session-calibration, missed-explosive, or score-monotonicity candidates.
- No hypothesis from this layer can affect production ranking or Analyzer trade decisions.

### 2026-08-31 — Phase 6 historical challenge
- Added `historical_hypothesis_challenge.py`: every learning hypothesis is challenged against causal historical replay or explicitly blocked when the required historical coverage does not exist.
- The challenger freezes the newest 40% of whole trading days for confirmation and de-correlates same-symbol observations by 60 minutes.
- Path-target candidates are fit only on earlier discovery days and evaluated on frozen later days against both the current Scanner hand score and a naive base-rate baseline.
- Validation receipts include AUC, Brier score, calibration ECE, top-decile lift, day-by-day lift, symbol concentration, market-regime coverage, and a conservative +5%/-3% path utility proxy under multiple friction assumptions.
- Added out-of-sample challenge specs for score monotonicity and missed-explosive/filter hypotheses. Session-specific calibration fails closed until multi-session historical replay exists.
- Historical Scanner replay v4.9 now records full 60-minute MFE/MAE, +3/+5/+10/+20 threshold timing/order, a -3% failure path, and causal SPY/QQQ/IWM regime context specifically for hypothesis challenges.
- Added `historical_hypothesis_regression_check.py`. The main integrity CI completed successfully with `PHASE6_HISTORICAL_CHALLENGE_REGRESSIONS=passed`.
- A fresh deep historical backfill/challenge run has been triggered. Its empirical Phase 6 verdicts are not marked complete here until that run finishes and its durable challenge receipt is inspected.

## Rule for updating this file

When work is completed:
- change only the specific checkbox that is actually complete;
- add/extend regression coverage where relevant;
- do not mark a research feature as production-ready unless its validation gate has passed;
- keep unresolved shortcomings visible rather than deleting them from the plan.
