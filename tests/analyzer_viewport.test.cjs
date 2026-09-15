const {test}=require('node:test');
const assert=require('node:assert/strict');
const {EventEmitter}=require('node:events');
const {visibleRange,install}=require('../analyzer_viewport.js');
const candle={type:'candlestick',x:['a','b','c','d'],low:[9,10,20,30],high:[10,11,21,31]};
const axis={type:'category',d2l:x=>candle.x.indexOf(x)};
test('fits only visible candles with padding',()=>assert.deepEqual(visibleRange([candle],axis,[1.5,2.5]),[19.895,21.105]));
test('ignores targets volume and hidden candles',()=>assert.deepEqual(visibleRange([candle,{type:'scatter',x:['a'],y:[1000]},{...candle,visible:false,high:[1000,1000,1000,1000]}],axis,[-.5,.5]),[8.92,10.08]));
test('empty window preserves range by returning no update',()=>assert.equal(visibleRange([candle],axis,[7,9]),null));
test('invalid prices are skipped',()=>assert.equal(visibleRange([{...candle,low:[NaN,0,-1,40]}],axis,[-1,4]),null));
test('reversed windows select the same data',()=>assert.deepEqual(visibleRange([candle],axis,[2.5,1.5]),visibleRange([candle],axis,[1.5,2.5])));
test('date axes compare instants not category offsets',()=>{const t={...candle,x:['2026-09-11 06:30','2026-09-11 06:35','2026-09-11 06:40','2026-09-11 06:45']};const a={type:'date',d2l:Date.parse,r2l:Date.parse};assert.deepEqual(visibleRange([t],a,[t.x[2],t.x[2]]),[19.895,21.105]);});
test('daily labels retain their date window',()=>{const t={...candle,x:['2026-09-08','2026-09-09','2026-09-10','2026-09-11']};assert.deepEqual(visibleRange([t],{type:'date',d2l:Date.parse,r2l:Date.parse},['2026-09-09','2026-09-10']),[9.12,21.88]);});
const flush=async()=>{for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve));};
function fixture(scope=""){
 const p=new EventEmitter();p.closest=()=>({className:'st-key-'+scope});p.dataset={};p.isConnected=true;p._fullData=[structuredClone(candle)];
 p._fullLayout={meta:{analyzerViewport:true},uirevision:'TEST:1m',dragmode:'pan',xaxis:{...axis,range:[-.5,3.5]},yaxis:{range:[8,32]}};
 const observers=[];let calls=0;
 const win={document:{body:{},querySelectorAll:()=>[p]},requestAnimationFrame:setImmediate,MutationObserver:class{constructor(fn){this.fn=fn;observers.push(this)}observe(){}disconnect(){}},Plotly:{relayout:async(plot,u)=>{calls++;if(calls>40)throw Error('relayout loop');for(const [key,value] of Object.entries(u)){const [name,prop]=key.split('.');plot._fullLayout[name][prop]=value;}plot.emit('plotly_afterplot');plot.emit('plotly_relayout',u);}}};
 const action=async u=>{await win.Plotly.relayout(p,u);await flush();};
 const button=async label=>{p.emit('plotly_buttonclicked',{button:{label}});await flush();};
 install(win);
 return {p,win,action,button,observers,calls:()=>calls};
}
test('horizontal window auto fits; manual price range persists through pan and rerun',async()=>{
 const f=fixture();await flush();await f.action({'xaxis.range':[1.5,2.5]});assert.deepEqual(f.p._fullLayout.yaxis.range,[19.895,21.105]);
 await f.action({'yaxis.range':[1,100]});assert.equal(f.p.dataset.priceScaleMode,'manual');
 await f.action({'xaxis.range':[-.5,.5]});assert.deepEqual(f.p._fullLayout.yaxis.range,[1,100]);
 install(f.win);await flush();assert.deepEqual(f.p._fullLayout.yaxis.range,[1,100]);assert.equal(f.p.listenerCount('plotly_relayout'),1);
 await f.button('Auto scale');assert.deepEqual(f.p._fullLayout.yaxis.range,[8.92,10.08]);
});
test('reset restores starting time window and candle scale',async()=>{
 const f=fixture();await flush();await f.action({'xaxis.range':[1.5,2.5]});await f.action({'yaxis.range':[1,100]});await f.button('Reset view');
 assert.deepEqual(f.p._fullLayout.xaxis.range,[-.5,3.5]);assert.deepEqual(f.p._fullLayout.yaxis.range,[7.24,32.76]);assert.equal(f.p.dataset.priceScaleMode,'auto');
});
test('All levels stays selected until auto scale',async()=>{
 const f=fixture();await flush();await f.button('All levels');assert.equal(f.p.dataset.priceScaleMode,'all');
 await f.action({'xaxis.range':[1.5,2.5]});assert.equal(f.p._fullLayout.yaxis.autorange,true);
 await f.button('Auto scale');assert.equal(f.p._fullLayout.yaxis.autorange,false);
});
test('live candle updates refit auto but preserve manual y',async()=>{
 const f=fixture();await flush();f.p._fullData[0].high[3]=40;f.p.emit('plotly_afterplot');await flush();assert.equal(f.p._fullLayout.yaxis.range[1],42.48);
 await f.action({'yaxis.range':[5,50]});f.p._fullData[0].high[3]=45;f.p.emit('plotly_afterplot');await flush();assert.deepEqual(f.p._fullLayout.yaxis.range,[5,50]);
});
test('new ticker or timeframe resets manual state',async()=>{
 const f=fixture();await flush();await f.action({'yaxis.range':[1,100]});f.p._fullLayout.uirevision='OTHER:5m';f.p._fullLayout.xaxis.range=[1.5,2.5];f.p.emit('plotly_afterplot');await flush();assert.equal(f.p.dataset.priceScaleMode,'auto');assert.deepEqual(f.p._fullLayout.yaxis.range,[19.895,21.105]);
});
test('unmount cleans listeners',async()=>{const f=fixture();await flush();f.p.isConnected=false;f.observers[0].fn();assert.equal(f.p.listenerCount('plotly_relayout'),0);});

test('native zoom controls changing both axes keep auto mode',async()=>{const f=fixture();await flush();await f.action({'xaxis.range':[1.5,2.5],'yaxis.range':[15,25]});assert.equal(f.p.dataset.priceScaleMode,'auto');assert.deepEqual(f.p._fullLayout.yaxis.range,[19.895,21.105]);});

test('Plotly purge removes event methods before unmount',async()=>{const f=fixture();await flush();f.p.isConnected=false;f.p.removeListener=undefined;f.p.on=undefined;assert.doesNotThrow(()=>f.observers[0].fn());});

test('server render cannot overwrite the chosen manual axes',async()=>{const f=fixture();await flush();await f.action({'xaxis.range':[1.5,2.5]});await f.action({'yaxis.range':[5,50]});f.p._fullLayout.xaxis.range=[-.5,4.5];f.p._fullLayout.yaxis.range=[8,33];f.p.emit('plotly_afterplot');await flush();assert.deepEqual(f.p._fullLayout.xaxis.range,[1.5,2.5]);assert.deepEqual(f.p._fullLayout.yaxis.range,[5,50]);});

test('a reused graph with a new Plotly emitter rebinds once',async()=>{const f=fixture();await flush();await f.action({'yaxis.range':[5,50]});f.p.removeAllListeners();f.p.on=f.p.on.bind(f.p);f.observers[0].fn();await flush();assert.equal(f.p.listenerCount('plotly_relayout'),1);assert.equal(f.p.dataset.priceScaleMode,'manual');assert.deepEqual(f.p._fullLayout.yaxis.range,[5,50]);});

test('separate charts with the same dataset do not share manual ranges',async()=>{const f=fixture('first');await flush();await f.action({'yaxis.range':[5,50]});const g=fixture('second');await flush();g.win.__analyzerViewportSaved=f.win.__analyzerViewportSaved;install(g.win);await flush();assert.equal(g.p.dataset.priceScaleMode,'auto');assert.deepEqual(g.p._fullLayout.yaxis.range,[7.24,32.76]);assert.deepEqual(f.p._fullLayout.yaxis.range,[5,50]);});

test('untouched viewport follows newest default; reset resumes following',async()=>{const f=fixture();await flush();f.p._fullLayout.meta.defaultX=[-.5,4.5];f.p.emit('plotly_afterplot');await flush();assert.deepEqual(f.p._fullLayout.xaxis.range,[-.5,4.5]);await f.action({'xaxis.range':[1,2]});f.p._fullLayout.meta.defaultX=[-.5,5.5];f.p.emit('plotly_afterplot');await flush();assert.deepEqual(f.p._fullLayout.xaxis.range,[1,2]);await f.button('Reset view');assert.deepEqual(f.p._fullLayout.xaxis.range,[-.5,5.5]);});
