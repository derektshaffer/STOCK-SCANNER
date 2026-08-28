# Trading Research Queue

This folder is intentionally not a production strategy file.

The background research agent searches current and foundational market research
and turns findings into measurable RESEARCH_CANDIDATE hypotheses. Research
material can suggest what the analyzer should test, but it cannot directly
change live recommendations, model weights, entry rules, or promotion status.

## Promotion path

1. Research candidate — a source-backed, falsifiable idea is stored here.
2. Feature specification — define exact past-only inputs available at a
   historical observation. Reject any feature that leaks future information.
3. Historical experiment — replay the feature across broad data and relevant
   cohorts, including different tickers, volatility regimes, price bands,
   liquidity levels, catalysts, and times of day.
4. Chronological validation — use expanding-window / walk-forward tests and
   compare against the existing model and a simple baseline.
5. Robustness checks — require adequate sample size and avoid promotion when
   gains depend on one ticker, one month, one threshold, or one market regime.
6. Challenger only — a passing idea first enters with zero production weight.
7. Promotion — only evidence that remains better out of sample can earn live
   influence. Failed experiments remain recorded so the system does not keep
   rediscovering and retesting the same weak idea.

latest_research.json contains the newest run. The archive folder preserves prior
research snapshots so hypotheses and source history remain auditable.

The weekly GitHub research workflow is optional: if OPENAI_API_KEY is not
configured as a GitHub Actions secret, it exits successfully without producing
or changing research files. The analyzer itself continues to work normally.
