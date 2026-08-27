import html, os, subprocess, sys, json, urllib.request, urllib.error, time
from datetime import datetime, timezone
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Single Stock Analyzer", page_icon="📈", layout="wide")

_COMBINED_WORKSPACE = st.session_state.get("app_view") == "Stock Analyzer"

# Make Streamlit Cloud secrets available to the analyzer module without
# placing credentials in GitHub. This deliberately happens BEFORE importing
# stock_analyzer because that module reads its configuration at import time.
def _load_streamlit_secrets_into_env():
    required = ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")
    optional = (
        "ALPACA_LIVE_FEED",
        "ALPACA_HISTORICAL_FEED",
        "TRADIER_ACCESS_TOKEN",
        "TRADIER_TOKEN",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
    )

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
from analyzer_v2_ui import render_v2_decision
from position_exit import build_position_exit_plan, merge_live_position_metrics
from live_market_stream import get_live_overlay

AUTO_REFRESH_SECONDS = max(30, int(os.environ.get("ANALYZER_REFRESH_SECONDS", "60") or 60))

def _result_age_seconds(result):
    if not result or not result.get("as_of"):
        return None
    try:
        dt=datetime.fromisoformat(str(result["as_of"]))
        if dt.tzinfo is None:
            dt=dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc)-dt.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None

def _age_text(seconds):
    if seconds is None:
        return "time unavailable"
    seconds=float(seconds)
    if seconds < 2:
        return "just now"
    if seconds < 60:
        return f"{seconds:.0f}s ago"
    minutes=seconds/60
    if minutes < 60:
        return f"{minutes:.1f}m ago"
    return f"{minutes/60:.1f}h ago"

@st.cache_data(ttl=21600, show_spinner=False)
def load_active_us_equities():
    """Load active US equities from Alpaca for ticker/company autocomplete.

    Alpaca paper keys and live keys use different trading API hosts.
    Try both automatically so the user does not have to change secrets.
    """
    key = os.environ.get("ALPACA_API_KEY", "").strip()
    secret = os.environ.get("ALPACA_SECRET_KEY", "").strip()
    if not key or not secret:
        return []

    headers = {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Accept": "application/json",
        "User-Agent": "single-stock-analyzer/1.0",
    }

    endpoints = [
        "https://paper-api.alpaca.markets/v2/assets?status=active&asset_class=us_equity",
        "https://api.alpaca.markets/v2/assets?status=active&asset_class=us_equity",
    ]

    assets = None
    last_error = None

    for url in endpoints:
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if isinstance(payload, list) and payload:
                assets = payload
                break
        except Exception as exc:
            last_error = str(exc)

    if not isinstance(assets, list):
        # Store a diagnostic for display without exposing credentials.
        st.session_state["_ticker_asset_load_error"] = last_error or "No asset data returned."
        return []

    st.session_state.pop("_ticker_asset_load_error", None)

    choices = []
    seen = set()
    for asset in assets:
        symbol = str(asset.get("symbol") or "").strip().upper()
        name = str(asset.get("name") or "").strip()
        status = str(asset.get("status") or "").lower()
        tradable = asset.get("tradable")

        if not symbol or symbol in seen or status not in ("", "active"):
            continue

        # Keep listed equities even when temporarily non-tradable, because
        # the analyzer may still be useful for research.
        seen.add(symbol)
        choices.append(f"{symbol} — {name}" if name else symbol)

    choices.sort(key=lambda x: x.split(" — ", 1)[0])
    return choices


def _ticker_from_choice(value):
    if value is None:
        return ""
    text = str(value).strip()
    return text.split(" — ", 1)[0].strip().upper()


def search_equity_choices(searchterm: str):
    """Return ranked ticker/company suggestions as the user types."""
    choices = load_active_us_equities()
    term = str(searchterm or "").strip().lower()
    if not term:
        current = str(st.session_state.get("ticker", "SDOT") or "SDOT").upper().strip()
        preferred = [x for x in choices if x.startswith(current + " — ") or x == current]
        return preferred[:1] + [x for x in choices[:9] if x not in preferred]

    ranked = []
    for choice in choices:
        symbol, _, name = choice.partition(" — ")
        s = symbol.lower()
        n = name.lower()

        # Strongest matches: ticker prefix/exact, then company-name prefix,
        # then substring matches.
        if s == term:
            rank = (0, len(symbol))
        elif s.startswith(term):
            rank = (1, len(symbol))
        elif n.startswith(term):
            rank = (2, len(name))
        elif term in s:
            rank = (3, s.index(term), len(symbol))
        elif term in n:
            rank = (4, n.index(term), len(name))
        else:
            continue

        ranked.append((rank, choice))

    ranked.sort(key=lambda x: x[0])
    return [choice for _, choice in ranked[:15]]


TERM_GLOSSARY = {
    "ATR": "Average True Range measures how much a stock normally moves over a recent period. A larger ATR means wider normal price swings, so entries and stops usually need more room.",
    "ATR %": "ATR expressed as a percentage of the stock price. This makes volatility easier to compare across stocks with very different share prices.",
    "VWAP": "Volume-Weighted Average Price is the session's average traded price weighted by volume. Price holding above VWAP is generally constructive for intraday momentum; below VWAP can signal weakness. Being far above VWAP can also mean the stock is extended and risky to chase.",
    "VWAP extension": "The percentage distance between the current price and VWAP. A positive value means price is above VWAP; a large positive value can indicate chase risk. A negative value means price is below VWAP.",
    "Volume pace": "Current trading volume compared with the amount of volume the stock would normally be expected to have by this time of day. 1.0x is roughly normal, while 3.0x means trading activity is running at about three times normal pace.",
    "Relative volume": "Current volume compared with a normal historical baseline. High relative volume suggests unusually strong participation and can make a move more meaningful.",
    "Liquidity": "How easily shares can be bought or sold without moving the price much. Higher liquidity usually means tighter spreads, less slippage, cleaner entries/exits, and lower risk of getting stuck.",
    "Dollar volume": "Share volume multiplied by price. It estimates how much money is changing hands and is often more useful than share volume alone when comparing liquidity across stocks.",
    "Bid": "The highest current price a buyer is offering to pay for a share.",
    "Ask": "The lowest current price a seller is willing to accept for a share.",
    "Bid/ask spread": "The gap between the best bid and ask. A tight spread generally means better liquidity and less immediate trading friction; a wide spread can cause significant slippage.",
    "Slippage": "The difference between the price you expect to trade at and the price you actually receive. Slippage tends to be worse in thinly traded stocks or when spreads are wide.",
    "Support": "A price area where buying has previously been strong enough to slow or reverse a decline. Support is a zone, not a guaranteed floor.",
    "Resistance": "A price area where selling has previously been strong enough to slow or reverse a rise. A clean break above resistance can become a breakout signal.",
    "Level touch": "A test of a support or resistance area. Multiple recent touches can make a level more meaningful, although repeated tests can also weaken a level over time.",
    "Breakout": "A move through an important resistance level. Higher-quality breakouts are usually supported by strong volume, acceptable liquidity, and price holding above the broken level.",
    "Breakout confirmation": "Evidence that a breakout is real rather than a quick false move, such as price holding above the level, increasing volume, a tight spread, or a successful retest.",
    "False breakout": "A move above resistance that quickly fails and falls back below the level. False breakouts are one reason the analyzer can recommend waiting for confirmation rather than buying the first tick above resistance.",
    "Pullback": "A temporary move lower during a broader upward move. Traders often look for pullbacks toward VWAP, prior resistance, or support to obtain a better entry than chasing a spike.",
    "Entry zone": "A price range where the analyzer sees a more favorable balance of upside versus downside. It is a zone rather than one exact price because real markets rarely turn at a single penny.",
    "Stop / invalidation": "The price area where the original trade thesis is considered wrong or materially weakened. It is based on technical structure and volatility rather than an arbitrary percentage loss.",
    "Target 1": "The first, usually more conservative, profit objective. It is commonly based on nearby resistance or another technically meaningful level.",
    "Target 2": "A secondary profit objective beyond Target 1, often using the next resistance area, volatility projection, or historical continuation behavior.",
    "Stretch target": "A more aggressive upside objective that generally requires unusually strong continuation. It should be treated as lower-probability than Target 1.",
    "Reward / risk": "Potential reward divided by potential loss from the proposed entry to the stop. A 2:1 ratio means the target offers about two dollars of potential reward for every one dollar at risk.",
    "Setup score": "The analyzer's combined technical score based on factors such as momentum, VWAP position, volume, liquidity, price location, and other setup-quality inputs. It is not a probability of profit.",
    "Plan confidence": "A quality score for the specific trade plan. It reflects how well the current technical, liquidity, historical, and catalyst evidence agree; it is not a guaranteed probability of success.",
    "Historical spike analog": "A previous large move in the same ticker that resembles the current move. The analyzer uses these examples to estimate how the stock behaved after comparable spikes.",
    "MFE": "Maximum Favorable Excursion: the largest favorable move that occurred after a historical setup. It helps estimate realistic upside behavior rather than only closing-price returns.",
    "MAE": "Maximum Adverse Excursion: the largest move against the position after a historical setup. It helps estimate how much normal drawdown similar setups experienced.",
    "Catalyst": "A news event or company development that can materially change demand for the stock, such as earnings, FDA news, a contract, merger activity, financing, or a reverse split.",
    "Warrant": "A security that gives its holder the right to buy common shares at a specified exercise price. Warrants can create future dilution if exercised.",
    "Warrant overhang": "Potential selling or dilution pressure created by outstanding warrants. If the stock rises above warrant exercise prices, holders may exercise warrants and sell shares, which can limit upside.",
    "Dilution": "An increase in the number of shares outstanding. New shares can reduce each existing share's proportional ownership and can pressure price when issued into the market.",
    "Float": "The number of shares readily available for public trading. Low-float stocks can move very quickly because relatively little buying or selling can shift price.",
    "Market cap": "Share price multiplied by total shares outstanding. It estimates the market value of the company's equity.",
    "Short interest": "Shares that have been sold short and remain open. High short interest can add squeeze potential but can also indicate substantial bearish conviction.",
    "Short float": "Short interest expressed as a percentage of the publicly tradable float.",
    "Short squeeze": "A rapid rise that forces short sellers to buy shares to close positions, which can add additional upward buying pressure.",
    "Halt": "A temporary pause in trading. Volatile stocks may be halted by exchange volatility rules, and trading can resume at a substantially different price.",
    "Gap up": "When a stock opens materially above the prior session's close, leaving a price gap between sessions.",
    "Gap down": "When a stock opens materially below the prior session's close.",
    "Momentum": "The speed and persistence of price movement. The analyzer compares several short time windows so a one-minute bounce is not mistaken for broader strength.",
}

TERM_ALIASES = {
    "vwap ext": "VWAP extension",
    "vwap extension %": "VWAP extension",
    "vol pace": "Volume pace",
    "rvol": "Relative volume",
    "spread": "Bid/ask spread",
    "bid ask spread": "Bid/ask spread",
    "risk reward": "Reward / risk",
    "rr": "Reward / risk",
    "stop": "Stop / invalidation",
    "invalidation": "Stop / invalidation",
    "analog": "Historical spike analog",
    "historical analog": "Historical spike analog",
    "warrant overhang": "Warrant overhang",
    "warrants": "Warrant",
}


def _glossary_match(term):
    if not term:
        return None, None
    q = str(term).strip().lower()
    canonical = TERM_ALIASES.get(q)
    if canonical:
        return canonical, TERM_GLOSSARY.get(canonical)
    for key, value in TERM_GLOSSARY.items():
        if key.lower() == q:
            return key, value
    return None, None


def _extract_response_text(data):
    pieces = []
    for item in data.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if text:
                    pieces.append(str(text))
    return "\n".join(pieces).strip()


def ask_openai_about_term(term, ticker, result):
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured in Streamlit Secrets.")
    model = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna").strip() or "gpt-5.6-luna"
    context = {
        "ticker": ticker,
        "price": result.get("price"),
        "day_pct": result.get("day_pct"),
        "vwap": result.get("vwap"),
        "vwap_extension_pct": result.get("vwap_extension_pct"),
        "volume_pace": result.get("volume_pace"),
        "spread_pct": result.get("spread_pct"),
        "score": result.get("score"),
        "grade": result.get("grade"),
        "trade_plan_status": (result.get("trade_plan") or {}).get("status"),
    }
    prompt = (
        "Explain the trading or stock-market term below in plain English for a newer short-term trader. "
        "First define it, then explain why it matters, then briefly relate it to the current analyzer data only if relevant. "
        "Do not promise outcomes or present the explanation as a guaranteed buy/sell signal. Keep the answer concise.\n\n"
        f"Term: {term}\nCurrent analyzer context: {json.dumps(context, default=str)}"
    )
    payload = json.dumps({
        "model": model,
        "input": prompt,
        "max_output_tokens": 450,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API error {exc.code}: {body[:400]}")
    text = _extract_response_text(data)
    if not text:
        raise RuntimeError("The OpenAI API returned no text answer.")
    return text, model


st.markdown("""
<style>
.stApp{background:#08111f;color:#edf5ff}.block-container{max-width:1450px;padding-top:1.4rem}
.hero{padding:20px 24px;border:1px solid #1e334e;border-radius:16px;background:linear-gradient(135deg,#0c1728,#0a1423);margin-bottom:14px}
.title{font-size:32px;font-weight:900;letter-spacing:-.6px}.sub{color:#91a7c2;font-size:14px}.pill{display:inline-block;padding:5px 10px;border-radius:999px;background:#11243a;border:1px solid #2c4969;font-size:12px;font-weight:800;margin-top:8px}
.card{border:1px solid #1d334e;background:#0c1727;border-radius:14px;padding:14px 16px;min-height:108px}.k{font-size:11px;color:#8097b3;font-weight:800;letter-spacing:.08em}.v{font-size:27px;font-weight:900;margin-top:4px}.n{font-size:12px;color:#91a7c2;margin-top:2px}
.good{color:#65e98d}.bad{color:#ff8181}.warn{color:#ffd166}.section{font-size:18px;font-weight:900;margin:22px 0 9px}.callout{border-left:4px solid #4593ff;background:#0d1a2d;padding:14px 16px;border-radius:8px;margin-top:10px}
.tradeplan{border:1px solid #274664;background:#0b1829;border-radius:16px;padding:18px 20px;margin:16px 0 8px}.tradeaction{font-size:25px;font-weight:900;margin-bottom:5px}.tradewhy{color:#a9bdd4;font-size:13px}.smallnote{color:#91a7c2;font-size:12px}
.search-label{font-size:19px;font-weight:900;color:#f4f8ff;margin:0 0 10px 2px;line-height:1.25;letter-spacing:.01em}
</style>
""",unsafe_allow_html=True)

if _COMBINED_WORKSPACE:
    st.markdown(
        """
        <style>
        /* Compact vertical rhythm for the combined workspace. */
        .block-container {
            padding-top: .08rem !important;
            padding-bottom: .5rem !important;
        }
        .block-container [data-testid="stVerticalBlock"] {
            gap: .34rem !important;
        }
        .block-container [data-testid="stHorizontalBlock"] {
            gap: .55rem !important;
        }
        .hero {
            padding: 4px 8px !important;
            margin: 0 0 3px !important;
            border-radius: 8px !important;
            min-height: 0 !important;
        }
        .hero .title {
            font-size: 15px !important;
            line-height: 1.05 !important;
        }
        .hero .sub { display: none !important; }
        .search-label {
            font-size: 12px !important;
            margin: 0 0 2px 1px !important;
            line-height: 1.05 !important;
        }
        [data-testid="stSelectbox"] > div > div {
            min-height: 34px !important;
        }
        div[data-testid="stButton"] button[kind="primary"] {
            min-height: 36px !important;
            height: 36px !important;
            border-radius: 8px !important;
        }
        .card {
            min-height: 76px !important;
            padding: 8px 10px !important;
            border-radius: 10px !important;
        }
        .k { font-size: 9px !important; line-height: 1.05 !important; }
        .v { font-size: 20px !important; margin-top: 2px !important; line-height: 1.08 !important; }
        .n { font-size: 10px !important; margin-top: 1px !important; line-height: 1.18 !important; }
        .tradeplan {
            padding: 9px 11px !important;
            margin: 6px 0 4px !important;
            border-radius: 10px !important;
        }
        .tradeaction { font-size: 19px !important; margin-bottom: 2px !important; }
        .tradewhy { font-size: 11px !important; line-height: 1.25 !important; }
        .section {
            font-size: 15px !important;
            margin: 9px 0 4px !important;
        }
        .callout {
            padding: 8px 10px !important;
            margin-top: 4px !important;
        }
        div[data-testid="stAlert"] {
            padding: 7px 10px !important;
            margin: 2px 0 !important;
            min-height: 0 !important;
        }
        div[data-testid="stAlert"] p {
            font-size: 11px !important;
            line-height: 1.3 !important;
        }
        [data-testid="stExpander"] {
            margin: 2px 0 !important;
        }
        [data-testid="stExpander"] details summary {
            min-height: 32px !important;
            padding-top: 3px !important;
            padding-bottom: 3px !important;
        }
        [data-testid="stSpinner"] {
            margin: 1px 0 !important;
            min-height: 18px !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

with st.container(key="analyzer_header"):
    _header_result = st.session_state.get("result") or {}
    _header_symbol = str(
        _header_result.get("symbol")
        or st.session_state.get("ticker")
        or st.session_state.get("ticker_search_request")
        or "—"
    ).upper().strip()
    st.markdown(
        f'<div class="hero">'
        f'<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;">'
        f'<div class="title">Single Stock Analyzer</div>'
        f'<div style="padding:4px 10px;border:1px solid #3f6a8f;border-radius:999px;'
        f'background:#10243a;color:#eaf4ff;font-size:12px;font-weight:900;'
        f'letter-spacing:.06em;">CURRENT STOCK · {html.escape(_header_symbol)}</div>'
        f'</div>'
        f'<div class="sub">Live momentum, VWAP, volume, historical analogs, '
        f'support/resistance and dynamic entry/exit planning.</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

@st.fragment
def render_ticker_search(asset_choices, current_symbol):
    """Native searchable ticker picker with Enter-to-select behavior."""
    # No default selection: clearing the picker keeps it empty instead of
    # restoring the currently analyzed ticker.
    selected_asset = st.selectbox(
        "Ticker or company",
        options=asset_choices,
        index=None,
        key="ticker_picker",
        placeholder="Start typing a ticker or company name…",
        label_visibility="collapsed",
        width="stretch",
    )

    selected_symbol = _ticker_from_choice(selected_asset)

    # In Streamlit's native selectbox, typing filters the options and Enter
    # accepts the currently highlighted match (normally the first result).
    if selected_symbol and selected_symbol != st.session_state.get("ticker_search_request"):
        # This core runs inside the Analyzer fragment. Mark the real Analyze
        # button busy before the expensive calculation begins.
        st.session_state["ticker_search_request"] = selected_symbol
        st.session_state["_analyzer_loading"] = True

    if not _COMBINED_WORKSPACE:
        st.caption(f"Currently analyzed: **{current_symbol}**")

    if asset_choices:
        if not _COMBINED_WORKSPACE:
            st.caption(
                f"Search ready · {len(asset_choices):,} active US equities loaded from Alpaca. "
                "Type a symbol or company name; press Enter to choose the highlighted match."
            )
    else:
        load_error = st.session_state.get("_ticker_asset_load_error")
        detail = f" ({load_error})" if load_error else ""
        st.warning(
            "Ticker search could not load Alpaca's active-equity list. "
            "The analyzer itself can still work; this only affects company-name suggestions."
            + detail
        )


with st.container(key="analyzer_controls"):
    c1,c2,c3,c4=st.columns([2.3,1,0.9,0.65], vertical_alignment="center")
    term_tool_col = c4
    with c1:
        asset_choices=load_active_us_equities()
        current_symbol=str(st.session_state.get("ticker","SDOT") or "SDOT").upper().strip()
        render_ticker_search(asset_choices, current_symbol)

    ticker=str(
        st.session_state.get("ticker_search_request")
        or st.session_state.get("ticker","SDOT")
        or "SDOT"
    ).upper().strip()

    def _request_manual_analysis():
        st.session_state["_manual_analyze_requested"] = True
        st.session_state["_analyzer_loading"] = True

    with c2:
        _button_loading = bool(st.session_state.get("_analyzer_loading"))
        st.button(
            "Analyzing..." if _button_loading else "Analyze",
            type="primary",
            width="stretch",
            key="analyzer_manual_analyze",
            disabled=_button_loading,
            on_click=_request_manual_analysis,
        )
    with c3:
        st.toggle("Auto-refresh", value=True, key="auto_refresh_enabled")
        if not _COMBINED_WORKSPACE:
            st.caption(f"Refresh every {AUTO_REFRESH_SECONDS}s · use `ALPACA_LIVE_FEED=\"sip\"` for consolidated real-time data when your Alpaca plan supports SIP.")

def _save_position_input(symbol, field, widget_key):
    store = st.session_state.setdefault("_position_inputs_by_symbol", {})
    record = dict(store.get(symbol) or {})
    record[field] = st.session_state.get(widget_key)
    store[symbol] = record
    st.session_state["_position_inputs_by_symbol"] = store


_position_store = st.session_state.setdefault("_position_inputs_by_symbol", {})
_position_saved = dict(_position_store.get(ticker) or {})
_position_toggle_key = f"position_owned_{ticker}"
_position_cost_key = f"position_avg_cost_{ticker}"
_position_shares_key = f"position_shares_{ticker}"

if _position_toggle_key not in st.session_state:
    st.session_state[_position_toggle_key] = bool(_position_saved.get("enabled", False))
if _position_cost_key not in st.session_state:
    st.session_state[_position_cost_key] = float(_position_saved.get("average_cost") or 0.0)
if _position_shares_key not in st.session_state:
    st.session_state[_position_shares_key] = float(_position_saved.get("shares") or 0.0)

_position_enabled = st.toggle(
    "I already own this stock",
    key=_position_toggle_key,
    on_change=_save_position_input,
    args=(ticker, "enabled", _position_toggle_key),
)
_position_avg_cost = float(st.session_state.get(_position_cost_key) or 0.0)
_position_shares = float(st.session_state.get(_position_shares_key) or 0.0)

if _position_enabled:
    _pc1, _pc2, _pc3 = st.columns([1, 1, 3.2], vertical_alignment="bottom")
    with _pc1:
        _position_avg_cost = st.number_input(
            "Average cost",
            min_value=0.0,
            step=0.01,
            format="%.4f",
            key=_position_cost_key,
            on_change=_save_position_input,
            args=(ticker, "average_cost", _position_cost_key),
        )
    with _pc2:
        _position_shares = st.number_input(
            "Shares (optional)",
            min_value=0.0,
            step=1.0,
            format="%.4f",
            key=_position_shares_key,
            on_change=_save_position_input,
            args=(ticker, "shares", _position_shares_key),
        )
    with _pc3:
        st.caption(
            "Position mode reuses the trade-plan area below for exit management. "
            "Cost and shares are kept separately for each ticker during this session."
        )

_existing_result=st.session_state.get("result")
_result_age=_result_age_seconds(_existing_result)
_refresh_due=(
    st.session_state.get("auto_refresh_enabled", True)
    and _result_age is not None
    and _result_age >= max(1, AUTO_REFRESH_SECONDS-1)
)

_ticker_changed = st.session_state.get("ticker") != ticker
_initial_analysis = "result" not in st.session_state
_manual_request = bool(st.session_state.get("_manual_analyze_requested"))
_needs_analysis = _manual_request or _initial_analysis or _ticker_changed or _refresh_due
_interactive_analysis = _manual_request or _ticker_changed

if _needs_analysis:
    try:
        # Never render a separate spinner/status line. Interactive analyses use
        # the actual Analyze button's "Analyzing..." state; timed deep refreshes
        # are silent.
        _new_result = analyze(ticker)
        st.session_state["result"] = _new_result
        st.session_state["ticker"] = ticker
        st.session_state["ticker_search_request"] = ticker
        st.session_state.pop("_analyzer_refresh_error", None)
    except Exception as e:
        # Keep the last good analysis visible if a background refresh fails.
        if _refresh_due and st.session_state.get("result"):
            st.session_state["_analyzer_refresh_error"] = str(e)
        else:
            st.session_state["_analyzer_loading"] = False
            st.session_state["_manual_analyze_requested"] = False
            st.error(str(e)); st.stop()
    finally:
        if _interactive_analysis:
            st.session_state["_analyzer_loading"] = False
            st.session_state["_manual_analyze_requested"] = False

    # Button clicks/selectbox changes rerun this fragment. Refresh it once more
    # after the analysis completes so the button immediately returns to Analyze
    # and the new values render without a full-app rerun.
    if _interactive_analysis:
        st.rerun(scope="fragment")

r=st.session_state["result"]

# Compact glossary/AI tool rendered into the top control row.
with term_tool_col.popover("📘 Terms / Ask AI"):
    st.caption("Search common analyzer terms. Built-in definitions work without an AI key; optional OpenAI answers can use the current ticker's metrics for context.")
    glossary_terms=sorted(TERM_GLOSSARY.keys())
    term=st.selectbox(
        "Term",
        glossary_terms,
        index=None,
        placeholder="Type VWAP, liquidity, warrant overhang, MFE…",
        accept_new_options=True,
        filter_mode="contains",
        width="stretch",
        key="term_lookup",
    )
    if term:
        matched_term, definition=_glossary_match(term)
        if definition:
            st.markdown(f"**{matched_term}**")
            st.write(definition)
        else:
            st.info(f'“{term}” is not in the built-in glossary yet.')

        openai_ready=bool(os.environ.get("OPENAI_API_KEY", "").strip())
        if openai_ready:
            if st.button("Ask AI for a contextual explanation", key="ask_term_ai", width="content"):
                try:
                    with st.spinner(f"Explaining {term}…"):
                        answer, model=ask_openai_about_term(term,ticker,r)
                    st.session_state["term_ai_answer"]={"term":str(term),"answer":answer,"model":model}
                except Exception as exc:
                    st.error(str(exc))
            saved=st.session_state.get("term_ai_answer") or {}
            if saved.get("term")==str(term) and saved.get("answer"):
                st.markdown("#### AI explanation")
                st.write(saved["answer"])
                st.caption(f'Answered by OpenAI API model: {saved.get("model")}')
        else:
            st.caption("Optional: add `OPENAI_API_KEY` to Streamlit Secrets to get AI explanations inside the analyzer. This is separate from your ChatGPT subscription.")
            st.link_button("Open ChatGPT", "https://chatgpt.com/", width="content")


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

with st.container(key="analyzer_metrics_top"):
    cols=st.columns(6)
    _trade_age=r.get("trade_age_seconds")
    _price_note=f'{pp(r.get("day_pct"))} · trade {_age_text(_trade_age)} · {r.get("live_feed")}'
    card(cols[0],"PRICE",money(r.get("price")),_price_note,"good" if (r.get("day_pct") or 0)>=0 else "bad")
    card(cols[1],"VWAP",money(r.get("vwap")),f'{r.get("vwap_position")} · {pp(r.get("vwap_extension_pct"))}',"good" if r.get("vwap_position")=="ABOVE" else "bad")
    card(cols[2],"DAY RANGE",f'{money(r.get("day_low"))}–{money(r.get("day_high"))}',f'{r.get("from_high_pct",0):.1f}% below high')
    card(cols[3],"VOL PACE",multiple(r.get("volume_pace")),f'{r.get("volume",0):,.0f} shown · {r.get("volume_source")}')
    card(cols[4],"SETUP SCORE",f'{r.get("score"):.1f} / 100',f'Grade {r.get("grade")}',"good" if r.get("grade") in ("A","B") else "warn")
    card(cols[5],"BASE SETUP",r.get("entry_quality"),f'Live feed: {r.get("live_feed")}',"good" if r.get("entry_quality")=="FAVORABLE" else "warn")

with st.container(key="analyzer_decision_v2"):
    render_v2_decision(st, r)



if _trade_age is not None and float(_trade_age) > max(30, AUTO_REFRESH_SECONDS*2):
    feed_name=str(r.get("live_feed") or "").upper()
    provider=str(r.get("market_provider") or r.get("live_provider") or "alpaca").lower()
    if provider=="tradier":
        extra=" Tradier consolidated data has not reported a newer eligible trade yet."
    elif feed_name=="IEX":
        extra=(
            " IEX is a single exchange, so its most recent trade can lag the consolidated market for some stocks. "
            "If your Alpaca subscription includes SIP, set ALPACA_LIVE_FEED=\"sip\" in Streamlit Secrets."
        )
    else:
        extra=" The upstream Alpaca feed itself has not reported a newer eligible trade yet."
    st.warning(f"Latest {feed_name or 'market'} trade is {_age_text(_trade_age)}.{extra}")

# Dynamic decision-support trade plan. Position mode replaces the visible
# entry plan with an exit-management plan while leaving the entry engine intact.
plan=r.get("trade_plan") or {}
if not plan:
    st.error(
        "Trade-plan data is missing from stock_analyzer.py. "
        "The dashboard and analysis engine are mismatched. "
        "Upload the matched trade-plan version of stock_analyzer.py."
    )
    st.stop()

_position_plan = None
if _position_enabled and float(_position_avg_cost or 0) > 0:
    try:
        _position_overlay = get_live_overlay(r) or {}
    except Exception:
        _position_overlay = {}
    _position_metrics = merge_live_position_metrics(r, _position_overlay)
    _position_plan = build_position_exit_plan(
        _position_metrics,
        _position_avg_cost,
        _position_shares if float(_position_shares or 0) > 0 else None,
    )

if _position_enabled and not _position_plan:
    st.info("Enter your average cost above to build a position exit plan.")
elif (
    _position_enabled
    and _position_plan
    and _position_plan.get("status") != "ok"
):
    st.warning(
        _position_plan.get("error")
        or "The position exit plan is temporarily unavailable."
    )

if _position_plan and _position_plan.get("status") == "ok":
    _pread = str(_position_plan.get("read") or "WATCH")
    _pcls = (
        "good" if _pread == "HOLD"
        else "bad" if _pread in {"EXIT", "REDUCE"}
        else "warn"
    )
    _pwhy = " ".join(_position_plan.get("reasons") or [])
    st.markdown(
        f'<div class="tradeplan"><div class="k">POSITION EXIT PLAN</div>'
        f'<div class="tradeaction {_pcls}">{html.escape(_position_plan.get("action") or _pread)}</div>'
        f'<div class="tradewhy">{html.escape(_pwhy)}</div></div>',
        unsafe_allow_html=True,
    )

    _protect_note = (
        f'{pp(_position_plan.get("protective_exit_return_pct"))} vs cost · '
        f'{_position_plan.get("room_to_protective_pct", 0):.1f}% below current'
    )
    _trail_note = (
        f'{pp(_position_plan.get("trailing_exit_return_pct"))} vs cost · '
        f'{_position_plan.get("room_to_trailing_pct", 0):.1f}% below current'
    )

    _pcards = st.columns(5)
    card(
        _pcards[0],
        "POSITION P/L",
        pp(_position_plan.get("pnl_pct")),
        f'Avg cost {money(_position_plan.get("average_cost"))}'
        + (
            f' · {float(_position_plan.get("shares")):,.0f} shares'
            if _position_plan.get("shares") is not None else ""
        ),
        "good" if (_position_plan.get("pnl_pct") or 0) >= 0 else "bad",
    )
    card(
        _pcards[1],
        "PROTECTIVE EXIT",
        money(_position_plan.get("protective_exit")),
        _protect_note,
        "warn",
    )
    card(
        _pcards[2],
        "FIRST TRIM",
        money(_position_plan.get("first_trim")),
        str(_position_plan.get("first_trim_reason") or ""),
        "good",
    )
    card(
        _pcards[3],
        "STRETCH EXIT",
        money(_position_plan.get("stretch_target")),
        str(_position_plan.get("stretch_reason") or ""),
        "good",
    )
    card(
        _pcards[4],
        "TRAILING EXIT",
        money(_position_plan.get("trailing_exit")),
        _trail_note,
        "warn",
    )

    with st.expander("Exit plan details"):
        _ec1, _ec2 = st.columns(2)
        with _ec1:
            st.markdown("#### Position")
            st.write(f'**Current price:** {money(_position_plan.get("price"))}')
            st.write(f'**Average cost:** {money(_position_plan.get("average_cost"))}')
            st.write(f'**P/L per share:** {money(_position_plan.get("pnl_per_share"))}')
            if _position_plan.get("shares") is not None:
                st.write(f'**Shares:** {float(_position_plan.get("shares")):,.0f}')
                st.write(f'**Market value:** {money(_position_plan.get("market_value"))}')
                st.write(f'**Estimated P/L:** {money(_position_plan.get("total_pnl"))}')
        with _ec2:
            st.markdown("#### Exit levels")
            st.write(
                f'**Protective exit:** {money(_position_plan.get("protective_exit"))} '
                f'({_position_plan.get("room_to_protective_pct", 0):.1f}% below current)'
            )
            st.write(
                f'**Trailing exit:** {money(_position_plan.get("trailing_exit"))} '
                f'({_position_plan.get("room_to_trailing_pct", 0):.1f}% below current)'
            )
            st.write(
                f'**First trim:** {money(_position_plan.get("first_trim"))} — '
                f'{_position_plan.get("first_trim_reason") or "—"}'
            )
            st.write(
                f'**Second target:** {money(_position_plan.get("second_target"))} — '
                f'{_position_plan.get("second_target_reason") or "—"}'
            )
            st.write(
                f'**Stretch exit:** {money(_position_plan.get("stretch_target"))} — '
                f'{_position_plan.get("stretch_reason") or "—"}'
            )
        st.caption(_position_plan.get("method_note") or "")
elif not _position_enabled:
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
        st.dataframe(ddf,width="stretch",hide_index=True)
        st.caption(plan.get("method_note") or "")

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
        st.dataframe(sdf[show],width="stretch",hide_index=True)
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

st.caption(f'Analysis as of {r.get("as_of")} · Latest trade={r.get("latest_trade_time") or "—"} ({_age_text(r.get("trade_age_seconds"))}) · Latest quote={r.get("latest_quote_time") or "—"} ({_age_text(r.get("quote_age_seconds"))}) · Live={r.get("live_feed")} · Historical/liquidity={r.get("historical_feed")} · Engine={r.get("engine_version") or "unknown"} · UI=live-refresh-v5.0')
