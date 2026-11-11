import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';
import {VisiblePoller} from '../../web/visible-poller.js';

const source = await fs.readFile(new URL('../../web/app.js', import.meta.url), 'utf8');
const pollerCode = source.slice(source.indexOf('const upgradePoller = '), source.indexOf('\nfunction shouldCaptureScreen'));

test('upgrade polling reads all six cached lists only while viewed, without overlapping batches', async () => {
  const calls = [], timers = new Map();
  const lists = ['buildings', 'lab', 'pets', 'gear', 'builder', 'star'];
  let timerId = 0;
  const context = vm.createContext({
    VisiblePoller, online: true, link: {}, activeView: 'overview', screenPageActive: true,
    document: {hidden: false}, BUILDING_LISTS: lists,
    send: async (action, args) => calls.push({action, list: args.list}),
  });
  vm.runInContext(pollerCode + '\nglobalThis.poller = upgradePoller;', context);
  const poller = context.poller;
  // Inject a deterministic clock into the real poller, avoiding browser timers.
  poller.schedule = (callback, interval) => {const id = ++timerId; timers.set(id, {callback, interval}); return id;};
  poller.cancel = id => timers.delete(id);
  poller.sync();
  assert.equal(calls.length, 0);
  context.activeView = 'buildings';
  poller.sync(); poller.sync();
  assert.deepEqual(calls.map(call => call.list), lists);
  assert.ok(calls.every(call => call.action === 'get_buildings'));
  await new Promise(setImmediate);
  assert.equal(timers.size, 1);
  assert.equal([...timers.values()][0].interval, 15000);
  context.document.hidden = true; poller.sync();
  assert.equal(timers.size, 0);
  context.document.hidden = false; context.online = false; poller.sync();
  assert.equal(calls.length, 6);
  context.online = true; context.screenPageActive = false; poller.sync();
  assert.equal(calls.length, 6);
  context.screenPageActive = true; poller.sync();
  assert.equal(calls.length, 12);
  await new Promise(setImmediate);
  context.activeView = 'overview'; poller.sync();
  assert.equal(timers.size, 0);
});
