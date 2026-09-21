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

from scanner_ranking import (rank_candidates, tradeability_rank_key, execution_quality_value,
                             tradability_value, tradability_status, execution_quality_rank_key)
import rendering_regression_check

ROOT = Path(__file__).resolve().parent


def backend():
    with patch.dict(os.environ, {"TRADIER_ACCESS_TOKEN": "offline-test-only"}):
        return importlib.import_module("stock_scanner")


class RankingTests(unittest.TestCase):
    def test_eligible_review_beats_rejected_100_without_mutation(self):
        rows = [
            dict(symbol="IGNITION", tradeability_score=20, explosion_score=100,
                 setup_grade="A", passed_base_filters=True, score=100,
                 scanner_action="WATCH", action_data_integrity_ok=True,
                 opportunity_score=100, ml_validated=True),
            dict(symbol="QUALITY", tradeability_score=100, explosion_score=12,
                 setup_grade="REJECT", passed_base_filters=False,
                 action_data_integrity_ok=False, scanner_action="DATA CHECK",
                 failed_filters=["thin liquidity"], tradability_warnings=["wide spread"]),
        ]
        before = copy.deepcopy(rows)
        ranked = rank_candidates(rows)
        self.assertEqual([r["symbol"] for r in ranked], ["IGNITION", "QUALITY"])
        self.assertEqual(rows, before)
        self.assertIs(ranked[1], rows[1])
        self.assertEqual(tradability_value(ranked[1]), 0)
        self.assertEqual(execution_quality_value(ranked[1]), 100)
        self.assertEqual(ranked[1]["scanner_action"], "DATA CHECK")
        self.assertFalse(ranked[1]["action_data_integrity_ok"])
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
        self.assertEqual(tradeability_rank_key({}), (False, float("-inf")))

    def test_expanded_details_prioritize_tradeability_and_do_not_invent_explosion(self):
        from scanner_expand import _load_details, scanner_detail_html
        details = _load_details({"candidates": [dict(symbol="TEST", score=90, tradeability_score=75)]})
        self.assertEqual(details["TEST"]["explosion_score"], "—")
        content = scanner_detail_html(details["TEST"])
        self.assertLess(content.index(">Tradability<"), content.index(">Explosion Score<"))
        for value in (None, True, "invalid", float("inf")):
            details = _load_details({"candidates": [dict(symbol="TEST", tradeability_score=value)]})
            self.assertEqual(details["TEST"]["execution_quality_score"], "—")

    def test_new_publication_json_csv_and_ranks_use_tradeability_before_limit(self):
        scanner = backend()
        rows = [dict(symbol=f"T{i}", tradeability_score=i, explosion_score=100-i,
                     setup_grade="B" if i < 2 else "REJECT", passed_base_filters=i < 2,
                     scanner_action="WATCH" if i < 2 else "NO TRADE", action_data_integrity_ok=True)
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
            self.assertEqual([r["symbol"] for r in records], ["T1", "T0", "T4"])
            self.assertEqual([r["rank"] for r in records], [1, 2, 3])
            self.assertEqual([r["scanner_action"] for r in records], ["WATCH", "WATCH", "NO TRADE"])
            self.assertEqual([r["tradability_score"] for r in records], [1, 0, 0])
            self.assertEqual([r["execution_quality_score"] for r in records], [1, 0, 4])
            self.assertEqual([r["tradeability_score"] for r in records], [1, 0, 4])
            self.assertEqual([r["tradability_status"] for r in records], ["WATCH", "WATCH", "REJECT"])
            with next(Path(tmp).glob("scan_*.csv")).open() as stream:
                exported = list(csv.DictReader(stream))
            self.assertEqual([r["symbol"] for r in exported], ["T1", "T0", "T4"])
        self.assertEqual(rows, before)


class EligibilityTests(unittest.TestCase):
    def candidate(self, **changes):
        row = dict(symbol="TEST", setup_grade="B", scanner_action="WATCH",
                   scanner_action_tier="watch", action_data_integrity_ok=True,
                   passed_base_filters=True, tradeability_score=100)
        return dict(row, **changes)

    def test_every_review_action_beats_rejected_100_even_at_zero(self):
        from scanner_ranking import REVIEW_ACTIONS
        rejected = self.candidate(symbol="REJECT", setup_grade="REJECT", explosion_score=100)
        for action in REVIEW_ACTIONS:
            with self.subTest(action=action):
                eligible = self.candidate(symbol=action, scanner_action=action, tradeability_score=0)
                self.assertIs(rank_candidates([rejected, eligible])[0], eligible)
                self.assertEqual(tradability_value(eligible), 0)
                self.assertEqual(tradability_status(eligible), action)

    def test_conflicting_flags_cannot_restore_eligibility(self):
        for fields, status in [
            (dict(setup_grade=" reject "), "REJECT"),
            (dict(scanner_action="no trade"), "NO TRADE"),
            (dict(scanner_action="EXPLOSIVE / NO TRADE"), "NO TRADE"),
            (dict(scanner_action_tier="avoid"), "NO TRADE"),
            (dict(scanner_action_tier="BLOCKED"), "DATA CHECK"),
            (dict(action_data_integrity_ok=False), "DATA CHECK"),
            (dict(radar_only=True), "DATA CHECK"),
        ]:
            with self.subTest(fields=fields):
                row = self.candidate(**fields, tradability_score=100, tradability_status="WATCH")
                self.assertEqual(tradability_status(row), status)
                self.assertEqual(tradability_value(row), 0)
                self.assertEqual(execution_quality_value(row), 100)
                self.assertFalse(tradeability_rank_key(row)[0])

    def test_existing_caution_near_miss_remains_a_review_candidate(self):
        row = self.candidate(setup_grade="C", scanner_action="CAUTION",
                             passed_base_filters=False, failed_count=1,
                             critical_fail_count=0, market_session="regular")
        self.assertEqual(backend().scanner_action_signal(row)["label"], "CAUTION")
        reject = self.candidate(symbol="REJECT", setup_grade="REJECT")
        self.assertIs(rank_candidates([reject, row])[0], row)
        self.assertEqual(tradability_value(row), 100)

    def test_missing_or_unrecognized_eligibility_fails_closed(self):
        for fields in (dict(setup_grade=None), dict(scanner_action=None),
                       dict(scanner_action="BUY SOMETHING"), dict(action_data_integrity_ok=None),
                       dict(action_data_integrity_ok="true")):
            with self.subTest(fields=fields):
                row = self.candidate(**fields)
                self.assertEqual(tradability_status(row), "UNKNOWN")
                self.assertIsNone(tradability_value(row))
                self.assertFalse(tradeability_rank_key(row)[0])

    def test_invalid_execution_does_not_borrow_other_scores(self):
        for value in (None, True, "bad", float("nan"), float("inf"), -1, 101, 10**1000):
            row = self.candidate(execution_quality_score=value, explosion_score=100, score=100)
            self.assertIsNone(execution_quality_value(row))
            self.assertIsNone(tradability_value(row))
        self.assertEqual(execution_quality_value(self.candidate(execution_quality_score=25)), 25)
        self.assertEqual(execution_quality_value(self.candidate()), 100)

    def test_ui_freshness_uses_existing_policy_without_rewriting_publication(self):
        from live_price_quality import price_view
        row = self.candidate(price=10, live_price_available=True,
                             live_price_source="tradier_consolidated_trade",
                             live_price_timestamp="2026-09-11T16:00:00+00:00")
        before = copy.deepcopy(row)
        with patch("live_price_quality.price_view", return_value={"current": False}) as view:
            self.assertEqual(tradability_value(row, live=True), 0)
            self.assertEqual(tradability_status(row, live=True), "DATA CHECK")
            self.assertFalse(tradeability_rank_key(row, live=True)[0])
            view.assert_called_with(row)
        self.assertEqual(tradability_value(row), 100)
        self.assertEqual(row, before)

    def test_real_stale_or_unknown_price_cannot_show_high_ui_tradability(self):
        row = self.candidate(price=10, live_price_available=True,
                             live_price_source="tradier_consolidated_trade",
                             live_price_timestamp="2000-01-01T00:00:00+00:00")
        self.assertEqual(tradability_value(row, live=True), 0)
        self.assertEqual(tradability_status(row, live=True), "DATA CHECK")
        row.pop("live_price_timestamp")
        self.assertEqual(tradability_value(row, live=True), 0)

    def test_compact_projection_preserves_unknown_and_blocked_eligibility(self):
        rows = [self.candidate(symbol="UNKNOWN", action_data_integrity_ok=None),
                self.candidate(symbol="REJECT", setup_grade="REJECT"),
                self.candidate(symbol="BLOCKED", scanner_action="NO TRADE")]
        before = copy.deepcopy(rows)
        projection = rendering_regression_check.RenderingTests().candidate_projection(rows)
        by_symbol = {r["symbol"]: r for r in projection}
        for row in rows:
            self.assertEqual(tradability_status(by_symbol[row["symbol"]], live=True),
                             tradability_status(row, live=True))
        self.assertEqual(rows, before)

    def test_predecision_enrichment_stays_execution_ordered(self):
        import ast
        tree = ast.parse((ROOT/"stock_scanner.py").read_text())
        keys = [kw.value.id for n in ast.walk(tree) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == "sort"
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "rows"
                for kw in n.keywords if kw.arg == "key" and isinstance(kw.value, ast.Name)]
        self.assertEqual(keys, ["execution_quality_rank_key"]*3 + ["ranking_key"])
        rows = [self.candidate(tradeability_score=10), self.candidate(setup_grade="REJECT")]
        self.assertIs(sorted(rows, key=execution_quality_rank_key, reverse=True)[0], rows[1])

    def test_execution_formula_and_explosion_are_independent_of_eligibility(self):
        scanner = backend()
        base = dict(price=10, live_price_available=True, live_price_age_seconds=1,
                    spread_pct=.2, liquidity_dollar_volume=50_000_000, volume_pace=5,
                    explosion_score=88)
        a = dict(base, setup_grade="A", scanner_action="WATCH")
        b = dict(base, setup_grade="REJECT", scanner_action="NO TRADE")
        self.assertEqual(scanner.refresh_tradeability_score(a), scanner.refresh_tradeability_score(b))
        self.assertEqual(a["tradeability_score"], 100)
        self.assertEqual(b["explosion_score"], 88)
        self.assertEqual(tradability_value(b), 0)


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
        # Low execution but eligible reviews must survive every display cap.
        for i, action in enumerate(("WATCH", "CAUTION", "BREAKOUT WATCH", "WATCH")):
            rows[i].update(setup_grade="C" if i == 1 else "B", passed_base_filters=True,
                           scanner_action=action, scanner_action_tier="watch",
                           action_data_integrity_ok=True, live_price_available=True,
                           live_price_source="tradier_consolidated_trade",
                           live_price_timestamp="2026-09-11T16:00:00+00:00")
        rows[1]["passed_base_filters"] = False  # Valid C/CAUTION near miss.
        rows[-1]["tradeability_score"] = 100
        if missing:
            rows[3]["tradeability_score"] = None
            rows[2]["tradeability_score"] = "invalid"
        # Rejected T34 retains Execution Quality 100, with missing Explosion data.
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

    def assert_detailed_ranking(self, at, first="T3"):
        table = next(df.value for df in at.dataframe if "Tradability" in df.value.columns and "#" in df.value.columns)
        self.assertEqual(list(table.columns[:8]), ["#", "Ticker", "Tradability", "Tradability Status", "Execution Quality", "Action", "Grade", "Status"])
        self.assertGreater(list(table.columns).index("Explosion"), list(table.columns).index("Action"))
        self.assertEqual(len(table), 30)
        self.assertEqual(table.iloc[0]["Ticker"], first)
        self.assertEqual(set(table.head(4)["Ticker"]), {"T0", "T1", "T2", "T3"})
        rejected = table[table["Grade"] == "REJECT"]
        self.assertTrue((rejected["Tradability"] == 0).all())
        self.assertEqual(rejected.iloc[0]["Execution Quality"], 100)
        cards = [m.value for m in at.markdown if '<div class="card ' in m.value]
        self.assertEqual(len(cards), 10)  # Six top candidates plus four eligible reviews.
        self.assertIn(f'<div class="ticker">{first}</div>', cards[0])
        self.assertTrue(all("<small>TRADABILITY / 100</small>" in c for c in cards))
        self.assertTrue(all("EXPLOSION (SUPPORTING)" in c for c in cards))
        self.assertTrue(all("EXECUTION QUALITY / 100" in c for c in cards))
        self.assertIn(">0<small>TRADABILITY / 100</small><small>REJECT</small>", cards[4])
        self.assertFalse(any("No candidate currently combines clean data integrity" in w.value for w in at.warning))

    def test_combined_compact_and_detailed_views_sort_saved_snapshot_before_caps(self):
        at = self.app()
        rows = [m.value for m in at.markdown if 'data-symbol="' in m.value]
        self.assertEqual(len(rows), 15)
        self.assertIn('data-symbol="T3"', rows[0])
        self.assertIn('data-symbol="T24"', rows[-1])
        self.assertTrue(all(r.index(">Tradability<") < r.index(">ACTION<") < r.index(">Explosion<") for r in rows))
        self.assert_detailed_ranking(at)
        at.button(key="combined_analyze_0_T3").click().run()
        self.assertFalse(at.exception, [e.message for e in at.exception])
        self.assertEqual(at.session_state["app_view"], "Stock Analyzer")
        self.assertTrue(any("Analyzing T3 in the background" in i.value for i in at.info))

    def test_standalone_scanner_uses_same_order_and_card_hierarchy(self):
        self.assert_detailed_ranking(self.app(standalone=True))

    def test_invalid_saved_scores_do_not_crash_or_replace_with_explosion(self):
        at = self.app(missing=True)
        self.assert_detailed_ranking(at, first="T1")

    def test_cross_horizon_view_uses_same_order_with_original_publication_identity(self):
        source = (ROOT/"tests/rendering_fixture.py").read_text()
        source = source.replace("ROOT=Path(__file__).resolve().parents[1]", f"ROOT=Path({str(ROOT)!r})")
        source = source.replace("return json.dumps(snapshot())", """payload=snapshot()
        payload['scan_id']='synthetic-render-publication'
        cross=dict(symbol='CROSS', tradeability_score=10, explosion_score=99,
                   timeframe_best_fit='SWING', timeframe_fit_horizons=['SWING'],
                   timeframe_intraday_score=62, momentum_5m=.3, price=10,
                   scanner_action='WAIT PULLBACK', setup_grade='B', passed_base_filters=True,
                   action_data_integrity_ok=True, live_price_available=True,
                   live_price_source='tradier_consolidated_trade',
                   live_price_timestamp='2026-09-11T16:00:00+00:00')
        payload['candidates']=[cross,dict(cross,symbol='HIGH',tradeability_score=95,explosion_score=0)]
        return json.dumps(payload)""")
        source = source.replace("def start(symbol,*args,**kwargs):", "def start(symbol,*args,**kwargs):\n    st.session_state['fixture_launch_source']=kwargs.get('source_publication')")
        at = AppTest.from_string(source, default_timeout=25)
        at.session_state["scanner_trade_horizon"] = "INTRADAY"
        at.run()
        self.assertFalse(at.exception, [e.message for e in at.exception])
        cross = [m.value for m in at.markdown if 'class="cross-horizon-mover"' in m.value]
        self.assertEqual(len(cross), 2)
        self.assertIn("HIGH", cross[0])
        self.assertIn("CROSS", cross[1])
        at.button(key="cross_horizon_analyze_HIGH").click().run()
        self.assertFalse(at.exception, [e.message for e in at.exception])
        self.assertEqual(at.session_state["fixture_launch_source"]["symbol"], "HIGH")
        self.assertEqual(at.session_state["fixture_launch_source"]["scan_id"], "synthetic-render-publication")


if __name__ == "__main__":
    unittest.main()
