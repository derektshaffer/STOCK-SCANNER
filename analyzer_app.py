import html, os, subprocess, sys, json
from datetime import datetime
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Single Stock Analyzer", page_icon="📈", layout="wide")

# Make Streamlit Cloud secrets available to the analyzer module without
# placing credentials in GitHub. This deliberately happens BEFORE importing
# stock_analyzer because that module reads its configuration at import time.
def _load_streamlit_secrets_into_env():
    required = ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")
    optional = ("ALPACA_LIVE_FEED", "ALPACA_HISTORICAL_FEED")

    try:
        secrets = dict(st.secrets)
    except Exception as exc:
        st.error(f"Streamlit Secrets could not be read: {exc}")
        st.stop()

    for key in required + optional:
        value = secrets.get(key)
        if value is not None and str(value).strip():
            os.environ[key] = str(value).strip()

    missing = [key for key in required if not os.environ.get(key, "").strip()]
    if missing:
        available = ", ".join(sorted(secrets.keys())) if secrets else "none"
        st.error(
            "Missing required Alpaca credentials in Streamlit Secrets: "
            + ", ".join(missing)
            + f". Secret names currently visible to the app: {available}."
        )
        st.stop()

_load_streamlit_secrets_into_env()

from stock_analyzer import analyze

st.markdown("""
<style>
.stApp{background:#08111f;color:#edf5ff}.block-container{max-width:1450px;padding-top:1.4rem}
.hero{padding:20px 24px;border:1px solid #1e334e;border-radius:16px;background:linear-gradient(135deg,#0c1728,#0a1423);margin-bottom:14px}
.title{font-size:32px;font-weight:900;letter-spacing:-.6px}.sub{color:#91a7c2;font-size:14px}.pill{display:inline-block;padding:5px 10px;border-radius:999px;background:#11243a;border:1px solid #2c4969;font-size:12px;font-weight:800;margin-top:8px}
.card{border:1px solid #1d334e;background:#0c1727;border-radius:14px;padding:14px 16px;min-height:108px}.k{font-size:11px;color:#8097b3;font-weight:800;letter-spacing:.08em}.v{font-size:27px;font-weight:900;margin-top:4px}.n{font-size:12px;color:#91a7c2;margin-top:2px}
.good{color:#65e98d}.bad{color:#ff8181}.warn{color:#ffd166}.section{font-size:18px;font-weight:900;margin:22px 0 9px}.callout{border-left:4px solid #4593ff;background:#0d1a2d;padding:14px 16px;border-radius:8px;margin-top:10px}
.tradeplan{border:1px solid #274664;background:#0b1829;border-radius:16px;padding:18px 20px;margin:16px 0 8px}.tradeaction{font-size:25px;font-weight:900;margin-bottom:5px}.tradewhy{color:#a9bdd4;font-size:13px}.smallnote{color:#91a7c2;font-size:12px}
</style>
""",unsafe_allow_html=True)

st.markdown('<div class="hero"><div class="title">Single Stock Analyzer</div><div class="sub">Live momentum, VWAP, volume, historical analogs, support/resistance and dynamic entry/exit planning.</div></div>',unsafe_allow_html=True)

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
def rr(x): return "—" if x is None else f"{x:.2f}:1"
def dollars_compact(x):
    if x is None: return "—"
    x=float(x)
    if abs(x)>=1_000_000_000:return f"${x/1_000_000_000:.1f}B"
    if abs(x)>=1_000_000:return f"${x/1_000_000:.1f}M"
    if abs(x)>=1_000:return f"${x/1_000:.1f}K"
    return f"${x:,.0f}"
def zone_text(plan):
    if not plan:return "—"
    lo=plan.get("entry_low"); hi=plan.get("entry_high")
    return f"{money(lo)}–{money(hi)}" if lo is not None and hi is not None else "—"

def card(col,k,v,n="",cls=""):
    with col: st.markdown(f'<div class="card"><div class="k">{html.escape(k)}</div><div class="v {cls}">{html.escape(str(v))}</div><div class="n">{html.escape(str(n))}</div></div>',unsafe_allow_html=True)

cols=st.columns(6)
card(cols[0],"PRICE",money(r.get("price")),pp(r.get("day_pct")),"good" if (r.get("day_pct") or 0)>=0 else "bad")
card(cols[1],"VWAP",money(r.get("vwap")),f'{r.get("vwap_position")} · {pp(r.get("vwap_extension_pct"))}',"good" if r.get("vwap_position")=="ABOVE" else "bad")
card(cols[2],"DAY RANGE",f'{money(r.get("day_low"))}–{money(r.get("day_high"))}',f'{r.get("from_high_pct",0):.1f}% below high')
card(cols[3],"VOL PACE",multiple(r.get("volume_pace")),f'{r.get("volume",0):,.0f} shown · {r.get("volume_source")}')
card(cols[4],"SETUP SCORE",f'{r.get("score"):.1f} / 100',f'Grade {r.get("grade")}',"good" if r.get("grade") in ("A","B") else "warn")
card(cols[5],"BASE SETUP",r.get("entry_quality"),f'Live feed: {r.get("live_feed")}',"good" if r.get("entry_quality")=="FAVORABLE" else "warn")

# Dynamic decision-support trade plan. This can explicitly return WAIT or
# NO TRADE instead of manufacturing an entry for every ticker.
plan=r.get("trade_plan") or {}
selected=plan.get("selected") or {}
status=plan.get("status") or "WAIT"
status_cls="good" if status=="ENTRY AVAILABLE" else "bad" if status=="NO TRADE" else "warn"
why=" ".join(plan.get("reasons") or [])
st.markdown(
    f'<div class="tradeplan"><div class="k">SUGGESTED TRADE PLAN</div>'
    f'<div class="tradeaction {status_cls}">{html.escape(plan.get("action") or status)}</div>'
    f'<div class="tradewhy">{html.escape(why)}</div></div>',
    unsafe_allow_html=True,
)

tp=st.columns(7)
card(tp[0],"ENTRY ZONE",zone_text(selected),str(selected.get("entry_source") or selected.get("breakout_source") or plan.get("preferred_plan") or ""),status_cls)
card(tp[1],"STOP / INVALIDATION",money(selected.get("stop")),selected.get("stop_reason") or "")
card(tp[2],"TARGET 1",money(selected.get("target1")),selected.get("target1_reason") or "","good")
card(tp[3],"TARGET 2",money(selected.get("target2")),selected.get("target2_reason") or "","good")
card(tp[4],"STRETCH",money(selected.get("stretch_target")),selected.get("stretch_reason") or "")
card(tp[5],"REWARD / RISK",rr(selected.get("risk_reward")),"to Target 1","good" if (selected.get("risk_reward") or 0)>=1.5 else "warn")
card(tp[6],"PLAN CONFIDENCE",f'{plan.get("confidence","—")} / 100',plan.get("confidence_label") or "","good" if (plan.get("confidence") or 0)>=75 else "warn")

with st.expander("Trade plan details — pullback vs breakout"):
    pc1,pc2=st.columns(2)
    pull=plan.get("pullback") or {}
    brk=plan.get("breakout") or {}
    with pc1:
        st.markdown("#### Pullback plan")
        st.write(f'**Entry zone:** {zone_text(pull)}')
        st.write(f'**Entry basis:** {pull.get("entry_source") or "—"}')
        st.write(f'**Stop / invalidation:** {money(pull.get("stop"))}')
        st.write(f'**Target 1:** {money(pull.get("target1"))} — {pull.get("target1_reason") or "—"}')
        st.write(f'**Target 2:** {money(pull.get("target2"))} — {pull.get("target2_reason") or "—"}')
        st.write(f'**Stretch:** {money(pull.get("stretch_target"))} — {pull.get("stretch_reason") or "—"}')
        st.write(f'**Reward/risk to T1:** {rr(pull.get("risk_reward"))}')
    with pc2:
        st.markdown("#### Breakout plan")
        st.write(f'**Breakout trigger:** {money(brk.get("breakout_level"))} ({brk.get("breakout_source") or "level"})')
        st.write(f'**Confirmed entry zone:** {zone_text(brk)}')
        st.write(f'**Stop / invalidation:** {money(brk.get("stop"))}')
        st.write(f'**Target 1:** {money(brk.get("target1"))} — {brk.get("target1_reason") or "—"}')
        st.write(f'**Target 2:** {money(brk.get("target2"))} — {brk.get("target2_reason") or "—"}')
        st.write(f'**Stretch:** {money(brk.get("stretch_target"))} — {brk.get("stretch_reason") or "—"}')
        st.write(f'**Reward/risk to T1:** {rr(brk.get("risk_reward"))}')
        st.caption(brk.get("confirmation") or "")

    histctx=plan.get("historical") or {}
    cat=plan.get("catalyst") or {}
    liq=plan.get("liquidity") or {}
    st.markdown("#### Inputs affecting the plan")
    ddf=pd.DataFrame([{
        "ATR 14":money(plan.get("atr")),
        "ATR %":pp(plan.get("atr_pct")),
        "Liquidity":liq.get("label"),
        "Avg $ volume":dollars_compact(liq.get("avg_dollar_volume")),
        "Nearest support":money(plan.get("nearest_support")),
        "Support quality":plan.get("nearest_support_quality") or "—",
        "Nearest resistance":money(plan.get("nearest_resistance")),
        "Historical analogs":histctx.get("sample_count",0),
        "Analog relevance":histctx.get("relevance") or "—",
        "Analog next-day higher":f'{histctx.get("next_day_up_pct"):.1f}%' if histctx.get("next_day_up_pct") is not None else "—",
        "Median 1d run-up":pp(histctx.get("median_mfe_1d")),
        "Median 3d run-up":pp(histctx.get("median_mfe_3d")),
        "Median 1d drawdown":pp(histctx.get("median_mae_1d")),
        "Catalyst bias":cat.get("label") or "NEUTRAL",
    }])
    st.dataframe(ddf,use_container_width=True,hide_index=True)
    st.caption(plan.get("method_note") or "")

st.markdown('<div class="section">Momentum & liquidity</div>',unsafe_allow_html=True)
liq=r.get("liquidity") or {}
df=pd.DataFrame([{
    "5m %":r.get("momentum_5m"),"15m %":r.get("momentum_15m"),"30m %":r.get("momentum_30m"),
    "VWAP Ext %":r.get("vwap_extension_pct"),"From High %":r.get("from_high_pct"),"ATR 14 %":r.get("atr_14_pct"),
    "Spread %":r.get("spread_pct"),"Volume Pace":r.get("volume_pace"),"Liquidity":liq.get("label"),
    "Avg $ Volume":dollars_compact(liq.get("avg_dollar_volume"))
}])
st.dataframe(df,use_container_width=True,hide_index=True)

def level_table(rows):
    columns=["Price","Touches","Last touch","Age","Quality","Side"]
    if not rows:
        return pd.DataFrame(columns=columns)
    out=[]
    for row in rows:
        out.append({
            "Price":row.get("price"),
            "Touches":row.get("touches"),
            "Last touch":row.get("last_touch_label") or "—",
            "Age":row.get("age") or "—",
            "Quality":f'{row.get("quality") or "—"} ({row.get("quality_score",0)}/100)',
            "Side":str(row.get("side") or "").title(),
        })
    return pd.DataFrame(out,columns=columns)

scol,rcol=st.columns(2)
with scol:
    st.markdown('<div class="section">Support</div>',unsafe_allow_html=True)
    sup=r.get("supports") or []
    st.dataframe(
        level_table(sup),
        use_container_width=True,
        hide_index=True,
        column_config={"Price":st.column_config.NumberColumn(format="$%.2f")},
    )
with rcol:
    st.markdown('<div class="section">Resistance</div>',unsafe_allow_html=True)
    res=r.get("resistances") or []
    st.dataframe(
        level_table(res),
        use_container_width=True,
        hide_index=True,
        column_config={"Price":st.column_config.NumberColumn(format="$%.2f")},
    )
st.caption("Last touch = most recent regular-session test of the level. Recent tests use 1-minute bars; older tests use 5-minute bars as a fallback. Times are Eastern (ET).")

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
