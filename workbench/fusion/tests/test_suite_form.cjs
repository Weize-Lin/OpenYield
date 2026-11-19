// Exercise the actual production submission handler; no browser or solver needed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../public/app.js'), 'utf8');
const start = source.indexOf('  async function runSuite() {');
const end = source.indexOf('  function renderCatalog()', start);
assert.ok(start >= 0 && end > start);
const handler = source.slice(start, end);
async function test(value, expectedCalls) {
  const input = { value, disabled: false };
  const text = { value: 'frozen-suite' };
  const feedback = {};
  const state = { suiteRunPending: false, suiteValidated: { text: text.value, experiments: [{ config: { fixture: true } }] } };
  const calls = [], errors = [];
  const context = {
    state, Number,
    $: id => id === 'suite-time-limit' ? input : id === 'suite-json' ? text : id === 'suite-message' ? feedback : {},
    syncButtons() {}, busy: () => false,
    api: async (route, body) => { calls.push({route, body}); return { id: 'not-a-real-job' }; },
    acceptJob: async () => {}, navigate() {}, showError: e => errors.push(e.message), errorText: e => e.message,
  };
  vm.createContext(context);
  await vm.runInContext(handler + '\nrunSuite()', context);
  assert.equal(calls.length, expectedCalls);
  if (expectedCalls) assert.equal(calls[0].body.time_limit_seconds, Number(value));
  else assert.equal(errors.length, 1);
}
(async () => {
  await test('120', 1);
  await test('1800', 1);
  for (const invalid of ['0', '-1', '120.5', '3601', 'oops', '']) await test(invalid, 0);
  console.log('Suite form passed: 120-second and default limits submitted unchanged; invalid limits never submitted.');
})().catch(error => { console.error(error); process.exitCode = 1; });
