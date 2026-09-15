/* Candle-only visible-window scaling. Plotly owns gestures and axis rendering. */
(function () {
  function visibleRange(traces, axis, range) {
    if (!axis || !range || range.length !== 2) return null;
    const limits = range.map(v => axis.type === 'category' ? Number(v) : axis.r2l(v));
    if (!limits.every(Number.isFinite)) return null;
    const left = Math.min(...limits), right = Math.max(...limits);
    let low = Infinity, high = -Infinity;
    for (const t of traces || []) {
      if (t.type !== 'candlestick' || t.visible === false || t.visible === 'legendonly' || (t.yaxis && t.yaxis !== 'y')) continue;
      for (let i = 0; i < (t.x || []).length; i++) {
        const x = axis.d2l(t.x[i]);
        const l = Number(t.low[i]), h = Number(t.high[i]);
        if (Number.isFinite(x) && x >= left && x <= right && Number.isFinite(l) && Number.isFinite(h) && l > 0 && h >= l) {
          low = Math.min(low, l); high = Math.max(high, h);
        }
      }
    }
    if (!Number.isFinite(low)) return null; // Empty windows keep their last useful range.
    const pad = Math.max((high-low)*.08, high*.005);
    return [low-pad, high+pad];
  }
  function changed(a, b) {
    return !a || !b || a.length !== b.length || a.some((v,i) => String(v) !== String(b[i]));
  }
  function install(win) {
    win.__analyzerViewportCleanup?.();
    const bindings = new Map(), saved = win.__analyzerViewportSaved || new Map();
    win.__analyzerViewportSaved = saved;
    const bind = plot => {
      if (!plot.isConnected || bindings.has(plot) || !plot._fullLayout?.meta?.analyzerViewport || !plot.on || !win.Plotly) return;
      let busy = false, disposed = false, pending = false;
      let revision, state, lastX, lastY;
      const remember = () => {
        lastX = [...plot._fullLayout.xaxis.range]; lastY = [...plot._fullLayout.yaxis.range];
        state.x = lastX; state.y = lastY;
      };
      const apply = update => {
        if (busy) { pending = true; return; }
        if (update['xaxis.range']) state.x = [...update['xaxis.range']];
        if (update['yaxis.range']) state.y = [...update['yaxis.range']];
        busy = true; plot.dataset.viewportBusy = "1";
        Promise.resolve(win.Plotly.relayout(plot, update)).finally(() => {
          busy = false; plot.dataset.viewportBusy = "0";
          if (disposed || !plot._fullLayout?.xaxis || !plot._fullLayout?.yaxis) return;
          lastX = [...plot._fullLayout.xaxis.range];
          lastY = [...plot._fullLayout.yaxis.range];
          if (pending) { pending = false; sync(); }
        });
      };
      const fit = () => {
        const y = visibleRange(plot._fullData, plot._fullLayout.xaxis, plot._fullLayout.xaxis.range);
        if (y && changed(y, plot._fullLayout.yaxis.range)) apply({'yaxis.range':y, 'yaxis.autorange':false});
      };
      const sync = () => {
        if (disposed || busy || !plot._fullLayout?.xaxis || !plot._fullLayout?.yaxis) return;
        const scope = plot.closest?.('[data-testid="stElementContainer"]')?.className.match(/\bst-key-\S+/)?.[0] || '';
        const current = scope + ':' + plot._fullLayout.uirevision;
        if (revision !== current) {
          revision = current;
          state = saved.get(current);
          if (!state) {
            state = {mode:'auto', timeManual:false, defaultX:[...plot._fullLayout.xaxis.range],
                     x:[...plot._fullLayout.xaxis.range], y:[...plot._fullLayout.yaxis.range]};
            saved.set(current, state);
            if (saved.size > 40) saved.delete(saved.keys().next().value);
          }
        }
        // Reset follows the latest server-provided default, while manual axes stay saved.
        if (plot._fullLayout.meta?.defaultX) {
          state.defaultX = [...plot._fullLayout.meta.defaultX];
          if (!state.timeManual) state.x = [...state.defaultX];
        }
        plot.dataset.priceScaleMode = state.mode;
        // Streamlit can replace the graph or supply new initial ranges on an
        // append. User relayout events update saved ranges; server renders do
        // not acquire permission to overwrite a manually chosen viewport.
        const restore = {};
        if (state.x && changed(state.x, plot._fullLayout.xaxis.range)) {
          restore['xaxis.range'] = state.x; restore['xaxis.autorange'] = false;
        }
        if (state.mode === 'manual' && state.y && changed(state.y, plot._fullLayout.yaxis.range)) {
          restore['yaxis.range'] = state.y; restore['yaxis.autorange'] = false;
        }
        if (state.mode === 'all' && !plot._fullLayout.yaxis.autorange) restore['yaxis.autorange'] = true;
        if (Object.keys(restore).length) { pending = true; apply(restore); return; }
        if (state.mode === 'auto') fit();
        lastX = [...plot._fullLayout.xaxis.range]; lastY = [...plot._fullLayout.yaxis.range];
      };
      const relayout = event => {
        if (busy || !state) return;
        const xKeys = Object.keys(event).some(k => /^xaxis\d*\.(range|autorange)/.test(k));
        const yKeys = Object.keys(event).some(k => /^yaxis\.(range|autorange)/.test(k));
        // Axis-only price drags stay manual. Horizontal pans and time-axis
        // zooms keep following visible candles while auto scale is enabled.
        if (xKeys) state.timeManual = true;
        if (event['xaxis.autorange'] === true || event['xaxis2.autorange'] === true) { state.mode = 'auto'; state.timeManual = false; }
        else if (yKeys && changed(lastY, plot._fullLayout.yaxis.range) && !xKeys) state.mode = 'manual';
        remember();
        if (state.mode === 'auto' && xKeys) fit();
        plot.dataset.priceScaleMode = state.mode;
      };
      const button = event => {
        const label = event.button?.label;
        if (label === 'Auto scale') { state.mode = 'auto'; fit(); }
        else if (label === 'Reset view') {
          state.mode = 'auto'; state.timeManual = false; pending = true;
          apply({'xaxis.range':state.defaultX, 'xaxis.autorange':false});
        } else if (label === 'All levels') {
          state.mode = 'all'; apply({'yaxis.autorange':true});
        }
        plot.dataset.priceScaleMode = state.mode;
      };
      plot.on('plotly_relayout', relayout);
      const afterplot = () => win.requestAnimationFrame(sync);
      plot.on('plotly_afterplot', afterplot);
      plot.on('plotly_buttonclicked', button);
      bindings.set(plot, Object.assign(() => {
        disposed = true; plot.dataset.viewportBusy = "0";
        plot.removeListener?.('plotly_relayout', relayout);
        plot.removeListener?.('plotly_afterplot', afterplot);
        plot.removeListener?.('plotly_buttonclicked', button);
      }, {on:plot.on}));
      sync();
    };
    const scan = () => {
      for (const [plot, cleanup] of bindings) if (!plot.isConnected || cleanup.on !== plot.on) {cleanup(); bindings.delete(plot);}
      win.document.querySelectorAll('.js-plotly-plot').forEach(bind);
    };
    const observer = new win.MutationObserver(scan);
    observer.observe(win.document.body, {childList:true, subtree:true});
    scan();
    win.__analyzerViewportCleanup = () => {observer.disconnect(); bindings.forEach(cleanup => cleanup());};
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {visibleRange, changed, install};
  else install(window);
})();
