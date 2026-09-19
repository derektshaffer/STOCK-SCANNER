"""Deterministic exchange-clock checks: no network, credentials, or orders."""
import ast
from datetime import datetime, timezone
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo
from market_session import market_session_phase, session_bounds, workspace_market_status

ET=ZoneInfo('America/New_York')
PT=ZoneInfo('America/Los_Angeles')

def stamp(day, clock, zone=ET):
    return datetime.fromisoformat(day+'T'+clock).replace(tzinfo=zone)

def source_function(file, name):
    tree=ast.parse(Path(file).read_text())
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
    fn.decorator_list=[]
    ns={}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),file,'exec'),ns)
    return ns[name]

class SessionChecks(unittest.TestCase):
    def test_reported_friday_pacific(self):
        now=stamp('2026-09-18','14:14',PT)
        self.assertEqual(market_session_phase(now),'afterhours')
        self.assertEqual(workspace_market_status(now)[1:],(False,'AFTER-HOURS'))
        self.assertEqual(source_function('app.py','_workspace_market_status')(now)[1:],(False,'AFTER-HOURS'))

    def test_all_regular_boundaries(self):
        for clock,phase in [('03:59:59','closed'),('04:00','premarket'),('09:29:59','premarket'),('09:30','regular'),('15:59:59','regular'),('16:00','afterhours'),('19:59:59','afterhours'),('20:00','closed')]:
            with self.subTest(clock=clock): self.assertEqual(market_session_phase(stamp('2026-09-18',clock)),phase)

    def test_holidays_and_weekends(self):
        for day in ['2026-01-01','2026-01-19','2026-02-16','2026-04-03','2026-05-25','2026-06-19','2026-07-03','2026-09-07','2026-11-26','2026-12-25','2026-09-19','2026-09-20','2025-01-09']:
            for clock in ['05:00','12:00','17:00']:
                with self.subTest(day=day,clock=clock): self.assertEqual(market_session_phase(stamp(day,clock)),'closed')

    def test_bank_holidays_are_equity_sessions(self):
        for day in ['2026-10-12','2026-11-11','2027-12-31']:
            self.assertEqual(market_session_phase(stamp(day,'12:00')),'regular')

    def test_early_close_boundaries(self):
        for day in ['2026-11-27','2026-12-24','2028-07-03']:
            for clock,phase in [('12:59:59','regular'),('13:00','afterhours'),('16:59:59','afterhours'),('17:00','closed')]:
                with self.subTest(day=day,clock=clock): self.assertEqual(market_session_phase(stamp(day,clock)),phase)
            self.assertEqual(session_bounds('afterhours',stamp(day,'14:00')),(780,1020))

    def test_dst_and_input_timezone(self):
        for day,utc in [('2026-03-06','14:30'),('2026-03-09','13:30'),('2026-10-30','13:30'),('2026-11-02','14:30')]:
            self.assertEqual(market_session_phase(stamp(day,utc,timezone.utc)),'regular')
            self.assertEqual(market_session_phase(stamp(day,'06:30',PT)),'regular')
            self.assertEqual(market_session_phase(stamp(day,'13:00',PT)),'afterhours')
        self.assertEqual(market_session_phase(datetime(2026,9,18,17,14)),'afterhours')

    def test_all_entrypoints_share_clock(self):
        funcs=[source_function(f,n) for f,n in [('stock_scanner.py','market_session_mode'),('stock_analyzer.py','market_session_phase'),('scanner_app.py','market_session_phase')]]
        for now in [stamp('2026-09-18','14:14',PT),stamp('2026-11-27','13:00'),stamp('2026-07-03','11:00')]:
            for fn in funcs:self.assertEqual(fn(now),market_session_phase(now))

    def test_analyzer_session_bars_honor_early_close_and_holiday(self):
        import stock_analyzer as sa
        rows=[{'t':stamp('2026-11-27',c).isoformat(),'c':10} for c in ['12:59','13:00','13:30']]
        self.assertEqual(sa._filter_session_bars(rows,stamp('2026-11-27','14:00')),rows[1:])
        self.assertEqual(sa._filter_session_bars(rows,stamp('2026-11-27','18:00')),rows[:1])
        self.assertFalse(sa._regular_session_bar({'t':stamp('2026-07-03','11:00').isoformat()}))

    def test_actual_header_rendering_at_session_boundaries(self):
        from streamlit.testing.v1 import AppTest
        tree=ast.parse(Path('app.py').read_text())
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_render_workspace_market_status')
        source="""import streamlit as st
from datetime import datetime
from zoneinfo import ZoneInfo
from market_session import workspace_market_status
st.session_state.setdefault('app_view','Stock Analyzer')
st.session_state.setdefault('clock','2026-09-18T12:59:59-07:00')
def _workspace_market_status():
    return workspace_market_status(datetime.fromisoformat(st.session_state['clock']))
"""+ast.unparse(node)+"\n_render_workspace_market_status()\n"
        at=AppTest.from_string(source,default_timeout=10)
        for clock,label in [('2026-09-18T12:59:59-07:00','MARKET OPEN'),('2026-09-18T13:00:00-07:00','AFTER-HOURS'),('2026-09-18T14:14:00-07:00','AFTER-HOURS'),('2026-09-19T10:00:00-07:00','MARKET CLOSED'),('2026-11-27T10:00:00-08:00','AFTER-HOURS')]:
            at.session_state['clock']=clock
            at.run()
            self.assertFalse(at.exception)
            html=' '.join(item.value for item in at.markdown)
            self.assertIn(label,html)
            if label!='MARKET OPEN': self.assertNotIn('MARKET OPEN',html)

    def test_badge_refreshes_independently(self):
        tree=ast.parse(Path('app.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_render_workspace_market_status')
        self.assertIn('run_every=30',ast.unparse(fn.decorator_list[0]))
        self.assertIn('live_text = workspace_session',ast.unparse(fn))

if __name__=='__main__':unittest.main()
