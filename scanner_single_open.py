import streamlit.components.v1 as components


def install_single_open_scanner_details():
    """Allow only one scanner detail card to remain expanded at a time."""
    components.html(
        """
        <script>
        (() => {
          const p = window.parent;
          const d = p.document;

          function symbolFor(el) {
            return String((el && el.textContent) || '').trim().toUpperCase();
          }

          function closeSymbol(symbol) {
            const safe = String(symbol || '').replace(/[^A-Z0-9_-]/g, '-');
            const detail = d.getElementById(`scanner-detail-${safe}`);
            if (detail) detail.remove();

            d.querySelectorAll('.scanner-expandable-ticker').forEach((ticker) => {
              if (symbolFor(ticker) === symbol) {
                ticker.setAttribute('aria-expanded', 'false');
              }
            });
          }

          function prepareForToggle(el) {
            if (!el) return;
            const symbol = symbolFor(el);
            if (!symbol) return;

            if (!p.__scannerExpandedSymbols) {
              p.__scannerExpandedSymbols = new Set();
            }

            const isOpen =
              el.getAttribute('aria-expanded') === 'true' ||
              p.__scannerExpandedSymbols.has(symbol);

            // If the user is opening a different ticker, close every existing
            // card first. The scanner's normal click handler will then open the
            // newly selected ticker immediately afterward.
            if (!isOpen) {
              Array.from(p.__scannerExpandedSymbols).forEach((other) => {
                if (other !== symbol) closeSymbol(other);
              });
              p.__scannerExpandedSymbols.clear();
            }
          }

          // Clean up any old multi-open state left over from a previous version.
          if (p.__scannerExpandedSymbols && p.__scannerExpandedSymbols.size > 1) {
            const open = Array.from(p.__scannerExpandedSymbols);
            const keep = open[open.length - 1];
            open.slice(0, -1).forEach(closeSymbol);
            p.__scannerExpandedSymbols.clear();
            p.__scannerExpandedSymbols.add(keep);
          }

          const old = p.__scannerSingleOpenGuard;
          if (old) {
            try { d.removeEventListener('click', old.click, true); } catch (_) {}
            try { d.removeEventListener('keydown', old.keydown, true); } catch (_) {}
          }

          const click = (event) => {
            const el = event.target.closest && event.target.closest('.scanner-expandable-ticker');
            if (el) prepareForToggle(el);
          };

          const keydown = (event) => {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            const el = event.target.closest && event.target.closest('.scanner-expandable-ticker');
            if (el) prepareForToggle(el);
          };

          // Capture phase runs just before the existing expander toggle handler.
          d.addEventListener('click', click, true);
          d.addEventListener('keydown', keydown, true);
          p.__scannerSingleOpenGuard = { click, keydown };
        })();
        </script>
        """,
        height=0,
        scrolling=False,
    )
