import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
import { webcrypto } from 'node:crypto';

const source = await fs.readFile(new URL('../../web/app.js', import.meta.url), 'utf8');
const opened = source.slice(source.indexOf('async function opened('), source.indexOf('\nfunction lineClass('));
const link = source.slice(source.indexOf('class RelayLink {'), source.indexOf('\n/** @returns {Record<string, unknown>}'));
const encoder = new TextEncoder();
const key = await webcrypto.subtle.importKey('raw', encoder.encode('local-test-key'), {name:'HMAC',hash:'SHA-256'}, false, ['sign','verify']);

async function fixture() {
  const lines = [];
  const context = vm.createContext({
    crypto: webcrypto, encoder, hmacKey: key, AGENT_PREFIX: 'agent:', MAX_SKEW_SECONDS: 300,
    unhex: text => Uint8Array.from(text.match(/../g), pair => Number.parseInt(pair, 16)),
    setOnline: () => {}, showState: () => {}, clearLines: () => { lines.length = 0; },
    addLines: values => lines.push(...values),
  });
  vm.runInContext(opened + '\n' + link + '\nglobalThis.receiver=Object.create(RelayLink.prototype);', context);
  const seal = async data => {
    const body = JSON.stringify({ts:Date.now()/1000,...data});
    const signature = await webcrypto.subtle.sign('HMAC', key, encoder.encode('agent:' + body));
    return {body, sig:Buffer.from(signature).toString('hex')};
  };
  return {receiver:context.receiver, lines, seal};
}

test('signed online logs and history prefer originals and accept older payloads', async () => {
  const {receiver,lines,seal} = await fixture();
  const original = 'WARNING: Upgrade Ratu Pemanah dilewati: sibuk';
  const rendered = 'WARNING: Upgrade of Archer Queen skipped: busy';
  await receiver.received('log', await seal({lines:[rendered],source_lines:[original]}));
  assert.deepEqual(lines,[original]);
  await receiver.received('history', await seal({lines:[rendered],source_lines:[original],state:{}}));
  assert.deepEqual(lines,[original]);
  await receiver.received('log', await seal({lines:['older agent log']}));
  assert.deepEqual(lines,[original,'older agent log']);
});

test('changing source_lines after signing rejects the entire log payload', async () => {
  const {receiver,lines,seal} = await fixture();
  const payload = await seal({lines:['rendered'],source_lines:['original']});
  payload.body = JSON.stringify({...JSON.parse(payload.body),source_lines:['tampered']});
  await receiver.received('log',payload);
  assert.deepEqual(lines,[]);
});
