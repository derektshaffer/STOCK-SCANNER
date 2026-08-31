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
- [ ] Collect premarket and after-hours Scanner observations into the durable outcome dataset in **shadow-only** fields.
- [ ] Record research outcomes through the full supported 4:00 AM–8:00 PM ET session without changing the current production target.
- [ ] Verify extended-hours market-data source and timestamp integrity with synthetic/provider smoke tests.

**Definition of done:** extended-hours examples are saved and measurable, while the current production model remains behaviorally unchanged.

### Phase 2 — Replace the one-number outcome with an outcome vector
- [ ] Preserve +15m / +30m / +60m and add +120m where data coverage permits.
- [ ] Add full-horizon MFE and MAE.
- [ ] Add +3% / +5% / +10% / +20% threshold-hit labels and time-to-hit.
- [ ] Add time-to-peak and peak return.
- [ ] Add target-before-stop / threshold-before-failure path labels.
- [ ] Add session-transition outcomes (regular → after-hours, premarket → regular).
- [ ] Add next-session continuation where appropriate.

**Definition of done:** a stock that rises +20% and later gives some back cannot be reduced to a misleading single 60-minute endpoint label.

### Phase 3 — Durable high-frequency learning observations
- [ ] Preserve important observations from the app's 2-minute scan stream instead of relying only on the slower durable GitHub collector.
- [ ] Deduplicate highly correlated same-symbol observations without deleting meaningful state transitions.
- [ ] Persist rank/action/score changes and major pattern transitions.
- [ ] Add rate/race protection so multiple browser sessions cannot corrupt the journal.

**Definition of done:** a high-quality discovery seen in the live app cannot disappear from the learning history merely because it occurred between durable collector runs.

### Phase 4 — Opportunity audit / “what did we miss?”
- [ ] Automatically identify high-ranked winners, high-ranked failures, and low-ranked explosive winners.
- [ ] Detect label contradictions such as large MFE but failed endpoint label.
- [ ] Compare score buckets and action states against later outcome distributions.
- [ ] Compare premarket, regular, and after-hours behavior separately.
- [ ] Detect data/features that are collected but never used by any research model.
- [ ] Detect filters that systematically remove later winners.
- [ ] Track false-positive and false-negative archetypes.

**Definition of done:** the system can surface “we found this, but our learning target failed to credit it” and “we missed this class of winners” without a human first noticing it on a chart.

### Phase 5 — Hypothesis engine
- [ ] Convert repeated empirical gaps into explicit candidate hypotheses.
- [ ] Require a minimum sample count / cross-symbol support before a hypothesis is testable.
- [ ] Record the exact evidence that generated each hypothesis.
- [ ] Keep AI-generated hypotheses separate from production rules.
- [ ] Reject hypotheses that merely restate hindsight outcomes.

**Example:** “Late-session high volume acceleration + VWAP retention + higher-plateau structure may predict after-hours continuation.”

**Definition of done:** the system proposes testable improvements rather than silently changing its own rules.

### Phase 6 — Automatic historical challenge
- [ ] Test every candidate hypothesis on data not used to invent it.
- [ ] Use whole-day / embargoed walk-forward splits where future windows overlap.
- [ ] Compare against the current hand score and naive baselines.
- [ ] Evaluate calibration, discrimination, MFE/MAE, and execution-aware utility.
- [ ] Reject unstable rules that only work in one ticker, day, or market regime.

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

1. Current Scanner ML primary target is **>= +3% at 60 minutes**.
2. Current durable Scanner outcome scoring is centered on the regular session.
3. The visible app scans more frequently than the durable learning collector.
4. MFE/MAE are already measured in some paths but are not the primary Scanner ML objective.
5. The prior integrity audit did not systematically challenge whether the target/objective itself matched the desired behavior.

## Rule for updating this file

When work is completed:
- change only the specific checkbox that is actually complete;
- add/extend regression coverage where relevant;
- do not mark a research feature as production-ready unless its validation gate has passed;
- keep unresolved shortcomings visible rather than deleting them from the plan.
