import html, os, subprocess, sys, json
from datetime import datetime
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Single Stock Analyzer", page_icon="📈", layout="wide")

# Make Streamlit secrets available to the analyzer module without placing keys in files.
try:
    if "ALPACA_API_KEY" in st.secrets: os.environ["ALPACA_API_KEY"] = st.secrets["ALPACA_API_KEY"]
    if "ALPACA_SECRET_KEY" in st.secrets: os.environ["ALPACA_SECRET_KEY"] = st.secrets["ALPACA_SECRET_KEY"]
    if "ALPACA_LIVE_FEED" in st.secrets: os.environ["ALPACA_LIVE_FEED"] = st.secrets["ALPACA_LIVE_FEED"]
except Exception:
    pass

from stock_analyzer import analyze

st.markdown("""
<style>
.stApp{background:#08111f;color:#edf5ff}.block-container{max-width:1450px;padding-top:1.4rem}
.hero{padding:20px 24px;border:1px solid #1e334e;border-radius:16px;background:linear-gradient(135deg,#0c1728,#0a1423);margin-bottom:14px}
.title{font-size:32px;font-weight:900;letter-spacing:-.6px}.sub{color:#91a7c2;font-size:14px}.pill{display:inline-block;padding:5px 10px;border-radius:999px;background:#11243a;border:1px solid #2c4969;font-size:12px;font-weight:800;margin-top:8px}
.card{border:1px solid #1d334e;background:#0c1727;border-radius:14px;padding:14px 16px;min-height:108px}.k{font-size:11px;color:#8097b3;font-weight:800;letter-spacing:.08em}.v{font-size:27px;font-weight:900;margin-top:4px}.n{font-size:12px;color:#91a7c2;margin-top:2px}
.good{color:#65e98d}.bad{color:#ff8181}.warn{color:#ffd166}.section{font-size:18px;font-weight:900;margin:22px 0 9px}.callout{border-left:4px solid #4593ff;background:#0d1a2d;padding:14px 16px;border-radius:8px;margin-top:10px}
</style>
""",unsafe_allow_html=True)

st.markdown('<div class="hero"><div class="title">Single Stock Analyzer</div><div class="sub">Live momentum, VWAP, volume, historical spike analogs, support/resistance and entry-risk context.</div></div>',unsafe_allow_html=True)

c1,c2,c3=st.columns([2.2,1,1])
with c1:
    ticker=st.text_input("Ticker",value=st.session_state.get("ticker","SDOT"),placeholder="SDOT").upper().strip()
with c2:
    run=st.button("Analyze",type="primary",use_container_width=True)
with c3:
    st.caption("Live feed is controlled by `ALPACA_LIVE_FEED` (IEX now; SIP after upgrade).")

if run or "result" not in st.session_state or st.session_state.get("ticker")!=ticker:
    try:
        with st.spinner(f"Analyzing {ticker}…"):
            st.session_state["result"]=analyze(ticker)
            st.session_state["ticker"]=ticker
    except Exception as e:
        st.error(str(e)); st.stop()
r=st.session_state["result"]

def money(x): return "—" if x is None else f"${x:,.2f}"
def pp(x): return "—" if x is None else f"{x:+.2f}%"
def multiple(x): return "—" if x is None else f"{x:.2f}x"

def card(col,k,v,n="",cls=""):
    with col: st.markdown(f'<div class="card"><div class="k">{html.escape(k)}</div><div class="v {cls}">{html.escape(str(v))}</div><div class="n">{html.escape(str(n))}</div></div>',unsafe_allow_html=True)

cols=st.columns(6)
card(cols[0],"PRICE",money(r.get("price")),pp(r.get("day_pct")),"good" if (r.get("day_pct") or 0)>=0 else "bad")
card(cols[1],"VWAP",money(r.get("vwap")),f'{r.get("vwap_position")} · {pp(r.get("vwap_extension_pct"))}',"good" if r.get("vwap_position")=="ABOVE" else "bad")
card(cols[2],"DAY RANGE",f'{money(r.get("day_low"))}–{money(r.get("day_high"))}',f'{r.get("from_high_pct",0):.1f}% below high')
card(cols[3],"VOL PACE",multiple(r.get("volume_pace")),f'{r.get("volume",0):,.0f} shown · {r.get("volume_source")}')
card(cols[4],"SETUP SCORE",f'{r.get("score"):.1f} / 100',f'Grade {r.get("grade")}',"good" if r.get("grade") in ("A","B") else "warn")
card(cols[5],"ENTRY",r.get("entry_quality"),f'Live feed: {r.get("live_feed")}',"good" if r.get("entry_quality")=="FAVORABLE" else "warn")

st.markdown('<div class="section">Momentum & liquidity</div>',unsafe_allow_html=True)
df=pd.DataFrame([{
    "5m %":r.get("momentum_5m"),"15m %":r.get("momentum_15m"),"30m %":r.get("momentum_30m"),"VWAP Ext %":r.get("vwap_extension_pct"),"From High %":r.get("from_high_pct"),"Spread %":r.get("spread_pct"),"Volume Pace":r.get("volume_pace")
}])
st.dataframe(df,use_container_width=True,hide_index=True)

scol,rcol=st.columns(2)
with scol:
    st.markdown('<div class="section">Support</div>',unsafe_allow_html=True)
    sup=r.get("supports") or []
    st.dataframe(pd.DataFrame(sup) if sup else pd.DataFrame(columns=["price","touches","side"]),use_container_width=True,hide_index=True)
with rcol:
    st.markdown('<div class="section">Resistance</div>',unsafe_allow_html=True)
    res=r.get("resistances") or []
    st.dataframe(pd.DataFrame(res) if res else pd.DataFrame(columns=["price","touches","side"]),use_container_width=True,hide_index=True)

h=r.get("historical_analogs") or {}
st.markdown('<div class="section">Historical spike analogs</div>',unsafe_allow_html=True)
if h.get("status")=="ok":
    sm=h.get("summary") or {}; hc=st.columns(4)
    for col,n in zip(hc,(1,2,3,5)):
        x=sm.get(f"d{n}") or {}
        card(col,f"+{n} DAY",f'{x.get("up_pct") if x.get("up_pct") is not None else "—"}% higher',f'Median {pp(x.get("median"))} · n={x.get("n",0)}')
    st.caption(f'Closest {h.get("sample_count",0)} same-ticker spikes, threshold ≥ {h.get("threshold_pct")}% · source: {h.get("feed")}')
    sdf=pd.DataFrame(h.get("samples") or [])
    if not sdf.empty:
        show=[c for c in ["date","spike_pct","d1","d2","d3","d5"] if c in sdf.columns]
        st.dataframe(sdf[show],use_container_width=True,hide_index=True)
else: st.info("Not enough historical data for spike analogs yet.")

st.markdown('<div class="section">Recent catalyst/news context</div>',unsafe_allow_html=True)
arts=r.get("news") or []
if arts:
    for a in arts[:5]:
        tag=f'{a.get("category")} ({a.get("score",0):+})'
        age=f'{a.get("age_hours"):.1f}h ago' if a.get("age_hours") is not None else "recent"
        st.markdown(f'**{html.escape(a.get("headline") or "")}**  \n{html.escape(tag)} · {html.escape(age)} · {html.escape(a.get("source") or "")}')
else: st.caption("No recent Alpaca news returned.")

# Plain-English rule-based readout.
score=r.get("score") or 0; pos=r.get("vwap_position"); fp=r.get("from_high_pct") or 0; day=r.get("day_pct") or 0
if r.get("entry_quality")=="FAVORABLE": verdict="The setup is currently favorable by the analyzer's momentum/risk rules, but still requires risk control."
elif day>40 and (r.get("vwap_extension_pct") or 0)>8: verdict="Momentum is strong, but the stock is extended. The analyzer favors waiting for a pullback/hold or a confirmed breakout rather than chasing."
elif pos=="BELOW": verdict="The setup has weakened because price is below VWAP. A VWAP reclaim would improve the intraday picture."
else: verdict="The setup is mixed. Watch the nearest support/resistance and require confirmation before treating the move as high quality."
st.markdown(f'<div class="callout"><b>{html.escape(ticker)} read:</b> {html.escape(verdict)}<br><span class="sub">This is a trading-analysis aid, not a guarantee of future price movement.</span></div>',unsafe_allow_html=True)

st.caption(f'As of {r.get("as_of")} · Live={r.get("live_feed")} · Historical/liquidity={r.get("historical_feed")}')
