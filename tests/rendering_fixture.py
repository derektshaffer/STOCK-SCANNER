"""Offline browser fixture: real app entrypoints and rendering, simulated providers."""
import copy,json,os,runpy,sys,time
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import streamlit as st
import scanner_runtime as sr
import analyzer_launch_runtime as ar
import analyzer_bootstrap as ab
import stock_analyzer as sa
import consistency_regression_check as cr
import live_market_stream as lm
import prediction_tracker as pt

class Process:
    def poll(self):return 0 if st.session_state.get('fixture_mode')=='success' else None

# No provider/network/persistence side effects; only fixture state changes.
for key in ('ALPACA_API_KEY','ALPACA_SECRET_KEY','OPENAI_API_KEY','ANALYZER_GITHUB_TOKEN','GITHUB_TOKEN'):
    os.environ.pop(key,None)
os.environ['TRADIER_ACCESS_TOKEN']='offline-test-only'
os.environ['ANALYZER_BACKGROUND_WORKER']='1'
sa.TRADIER_TOKEN='offline-test-only'
sa.USE_TRADIER=True
sa.USE_TRADIER_HISTORY=True
st.session_state.setdefault('auto_scan_enabled',False)
st.session_state.setdefault('fixture_mode','running')

def snapshot():
    now=(datetime.now(timezone.utc)-timedelta(minutes=5 if st.session_state.get('fixture_stale') else 0)).isoformat()
    syms=['PDSB','GCDT','BNC','ARBE','WCT','ROIV','INDP','SFWL','LONGNAME','XYZ','TEST']
    if st.session_state.get('fixture_reverse'): syms.reverse()
    return {'scan_time_et':now,'radar':{'full_market_enabled':True,'requested_symbols':6101,'quotes_received':6044,'coverage_pct':99.1,'coverage_ok':True,'sub_dollar_eligible':379},
    'candidates':[dict(symbol=s,setup_grade='REJECT' if i%2==0 else 'B',timeframe_best_fit=['MIXED','INTRADAY','LONGER-TERM'][i%3], explosion_score=52-i,score=50,tradeability_score=94,scanner_action='DATA CHECK' if i%2==0 else 'CAUTION',scanner_action_tier='blocked',day_pct=393.4,volume_pace=100.1,price=10,live_price_is_fallback=i%3!=1 or s=='WCT',live_price_fallback_reason='Tradier last trade was stale or older than the selected fresh consolidated quote midpoint. '+('VeryLongProviderDiagnosticWithoutSpaces'*4 if s=='WCT' else ''),action_data_integrity_ok=False) for i,s in enumerate(syms)]}

original_read=getattr(Path,'_rendering_original_read',Path.read_text)
original_exists=getattr(Path,'_rendering_original_exists',Path.exists)
Path._rendering_original_read=original_read
Path._rendering_original_exists=original_exists

def read(path,*args,**kwargs):
    if path.as_posix().endswith('scan_logs/latest_scan.json'):
        if st.session_state.get('fixture_corrupt'):return '{broken json'
        return json.dumps(snapshot())
    return original_read(path,*args,**kwargs)
def exists(path):
    if path.as_posix().endswith('scan_logs/latest_scan.json'):return True
    return original_exists(path)

def start(symbol,*args,**kwargs):
    if st.session_state.get('fixture_mode')=='start_failure':return {'started':False,'message':'Fixture start failure'}
    return dict(started=True,symbol=symbol,process=Process(),started_at=time.time())
def poll(state):
    mode=st.session_state.get('fixture_mode')
    if mode=='failure':return dict(done=True,ok=False,message='LIVE PRICE UNAVAILABLE — fixture stale quote')
    if mode=='success':return dict(done=True,ok=True,symbol=state['symbol'],result=result(state['symbol']))
    return dict(done=False,runtime_seconds=time.time()-state['started_at'])
def result(symbol):
    cr._install_common_analyzer_stubs()
    # Use the existing deterministic analyzer provider fixture and real analysis.
    original=sa.analyze
    captured=[]
    def capture(s):
        r=original(s);captured.append(r);return r
    sa.analyze=capture
    try:cr.test_analyzer_prefers_tradier()
    finally:sa.analyze=original
    r=captured[0];r['snapshot_tag']='new';r['symbol']=symbol;r['as_of']=datetime.now(timezone.utc).isoformat()
    return r

ar.start_analyzer_process=start;ar.poll_analyzer_process=poll
ar.cancel_analyzer_process=lambda state:dict(done=True,cancelled=True,message='Fixture analysis cancelled.')
sr.scanner_process_busy=lambda:False
sr.start_scanner_process=lambda **kwargs:dict(started=True,process=Process(),started_at=time.time())
sr.cancel_scanner_process=lambda state:dict(cancelled=True,message='Fixture scan cancelled.')
sr.poll_scanner_process=lambda state:dict(done=st.session_state.get('fixture_scan_done',False),ok=True,runtime_seconds=12,message='Fixture scan completed.')
ab._preload_secrets=lambda:None
ab._load_active_us_equity_choices=lambda:['BNC','TEST','PDSB']
ab._start_async_prediction_sync=lambda:None
lm.get_live_overlay=lambda metrics:dict(price=metrics.get('price'),symbol=metrics.get('symbol'),status='snapshot_only',live_price_available=True)
pt.sync_predictions_remote=lambda *a,**k:None
pt.record_prediction=lambda *a,**k:None
pt.capture_live_prediction=lambda *a,**k:None
# Secret presence is simulated; no real secrets are read.
class Secrets(dict):
    def __getattr__(self,key): return self[key]

# This is a dedicated offline process; keep fixtures active on timer reruns.
st.secrets=Secrets(TRADIER_ACCESS_TOKEN='offline-test-only')
Path.read_text=read
Path.exists=exists
runpy.run_path(str(ROOT/'analyzer_app.py'),run_name='__main__')

st.divider()
st.caption('OFFLINE RENDERING TEST — fixture data only')
st.selectbox('Fixture analyzer outcome',['running','success','failure','start_failure'],key='fixture_mode')
st.toggle('Reverse fixture ranking',key='fixture_reverse')
st.toggle('Finish fixture scan',key='fixture_scan_done')
