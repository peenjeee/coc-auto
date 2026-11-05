import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { VisiblePoller } from './web/visible-poller.js';

function fixture(request = async () => {}) {
  const tasks = new Map();
  let next = 0, visible = false, calls = 0;
  const poller = new VisiblePoller({
    visible: () => visible,
    request: () => { calls++; return request(); },
    schedule: (fn, delay) => { assert.equal(delay, 1000); tasks.set(++next, fn); return next; },
    cancel: id => tasks.delete(id),
  });
  return {poller, tasks, get calls() {return calls;}, show(value) {visible=value; poller.sync();},
    async next() {const [id, fn] = tasks.entries().next().value; tasks.delete(id); await fn();}};
}

test('captures only while viewed, resumes immediately, and cancels hidden timers', async () => {
  const f = fixture();
  f.poller.sync();
  assert.equal(f.calls, 0);
  assert.equal(f.tasks.size, 0);
  f.show(true);
  await Promise.resolve();
  assert.equal(f.calls, 1);
  assert.equal(f.tasks.size, 1);
  await f.next();
  assert.equal(f.calls, 2);
  f.show(false);
  assert.equal(f.tasks.size, 0);
  f.show(true);
  await Promise.resolve();
  assert.equal(f.calls, 3);
  assert.equal(f.tasks.size, 1);
  f.show(false);
});

test('slow captures never overlap and finishing while hidden schedules nothing', async () => {
  let finish;
  const f = fixture(() => new Promise(resolve => {finish=resolve;}));
  f.show(true);
  f.poller.sync();
  f.poller.sync();
  assert.equal(f.calls, 1);
  assert.equal(f.tasks.size, 0);
  f.show(false);
  finish();
  await Promise.resolve();
  assert.equal(f.tasks.size, 0);
  f.show(true);
  assert.equal(f.calls, 2);
  f.show(false);
  finish();
  await Promise.resolve();
  assert.equal(f.tasks.size, 0);
});

test('default browser timers are called without the poller as their receiver', async () => {
  let callback, cleared, visible = true;
  const source = fs.readFileSync(new URL('./web/visible-poller.js', import.meta.url), 'utf8');
  const BrowserPoller = vm.runInNewContext(source.replace('export class', 'class') + '\nVisiblePoller;', {
    setTimeout: function (fn, delay) {
      assert.equal(this, undefined, 'a Window timer must not be invoked as a poller method');
      assert.equal(delay, 1000);
      callback = fn;
      return 7;
    },
    clearTimeout: function (id) { assert.equal(this, undefined); cleared = id; },
  });
  const poller = new BrowserPoller({visible: () => visible, request: async () => {}});
  await poller.tick();
  assert.equal(typeof callback, 'function');
  visible = false;
  poller.sync();
  assert.equal(cleared, 7);
});
