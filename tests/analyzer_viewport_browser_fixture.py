"""Offline real-Plotly browser checks. Generated data; no providers or models."""
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import streamlit as st
from analyzer_overview import inject_overview_theme, price_figure
from analyzer_visuals import trade_plan_plotly_figure

st.set_page_config(layout='wide')
st.title('Offline chart regression fixture')
st.caption('Generated candles only. No quotes, scans, orders or training.')
append=st.checkbox('Append fixture candle')
st.button('Unrelated rerun')
start=datetime(2026,9,11,13,30,tzinfo=timezone.utc)
timeframe=st.segmented_control('Timeframe', ['1m','5m','15m','1h','D'],default='5m',required=True)
window=st.segmented_control('Range',['1D','5D','1M','All'],default='1D',required=True)
bars=[]
for i in range(60+int(append)):
    c=10+i*.1
    bars.append(dict(t=(start+timedelta(minutes=i*(1 if timeframe=='1m' else 5))).isoformat(),o=c-.03,h=c+.08,l=c-.08,c=c,v=1000+i))
result=dict(symbol='FIXTURE',as_of='2026-09-11T20:00:00Z',chart_data=dict(intraday=bars,intraday_interval='1m',daily=[dict(b,t=b['t'][:10]) for b in bars]),vwap=12,
            trade_plan=dict(selected=dict(stop=1,target1=30,target2=40)))
st.html("<style>"+Path(__file__).resolve().parents[1].joinpath("analyzer_overview.css").read_text()+"</style>")
with st.container(key='ao_chart'):
    st.plotly_chart(price_figure(result,timeframe,window),key='fixture_overview',config=dict(scrollZoom=True,displayModeBar=False))
st.plotly_chart(trade_plan_plotly_figure(result),key='fixture_detail',config=dict(scrollZoom=True))
st.html('<section id="viewport-test-panel"><button id="viewport-test-run">Run browser viewport regressions</button><pre id="viewport-test-results"></pre><button id="terminal-test-run">Run terminal interaction regressions</button><pre id="terminal-test-results"></pre></section>')
inject_overview_theme(st)
scripts="\n".join(Path(__file__).resolve().parents[1].joinpath(n).read_text() for n in
                  ('tests/analyzer_viewport_browser_checks.js','tests/analyzer_terminal_browser_checks.js'))
st.iframe('<script>(function(window,document,Plotly){'+scripts+'})(parent,parent.document,parent.Plotly);</script>',height=1,tab_index=-1)
