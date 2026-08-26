import html
import json
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

SCAN_FILE = Path("scan_logs/latest_scan.json")
AUTO_SCAN_SECONDS = 300
AUTO_STATUS_REFRESH_SECONDS = 30

st.set_page_config(
    page_title="Momentum Scanner",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
:root{
  --bg:#0b1220;--panel:#111b2e;--panel2:#16233a;--text:#f4f7fb;
  --muted:#9fb0c9;--line:#2b3b56;--green:#22c55e;--amber:#f59e0b;
  --red:#ef4444;--blue:#38bdf8
}
.stApp{background:var(--bg);color:var(--text)}
.block-container{padding-top:1.1rem;padding-bottom:3rem;max-width:1500px}
.header{border:1px solid var(--line);background:linear-gradient(135deg,#101a2c,#0d1728);
  padding:20px 24px;border-radius:18px;margin-bottom:16px}
.title{font-size:34px;font-weight:900;letter-spacing:-.02em}
.sub{color:var(--muted);font-size:14px;margin-top:5px}
.pill,.badge{display:inline-block;border-radius:999px;font-size:12px;font-weight:850;
  padding:6px 9px;margin:4px 5px 4px 0;border:1px solid transparent}
.green{color:#b8f7ca;background:rgba(34,197,94,.13);border-color:rgba(34,197,94,.34)}
.amber{color:#ffe0a0;background:rgba(245,158,11,.14);border-color:rgba(245,158,11,.34)}
.red{color:#ffc1c1;background:rgba(239,68,68,.14);border-color:rgba(239,68,68,.34)}
.blue{color:#bfeaff;background:rgba(56,189,248,.12);border-color:rgba(56,189,248,.34)}
.section{font-size:22px;font-weight:900;margin:24px 0 4px}
.section-sub{color:var(--muted);font-size:14px;margin-bottom:12px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  padding:15px 17px;min-height:100px}
.stat-k{color:var(--muted);font-size:11px;font-weight:850;letter-spacing:.08em;text-transform:uppercase}
.stat-v{font-size:31px;font-weight:950;margin-top:5px}
.stat-n{color:var(--muted);font-size:12px;margin-top:7px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;
  padding:18px;margin-bottom:12px;min-height:370px;box-shadow:0 8px 24px rgba(0,0,0,.12)}
.card-a{border-top:5px solid var(--green)}
.card-b{border-top:5px solid var(--blue)}
.card-c{border-top:5px solid var(--amber)}
.card-r{border-top:5px solid var(--red)}
.trow{display:flex;justify-content:space-between;gap:12px}
.ticker{font-size:30px;font-weight:950}
.score{font-size:29px;font-weight:950}
.score small{display:block;color:var(--muted);font-size:10px;text-align:right;letter-spacing:.08em}
.price{font-size:18px;font-weight:800;margin:3px 0 10px}
.pos{color:#65e98d}.neg{color:#ff8181}.muted{color:var(--muted)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:12px 0}
.metric{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:9px 10px}
.mk{color:var(--muted);font-size:10px;font-weight:850;letter-spacing:.06em}
.mv{font-size:17px;font-weight:900;margin-top:2px}
.note{background:rgba(255,255,255,.025);border-left:3px solid var(--line);
  padding:9px 10px;margin-top:10px;border-radius:6px}
.nk{color:var(--muted);font-size:10px;font-weight:850;letter-spacing:.06em}
.nv{font-size:13px;line-height:1.45;margin-top:3px}
.news-time{color:var(--muted);font-size:11px;font-weight:750;margin-top:6px}
.legend-box{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  padding:14px 16px;margin:14px 0 20px}
.legend-title{font-size:13px;font-weight:900;letter-spacing:.06em;text-transform:uppercase;margin-bottom:9px}
.legend-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
.legend-item{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.legend-term{font-size:12px;font-weight:900;margin-bottom:4px}
.legend-def{color:var(--muted);font-size:12px;line-height:1.4}
.auto-box{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:7px 10px;margin:2px 0 6px}
.market-box{display:flex;align-items:center;justify-content:space-between;gap:10px;
  min-height:38px;box-sizing:border-box;background:var(--panel);border:1px solid var(--line);
  border-radius:9px;padding:6px 10px;margin:0}
.market-box.open{border-left:4px solid var(--green)}
.market-box.closed{border-left:4px solid var(--amber)}
.market-main{font-size:13px;font-weight:900;white-space:nowrap}
.market-time{color:var(--muted);font-size:11px;font-weight:700;white-space:nowrap}
.auto-on{border-left:4px solid var(--green)}
.auto-wait{border-left:4px solid var(--amber)}
.auto-off{border-left:4px solid var(--muted)}
.bar-row{margin:9px 0 13px}
.bar-head{display:flex;justify-content:space-between;font-size:13px;font-weight:750}
.bar-track{height:9px;background:#17243a;border-radius:999px;overflow:hidden;
  margin-top:5px;border:1px solid #263750}
.bar-fill{height:100%;background:linear-gradient(90deg,#f59e0b,#ef4444);border-radius:999px}
div[data-testid="stDataFrame"]{border:1px solid var(--line);border-radius:12px;overflow:hidden}
.stButton button{border-radius:8px;font-weight:850;min-height:38px;height:38px;padding-top:4px;padding-bottom:4px}
@media(max-width:900px){.title{font-size:28px}.ticker{font-size:27px}.grid{grid-template-columns:1fr}.legend-grid{grid-template-columns:1fr}}

/* COMPACT MOMENTUM SCANNER LAYOUT */
.block-container{
  padding-top:.35rem !important;
  padding-bottom:1.25rem !important;
}
.block-container [data-testid="stVerticalBlock"]{
  gap:.42rem !important;
}
.header{
  padding:10px 14px !important;
  border-radius:12px !important;
  margin-bottom:7px !important;
}
.title{
  font-size:25px !important;
}
.sub{
  font-size:12px !important;
  margin-top:3px !important;
}
.pill,.badge{
  font-size:10px !important;
  padding:4px 7px !important;
  margin:2px 4px 2px 0 !important;
}
.section{
  font-size:18px !important;
  margin:13px 0 2px !important;
}
.section-sub{
  font-size:12px !important;
  line-height:1.25 !important;
  margin-bottom:6px !important;
}
.stat{
  padding:9px 11px !important;
  min-height:72px !important;
  border-radius:10px !important;
}
.stat-k{
  font-size:9px !important;
}
.stat-v{
  font-size:24px !important;
  margin-top:2px !important;
}
.stat-n{
  font-size:10px !important;
  margin-top:3px !important;
}
.card{
  padding:11px !important;
  margin-bottom:7px !important;
  min-height:0 !important;
  border-radius:11px !important;
}
.card-a,.card-b,.card-c,.card-r{
  border-top-width:3px !important;
}
.ticker{
  font-size:24px !important;
}
.score{
  font-size:23px !important;
}
.score small{
  font-size:9px !important;
}
.price{
  font-size:15px !important;
  margin:2px 0 6px !important;
}
.grid{
  gap:6px !important;
  margin:7px 0 !important;
}
.metric{
  padding:6px 8px !important;
  border-radius:8px !important;
}
.mk{
  font-size:9px !important;
}
.mv{
  font-size:15px !important;
  margin-top:1px !important;
}
.note{
  padding:6px 8px !important;
  margin-top:6px !important;
}
.nk{
  font-size:9px !important;
}
.nv{
  font-size:11.5px !important;
  line-height:1.3 !important;
  margin-top:2px !important;
}
.news-time{
  font-size:10px !important;
  margin-top:3px !important;
}
.legend-box{
  padding:9px 10px !important;
  margin:8px 0 10px !important;
  border-radius:10px !important;
}
.legend-title{
  margin-bottom:6px !important;
}
.legend-grid{
  gap:6px !important;
}
.legend-item{
  padding:7px 8px !important;
  border-radius:8px !important;
}
.legend-term{
  font-size:11px !important;
  margin-bottom:2px !important;
}
.legend-def{
  font-size:11px !important;
  line-height:1.3 !important;
}
.bar-row{
  margin:6px 0 8px !important;
}
.bar-track{
  height:7px !important;
  margin-top:3px !important;
}
[data-testid="stExpander"]{
  margin:4px 0 !important;
}
div[data-testid="stAlert"]{
  padding-top:6px !important;
  padding-bottom:6px !important;
  margin:4px 0 !important;
}


/* SECOND COMPACT PASS */
.header{
  padding:8px 12px !important;
  margin-bottom:5px !important;
}
.stat{
  padding:7px 9px !important;
  min-height:64px !important;
}
.card{
  padding:9px !important;
  margin-bottom:5px !important;
}
.grid{
  margin:5px 0 !important;
  gap:5px !important;
}
.metric{
  padding:5px 7px !important;
}
.note{
  padding:5px 7px !important;
  margin-top:5px !important;
}
.section{
  margin:10px 0 2px !important;
}
.section-sub{
  margin-bottom:4px !important;
}
.legend-box{
  margin:6px 0 8px !important;
  padding:7px 8px !important;
}

</style>
""",
    unsafe_allow_html=True,
)


def secret(name):
    try:
        return str(st.secrets[name]).strip()
    except Exception:
        return os.environ.get(name, "").strip()


def market_is_open():
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:
        return False, now_et
    minutes = now_et.hour * 60 + now_et.minute
    return (9 * 60 + 30) <= minutes < (16 * 60), now_et


def run_scanner():
    key = secret("ALPACA_API_KEY")
    sec = secret("ALPACA_SECRET_KEY")
    if not key or not sec:
        return (
            False,
            "Alpaca credentials are not configured for this app yet. "
            "Add ALPACA_API_KEY and ALPACA_SECRET_KEY in Streamlit Secrets.",
        )

    env = os.environ.copy()
    env["ALPACA_API_KEY"] = key
    env["ALPACA_SECRET_KEY"] = sec

    try:
        p = subprocess.run(
            [sys.executable, "stock_scanner.py"],
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return False, "The scanner exceeded its 3-minute timeout."

    st.session_state["scanner_out"] = p.stdout[-12000:]
    st.session_state["scanner_err"] = p.stderr[-6000:]

    if p.returncode != 0:
        error = p.stderr.strip() or p.stdout.strip() or "Unknown scanner error"
        return False, error[-3000:]

    return (
        SCAN_FILE.exists(),
        "Fresh scan complete."
        if SCAN_FILE.exists()
        else "Scanner ran, but latest_scan.json was not created.",
    )


def load_scan():
    if not SCAN_FILE.exists():
        return None
    try:
        return json.loads(SCAN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def f(v, d=1, suffix=""):
    if v is None:
        return "—"
    try:
        return f"{float(v):,.{d}f}{suffix}"
    except Exception:
        return "—"


def metric(k, v, cls=""):
    return (
        f'<div class="metric"><div class="mk">{html.escape(k)}</div>'
        f'<div class="mv {cls}">{html.escape(v)}</div></div>'
    )


def ml_display(c):
    if not c.get("ml_validated"):
        status = str(c.get("ml_status") or "").lower()
        if status in {"failed_validation", "prediction_error", "error"}:
            return "NOT VALID", "neg"
        return "LEARNING", "muted"

    try:
        probability = float(c.get("ml_continuation_prob_pct"))
    except (TypeError, ValueError):
        return "—", "muted"

    cls = "pos" if probability >= 65 else ("muted" if probability >= 50 else "neg")
    return f"{probability:.0f}%", cls


def format_catalyst_time(news):
    """Return the catalyst publication timestamp in U.S. Eastern Time."""
    raw = news.get("published_at") or news.get("created_at")
    if raw:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            et = dt.astimezone(ZoneInfo("America/New_York"))
            stamp = et.strftime("%a %b %d, %Y · %I:%M %p ET").replace(" 0", " ")
            age = news.get("age_hours")
            if age is not None:
                try:
                    age = float(age)
                    if age < 1:
                        age_text = f"{max(1, round(age * 60))} min ago"
                    elif age < 24:
                        age_text = f"{age:.1f}h ago"
                    else:
                        age_text = f"{age / 24:.1f}d ago"
                    return f"Published {stamp} · {age_text}"
                except (TypeError, ValueError):
                    pass
            return f"Published {stamp}"
        except (TypeError, ValueError):
            pass

    age = news.get("age_hours")
    if age is not None:
        try:
            age = float(age)
            if age < 1:
                return f"Published about {max(1, round(age * 60))} min before this scan"
            if age < 24:
                return f"Published about {age:.1f}h before this scan"
            return f"Published about {age / 24:.1f}d before this scan"
        except (TypeError, ValueError):
            pass
    return "Publication time unavailable"


def card(c):
    grade = str(c.get("setup_grade") or "REJECT")
    label = str(c.get("setup_label") or "")
    card_cls = {"A": "card-a", "B": "card-b", "C": "card-c"}.get(grade, "card-r")
    badge_cls = {"A": "green", "B": "blue", "C": "amber"}.get(grade, "red")

    score = float(c.get("score") or 0)
    score_color = "#65e98d" if score >= 75 else ("#ffd166" if score >= 60 else "#ff8181")

    day = c.get("day_pct")
    day_cls = "pos" if (day or 0) >= 0 else "neg"

    passed = bool(c.get("passed_base_filters"))
    pass_badge = (
        '<span class="badge green">BASE FILTERS PASS</span>'
        if passed
        else '<span class="badge amber">FILTERED / NEAR MISS</span>'
    )
    vwap_badge = (
        '<span class="badge green">ABOVE VWAP</span>'
        if c.get("above_vwap")
        else '<span class="badge red">BELOW VWAP</span>'
    )

    alert = c.get("alert_tier")
    alert_badge = ""
    if alert:
        ac = "green" if alert == "HIGH" else ("blue" if alert == "WATCH" else "amber")
        alert_badge = f'<span class="badge {ac}">ALERT {html.escape(str(alert))}</span>'

    ml_text, ml_cls = ml_display(c)

    spread = (
        c.get("iex_spread_pct")
        if c.get("iex_spread_pct") is not None
        else c.get("spread_pct")
    )

    notes = (
        (c.get("grade_reasons") or [])
        + (c.get("failed_filters") or [])
        + (c.get("tradability_warnings") or [])
        + (c.get("setup_flags") or [])
    )
    note = " · ".join(str(x) for x in notes[:4]) or "No major issues flagged."

    news = c.get("news") or {}
    news_bits = [news.get("category"), news.get("headline")]
    news_text = " — ".join(str(x) for x in news_bits if x)
    news_time = format_catalyst_time(news)
    news_html = (
        f'<div class="note"><div class="nk">CATALYST</div>'
        f'<div class="nv">{html.escape(news_text[:220])}</div>'
        f'<div class="news-time">{html.escape(news_time)}</div></div>'
        if news_text
        else ""
    )

    return f"""
<div class="card {card_cls}">
  <div class="trow">
    <div>
      <div class="ticker">{html.escape(str(c.get("symbol") or "—"))}</div>
      <div class="price">${f(c.get("price"),2)}
        <span class="{day_cls}">&nbsp;{f(day,1,"%")} today</span>
      </div>
    </div>
    <div class="score" style="color:{score_color}">{score:.0f}<small>SCORE / 100</small></div>
  </div>
  <div>
    <span class="badge {badge_cls}">GRADE {html.escape(grade)} · {html.escape(label)}</span>
    {pass_badge}{alert_badge}{vwap_badge}
  </div>
  <div class="grid">
    {metric("5 MIN",f(c.get("momentum_5m"),2,"%"),"pos" if (c.get("momentum_5m") or 0)>0 else "muted")}
    {metric("15 MIN",f(c.get("momentum_15m"),2,"%"),"pos" if (c.get("momentum_15m") or 0)>0 else "muted")}
    {metric("ML 60M",ml_text,ml_cls)}
    {metric("TOD VOL PACE",f(c.get("volume_pace"),2,"x"),"pos" if (c.get("volume_pace") or 0)>=1.5 else "muted")}
    {metric("NORMAL VOL BY NOW",f(c.get("expected_volume_fraction_pct"),1,"%"))}
    {metric("VWAP PRICE","$"+f(c.get("vwap"),2),"pos" if c.get("above_vwap") else "neg")}
    {metric("FROM HIGH",f(c.get("distance_from_high_pct"),2,"%"))}
    {metric("IEX SPREAD",f(spread,2,"%"))}
    {metric("LIQUIDITY","$"+f((c.get("liquidity_dollar_volume") or 0)/1_000_000,1,"M"))}
  </div>
  <div class="note"><div class="nk">SETUP READ</div><div class="nv">{html.escape(note[:250])}</div></div>
  {news_html}
</div>
"""


def to_df(records):
    out = []
    for c in records:
        news = c.get("news") or {}
        fail = (c.get("failed_filters") or []) + (c.get("tradability_warnings") or [])
        out.append(
            {
                "Ticker": c.get("symbol"),
                "Grade": c.get("setup_grade"),
                "Status": c.get("setup_label"),
                "Score": c.get("score"),
                "Opportunity": c.get("opportunity_score"),
                "ML 60m %": c.get("ml_continuation_prob_pct"),
                "ML Status": "VALIDATED" if c.get("ml_validated") else str(c.get("ml_status") or "learning").upper(),
                "Price": c.get("price"),
                "Day %": c.get("day_pct"),
                "5m %": c.get("momentum_5m"),
                "15m %": c.get("momentum_15m"),
                "TOD Vol Pace": c.get("volume_pace"),
                "Normal Vol by Now %": c.get("expected_volume_fraction_pct"),
                "Vol vs Expected %": c.get("volume_vs_expected_pct"),
                "Vol Profile Days": c.get("volume_profile_samples"),
                "From High %": c.get("distance_from_high_pct"),
                "VWAP $": c.get("vwap"),
                "VWAP Status": "ABOVE" if c.get("above_vwap") else "BELOW",
                "Liquidity $M": round(
                    (c.get("liquidity_dollar_volume") or 0) / 1_000_000, 2
                ),
                "IEX Spread %": c.get("iex_spread_pct"),
                "Catalyst": news.get("category") or "—",
                "Failed / Warning": " | ".join(fail[:3]) or "PASS",
            }
        )
    return pd.DataFrame(out)


def styled(df):
    if df.empty:
        return df

    def score_style(v):
        try:
            x = float(v)
        except Exception:
            return ""
        if x >= 75:
            return "background-color:rgba(34,197,94,.20);font-weight:800"
        if x >= 60:
            return "background-color:rgba(245,158,11,.20);font-weight:800"
        return "background-color:rgba(239,68,68,.17);font-weight:800"

    def grade_style(v):
        return {
            "A": "background-color:rgba(34,197,94,.20);font-weight:900",
            "B": "background-color:rgba(56,189,248,.18);font-weight:900",
            "C": "background-color:rgba(245,158,11,.20);font-weight:900",
            "REJECT": "background-color:rgba(239,68,68,.17);font-weight:900",
        }.get(str(v), "")

    def vwap_style(v):
        return (
            "color:#65e98d;font-weight:850"
            if v == "ABOVE"
            else "color:#ff8181;font-weight:850"
        )

    def ml_style(v):
        try:
            x = float(v)
        except Exception:
            return "color:#9fb0c9;font-weight:800"
        if x >= 65:
            return "color:#65e98d;font-weight:900"
        if x >= 50:
            return "color:#ffd166;font-weight:900"
        return "color:#ff8181;font-weight:900"

    return (
        df.style.map(score_style, subset=["Score", "Opportunity"])
        .map(ml_style, subset=["ML 60m %"])
        .map(grade_style, subset=["Grade"])
        .map(vwap_style, subset=["VWAP Status"])
        .format(
            {
                "Score": "{:.1f}",
                "Opportunity": lambda x: "—" if pd.isna(x) else f"{x:.1f}",
                "ML 60m %": lambda x: "—" if pd.isna(x) else f"{x:.1f}%",
                "Price": "${:.2f}",
                "Day %": lambda x: "—" if pd.isna(x) else f"{x:.2f}%",
                "5m %": lambda x: "—" if pd.isna(x) else f"{x:.2f}%",
                "15m %": lambda x: "—" if pd.isna(x) else f"{x:.2f}%",
                "Vol Pace": lambda x: "—" if pd.isna(x) else f"{x:.2f}x",
                "From High %": lambda x: "—" if pd.isna(x) else f"{x:.2f}%",
                "VWAP $": lambda x: "—" if pd.isna(x) else f"${x:.2f}",
                "Liquidity $M": "${:.2f}M",
                "IEX Spread %": lambda x: "—" if pd.isna(x) else f"{x:.2f}%",
            }
        )
    )


if "auto_scan_enabled" not in st.session_state:
    st.session_state["auto_scan_enabled"] = True
if "last_auto_scan_at" not in st.session_state:
    st.session_state["last_auto_scan_at"] = 0.0
if "last_auto_message" not in st.session_state:
    st.session_state["last_auto_message"] = ""


controls_mount = st.session_state.get("_scanner_controls_mount")
controls_context = controls_mount.container() if controls_mount is not None else st.container(key="scanner_controls_top")

with controls_context:
    scan_col, auto_col, spacer_col, market_col = st.columns(
        [1.15, 1.45, 2.45, 1.55],
        vertical_alignment="center",
    )
    with scan_col:
        clicked = st.button("▶ Run Fresh Scan", type="primary", use_container_width=True)
    with auto_col:
        st.toggle(
            "Auto scan every 5 minutes",
            key="auto_scan_enabled",
            help="Runs while this dashboard tab/session is active. Automatic scans pause when the regular US market is closed.",
        )
    with market_col:
        market_open, now_et = market_is_open()
        market_label = "🟢 MARKET OPEN" if market_open else "🟡 MARKET CLOSED"
        st.markdown(
            f'<div class="market-box {"open" if market_open else "closed"}">'
            f'<span class="market-main">{market_label}</span>'
            f'<span class="market-time">{now_et:%I:%M %p ET}</span></div>',
            unsafe_allow_html=True,
        )

if clicked:
    with st.spinner("Scanning Alpaca movers and ranking setups…"):
        ok, msg = run_scanner()
    if ok:
        st.session_state["last_auto_scan_at"] = time.time()
        st.session_state["last_auto_message"] = "Manual scan completed."
        st.success(msg)
    else:
        st.error(msg)


auto_run_every = (
    AUTO_STATUS_REFRESH_SECONDS if st.session_state["auto_scan_enabled"] else None
)


@st.fragment(run_every=auto_run_every)
def auto_scan_controller():
    enabled = st.session_state["auto_scan_enabled"]
    open_now, now_et = market_is_open()

    if not enabled:
        st.markdown(
            '<div class="auto-box auto-off"><b>⏸ AUTO SCAN OFF</b><br>'
            '<span class="sub">Use Run Fresh Scan anytime.</span></div>',
            unsafe_allow_html=True,
        )
        return

    if not open_now:
        st.markdown(
            '<div class="auto-box auto-wait"><b>🟡 AUTO SCAN ARMED</b><br>'
            '<span class="sub">Paused while the regular market is closed. '
            'It will scan automatically while this app is open during 9:30 AM–4:00 PM ET.</span></div>',
            unsafe_allow_html=True,
        )
        return

    elapsed = time.time() - float(st.session_state.get("last_auto_scan_at", 0.0))
    remaining = max(0, AUTO_SCAN_SECONDS - elapsed)

    if elapsed >= AUTO_SCAN_SECONDS:
        with st.spinner("Automatic 5-minute scan running…"):
            ok, msg = run_scanner()
        st.session_state["last_auto_scan_at"] = time.time()
        st.session_state["last_auto_message"] = msg
        if ok:
            st.rerun()
        else:
            st.error(f"Automatic scan failed: {msg}")
        return

    # Keep the actual scan controller lightweight, but let the browser update
    # the visible countdown every second without rerunning the Streamlit app.
    initial_remaining = max(0, int(round(remaining)))
    components.html(
        f"""
        <div class="auto-box auto-on">
          <b>🟢 AUTO SCAN ON</b><br>
          <span class="sub">Next fresh scan in <span id="live-countdown">--:--</span>.
          The dashboard refreshes itself after each scan.</span>
        </div>
        <style>
          html,body{{margin:0;padding:0;background:transparent;color:#f4f7fb;
            font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}}
          .auto-box{{box-sizing:border-box;width:100%;background:#111b2e;
            border:1px solid #2b3b56;border-left:4px solid #22c55e;
            border-radius:9px;padding:6px 10px;margin:1px 0 5px;}}
          b{{font-size:13px;font-weight:850;}}
          .sub{{display:inline-block;color:#9fb0c9;font-size:11px;margin-top:2px;}}
          #live-countdown{{font-variant-numeric:tabular-nums;font-weight:800;color:#f4f7fb;}}
        </style>
        <script>
          (() => {{
            const countdown = document.getElementById('live-countdown');
            const deadline = performance.now() + ({initial_remaining} * 1000);
            function tick() {{
              const msLeft = Math.max(0, deadline - performance.now());
              const total = Math.max(0, Math.ceil(msLeft / 1000));
              const mins = Math.floor(total / 60);
              const secs = total % 60;
              countdown.textContent = `${{mins}}:${{String(secs).padStart(2, '0')}}`;
              if (total <= 0) {{
                countdown.textContent = '0:00 — scan starting…';
              }}
            }}
            tick();
            setInterval(tick, 250);
          }})();
        </script>
        """,
        height=54,
        scrolling=False,
    )


status_mount = st.session_state.get("_scanner_status_mount")
status_context = status_mount.container() if status_mount is not None else st.container(key="scanner_auto_status_top")
with status_context:
    auto_scan_controller()

payload = load_scan()

if payload is None:
    st.markdown(
        '<div class="header"><div class="title">Momentum Scanner</div>'
        '<div class="sub">The dashboard is ready, but it does not have a scan to display yet.</div></div>',
        unsafe_allow_html=True,
    )
    st.info(
        "Click **Run Fresh Scan**. Automatic scanning will also start on its own "
        "during regular market hours while this app is open."
    )
    st.stop()

mode = payload.get("mode", "off_hours_test")
live = mode == "regular_market_session"
scan_et = payload.get("scan_time_et")

try:
    when = datetime.fromisoformat(scan_et).strftime("%a %b %d · %I:%M:%S %p ET")
except Exception:
    when = scan_et or "Unknown"

pill_cls = "green" if live else "amber"
pill_text = "● REGULAR MARKET SESSION" if live else "● OFF-HOURS PREVIEW"

payload_summary = payload.get("summary") or {}
ml_model = payload_summary.get("ml_model") or {}
ml_status = str(ml_model.get("status") or "learning")
ml_samples = int(ml_model.get("samples") or 0)
ml_days = int(ml_model.get("trading_days") or 0)
ml_auc = ml_model.get("walk_forward_auc")

if ml_model.get("validated"):
    ml_pill_cls = "green"
    ml_pill_text = f"ML VALIDATED · AUC {ml_auc if ml_auc is not None else '—'} · n={ml_samples}"
elif ml_status == "failed_validation":
    ml_pill_cls = "amber"
    ml_pill_text = f"ML NOT VALIDATED · AUC {ml_auc if ml_auc is not None else '—'} · n={ml_samples}"
elif ml_status == "skipped_off_hours":
    ml_pill_cls = "amber"
    ml_pill_text = "ML OFF-HOURS · live prediction paused"
else:
    ml_pill_cls = "blue"
    ml_pill_text = f"ML LEARNING · {ml_days}/3 days · n={ml_samples}"

st.markdown(
    f'<div class="header"><div class="title">Momentum Scanner</div>'
    '<div class="sub">Readable ranking of momentum, liquidity, VWAP position, '
    'catalysts, historical context, and validated ML continuation.</div>'
    f'<span class="pill {pill_cls}">{pill_text}</span>'
    f'<span class="pill {ml_pill_cls}">{html.escape(ml_pill_text)}</span>'
    f'<div class="sub">Last scan: {html.escape(str(when))} · '
    f'Scanner v{html.escape(str(payload.get("scanner_version","—")))}</div></div>',
    unsafe_allow_html=True,
)

summary = payload_summary
records = payload.get("candidates") or []
display_records = records[:15]
full_table_records = records[:30]
grades = summary.get("grade_counts") or {}

vals = [
    ("ANALYZED", summary.get("candidates_analyzed", len(records)), "Common-stock mover candidates"),
    ("BASE PASSES", summary.get("base_filter_passes", 0), "Passed every base filter"),
    ("NEAR MISSES", grades.get("C", 0), "Grade C: one filter short"),
    ("EXCLUDED", len(summary.get("excluded_non_common_symbols") or []), "Likely warrants / rights / units"),
]

cols = st.columns(4)
for col, (k, v, n) in zip(cols, vals):
    with col:
        st.markdown(
            f'<div class="stat"><div class="stat-k">{k}</div>'
            f'<div class="stat-v">{v}</div><div class="stat-n">{n}</div></div>',
            unsafe_allow_html=True,
        )

top = [
    c
    for c in display_records
    if c.get("setup_grade") in {"A", "B"} and c.get("passed_base_filters")
]
if not top:
    top = [c for c in display_records if c.get("setup_grade") in {"A", "B"}]
top = top[:4]

st.markdown(
    '<div class="section">🔥 Top Candidates</div>'
    '<div class="section-sub">Highest-ranked setups first. Color is backed up by text labels '
    "so you never have to rely on color alone.</div>",
    unsafe_allow_html=True,
)

if top:
    for i in range(0, len(top), 2):
        pair = st.columns(2)
        for j, c in enumerate(top[i : i + 2]):
            with pair[j]:
                st.markdown(card(c), unsafe_allow_html=True)
else:
    st.warning("No A/B candidates are available in this scan.")

st.markdown(
    """
<div class="legend-box">
  <div class="legend-title">Quick metric guide</div>
  <div class="legend-grid">
    <div class="legend-item">
      <div class="legend-term">VWAP — Volume-Weighted Average Price</div>
      <div class="legend-def">
        VWAP is the stock's average traded price for the current session, but it gives more weight to prices where more shares actually traded. That makes it more useful than a simple average for judging where most of the day's trading activity has occurred.<br><br>
        <b>Price above VWAP:</b> buyers are generally controlling the session and the stock is trading above the average price paid today. For a momentum trade, holding above VWAP is usually constructive.<br><br>
        <b>Price below VWAP:</b> the stock is trading below the day's volume-weighted average. That can mean momentum is weakening or sellers have taken control, so the scanner treats it as a warning.<br><br>
        <b>VWAP can act like support or resistance:</b> a strong stock may pull back toward VWAP, hold it, and bounce. A weak stock may rally into VWAP and get rejected.<br><br>
        <b>Distance matters:</b> being above VWAP is good, but being far above it can mean the stock is extended and more vulnerable to a pullback. A cleaner momentum entry is often a stock above VWAP without being excessively stretched away from it.<br><br>
        Example: if VWAP is <b>$10.00</b> and the stock is <b>$10.30</b>, it is about 3% above VWAP. If it is <b>$12.00</b>, it is 20% above VWAP — much stronger, but also much more extended and potentially riskier to chase.
      </div>
    </div>
    <div class="legend-item">
      <div class="legend-term">Liquidity</div>
      <div class="legend-def">
        <b>Higher liquidity:</b> easier entry/exit, tighter spreads, less slippage, and generally more reliable price action.<br><br>
        <b>Lower liquidity:</b> harder exits, wider spreads, more violent swings, and a greater chance of getting stuck in a position.<br><br>
        <b>Very high liquidity + strong volume pace:</b> usually a better-quality momentum setup because there is real participation behind the move.<br><br>
        High liquidity by itself does not make a stock bullish. The combination the scanner prefers is <b>strong price momentum + high liquidity + rising volume pace + a tight spread</b>.
      </div>
    </div>
    <div class="legend-item">
      <div class="legend-term">Time-of-Day Volume Pace</div>
      <div class="legend-def">This now uses the stock's <b>own recent intraday volume pattern</b>, not a straight-line assumption. The scanner looks at recent completed sessions and estimates what percentage of a normal day's volume this ticker usually has traded by the current clock time. That automatically accounts for the fact that volume is normally much heavier near the open and often quieter in the middle of the day.<br><br><b>1.00x:</b> about normal for this ticker at this exact time. <b>2.00x:</b> about twice the volume normally expected by now. <b>Normal Vol by Now %</b> shows the historical percentage of a typical day's volume usually completed by this time. The scanner uses a median-based baseline so one unusually huge prior day does not distort the comparison. If a ticker does not yet have enough intraday history, it temporarily falls back to the older linear calculation.</div>
    </div>
    <div class="legend-item">
      <div class="legend-term">IEX Spread</div>
      <div class="legend-def">The percentage gap between the current IEX bid and ask. A smaller spread generally means cleaner entries/exits and less immediate slippage. A wide spread can make a trade expensive even when the chart looks good. This reflects IEX quotes, not the full consolidated SIP market.</div>
    </div>
  </div>
</div>
""",
    unsafe_allow_html=True,
)

near = [c for c in display_records if c.get("setup_grade") == "C"][:8]
st.markdown(
    '<div class="section">🟡 Near Misses</div>'
    '<div class="section-sub">Close enough to watch, but one base rule still failed.</div>',
    unsafe_allow_html=True,
)
if near:
    st.dataframe(
        styled(to_df(near)),
        use_container_width=True,
        hide_index=True,
        height=min(300, 38 + 30 * len(near)),
    )
else:
    st.info("No Grade C near misses in this scan.")

st.markdown(
    '<div class="section">📊 Full Ranked Table</div>'
    '<div class="section-sub">Top 30 ranked scanner results.</div>',
    unsafe_allow_html=True,
)
df = to_df(full_table_records)
if not df.empty:
    df.insert(0, "#", range(1, len(df) + 1))
    st.dataframe(
        styled(df),
        use_container_width=True,
        hide_index=True,
        height=min(520, 38 + 30 * len(df)),
    )
else:
    st.info("No ranked candidates were logged.")

rej = Counter(r for c in display_records for r in (c.get("failed_filters") or []))
st.markdown(
    '<div class="section">🚫 Rejection Pattern</div>'
    '<div class="section-sub">Counts below come from the candidates saved in this dashboard snapshot.</div>',
    unsafe_allow_html=True,
)
if rej:
    m = max(rej.values())
    for reason, count in rej.most_common(8):
        w = max(6, int(count / m * 100))
        st.markdown(
            f'<div class="bar-row"><div class="bar-head"><span>{html.escape(reason)}</span>'
            f'<span>{count}</span></div><div class="bar-track">'
            f'<div class="bar-fill" style="width:{w}%"></div></div></div>',
            unsafe_allow_html=True,
        )
else:
    st.success("No rejection reasons were logged for the displayed candidates.")

with st.expander("What the colors mean"):
    st.markdown(
        "**Green — strong/pass.**  **Blue — watch/neutral.**  "
        "**Amber — caution/near miss.**  **Red — reject/risk.**\n\n"
        "Color is never the only cue; labels such as **PASS**, **WATCH**, "
        "**ABOVE VWAP**, and **REJECT** are shown too."
    )

with st.expander("Scanner technical output"):
    if st.session_state.get("scanner_out"):
        st.code(st.session_state["scanner_out"])
    else:
        st.caption("Run a fresh scan from this page to capture the console output here.")
