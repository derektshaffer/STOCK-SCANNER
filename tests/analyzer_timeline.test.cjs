const {test} = require('node:test');
const assert = require('node:assert/strict');
const {compactLabels} = require('../analyzer_timeline.js');
const {install} = require('../analyzer_timeline.js');
test('date only on the first visible tick and each new day', () => {
  assert.deepEqual(compactLabels(['2026-09-10 06:30','2026-09-10 08:15','2026-09-11 06:30','2026-09-11 10:00']),
    [['06:30','Sep 10, 2026'],['08:15'],['06:30','Sep 11, 2026'],['10:00']]);
});
test('pan or zoom starts a new visible date group, even halfway through a day', () => {
  assert.deepEqual(compactLabels(['2026-09-11 08:15','2026-09-11 10:00']), [['08:15','Sep 11, 2026'],['10:00']]);
});
test('daily labels and empty charts remain intact; year changes are explicit', () => {
  assert.deepEqual(compactLabels(['2026-09-11']), [['Sep 11','2026']]);
  assert.deepEqual(compactLabels([]), []);
  assert.deepEqual(compactLabels(['2026-12-31 12:55','2027-01-04 06:30']), [['12:55','Dec 31, 2026'],['06:30','Jan 04, 2027']]);
});
test('Streamlit chart replacement rebinds; reruns and label edits do not duplicate listeners', () => {
  const makePlot = () => ({events:new Set(), querySelectorAll:()=>[],
    on(name, fn) { this.events.add(fn); }, removeListener(name, fn) { this.events.delete(fn); }});
  let current = makePlot(), watch;
  const doc = {body:{}, querySelector:()=>current};
  const win = {document:doc, MutationObserver:class {
    constructor(fn) { watch=fn; } observe(){} disconnect(){}
  }};
  install(win);
  assert.equal(current.events.size, 1);
  watch(); watch(); assert.equal(current.events.size, 1);
  const old = current; current = makePlot(); watch();
  assert.equal(old.events.size, 0); assert.equal(current.events.size, 1);
  install(win); assert.equal(current.events.size, 1);
  win.__analyzerTimeline(); assert.equal(current.events.size, 0);
});
