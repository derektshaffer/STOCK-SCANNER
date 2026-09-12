"""Offline regression checks for history acquisition; no training or research data."""
import gzip
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import analyzer_history_cache as hc
import ml_predictor as ml
import stock_analyzer as sa

END = datetime(2026, 9, 12, 15, tzinfo=timezone.utc)
START = END - timedelta(days=45)


def bar(dt):
    return {"t": dt.isoformat(), "o": 10, "h": 11, "l": 9, "c": 10, "v": 100}


class PaginationTests(unittest.TestCase):
    def test_short_and_empty_pages_do_not_end_history(self):
        pages = [{"bars": [bar(START)], "next_page_token": "a"},
                 {"bars": [], "next_page_token": "b"},
                 {"bars": [bar(END)], "next_page_token": None}]
        with patch.object(sa, "get_json", side_effect=pages) as fetch:
            rows = sa.bars("TEST", "5Min", START, END, 10000, feed="sip", complete=True)
        self.assertEqual([r["t"] for r in rows], [START.isoformat(), END.isoformat()])
        queries = [parse_qs(urlparse(call.args[0]).query) for call in fetch.call_args_list]
        self.assertEqual(queries[1]["page_token"], ["a"])
        self.assertEqual(queries[2]["feed"], ["sip"])
        self.assertEqual(queries[2]["start"], queries[0]["start"])

    def test_normal_callers_keep_total_limit(self):
        with patch.object(sa, "get_json", return_value={"bars": [bar(START), bar(END)], "next_page_token": "more"}) as fetch:
            self.assertEqual(len(sa.bars("TEST", "1Day", START, END, 1)), 1)
            self.assertEqual(fetch.call_count, 1)

    def test_complete_history_continues_past_requested_page_size(self):
        with patch.object(sa, "get_json", side_effect=[
            {"bars": [bar(START)], "next_page_token": "more"}, {"bars": [bar(END)]}]):
            self.assertEqual(len(sa.bars("TEST", "5Min", START, END, 1, complete=True)), 2)

    def test_page_failure_never_returns_first_page_as_complete(self):
        with patch.object(sa, "get_json", side_effect=[
            {"bars": [bar(START)], "next_page_token": "more"}, TimeoutError()]):
            with self.assertRaises(TimeoutError):
                sa.bars("TEST", "5Min", START, END, complete=True)

    def test_repeated_tokens_and_request_bound_fail_closed(self):
        for payloads in (
            [{"bars": [], "next_page_token": "same"}] * 2,
            [{"bars": [], "next_page_token": str(i)} for i in range(20)],
        ):
            with self.subTest(count=len(payloads)), patch.object(sa, "get_json", side_effect=payloads):
                with self.assertRaisesRegex(RuntimeError, "pagination"):
                    sa.bars("TEST", "5Min", START, END, complete=True)

    def test_deduplicates_timestamps_and_excludes_future_rows(self):
        with patch.object(sa, "get_json", return_value={"bars": [bar(END), bar(START), bar(END), bar(END + timedelta(minutes=5))]}):
            rows = sa.bars("TEST", "5Min", START, END, complete=True)
            self.assertEqual(rows, [bar(START), bar(END)])

    def test_wrong_symbol_is_rejected(self):
        with patch.object(sa, "get_json", return_value={"symbol": "OTHER", "bars": [bar(END)]}):
            with self.assertRaisesRegex(RuntimeError, "different symbol"):
                sa.bars("TEST", "5Min", START, END)

    def test_successful_http_error_envelope_is_not_empty_history(self):
        with patch.object(sa, "get_json", return_value={"code": 401, "message": "PRIVATE"}):
            with self.assertRaisesRegex(RuntimeError, "malformed"):
                sa.bars("TEST", "5Min", START, END)

    def test_fallback_discards_incomplete_primary_page(self):
        calls = []
        def fetch(url):
            q = parse_qs(urlparse(url).query); calls.append(q)
            if q["feed"] == ["sip"]:
                if "page_token" in q:
                    raise RuntimeError("Alpaca HTTP 403: secret-provider-body")
                return {"bars": [bar(START)], "next_page_token": "more"}
            return {"bars": [bar(START + timedelta(days=1))]}
        with patch.object(sa, "get_json", side_effect=fetch), patch.object(sa, "LIVE_FEED", "iex"):
            rows, source = sa.try_sip_delayed_bars("TEST", "5Min", START, END - timedelta(days=1), 10000)
        self.assertEqual(rows, [bar(START + timedelta(days=1))])
        self.assertEqual(source, "IEX")
        self.assertEqual(len(calls), 3)


class CacheAndModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patcher = patch.object(hc, "CACHE_DIR", Path(self.tmp.name))
        self.patcher.start(); self.addCleanup(self.patcher.stop)

    def load(self, fetch, **kwargs):
        return hc.load_deep_5m_history("TEST", end=END, days=90, step_days=30, fetch_bars=fetch, **kwargs)

    def test_interior_failure_is_not_cached_as_complete_and_recovers(self):
        calls = []
        def failing(symbol, tf, start, end, limit):
            calls.append(start)
            if start == END - timedelta(days=60):
                raise RuntimeError("Alpaca HTTP 401: PRIVATE BODY")
            return [bar(start), bar(end)], "delayed SIP"
        with patch.object(hc.time, "time", return_value=1000):
            with self.assertRaises(hc.HistoryLoadError) as caught:
                self.load(failing)
        self.assertIn("401", str(caught.exception)); self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertEqual(caught.exception.diagnostics["failed_chunks"], 1)
        with patch.object(hc.time, "time", return_value=1020):
            with self.assertRaises(hc.HistoryLoadError): self.load(failing)
        self.assertEqual(len(calls), 3)
        with patch.object(hc.time, "time", return_value=1061):
            rows, source = self.load(lambda s,t,start,end,n: ([bar(start), bar(end)], "delayed SIP"))
        self.assertEqual(len(rows), 4)
        self.assertEqual(source, "delayed SIP")
        self.assertTrue(hc._load_cache("TEST")["complete"])

    def test_new_listing_and_empty_intervals_share_complete_cache(self):
        for bars in ([], [bar(END-timedelta(days=1))]):
            with self.subTest(rows=len(bars)):
                hc._cache_path("TEST").unlink(missing_ok=True)
                with patch.object(hc.time, "time", return_value=1000):
                    self.load(lambda *args: (bars, "delayed SIP"))
                    with patch.object(hc, "_fetch_chunks", side_effect=AssertionError("unnecessary refetch")):
                        result, _ = self.load(None)
                self.assertEqual(result, bars)

    def test_old_unpaginated_cache_is_preserved_but_not_reused(self):
        old = Path(self.tmp.name)/"TEST-5min.json.gz"
        with gzip.open(old, "wt") as f:
            json.dump({"rows": [bar(START)], "coverage_start": START.isoformat(), "coverage_end": END.isoformat()}, f)
        rows, _ = self.load(lambda *args: ([bar(END)], "delayed SIP"))
        self.assertEqual(rows, [bar(END)])
        self.assertTrue(old.exists())

    def test_expired_cache_tail_failure_cannot_reach_training(self):
        with patch.object(hc.time, "time", return_value=1000):
            self.load(lambda s,t,start,end,n: ([bar(start), bar(end)], "delayed SIP"))
        with patch.object(hc.time, "time", return_value=3000):
            with self.assertRaises(hc.HistoryLoadError):
                self.load(lambda *args: (_ for _ in ()).throw(TimeoutError()))

    def test_concurrent_cache_writes_are_atomic(self):
        payloads = [{"rows": [bar(END)], "marker": i} for i in range(8)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertTrue(all(pool.map(lambda p: hc._write_cache("TEST", p), payloads)))
        self.assertIn(hc._load_cache("TEST"), payloads)
        self.assertFalse(list(Path(self.tmp.name).glob("*.tmp")))

    def test_missing_credentials_get_diagnostics_before_any_training(self):
        def fetch(*args):
            raise RuntimeError("Missing ALPACA_API_KEY / ALPACA_SECRET_KEY")
        with patch.object(ml, "_build_dataset", side_effect=AssertionError("training path reached")):
            result = ml.predict_ml("TEST", END, {}, lambda *args: self.load(fetch), sa.ET)
        self.assertEqual(result["status"], "history_unavailable")
        self.assertIn("credentials missing", result["error"])
        self.assertEqual(result["models"], {})

    def test_genuine_short_history_keeps_existing_700_bar_gate(self):
        with patch.object(ml, "_build_dataset", side_effect=AssertionError("training path reached")):
            result = ml.predict_ml("TEST", END, {}, lambda *args: ([bar(END)]*699, "delayed SIP"), sa.ET)
        self.assertEqual(result["status"], "insufficient_history")
        self.assertEqual(result["bar_count"], 699)

    def test_partial_high_count_does_not_bypass_ml_gate(self):
        error = hc.HistoryLoadError({"status":"history_unavailable", "message":"History fetch failed", "source":"delayed SIP", "bar_count":9000})
        with patch.object(ml, "_build_dataset", side_effect=AssertionError("training path reached")):
            result = ml.predict_ml("TEST", END, {}, lambda *args: (_ for _ in ()).throw(error), sa.ET)
        self.assertEqual(result["status"], "history_unavailable")
        self.assertEqual(result["models"], {})


if __name__ == "__main__":
    unittest.main()
