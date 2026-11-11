import test from 'node:test';
import assert from 'node:assert/strict';
import { BuildingsPanel } from '../../web/buildings.js';

test('polling preserves drafts and ignores older receipts or replies arriving after a newer sync', () => {
  const panel = Object.create(BuildingsPanel.prototype);
  const draft = [{id:'minion_prince',level:40}];
  panel.lists = {buildings:{rows:null,scannedAt:0,targets:draft}};
  panel.ticker = true;
  let renders = 0;
  panel.render = () => { renders++; };
  const data = {list:'buildings',scanned_at:1000,receipt_watermark:5,
    rows:[{id:'minion_prince',current_level:37,next_level:38,receipt_id:5}]};
  panel.load(data);
  panel.load(structuredClone(data));
  assert.equal(renders,1);
  panel.load({...data,receipt_watermark:0,rows:[{id:'minion_prince',current_level:36}]});
  assert.equal(renders,1);
  panel.load({...data,scanned_at:2000,receipt_watermark:0,rows:[{id:'minion_prince',current_level:38}]});
  panel.load(data);
  assert.equal(renders,2);
  assert.equal(panel.lists.buildings.rows[0].current_level,38);
  assert.equal(panel.lists.buildings.targets,draft);
});

test('a delayed Goblin save acknowledgement preserves inputs edited after sending', () => {
  const fields = {
    'goblin-builder': { checked: true },
    'goblin-researcher': { checked: false },
    'goblin-max': { value: '25' },
  };
  const previous = globalThis.document;
  globalThis.document = { getElementById: id => fields[id] };
  try {
    const panel = { goblinDirty: true };
    BuildingsPanel.prototype.acknowledgeGoblin.call(panel, { goblin: { builder: true, researcher: false, max_gems: 0 } });
    assert.equal(panel.goblinDirty, true);
    assert.equal(fields['goblin-max'].value, '25');
    BuildingsPanel.prototype.acknowledgeGoblin.call(panel, { goblin: { builder: true, researcher: false, max_gems: 25 } });
    assert.equal(panel.goblinDirty, false);
  } finally {
    globalThis.document = previous;
  }
});

test('an older queue save reply cannot mark a newer order as saved', () => {
  const older = [{ id: 'archer_tower', name: 'Archer Tower', level: 8 }, { id: 'spring_trap', name: 'Spring Trap', level: null }];
  const panel = Object.create(BuildingsPanel.prototype);
  panel.list = 'buildings';
  panel.keep = 1;
  panel.lists = { buildings: { targets: [...older].reverse() } };
  panel.baselines = { buildings: 'previous saved queue' };
  panel.renderStatus = () => {};
  panel.acknowledge({ buildings: { targets: older, keep_builders: 1 } });
  assert.equal(panel.baselines.buildings, 'previous saved queue');
  panel.acknowledge({ buildings: { targets: [...older].reverse(), keep_builders: 1 } });
  assert.equal(panel.baselines.buildings, panel.signature());
});


test('one village export refreshes every list without replacing unsaved queue drafts', () => {
  const names = ['buildings', 'lab', 'pets', 'gear', 'builder', 'star'];
  const panel = Object.create(BuildingsPanel.prototype);
  panel.list = 'lab';
  panel.ticker = true;
  panel.render = () => {};
  panel.lists = Object.fromEntries(names.map(list => [list, { rows: null, targets: [] }]));
  const draft = [{ id: 'archer', name: 'Archer', level: 10 }];
  panel.lists.lab.targets = draft;
  panel.load({ source: 'game_export', lists: names.map(list => ({
    list, scanned_at: 1000, rows: [{ id: list, current_level: 5, source: 'game_export' }],
  })) });
  for (const list of names) {
    assert.equal(panel.lists[list].rows[0].current_level, 5);
    assert.equal(panel.lists[list].scannedAt, 1000000);
  }
  assert.equal(panel.list, 'lab');
  assert.equal(panel.targets, draft);
});
