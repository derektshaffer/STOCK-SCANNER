from pathlib import Path
import runpy

import streamlit as st
import streamlit.components.v1 as components

from scanner_expand import install_scanner_expander

# Compatibility entrypoint for Streamlit deployments that were originally
# configured to launch analyzer_app.py. Re-execute the combined app on every
# Streamlit rerun instead of importing it as a cached Python module.
target = Path(__file__).with_name("app.py")

if not target.exists():
    raise FileNotFoundError(
        "app.py was not found in the repository root. "
        "The combined Momentum Scanner + Stock Analyzer requires app.py."
    )

# app.py owns st.set_page_config, so let it render the complete requested view
# first. A browser loading mask from the previous view (when present) remains
# visible while this heavy rerun is happening and is removed only afterward.
runpy.run_path(str(target), run_name="__main__")
view = st.session_state.get("app_view", "Momentum Scanner")


def _install_scanner_interactions():
    """Scanner card behavior without a page-wide DOM observer."""
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


def _finish_transition_and_prepare_next(view_name):
    """Remove the loading mask after the new page is complete and arm the next transition."""
    mode = "scanner" if view_name == "Momentum Scanner" else "analyzer"
    components.html(
        f"""
        <script>
        (() => {{
          const p = window.parent;
          const d = p.document;
          const mode = {mode!r};

          // app.py has now completed the requested view. Only now reveal it.
          const existing = d.getElementById('stock-workspace-transition-mask');
          if (existing) existing.remove();
          try {{ d.body.style.overflow = ''; }} catch (_) {{}}

          // The glossary has already annotated the completed page. Its
          // full-page MutationObserver is unnecessary between reruns and adds
          // noticeable work while Streamlit replaces large dashboards.
          const tech = p.__stockTechnicalTooltips;
          if (tech && tech.observer) {{
            try {{ tech.observer.disconnect(); }} catch (_) {{}}
          }}

          const old = p.__stockWorkspaceTransition;
          if (old && old.capture) {{
            try {{ d.removeEventListener('click', old.capture, true); }} catch (_) {{}}
          }}

          function showMask(label) {{
            let mask = d.getElementById('stock-workspace-transition-mask');
            if (!mask) {{
              mask = d.createElement('div');
              mask.id = 'stock-workspace-transition-mask';
              mask.style.cssText = [
                'position:fixed','inset:0','z-index:2147482500',
                'background:#07111f','display:flex','align-items:flex-start',
                'justify-content:center','padding:120px 24px 40px','box-sizing:border-box'
              ].join(';');
              d.body.appendChild(mask);
            }}
            const safe = String(label || 'Stock Analyzer').replace(/[<>&]/g, '');
            mask.innerHTML = `
              <div style="width:min(560px,100%);background:#101b2d;border:1px solid #304865;border-radius:18px;padding:28px 30px;color:#f4f7fb;box-shadow:0 18px 50px rgba(0,0,0,.35);font-family:inherit">
                <div style="display:flex;align-items:center;gap:16px">
                  <div style="width:24px;height:24px;border:3px solid #355071;border-top-color:#65e98d;border-radius:50%;animation:stockSpin .8s linear infinite"></div>
                  <div>
                    <div style="font-size:20px;font-weight:900">Loading ${{safe}}…</div>
                    <div style="font-size:13px;color:#91a7c2;margin-top:5px">Clearing the previous view and building fresh market data.</div>
                  </div>
                </div>
              </div>`;
            if (!d.getElementById('stock-workspace-transition-style')) {{
              const style = d.createElement('style');
              style.id = 'stock-workspace-transition-style';
              style.textContent = '@keyframes stockSpin{{to{{transform:rotate(360deg)}}}}';
              d.head.appendChild(style);
            }}
            try {{ d.body.style.overflow = 'hidden'; }} catch (_) {{}}
          }}

          const capture = (event) => {{
            if (mode === 'scanner') {{
              const button = event.target.closest && event.target.closest('button');
              if (button) {{
                const text = String(button.textContent || '').trim();
                const match = text.match(/^Analyze\s+([A-Z0-9.\-]+)/i);
                if (match) {{
                  showMask(`${{match[1].toUpperCase()}} Analyzer`);
                  return;
                }}
              }}
              const label = event.target.closest && event.target.closest('label');
              if (label && String(label.textContent || '').trim() === 'Stock Analyzer') {{
                showMask('Stock Analyzer');
              }}
            }} else {{
              const label = event.target.closest && event.target.closest('label');
              if (label && String(label.textContent || '').trim() === 'Momentum Scanner') {{
                showMask('Momentum Scanner');
                return;
              }}

              // Clicking a saved ticker triggers a fresh Analyzer rerun too.
              const saved = event.target.closest && event.target.closest('.st-key-saved_stocks_top button');
              if (saved) {{
                const text = String(saved.textContent || '').trim();
                if (text && !/^Remove\b/i.test(text) && !/^☆\s*Save\b/i.test(text)) {{
                  showMask(`${{text.replace(/^●\s*/, '')}} Analyzer`);
                }}
              }}
            }}
          }};

          d.addEventListener('click', capture, true);
          p.__stockWorkspaceTransition = {{capture, mode}};
        }})();
        </script>
        """,
        height=0,
        scrolling=False,
    )


# Scanner-only browser helpers must never be installed on the Analyzer page.
if view == "Momentum Scanner":
    install_scanner_expander()
    _install_scanner_interactions()

# Remove any transition mask only after the selected view has fully rendered,
# then install the lightweight click listener used for the next navigation.
_finish_transition_and_prepare_next(view)
