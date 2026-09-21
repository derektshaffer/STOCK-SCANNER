"""Behavioral loading/row/identity regressions. No credentials or network needed."""
import ast
import copy
import math
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

ROOT=Path(__file__).resolve().parent

class RenderingTests(unittest.TestCase):
    def candidate_projection(self, rows):
        node = next(n for n in ast.parse((ROOT/'app.py').read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == '_latest_scan_candidates')
        scope = {'math': math}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'app.py', 'exec'), scope)
        return scope['_latest_scan_candidates']({'candidates': rows})

    def test_tradeability_order_before_display_limit_preserves_publication(self):
        rows = [dict(symbol=f'T{i}', tradeability_score=i, explosion_score=100-i,
                     setup_grade='REJECT', scanner_action='NO TRADE') for i in range(16)]
        before = copy.deepcopy(rows)
        result = self.candidate_projection(rows)
        self.assertEqual([r['symbol'] for r in result], [f'T{i}' for i in range(15, 0, -1)])
        self.assertEqual(rows, before)
        self.assertTrue(all(r['grade']=='REJECT' and r['scanner_action']=='NO TRADE' for r in result))

    def test_tradeability_ties_stable_and_missing_scores_last(self):
        scores = [None, 90, '100', 90, float('nan'), float('inf'), 'bad', False, 0]
        rows = [dict(symbol=f'T{i}', tradeability_score=s) for i,s in enumerate(scores)]
        result = self.candidate_projection(rows)
        self.assertEqual([r['symbol'] for r in result],
                         ['T2','T1','T3','T8','T0','T4','T5','T6','T7'])

    def test_tradeability_row_order_columns_and_reordered_analyzer_click(self):
        source = (ROOT/'tests/rendering_fixture.py').read_text()
        source = source.replace('ROOT=Path(__file__).resolve().parents[1]', f'ROOT=Path({str(ROOT)!r})')
        source = source.replace('return json.dumps(snapshot())', '''
        payload=snapshot()
        for i,row in enumerate(payload['candidates']):
            row['tradeability_score']=100 if row['symbol']=='BNC' else 90-i
        return json.dumps(payload)''')
        at = AppTest.from_string(source, default_timeout=20)
        at.run(); self.clean(at)
        rows = [m.value for m in at.markdown if 'data-symbol="' in m.value]
        self.assertIn('data-symbol="BNC"', rows[0])
        self.assertTrue(all(row.index('>Tradability<') < row.index('>Explosion<') for row in rows))
        self.assertTrue(all(row.index('>Tradability<') < row.index('>ACTION<') < row.index('>Explosion<') for row in rows))
        self.assertTrue(all('DATA CHECK' in row for row in rows))
        self.assertTrue(any('then Tradability' in c.value for c in at.caption))
        at.button(key='combined_analyze_0_BNC').click().run(); self.clean(at)
        self.assertEqual(at.session_state['app_view'], 'Stock Analyzer')
        self.assertTrue(any('Analyzing BNC in the background' in x.value for x in at.info))

    def tearDown(self):
        # The browser fixture freezes provider timestamps for deterministic
        # reruns. Restore these modules before independent non-UI tests.
        import live_price_quality, stock_analyzer, consistency_regression_check
        import datetime as datetime_module
        datetime_module.datetime = datetime
        for module in (live_price_quality, stock_analyzer, consistency_regression_check):
            module.datetime = datetime

    def app(self,mode='running'):
        at=AppTest.from_file(str(ROOT/'tests/rendering_fixture.py'),default_timeout=20)
        at.session_state['fixture_mode']=mode
        at.run()
        self.clean(at)
        return at
    def clean(self,at):
        self.assertFalse(at.exception,[e.message for e in at.exception])
    def analyze(self,at):
        at.button(key='combined_analyze_2_BNC').click().run()
        self.clean(at)
        return at
    def test_rows_contain_notices_and_html_details(self):
        at=self.app()
        rows=[m.value for m in at.markdown if 'data-symbol="' in m.value]
        self.assertEqual(len(rows),11)
        self.assertTrue(all('UNAVAILABLE PRICE' in row for row in rows))
        for row in rows:
            self.assertIn('<details class="combined-candidate-card"',row)
            self.assertIn('class="scanner-inline-detail"',row)
            self.assertEqual(row.count('<summary '),1)
            self.assertEqual(row.count('</summary>'),1)
            if 'UNAVAILABLE PRICE' in row:
                self.assertIn('class="combined-price-notice"',row)
                self.assertLess(row.index('UNAVAILABLE PRICE'),row.index('</summary>'))
        self.assertFalse(any('LIVE PRICE FALLBACK' in c.value for c in at.caption))
    def test_cold_handoff_and_cancel_return(self):
        at=self.analyze(self.app())
        self.assertEqual(at.session_state['app_view'],'Stock Analyzer')
        self.assertTrue(any('Analyzing BNC in the background' in x.value for x in at.info))
        at.button(key='cancel_combined_loader_BNC').click().run()
        self.clean(at)
        self.assertEqual(at.session_state['app_view'],'Momentum Scanner')
        self.assertIsNone(at.session_state['_analyzer_bootstrap_launch_state'])
    def test_terminal_failure_has_retry_and_no_stuck_loader(self):
        at=self.analyze(self.app('failure'))
        self.assertTrue(any('LIVE PRICE UNAVAILABLE' in x.value for x in at.error))
        self.assertTrue(any(b.label=='Retry BNC' for b in at.button))
        self.assertFalse(any('Cancel BNC'==b.label for b in at.button))
        self.assertFalse(at.session_state['_analyzer_loading'])
    def test_start_failure_keeps_analyze_button_usable(self):
        at=self.analyze(self.app('start_failure'))
        self.assertEqual(at.session_state['app_view'],'Momentum Scanner')
        self.assertFalse(at.button(key='combined_analyze_2_BNC').disabled)
    def test_completed_analysis_renders_full_view(self):
        at=self.analyze(self.app('success'))
        self.assertEqual(at.session_state['result']['symbol'],'BNC')
        self.assertFalse(at.session_state['_analyzer_loading'])
        self.assertTrue(any('Analysis as of' in c.value for c in at.caption))
        self.assertGreater(len(at.get('plotly_chart')),0)
    def test_manual_scan_finishes_and_cancel_works(self):
        at=self.app()
        next(b for b in at.button if b.label=='▶ Run Fresh Scan').click().run()
        at.run()
        self.clean(at)
        next(b for b in at.button if b.label=='■ Cancel Scan').click().run()
        self.clean(at)
        self.assertIsNone(at.session_state['_scanner_async_state'])
        next(b for b in at.button if b.label=='▶ Run Fresh Scan').click().run()
        at.session_state['fixture_scan_done']=True
        at.run()
        self.clean(at)
        self.assertIsNone(at.session_state['_scanner_async_state'])
        self.assertTrue(any(b.label=='▶ Run Fresh Scan' for b in at.button))
    def test_completed_worker_replaces_cached_result(self):
        at=self.analyze(self.app('success'))
        at.radio(key='app_view').set_value('Momentum Scanner').run()
        cache=at.session_state['_analyzer_result_cache']
        cache['BNC']['result']['snapshot_tag']='old'
        at.session_state['_analyzer_result_cache']=cache
        self.analyze(at)
        self.assertEqual(at.session_state['result']['snapshot_tag'],'new')
        self.assertIsNone(at.session_state['_analyzer_bootstrap_launch_state'])
    def test_stale_snapshot_disables_analyze(self):
        at=self.app()
        at.session_state['fixture_stale']=True
        at.run()
        self.clean(at)
        self.assertTrue(at.button(key='combined_analyze_2_BNC').disabled)
        self.assertTrue(any('snapshot is stale' in x.value for x in at.warning))
    def test_corrupt_snapshot_renders_empty_state(self):
        at=self.app()
        at.session_state['fixture_corrupt']=True
        at.run()
        self.clean(at)
        self.assertFalse(any(b.label.startswith('Analyze ') for b in at.button))
        self.assertTrue(any('No scanner candidates' in x.value for x in at.caption))
    def test_future_snapshot_disables_analyze(self):
        at=self.app()
        at.session_state['fixture_future']=True
        at.run()
        self.clean(at)
        self.assertTrue(at.button(key='combined_analyze_2_BNC').disabled)
        self.assertTrue(any('snapshot is stale' in x.value for x in at.warning))
    def test_filter_changes_visible_candidates(self):
        at=self.app()
        at.selectbox(key='scanner_trade_horizon').set_value('LONGER-TERM').run()
        self.clean(at)
        labels=[b.label for b in at.button if b.label.startswith('Analyze ')]
        self.assertEqual(labels,['Analyze BNC','Analyze ROIV','Analyze LONGNAME'])
    def test_tradier_switch_never_relabels_other_symbol_data(self):
        import tradier_live_stream as ts
        stream=ts._TradierStream()
        now=datetime.now(timezone.utc).isoformat()
        stream.state.update(symbol='OLD',last_trade={'price':0.32,'timestamp':now},
                            last_quote={'bid':0.31,'ask':0.33,'timestamp':now},
                            session_vwap=0.3,session_volume=1000)
        state=stream.get('NEW')
        self.assertEqual(state['status'],'switching')
        for k in ('last_trade','last_quote','session_vwap','session_volume'):
            self.assertNotIn(k,state)
        with patch.object(ts,'get_live_state',stream.get):
            overlay=ts.get_live_overlay(dict(symbol='NEW',market_provider='tradier',price=5,
                live_price_timestamp=now,live_price_source='tradier_consolidated_quote_midpoint',vwap=4.9))
        self.assertEqual(overlay['price'],5)
        self.assertEqual(overlay['vwap'],4.9)
    def test_cold_directory_never_waits_for_provider(self):
        import equity_directory as ed
        future=Future()
        with patch.dict(ed.os.environ,{'ALPACA_API_KEY':'fixture','ALPACA_SECRET_KEY':'fixture'}),patch.object(ed,'_directory_job',return_value=future):
            choices=ed.equity_choices()
        self.assertIsInstance(choices,list)
        self.assertFalse(future.done())

if __name__=='__main__':
    unittest.main(verbosity=2)
