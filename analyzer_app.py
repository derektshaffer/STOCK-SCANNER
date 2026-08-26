from pathlib import Path
import runpy

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

# Render the combined app first so app.py's st.set_page_config remains the
# first Streamlit command on each rerun.
runpy.run_path(str(target), run_name="__main__")

# Attach the lightweight scanner detail renderer.
install_scanner_expander()


def _install_scanner_interactions():
    """Polish scanner detail behavior without another DOM observer.

    - Only one ticker detail card can be open at once.
    - Clicking anywhere on the expanded detail card closes it.
    - Technical labels inside the injected card get the same hover tooltips
      used by the rest of the combined app.
    """
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

          // A small visual cue makes the close behavior discoverable without
          // adding another control to the card.
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

          // Capture phase runs before scanner_expand.py's ticker click handler.
          // That lets us collapse the previous ticker before the new one opens.
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

            // scanner_expand.py opens/closes the selected ticker in the bubble
            // phase. Annotate the newly rendered card right after that finishes.
            p.setTimeout(annotateCards, 0);
          };

          const keydown = (event) => {
            const card = event.target.closest && event.target.closest('.scanner-inline-detail');
            if (!card || (event.key !== 'Enter' && event.key !== ' ')) return;
            // Do not collapse when keyboard focus is on a tooltip term inside
            // the card; Enter/Space there should remain harmless.
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


_install_scanner_interactions()
