import test from 'node:test';
import assert from 'node:assert/strict';
import { BuildingsPanel } from './web/buildings.js';

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
