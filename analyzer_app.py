from pathlib import Path
import runpy

import streamlit as st
import streamlit.components.v1 as components

from scanner_expand import install_scanner_expander

# Compatibility entrypoint for deployments configured to launch analyzer_app.py.
target = Path(__file__).with_name("app.py")
if not target.exists():
    raise FileNotFoundError(
        "app.py was not found in the repository root. "
        "The combined Momentum Scanner + Stock Analyzer requires app.py."
    )

runpy.run_path(str(target), run_name="__main__")
view = st.session_state.get("app_view", "Momentum Scanner")


# Presentation-only polish for the combined workspace. This intentionally does
# not change navigation/session/loading behavior.
st.markdown(
    """
    <style>
    /* app.py still emits its old Stock Workspace title box. Hide it so the
       selector itself becomes the header, matching the approved mockup. */
    .combined-nav-wrap { display: none !important; }

    /* Force the workspace selector and all of its Streamlit wrappers to use
       the full content width rather than shrinking to radio-label content. */
    .st-key-app_view,
    .st-key-app_view > div,
    .st-key-app_view [data-testid="stRadio"],
    .st-key-app_view [data-testid="stRadio"] > div,
    [data-testid="stElementContainer"]:has(.st-key-app_view) {
        width: 100% !important;
        max-width: none !important;
        min-width: 0 !important;
    }

    .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] {
        display: grid !important;
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
        gap: 10px !important;
        width: 100% !important;
        max-width: none !important;
        box-sizing: border-box !important;
        padding: 8px !important;
        margin: 4px 0 28px !important;
        border: 1px solid #33475f !important;
        border-radius: 24px !important;
        background: linear-gradient(135deg, rgba(12,23,39,.96), rgba(8,17,31,.96)) !important;
        box-shadow: inset 0 0 0 1px rgba(255,255,255,.015), 0 10px 26px rgba(0,0,0,.18) !important;
    }

    .st-key-app_view [data-testid="stRadio"] label {
        position: relative !important;
        display: grid !important;
        grid-template-columns: 88px minmax(0,1fr) !important;
        grid-template-rows: auto auto !important;
        column-gap: 22px !important;
        row-gap: 5px !important;
        align-items: center !important;
        min-height: 154px !important;
        width: 100% !important;
        box-sizing: border-box !important;
        padding: 25px 30px !important;
        border: 1px solid transparent !important;
        border-radius: 20px !important;
        background: transparent !important;
        box-shadow: none !important;
        cursor: pointer !important;
        overflow: hidden !important;
        transition: border-color .15s ease, background .15s ease, box-shadow .15s ease, transform .15s ease !important;
    }

    .st-key-app_view [data-testid="stRadio"] label:hover {
        border-color: #3c5b77 !important;
        background: linear-gradient(135deg, rgba(18,31,49,.92), rgba(12,25,40,.92)) !important;
        transform: translateY(-1px) !important;
    }

    /* Hide the small native radio circle. The large icon circle below replaces
       it visually while the original input remains clickable/accessibile. */
    .st-key-app_view [data-testid="stRadio"] label > div:first-child {
        position: absolute !important;
        opacity: 0 !important;
        pointer-events: none !important;
        width: 1px !important;
        height: 1px !important;
        overflow: hidden !important;
    }

    .st-key-app_view [data-testid="stRadio"] label::before {
        grid-column: 1 !important;
        grid-row: 1 / 3 !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        width: 72px !important;
        height: 72px !important;
        border-radius: 999px !important;
        border: 2px solid #2c4059 !important;
        color: #f4f8ff !important;
        background: rgba(11,22,37,.55) !important;
        font-size: 40px !important;
        line-height: 1 !important;
        font-weight: 700 !important;
        box-sizing: border-box !important;
    }
    .st-key-app_view [data-testid="stRadio"] label:nth-child(1)::before { content: "↗"; }
    .st-key-app_view [data-testid="stRadio"] label:nth-child(2)::before { content: "⌕"; font-size: 46px !important; }

    .st-key-app_view [data-testid="stRadio"] label [data-testid="stMarkdownContainer"] {
        grid-column: 2 !important;
        grid-row: 1 !important;
        align-self: end !important;
        margin: 0 !important;
        padding: 0 !important;
    }

    .st-key-app_view [data-testid="stRadio"] label p {
        margin: 0 !important;
        color: #f5f9ff !important;
        font-size: 30px !important;
        line-height: 1.08 !important;
        font-weight: 950 !important;
        letter-spacing: -.025em !important;
    }

    .st-key-app_view [data-testid="stRadio"] label::after {
        grid-column: 2 !important;
        grid-row: 2 !important;
        align-self: start !important;
        color: #b5c3d5 !important;
        font-size: 18px !important;
        line-height: 1.25 !important;
        font-weight: 500 !important;
        letter-spacing: 0 !important;
    }
    .st-key-app_view [data-testid="stRadio"] label:nth-child(1)::after {
        content: "Discover high-momentum stocks";
    }
    .st-key-app_view [data-testid="stRadio"] label:nth-child(2)::after {
        content: "Deep dive into any stock";
    }

    /* Approved active-state treatment: dark emerald panel with a restrained
       green outline/glow instead of the old bright red radio styling. */
    .st-key-app_view [data-testid="stRadio"] label:has(input:checked) {
        border-color: #55cf70 !important;
        background: linear-gradient(135deg, #124328 0%, #0d341f 58%, #0a2819 100%) !important;
        box-shadow: 0 0 0 1px rgba(85,207,112,.18), 0 8px 28px rgba(32,139,67,.25) !important;
    }
    .st-key-app_view [data-testid="stRadio"] label:has(input:checked)::before {
        border-color: #3b9d55 !important;
        background: rgba(12,53,31,.72) !important;
        box-shadow: inset 0 0 0 1px rgba(91,219,120,.08) !important;
    }
    .st-key-app_view [data-testid="stRadio"] label:has(input:checked) p {
        color: #ffffff !important;
    }
    .st-key-app_view [data-testid="stRadio"] label:has(input:checked)::after {
        color: #d2ddda !important;
    }

    /* Saved Stocks block: scale it to the same visual weight as the mockup. */
    .st-key-saved_stocks_top .saved-stock-shell {
        min-height: 132px !important;
        box-sizing: border-box !important;
        padding: 28px 30px 24px !important;
        margin: 2px 0 20px !important;
        border: 1px solid #2b4664 !important;
        border-radius: 18px !important;
        background: linear-gradient(135deg, #0d1a2d, #0b1728) !important;
    }
    .st-key-saved_stocks_top .saved-stock-title {
        font-size: 28px !important;
        line-height: 1.15 !important;
        font-weight: 950 !important;
        color: #f4f8ff !important;
        letter-spacing: -.02em !important;
    }
    .st-key-saved_stocks_top .saved-stock-sub {
        margin-top: 16px !important;
        font-size: 18px !important;
        line-height: 1.35 !important;
        color: #adc0d7 !important;
    }

    /* Enlarge the Save / Remove action row and give both actions clear,
       readable mockup-like treatments. */
    .st-key-saved_stocks_top [data-testid="stHorizontalBlock"]:has(.st-key-save_current_stock) {
        gap: 20px !important;
        align-items: stretch !important;
        margin-bottom: 16px !important;
    }
    .st-key-saved_stocks_top [data-testid="stHorizontalBlock"]:has(.st-key-save_current_stock) > [data-testid="stColumn"]:nth-child(1),
    .st-key-saved_stocks_top [data-testid="stHorizontalBlock"]:has(.st-key-save_current_stock) > [data-testid="stColumn"]:nth-child(2) {
        flex: 0 0 34% !important;
        width: 34% !important;
        max-width: 34% !important;
    }
    .st-key-saved_stocks_top [data-testid="stHorizontalBlock"]:has(.st-key-save_current_stock) > [data-testid="stColumn"]:nth-child(3) {
        flex: 1 1 auto !important;
        width: auto !important;
    }

    .st-key-saved_stocks_top .st-key-save_current_stock button,
    .st-key-saved_stocks_top .st-key-remove_current_stock button {
        min-height: 82px !important;
        border-radius: 15px !important;
        font-size: 21px !important;
        font-weight: 900 !important;
        box-shadow: none !important;
    }
    .st-key-saved_stocks_top .st-key-save_current_stock button:not(:disabled) {
        background: linear-gradient(135deg, #255f35, #164726) !important;
        border: 2px solid #59cc70 !important;
        color: #ffffff !important;
    }
    .st-key-saved_stocks_top .st-key-save_current_stock button:not(:disabled):hover {
        background: linear-gradient(135deg, #2b713e, #1a542d) !important;
        border-color: #72df87 !important;
    }
    .st-key-saved_stocks_top .st-key-remove_current_stock button:not(:disabled) {
        background: #0b1625 !important;
        border: 2px solid #3e9e56 !important;
        color: #58c66f !important;
    }
    .st-key-saved_stocks_top .st-key-remove_current_stock button:not(:disabled):hover {
        background: #10241a !important;
        border-color: #58c66f !important;
        color: #78df8d !important;
    }
    .st-key-saved_stocks_top .st-key-save_current_stock button p,
    .st-key-saved_stocks_top .st-key-save_current_stock button span {
        color: inherit !important;
        font-size: 21px !important;
        font-weight: 900 !important;
    }
    .st-key-saved_stocks_top .st-key-remove_current_stock button p,
    .st-key-saved_stocks_top .st-key-remove_current_stock button span {
        color: inherit !important;
        font-size: 21px !important;
        font-weight: 900 !important;
    }

    .st-key-saved_stocks_top [data-testid="stCaptionContainer"] p {
        font-size: 17px !important;
        line-height: 1.45 !important;
        color: #a6b5c8 !important;
    }

    /* Streamlit secondary buttons should never turn white on this dark app. */
    div[data-testid="stButton"] button[kind="secondary"] {
        background: #101b2d !important;
        border: 1px solid #36506d !important;
        color: #eef5ff !important;
        box-shadow: none !important;
    }
    div[data-testid="stButton"] button[kind="secondary"] p,
    div[data-testid="stButton"] button[kind="secondary"] span {
        color: #eef5ff !important;
        font-weight: 800 !important;
    }
    div[data-testid="stButton"] button[kind="secondary"]:hover:not(:disabled) {
        background: #153524 !important;
        border-color: #49b66a !important;
        color: #ffffff !important;
    }

    /* Disabled buttons remain dark and readable instead of white/washed out. */
    div[data-testid="stButton"] button:disabled {
        background: #0d1624 !important;
        border-color: #26384d !important;
        color: #8396ad !important;
        opacity: .82 !important;
    }
    div[data-testid="stButton"] button:disabled p,
    div[data-testid="stButton"] button:disabled span {
        color: #8396ad !important;
    }

    @media (max-width: 900px) {
        .st-key-app_view [data-testid="stRadio"] > div[role="radiogroup"] {
            grid-template-columns: 1fr !important;
            gap: 8px !important;
        }
        .st-key-app_view [data-testid="stRadio"] label {
            min-height: 118px !important;
            grid-template-columns: 66px minmax(0,1fr) !important;
            column-gap: 16px !important;
            padding: 20px 22px !important;
        }
        .st-key-app_view [data-testid="stRadio"] label::before {
            width: 58px !important;
            height: 58px !important;
            font-size: 32px !important;
        }
        .st-key-app_view [data-testid="stRadio"] label p {
            font-size: 23px !important;
        }
        .st-key-app_view [data-testid="stRadio"] label::after {
            font-size: 15px !important;
        }
        .st-key-saved_stocks_top [data-testid="stHorizontalBlock"]:has(.st-key-save_current_stock) > [data-testid="stColumn"]:nth-child(1),
        .st-key-saved_stocks_top [data-testid="stHorizontalBlock"]:has(.st-key-save_current_stock) > [data-testid="stColumn"]:nth-child(2) {
            flex: 1 1 50% !important;
            width: 50% !important;
            max-width: none !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def _install_scanner_interactions():
    """Scanner detail-card behavior without a page-wide observer."""
    components.html(
        """
        <script>
        (() => {
          const p = window.parent;
          const d = p.document;

          const tips = {
            '5 MIN': 'Price momentum over roughly the last five minutes. Positive values show short-term upward movement; negative values show weakening.',
            '15 MIN': 'Price momentum over roughly the last fifteen minutes, giving a broader view than the five-minute reading.',
            'TOD VOL PACE': 'Current volume compared with what this stock normally trades by this exact time of day. 1.0x is about normal; 2.0x is about twice normal.',
            'NORMAL VOL BY NOW': 'The share of a normal day’s volume this ticker historically tends to have completed by the current time.',
            'VWAP PRICE': 'Volume-Weighted Average Price: the session average traded price weighted by volume. Price holding above VWAP is often constructive for intraday momentum.',
            'FROM HIGH': 'How far the current price is below today’s session high. A smaller value means price is still trading close to the high.',
            'IEX SPREAD': 'The percentage gap between the current IEX bid and ask. A smaller spread usually means cleaner entries and exits with less slippage.',
            'LIQUIDITY': 'How easily shares can be bought or sold without moving price much. Higher liquidity generally means tighter spreads and easier entries and exits.',
            'SETUP READ': 'A plain-English summary of the scanner conditions, strengths, warnings, and filter results for this setup.',
            'CATALYST': 'A news event or company development that can materially change demand for the stock, such as earnings, FDA news, a contract, financing, or merger activity.'
          };

          const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim().toUpperCase();
          const tickerEls = () => Array.from(d.querySelectorAll('.scanner-expandable-ticker'));

          function symbolForTicker(el) {
            return normalize(el && el.textContent);
          }

          function tickerForSymbol(symbol) {
            return tickerEls().find((el) => symbolForTicker(el) === symbol) || null;
          }

          function closeDetailCard(card) {
            if (!card) return;
            const symbolEl = card.querySelector('.sid-symbol');
            const symbol = normalize(symbolEl && symbolEl.textContent);
            card.remove();
            if (p.__scannerExpandedSymbols && symbol) p.__scannerExpandedSymbols.delete(symbol);
            const ticker = tickerForSymbol(symbol);
            if (ticker) ticker.setAttribute('aria-expanded', 'false');
            const tooltip = d.getElementById('stock-tech-tooltip');
            if (tooltip) tooltip.style.display = 'none';
          }

          function closeOtherCards(keepSymbol) {
            d.querySelectorAll('.scanner-inline-detail').forEach((card) => {
              const symbol = normalize(card.querySelector('.sid-symbol')?.textContent);
              if (symbol !== keepSymbol) closeDetailCard(card);
            });
            tickerEls().forEach((ticker) => {
              const symbol = symbolForTicker(ticker);
              if (symbol !== keepSymbol) ticker.setAttribute('aria-expanded', 'false');
            });
            if (p.__scannerExpandedSymbols) {
              Array.from(p.__scannerExpandedSymbols).forEach((symbol) => {
                if (symbol !== keepSymbol) p.__scannerExpandedSymbols.delete(symbol);
              });
            }
          }

          function annotateCards() {
            d.querySelectorAll('.scanner-inline-detail').forEach((card) => {
              card.style.cursor = 'pointer';
              card.setAttribute('role', 'button');
              card.setAttribute('tabindex', '0');
              card.setAttribute('aria-label', 'Expanded scanner details. Click anywhere on this card to close.');

              card.querySelectorAll('.sid-mk, .sid-note-k').forEach((label) => {
                const key = normalize(label.textContent);
                const definition = tips[key];
                if (!definition) return;
                label.setAttribute('data-tech-tooltip', definition);
                label.setAttribute('tabindex', '0');
                label.setAttribute('aria-label', `${label.textContent.trim()}. ${definition}`);
              });
            });
          }

          const STYLE_ID = 'scanner-card-close-hint-style';
          if (!d.getElementById(STYLE_ID)) {
            const style = d.createElement('style');
            style.id = STYLE_ID;
            style.textContent = `
              .scanner-inline-detail::after {
                content:'CLICK ANYWHERE ON CARD TO CLOSE';
                display:block;margin-top:14px;padding-top:11px;
                border-top:1px solid rgba(120,150,190,.18);
                color:#7890ad;font-size:9px;font-weight:900;
                letter-spacing:.10em;text-align:right;
              }
              .scanner-inline-detail:hover { border-color:#496888; }
              .scanner-inline-detail:focus { outline:2px solid #4593ff; outline-offset:3px; }
              .scanner-inline-detail [data-tech-tooltip] { cursor:help !important; }
            `;
            d.head.appendChild(style);
          }

          const old = p.__scannerUXPatch;
          if (old) {
            try { d.removeEventListener('click', old.captureClick, true); } catch (_) {}
            try { d.removeEventListener('keydown', old.keydown); } catch (_) {}
          }

          const captureClick = (event) => {
            const card = event.target.closest && event.target.closest('.scanner-inline-detail');
            if (card) {
              closeDetailCard(card);
              return;
            }

            const ticker = event.target.closest && event.target.closest('.scanner-expandable-ticker');
            if (!ticker) return;
            const symbol = symbolForTicker(ticker);
            const alreadyOpen = ticker.getAttribute('aria-expanded') === 'true';
            if (!alreadyOpen) closeOtherCards(symbol);
            p.setTimeout(annotateCards, 0);
          };

          const keydown = (event) => {
            const card = event.target.closest && event.target.closest('.scanner-inline-detail');
            if (!card || (event.key !== 'Enter' && event.key !== ' ')) return;
            if (event.target.closest('[data-tech-tooltip]')) return;
            event.preventDefault();
            closeDetailCard(card);
          };

          d.addEventListener('click', captureClick, true);
          d.addEventListener('keydown', keydown);
          p.__scannerUXPatch = {captureClick, keydown};
          annotateCards();
        })();
        </script>
        """,
        height=0,
        scrolling=False,
    )


def _install_working_button_transition():
    """Keep Scanner visible while the selected analysis is prepared."""
    components.html(
        """
        <script>
        (() => {
          const p = window.parent;
          const d = p.document;

          const previous = p.__stockWorkspaceTransition;
          if (previous && previous.capture) {
            try { d.removeEventListener('click', previous.capture, true); } catch (_) {}
          }
          p.__stockWorkspaceTransition = null;
          const oldMask = d.getElementById('stock-workspace-transition-mask');
          if (oldMask) oldMask.remove();
          const oldHide = d.getElementById('stock-switch-hide-stale');
          if (oldHide) oldHide.remove();
          try { d.body.style.overflow = ''; } catch (_) {}

          const old = p.__stockWorkingButtonTransition;
          if (old) {
            try { old.capture && d.removeEventListener('click', old.capture, true); } catch (_) {}
            try { old.click && d.removeEventListener('click', old.click); } catch (_) {}
          }

          function preserveScannerDuringAnalysis() {
            let style = d.getElementById('stock-analyze-preserve-scanner');
            if (!style) {
              style = d.createElement('style');
              style.id = 'stock-analyze-preserve-scanner';
              style.textContent = `
                [data-stale="true"],
                div[data-stale="true"],
                .element-container[data-stale="true"] {
                  opacity: 1 !important;
                  filter: none !important;
                  transition: none !important;
                  animation: none !important;
                }
              `;
              d.head.appendChild(style);
            }
          }

          function setWorking(button) {
            if (!button || button.dataset.stockWorking === '1') return;
            button.dataset.stockWorking = '1';
            button.setAttribute('aria-busy', 'true');
            button.style.cursor = 'wait';
            button.style.pointerEvents = 'none';
            const textNode = button.querySelector('p') || button.querySelector('span') || button;
            if (textNode) textNode.textContent = 'Working...';
            preserveScannerDuringAnalysis();
          }

          const capture = (event) => {
            const button = event.target.closest && event.target.closest('button');
            if (!button) return;
            const text = String(button.textContent || '').trim();
            if (!/^Analyze\s+[A-Z0-9.\-]+/i.test(text)) return;
            setWorking(button);
          };

          d.addEventListener('click', capture, true);
          p.__stockWorkingButtonTransition = {capture};
        })();
        </script>
        """,
        height=0,
        scrolling=False,
    )


def _finish_transition_cleanup():
    components.html(
        """
        <script>
        (() => {
          const p = window.parent;
          const d = p.document;

          const previous = p.__stockWorkspaceTransition;
          if (previous && previous.capture) {
            try { d.removeEventListener('click', previous.capture, true); } catch (_) {}
          }
          p.__stockWorkspaceTransition = null;

          const working = p.__stockWorkingButtonTransition;
          if (working) {
            try { working.capture && d.removeEventListener('click', working.capture, true); } catch (_) {}
            try { working.click && d.removeEventListener('click', working.click); } catch (_) {}
          }
          p.__stockWorkingButtonTransition = null;

          const mask = d.getElementById('stock-workspace-transition-mask');
          if (mask) mask.remove();
          const staleStyle = d.getElementById('stock-switch-hide-stale');
          if (staleStyle) staleStyle.remove();
          const preserveStyle = d.getElementById('stock-analyze-preserve-scanner');
          if (preserveStyle) preserveStyle.remove();
          try { d.body.style.overflow = ''; } catch (_) {}
        })();
        </script>
        """,
        height=0,
        scrolling=False,
    )


if view == "Momentum Scanner":
    install_scanner_expander()
    _install_scanner_interactions()
    _install_working_button_transition()
else:
    _finish_transition_cleanup()
