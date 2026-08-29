# Momentum Scanner / Analyzer App Boundary

This repository is the standalone **Momentum Scanner + Stock Analyzer** app.

## Product role

The app is a decision-support tool:
- Scanner finds and ranks live momentum opportunities.
- Analyzer explains the setup, risk, levels, historical behavior, and ML evidence.
- Machine learning may influence ranking and analysis only when validation gates allow it.
- The user makes the final trade decision.

## Hard boundary from Trading Intelligence Lab

The Momentum Scanner / Analyzer and Trading Intelligence Lab are separate products and must remain independently runnable.

Allowed:
- Copy or adapt a proven algorithm, feature definition, test idea, or research result from the Trading Intelligence Lab into this repository.
- Re-implement shared concepts locally when they materially improve scanner/analyzer decision support.
- Compare outputs between the apps during research.

Not allowed:
- Runtime imports from Trading Intelligence Lab modules.
- Importing the Trading Intelligence Lab package/repository as a dependency.
- Requiring Trading Intelligence Lab state, artifacts, UI, workflows, or deployment for this app to run.
- Adding Trading Intelligence Lab research workspaces or controls to the normal Scanner / Analyzer user flow.

If code is reused, it should become owned code in this repository with its own tests and versioning.
