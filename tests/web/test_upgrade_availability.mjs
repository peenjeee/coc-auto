import assert from 'node:assert/strict';
import test from 'node:test';
import { MAX_LEVELS } from '../../web/upgrade-max-levels.js';
import { isFullyUpgraded } from '../../web/upgrade-availability.js';

const cannon = { source: 'game_export', data_id: 1000008, section: 'other', current_level: MAX_LEVELS['1000008'] };

test('completed maxima disappear, but lower-level copies of the same building remain', () => {
  const lower = { ...cannon, current_level: cannon.current_level - 1 };
  assert.deepEqual([cannon, lower].filter(row => !isFullyUpgraded(row)), [lower]);
});

test('running final upgrades and unknown or newer game data remain visible', () => {
  for (const row of [
    { ...cannon, section: 'in_progress', seconds: 120 },
    { ...cannon, data_id: 199999999 },
    { ...cannon, current_level: null },
    { ...cannon, current_level: cannon.current_level + 1 },
    { ...cannon, source: 'ocr' },
  ]) assert.equal(isFullyUpgraded(row), false);
});

test('wall, hero, research and equipment maxima use game identity rather than translated names', () => {
  for (const data_id of [1000010, 28000000, 4000000, 90000013]) {
    assert.ok(Number.isInteger(MAX_LEVELS[String(data_id)]), String(data_id));
    assert.equal(isFullyUpgraded({ ...cannon, data_id, name: 'translated name', current_level: MAX_LEVELS[String(data_id)] }), true);
  }
});
