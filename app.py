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
.auto-box{background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:10px 13px;margin:4px 0 12px}
.auto-on{border-left:4px solid var(--green)}
.auto-wait{border-left:4px solid var(--amber)}
.auto-off{border-left:4px solid var(--muted)}
.bar-row{margin:9px 0 13px}
.bar-head{display:flex;justify-content:space-between;font-size:13px;font-weight:750}
.bar-track{height:9px;background:#17243a;border-radius:999px;overflow:hidden;
  margin-top:5px;border:1px solid #263750}
.bar-fill{height:100%;background:linear-gradient(90deg,#f59e0b,#ef4444);border-radius:999px}
div[data-testid="stDataFrame"]{border:1px solid var(--line);border-radius:12px;overflow:hidden}
.stButton button{border-radius:10px;font-weight:850;min-height:44px}
@media(max-width:900px){.title{font-size:28px}.ticker{font-size:27px}.grid{grid-template-columns:1fr}.legend-grid{grid-template-columns:1fr}}
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
    {metric("VOLUME PACE",f(c.get("volume_pace"),2,"x"),"pos" if (c.get("volume_pace") or 0)>=1.5 else "muted")}
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
                "Price": c.get("price"),
                "Day %": c.get("day_pct"),
                "5m %": c.get("momentum_5m"),
                "15m %": c.get("momentum_15m"),
                "Vol Pace": c.get("volume_pace"),
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

    return (
        df.style.map(score_style, subset=["Score"])
        .map(grade_style, subset=["Grade"])
        .map(vwap_style, subset=["VWAP Status"])
        .format(
            {
                "Score": "{:.1f}",
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


control_a, control_b, control_c = st.columns([2.2, 1.2, 1.2])
with control_a:
    st.toggle(
        "Auto scan every 5 minutes",
        key="auto_scan_enabled",
        help="Runs while this dashboard tab/session is active. Automatic scans pause when the regular US market is closed.",
    )
with control_b:
    clicked = st.button("▶ Run Fresh Scan", type="primary", use_container_width=True)
with control_c:
    market_open, now_et = market_is_open()
    market_label = "🟢 MARKET OPEN" if market_open else "🟡 MARKET CLOSED"
    st.markdown(
        f'<div class="auto-box {"auto-on" if market_open else "auto-wait"}">'
        f'<b>{market_label}</b><br><span class="sub">{now_et:%I:%M %p ET}</span></div>',
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

    mins = int(remaining // 60)
    secs = int(remaining % 60)
    st.markdown(
        f'<div class="auto-box auto-on"><b>🟢 AUTO SCAN ON</b><br>'
        f'<span class="sub">Next fresh scan in about {mins}:{secs:02d}. '
        'The dashboard refreshes itself after each scan.</span></div>',
        unsafe_allow_html=True,
    )


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

st.markdown(
    f'<div class="header"><div class="title">Momentum Scanner</div>'
    '<div class="sub">Readable ranking of momentum, liquidity, VWAP position, '
    'catalysts and historical context.</div>'
    f'<span class="pill {pill_cls}">{pill_text}</span>'
    f'<div class="sub">Last scan: {html.escape(str(when))} · '
    f'Scanner v{html.escape(str(payload.get("scanner_version","—")))}</div></div>',
    unsafe_allow_html=True,
)

summary = payload.get("summary") or {}
records = payload.get("candidates") or []
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
    for c in records
    if c.get("setup_grade") in {"A", "B"} and c.get("passed_base_filters")
]
if not top:
    top = [c for c in records if c.get("setup_grade") in {"A", "B"}]
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
      <div class="legend-term">Volume Pace</div>
      <div class="legend-def">Current volume compared with the stock's normal volume expected by this time of day. 1.00x is roughly normal pace; 2.00x means roughly twice the usual pace. Strong volume pace helps confirm that a price move has broad participation instead of being caused by only a small number of trades.</div>
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

near = [c for c in records if c.get("setup_grade") == "C"][:8]
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
        height=min(390, 42 + 35 * len(near)),
    )
else:
    st.info("No Grade C near misses in this scan.")

st.markdown(
    '<div class="section">📊 Full Ranked Table</div>'
    '<div class="section-sub">Complete at-a-glance comparison of the saved watchlist.</div>',
    unsafe_allow_html=True,
)
df = to_df(records)
if not df.empty:
    st.dataframe(
        styled(df),
        use_container_width=True,
        hide_index=True,
        height=min(720, 42 + 35 * len(df)),
    )
else:
    st.info("No ranked candidates were logged.")

rej = Counter(r for c in records for r in (c.get("failed_filters") or []))
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
