/* Price at the pointer, not the nearest candle close. No Streamlit reruns. */
(function () {
  function cursorPoint(plot, event) {
    const layout = plot._fullLayout;
    const x = layout?.xaxis, y = layout?.yaxis;
    const rect = plot.getBoundingClientRect();
    if (!x || !y || typeof y.p2d !== "function" || !rect.width || !rect.height) return null;
    const px = (event.clientX - rect.left) * layout.width / rect.width;
    const py = (event.clientY - rect.top) * layout.height / rect.height;
    // Read the current Plotly transform on every move: pan, zoom and resize change it.
    if (px < x._offset || px > x._offset + x._length ||
        py < y._offset || py > y._offset + y._length) return null;
    const price = y.p2d(py - y._offset);
    if (!Number.isFinite(price)) return null;
    return {price, x: px, y: py, left: x._offset, right: x._offset + x._length,
      top: y._offset, bottom: y._offset + y._length};
  }

  function install(win) {
    win.__analyzerPriceCursor?.();
    const doc = win.document;
    const controller = new win.AbortController();
    const options = {capture: true, passive: true, signal: controller.signal};
    let label;
    const hide = () => { label?.remove(); label = null; };
    const move = event => {
      const plot = event.target.closest?.(".st-key-ao_chart .js-plotly-plot");
      if (!plot || event.buttons || event.pointerType === "touch" ||
          event.target.closest?.(".modebar")) { hide(); return; }
      const point = cursorPoint(plot, event);
      if (!point) { hide(); return; }
      if (!label || label.parentNode !== plot) {
        hide();
        label = doc.createElement("span");
        label.className = "ao-price-cursor";
        label.setAttribute("aria-hidden", "true");
        plot.appendChild(label);
      }
      label.textContent = "$" + point.price.toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: Math.abs(point.price) < 1 ? 4 : 2
      });
      // Flip at the edges so the small label stays inside the price panel.
      const left = point.x + 12 + label.offsetWidth <= point.right
        ? point.x + 12 : point.x - label.offsetWidth - 12;
      label.style.left = Math.max(point.left, left) + "px";
      label.style.top = Math.max(point.top, Math.min(point.y - label.offsetHeight - 8,
        point.bottom - label.offsetHeight)) + "px";
    };
    doc.addEventListener("pointermove", move, options);
    // Plotly removes its temporary drag cover on mouseup. Resolve the chart
    // underneath afterwards so the label resumes at the release position.
    doc.addEventListener("mouseup", event => win.requestAnimationFrame(() => {
      if (controller.signal.aborted || event.sourceCapabilities?.firesTouchEvents) return;
      move({target: doc.elementFromPoint(event.clientX, event.clientY) || doc,
        clientX: event.clientX, clientY: event.clientY, buttons: event.buttons});
    }), options);
    doc.addEventListener("pointerdown", hide, options);
    doc.addEventListener("pointerout", event => {
      if (!event.relatedTarget?.closest?.(".st-key-ao_chart .js-plotly-plot")) hide();
    }, options);
    doc.addEventListener("scroll", hide, options);
    win.addEventListener("resize", hide, options);
    win.addEventListener("blur", hide, options);
    win.__analyzerPriceCursor = () => { controller.abort(); hide(); };
  }

  if (typeof module !== "undefined" && module.exports) module.exports = {cursorPoint, install};
  else install(window);
})();
