const {test} = require('node:test');
const assert = require('node:assert/strict');
const {cursorPoint, install} = require('../analyzer_price_cursor.js');

function plotFixture() {
  return {
    _fullLayout: {width: 800, height: 300,
      xaxis: {_offset: 20, _length: 740},
      yaxis: {_offset: 10, _length: 200, p2d: px => 15 - px / 20}},
    getBoundingClientRect: () => ({left: 100, top: 50, width: 800, height: 300}),
    appendChild(label) { label.parentNode = this; },
  };
}
test('price follows cursor height, including gaps between candles', () => {
  const plot = plotFixture();
  assert.equal(cursorPoint(plot, {clientX: 250, clientY: 100}).price, 13);
  assert.equal(cursorPoint(plot, {clientX: 600, clientY: 100}).price, 13);
  assert.equal(cursorPoint(plot, {clientX: 250, clientY: 160}).price, 10);
});
test('uses the new transform after pan/zoom and CSS scaling', () => {
  const plot = plotFixture();
  plot._fullLayout.yaxis.p2d = px => 100 - px / 2;
  plot.getBoundingClientRect = () => ({left: 100, top: 50, width: 400, height: 150});
  assert.equal(cursorPoint(plot, {clientX: 250, clientY: 100}).price, 55);
});
test('no price over volume, axes, unavailable plots or invalid conversion', () => {
  const plot = plotFixture();
  for (const [clientX, clientY] of [[150, 280], [105, 150], [899, 100], [200, 52]]) {
    assert.equal(cursorPoint(plot, {clientX, clientY}), null);
  }
  plot._fullLayout.yaxis.p2d = () => NaN;
  assert.equal(cursorPoint(plot, {clientX: 200, clientY: 150}), null);
  delete plot._fullLayout;
  assert.equal(cursorPoint(plot, {clientX: 200, clientY: 150}), null);
});
test('one small price label; hides on drag, scroll, touch, exit; reruns replace listeners', async () => {
  const win = new EventTarget(), doc = new EventTarget(), labels = [];
  win.document = doc; win.AbortController = AbortController; win.requestAnimationFrame = queueMicrotask;
  doc.createElement = () => {
    const label = {style: {}, offsetWidth: 55, offsetHeight: 22, setAttribute() {},
      remove() { this.parentNode = null; }};
    labels.push(label); return label;
  };
  const plot = plotFixture();
  const target = {closest: selector => selector.includes('js-plotly-plot') ? plot : null};
  function fire(type, extra = {}) {
    const event = new Event(type);
    Object.defineProperty(event, 'target', {value: target});
    Object.assign(event, {clientX: 850, clientY: 70, buttons: 0, pointerType: 'mouse'}, extra);
    doc.dispatchEvent(event);
  }
  const visible = () => labels.filter(label => label.parentNode);
  doc.elementFromPoint = () => target;
  install(win);
  fire('pointermove');
  assert.equal(visible()[0].textContent, '$14.50');
  assert.equal(visible()[0].style.left, '683px'); // Flipped inside right edge.
  assert.equal(visible()[0].style.top, '10px');
  fire('pointermove', {clientY: 160});
  assert.equal(visible().length, 1);
  assert.equal(visible()[0].textContent, '$10.00');
  for (const [type, extra] of [['pointermove', {buttons: 1}], ['scroll', {}],
    ['pointermove', {pointerType: 'touch'}], ['pointerout', {}]]) {
    fire(type, extra); assert.equal(visible().length, 0);
    fire('pointermove'); assert.equal(visible().length, 1);
  }
  fire('pointerdown'); assert.equal(visible().length, 0);
  fire('mouseup'); await Promise.resolve(); assert.equal(visible().length, 1);
  install(win); assert.equal(visible().length, 0);
  fire('pointermove'); assert.equal(visible().length, 1);
  win.__analyzerPriceCursor(); assert.equal(visible().length, 0);
  fire('pointermove'); assert.equal(visible().length, 0);
});
