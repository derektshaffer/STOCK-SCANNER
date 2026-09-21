"""Offline Today % regressions: no provider calls, credentials, or recording."""
import ast
import copy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault('TRADIER_ACCESS_TOKEN', 'offline-test-only')
os.environ['SCANNER_PASSIVE_EVIDENCE_ENABLED'] = '0'
import live_price_quality as quality
import scanner_discovery as discovery
import stock_scanner as scanner

NOW = datetime(2026, 9, 21, 13, 28, tzinfo=timezone.utc)  # 9:28 AM ET
ROOT = Path(__file__).resolve().parent


def quote(**changes):
    stamp = (NOW - timedelta(seconds=2)).isoformat()
    return dict(dict(symbol='TEST', type='stock', last=12.5, prevclose=10,
                     bid=12.49, ask=12.51, trade_date=stamp, bid_date=stamp, ask_date=stamp,
                     high=13, low=10, volume=100000, average_volume=20000,
                     change_percentage=0), **changes)


def record(**changes):
    return dict(dict(symbol='TEST', price=12.5, prev_close=10, day_pct=0,
                     live_price_source='tradier_consolidated_trade', live_price_available=True,
                     live_price_timestamp=(NOW-timedelta(seconds=2)).isoformat()), **changes)


class PercentageTests(unittest.TestCase):
    def test_missing_and_invalid_price_never_become_zero(self):
        for value in (None, '', 'bad', 0, -1, True, float('nan'), float('inf')):
            with self.subTest(value=value):
                self.assertIsNone(quality.today_change_pct(record(price=value), now=NOW))

    def test_last_known_price_is_not_a_current_percentage(self):
        self.assertIsNone(quality.today_change_pct(record(price=None, last_known_price=10), now=NOW))

    def test_missing_and_invalid_previous_close_never_become_zero(self):
        for value in (None, '', 'bad', 0, -1, True, float('nan'), float('inf')):
            with self.subTest(value=value):
                self.assertIsNone(quality.today_change_pct(record(prev_close=value), now=NOW))

    def test_stale_unavailable_unknown_and_future_quotes(self):
        for changes in (dict(live_price_available=False), dict(live_price_source='unknown'),
                        dict(live_price_timestamp=None),
                        dict(live_price_timestamp=(NOW-timedelta(seconds=121)).isoformat()),
                        dict(live_price_timestamp=(NOW+timedelta(seconds=60)).isoformat())):
            with self.subTest(changes=changes):
                self.assertIsNone(quality.today_change_pct(record(**changes), now=NOW))

    def test_valid_premarket_percentage_ignores_provider_cached_zero(self):
        row = discovery._radar_row('TEST', quote(), [], NOW.timestamp())
        self.assertEqual(row['discovery_change_pct'], 25)
        candidate = scanner.analyze_snapshot('TEST', quote(), {}, NOW)
        self.assertEqual(candidate['day_pct'], 25)
        self.assertEqual(quality.today_change_pct(candidate, now=NOW), 25)

    def test_fresh_midpoint_restores_premarket_with_stale_last(self):
        q = quote(last=10, trade_date=(NOW-timedelta(days=3)).isoformat())
        row = discovery._radar_row('TEST', q, [], NOW.timestamp())
        self.assertEqual(row['radar_price_kind'], 'quote_midpoint')
        self.assertEqual(row['discovery_change_pct'], 25)
        candidate = scanner.radar_only_candidate(row, q, NOW)
        self.assertEqual(quality.price_view(candidate, now=NOW)['state'], 'FALLBACK')
        self.assertEqual(quality.today_change_pct(candidate, now=NOW), 25)

    def test_missing_last_uses_valid_two_sided_quote(self):
        candidate = scanner.analyze_snapshot('TEST', quote(last=None), {}, NOW)
        self.assertEqual(candidate['price'], 12.5)
        self.assertEqual(candidate['day_pct'], 25)

    def test_previous_close_and_close_are_never_current_price_fallbacks(self):
        q = quote(last=None, bid=None, ask=None, close=10)
        self.assertIsNone(discovery._radar_row('TEST', q, [], NOW.timestamp()))
        self.assertIsNone(scanner.analyze_snapshot('TEST', q, {}, NOW))
        self.assertIsNone(discovery._quote_price(q))

    def test_radar_missing_reference_retains_detection_but_no_day_move(self):
        for value in (None, 0, -1, 'bad', float('nan'), float('inf')):
            q = quote(prevclose=value)
            row = discovery._radar_row('TEST', q, [], NOW.timestamp())
            self.assertIsNotNone(row)
            self.assertIsNone(row['discovery_prev_close'])
            self.assertIsNone(row['discovery_change_pct'])
            candidate = scanner.radar_only_candidate(row, q, NOW)
            self.assertIsNone(candidate['day_pct'])
            self.assertIsNone(quality.today_change_pct(candidate, now=NOW))

    def test_valid_alpaca_reference_can_support_tradier_price(self):
        candidate = scanner.analyze_snapshot('TEST', quote(prevclose=-1),
                                             {'prevDailyBar': {'c': 10}}, NOW)
        self.assertEqual(candidate['prev_close'], 10)
        self.assertEqual(candidate['day_pct'], 25)

    def test_real_zero_and_negative_remain_numeric(self):
        self.assertEqual(quality.today_change_pct(record(price=10), now=NOW), 0)
        self.assertAlmostEqual(quality.today_change_pct(record(price=9), now=NOW), -10)

    def test_radar_window_does_not_relax_live_window(self):
        stamp = (NOW-timedelta(seconds=150)).isoformat()
        q = quote(trade_date=stamp, bid_date=stamp, ask_date=stamp)
        row = discovery._radar_row('TEST', q, [], NOW.timestamp())
        self.assertTrue(row['radar_quote_fresh'])
        candidate = scanner.radar_only_candidate(row, q, NOW)
        self.assertFalse(candidate['live_price_available'])
        self.assertIsNone(candidate['day_pct'])
        self.assertIsNone(quality.today_change_pct(candidate, now=NOW))

    def test_stale_quote_and_mismatched_symbol_rejected(self):
        stamp = (NOW-timedelta(seconds=181)).isoformat()
        for q in (quote(trade_date=stamp, bid_date=stamp, ask_date=stamp), quote(symbol='OTHER')):
            self.assertIsNone(discovery._radar_row('TEST', q, [], NOW.timestamp()))

    def test_quote_sides_revalidated_in_display(self):
        row = record(live_price_source='tradier_consolidated_quote_midpoint',
                     side_timestamps=[NOW.isoformat(), (NOW-timedelta(seconds=121)).isoformat()])
        self.assertIsNone(quality.today_change_pct(row, now=NOW))

    def test_publication_and_ui_projection_preserve_inputs(self):
        row = discovery._radar_row('TEST', quote(), [], NOW.timestamp())
        candidate = scanner.radar_only_candidate(row, quote(), NOW)
        published = scanner.candidate_log_record(candidate, 1)
        before = copy.deepcopy(published)
        node = next(n for n in ast.parse((ROOT/'app.py').read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == '_latest_scan_candidates')
        scope = {}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'app.py', 'exec'), scope)
        projected = scope['_latest_scan_candidates']({'candidates': [published]})[0]
        self.assertEqual(quality.today_change_pct(projected, now=NOW), 25)
        self.assertEqual(published, before)

    def test_zero_sort_is_distinct_from_missing_move(self):
        rows = [dict(symbol=s, radar_quote_fresh=True, discovery_change_pct=d,
                     discovery_relative_volume=2, explosion_score=30, tradeability_score=50)
                for s,d in [('MISSING',None),('DOWN',-2),('ZERO',0)]]
        self.assertEqual([r['symbol'] for r in discovery._select_candidates(rows,20)],
                         ['ZERO','DOWN','MISSING'])

    def test_enrichment_recomputes_without_zero_filling_or_old_quote_sides(self):
        stats = dict(last_price=12.5, last_price_source='tradier_consolidated_timesales_bar',
                     last_price_timestamp=NOW.isoformat(), last_price_age_seconds=0,
                     volume=100000, dollar_volume=1250000, high=13, low=10, vwap=12)
        for previous, expected in [(None,None),(0,None),(-1,None),(10,25)]:
            candidate = record(prev_close=previous, day_pct=0,
                               side_timestamps=[(NOW-timedelta(days=3)).isoformat()]*2)
            with patch.object(scanner, 'current_session_live_metrics', return_value=stats), \
                 patch.object(scanner, 'daily_history_context', return_value={}), \
                 patch.object(scanner, 'historical_volume_profile', return_value=None), \
                 patch.object(scanner, 'market_session_mode', return_value='premarket'):
                result = scanner.enrich_live(candidate, NOW, NOW.astimezone(discovery.ET))
            self.assertEqual(result['day_pct'], expected)
            self.assertEqual(quality.today_change_pct(result, now=NOW), expected)

    def test_full_coverage_does_not_imply_usable_price(self):
        import tempfile
        stale = (NOW-timedelta(days=3)).isoformat()
        q = quote(last=None, bid=None, ask=None, close=10,
                  trade_date=stale, bid_date=stale, ask_date=stale)
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(discovery, 'get_or_build_discovery_universe', return_value=(['TEST'], {})), \
             patch.object(discovery, '_quote_rows', return_value=({'TEST':q}, {'failed_batches':0})), \
             patch.object(discovery, '_load_state', return_value=({},False)), \
             patch.object(discovery, '_save_state'), \
             patch.object(discovery.time, 'time', return_value=NOW.timestamp()):
            discovery.discover_tradier_candidates('offline-test-only', lambda _: True, top=20)
        meta = discovery.get_last_discovery_meta()
        self.assertEqual(meta['coverage_pct'], 100)
        self.assertEqual(meta['fresh_quotes'], 0)
        self.assertEqual(meta['eligible_stocks'], 0)


class PercentageRenderingTests(unittest.TestCase):
    def tearDown(self):
        import rendering_regression_check as rendering
        rendering.RenderingTests().tearDown()

    def test_today_cards_render_valid_zero_move_and_unavailable_separately(self):
        from streamlit.testing.v1 import AppTest
        source = (ROOT/'tests/rendering_fixture.py').read_text()
        source = source.replace('ROOT=Path(__file__).resolve().parents[1]', f'ROOT=Path({str(ROOT)!r})')
        source = source.replace('return json.dumps(snapshot())', '''
        payload = snapshot()
        for row in payload['candidates']:
            row.update(prev_close=10, day_pct=0, live_price_available=True,
                       live_price_source='tradier_consolidated_trade',
                       live_price_timestamp=datetime.now(timezone.utc).isoformat())
            if row['symbol']=='PDSB': row['price']=12.5
            elif row['symbol']=='GCDT': row['price']=10
            elif row['symbol']=='BNC': row['price']=None
            elif row['symbol']=='ARBE': row['prev_close']=None
            elif row['symbol']=='WCT': row['live_price_timestamp']=(datetime.now(timezone.utc)-timedelta(seconds=121)).isoformat()
            elif row['symbol']=='ROIV': row['live_price_available']=False
        return json.dumps(payload)''')
        at = AppTest.from_string(source, default_timeout=20).run()
        self.assertFalse(at.exception, [e.message for e in at.exception])
        cards = [m.value for m in at.markdown if 'data-symbol="' in m.value]
        def card(symbol):
            return next(c for c in cards if f'data-symbol="{symbol}"' in c)
        self.assertIn('+25.0%', card('PDSB'))
        self.assertIn('+0.0%', card('GCDT'))
        for symbol in ('BNC','ARBE','WCT','ROIV'):
            self.assertIn('N/A', card(symbol))
            self.assertNotIn('+0.0%', card(symbol))
        self.assertIn('UNAVAILABLE PRICE', card('BNC'))
        self.assertIn('STALE PRICE', card('WCT'))
        at.session_state['_scanner_price_failure'] = {'provider':'tradier','state':'UNAVAILABLE'}
        at.run()
        self.assertFalse(at.exception, [e.message for e in at.exception])
        failed_cards = [m.value for m in at.markdown if 'data-symbol="' in m.value]
        self.assertTrue(all('N/A' in c and '+0.0%' not in c and '+25.0%' not in c for c in failed_cards))

    def test_expanded_details_do_not_reuse_cached_zero(self):
        import scanner_expand
        with patch.object(quality, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = NOW
            details = scanner_expand._load_details({'candidates': [record(price=None)]})
            self.assertEqual(details['TEST']['day'], 'N/A')
            self.assertFalse(details['TEST']['day_positive'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
