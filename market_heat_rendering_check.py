"""Streamlit rendering smoke with synthetic context; no live application launched."""
import unittest
from streamlit.testing.v1 import AppTest

SCRIPT='''
import streamlit as st
from market_heat import calculate
from market_heat_ui import render_market_heat
from candidate_outcome_study import digest
st.write("WAIT PULLBACK")
choice=st.selectbox("Synthetic context fixture",["UNKNOWN","COLD","NORMAL","HOT","EXTREME"])
s,_=calculate({'batches':[]},scan_id='synthetic-ui',as_of='2026-09-15T14:00:20Z',scan_started_at='2026-09-15T14:00:00Z',phase='regular')
if choice!='UNKNOWN':
    s['regime']=choice;s['value']={'COLD':0,'NORMAL':40,'HOT':60,'EXTREME':90}[choice]
    s.pop('snapshot_id');s['snapshot_id']=digest(s)
render_market_heat(st,s,now='2026-09-15T14:00:30Z')
st.write("NO TRADE")
'''


class RenderingTests(unittest.TestCase):
    def test_all_regimes_render_without_changing_recommendation_text(self):
        app=AppTest.from_string(SCRIPT).run(timeout=20)
        for regime in ('UNKNOWN','COLD','NORMAL','HOT','EXTREME'):
            app.selectbox[0].select(regime).run(timeout=20)
            self.assertEqual(len(app.exception),0)
            text='\n'.join(x.value for x in app.markdown)
            self.assertIn('WAIT PULLBACK',text);self.assertIn('NO TRADE',text)
            self.assertIn('Market regime: '+regime,text)
            self.assertEqual(len(app.expander),1)
            self.assertIn('Context only',app.caption[0].value)

    def test_missing_context_renders_unknown(self):
        app=AppTest.from_string('import streamlit as st\nfrom market_heat_ui import render_market_heat\nrender_market_heat(st,None)').run(timeout=20)
        self.assertEqual(len(app.exception),0);self.assertIn('UNKNOWN',app.markdown[0].value)


if __name__=='__main__':unittest.main(verbosity=2)
