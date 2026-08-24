SINGLE STOCK ANALYZER — MATCHED TRADE PLAN V2

Upload/replace these FOUR files in the ROOT of the GitHub repository:

1. app.py
2. analyzer_app.py
3. analyzer_app_fixed.py
4. stock_analyzer.py

Do not rename them.

The key file is stock_analyzer.py. It must contain:
    def build_trade_plan(metrics, now):
and:
    metrics["trade_plan"]=build_trade_plan(metrics,now)

Verification after deploy:
- Top-right card says BASE SETUP
- SUGGESTED TRADE PLAN has numeric entry/stop/targets (even when status is WAIT)
- Bottom caption ends with Engine=trade-plan-v2

If the UI and engine are ever mismatched again, the app will show an explicit error instead of blank trade-plan fields.
