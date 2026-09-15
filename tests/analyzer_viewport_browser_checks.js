/* Test-only in-page harness. Uses the application's actual Plotly instance. */
(function(){
  const button=document.getElementById('viewport-test-run');
  const output=document.getElementById('viewport-test-results');
  const settle=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const close=(a,b)=>a?.length===b?.length&&a.every((v,i)=>Math.abs(v-b[i])< 1e-6);
  const plots=()=>[...document.querySelectorAll('.js-plotly-plot')].filter(p=>p._fullLayout?.meta?.analyzerViewport);
  const report=()=>{
    for(const [i,p] of plots().entries()) {
      p.dataset.viewportTestState=JSON.stringify({index:i,mode:p.dataset.priceScaleMode,x:p._fullLayout.xaxis.range,y:p._fullLayout.yaxis.range,candles:p._fullData[0].x.length,revision:p._fullLayout.uirevision});
    }
  };
  window.__viewportTestCleanup?.();
  const observer=new MutationObserver(report);observer.observe(document.body,{childList:true,subtree:true});
  window.__viewportTestCleanup=()=>observer.disconnect();report();
  button.onclick=async()=>{
    const results=[];
    const check=(name,ok)=>{results.push({name,pass:!!ok});output.textContent=JSON.stringify(results,null,2);if(!ok)throw Error(name);};
    try {
      check('both real charts mounted',plots().length===2);
      for(const [i,p] of plots().entries()){
        await settle();
        const initialX=[...p._fullLayout.xaxis.range], initialY=[...p._fullLayout.yaxis.range];
        const count=p._fullData[0].x.length;
        const x=p._fullLayout.xaxis.type==='category'?[40,50]:[p._fullData[0].x[40],p._fullData[0].x[50]];
        await Plotly.relayout(p,{'xaxis.range':x,'xaxis.autorange':false});await settle();
        check(i+': visible-window scale follows horizontal zoom',p._fullLayout.yaxis.range[0]>13&&p._fullLayout.yaxis.range[1]< 16);
        await Plotly.relayout(p,{'yaxis.range':[2,50],'yaxis.autorange':false});await settle();
        check(i+': price-axis adjustment switches to manual',p.dataset.priceScaleMode==='manual'&&close(p._fullLayout.yaxis.range,[2,50]));
        await Plotly.relayout(p,{'xaxis.range':initialX});await settle();
        check(i+': horizontal pan preserves manual price range',close(p._fullLayout.yaxis.range,[2,50]));
        p.emit('plotly_buttonclicked',{button:{label:'Auto scale'}});await settle();
        check(i+': auto scale restores visible candles',p.dataset.priceScaleMode==='auto'&&close(p._fullLayout.yaxis.range,initialY));
        p.emit('plotly_buttonclicked',{button:{label:'All levels'}});await settle();
        check(i+': all levels mode persists',p.dataset.priceScaleMode==='all');
        if(i===1)check(i+': all levels includes distant target',p._fullLayout.yaxis.range[1]>=40);
        await Plotly.relayout(p,{'xaxis.range':x});await settle();
        p.emit('plotly_buttonclicked',{button:{label:'Reset view'}});await settle();
        check(i+': reset restores original time and candle ranges',close(p._fullLayout.yaxis.range,initialY)&&JSON.stringify(p._fullLayout.xaxis.range)===JSON.stringify(initialX));
        const oldHigh=[...p._fullData[0].high];const highs=[...oldHigh];highs[count-1]=20;
        await Plotly.restyle(p,{high:[highs]},[0]);await settle();
        check(i+': live candle update refits in auto',p._fullLayout.yaxis.range[1]>20);
        await Plotly.relayout(p,{'yaxis.range':[2,50]});await settle();
        await Plotly.restyle(p,{high:[oldHigh]},[0]);await settle();
        check(i+': live candle update preserves manual range',close(p._fullLayout.yaxis.range,[2,50]));
        p.emit('plotly_buttonclicked',{button:{label:'Reset view'}});await settle();
        report();
      }
      output.dataset.result='passed';
    }catch(error){output.dataset.result='failed';output.textContent+='\n'+error.message;}
    report();
  };
})();
