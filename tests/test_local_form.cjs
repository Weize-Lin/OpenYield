// Run the real form handlers without browser or npm dependencies.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function main() {
  const source = process.argv[2] || path.join(__dirname, '../yield_estimation/evaluation/web_static/app.js');
  const templatePath = process.argv[3] || path.join(__dirname, '../docs/research/hold_workflow_cases/comparison.json');
  const template = JSON.parse(fs.readFileSync(templatePath, 'utf8'));
  const elements = new Map();
  class Element {
    constructor() { this.value = ''; this.checked = false; this.events = {}; }
    set id(value) { this.identifier = value; elements.set(value, this); }
    addEventListener(name, callback) { this.events[name] = callback; }
    append() {}
  }
  const element = id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const sandbox = {
    document: {getElementById: element, createElement: () => new Element()},
    structuredClone,
    setTimeout,
    console,
    fetch: async () => ({ok: true, json: async () => ({token: 'fixture', template: structuredClone(template)})}),
  };
  vm.runInNewContext(fs.readFileSync(source, 'utf8'), sandbox, {filename: source});
  await new Promise(setImmediate);
  assert.ok(element('json').value.includes('schema_version'));

  const config = structuredClone(template);
  const base = config.estimators.find(item => item.algorithm === 'mnis');
  const alternative = structuredClone(base);
  alternative.id = 'mnis-standardized';
  alternative.center_selection.rule = 'nearest_standardized';
  alternative.proposal.std_scale = 0.75;
  config.estimators = [{...base, id: 'mnis-discovery'}, alternative];
  const expected = structuredClone(config.estimators);
  await element('import').onchange({target: {files: [{text: async () => JSON.stringify(config)}]}});
  element('budget').value = '24';
  element('draw-cap').value = '24';
  element('settings').events.input();
  element('settings').events.submit({preventDefault() {}});
  const updated = JSON.parse(element('json').value);
  assert.deepEqual(updated.estimators, expected);
  assert.equal(updated.budget_per_algorithm, 24);
  assert.equal(updated.max_algorithm_draws, 24);

  element('alg-mc').checked = true;
  element('settings').events.submit({preventDefault() {}});
  const withMC = JSON.parse(element('json').value);
  assert.deepEqual(withMC.estimators.slice(0, 2), expected);
  assert.equal(withMC.estimators[2].algorithm, 'mc');

  element('alg-mnis').checked = false;
  element('settings').events.submit({preventDefault() {}});
  const withoutMNIS = JSON.parse(element('json').value);
  assert.deepEqual(withoutMNIS.estimators.map(item => item.algorithm), ['mc']);
  console.log('Form regression passed: both MNIS IDs and complete strategies preserved on budget edit and MC addition; explicit MNIS deselection removes both.');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
