// coc-auto dashboard: live log and control for one device, found by its API key. On localhost it talks to the
// bot on this PC directly; online it goes through the Supabase Realtime channel named after the key's hash, and
// only trusts messages signed with the key, so nobody else on the channel can pose as the device.
import { SUPABASE_URL, SUPABASE_KEY } from './config.js';
import { applyLanguage, t } from './i18n.js';
import { RealtimeChannel } from './realtime.js';
import { DeployEditor } from './deploy.js';

const ACK_MS = 10000;
const OFFLINE_AFTER_MS = 40000; // the device repeats its signed state every 15 s
const MAX_SKEW_SECONDS = 300;
const MAX_LINES = 2000;
const STORE_KEY = 'coc-remote-key';
const STORE_OPTIONS = 'coc-remote-options';
const AGENT_PREFIX = 'agent:';
const LOCAL = ['localhost', '127.0.0.1'].includes(location.hostname);
const KEY_PATTERN = /^coc_[A-Za-z0-9_-]{20,}$/;

const $ = (id) => document.getElementById(id);
const encoder = new TextEncoder();
const hex = (buffer) => [...new Uint8Array(buffer)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
const unhex = (text) => new Uint8Array(text.match(/../g).map((pair) => Number.parseInt(pair, 16)));

let hmacKey = null;
let link = null;
let online = false;
let lastState = null;
let editor = null;

/** localStorage that never throws (private windows, blocked storage). */
const stored = {
  get(name) {
    try { return localStorage.getItem(name); } catch { return null; }
  },
  set(name, value) {
    try {
      if (value === null) localStorage.removeItem(name);
      else localStorage.setItem(name, value);
    } catch { /* storage unavailable: settings just are not remembered */ }
  },
};

/** @param {string} key @returns {Promise<string>} the channel the device joins; the key itself never leaves */
async function channelOf(key) {
  return 'coc-' + hex(await crypto.subtle.digest('SHA-256', encoder.encode(key))).slice(0, 32);
}

async function useKey(key) {
  hmacKey = await crypto.subtle.importKey('raw', encoder.encode(key), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign', 'verify']);
}

/** @param {string} body @returns {Promise<string>} HMAC-SHA256 the device checks */
async function sign(body) {
  return hex(await crypto.subtle.sign('HMAC', hmacKey, encoder.encode(body)));
}

/** A device message, or null unless it carries the device's signature and a recent time. */
async function opened(payload) {
  if (!payload || typeof payload.body !== 'string' || typeof payload.sig !== 'string' || !/^[0-9a-f]{64}$/.test(payload.sig)) return null;
  const genuine = await crypto.subtle.verify('HMAC', hmacKey, unhex(payload.sig), encoder.encode(AGENT_PREFIX + payload.body));
  if (!genuine) return null;
  let data;
  try { data = JSON.parse(payload.body); } catch { return null; }
  if (!data || typeof data !== 'object' || typeof data.ts !== 'number') return null;
  return Math.abs(Date.now() / 1000 - data.ts) <= MAX_SKEW_SECONDS ? data : null;
}

function lineClass(line) {
  if (line.startsWith('ERROR')) return 'error';
  if (line.startsWith('WARNING')) return 'warning';
  if (line.startsWith('REMOTE')) return 'remote';
  return 'info';
}

/** @param {unknown[]} lines */
function addLines(lines) {
  const view = $('log');
  const fragment = document.createDocumentFragment();
  for (const line of lines) {
    if (typeof line !== 'string') continue;
    const row = document.createElement('span');
    row.textContent = line;
    row.className = lineClass(line);
    fragment.append(row);
  }
  view.append(fragment);
  while (view.childElementCount > MAX_LINES) view.firstElementChild.remove();
  if ($('follow').checked) view.scrollTop = view.scrollHeight;
}

function clearLines() {
  $('log').textContent = '';
}

function notify(message, kind = 'info') {
  const notice = $('notice');
  notice.textContent = message;
  notice.className = `notice ${kind}`;
  notice.hidden = !message;
}

function setOnline(isOnline, reason) {
  const changed = online !== isOnline;
  online = isOnline;
  const badge = $('online');
  badge.textContent = t(isOnline ? 'online' : 'offline');
  badge.className = `badge ${isOnline ? 'online' : 'offline'}`;
  if (!isOnline && (changed || reason)) {
    $('bot-state').textContent = '-';
    notify(reason || t('offline_text'), 'error');
  } else if (isOnline && changed) {
    notify(t('connected'), 'ok');
  }
}

/** @param {{state?: string, args?: string[], host?: string} | undefined} state */
function showState(state) {
  if (!state || typeof state !== 'object') return;
  lastState = state;
  $('host').textContent = typeof state.host === 'string' ? state.host : '-';
  $('bot-state').textContent = t(state.state === 'running' ? 'running' : 'standby');
  $('bot-args').textContent = Array.isArray(state.args) && state.args.length ? `python bot.py ${state.args.join(' ')}` : '-';
}

/** The dashboard's own words for a device answer; refusals and failures keep the device's detail. */
function resultText(result) {
  const key = `code_${result.code}`;
  const text = t(key);
  return text === key ? String(result.message || '') : text;
}

function handleResult(result) {
  notify(resultText(result), result.ok ? 'ok' : 'error');
  showState(result.state);
  if (result.ok && result.code === 'deploy_loaded' && result.data) editor.load(result.data);
}

class LocalLink {
  /** The bot on this PC: log over SSE, commands over POST. */
  static async open(channel) {
    const url = `/api/stream?channel=${encodeURIComponent(channel)}`;
    const probe = await fetch(url);
    if (probe.status === 403) throw new Error(t('key_not_here'));
    await probe.body.cancel();
    return new LocalLink(url);
  }

  constructor(url) {
    this.source = new EventSource(url);
    // Every (re)connect starts with the state and the whole recent log.
    this.source.onopen = () => { clearLines(); setOnline(true); };
    this.source.onerror = () => setOnline(false, t('local_lost'));
    this.source.addEventListener('state', (message) => showState(JSON.parse(message.data)));
    this.source.addEventListener('line', (message) => addLines([JSON.parse(message.data)]));
    this.source.addEventListener('snapshot', (message) => {
      const shot = JSON.parse(message.data);
      editor.image(shot.screen, shot.image);
    });
  }

  async send(body, sig) {
    const response = await fetch('/api/command', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ body, sig }),
    });
    return response.json();
  }

  close() {
    this.source.close();
  }
}

class RelayLink {
  /** The device's Realtime channel; the device is online while its signed state keeps arriving. */
  static async open(channel) {
    if (!SUPABASE_KEY) throw new Error(t('relay_missing'));
    return new RelayLink(channel);
  }

  constructor(name) {
    this.waiting = new Map();
    this.heard = 0;
    this.stateTime = 0;
    this.channel = new RealtimeChannel(SUPABASE_URL, SUPABASE_KEY, name, {
      status: (joined) => {
        if (joined) this.channel.send('hello', {});
        else setOnline(false, t('relay_lost'));
      },
      broadcast: (event, payload) => { this.received(event, payload); },
    });
    this.watch = setInterval(() => {
      if (online && Date.now() - this.heard > OFFLINE_AFTER_MS) setOnline(false);
    }, 3000);
  }

  async received(event, payload) {
    const data = await opened(payload);
    if (!data) return; // not from this device
    this.heard = Date.now();
    if (event === 'state') {
      if (data.ts < this.stateTime) return;
      this.stateTime = data.ts;
      setOnline(true);
      showState(data);
    } else if (event === 'log') {
      setOnline(true);
      addLines(Array.isArray(data.lines) ? data.lines : []);
    } else if (event === 'history') {
      setOnline(true);
      clearLines();
      addLines(Array.isArray(data.lines) ? data.lines : []);
      showState(data.state);
    } else if (event === 'snapshot') {
      editor.image(data.screen, data.image);
    } else if (event === 'ack') {
      const done = this.waiting.get(data.nonce);
      if (done) {
        this.waiting.delete(data.nonce);
        done(data);
      }
    }
  }

  send(body, sig, nonce) {
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.waiting.delete(nonce);
        resolve({ ok: false, message: t('no_answer') });
      }, ACK_MS);
      this.waiting.set(nonce, (payload) => { clearTimeout(timer); resolve(payload); });
      if (!this.channel.send('cmd', { body, sig })) {
        clearTimeout(timer);
        this.waiting.delete(nonce);
        resolve({ ok: false, message: t('relay_lost') });
      }
    });
  }

  close() {
    clearInterval(this.watch);
    this.channel.close();
  }
}

/** @returns {Record<string, unknown>} the choices, in the shape remote.build_args accepts */
function readOptions() {
  const data = new FormData($('options'));
  const task = data.get('task');
  const number = (name, fallback = 0) => {
    const value = Number.parseInt(String(data.get(name) ?? ''), 10);
    return Number.isFinite(value) ? value : fallback;
  };
  const options = { task };
  if (task === 'walls') options.walls = data.get('walls');
  if (task === 'farm') {
    options.builder = data.getAll('builder');
    options.home = data.getAll('home');
  }
  if (task === 'walls' || task === 'farm') {
    options.first = data.get('first');
    for (const name of ['hours', 'minutes']) if (number(name)) options[name] = number(name);
    options.max_recoveries = number('max_recoveries', 20);
  }
  if (task === 'walls' && number('max_upgrades')) options.max_upgrades = number('max_upgrades');
  if (['walls', 'farm', 'attack'].includes(task) && number('searches')) options.searches = number('searches');
  if (task === 'attack' || task === 'dry') options.village = data.get('village');
  if (task === 'attack') options.cycles = Math.max(1, number('cycles', 1));
  return options;
}

/** Same arguments as remote.build_args, for the preview only; the device builds and checks its own. */
function previewArgs(options) {
  let args;
  if (options.task === 'attack' || options.task === 'dry') {
    args = ['--mode', options.village];
    if (options.task === 'dry') return args;
    args.push('--cycles', String(options.cycles));
  } else if (options.task === 'walls') {
    args = ['--walls', options.walls];
  } else {
    args = [];
    for (const village of ['builder', 'home']) {
      if (options[village].length) args.push(`--${village}`, options[village].join(','));
    }
    if (!args.length) args = ['--until-full'];
  }
  if (options.first) args.push('--first', options.first);
  for (const [name, flag] of [['searches', '--searches'], ['hours', '--hours'], ['minutes', '--minutes'], ['max_upgrades', '--max-upgrades']]) {
    if (options[name]) args.push(flag, String(options[name]));
  }
  if (options.max_recoveries !== undefined) args.push('--max-recoveries', String(options.max_recoveries));
  return [...args, '--live'];
}

function refreshOptions() {
  const options = readOptions();
  for (const element of document.querySelectorAll('[data-for]')) {
    element.hidden = !element.dataset.for.split(' ').includes(options.task);
  }
  $('preview').textContent = `$ python bot.py ${previewArgs(options).join(' ')}`;
  const saved = {};
  for (const element of $('options').elements) {
    if (!element.name) continue;
    if (element.type === 'checkbox' || element.type === 'radio') {
      if (element.checked) saved[`${element.name}=${element.value}`] = true;
    } else {
      saved[element.name] = element.value;
    }
  }
  stored.set(STORE_OPTIONS, JSON.stringify(saved));
}

function restoreOptions() {
  let saved;
  try { saved = JSON.parse(stored.get(STORE_OPTIONS) || 'null'); } catch { saved = null; }
  if (!saved) return;
  for (const element of $('options').elements) {
    if (!element.name) continue;
    if (element.type === 'checkbox' || element.type === 'radio') element.checked = Boolean(saved[`${element.name}=${element.value}`]);
    else if (saved[element.name] !== undefined) element.value = saved[element.name];
  }
}

function setBusy(busy) {
  for (const button of document.querySelectorAll('#options button, #deploy-load, #deploy-save')) button.disabled = busy;
}

async function send(action, args = {}) {
  if (!link) return;
  if (!online) {
    notify(t('cannot_run', { action, reason: t('offline_text') }), 'error');
    return;
  }
  const nonce = crypto.randomUUID();
  const body = JSON.stringify({ action, args, ts: Date.now() / 1000, nonce });
  setBusy(true);
  notify(t('sending', { action }));
  try {
    handleResult(await link.send(body, await sign(body), nonce));
  } catch (error) {
    notify(t('send_failed', { error: error instanceof Error ? error.message : String(error) }), 'error');
  } finally {
    setBusy(false);
  }
}

async function connect(rawKey) {
  const key = rawKey.trim();
  const problem = $('connect-error');
  problem.hidden = true;
  if (!KEY_PATTERN.test(key)) {
    problem.textContent = t('key_format');
    problem.hidden = false;
    return;
  }
  await useKey(key);
  const channel = await channelOf(key);
  if (link) link.close();
  clearLines();
  online = false;
  try {
    link = LOCAL ? await LocalLink.open(channel) : await RelayLink.open(channel);
  } catch (error) {
    link = null;
    problem.textContent = error instanceof Error ? error.message : String(error);
    problem.hidden = false;
    return;
  }
  stored.set(STORE_KEY, key);
  $('connect').hidden = true;
  $('dashboard').hidden = false;
  if (!LOCAL) setOnline(false, t('waiting_device'));
}

function changeLanguage(language) {
  applyLanguage(language);
  $('mode').textContent = t(LOCAL ? 'mode_local' : 'mode_online');
  const badge = $('online');
  badge.textContent = t(online ? 'online' : 'offline');
  showState(lastState);
  editor.render();
}

function init() {
  editor = new DeployEditor((screen, deploy, points) => send('set_deploy', { screen, deploy, points }));
  changeLanguage();
  restoreOptions();
  refreshOptions();
  for (const button of document.querySelectorAll('[data-lang]')) {
    button.addEventListener('click', () => changeLanguage(button.dataset.lang));
  }
  $('options').addEventListener('change', refreshOptions);
  $('options').addEventListener('input', refreshOptions);
  $('options').addEventListener('submit', (submitted) => {
    submitted.preventDefault();
    send('start', readOptions());
  });
  for (const button of document.querySelectorAll('[data-action]')) {
    button.addEventListener('click', () => {
      if (button.dataset.confirm && !window.confirm(button.dataset.confirm)) return;
      send(button.dataset.action);
    });
  }
  $('deploy-load').addEventListener('click', () => send('get_deploy'));
  $('key-form').addEventListener('submit', (submitted) => {
    submitted.preventDefault();
    connect($('key').value);
  });
  $('forget').addEventListener('click', () => {
    if (link) link.close();
    link = null;
    hmacKey = null;
    stored.set(STORE_KEY, null);
    $('key').value = '';
    $('dashboard').hidden = true;
    $('connect').hidden = false;
  });
  $('clear').addEventListener('click', clearLines);
  const saved = stored.get(STORE_KEY);
  if (saved) {
    $('key').value = saved;
    connect(saved);
  }
}

init();
