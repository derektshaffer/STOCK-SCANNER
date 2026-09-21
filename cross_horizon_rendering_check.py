"""Real combined-app rendering with synthetic providers and publication rows."""
from pathlib import Path
import unittest
from streamlit.testing.v1 import AppTest
import rendering_regression_check as legacy_rendering

ROOT=Path(__file__).resolve().parent


def fixture_source():
    source=(ROOT/'tests/rendering_fixture.py').read_text()
    source=source.replace('ROOT=Path(__file__).resolve().parents[1]', f'ROOT=Path({str(ROOT)!r})')
    source=source.replace('return json.dumps(snapshot())', '''
        payload=snapshot()
        payload['scan_id']='synthetic-render-publication'
        payload['candidates']=[dict(symbol='NORMAL',score=62,timeframe_best_fit='INTRADAY',timeframe_fit_horizons=['INTRADAY'],setup_grade='B',scanner_action='WAIT PULLBACK',timeframe_intraday_score=80,momentum_5m=1,price=10),
            dict(symbol='CROSS',score=62,timeframe_best_fit='SWING',timeframe_fit_horizons=['SWING'],setup_grade='B',scanner_action='WAIT PULLBACK',timeframe_intraday_score=62,momentum_5m=.3,price=10)]
        return json.dumps(payload)''')
    source=source.replace("def start(symbol,*args,**kwargs):", "def start(symbol,*args,**kwargs):\n    st.session_state['fixture_launch_source']=kwargs.get('source_publication')")
    return source


class CrossHorizonRenderingTests(unittest.TestCase):
    tearDown=legacy_rendering.RenderingTests.tearDown
    def app(self,stale=False):
        app=AppTest.from_string(fixture_source(),default_timeout=20)
        app.session_state['scanner_trade_horizon']='INTRADAY'
        app.session_state['fixture_stale']=stale
        app.run();self.assertFalse(app.exception,[e.message for e in app.exception])
        return app
    def test_separate_section_analyze_and_original_list(self):
        app=self.app()
        self.assertTrue(any(h.value=='Cross-Horizon Movers' for h in app.subheader))
        cards=[m.value for m in app.markdown if 'data-symbol="' in m.value]
        self.assertEqual(len(cards),1);self.assertIn('NORMAL',cards[0])
        cross=[m.value for m in app.markdown if 'class="cross-horizon-mover"' in m.value]
        self.assertEqual(len(cross),1);self.assertIn('CROSS',cross[0]);self.assertIn('DATA CHECK',cross[0])
        app.button(key='cross_horizon_analyze_CROSS').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['app_view'],'Stock Analyzer')
        source=app.session_state['fixture_launch_source']
        self.assertEqual(source['scan_id'],'synthetic-render-publication')
        self.assertEqual(source['symbol'],'CROSS')
        self.assertEqual(len(source['payload_sha256']),64)
    def test_normal_surface_handoff(self):
        app=self.app()
        button=next(b for b in app.button if b.label=='Analyze NORMAL')
        button.click().run(); self.assertFalse(app.exception)
        self.assertEqual(app.session_state['fixture_launch_source']['symbol'],'NORMAL')
        self.assertEqual(app.session_state['fixture_launch_source']['scan_id'],'synthetic-render-publication')

    def test_standalone_entrypoint_cross_horizon_renders(self):
        source=fixture_source().replace("ROOT/'analyzer_app.py'", "ROOT/'scanner_app.py'")
        app=AppTest.from_string(source,default_timeout=20)
        app.session_state['scanner_trade_horizon']='INTRADAY'
        app.run();self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertTrue(any(h.value=='Cross-Horizon Movers' for h in app.subheader))
        self.assertTrue(any('CROSS' in m.value for m in app.markdown))

    def test_stale_snapshot_disables_new_analyze_button(self):
        self.assertTrue(self.app(stale=True).button(key='cross_horizon_analyze_CROSS').disabled)
    def test_all_focus_does_not_add_duplicate_section(self):
        app=self.app();app.session_state['scanner_trade_horizon']='ALL';app.run()
        self.assertFalse(app.exception)
        self.assertFalse(any(h.value=='Cross-Horizon Movers' for h in app.subheader))


if __name__=='__main__':unittest.main()
