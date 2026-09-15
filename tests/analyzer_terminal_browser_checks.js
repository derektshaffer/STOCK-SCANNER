/* Real Plotly, generated candles. Exercise dispatched input and redraws. */
(function(){
 const button=document.getElementById('terminal-test-run'),output=document.getElementById('terminal-test-results');
 if(!button)return;
 const panel=document.getElementById('viewport-test-panel');
 if(!document.getElementById('terminal-save-view')){
  const save=document.createElement('button');save.id='terminal-save-view';save.textContent='Hold manual view for refresh test';panel.appendChild(save);
  const verify=document.createElement('button');verify.id='terminal-verify-view';verify.textContent='Verify preserved view';panel.appendChild(verify);
 }
 document.getElementById('terminal-save-view').onclick=async()=>{
  const p=document.querySelector('.st-key-ao_chart .js-plotly-plot');
  await Plotly.relayout(p,{'xaxis.range':[10,35],'yaxis.range':[8,20],'xaxis.autorange':false,'yaxis.autorange':false});
  await new Promise(r=>setTimeout(r,150));
  await Plotly.relayout(p,{'yaxis.range':[8,20],'yaxis.autorange':false});
  await new Promise(r=>setTimeout(r,150));
  window.__terminalFixtureView={x:[...p._fullLayout.xaxis.range],y:[...p._fullLayout.yaxis.range],count:p._fullData[0].x.length};
  output.textContent='Saved manual view: '+JSON.stringify(window.__terminalFixtureView);
 };
 document.getElementById('terminal-verify-view').onclick=()=>{
  const p=document.querySelector('.st-key-ao_chart .js-plotly-plot'),saved=window.__terminalFixtureView;
  const same=saved&&JSON.stringify(saved.x)===JSON.stringify(p._fullLayout.xaxis.range)&&JSON.stringify(saved.y)===JSON.stringify(p._fullLayout.yaxis.range);
  output.textContent=(same?'PASS':'FAIL')+' refresh/rerun/resize preserves manual axes; candles '+saved?.count+' -> '+p._fullData[0].x.length+'; chart width '+p._fullLayout.width;
 };
 button.onclick=async()=>{
  const results=[],check=(name,value)=>{results.push((value?'PASS ':'FAIL ')+name);output.textContent=results.join('\n');if(!value)throw Error(name);};
  const settle=()=>new Promise(r=>setTimeout(r,350));
  const close=(a,b)=>a.every((v,i)=>Math.abs(v-b[i])<1e-6);
  const p=document.querySelector('.st-key-ao_chart .js-plotly-plot');
  const at=(fx,fy)=>{const r=p.getBoundingClientRect(),l=p._fullLayout;return {clientX:r.left+fx*r.width/l.width,clientY:r.top+fy*r.height/l.height};};
  const wheel=(delta,ctrl=false,deltaX=0)=>{const l=p._fullLayout;const pt=at(l.xaxis._offset+l.xaxis._length*.3,l.yaxis._offset+l.yaxis._length*.5);p.dispatchEvent(new WheelEvent('wheel',{...pt,deltaY:delta,deltaX,ctrlKey:ctrl,bubbles:true,cancelable:true}));};
  const drag=async(x,y,dx,dy)=>{let pt=at(x,y);p.dispatchEvent(new PointerEvent('pointerdown',{...pt,button:0,buttons:1,pointerId:999,bubbles:true,cancelable:true}));pt=at(x+dx,y+dy);p.dispatchEvent(new PointerEvent('pointermove',{...pt,buttons:1,pointerId:999,bubbles:true,cancelable:true}));p.dispatchEvent(new PointerEvent('pointerup',{...pt,button:0,pointerId:999,bubbles:true}));await settle();};
  try{
   p.emit('plotly_buttonclicked',{button:{label:'Reset view'}});await settle();
   const initial=[...p._fullLayout.xaxis.range],width=initial[1]-initial[0];
   wheel(-100,true);await settle();const zoomed=[...p._fullLayout.xaxis.range];
   check('pinch contracts time range',zoomed[1]-zoomed[0]<width);
   check('pinch anchor stays under pointer',Math.abs((zoomed[0]+(zoomed[1]-zoomed[0])*.3)-(initial[0]+width*.3))<.01);
   for(let i=0;i<12;i++)wheel(-6);await settle();
   check('rapid wheel input is coalesced without losing zoom',p._fullLayout.xaxis.range[1]-p._fullLayout.xaxis.range[0]<zoomed[1]-zoomed[0]);
   let l=p._fullLayout,x=l.xaxis._offset+l.xaxis._length*.5,y=l.yaxis._offset+l.yaxis._length*.5;
   const oldX=[...l.xaxis.range];await drag(x,y,40,0);
   check('horizontal drag pans through time',!close(p._fullLayout.xaxis.range,oldX));
   l=p._fullLayout;const beforeY=[...l.yaxis.range];await drag(l.xaxis._offset+l.xaxis._length+35,y,0,40);
   const manualY=[...p._fullLayout.yaxis.range];
   check('right axis drag expands price range',manualY[1]-manualY[0]>beforeY[1]-beforeY[0]);
   check('right axis drag enables manual scaling',p.dataset.priceScaleMode==='manual');
   l=p._fullLayout;const beforeTime=[...l.xaxis.range];await drag(x,l.yaxis2._offset+l.yaxis2._length+18,45,0);
   check('bottom axis drag changes time scale',!close(p._fullLayout.xaxis.range,beforeTime));
   check('time scaling preserves manual price scale',close(p._fullLayout.yaxis.range,manualY));
   wheel(50);await settle();check('wheel preserves manual price scale',close(p._fullLayout.yaxis.range,manualY));
   const beforeAppend=[...p._fullLayout.xaxis.range],data=JSON.parse(JSON.stringify(p.data)),layout=JSON.parse(JSON.stringify(p.layout));
   const original=JSON.parse(JSON.stringify(data));data[0].high[data[0].high.length-1]+=1;
   await Plotly.react(p,data,layout,p._context);await settle();
   check('current candle refresh preserves X and manual Y',close(p._fullLayout.xaxis.range,beforeAppend)&&close(p._fullLayout.yaxis.range,manualY));
   const oldWidth=p._fullLayout.width;await Plotly.relayout(p,{width:480});await settle();
   check('responsive relayout keeps manual price viewport (width '+p._fullLayout.width+', Y '+p._fullLayout.yaxis.range+')',p._fullLayout.width>0&&close(p._fullLayout.yaxis.range,manualY));
   await Plotly.relayout(p,{width:oldWidth});await settle();
   l=p._fullLayout;let pt=at(l.xaxis._offset+l.xaxis._length*.5,l.yaxis._offset+l.yaxis._length*.5);
   p.dispatchEvent(new PointerEvent('pointermove',{...pt,bubbles:true}));
   check('crosshair shows OHLC and Pacific timestamp',p.querySelector('.ao-crosshair').style.display==='block'&&p.querySelector('.ao-crosshair-ohlc').textContent.includes('O ')&&p.querySelector('.ao-crosshair-time').textContent.includes(' PT'));
   await Plotly.react(p,original,layout,p._context);await settle();
   p.emit('plotly_buttonclicked',{button:{label:'Reset view'}});await settle();
   check('reset restores automatic scale and latest default window',p.dataset.priceScaleMode==='auto'&&close(p._fullLayout.xaxis.range,initial));
   output.dataset.result='passed';
  }catch(e){output.dataset.result='failed';output.textContent+='\n'+e.message;}
 };
})();
