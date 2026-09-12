/* Compact visible timeline labels without changing candle data or chart state. */
(function () {
  function compactLabels(values) {
    let previousDay;
    return values.map(value => {
      const match = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}:\d{2})$/.exec(value);
      if (!match) return [value]; // Daily dates keep their native labels.
      const day = value.slice(0, 10), lines = [match[4]];
      if (day !== previousDay) {
        const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        lines.push(`${months[Number(match[2])-1]} ${match[3]}, ${match[1]}`);
      }
      previousDay = day;
      return lines;
    });
  }

  function install(win) {
    win.__analyzerTimeline?.();
    const doc = win.document;
    let plot;
    const format = () => {
      if (!plot) return;
      const labels = [...plot.querySelectorAll('.xaxislayer-above .x2tick text')]
        .filter(label => label.style.display !== 'none');
      const values = labels.map(label => label.getAttribute('data-unformatted') || label.textContent);
      compactLabels(values).forEach((lines, i) => {
        if (!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(values[i])) return;
        const label = labels[i];
        label.replaceChildren(...lines.map((line, n) => {
          const span = doc.createElementNS('http://www.w3.org/2000/svg', 'tspan');
          span.setAttribute('x', label.getAttribute('x') || '0');
          span.setAttribute('dy', n ? '1.3em' : '0em');
          span.textContent = line;
          return span;
        }));
      });
    };
    const bind = () => {
      const candidate = doc.querySelector('.st-key-ao_chart .js-plotly-plot');
      if (candidate === plot || !candidate || typeof candidate.on !== 'function') return;
      plot?.removeListener?.('plotly_afterplot', format);
      plot = candidate;
      plot.on('plotly_afterplot', format);
      format();
    };
    // Streamlit can replace the plot after the script runs. Observe mounts only;
    // the identity guard leaves ordinary DOM changes alone. Plotly owns zoom.
    // No polling, page reparenting, provider calls or Streamlit reruns.
    const observer = new win.MutationObserver(bind);
    observer.observe(doc.body, {childList:true, subtree:true});
    bind();
    win.__analyzerTimeline = () => {
      observer.disconnect();
      plot?.removeListener?.('plotly_afterplot', format);
      plot = null;
    };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {compactLabels, install};
  else install(window);
})();
