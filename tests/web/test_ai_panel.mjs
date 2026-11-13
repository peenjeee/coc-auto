// Run with node --test test_ai_panel.mjs. A stub DOM stands in for the browser.
import assert from 'node:assert/strict';
import test from 'node:test';

const elements = new Map();
function element(id) {
  if (!elements.has(id)) {
    elements.set(id, {
      id, value: '', checked: false, disabled: false, textContent: '', placeholder: '', type: 'text', innerHTML: '',
      dataset: {}, listeners: {},
      classList: { toggle(name, on) { this.last = [name, on]; } },
      addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); },
      querySelectorAll() { return []; },
    });
  }
  return elements.get(id);
}
globalThis.document = { documentElement: { lang: 'en' }, getElementById: element };
globalThis.localStorage = { getItem: () => null, setItem() {} };
const { AiPanel } = await import('../../web/ai.js');

const SAVED = { enabled: true, base_url: 'https://openrouter.ai/api/v1', model: 'some/model', goal: 'stars', hint: 'no healers',
  probe_points: 4, probe_max: 10, reasoning_effort: 'low', key_set: true };

function panel(local = true) {
  elements.clear();
  const sent = [];
  const made = new AiPanel((action, args) => sent.push({ action, args }), { local });
  return { made, sent };
}

test('loading fills the form, never a key, and enables the test button', () => {
  const { made } = panel();
  made.load(SAVED, 'ai_loaded');
  assert.equal(element('ai-url').value, SAVED.base_url);
  assert.equal(element('ai-model').value, 'some/model');
  assert.equal(element('ai-goal').value, 'stars');
  assert.equal(element('ai-hint').value, 'no healers');
  assert.equal(element('ai-probe-points').value, '4');
  assert.equal(element('ai-probe-max').value, '10');
  assert.equal(element('ai-effort').value, 'low');
  assert.equal(element('ai-enabled').checked, true);
  assert.equal(element('ai-key').value, '');
  assert.match(element('ai-key').placeholder, /saved/i);
  assert.equal(element('ai-test').disabled, false);
  assert.equal(element('ai-status').textContent, 'Enabled');
});

test('saving sends the typed key only, so a saved key is not overwritten by blank', () => {
  const { made, sent } = panel();
  made.load(SAVED, 'ai_loaded');
  made.save();
  assert.equal(sent.at(-1).action, 'set_ai');
  assert.equal('api_key' in sent.at(-1).args, false);
  element('ai-key').value = '  sk-new-key  ';
  made.save();
  assert.equal(sent.at(-1).args.api_key, 'sk-new-key');
  assert.deepEqual(
    { goal: sent.at(-1).args.goal, probe_points: sent.at(-1).args.probe_points, probe_max: sent.at(-1).args.probe_max,
      reasoning_effort: sent.at(-1).args.reasoning_effort, enabled: sent.at(-1).args.enabled, base_url: sent.at(-1).args.base_url },
    { goal: 'stars', probe_points: 4, probe_max: 10, reasoning_effort: 'low', enabled: true, base_url: SAVED.base_url });
});

test('a load clears the typed key field and an answer after editing does not wipe the edit', () => {
  const { made } = panel();
  made.load(SAVED, 'ai_loaded');
  element('ai-key').value = 'typed';
  made.load({ ...SAVED, key_set: true }, 'ai_saved');
  assert.equal(element('ai-key').value, '');
  made.dirty = true;
  element('ai-model').value = 'edited/model';
  made.load(SAVED, 'ai_loaded');                       // the periodic reload must not overwrite an unsaved edit
  assert.equal(element('ai-model').value, 'edited/model');
});

test('without a saved key or a model the test button stays off and the status says so', () => {
  const { made } = panel();
  made.load({ ...SAVED, key_set: false }, 'ai_loaded');
  assert.equal(element('ai-test').disabled, true);
  assert.equal(element('ai-status').textContent, 'Not set up');
  made.load({ ...SAVED, model: '' }, 'ai_loaded');
  assert.equal(element('ai-test').disabled, true);
});

test('a tested answer shows the seconds the model took', () => {
  const { made } = panel();
  made.load({ ...SAVED, tested_seconds: 1.4 }, 'ai_tested');
  assert.match(element('ai-feedback').textContent, /1\.4 s/);
  made.load(SAVED, 'ai_saved');
  assert.match(element('ai-feedback').textContent, /Saved/);
});

test('an unknown goal falls back to loot', () => {
  const { made } = panel();
  made.load({ ...SAVED, goal: 'chaos' }, 'ai_loaded');
  assert.equal(element('ai-goal').value, 'loot');
});

test('the online dashboard shows the form disabled with the local-only note', () => {
  const { made } = panel(false);
  made.load(SAVED, 'ai_loaded');
  assert.match(element('ai-feedback').textContent, /local dashboard/i);
  assert.equal(element('ai-test').disabled, true);
});

test('an unknown or missing reasoning effort falls back to the model default', () => {
  const { made } = panel();
  made.load({ ...SAVED, reasoning_effort: 'extreme' }, 'ai_loaded');
  assert.equal(element('ai-effort').value, '');
  const { made: second } = panel();
  second.load({ ...SAVED, reasoning_effort: undefined }, 'ai_loaded');
  assert.equal(element('ai-effort').value, '');
});
