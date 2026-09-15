"""Offline ranking, publication and real Streamlit rendering regressions."""

import copy
import csv
import importlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from scanner_ranking import rank_candidates, tradeability_rank_key
import rendering_regression_check

ROOT = Path(__file__).resolve().parent


def backend():
    with patch.dict(os.environ, {"TRADIER_ACCESS_TOKEN": "offline-test-only"}):
        return importlib.import_module("stock_scanner")


class RankingTests(unittest.TestCase):
    def test_tradeability_beats_explosion_grade_and_ml_without_mutation(self):
        rows = [
            dict(symbol="IGNITION", tradeability_score=20, explosion_score=100,
                 setup_grade="A", passed_base_filters=True, score=100,
                 opportunity_score=100, ml_validated=True),
            dict(symbol="QUALITY", tradeability_score=90, explosion_score=12,
                 setup_grade="REJECT", passed_base_filters=False,
                 action_data_integrity_ok=False, scanner_action="DATA CHECK",
                 failed_filters=["thin liquidity"], tradability_warnings=["wide spread"]),
        ]
        before = copy.deepcopy(rows)
        ranked = rank_candidates(rows)
        self.assertEqual([r["symbol"] for r in ranked], ["QUALITY", "IGNITION"])
        self.assertEqual(rows, before)
        self.assertIs(ranked[0], rows[1])
        self.assertEqual(ranked[0]["scanner_action"], "DATA CHECK")
        self.assertFalse(ranked[0]["action_data_integrity_ok"])
        self.assertEqual(sorted(rows, key=backend().ranking_key, reverse=True), ranked)

    def test_equal_scores_stay_stable_despite_opposing_supporting_metrics(self):
        rows = [dict(symbol="FIRST", tradeability_score=75, explosion_score=1, score=0),
                dict(symbol="SECOND", tradeability_score=75, explosion_score=99, score=100)]
        self.assertEqual(rank_candidates(rows), rows)
        self.assertEqual(rank_candidates(rows[::-1]), rows[::-1])
        self.assertEqual(backend().ranking_key(rows[0]), backend().ranking_key(rows[1]))

    def test_unavailable_scores_sort_after_valid_zero(self):
        values = [None, "90", "bad", float("nan"), float("inf"),
                  float("-inf"), False, True, 0, 100, 10 ** 1000]
        rows = [dict(symbol=str(i), tradeability_score=v, explosion_score=100-i)
                for i, v in enumerate(values)]
        self.assertEqual([r["symbol"] for r in rank_candidates(rows)],
                         ["9", "1", "8", "0", "2", "3", "4", "5", "6", "7", "10"])
        self.assertEqual(rank_candidates([]), [])
        self.assertEqual(tradeability_rank_key({}), float("-inf"))

    def test_expanded_details_prioritize_tradeability_and_do_not_invent_explosion(self):
        from scanner_expand import _load_details, scanner_detail_html
        details = _load_details({"candidates": [dict(symbol="TEST", score=90, tradeability_score=75)]})
        self.assertEqual(details["TEST"]["explosion_score"], "—")
        content = scanner_detail_html(details["TEST"])
        self.assertLess(content.index(">Tradeability<"), content.index(">Explosion Score<"))
        for value in (None, True, "invalid", float("inf")):
            details = _load_details({"candidates": [dict(symbol="TEST", tradeability_score=value)]})
            self.assertEqual(details["TEST"]["tradeability_score"], "—")

    def test_new_publication_json_csv_and_ranks_use_tradeability_before_limit(self):
        scanner = backend()
        rows = [dict(symbol=f"T{i}", tradeability_score=i, explosion_score=100-i,
                     setup_grade="REJECT", passed_base_filters=False,
                     scanner_action="DATA CHECK", action_data_integrity_ok=False)
                for i in range(5)]
        rows.sort(key=scanner.ranking_key, reverse=True)
        before = copy.deepcopy(rows)
        now = datetime(2026, 9, 15, 15, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(scanner, "SCAN_LOG_DIR", tmp), \
                patch.object(scanner, "SCAN_LOG_TOP", 3), \
                patch.dict(os.environ, {"SCANNER_PASSIVE_EVIDENCE_ENABLED": "0"}), \
                redirect_stdout(io.StringIO()):
            paths = scanner.write_scan_logs(rows, now, now, [])
            self.assertTrue(paths)
            payload = json.loads((Path(tmp)/"latest_scan.json").read_text())
            records = payload["candidates"]
            self.assertEqual([r["symbol"] for r in records], ["T4", "T3", "T2"])
            self.assertEqual([r["rank"] for r in records], [1, 2, 3])
            self.assertTrue(all(r["scanner_action"] == "DATA CHECK" for r in records))
            self.assertTrue(all(not r["action_data_integrity_ok"] for r in records))
            with next(Path(tmp).glob("scan_*.csv")).open() as stream:
                exported = list(csv.DictReader(stream))
            self.assertEqual([r["symbol"] for r in exported], ["T4", "T3", "T2"])
        self.assertEqual(rows, before)


class RankingRenderingTests(unittest.TestCase):
    # Restore the existing fixture's simulated clock after each UI test.
    tearDown = rendering_regression_check.RenderingTests.tearDown

    def app(self, *, standalone=False, missing=False):
        source = (ROOT/"tests/rendering_fixture.py").read_text()
        source = source.replace("ROOT=Path(__file__).resolve().parents[1]", f"ROOT=Path({str(ROOT)!r})")
        rows = [dict(symbol=f"T{i}", tradeability_score=i*2, explosion_score=100-i,
                     setup_grade="REJECT", passed_base_filters=False,
                     scanner_action="NO TRADE", scanner_action_tier="blocked",
                     action_data_integrity_ok=False, timeframe_best_fit="INTRADAY",
                     price=10, score=50, day_pct=1,
                     failed_filters=["thin liquidity"], tradability_warnings=["wide spread"])
                for i in range(35)]
        if missing:
            rows[-1]["tradeability_score"] = None
            rows[-2]["tradeability_score"] = "invalid"
        # T34 has the highest Tradability even though it lacks Explosion data.
        rows[-1]["explosion_score"] = None
        replacement = "payload=snapshot()\n        payload['candidates']=" + repr(rows) + "\n        return json.dumps(payload)"
        source = source.replace("return json.dumps(snapshot())", replacement)
        if standalone:
            source = source.replace("ROOT/'analyzer_app.py'", "ROOT/'scanner_app.py'")
        at = AppTest.from_string(source, default_timeout=25)
        at.session_state["scanner_show_detailed_data"] = True
        at.run()
        self.assertFalse(at.exception, [e.message for e in at.exception])
        return at

    def assert_detailed_ranking(self, at, first="T34"):
        table = next(df.value for df in at.dataframe if "Tradeability" in df.value.columns)
        self.assertEqual(list(table.columns[:6]), ["#", "Ticker", "Tradeability", "Action", "Grade", "Status"])
        self.assertGreater(list(table.columns).index("Explosion"), list(table.columns).index("Action"))
        self.assertEqual(len(table), 30)
        self.assertEqual(table.iloc[0]["Ticker"], first)
        self.assertEqual(table["Tradeability"].tolist(), sorted(table["Tradeability"], reverse=True))
        cards = [m.value for m in at.markdown if '<div class="card ' in m.value]
        self.assertEqual(len(cards), 6)
        self.assertIn(f'<div class="ticker">{first}</div>', cards[0])
        self.assertTrue(all("<small>TRADEABILITY / 100</small>" in c for c in cards))
        self.assertTrue(all("EXPLOSION (SUPPORTING)" in c for c in cards))
        self.assertTrue(all("DATA CHECK" in c and "thin liquidity" in c for c in cards))
        self.assertTrue(any("No candidate currently combines clean data integrity" in w.value for w in at.warning))

    def test_combined_compact_and_detailed_views_sort_saved_snapshot_before_caps(self):
        at = self.app()
        rows = [m.value for m in at.markdown if 'data-symbol="' in m.value]
        self.assertEqual(len(rows), 15)
        self.assertIn('data-symbol="T34"', rows[0])
        self.assertIn('data-symbol="T20"', rows[-1])
        self.assertTrue(all(r.index(">Tradeability<") < r.index(">ACTION<") < r.index(">Explosion<") for r in rows))
        self.assert_detailed_ranking(at)
        at.button(key="combined_analyze_0_T34").click().run()
        self.assertFalse(at.exception, [e.message for e in at.exception])
        self.assertEqual(at.session_state["app_view"], "Stock Analyzer")
        self.assertTrue(any("Analyzing T34 in the background" in i.value for i in at.info))

    def test_standalone_scanner_uses_same_order_and_card_hierarchy(self):
        self.assert_detailed_ranking(self.app(standalone=True))

    def test_invalid_saved_scores_do_not_crash_or_replace_with_explosion(self):
        at = self.app(missing=True)
        self.assert_detailed_ranking(at, first="T32")



if __name__ == "__main__":
    unittest.main()
