/* Analyzer-only gestures. One relayout in flight; newest intent wins. No reruns. */
(function () {
  function bounded(range, count) {
    const width = Math.min(count, Math.max(Math.min(6, count), range[1] - range[0]));
    const left = Math.max(-.5, Math.min(count - .5 - width, range[0]));
    return [left, left + width];
  }
  function zoomRange(range, fraction, delta, count) {
    const span = range[1] - range[0], anchor = range[0] + span * fraction;
    const width = span * Math.exp(Math.max(-.6, Math.min(.6, delta * .0025)));
    return bounded([anchor - width * fraction, anchor + width * (1-fraction)], count);
  }
  function priceRange(range, delta) {
    const mid = (range[0]+range[1])/2;
    const half = Math.max(Math.abs(mid)*.00005, (range[1]-range[0])/2 * Math.exp(Math.max(-5, Math.min(5, delta*.008))));
    return [mid-half,mid+half];
  }
  function install(win) {
    win.__analyzerTerminalCleanup?.();
    const bindings = new Map();
    function bind(plot) {
      if (bindings.has(plot) || !plot._fullLayout?.meta?.analyzerTerminal || !plot.on || !win.Plotly) return;
      const controller = new win.AbortController(), signal = controller.signal;
      let drag, queued = {}, inFlight = false, frame = null, intentX, intentY;
      const hide = () => overlay.style.display = 'none';
      const overlay = win.document.createElement('div');
      overlay.className = 'ao-crosshair'; overlay.setAttribute('aria-hidden','true');
      overlay.innerHTML = '<i class="ao-crosshair-v"></i><i class="ao-crosshair-h"></i><span class="ao-crosshair-price"></span><span class="ao-crosshair-time"></span><span class="ao-crosshair-ohlc"></span>';
      plot.appendChild(overlay);
      const [vertical,horizontal,priceLabel,timeLabel,ohlc] = overlay.children;
      const flush = () => {
        frame = null;
        if (inFlight || signal.aborted || !Object.keys(queued).length) return;
        // Candle autoscaling shares the same Plotly instance. Wait until its
        // relayout finishes so a fast price-axis drag cannot be mistaken for
        // the autoscaler's own event and overwritten.
        if (plot.dataset.viewportBusy === '1') { frame = win.requestAnimationFrame(flush); return; }
        const update = queued; queued = {}; inFlight = true;
        Promise.resolve(win.Plotly.relayout(plot,update)).catch(() => {}).finally(() => {
          inFlight = false;
          if (signal.aborted) return;
          if (Object.keys(queued).length) frame = win.requestAnimationFrame(flush);
          else { intentX = intentY = null; }
        });
      };
      const queue = update => {
        Object.assign(queued,update);
        if (update['xaxis.range']) intentX = update['xaxis.range'];
        if (update['yaxis.range']) intentY = update['yaxis.range'];
        if (frame === null && !inFlight) frame = win.requestAnimationFrame(flush);
      };
      const geometry = event => {
        const l = plot._fullLayout, rect = plot.getBoundingClientRect();
        if (!l?.xaxis || !l.yaxis || !rect.width || !rect.height) return null;
        return {l, x:(event.clientX-rect.left)*l.width/rect.width,
          y:(event.clientY-rect.top)*l.height/rect.height, xa:l.xaxis, ya:l.yaxis,
          bottom:(l.yaxis2 || l.yaxis)._offset + (l.yaxis2 || l.yaxis)._length, count:plot._fullData[0].x.length};
      };
      const wheel = event => {
        const p = geometry(event); if (!p || event.target.closest?.('.updatemenu-container,.modebar')) return;
        const {x,y,xa,ya,count} = p;
        if (x < xa._offset || x > xa._offset+xa._length || y < ya._offset || y > p.bottom) return;
        event.preventDefault(); event.stopImmediatePropagation(); hide();
        const range = intentX || xa.range.map(Number), unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 300 : 1;
        if (!event.ctrlKey && Math.abs(event.deltaX)>Math.abs(event.deltaY)) {
          const shift = event.deltaX*unit/xa._length*(range[1]-range[0]);
          queue({'xaxis.range':bounded(range.map(v=>v+shift),count),'xaxis.autorange':false});
        } else queue({'xaxis.range':zoomRange(range,(x-xa._offset)/xa._length,event.deltaY*unit,count),'xaxis.autorange':false});
      };
      const down = event => {
        if (event.button !== 0 || event.pointerType === 'touch' || event.target.closest?.('.updatemenu-container,.modebar')) return;
        const p = geometry(event); if (!p) return;
        const {x,y,xa,ya,l} = p;
        let mode;
        if (x > xa._offset+xa._length && x < l.width && y >= ya._offset && y <= ya._offset+ya._length) mode='price';
        else if (x >= xa._offset && x <= xa._offset+xa._length && y > p.bottom && y < l.height) mode='time';
        else if (x >= xa._offset && x <= xa._offset+xa._length && y >= ya._offset && y <= p.bottom) mode='pan';
        if (!mode) return;
        event.preventDefault(); event.stopImmediatePropagation(); hide();
        drag={mode,x,y,range:[...(mode==='price' ? intentY || ya.range : intentX || xa.range)],length:xa._length,count:p.count,pointer:event.pointerId};
        try { plot.setPointerCapture(event.pointerId); } catch (_) { /* Dispatched test events have no active hardware pointer. */ }
      };
      const move = event => {
        const p = geometry(event); if (!p) return;
        if (drag) {
          event.preventDefault(); event.stopImmediatePropagation();
          const dx=p.x-drag.x,dy=p.y-drag.y;
          if (drag.mode==='price') queue({'yaxis.range':priceRange(drag.range,dy),'yaxis.autorange':false});
          else if (drag.mode==='time') queue({'xaxis.range':zoomRange(drag.range,1,-dx*2,drag.count),'xaxis.autorange':false});
          else {
            const shift=-dx/drag.length*(drag.range[1]-drag.range[0]);
            queue({'xaxis.range':bounded(drag.range.map(v=>v+shift),drag.count),'xaxis.autorange':false});
          }
          return;
        }
        const {x,y,xa,ya} = p;
        if (event.buttons || x<xa._offset || x>xa._offset+xa._length || y<ya._offset || y>ya._offset+ya._length) {hide();return;}
        const trace=plot._fullData[0], idx=Math.round(xa.p2l(x-xa._offset));
        if (idx<0 || idx>=trace.x.length) {hide();return;}
        const price=ya.p2d(y-ya._offset), fmt=v=>Number(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:Math.abs(Number(v))<1?4:2});
        overlay.style.display='block';
        Object.assign(vertical.style,{left:x+'px',top:ya._offset+'px',height:(p.bottom-ya._offset)+'px'});
        Object.assign(horizontal.style,{left:xa._offset+'px',top:y+'px',width:xa._length+'px'});
        priceLabel.textContent='$'+fmt(price);
        Object.assign(priceLabel.style,{left:(xa._offset+xa._length+2)+'px',top:(y-10)+'px'});
        timeLabel.textContent=trace.x[idx] + (plot._fullLayout.uirevision.includes(':D:')?'':' PT');
        Object.assign(timeLabel.style,{left:Math.max(xa._offset,Math.min(x-75,xa._offset+xa._length-160))+'px',top:(p.bottom+3)+'px'});
        ohlc.textContent=`O ${fmt(trace.open[idx])}   H ${fmt(trace.high[idx])}   L ${fmt(trace.low[idx])}   C ${fmt(trace.close[idx])}`;
        Object.assign(ohlc.style,{left:xa._offset+'px',top:Math.max(0,ya._offset-23)+'px',maxWidth:xa._length+'px'});
      };
      const up = event => { if(drag) { try { plot.releasePointerCapture?.(drag.pointer); } catch (_) {} drag=null; } };
      plot.addEventListener('wheel',wheel,{capture:true,passive:false,signal});
      plot.addEventListener('pointerdown',down,{capture:true,signal});
      plot.addEventListener('pointermove',move,{capture:true,signal});
      plot.addEventListener('pointerup',up,{capture:true,signal});
      plot.addEventListener('pointercancel',up,{capture:true,signal});
      plot.addEventListener('pointerleave',hide,{signal});
      plot.addEventListener('dblclick',event=>{
        if (!plot._fullLayout || event.target.closest?.('.updatemenu-container,.modebar')) return;
        event.preventDefault();event.stopImmediatePropagation();
        plot.emit('plotly_buttonclicked',{button:{label:'Reset view'}});
      },{capture:true,signal});
      plot.on('plotly_afterplot',hide);
      bindings.set(plot,{on:plot.on,cleanup:()=>{controller.abort();if(frame!==null)win.cancelAnimationFrame(frame);plot.removeListener?.('plotly_afterplot',hide);overlay.remove();}});
    }
    const scan=()=>{
      for(const [plot,b] of bindings) if(!plot.isConnected || b.on!==plot.on){b.cleanup();bindings.delete(plot);}
      win.document.querySelectorAll('.js-plotly-plot').forEach(bind);
    };
    const observer=new win.MutationObserver(scan);observer.observe(win.document.body,{childList:true,subtree:true});scan();
    win.__analyzerTerminalCleanup=()=>{observer.disconnect();bindings.forEach(b=>b.cleanup());};
  }
  if(typeof module!=='undefined' && module.exports) module.exports={bounded,zoomRange,priceRange,install};
  else install(window);
})();
