// Deterministic reduction of the captured missed automation click.
// Execute the production scroll keeper, not a duplicate implementation.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const py=fs.readFileSync(path.join(__dirname,'../analyzer_bootstrap.py'),'utf8');
const source=py.split('def _install_scroll_keeper():')[1].split('</script>')[0].split('<script>')[1];
function fixture(){
 const handlers=new Map(),timers=new Map();let next=0,stale=false,observer;
 const root={style:{setProperty(){},removeProperty(){}},getBoundingClientRect:()=>({height:1000}),contains:()=>false};
 const scroller={scrollTop:700};
 const document={body:{},querySelector:s=>s==='.st-key-analyzer_live_fragment'?root:scroller,
  querySelectorAll:()=>stale?[root]:[],addEventListener:(name,fn)=>handlers.set(name,fn),
  removeEventListener:name=>handlers.delete(name)};
 const window={document,clearTimeout:id=>timers.delete(id),setTimeout:(fn,ms)=>{timers.set(++next,{fn,ms});return next;},
  MutationObserver:class{constructor(fn){observer=fn;}observe(){}disconnect(){}},removeEventListener(){}};
 vm.runInNewContext(source,{window});
 return {scroller,handlers,window,begin(){stale=true;observer();},finish(){stale=false;observer();for(const [id,t] of [...timers])if(t.ms===120){timers.delete(id);t.fn();}}};
}
test('reproduces implicit locator scrolling being undone before its click',()=>{
 const f=fixture();f.begin();f.scroller.scrollTop=0;f.finish();
 assert.equal(f.scroller.scrollTop,700);
 // The header is no longer under the coordinate chosen at scrollTop=0.
 assert.equal(f.scroller.scrollTop===0,false);
});
test('explicit wheel navigation keeps the header under the first click',()=>{
 const f=fixture();f.begin();f.handlers.get('wheel')();f.scroller.scrollTop=0;f.finish();
 assert.equal(f.scroller.scrollTop,0);
 assert.equal(f.window.__ssaRerunPending,false);
});
test('disposing the old Analyzer controller cannot undo return navigation',()=>{
 const f=fixture();f.begin();f.scroller.scrollTop=0;f.window.__ssaScrollKeeper.dispose();
 assert.equal(f.scroller.scrollTop,0);
 assert.equal(f.window.__ssaRerunPending,false);
 assert.equal(f.handlers.size,0);
});
