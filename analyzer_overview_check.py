"""Offline presentation/interaction regressions; never requests prices or trains ML."""
import copy
from datetime import datetime, timezone, timedelta
from pathlib import Path
import unittest
from unittest.mock import patch

from analyzer_overview import chart_frame, price_figure, ml_summary, render_quote, DETAILS

ROOT = Path(__file__).resolve().parent


class HtmlSink:
    def __init__(self): self.values = []
    def html(self, value): self.values.append(value)


class OverviewDataTests(unittest.TestCase):
    def bars(self):
        return {"symbol": "TEST", "chart_data": {"intraday": [
            {"t": f"2026-09-11T13:{minute:02d}:00Z", "o": 10, "h": 12, "l": 9, "c": 11, "v": 10}
            for minute in (30, 31, 34, 40)]}}

    def test_ohlcv_aggregation_preserves_values_and_gaps(self):
        frame = chart_frame(self.bars(), "5m")
        self.assertEqual(len(frame), 2)
        self.assertEqual(frame.iloc[0].to_dict(), {"o": 10, "h": 12, "l": 9, "c": 11, "v": 30})
        self.assertEqual(frame.iloc[1].v, 10)

    def test_daily_bars_never_become_intraday_history(self):
        r = self.bars(); r["chart_data"]["daily"] = r["chart_data"].pop("intraday")
        self.assertTrue(chart_frame(r, "5m").empty)
        self.assertIsNone(price_figure(r, "15m"))

    def test_bad_prices_and_missing_volume_are_not_fabricated(self):
        r = self.bars(); r["chart_data"]["intraday"][0]["h"] = 2
        for bar in r["chart_data"]["intraday"]: bar["v"] = None
        frame = chart_frame(r, "5m")
        self.assertTrue(frame.v.isna().all())
        self.assertEqual(frame.iloc[0].h, 12)

    def test_chart_uses_pacific_time_and_keeps_session_dates(self):
        r = self.bars()
        self.assertEqual(price_figure(r, "5m").data[0].x[0], "2026-09-11 06:30")
        r["chart_data"]["daily"] = [dict(r["chart_data"]["intraday"][0], t="2026-09-11")]
        self.assertEqual(price_figure(r, "D").data[0].x[0], "2026-09-11")

    def test_visualization_does_not_mutate_analysis(self):
        r = self.bars(); before = copy.deepcopy(r)
        price_figure(r, "5m"); ml_summary(r)
        self.assertEqual(r, before)

    def test_insufficient_history_cannot_show_stray_predictions(self):
        r = {"ml_prediction": {"status": "insufficient_history", "bar_count": 412, "ml_edge_score": 90,
             "models": {"higher_30": {"status": "ok", "probability_pct": 88}}}}
        summary = ml_summary(r)
        self.assertTrue(all(v == "—" for _, v in summary["cells"]))
        self.assertIn("412", summary["history"]); self.assertIn("700 required", summary["history"])

    def test_advisory_predictions_keep_their_label(self):
        r = {"ml_prediction": {"status": "ok", "ml_edge_score": 90, "models": {
             "higher_30": {"status": "ok", "probability_pct": 88, "validated": False}}}}
        cells = dict(ml_summary(r)["cells"])
        self.assertEqual(cells["Validated edge"], "—")
        self.assertEqual(cells["30m higher"], "ADVISORY · 88%")

    def test_failed_history_is_not_shown_as_collecting_or_training(self):
        summary = ml_summary({"ml_prediction": {"status":"history_unavailable", "error":"provider HTTP 401", "bar_count":9000}})
        self.assertEqual(summary["badge"], "HISTORY UNAVAILABLE")
        self.assertIn("401", summary["reason"])
        self.assertIn("training not started", summary["history"])
        self.assertTrue(all(value == "—" for _, value in summary["cells"]))

    def test_research_model_estimates_are_not_live_predictions(self):
        r = {"research_only": True, "ml_prediction": {"status": "ok"}}
        self.assertEqual(ml_summary(r)["badge"], "RESEARCH ONLY")
        self.assertIn("no live prediction", ml_summary(r)["reason"])

    def test_research_quote_ignores_live_overlay(self):
        sink = HtmlSink()
        render_quote(sink, {"symbol": "TEST", "research_only": True, "price": 10, "reference_price_timestamp": "2026-09-11"}, {"symbol": "OTHER", "price": 90})
        self.assertIn("$10.00", sink.values[0]); self.assertNotIn("90", sink.values[0])
        self.assertIn("Reference close", sink.values[0])

    def test_wrong_symbol_overlay_is_unavailable(self):
        sink = HtmlSink()
        render_quote(sink, {"symbol": "TEST"}, {"symbol": "OTHER", "price": 90})
        self.assertIn("UNAVAILABLE", sink.values[0]); self.assertNotIn("$90", sink.values[0])

    def test_stale_quote_is_never_labelled_live(self):
        sink = HtmlSink()
        render_quote(sink, {"symbol": "TEST", "price": 10, "live_price_available": True,
                     "live_price_source": "tradier_consolidated_trade",
                     "live_price_timestamp": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()})
        self.assertIn("STALE", sink.values[0]); self.assertIn("Last known", sink.values[0])
        self.assertNotIn("LIVE ·", sink.values[0])

    def test_quote_html_is_escaped(self):
        sink = HtmlSink()
        render_quote(sink, {"symbol": '<img src=x onerror="alert(1)">', "research_only": True, "price": 10})
        self.assertNotIn("<img", sink.values[0]); self.assertIn("&lt;img", sink.values[0])


class OverviewInteractionTests(unittest.TestCase):
    def tearDown(self):
        import live_price_quality, stock_analyzer, consistency_regression_check
        import datetime as datetime_module
        datetime_module.datetime = datetime
        for module in (live_price_quality, stock_analyzer, consistency_regression_check):
            module.datetime = datetime

    def app(self, mode="research"):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(ROOT / "tests/rendering_fixture.py"), default_timeout=25)
        at.query_params["overview"] = mode
        at.run(); self.clean(at)
        return at

    def clean(self, at):
        self.assertFalse(at.exception, [e.message for e in at.exception])

    def test_overview_and_every_detail_render_in_both_modes(self):
        for mode in ("research", "live"):
            at = self.app(mode)
            self.assertEqual(len(at.get("plotly_chart")), 1)
            self.assertFalse(at.expander)
            self.assertFalse(any("Single Stock Analyzer" in x.value for x in at.markdown))
            for section in DETAILS:
                with self.subTest(mode=mode, section=section):
                    at.session_state["analyzer_detail"] = section
                    at.run(); self.clean(at)
                    self.assertEqual(at.session_state["result"]["symbol"], "SVRN")

    def test_panel_links_reach_shared_details(self):
        at = self.app()
        for key, section in (("overview_ml_details", "ML diagnostics"), ("overview_fit_details", "Setup & timeframe"),
                             ("overview_pattern_details", "Patterns"), ("overview_sources_details", "Sources")):
            at.button(key=key).click().run(); self.clean(at)
            self.assertEqual(at.session_state["analyzer_detail"], section)
        at.button(key="toolbar_save_current_stock").click().run(); self.clean(at)
        self.assertEqual(at.session_state["analyzer_detail"], "Sources")

    def test_saved_stock_actions_and_position_inputs(self):
        at = self.app("live")
        at.button(key="toolbar_save_current_stock").click().run(); self.clean(at)
        self.assertEqual(at.session_state["saved_stocks"], ["SVRN"])
        at.button(key="toolbar_remove_current_stock").click().run(); self.clean(at)
        self.assertEqual(at.session_state["saved_stocks"], [])
        at.toggle(key="position_owned_SVRN").set_value(True).run(); self.clean(at)
        self.assertEqual(at.session_state["analyzer_detail"], "Execution plan")
        at.number_input(key="position_avg_cost_SVRN").set_value(9.5).run(); self.clean(at)
        self.assertEqual(at.session_state["_position_inputs_by_symbol"]["SVRN"]["average_cost"], 9.5)

    def test_research_controls_remain_disabled(self):
        at = self.app()
        self.assertTrue(at.toggle(key="auto_refresh_enabled").disabled)
        self.assertTrue(at.toggle(key="position_owned_SVRN").disabled)
        self.assertFalse(at.session_state["result"]["live_price_available"])
        self.assertEqual(at.session_state["result"]["trade_plan"]["selected"], {})

    def test_manual_analyze_cancels_without_stuck_loading(self):
        at = self.app("live")
        at.button(key="analyzer_manual_analyze").click().run(); self.clean(at)
        self.assertTrue(any("background" in x.value for x in at.info))
        at.button(key="cancel_combined_loader_SVRN").click().run(); self.clean(at)
        self.assertFalse(at.session_state["_analyzer_loading"])

    def test_switching_chart_does_not_request_analysis_or_train(self):
        at = self.app("live")
        with patch("stock_analyzer.analyze", side_effect=AssertionError("UI must not analyze")), \
             patch("ml_predictor.predict_ml", side_effect=AssertionError("UI must not train")):
            for timeframe in ("15m", "1h", "D", "5m"):
                at.session_state["overview_chart_timeframe"] = timeframe
                at.run(); self.clean(at)
                self.assertFalse(at.session_state.filtered_state.get("_analyzer_bootstrap_launch_state"))


if __name__ == "__main__":
    unittest.main()
