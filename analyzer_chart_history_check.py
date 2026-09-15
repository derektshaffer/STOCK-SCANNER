"""Offline regressions for chart-only history and bounded provider failures."""
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

import analyzer_chart_history as history
import tradier_live
from analyzer_overview import chart_frame, price_figure

NOW = datetime(2026, 9, 12, 16, tzinfo=timezone.utc)
BAR = dict(t="2026-09-10T13:30:00Z", o=10, h=12, l=9, c=11, v=100)


class ChartHistoryTests(unittest.TestCase):
    def load(self, rows):
        with patch.object(history, "get_cached_context", return_value=None), \
             patch.object(history, "set_cached_context") as save, \
             patch.object(history, "get_timesales_bars", return_value=rows) as fetch:
            result = history.load_chart_history("TEST", NOW, "2026-09-11", tradier_token="fixture")
        return result, fetch, save

    def test_bounded_regular_history_excludes_reference_session(self):
        result, fetch, save = self.load([BAR, dict(BAR, t="2026-09-11T13:30:00Z"),
                                        dict(BAR, t="2026-09-10T12:30:00Z")])
        self.assertEqual(len(result["bars"]), 1)
        self.assertEqual(result["status"], "ok")
        self.assertEqual((fetch.call_args.args[3] - fetch.call_args.args[2]).days, 30)
        self.assertEqual(fetch.call_args.kwargs, dict(interval="5min", session_filter="open", strict=True, timeout=12))
        save.assert_called_once()

    def test_empty_is_not_a_successful_history_cache(self):
        result, _, save = self.load([])
        self.assertEqual(result["status"], "empty")
        save.assert_not_called()

    def test_invalid_ohlcv_fails_closed_without_partial_rows(self):
        result, _, save = self.load([BAR, dict(BAR, t="2026-09-10T13:35:00Z", h=2)])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["bars"], [])
        save.assert_not_called()

    def test_provider_error_redacted_and_cannot_abort_analysis(self):
        with patch.object(history, "get_cached_context", return_value=None), \
             patch.object(history, "get_timesales_bars", side_effect=RuntimeError("HTTP 401 SECRET")):
            result = history.load_chart_history("TEST", NOW, "2026-09-11", tradier_token="fixture")
        self.assertIn("provider HTTP 401", result["error"])
        self.assertNotIn("SECRET", str(result))

    def test_completed_history_cache_avoids_provider_fetch(self):
        cached = dict(symbol="TEST", status="ok", bars=[BAR])
        with patch.object(history, "get_cached_context", return_value=cached), \
             patch.object(history, "get_timesales_bars", side_effect=AssertionError("network")):
            self.assertEqual(history.load_chart_history("TEST", NOW, "2026-09-11", tradier_token="fixture"), cached)

    def test_alpaca_uses_complete_pagination_and_separate_source(self):
        from unittest.mock import Mock
        fetch = Mock(return_value=[BAR])
        with patch.object(history, "get_cached_context", return_value=None), patch.object(history, "set_cached_context"):
            result = history.load_chart_history("TEST", NOW, "2026-09-11", alpaca_fetch=fetch)
        self.assertTrue(fetch.call_args.kwargs["complete"])
        self.assertIn("Alpaca sip", result["source"])

    def test_strict_tradier_rejects_faults_missing_fields_and_wrong_symbols(self):
        raw = dict(time="2026-09-10T09:30:00", open=10, high=12, low=9, close=11, volume=100)
        for payload in ({"fault": {}}, {"series": {}}, {"series": {"data": dict(raw, volume=None)}},
                        {"series": {"data": dict(raw, symbol="WRONG")}}):
            with self.subTest(payload=payload), patch.object(tradier_live, "_request_json", return_value=payload):
                with self.assertRaises(ValueError):
                    tradier_live.get_timesales_bars("TEST", "fixture", NOW, NOW, strict=True)
        with patch.object(tradier_live, "_request_json", return_value={"series": {"data": raw}}):
            self.assertEqual(len(tradier_live.get_timesales_bars("TEST", "fixture", NOW, NOW, strict=True)), 1)

    def test_history_never_overwrites_or_double_counts_reference_session(self):
        current = dict(BAR, t="2026-09-11T13:30:00Z", v=7)
        result = dict(symbol="TEST", research_only=True, reference_price_timestamp="2026-09-11",
            chart_data={"intraday": [current]}, overview_history=dict(symbol="TEST", status="ok", bars=[BAR, dict(current, v=500)]))
        frame = chart_frame(result, "5m")
        self.assertEqual(len(frame), 2)
        self.assertEqual(list(frame.v), [100, 7])
        self.assertEqual(result["chart_data"]["intraday"], [current])
        result["overview_history"]["symbol"] = "WRONG"
        self.assertEqual(len(chart_frame(result, "5m")), 1)

    def test_range_selects_initial_window_without_discarding_loaded_history(self):
        result = dict(symbol="TEST", as_of=NOW.isoformat(), chart_data={"intraday": [BAR]},
            overview_history=dict(symbol="TEST", status="ok", bars=[dict(BAR, t="2026-09-09T13:30:00Z")]))
        fig = price_figure(result, "5m", "1D")
        self.assertEqual(len(fig.data[0].x), 2)
        self.assertEqual(fig.layout.xaxis.range[0], .5)
        self.assertEqual(fig.layout.xaxis.minallowed, -.5)
        self.assertEqual(fig.layout.xaxis.maxallowed, 1.5)
        self.assertEqual(fig.layout.xaxis.type, "category")

    def test_daily_uses_existing_long_history_without_intraday_substitution(self):
        result = dict(daily_context_bars=[dict(BAR, t="2026-09-09")],
                      chart_data={"daily": [dict(BAR, t="2026-09-10")]})
        self.assertEqual(len(chart_frame(result, "D")), 2)
        self.assertTrue(chart_frame(result, "5m").empty)

    def test_daily_mixed_timestamp_formats_use_one_candle_per_session(self):
        result = dict(daily_context_bars=[dict(BAR, t="2026-09-10T04:00:00Z")],
                      chart_data={"daily": [dict(BAR, t="2026-09-10", c=10.5)]})
        frame = chart_frame(result, "D")
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0].c, 10.5)


if __name__ == "__main__":
    unittest.main()
