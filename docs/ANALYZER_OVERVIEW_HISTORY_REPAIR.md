# Analyzer overview and history repair

The Analyzer now groups its primary metrics, price chart, ML outlook, timeframe fit, structure, and cautions in a compact overview. Supporting material remains available through one detail selector. Saved stocks, help, and position inputs use compact toolbar controls. The Scanner and all existing price-freshness, symbol, validation, and research-only gates remain in place.

## History findings

The historical bar adapter discarded Alpaca's continuation token. A page can be shorter than the requested limit and still require pagination. Authenticated read-only diagnostics on September 12, 2026 reproduced this with both SVRN and AAPL. The shared history loader also swallowed chunk failures and judged coverage from first/last timestamps, which cannot reveal missing intervals between those timestamps. ML then displayed insufficient history without the provider failure. Sparse/new listings and empty intervals could repeatedly trigger full downloads.

The adapter now follows continuation tokens, preserves normal callers' total limits, requires complete paginated intervals for deep five-minute history, rejects symbol mismatches/malformed responses, and bounds pagination. Provider fallback starts a new complete request; it cannot borrow partial primary rows. Cache v2 records completed request coverage, reports failures distinctly, retries failed history after a short pause, and uses unique atomic temporary files. Old cache files remain intact but are not reused as complete training history. ML does not train on incomplete history, even if partial history exceeds 700 bars. The existing 700-bar threshold and validation requirements are unchanged.

Using the configured local Alpaca account, the corrected shared path returned 13,857 SVRN five-minute bars from the last year, with 14 requests (two continuation requests) in 2.76 seconds. Its warm reload made zero requests and returned identical history. The corresponding old first-page-only path would have returned 13,563 bars, also above 700. Therefore pagination is a confirmed defect but is not sufficient evidence for the hosted app's repeated insufficient-history result. The hosted configuration contains nonempty Alpaca and Tradier keys; their values were not exported or changed. Existing hosted logs do not expose history errors. Hosted ML availability still requires checking the new diagnostics after deployment.

The provider's documented continuation behavior is described in [Alpaca historical bars](https://docs.alpaca.markets/us/reference/stockbars). [Alpaca's data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq) distinguishes delayed historical SIP access from live SIP entitlement.

## Verification

- 17 acquisition/cache/error regressions, without training or research-data access.
- 31 live-price/provider-failure regressions, run in their normal separate process.
- 20 overview and 12 rendering/lifecycle regressions.
- 10 relevant existing history, ML gate, refresh, and continuity checks.
- Compilation and standalone import boundary across all 85 Python files in the deployment manifest; unrelated untracked research directories are not publication inputs.
- Desktop, narrow-screen, fallback-message containment, scrolling, and navigation browser evidence from the layout implementation.

Combining otherwise separate test programs in one interpreter revealed pre-existing global test-fixture pollution (provider import flags and a replaced stream-router function). The normal separate-process invocations pass; these mixed-process attempts are not reported as passing. The whole working-directory recursive boundary check includes protected untracked Lab copies and is not claimed green. Those copies were left untouched.

These checks do not certify live feed entitlement, profitable predictions, or market-open operation. The bounded authenticated diagnostic fetched market history only; it did not train models, change credentials, place orders, or modify the separate research jobs.

## Hosted follow-up

After deployment of PR #105, the hosted SVRN result confirmed zero five-minute bars and provider HTTP 401. Alpaca rejects the hosted authentication pair; the old history loader hid that failure as insufficient history. Tradier still provides the main analysis. Replacing the hosted Alpaca pair with the existing locally verified pair requires the user's specific credential-change approval, which has been requested. No credential values were exported or changed.

The hosted navigation check also reproduced a separate warm-cache lifecycle defect: selecting a recently analyzed ticker from the landing picker restored its result while leaving the Analyze button permanently disabled as "Analyzing...". That path starts no worker and therefore receives no worker-completion callback. The renderer now ends the loading state when a result is ready and no manual refresh is pending. A regression reproduces the failure before the change, then checks the cached result, enabled button, and a subsequent real background-refresh handoff. Active/forced/manual requests keep their existing lifecycle.

## Deployment

Production remains Streamlit Community Cloud, repository `derektshaffer/STOCK-SCANNER`, branch `main`, entrypoint `analyzer_app.py`. No dependency or secret changes are required. Merge only after the release checks pass; then verify the deployed overview and ML diagnostic in the existing hosted app. The first successful history load builds a new v2 cache. Rollback is the previous deployed commit; the old cache remains available to the old code. Market-open price/provider verification remains outstanding and must not be described as completed based on after-hours checks.
