const {test}=require('node:test'),assert=require('node:assert/strict');
const {bounded,zoomRange,priceRange}=require('../analyzer_terminal.js');
test('zoom is centered at the pointer',()=>{const r=zoomRange([20,80],.25,-100,100);assert.ok(r[1]-r[0]<60);assert.ok(Math.abs(r[0]+(r[1]-r[0])*.25-35)<1e-9);});
test('zoom is bounded to real candle slots',()=>{assert.deepEqual(zoomRange([-.5,99.5],.5,100000,100),[-.5,99.5]);assert.equal(zoomRange([20,26],.5,-100000,100)[1]-zoomRange([20,26],.5,-100000,100)[0],6);});
test('pan stops at data boundaries',()=>{assert.deepEqual(bounded([-100,-80],100),[-.5,19.5]);assert.deepEqual(bounded([120,140],100),[79.5,99.5]);});
test('right axis drag changes candle height around center',()=>{const a=priceRange([10,20],50),b=priceRange([10,20],-50);assert.equal((a[0]+a[1])/2,15);assert.ok(a[1]-a[0]>10);assert.ok(b[1]-b[0]<10);});
test('one candle and sub-dollar prices remain usable',()=>{assert.deepEqual(zoomRange([-.5,.5],.5,-100,1),[-.5,.5]);const r=priceRange([.0001,.0002],-10000);assert.ok(r.every(Number.isFinite));assert.ok(r[1]>r[0]);});
