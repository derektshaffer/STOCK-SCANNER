import html
import json
import os
import runpy
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
import streamlit.components.v1 as components

from glass_theme import inject_glass_theme
from momentum_alerts import (
    alert_message,
    newly_actionable,
    newly_high_score_pullback,
    pullback_watch_message,
)
from scanner_runtime import (
    cadence_health,
    poll_scanner_process,
    start_scanner_process,
)
from analyzer_launch_runtime import (
    cancel_analyzer_process,
    poll_analyzer_process,
    start_analyzer_process,
)


st.set_page_config(
    page_title="Stock Momentum + Analyzer",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# Shared shell styling. The scanner/analyzer keep their own existing styles.
st.markdown(
    """
    <style>

    /* Hide Streamlit Cloud's built-in white header/toolbar so the dashboard
       begins at the top of the viewport. This removes Share/menu/manage-app
       controls from the normal app view. */
    header[data-testid="stHeader"],
    [data-testid="stHeader"] {
        display: none !important;
        height: 0 !important;
        min-height: 0 !important;
        visibility: hidden !important;
    }
    [data-testid="stToolbar"],
    [data-testid="stDecoration"],
    [data-testid="stStatusWidget"] {
        display: none !important;
    }
    [data-testid="stAppViewContainer"] {
        padding-top: 0 !important;
    }
    .block-container {
        padding-top: .35rem !important;
    }

    .pullback-watch-banner {
        display: flex;
        align-items: center;
        gap: 10px;
        border: 2px solid #f6b83f;
        border-left: 6px solid #ffd45c;
        background: linear-gradient(
            90deg,
            rgba(124, 75, 8, .92) 0%,
            rgba(83, 55, 12, .88) 52%,
            rgba(50, 42, 24, .88) 100%
        );
        box-shadow:
            0 0 0 1px rgba(255, 212, 92, .18),
            0 0 18px rgba(246, 184, 63, .28);
        border-radius: 12px;
        padding: 10px 14px;
        margin: 2px 0 6px;
        color: #fff7dc;
        line-height: 1.25;
    }
    .pullback-watch-icon {
        flex: 0 0 auto;
        font-size: 22px;
        filter: drop-shadow(0 0 5px rgba(255, 212, 92, .45));
    }
    .pullback-watch-title {
        color: #ffd45c;
        font-weight: 950;
        letter-spacing: .01em;
    }
    .pullback-watch-message {
        color: #fff7dc;
        font-weight: 750;
    }
    .pullback-watch-note {
        color: #ffe6a3;
        font-weight: 900;
    }
    .scanner-monitor-status {
        color: #aebdcb;
        font-size: 13px;
        line-height: 28px;
        min-height: 28px;
        margin: 0;
        white-space: nowrap;
    }

    .combined-nav-wrap {
        border: 1px solid rgba(120,150,190,.28);
        background: rgba(17,27,46,.92);
        border-radius: 14px;
        padding: 10px 14px 7px;
        margin-bottom: 12px;
    }
    .combined-nav-title {
        font-size: 12px;
        font-weight: 850;
        letter-spacing: .08em;
        text-transform: uppercase;
        color: #91a7c2;
        margin-bottom: 4px;
    }
    .combined-quick {
        border: 1px solid rgba(120,150,190,.24);
        background: rgba(17,27,46,.62);
        border-radius: 12px;
        padding: 12px 14px 10px;
        margin: 2px 0 14px;
    }
    .combined-quick-title {
        font-size: 16px;
        font-weight: 900;
        margin-bottom: 2px;
    }
    .combined-quick-sub {
        color: #91a7c2;
        font-size: 13px;
        margin-bottom: 9px;
    }
    .combined-ticker-row {
        min-height: 68px;
        display: grid;
        grid-template-columns: minmax(115px, 1.25fr) repeat(5, minmax(82px, 1fr));
        gap: 10px;
        align-items: stretch;
        border-bottom: 1px solid rgba(120,150,190,.18);
        padding: 7px 0;
    }
    .combined-ticker-symbol-wrap,
    .combined-stat {
        display: flex;
        flex-direction: column;
        justify-content: center;
        min-width: 0;
    }
    .combined-ticker-symbol {
        font-size: 24px;
        line-height: 1.05;
        font-weight: 950;
        letter-spacing: .01em;
        color: #f4f7fb;
    }
    .combined-ticker-caption {
        color: #91a7c2;
        font-size: 11px;
        font-weight: 800;
        letter-spacing: .07em;
        margin-top: 5px;
    }
    .combined-stat {
        background: rgba(22,35,58,.72);
        border: 1px solid rgba(120,150,190,.20);
        border-radius: 10px;
        padding: 8px 12px;
    }
    .combined-stat-label {
        color: #91a7c2;
        font-size: 10px;
        font-weight: 900;
        letter-spacing: .09em;
        text-transform: uppercase;
        line-height: 1;
    }
    .combined-stat-value {
        color: #f4f7fb;
        font-size: 22px;
        font-weight: 950;
        line-height: 1.1;
        margin-top: 5px;
        white-space: nowrap;
    }
    .combined-action-value {
        font-size: 16px;
    }
    .combined-stat-value.grade-a,
    .combined-stat-value.change-pos,
    .combined-stat-value.volume-strong { color: #65e98d; }
    .combined-stat-value.grade-b,
    .combined-stat-value.volume-normal { color: #7dd3fc; }
    .combined-stat-value.grade-c { color: #ffd166; }
    .combined-stat-value.grade-reject,
    .combined-stat-value.change-neg { color: #ff8181; }
    .combined-stat-value.volume-slow { color: #9fb0c9; }

    /* Technical-term hover definitions. The labels stay compact until the
       user points at one; the floating explanation does not shift the page. */
    [data-tech-tooltip] {
        text-decoration-line: underline;
        text-decoration-style: dotted;
        text-decoration-thickness: 1px;
        text-underline-offset: 3px;
        cursor: help !important;
    }
    #stock-tech-tooltip {
        position: fixed;
        display: none;
        z-index: 2147483000;
        width: max-content;
        max-width: min(360px, calc(100vw - 28px));
        box-sizing: border-box;
        padding: 10px 12px;
        border-radius: 10px;
        border: 1px solid #355071;
        background: #101b2d;
        color: #eef5ff;
        font-size: 13px;
        line-height: 1.42;
        font-weight: 600;
        letter-spacing: 0;
        text-transform: none;
        box-shadow: 0 10px 30px rgba(0,0,0,.35);
        pointer-events: none;
        white-space: normal;
    }

    /* Keep one-click scanner Analyze buttons readable without forcing every
       primary control (such as Run Fresh Scan) to be oversized. */
    [class*="st-key-combined_analyze_"] button {
        font-weight: 900 !important;
        min-height: 58px !important;
        border-radius: 12px !important;
    }

    @media (max-width: 1050px) {
        .combined-ticker-row {
            grid-template-columns: minmax(98px, 1.1fr) repeat(5, minmax(68px, 1fr));
            gap: 6px;
        }
        .combined-stat { padding: 7px 8px; }
        .combined-stat-value { font-size: 17px; }
        .combined-ticker-symbol { font-size: 20px; }
        .combined-stat-label { font-size: 9px; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


VIEWS = ("Momentum Scanner", "Stock Analyzer")
if st.session_state.get("app_view") not in VIEWS:
    st.session_state["app_view"] = "Momentum Scanner"
if "auto_scan_enabled" not in st.session_state:
    st.session_state["auto_scan_enabled"] = True
if "last_auto_scan_at" not in st.session_state:
    st.session_state["last_auto_scan_at"] = 0.0
if "_scanner_process_running" not in st.session_state:
    st.session_state["_scanner_process_running"] = False
if "_scanner_async_state" not in st.session_state:
    st.session_state["_scanner_async_state"] = None
if "last_auto_scan_started_at" not in st.session_state:
    st.session_state["last_auto_scan_started_at"] = 0.0
if "scanner_trade_horizon" not in st.session_state:
    st.session_state["scanner_trade_horizon"] = "ALL"
if "_analyzer_launch_state" not in st.session_state:
    st.session_state["_analyzer_launch_state"] = None
st.session_state["_combined_scanner_monitor_active"] = True

def _workspace_market_status():
    now_et = datetime.now(ZoneInfo("America/New_York"))
    minutes = now_et.hour * 60 + now_et.minute
    weekday = now_et.weekday() < 5

    if weekday and 4 * 60 <= minutes < 9 * 60 + 30:
        return now_et, True, "PRE-MARKET"
    if weekday and 9 * 60 + 30 <= minutes < 16 * 60:
        return now_et, True, "MARKET OPEN"
    if weekday and 16 * 60 <= minutes < 20 * 60:
        return now_et, True, "AFTER-HOURS"
    return now_et, False, "MARKET CLOSED"


def _shell_secret(name):
    try:
        return str(st.secrets[name]).strip()
    except Exception:
        return os.environ.get(name, "").strip()


def _shell_tradier_token():
    return _shell_secret("TRADIER_ACCESS_TOKEN") or _shell_secret("TRADIER_TOKEN")


def _shell_live_feed():
    feed = (_shell_secret("ALPACA_LIVE_FEED") or "iex").lower().strip()
    return feed if feed in {"iex", "sip"} else "iex"


def _shell_scanner_feed_available(now_et):
    if now_et.weekday() >= 5:
        return False
    minute = now_et.hour * 60 + now_et.minute
    if _shell_tradier_token() or _shell_live_feed() == "sip":
        return 4 * 60 <= minute < 20 * 60
    return 8 * 60 <= minute < 17 * 60


def _read_latest_scan_payload():
    path = Path("scan_logs/latest_scan.json")
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _process_momentum_alerts(payload):
    if not payload:
        return []
    scan_key = str(payload.get("scan_time_et") or "").strip()
    if not scan_key:
        return []
    previous_scan = st.session_state.get("_momentum_alert_processed_scan")
    previous_keys = st.session_state.get("_momentum_alert_current_keys") or []

    new_rows, current_keys = newly_actionable(payload, previous_keys)
    st.session_state["_momentum_alert_current_keys"] = sorted(current_keys)
    st.session_state["_momentum_alert_processed_scan"] = scan_key

    if previous_scan is None:
        return []
    if scan_key == previous_scan:
        return []

    if new_rows:
        history = list(st.session_state.get("_momentum_alert_history") or [])
        now_ts = time.time()
        for row in new_rows:
            history.append(
                {
                    "symbol": str(row.get("symbol") or "").upper(),
                    "message": alert_message(row),
                    "detected_at": now_ts,
                    "scan_time_et": scan_key,
                }
            )
        st.session_state["_momentum_alert_history"] = history[-12:]
    return new_rows


def _process_pullback_watch_alerts(payload):
    if not payload:
        return []
    scan_key = str(payload.get("scan_time_et") or "").strip()
    if not scan_key:
        return []

    previous_scan = st.session_state.get("_pullback_watch_processed_scan")
    previous_keys = st.session_state.get("_pullback_watch_current_keys") or []
    new_rows, current_keys = newly_high_score_pullback(payload, previous_keys)

    st.session_state["_pullback_watch_current_keys"] = sorted(current_keys)
    st.session_state["_pullback_watch_processed_scan"] = scan_key

    # Do not fire stale alerts just because the page was opened; wait for a
    # genuinely newer scan to show that the ticker entered the watch state.
    if previous_scan is None or scan_key == previous_scan:
        return []

    if new_rows:
        history = list(st.session_state.get("_pullback_watch_history") or [])
        now_ts = time.time()
        for row in new_rows:
            history.append(
                {
                    "symbol": str(row.get("symbol") or "").upper(),
                    "message": pullback_watch_message(row),
                    "detected_at": now_ts,
                    "scan_time_et": scan_key,
                }
            )
        st.session_state["_pullback_watch_history"] = history[-12:]
    return new_rows


def _browser_alert_control(alert_row=None, alert_kind="actionable"):
    alert_payload = None
    if alert_row:
        is_pullback_watch = alert_kind == "pullback_watch"
        message = (
            pullback_watch_message(alert_row)
            if is_pullback_watch
            else alert_message(alert_row)
        )
        alert_payload = {
            "symbol": str(alert_row.get("symbol") or "").upper(),
            "title": (
                "Pullback Watch"
                if is_pullback_watch
                else "Momentum Alert"
            ),
            "body": message
            + (
                " · Watch for pullback/reclaim confirmation in Analyzer; this is not an entry signal."
                if is_pullback_watch
                else " · Review in Analyzer; this is not an automatic buy signal."
            ),
        }
    payload_json = json.dumps(alert_payload)
    components.html(
        f"""
        <style>
          html,body{{margin:0;padding:0;background:transparent;color:#91a7c2;
            font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}}
          .wrap{{display:flex;align-items:center;gap:8px;height:28px;}}
          button{{border:1px solid rgba(120,150,190,.35);background:#111b2e;
            color:#dcecff;border-radius:8px;padding:4px 9px;font-weight:800;
            cursor:pointer;font-size:11px;}}
          #state{{font-size:11px;font-weight:700;}}
        </style>
        <div class="wrap">
          <button id="notify">🔔 Browser alerts</button>
          <span id="state"></span>
        </div>
        <script>
        (() => {{
          const p = window.parent;
          const btn = document.getElementById('notify');
          const state = document.getElementById('state');
          const hasNotifications = !!p.Notification;

          function sync() {{
            if (!hasNotifications) {{
              btn.disabled = true;
              state.textContent = 'browser notifications unavailable';
              return;
            }}
            const permission = p.Notification.permission;
            if (permission === 'granted') {{
              btn.textContent = '🔔 Browser alerts ON';
              state.textContent = '';
            }} else if (permission === 'denied') {{
              btn.textContent = '🔕 Browser alerts blocked';
              state.textContent = 'enable them in browser site settings';
            }} else {{
              btn.textContent = '🔔 Enable browser alerts';
              state.textContent = '';
            }}
          }}

          btn.onclick = async () => {{
            if (!hasNotifications) return;
            try {{ await p.Notification.requestPermission(); }} catch (_) {{}}
            sync();
          }};

          sync();
          const alertPayload = {payload_json};
          if (alertPayload) {{
            const oldTitle = p.document.title;
            p.document.title = '🚨 ' + alertPayload.symbol + ' ' + alertPayload.title;
            p.setTimeout(() => {{ p.document.title = oldTitle; }}, 30000);
            if (hasNotifications && p.Notification.permission === 'granted') {{
              try {{
                new p.Notification(
                  alertPayload.title + ' · ' + alertPayload.symbol,
                  {{body: alertPayload.body}}
                );
              }} catch (_) {{}}
            }}
          }}
        }})();
        </script>
        """,
        height=30,
        scrolling=False,
    )


previous_rendered_view = st.session_state.get("_rendered_app_view")
workspace_now_et, workspace_live, workspace_session = _workspace_market_status()
brand_col, nav_col, status_col = st.columns(
    [1.0, 2.35, 1.15],
    gap="small",
    vertical_alignment="top",
)
with brand_col:
    st.markdown(
        '<div class="workspace-brand">'
        '<span class="workspace-brand-mark">↗</span>'
        '<span class="workspace-brand-text">STOCK WORKSPACE</span>'
        '</div>',
        unsafe_allow_html=True,
    )
with nav_col:
    view = st.radio(
        "Stock Workspace",
        VIEWS,
        key="app_view",
        horizontal=True,
        label_visibility="collapsed",
    )
with status_col:
    live_class = "live" if workspace_live else ""
    live_text = "LIVE" if workspace_live else "CLOSED"
    session_class = "session" if workspace_session in {"PRE-MARKET", "AFTER-HOURS"} else ""
    st.markdown(
        '<div class="workspace-status">'
        f'<span class="workspace-status-pill {live_class}">'
        '<span class="workspace-status-dot"></span>'
        f'{live_text}</span>'
        f'<span class="workspace-status-pill {session_class}">{workspace_session}</span>'
        f'<span class="workspace-status-pill time">{workspace_now_et:%I:%M %p ET}</span>'
        '</div>',
        unsafe_allow_html=True,
    )


def _install_workspace_selector_cleanup():
    """Hide Streamlit's native radio indicator; the segment highlight is enough."""
    components.html(
        """
        <script>
        (() => {
          const p = window.parent;
          const d = p.document;

          function clean() {
            d.querySelectorAll('.st-key-app_view label').forEach((label) => {
              const input = label.querySelector('input[type="radio"]');
              if (!input) return;

              // Hide the entire native BaseWeb radio-control branch, not only
              // the input node. Streamlit currently renders the visible dot in
              // a sibling inside this branch.
              let node = input;
              while (node.parentElement && node.parentElement !== label) {
                node = node.parentElement;
              }
              if (node && node !== label) {
                node.style.setProperty('display', 'none', 'important');
                node.style.setProperty('width', '0', 'important');
                node.style.setProperty('height', '0', 'important');
                node.style.setProperty('margin', '0', 'important');
                node.style.setProperty('padding', '0', 'important');
                node.style.setProperty('gap', '0', 'important');
              }

              input.style.setProperty('position', 'absolute', 'important');
              input.style.setProperty('opacity', '0', 'important');
              input.style.setProperty('width', '1px', 'important');
              input.style.setProperty('height', '1px', 'important');
            });
          }

          const old = p.__workspaceSelectorCleanup;
          if (old && old.observer) {
            try { old.observer.disconnect(); } catch (_) {}
          }

          clean();
          const observer = new MutationObserver(() => p.requestAnimationFrame(clean));
          if (d.body) observer.observe(d.body, {childList: true, subtree: true});
          p.__workspaceSelectorCleanup = {observer};
        })();
        </script>
        """,
        height=0,
        scrolling=False,
    )


_install_workspace_selector_cleanup()

# Returning from the Analyzer should be an instant view switch. Do not let an
# overdue automatic scan run inside this same full-app rerun; give Streamlit a
# moment to finish replacing the Analyzer DOM with the cached Scanner view.
if view != previous_rendered_view:
    if view == "Momentum Scanner" and previous_rendered_view == "Stock Analyzer":
        st.session_state["_scanner_return_grace_until"] = time.time() + 2.5
    st.session_state["_rendered_app_view"] = view

@st.fragment(run_every=5)
def _workspace_scanner_monitor():
    now_et = datetime.now(ZoneInfo("America/New_York"))
    enabled = bool(st.session_state.get("auto_scan_enabled", True))
    feed_available = _shell_scanner_feed_available(now_et)
    now_ts = time.time()

    state = st.session_state.get("_scanner_async_state")
    scan_running = bool(state)

    if state:
        poll = poll_scanner_process(state)
        if poll.get("done"):
            st.session_state["_scanner_async_state"] = None
            st.session_state["_scanner_process_running"] = False
            st.session_state["last_auto_message"] = str(
                poll.get("message") or ""
            )
            st.session_state["scanner_out"] = str(
                poll.get("stdout") or ""
            )[-12000:]
            st.session_state["scanner_err"] = str(
                poll.get("stderr") or ""
            )[-6000:]
            if poll.get("runtime_seconds") is not None:
                st.session_state["scanner_last_runtime_seconds"] = poll.get(
                    "runtime_seconds"
                )
            if not poll.get("ok"):
                st.warning(
                    "Background momentum scan failed: "
                    + str(poll.get("message") or "unknown error")[:260]
                )
                scan_running = False
            else:
                st.session_state["last_auto_scan_at"] = now_ts
                # The candidate rows live outside this monitor fragment. Force
                # one full-app rerun as soon as a fresh background scan lands
                # so the visible Scanner actually loads the new snapshot.
                scan_running = False
                st.session_state["_scanner_flash_success"] = str(
                    poll.get("message") or "Fresh background scan complete."
                )
                st.rerun(scope="app")
        else:
            scan_running = True

    last_started = float(
        st.session_state.get("last_auto_scan_started_at")
        or st.session_state.get("last_auto_scan_at")
        or 0.0
    )
    last_completed = float(st.session_state.get("last_auto_scan_at") or 0.0)
    next_due_at = max(
        last_started + 120.0,
        (last_completed + 15.0) if last_completed else 0.0,
    )
    due = now_ts >= next_due_at

    if enabled and feed_available and due and not scan_running:
        started = start_scanner_process(
            alpaca_key=_shell_secret("ALPACA_API_KEY"),
            alpaca_secret=_shell_secret("ALPACA_SECRET_KEY"),
            alpaca_live_feed=_shell_live_feed(),
            tradier_token=_shell_tradier_token(),
            discovery_universe_size="1200",
            timeout_seconds=105,
        )
        if started.get("started"):
            st.session_state["_scanner_async_state"] = started
            st.session_state["_scanner_process_running"] = True
            st.session_state["last_auto_scan_started_at"] = now_ts
            scan_running = True
        elif started.get("busy"):
            # Another browser session or manual scan owns the shared lock.
            # Retry on the next monitor tick instead of moving the
            # two-minute clock forward and silently skipping a scan.
            scan_running = True
        else:
            # Configuration/startup failures should not fire on every monitor tick.
            st.session_state["last_auto_scan_started_at"] = now_ts
            st.warning(
                "Could not start background momentum scan: "
                + str(started.get("message") or "unknown error")[:260]
            )

    payload = _read_latest_scan_payload()
    new_alerts = _process_momentum_alerts(payload)
    new_pullback_watches = _process_pullback_watch_alerts(payload)

    first_alert = None
    first_alert_kind = "actionable"
    if new_alerts:
        first_alert = new_alerts[0]
    elif new_pullback_watches:
        first_alert = new_pullback_watches[0]
        first_alert_kind = "pullback_watch"

    for row in new_pullback_watches[:3]:
        st.toast(
            "PULLBACK WATCH · "
            + pullback_watch_message(row),
            icon="👀",
        )

    for row in new_alerts[:3]:
        st.toast(
            "ACTIONABLE MOMENTUM ALERT · "
            + alert_message(row)
            + " · Review in Analyzer.",
            icon="📈",
        )

    pullback_history = list(st.session_state.get("_pullback_watch_history") or [])
    if pullback_history:
        latest = pullback_history[-1]
        if time.time() - float(latest.get("detected_at") or 0) <= 600:
            _pullback_message = html.escape(str(latest.get("message") or ""))
            st.markdown(
                '<div class="pullback-watch-banner">'
                '<span class="pullback-watch-icon">🔔</span>'
                '<div>'
                '<span class="pullback-watch-title">HIGH-SCORE PULLBACK WATCH</span>'
                '<span class="pullback-watch-message"> · '
                + _pullback_message
                + '</span>'
                '<span class="pullback-watch-note"> · Early heads-up — not an entry signal.</span>'
                '</div></div>',
                unsafe_allow_html=True,
            )

    history = list(st.session_state.get("_momentum_alert_history") or [])
    if history:
        latest = history[-1]
        if time.time() - float(latest.get("detected_at") or 0) <= 600:
            st.warning(
                "🚨 **Actionable Momentum Alert:** "
                + str(latest.get("message") or "")
                + ". **Review it in Analyzer before deciding whether to trade.**"
            )

    runtime = st.session_state.get("scanner_last_runtime_seconds")
    if runtime is not None:
        health = cadence_health(runtime, 120.0)
        if health.get("status") == "overrun":
            st.warning(
                "⚠️ **2-minute cadence overrun:** "
                + str(health.get("message") or "")
                + " The next scan waits until the current one is fully finished."
            )
        elif health.get("status") == "tight":
            st.caption("⚠️ " + str(health.get("message") or ""))

    if enabled and feed_available:
        state = st.session_state.get("_scanner_async_state")
        if state:
            running_for = max(
                0,
                int(now_ts - float(state.get("started_at") or now_ts)),
            )
            monitor_status = (
                f"2-minute scanner RUNNING · {running_for}s elapsed"
                + (
                    f" · previous runtime {float(runtime):.1f}s"
                    if runtime is not None
                    else ""
                )
            )
        else:
            last_started = float(
                st.session_state.get("last_auto_scan_started_at")
                or st.session_state.get("last_auto_scan_at")
                or 0.0
            )
            remaining = max(0, int((last_started + 120.0) - now_ts))
            monitor_status = (
                f"2-minute scanner ON · next scan ~{remaining}s"
                + (
                    f" · last runtime {float(runtime):.1f}s"
                    if runtime is not None
                    else ""
                )
            )
    elif not enabled:
        monitor_status = "Background scanner paused because Auto Scan is OFF."
    else:
        monitor_status = (
            "2-minute live momentum scan is paused because the live feed is closed. "
            "Completed-daily Swing / Longer-Term discovery remains available."
        )

    status_col, alerts_col = st.columns(
        [5.2, 1.0],
        gap="small",
        vertical_alignment="center",
    )
    with status_col:
        st.markdown(
            '<div class="scanner-monitor-status">'
            + html.escape(monitor_status)
            + '</div>',
            unsafe_allow_html=True,
        )
    with alerts_col:
        _browser_alert_control(first_alert, first_alert_kind)


_workspace_scanner_monitor()


# Real render slots for the Momentum Scanner controls. scanner_app.py fills
# these later, but their position is fixed here directly under the workspace
# selector, before the one-click candidate list or any other scanner content.
if view == "Momentum Scanner":
    st.session_state["_scanner_controls_mount"] = st.empty()
    st.session_state["_scanner_status_mount"] = st.empty()


if view == "Stock Analyzer":
    st.markdown(
        """
        <style>
        /* During a Streamlit full-app rerun, stale Scanner rows can remain in
           the browser DOM until their replacements arrive. Hide those
           Scanner-only row structures immediately when Analyzer is selected so
           a background analysis never looks like a mixed Scanner/Analyzer page. */
        .combined-ticker-row,
        [class*="st-key-combined_analyze_"],
        div[data-testid="stHorizontalBlock"]:has(.combined-ticker-row) {
            display: none !important;
            pointer-events: none !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if view == "Momentum Scanner":
    st.markdown(
        """
        <style>
        /* Never leave detached Analyzer widgets visible/clickable while the
           Scanner is active. These can briefly remain in Streamlit's DOM
           during a full view switch, but their widget handlers are already
           gone, which creates "dead" buttons. */
        .st-key-saved_stocks_top,
        .st-key-analyzer_header,
        .st-key-analyzer_controls,
        .st-key-analyzer_live_fragment {
            display: none !important;
            pointer-events: none !important;
        }

        /* Compact the combined one-click scanner section without affecting
           the Stock Analyzer page. */
        .combined-quick {
            padding: 5px 9px 4px !important;
            margin: 0 0 5px !important;
            border-radius: 8px !important;
        }
        .combined-quick-title {
            font-size: 14px !important;
            line-height: 1.1 !important;
            margin-bottom: 1px !important;
        }
        .combined-quick-sub {
            font-size: 11px !important;
            line-height: 1.2 !important;
            margin-bottom: 3px !important;
        }
        .combined-ticker-row {
            min-height: 50px !important;
            height: 50px !important;
            box-sizing: border-box !important;
            gap: 5px !important;
            padding: 3px 0 !important;
        }
        .combined-ticker-symbol {
            font-size: 20px !important;
        }
        .combined-ticker-caption {
            font-size: 9px !important;
            margin-top: 2px !important;
        }
        .combined-stat {
            height: 44px !important;
            box-sizing: border-box !important;
            padding: 4px 7px !important;
            border-radius: 7px !important;
        }
        .combined-stat-label {
            font-size: 8.5px !important;
        }
        .combined-stat-value {
            font-size: 16px !important;
            margin-top: 1px !important;
        }
        .combined-action-value {
            font-size: 13px !important;
        }

        /* Align ticker cells with metric rows */
        .combined-ticker-row {
            align-items: center !important;
        }
        .combined-ticker-symbol-wrap {
            height: 100% !important;
            width: 100% !important;
            display: grid !important;
            grid-template-columns: 36px minmax(0, 1fr) !important;
            align-items: center !important;
            justify-content: stretch !important;
            column-gap: 10px !important;
        }
        .combined-ticker-symbol {
            display: inline-flex !important;
            align-items: center !important;
            justify-self: start !important;
            height: 100% !important;
            line-height: 1 !important;
            min-width: 0 !important;
        }
        .combined-rank {
            width: 36px !important;
            min-width: 36px !important;
            max-width: 36px !important;
            box-sizing: border-box !important;
            text-align: right !important;
            justify-self: end !important;
            padding: 0 !important;
            margin: 0 !important;
            color: #91a7c2 !important;
            font-size: 12px !important;
            font-weight: 900 !important;
            line-height: 1 !important;
            font-variant-numeric: tabular-nums !important;
            font-feature-settings: "tnum" 1 !important;
        }
        .combined-ticker-caption {
            display: none !important;
        }

        [class*="st-key-combined_analyze_"] {
            height: 50px !important;
            min-height: 50px !important;
            margin: 0 !important;
            padding: 3px 0 !important;
            box-sizing: border-box !important;
            display: flex !important;
            align-items: stretch !important;
        }
        [class*="st-key-combined_analyze_"] [data-testid="stButton"] {
            width: 100% !important;
            height: 44px !important;
            margin: 0 !important;
        }
        [class*="st-key-combined_analyze_"] button {
            min-height: 44px !important;
            height: 44px !important;
            margin: 0 !important;
            border-radius: 8px !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _latest_scan_age_seconds():
    path = Path("scan_logs/latest_scan.json")
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw = payload.get("scan_time_et")
        if not raw:
            return None
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        et = ZoneInfo("America/New_York")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=et)
        return max(
            0.0,
            (datetime.now(et) - dt.astimezone(et)).total_seconds(),
        )
    except Exception:
        return None


def _scan_age_text(seconds):
    if seconds is None:
        return "age unavailable"
    seconds = float(seconds)
    if seconds < 60:
        return f"{seconds:.0f}s old"
    minutes = seconds / 60.0
    if minutes < 60:
        return f"{minutes:.1f}m old"
    return f"{minutes / 60.0:.1f}h old"


def _trade_horizon_matches(row, selected):
    selected = str(selected or "ALL").upper().strip()
    if selected == "ALL":
        return True
    best_fit = str((row or {}).get("timeframe_best_fit") or "").upper().strip()
    horizons = {
        str(value).upper().strip()
        for value in ((row or {}).get("timeframe_fit_horizons") or [])
        if str(value).strip()
    }
    if selected == "MIXED":
        return best_fit == "MIXED"
    return best_fit == selected or selected in horizons


def _offhours_timeframe_candidates():
    path = Path("scan_logs/offhours_timeframe_latest.json")
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    out = []
    seen = set()
    for row in payload.get("candidates") or []:
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        swing = row.get("timeframe_swing_score")
        longer = row.get("timeframe_longer_term_score")
        out.append(
            {
                "symbol": symbol,
                "grade": str(row.get("daily_setup_grade") or "—"),
                "score": row.get("daily_discovery_score"),
                "ml_probability": None,
                "ml_validated": False,
                "ml_status": "OFF-HOURS DAILY",
                "opportunity_score": row.get("daily_discovery_score"),
                "scanner_action": row.get("daily_review_action"),
                "scanner_action_tier": "watch",
                "scanner_action_reason": row.get("daily_review_reason"),
                "timeframe_best_fit": (
                    "LONGER-TERM"
                    if (longer or 0) > (swing or 0) + 5
                    else "SWING"
                    if (swing or 0) > (longer or 0) + 5
                    else "MIXED"
                ),
                "timeframe_fit_reason": row.get("daily_review_reason"),
                "timeframe_fit_horizons": row.get("timeframe_fit_horizons") or [],
                "timeframe_intraday_score": row.get("timeframe_intraday_score"),
                "timeframe_swing_score": swing,
                "timeframe_longer_term_score": longer,
                "day_pct": row.get("day_pct"),
                "volume_pace": row.get("daily_volume_ratio"),
                "volume_pace_display": row.get("daily_volume_ratio"),
                "volume_pace_source": "daily_volume_vs_20d_average",
                "source_mode": "offhours_daily_timeframe",
            }
        )
    return out[:15]


def _latest_scan_candidates():
    path = Path("scan_logs/latest_scan.json")
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    rows = payload.get("candidates") or []
    out = []
    seen = set()
    for row in rows:
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        out.append(
            {
                "symbol": symbol,
                "grade": str(row.get("setup_grade") or "—"),
                "score": row.get("score"),
                "ml_probability": row.get("ml_continuation_prob_pct"),
                "ml_validated": bool(row.get("ml_validated")),
                "ml_status": row.get("ml_status"),
                "opportunity_score": row.get("opportunity_score"),
                "scanner_action": row.get("scanner_action"),
                "scanner_action_tier": row.get("scanner_action_tier"),
                "scanner_action_reason": row.get("scanner_action_reason"),
                "timeframe_best_fit": row.get("timeframe_best_fit"),
                "timeframe_fit_reason": row.get("timeframe_fit_reason"),
                "timeframe_fit_horizons": row.get("timeframe_fit_horizons") or [],
                "timeframe_intraday_score": row.get("timeframe_intraday_score"),
                "timeframe_swing_score": row.get("timeframe_swing_score"),
                "timeframe_longer_term_score": row.get("timeframe_longer_term_score"),
                "day_pct": row.get("day_pct"),
                "volume_pace": row.get("volume_pace"),
                "volume_pace_display": row.get("volume_pace_display"),
                "volume_pace_source": (
                    row.get("volume_pace_display_source")
                    or row.get("volume_pace_source")
                ),
            }
        )
    return out[:15]
def _cancel_analyzer_launch():
    # Scanner -> Analyzer launches now hand off directly to the Analyzer page,
    # so the active process normally lives under the Analyzer bootstrap key.
    # Keep the legacy key as a fallback so an older browser session can still
    # cancel cleanly after a deploy.
    state=(
        st.session_state.get("_analyzer_bootstrap_launch_state")
        or st.session_state.get("_analyzer_launch_state")
    )
    if state:
        result=cancel_analyzer_process(state)
        st.session_state["_analyzer_cancel_notice"]=result.get("message")
    st.session_state["_analyzer_bootstrap_launch_state"]=None
    st.session_state["_analyzer_launch_state"]=None
    st.session_state["_analyzer_loading"]=False
    st.session_state.pop("_analyzer_background_request_symbol",None)


def _toggle_analyzer_launch(symbol):
    """Open Analyzer immediately, then finish the deep analysis in background."""
    symbol=str(symbol or "").upper().strip()
    if not symbol:
        return

    state=(
        st.session_state.get("_analyzer_bootstrap_launch_state")
        or st.session_state.get("_analyzer_launch_state")
        or {}
    )
    active_process=state.get("process")
    active=bool(active_process is not None and active_process.poll() is None)
    active_symbol=str(state.get("symbol") or "").upper().strip()

    if active and active_symbol==symbol:
        _cancel_analyzer_launch()
        return

    # Only one expensive Analyzer launch at a time. Clicking a different
    # ticker cancels the prior launch and immediately starts the new one.
    if active:
        cancel_analyzer_process(state)
        st.session_state["_analyzer_bootstrap_launch_state"]=None
        st.session_state["_analyzer_launch_state"]=None

    launch=start_analyzer_process(
        symbol,
        alpaca_key=_shell_secret("ALPACA_API_KEY"),
        alpaca_secret=_shell_secret("ALPACA_SECRET_KEY"),
        alpaca_live_feed=_shell_live_feed(),
        tradier_token=_shell_tradier_token(),
        timeout_seconds=180,
    )
    if not launch.get("started"):
        st.session_state["_analyzer_launch_error"]=launch.get("message") or "Could not start Analyzer."
        st.session_state["_analyzer_bootstrap_launch_state"]=None
        st.session_state["_analyzer_launch_state"]=None
        return

    # IMPORTANT: switch views now, not after analyze() finishes. The previous
    # flow deliberately left the user on Scanner until the worker completed,
    # which made the Analyze button look frozen for tens of seconds.
    st.session_state.pop("_analyzer_launch_error",None)
    st.session_state.pop("_analyzer_cancel_notice",None)
    st.session_state["_analyzer_bootstrap_launch_state"]=launch
    st.session_state["_analyzer_launch_state"]=None
    st.session_state["ticker"]=symbol
    st.session_state["ticker_search_request"]=symbol
    st.session_state["_analyzer_loading"]=True
    st.session_state.pop("ticker_picker",None)
    st.session_state["app_view"]="Stock Analyzer"


def _poll_analyzer_launch():
    state=st.session_state.get("_analyzer_launch_state")
    if not state:
        return
    outcome=poll_analyzer_process(state)
    if not outcome.get("done"):
        return

    st.session_state["_analyzer_launch_state"]=None
    if not outcome.get("ok"):
        st.session_state["_analyzer_launch_error"]=outcome.get("message") or "Analyzer failed."
        return

    symbol=str(outcome.get("symbol") or "").upper().strip()
    result=outcome.get("result")
    st.session_state["ticker"]=symbol
    st.session_state["ticker_search_request"]=symbol
    st.session_state.pop("ticker_picker",None)
    st.session_state["result"]=result
    st.session_state.pop("_analyzer_launch_error",None)
    st.session_state["app_view"]="Stock Analyzer"
    st.rerun(scope="app")


def _fmt_num(value, pattern, fallback="—"):
    try:
        return pattern.format(float(value))
    except (TypeError, ValueError):
        return fallback


def _grade_class(grade):
    grade = str(grade or "").upper()
    if grade == "A":
        return "grade-a"
    if grade == "B":
        return "grade-b"
    if grade == "C":
        return "grade-c"
    return "grade-reject"


def _change_class(value):
    try:
        return "change-pos" if float(value) >= 0 else "change-neg"
    except (TypeError, ValueError):
        return ""


def _volume_class(value):
    try:
        pace = float(value)
    except (TypeError, ValueError):
        return "volume-slow"
    if pace >= 1.5:
        return "volume-strong"
    if pace >= 1.0:
        return "volume-normal"
    return "volume-slow"


def _action_display(row):
    action = str(row.get("scanner_action") or "WATCH").upper()
    tier = str(row.get("scanner_action_tier") or "watch").lower()
    if row.get("source_mode") == "offhours_daily_timeframe":
        return action, "grade-b", "DAILY REVIEW"
    cls = {
        "ready": "volume-strong",
        "breakout": "grade-b",
        "pullback": "grade-c",
        "avoid": "change-neg",
        "caution": "grade-c",
        "watch": "volume-normal",
    }.get(tier, "volume-normal")

    label = "ACTION"
    if row.get("ml_validated"):
        try:
            probability = float(row.get("ml_probability"))
            label += f" · ML {probability:.0f}%"
        except (TypeError, ValueError):
            pass
    return action, cls, label


def _volume_pace_display(row):
    value = (
        row.get("volume_pace_display")
        if row.get("volume_pace_display") is not None
        else row.get("volume_pace")
    )
    try:
        pace = float(value)
    except (TypeError, ValueError):
        source = str(row.get("volume_pace_source") or "")
        if "profile_unavailable" in source:
            return "N/A", None
        return "—", None
    if pace >= 100:
        return "100x+", pace
    if pace >= 10:
        return f"{pace:.1f}x", pace
    return f"{pace:.2f}x", pace


if view == "Momentum Scanner":
    launch_error = st.session_state.pop("_analyzer_launch_error", None)
    if launch_error:
        st.error(f"Could not analyze the selected ticker: {launch_error}")
    cancel_notice = st.session_state.pop("_analyzer_cancel_notice", None)
    if cancel_notice:
        st.toast(cancel_notice, icon="✕")

    _analyzer_poll_every = 1 if st.session_state.get("_analyzer_launch_state") else None

    @st.fragment(run_every=_analyzer_poll_every)
    def _analyzer_launch_monitor():
        _poll_analyzer_launch()

    _analyzer_launch_monitor()

    offhours_mode = not workspace_live
    offhours_candidates = (
        _offhours_timeframe_candidates()
        if offhours_mode
        else []
    )
    candidates = offhours_candidates or _latest_scan_candidates()
    trade_horizon = st.session_state.get("scanner_trade_horizon", "ALL")
    candidates = [
        row for row in candidates
        if _trade_horizon_matches(row, trade_horizon)
    ]
    latest_scan_age = _latest_scan_age_seconds()
    # Auto-scan targets every two minutes. During a live session, allow a
    # two-minute grace window and prevent one-click analysis of stale rows.
    latest_scan_stale = bool(
        workspace_live
        and not offhours_candidates
        and (
            latest_scan_age is None
            or latest_scan_age > 4 * 60
        )
    )
    if latest_scan_stale and candidates:
        st.warning(
            "The displayed scanner snapshot is stale ("
            + _scan_age_text(latest_scan_age)
            + "). A fresh scan is due; Analyze buttons are temporarily disabled "
            "so an old setup cannot be mistaken for a current one."
        )

    if offhours_mode and trade_horizon == "INTRADAY":
        st.caption(
            "Short term (intraday) candidates require live market data. "
            "The completed-daily off-hours screen only contains medium-term "
            "(swing) and long-term candidates."
        )

    if candidates:
        if offhours_candidates:
            st.caption(
                "Showing the latest completed-daily Swing / Longer-Term discovery. "
                "These are research candidates, not live entry signals."
            )
        # Make each row useful at a glance: ticker, grade, score/review cue,
        # today's completed move and volume context, with Analyze at the end.
        for idx, row in enumerate(candidates):
            symbol = row["symbol"]
            grade = row.get("grade") or "—"
            fit = str(row.get("timeframe_best_fit") or "—")
            fit_display = "MULTIPLE TIMEFRAMES" if fit == "MIXED" else fit
            grade_fit = f"{grade} · {fit_display}" if fit_display != "—" else grade
            score_text = _fmt_num(row.get("score"), "{:.0f}")
            score_label = (
                "Trend Candidate Score"
                if row.get("source_mode") == "offhours_daily_timeframe"
                else "Score"
            )
            action_text, action_cls, action_label = _action_display(row)
            day_text = _fmt_num(row.get("day_pct"), "{:+.1f}%")
            volume_text, volume_value = _volume_pace_display(row)
            grade_cls = _grade_class(grade)
            change_cls = _change_class(row.get("day_pct"))
            volume_cls = _volume_class(volume_value)

            left, right = st.columns([7.2, 1.55], vertical_alignment="center")
            with left:
                st.markdown(
                    f'<div class="combined-ticker-row">'
                    f'  <div class="combined-ticker-symbol-wrap">'
                    f'    <div class="combined-rank">{idx + 1}.</div>'
                    f'    <div class="combined-ticker-symbol">{symbol}</div>'
                    f'  </div>'
                    f'  <div class="combined-stat" title="{html.escape(str(row.get("timeframe_fit_reason") or ""))}">'
                    f'    <div class="combined-stat-label">Grade · Best Fit</div>'
                    f'    <div class="combined-stat-value {grade_cls}">{html.escape(grade_fit)}</div>'
                    f'  </div>'
                    f'  <div class="combined-stat">'
                    f'    <div class="combined-stat-label">{score_label}</div>'
                    f'    <div class="combined-stat-value">{score_text}</div>'
                    f'  </div>'
                    f'  <div class="combined-stat" title="{html.escape(str(row.get("scanner_action_reason") or ""))}">'
                    f'    <div class="combined-stat-label">{action_label}</div>'
                    f'    <div class="combined-stat-value combined-action-value {action_cls}">{action_text}</div>'
                    f'  </div>'
                    f'  <div class="combined-stat">'
                    f'    <div class="combined-stat-label">Today</div>'
                    f'    <div class="combined-stat-value {change_cls}">{day_text}</div>'
                    f'  </div>'
                    f'  <div class="combined-stat">'
                    f'    <div class="combined-stat-label">'
                    f'{"Daily Vol / Avg" if row.get("source_mode") == "offhours_daily_timeframe" else "Volume Pace"}'
                    f'</div>'
                    f'    <div class="combined-stat-value {volume_cls}">{volume_text}</div>'
                    f'  </div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            with right:
                _launch_state=(
                    st.session_state.get("_analyzer_bootstrap_launch_state")
                    or st.session_state.get("_analyzer_launch_state")
                    or {}
                )
                _launch_process=_launch_state.get("process")
                _launch_active=bool(
                    _launch_process is not None
                    and _launch_process.poll() is None
                )
                _this_running=bool(
                    _launch_active
                    and str(_launch_state.get("symbol") or "").upper()==symbol
                )
                st.button(
                    f"Cancel {symbol}" if _this_running else f"Analyze {symbol}",
                    key=f"combined_analyze_{idx}_{symbol}",
                    type="secondary" if _this_running else "primary",
                    use_container_width=True,
                    disabled=bool(latest_scan_stale and not _this_running),
                    help=(
                        "Waiting for a fresh scanner snapshot."
                        if latest_scan_stale and not _this_running
                        else None
                    ),
                    on_click=_toggle_analyzer_launch,
                    args=(symbol,),
                )
    else:
        st.caption(
            "No scanner candidates are available yet. Live momentum scanning runs "
            "during supported market-data hours; the completed-daily Swing / "
            "Longer-Term scan runs after each regular session."
        )


# Both legacy child apps call st.set_page_config themselves. In the combined
# shell we already configured the page above, so temporarily make those child
# calls a no-op. This preserves analyzer_app.py and scanner_app.py as usable
# standalone entrypoints while avoiding duplicate page-config calls here.
_original_set_page_config = st.set_page_config
st.set_page_config = lambda *args, **kwargs: None
try:
    if view == "Momentum Scanner":
        runpy.run_path(str(Path(__file__).with_name("scanner_app.py")), run_name="__main__")
    else:
        from analyzer_bootstrap import run as run_analyzer

        run_analyzer()
finally:
    st.set_page_config = _original_set_page_config

# Apply the shared Option-B glass theme after the active child app has rendered
# its own legacy styles. This lets the theme override presentation without
# changing scanner/analyzer behavior.
inject_glass_theme()


# One shared tooltip layer for both child apps.  The analyzer already contains
# a full glossary; these are short hover versions of its most common terms.
TECHNICAL_TOOLTIPS = {
    "VOLUME PACE": "Current volume compared with what this stock normally trades by this time of day. 1.0x is about normal; 2.0x is about twice normal.",
    "VOL PACE": "Current volume compared with what this stock normally trades by this time of day. 1.0x is about normal; 2.0x is about twice normal.",
    "TOD VOL PACE": "Time-of-day volume pace: current volume versus the stock's normal volume by this exact time of day.",
    "NORMAL VOL BY NOW": "The share of a normal day's volume this ticker historically tends to have completed by the current time.",
    "VWAP": "Volume-Weighted Average Price: the session's average traded price weighted by volume. Holding above it is often constructive for intraday momentum.",
    "VWAP PRICE": "The current session's Volume-Weighted Average Price—the average traded price with heavier-volume prices given more weight.",
    "VWAP EXTENSION": "How far the current price is above or below VWAP. A large positive extension can mean the stock is becoming risky to chase.",
    "LIQUIDITY": "How easily shares can be bought or sold without moving price much. Higher liquidity usually means tighter spreads and less slippage.",
    "LIVE SPREAD": "The percentage gap between the current live bid and ask. With Tradier configured this is consolidated market data; smaller is generally better for entries, exits, and slippage.",
    "BID/ASK SPREAD": "The gap between the best current buying price and selling price. Wider spreads generally increase trading friction and slippage.",
    "DOLLAR VOLUME": "Share volume multiplied by price. It estimates how much money is changing hands and helps compare liquidity between stocks.",
    "FROM HIGH": "How far the current price has fallen from today's session high. A small value means price is still trading close to its high.",
    "5 MIN": "Price momentum over roughly the last five minutes. Positive values show short-term upward movement; negative values show weakening.",
    "15 MIN": "Price momentum over roughly the last fifteen minutes, giving a broader view than the five-minute reading.",
    "MOMENTUM": "The speed and persistence of price movement. Stronger momentum means price is moving more decisively in one direction.",
    "SETUP SCORE": "A combined technical-quality score using factors such as momentum, VWAP, volume, liquidity and price location. It is not a probability of profit.",
    "SCORE": "A combined technical-quality score used to rank the scanner's live setups. Higher is stronger, but it is not a guaranteed probability of success.",
    "TREND CANDIDATE SCORE": "An off-hours ranking score for how strongly a stock matches the scanner's multi-day trend criteria. It is a discovery/ranking score, not entry readiness or a probability of profit.",
    "ACTION": "A scanner-level review cue. ANALYZE NOW means the setup deserves immediate deeper review in Analyzer; it is not a trade instruction.",
    "ANALYZE NOW": "The scanner's strongest review cue. It means the setup deserves immediate Analyzer review, not that you should automatically enter a trade.",
    "WAIT PULLBACK": "Momentum may remain attractive, but the current price looks too stretched to chase. Wait for a better pullback area and confirm it in Analyzer.",
    "BREAKOUT WATCH": "Price is pressing the session high with constructive momentum. Wait for breakout confirmation and check the exact trigger in Analyzer.",
    "ML 60M": "Validated XGBoost estimate of the chance this scanner setup will be at least 3% higher 60 minutes later. It stays in Learning mode until chronological validation passes.",
    "OPPORTUNITY": "Combined ranking score using 70% of the existing scanner score and 30% of validated ML probability. ML has no ranking weight until validation passes.",
    "GRADE": "A quick quality tier based on the scanner's rules. A is strongest, followed by B and C; the grade is not a guarantee of profit.",
    "DAY RANGE": "The lowest and highest prices traded during the current session.",
    "BASE SETUP": "The analyzer's overall read of the current technical setup before considering a specific entry, stop and targets.",
    "ENTRY ZONE": "A price range where the analyzer sees a more favorable balance of potential upside versus downside rather than one exact entry penny.",
    "STOP / INVALIDATION": "The price area where the trade idea is considered wrong or materially weakened, based on technical structure and volatility.",
    "TARGET 1": "The first, more conservative profit objective, usually based on nearby resistance or another meaningful technical level.",
    "TARGET 2": "A second profit objective beyond Target 1, typically requiring stronger continuation.",
    "STRETCH": "A more aggressive upside target that generally requires unusually strong continuation and should be treated as lower probability.",
    "STRETCH TARGET": "A more aggressive upside target that generally requires unusually strong continuation and should be treated as lower probability.",
    "REWARD / RISK": "Potential reward divided by potential loss from the entry to the stop. For example, 2:1 means two dollars of potential reward for each dollar at risk.",
    "SUPPORT": "A price area where buying has previously been strong enough to slow or reverse a decline. It is a zone, not a guaranteed floor.",
    "RESISTANCE": "A price area where selling has previously been strong enough to slow or reverse a rise.",
    "BREAKOUT": "A move through an important resistance level. Higher-quality breakouts are usually supported by strong volume and price holding above the level.",
    "BREAKOUT CONFIRMATION": "Evidence that a breakout is holding, such as sustained price above the level, strong volume or a successful retest.",
    "ATR": "Average True Range: a measure of how much the stock normally moves. Higher ATR means wider normal price swings.",
    "ATR %": "ATR expressed as a percentage of the stock price, making volatility easier to compare across different-priced stocks.",
    "MFE": "Maximum Favorable Excursion: the largest favorable move seen after a comparable historical setup.",
    "MAE": "Maximum Adverse Excursion: the largest move against the position after a comparable historical setup.",
    "CATALYST": "A news event or company development that can materially change demand for the stock, such as earnings, FDA news, a contract or financing.",
    "FLOAT": "The number of shares readily available for public trading. Lower-float stocks can move more sharply because fewer shares are available.",
    "MARKET CAP": "Share price multiplied by shares outstanding—an estimate of the market value of the company's equity.",
    "SHORT INTEREST": "Shares that have been sold short and remain open. High short interest can add squeeze potential but can also reflect bearish conviction.",
    "SHORT FLOAT": "Short interest expressed as a percentage of the publicly tradable float.",
    "WARRANT OVERHANG": "Potential selling or dilution pressure from outstanding warrants that may be exercised if the stock rises enough.",
    "DILUTION": "An increase in shares outstanding. New shares can reduce existing owners' proportional stake and sometimes pressure price.",
}


def _install_technical_tooltips():
    tooltip_json = json.dumps(TECHNICAL_TOOLTIPS)
    components.html(
        f"""
        <script>
        (() => {{
          const p = window.parent;
          const d = p.document;
          const tips = {tooltip_json};
          const selector = '.combined-stat-label, .mk, .k, .legend-term';

          const normalize = (text) => String(text || '')
            .replace(/\\s+/g, ' ')
            .trim()
            .toUpperCase();

          function applyTips() {{
            d.querySelectorAll(selector).forEach((el) => {{
              const key = normalize(el.textContent);
              const text = tips[key];
              if (text) {{
                el.setAttribute('data-tech-tooltip', text);
                el.setAttribute('tabindex', '0');
                el.setAttribute('aria-label', `${{el.textContent.trim()}}. ${{text}}`);
              }}
            }});
          }}

          let box = d.getElementById('stock-tech-tooltip');
          if (!box) {{
            box = d.createElement('div');
            box.id = 'stock-tech-tooltip';
            box.setAttribute('role', 'tooltip');
            d.body.appendChild(box);
          }}

          function show(el) {{
            const text = el && el.getAttribute('data-tech-tooltip');
            if (!text) return;
            box.textContent = text;
            box.style.display = 'block';

            const r = el.getBoundingClientRect();
            const pad = 10;
            const width = box.offsetWidth;
            const height = box.offsetHeight;
            let left = r.left;
            let top = r.bottom + 8;

            if (left + width > p.innerWidth - pad) left = p.innerWidth - width - pad;
            if (left < pad) left = pad;
            if (top + height > p.innerHeight - pad) top = r.top - height - 8;
            if (top < pad) top = pad;

            box.style.left = `${{Math.round(left)}}px`;
            box.style.top = `${{Math.round(top)}}px`;
          }}

          function hide() {{
            box.style.display = 'none';
          }}

          const old = p.__stockTechnicalTooltips;
          if (old) {{
            try {{ old.observer.disconnect(); }} catch (_) {{}}
            try {{ d.removeEventListener('mouseover', old.over); }} catch (_) {{}}
            try {{ d.removeEventListener('mousemove', old.move); }} catch (_) {{}}
            try {{ d.removeEventListener('mouseout', old.out); }} catch (_) {{}}
            try {{ d.removeEventListener('focusin', old.focusin); }} catch (_) {{}}
            try {{ d.removeEventListener('focusout', old.focusout); }} catch (_) {{}}
          }}

          // A number of analyzer labels are flex/grid items whose element box
          // stretches well past the visible word. Hit-test the rendered text
          // itself so hovering blank space to the right does not open a tip.
          function pointerIsOverRenderedText(el, event) {{
            if (!el || event.clientX == null || event.clientY == null) return false;
            const range = d.createRange();
            range.selectNodeContents(el);
            const rects = Array.from(range.getClientRects());
            if (range.detach) range.detach();
            return rects.some((r) =>
              event.clientX >= r.left && event.clientX <= r.right &&
              event.clientY >= r.top && event.clientY <= r.bottom
            );
          }}

          let activeHover = null;
          const over = (event) => {{
            const el = event.target.closest && event.target.closest('[data-tech-tooltip]');
            if (el && pointerIsOverRenderedText(el, event)) {{
              activeHover = el;
              show(el);
            }} else if (activeHover) {{
              activeHover = null;
              hide();
            }}
          }};
          const move = over;
          const out = (event) => {{
            const el = event.target.closest && event.target.closest('[data-tech-tooltip]');
            if (el && (!event.relatedTarget || !el.contains(event.relatedTarget))) {{
              activeHover = null;
              hide();
            }}
          }};
          const focusin = (event) => {{
            const el = event.target.closest && event.target.closest('[data-tech-tooltip]');
            if (el) show(el);
          }};
          const focusout = (event) => {{
            const el = event.target.closest && event.target.closest('[data-tech-tooltip]');
            if (el) hide();
          }};

          d.addEventListener('mouseover', over);
          d.addEventListener('mousemove', move);
          d.addEventListener('mouseout', out);
          d.addEventListener('focusin', focusin);
          d.addEventListener('focusout', focusout);

          let queued = false;
          const observer = new MutationObserver(() => {{
            if (queued) return;
            queued = true;
            p.requestAnimationFrame(() => {{
              queued = false;
              applyTips();
            }});
          }});
          if (d.body) observer.observe(d.body, {{childList: true, subtree: true}});

          p.__stockTechnicalTooltips = {{observer, over, move, out, focusin, focusout}};
          applyTips();
        }})();
        </script>
        """,
        height=0,
        scrolling=False,
    )


_install_technical_tooltips()
