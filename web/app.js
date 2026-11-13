// coc-auto dashboard: live log and control for one device, found by its API key. On localhost it talks to the
// bot on this PC directly; online it goes through the relay channel named after the key's hash, and
// only trusts messages signed with the key, so nobody else on the channel can pose as the device.
import { RELAY_URL, RELAY_KEY } from './config.js';
import { applyLanguage, t } from './i18n.js?v=5';
import { translate } from './messages.js?v=5';
import { RealtimeChannel } from './realtime.js';
import { DeployEditor } from './deploy.js?v=3';
import { SettingsPanel } from './settings.js?v=10';
import { setupSelects, refreshSelects } from './select.js?v=2';
import { StatsPanel, previewStatistics } from './stats.js?v=17';
import { DiscordPanel } from './discord.js?v=7';
import { AiPanel } from './ai.js?v=1';
import { VisiblePoller } from './visible-poller.js?v=2';
import { BuildingsPanel, LISTS as BUILDING_LISTS } from './buildings.js?v=22';

const DEMO = new URLSearchParams(location.search).get('preview') === '1';
const ACK_MS = 10000;
const OFFLINE_AFTER_MS = 40000; // the device repeats its signed state every 15 s
const MAX_SKEW_SECONDS = 300;
const MAX_LINES = 2000;
const STORE_REQUIREMENTS = 'coc-requirements-acknowledged-v1';
const STORE_KEY = 'coc-remote-key';
// v2: surrender at 50% became the default; choices saved before it held 0 there.
const STORE_OPTIONS = 'coc-remote-options-v2';
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
let settings = null;
let stats = null;
let discord = null;
let ai = null;
let buildings = null;
let settingsAsked = false;
let currentNotice = null;
let connectionProblem = null;
let initialDisplayChecked = false;

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
    row.sourceMessage = line;
    row.textContent = translate(line);
    row.className = lineClass(line);
    fragment.append(row);
  }
  view.append(fragment);
  while (view.childElementCount > MAX_LINES) view.firstElementChild.remove();
  $('log-count').textContent = String(view.childElementCount);
  if ($('follow').checked) view.scrollTop = view.scrollHeight;
}

function clearLines() {
  $('log').textContent = '';
  $('log-count').textContent = '0';
}

function notify(message, kind = 'info') {
  currentNotice = { message, kind };
  renderNotice();
}

function renderNotice() {
  if (!currentNotice) return;
  const { message, kind } = currentNotice;
  const notice = $('notice');
  const text = typeof message === 'function' ? message() : translate(message);
  notice.textContent = text;
  notice.className = `notice ${kind}`;
  notice.hidden = !text;
}

function renderConnectionProblem() {
  if (connectionProblem) $('connect-error').textContent = connectionProblem();
}

function setOnline(isOnline, reason) {
  const changed = online !== isOnline;
  online = isOnline;
  const badge = $('online');
  badge.textContent = t(isOnline ? 'online' : 'offline');
  badge.className = `badge ${isOnline ? 'online' : 'offline'}`;
  if (!isOnline && (changed || reason)) {
    $('bot-state').textContent = '-';
    $('screen-status').textContent = t('offline');
    notify(reason || (() => t('offline_text')), 'error');
  } else if (isOnline && changed) {
    notify(() => t('connected'), 'ok');
    // Show the device's settings and its deploy pins over the maps once per connection, without a click.
    if (!settingsAsked) {
      settingsAsked = true;
      setTimeout(async () => {
        await send('get_settings', {}, { quiet: true });
        await send('get_deploy', {}, { quiet: true });
        await send('get_discord', {}, { quiet: true });
        await send('get_ai', {}, { quiet: true });
        for (const list of BUILDING_LISTS) await send('get_buildings', { list }, { quiet: true });
      }, 0);
    }
  }
  screenPoller.sync();
  upgradePoller.sync();
  stats?.sync();
}

/** @param {{state?: string, args?: string[], host?: string} | undefined} state */
function showState(state) {
  if (!state || typeof state !== 'object') return;
  lastState = state;
  updateRunMetrics();
  $('host').textContent = typeof state.host === 'string' ? state.host : '-';
  $('bot-state').textContent = t(state.state === 'running' ? 'running' : 'standby');
  $('bot-args').textContent = Array.isArray(state.args) && state.args.length ? `python bot.py ${state.args.join(' ')}` : '-';
}

/** The dashboard's own words for a device answer; refusals and failures keep the device's detail. */
function resultText(result) {
  if (['no_answer', 'relay_lost'].includes(result.code)) return t(result.code);
  const key = `code_${result.code}`;
  const text = t(key);
  return text === key ? translate(String(result.source_message ?? result.message ?? '')) : text;
}

const SETTINGS_ANSWERS = ['settings_loaded', 'minimum_saved', 'setup_saved', 'language_saved', 'buildings_saved', 'goblin_saved'];
const DISPLAY_ANSWERS = ['display_fitted', 'display_ok', 'display_restored', 'display_unchanged'];

function handleResult(result, quiet = false) {
  if (!quiet || !result.ok) notify(() => resultText(result), result.ok ? 'ok' : 'error');
  showState(result.state);
  if (!result.ok || !result.data) return;
  if (result.code === 'stats_loaded') { stats.load(result.data); if (result.data.period === 'run') discord.stats(result.data); }
  else if (['discord_loaded','discord_saved','discord_tested'].includes(result.code)) { discord.load(result.data, result.code); refreshSelects(); }
  else if (['ai_loaded','ai_saved','ai_tested'].includes(result.code)) { ai.load(result.data, result.code); refreshSelects(); }
  else if (result.code === 'deploy_loaded') editor.load(result.data);
  else if (['buildings_scanned', 'buildings_loaded'].includes(result.code)) buildings.load(result.data);
  else if (result.code === 'setup_checked') settings.checkedSetup(result.data);
  else if (result.code === 'setup_detected') settings.detected(result.data);
  else if (SETTINGS_ANSWERS.includes(result.code)) {
    settings.load(result.data);
    if (result.code === 'buildings_saved') buildings.acknowledge(result.data);
    if (result.code === 'goblin_saved') buildings.acknowledgeGoblin(result.data);
    buildings.select(result.data);
    if (result.code === 'settings_loaded' && !initialDisplayChecked) {
      initialDisplayChecked = true;
      if (!settings.displayMatches()) switchView('settings');
    }
    // The device's terminal speaks the dashboard's language; one still on the other is switched once.
    const shown = document.documentElement.lang;
    if (result.code === 'settings_loaded' && result.data.language && result.data.language !== shown) {
      send('set_language', { language: shown }, { quiet: true });
    }
  }
  else if (DISPLAY_ANSWERS.includes(result.code)) settings.showDisplay(result.data);
}

class LocalLink {
  /** The bot on this PC: log over SSE, commands over POST. */
  static async open(channel) {
    const url = `/api/stream?channel=${encodeURIComponent(channel)}`;
    const probe = await fetch(url);
    if (probe.status === 403) throw Object.assign(new Error(t('key_not_here')), { code: 'key_not_here' });
    await probe.body.cancel();
    return new LocalLink(url);
  }

  constructor(url) {
    this.source = new EventSource(url);
    // Every (re)connect starts with the state and the whole recent log.
    this.source.onopen = () => { clearLines(); setOnline(true); };
    this.source.onerror = () => setOnline(false, () => t('local_lost'));
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
    if (!RELAY_KEY) throw Object.assign(new Error(t('relay_missing')), { code: 'relay_missing' });
    return new RelayLink(channel);
  }

  constructor(name) {
    this.waiting = new Map();
    this.heard = 0;
    this.stateTime = 0;
    this.channel = new RealtimeChannel(RELAY_URL, RELAY_KEY, name, {
      status: (joined) => {
        if (joined) this.channel.send('hello', {});
        else setOnline(false, () => t('relay_lost'));
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
      addLines(Array.isArray(data.source_lines) ? data.source_lines : Array.isArray(data.lines) ? data.lines : []);
    } else if (event === 'history') {
      setOnline(true);
      clearLines();
      addLines(Array.isArray(data.source_lines) ? data.source_lines : Array.isArray(data.lines) ? data.lines : []);
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

  send(body, sig, nonce, wait = ACK_MS) {
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.waiting.delete(nonce);
        resolve({ ok: false, code: 'no_answer' });
      }, wait);
      this.waiting.set(nonce, (payload) => { clearTimeout(timer); resolve(payload); });
      if (!this.channel.send('cmd', { body, sig })) {
        clearTimeout(timer);
        this.waiting.delete(nonce);
        resolve({ ok: false, code: 'relay_lost' });
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
  if (task === 'walls') {
    options.walls = data.get('walls');
    if (data.get('bulk_walls')) options.bulk_walls = true;
    if (data.get('wall_resource') !== 'both') options.wall_resource = data.get('wall_resource');
    if (number('wall_every')) options.wall_every = number('wall_every');
  }
  if (task === 'farm') {
    options.builder = data.getAll('builder');
    options.home = data.getAll('home');
    if (data.get('dark') && !options.builder.length && !options.home.length) options.dark = true;
  }
  if (task === 'walls' || task === 'farm') {
    const selected = data.get('target_village') || 'home';
    options.first = selected === 'both' ? data.get('first') : selected;
  }
  if (['walls', 'farm', 'buildings'].includes(task)) {
    for (const name of ['hours', 'minutes']) if (number(name)) options[name] = number(name);
    options.max_recoveries = number('max_recoveries', 20);
  }
  if (task === 'walls' && number('max_upgrades')) options.max_upgrades = number('max_upgrades');
  if (['walls', 'farm', 'attack', 'buildings'].includes(task) && number('searches')) options.searches = number('searches');
  if (task === 'attack' || task === 'dry') options.village = data.get('village');
  if (task === 'attack') options.cycles = Math.max(1, number('cycles', 1));
  // The buildings task attacks too, when a pick is short of resources.
  if (['walls', 'farm', 'attack', 'buildings'].includes(task)) {
    if (data.get('surrender')) {
      options.surrender = data.get('surrender');
      options.surrender_after = number('surrender_after', 60);
    }
    // Always sent, 0 included: the bot surrenders at 50% unless told otherwise.
    options.surrender_at = number('surrender_at', 50);
    options.surrender_stars = number('surrender_stars', 2);
    options.trophy_floor = number('trophy_floor', 0);
    options.home_style = data.get('home_style');
    options.builder_style = data.get('builder_style');
    if (data.get('quake')) options.quake = true;
  }
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
    if (options.bulk_walls) args.push('--bulk-walls');
    if (options.wall_resource) args.push('--wall-resource', options.wall_resource);
  } else if (options.task === 'buildings') {
    args = ['--buildings'];
  } else {
    args = [];
    for (const village of ['builder', 'home']) {
      if (options[village].length) args.push(`--${village}`, options[village].join(','));
    }
    if (!args.length) args = options.dark ? ['--until-full', '--dark'] : ['--until-full'];
  }
  if (options.first) args.push('--first', options.first);
  for (const [name, flag] of [['searches', '--searches'], ['hours', '--hours'], ['minutes', '--minutes'], ['max_upgrades', '--max-upgrades'], ['wall_every', '--wall-every']]) {
    if (options[name]) args.push(flag, String(options[name]));
  }
  if (options.max_recoveries !== undefined) args.push('--max-recoveries', String(options.max_recoveries));
  if (options.surrender) args.push('--surrender', options.surrender, '--surrender-after', String(options.surrender_after));
  for (const style of ['ring', 'stack']) {
    const villages = ['home', 'builder'].filter((village) => options[`${village}_style`] === style);
    if (villages.length) args.push(`--${style}`, villages.length === 2 ? 'both' : villages[0]);
  }
  if (options.quake) args.push('--quake');
  if (options.surrender_at !== undefined) args.push('--surrender-at', String(options.surrender_at));
  if (options.surrender_stars !== undefined) args.push('--surrender-stars', String(options.surrender_stars));
  if (options.trophy_floor) args.push('--trophy-floor', String(options.trophy_floor));
  return [...args, '--live'];
}

// Army templates fill in how the attack is fought; the fields can still be changed by hand afterwards.
const ARMY_TEMPLATES = {
  valkyrie_quake: { style: 'ring', quake: true, surrender_at: 50 },
  barbarian_skeleton: { style: 'ring', quake: false, surrender_at: 50 },
  dragons: { style: 'stack', quake: false, surrender_at: 50 },
  miner_skeleton: { style: 'ring', quake: false, surrender_at: 50 },
  super_minion: { style: 'ring', quake: false, surrender_at: 50 },
};

// Builder Base templates: Witches go together on the deploy point, the rest spread round the base.
const BUILDER_TEMPLATES = { baby_dragon: 'ring', witch: 'stack', raged_barbarian: 'ring' };

function applyBuilderTemplate() {
  const form = $('options').elements;
  const style = BUILDER_TEMPLATES[form.namedItem('builder_army').value];
  if (style) form.namedItem('builder_style').value = style;
}

function applyTemplate() {
  const form = $('options').elements;
  const template = ARMY_TEMPLATES[form.namedItem('army').value];
  if (!template) return;
  form.namedItem('home_style').value = template.style;
  form.namedItem('quake').checked = template.quake;
  form.namedItem('surrender_at').value = String(template.surrender_at);
}

function refreshOptions() {
  syncVillageControls();
  // "Include dark elixir" belongs to the plain fill-until-full; picked resources say it themselves.
  const form = $('options');

  const options = readOptions();
  for (const element of document.querySelectorAll('[data-for]')) {
    element.hidden = !element.dataset.for.split(' ').includes(options.task);
  }
  syncVillageControls();
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
  refreshSelects();
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
  for (const button of document.querySelectorAll('#options button, #deploy-load, #deploy-save, .settings button')) button.disabled = busy;
  settings?.busy(busy);
  refreshSelects();
  if (!LOCAL) for (const b of document.querySelectorAll('#discord-form button, #ai-form button')) b.disabled=true;
  if (ai?.saved) $('ai-test').disabled=!LOCAL||!ai.saved.key_set||!ai.saved.base_url||!ai.saved.model||ai.dirty;
  if (discord?.saved) { $('discord-test').disabled=!LOCAL||!discord.saved.configured||discord.dirty; $('discord-remove').disabled=!LOCAL||!discord.saved.configured; }
}

/** Send a signed command; quiet ones (loading on connect) only speak up when they fail. A command that works on the
 * device for a while (the building scan) waits longer for its answer. */
async function send(action, args = {}, { quiet = false, wait = ACK_MS, label = action } = {}) {
  if (!link) return;
  if (!online) {
    notify(() => t('cannot_run', { action: label, reason: t('offline_text') }), 'error');
    return;
  }
  const nonce = crypto.randomUUID();
  const body = JSON.stringify({ action, args, ts: Date.now() / 1000, nonce });
  setBusy(true);
  if (!quiet) notify(() => t('sending', { action: label }));
  try {
    handleResult(await link.send(body, await sign(body), nonce, wait), quiet);
  } catch (error) {
    notify(() => t('send_failed', { error: error instanceof Error ? error.message : String(error) }), 'error');
  } finally {
    setBusy(false);
  }
}

async function connect(rawKey) {
  const key = rawKey.trim();
  const problem = $('connect-error');
  problem.hidden = true;
  connectionProblem = null;
  if (!KEY_PATTERN.test(key)) {
    connectionProblem = () => t('key_format');
    renderConnectionProblem();
    problem.hidden = false;
    return;
  }
  await useKey(key);
  const channel = await channelOf(key);
  if (link) link.close();
  resetScreen();
  clearLines();
  online = false;
  settingsAsked = false;
  initialDisplayChecked = false;
  try {
    link = LOCAL ? await LocalLink.open(channel) : await RelayLink.open(channel);
  } catch (error) {
    link = null;
    connectionProblem = () => ['key_not_here', 'relay_missing'].includes(error?.code)
      ? t(error.code) : error instanceof Error ? error.message : String(error);
    renderConnectionProblem();
    problem.hidden = false;
    return;
  }
  stored.set(STORE_KEY, key);
  $('connect').hidden = true;
  $('dashboard').hidden = false;
  if (!LOCAL) setOnline(false, () => t('waiting_device'));
}

function changeLanguage(language) {
  applyLanguage(language);
  stats?.localize();
  discord?.localize();
  ai?.localize();
  $('mode').textContent = t(LOCAL ? 'mode_local' : 'mode_online');
  const badge = $('online');
  badge.textContent = t(online ? 'online' : 'offline');
  showState(lastState);
  editor.render();
  settings.render();
  buildings.render();
  for (const row of $('log').children) row.textContent = translate(row.sourceMessage);
  renderNotice();
  renderConnectionProblem();
  if (typeof activeView !== 'undefined') switchView(activeView);
  refreshSelects();
}

/** Installable app: the browser offers it once the page qualifies; the button appears only then. */
function setUpInstall() {
  let offer = null;
  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    offer = event;
    $('install').hidden = false;
  });
  window.addEventListener('appinstalled', () => { $('install').hidden = true; });
  $('install').addEventListener('click', async () => {
    if (!offer) return;
    offer.prompt();
    await offer.userChoice;
    offer = null;
    $('install').hidden = true;
  });
  // Online only: the local dashboard is served by the bot itself and must always be the live one.
  if ('serviceWorker' in navigator && !LOCAL) {
    navigator.serviceWorker.register('sw.js').catch(() => { /* the dashboard still works, only not offline */ });
  }
}

function init() {
  setUpInstall();
  setupWorkspace();
  setupRequirements();
  stats = new StatsPanel(period => send('get_stats', {period}, {quiet:true}),
    () => online && link !== null && !document.hidden && screenPageActive && activeView === 'stats' && !$('dashboard').hidden);
  discord = new DiscordPanel(send, {local: LOCAL, preview: DEMO});
  ai = new AiPanel(send, {local: LOCAL});
  setupSelects();
  editor = new DeployEditor((screen, deploy, points) => send('set_deploy', { screen, deploy, points }));
  settings = new SettingsPanel(send);
  $('setup-continue').addEventListener('click', () => switchView('overview'));
  buildings = new BuildingsPanel(send);
  changeLanguage();
  restoreOptions();
  refreshOptions();
  for (const button of document.querySelectorAll('[data-lang]')) {
    button.addEventListener('click', () => {
      changeLanguage(button.dataset.lang);
      if (link && online) send('set_language', { language: button.dataset.lang }, { quiet: true });
    });
  }
  $('options').elements.namedItem('army').addEventListener('change', applyTemplate);
  $('options').elements.namedItem('builder_army').addEventListener('change', applyBuilderTemplate);
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
    resetScreen();
    hmacKey = null;
    stored.set(STORE_KEY, null);
    $('key').value = '';
    $('dashboard').hidden = true;
    $('connect').hidden = false;
  });
  $('clear').addEventListener('click', clearLines);
  if (DEMO) { openDemo(); return; }
  const fragment = new URLSearchParams(location.hash.slice(1));
  const localKey = LOCAL ? fragment.get('key') : null;
  if (localKey) history.replaceState(null, '', location.pathname + location.search);
  const saved = localKey || stored.get(STORE_KEY);
  if (saved) {
    $('key').value = saved;
    connect(saved);
  }
}



const DEMO_DEPLOY = {"resolution": [1280, 720], "screens": {"home_scout": {"kind": "spells", "deploy": [125, 346], "points": [[380, 346], [290, 271], [290, 421], [500, 346], [410, 271], [410, 421], [620, 346], [530, 271], [530, 421], [740, 346], [650, 271], [650, 421]], "bar": [0, 598, 1280, 106]}, "home_ring": {"kind": "ring", "deploy": [125, 346], "points": [[141, 299], [243, 232], [356, 156], [470, 82], [810, 82], [924, 156], [1037, 232], [1139, 299], [1139, 391], [1037, 458], [924, 534], [243, 458], [141, 391]], "bar": [0, 598, 1280, 106]}, "home_spell_spread": {"kind": "spell_spread", "deploy": [125, 346], "points": [[640, 330], [490, 321], [521, 301], [555, 278], [589, 256], [691, 256], [725, 278], [759, 301], [795, 321], [795, 348], [759, 368], [725, 391], [640, 399], [521, 368], [490, 348]], "bar": [0, 598, 1280, 106]}, "home_quake": {"kind": "quake", "deploy": [125, 346], "points": [[640, 150], [960, 330], [640, 520], [320, 330]], "bar": [0, 598, 1280, 106]}, "builder_scout": {"kind": "points", "deploy": [150, 370], "points": [[345, 120], [850, 140], [1027, 264], [1000, 500], [380, 513], [238, 340]], "bar": [0, 588, 1280, 120]}, "builder_stage2": {"kind": "points", "deploy": [73, 299], "points": [[396, 92], [830, 51], [926, 134], [921, 457], [263, 359], [254, 205], [982, 308], [413, 489]], "bar": [0, 588, 1280, 120]}}};

const DEMO_BUILDINGS = [
  {id:'archer_queen',name:'Archer Queen',category:'hero',count:1,current_level:77,next_level:78,seconds:129600,duration_seconds:345600,section:'in_progress'},
  {id:'eagle_artillery',name:'Eagle Artillery',category:'defense',count:1,current_level:5,next_level:6,time:'17h 57m',seconds:64620,cost:null,currency:null,section:'in_progress'},
  {id:'barbarian_king',name:'Barbarian King',category:'hero',count:1,next_level:11,time:'11h',cost:5250,currency:'dark',section:'suggested'},
  {id:'hero_hall',name:'Hero Hall',category:'army',count:1,next_level:10,time:'4d',cost:6500000,currency:'elixir',section:'suggested'},
  {id:'archer_tower',name:'Archer Tower',category:'defense',count:3,next_level:8,time:'2h',cost:100000,currency:'gold',section:'other'},
  {id:'gold_storage',name:'Gold Storage',category:'resource',count:4,next_level:16,time:'1d 12h',cost:2000000,currency:'elixir',section:'other'},
  {id:'spring_trap',name:'Spring Trap',category:'trap',count:9,next_level:2,time:'30m 0s',cost:65000,currency:'gold',section:'other'},
];
const DEMO_LAB = [
  {id:'root_rider',name:'Root Rider',category:'troop',count:1,current_level:2,next_level:3,seconds:93600,duration_seconds:259200,section:'in_progress'},
  {id:'valkyrie',name:'Valkyrie',category:'dark_troop',count:1,next_level:6,time:'1d',cost:15750,currency:'dark',section:'suggested'},
  {id:'balloon',name:'Balloon',category:'troop',count:1,next_level:10,time:'3d 12h',cost:5000000,currency:'elixir',section:'suggested'},
  {id:'giant',name:'Giant',category:'troop',count:1,next_level:5,time:'6h',cost:400000,currency:'elixir',section:'other'},
  {id:'poison_spell',name:'Poison Spell',category:'dark_spell',count:1,next_level:4,time:'1d',cost:10750,currency:'dark',section:'other'},
  {id:'freeze_spell',name:'Freeze Spell',category:'spell',count:1,next_level:3,time:'18h',cost:850000,currency:'elixir',section:'other'},
  {id:'wall_wrecker',name:'Wall Wrecker',category:'siege',count:1,next_level:2,time:'1d',cost:1250000,currency:'elixir',section:'other'},
];
const DEMO_PETS = [
  {id:'lassi',name:'L.A.S.S.I',category:'pet',count:1,current_level:10,next_level:11,time:'13h 57m',seconds:50220,cost:null,currency:null,section:'in_progress'},
];
const DEMO_GEAR = [
  {id:'giant_gauntlet',name:'Giant Gauntlet',category:'equipment',count:1,next_level:21,costs:{shiny:3000,glowy:600,starry:100},section:'equipped'},
  {id:'giant_arrow',name:'Giant Arrow',category:'equipment',count:1,next_level:16,costs:{shiny:2500},section:'equipped'},
  {id:'barbarian_puppet',name:'Barbarian Puppet',category:'equipment',count:1,next_level:6,costs:{shiny:840,glowy:null},section:'unequipped'},
];
const DEMO_BUILDER = [
  {id:'battle_machine',name:'Battle Machine',category:'hero',count:1,current_level:30,next_level:31,time:'2d 4h',seconds:187200,duration_seconds:518400,cost:null,currency:null,section:'in_progress'},
  {id:'battle_copter',name:'Battle Copter',category:'hero',count:1,next_level:19,time:'5d',cost:2900000,currency:'elixir',section:'suggested'},
  {id:'mega_tesla',name:'Mega Tesla',category:'defense',count:1,next_level:2,time:'3d',cost:3100000,currency:'gold',section:'other'},
];
const DEMO_STAR = [
  {id:'night_witch',name:'Night Witch',category:'troop',count:1,current_level:16,next_level:17,time:'1d 23h',seconds:169000,cost:null,currency:null,section:'in_progress'},
  {id:'cannon_cart',name:'Cannon Cart',category:'troop',count:1,next_level:11,time:'2d',cost:1400000,currency:'elixir',section:'suggested'},
];
const DEMO_SCANS = {buildings:DEMO_BUILDINGS, lab:DEMO_LAB, pets:DEMO_PETS, gear:DEMO_GEAR, builder:DEMO_BUILDER, star:DEMO_STAR};

let activeView = 'overview';

function switchView(view) {
  activeView = view;
  for (const button of document.querySelectorAll('[data-view]')) {
    const selected = button.dataset.view === view;
    button.classList.toggle('active', selected);
    button.setAttribute('aria-selected', String(selected));
    button.tabIndex = selected ? 0 : -1;
  }
  for (const panel of document.querySelectorAll('.page-panel')) panel.hidden = panel.id !== `view-${view}`;
  $('device-controls').hidden = view !== 'settings';
  const key = view === 'stats' ? 'stats' : view === 'buildings' ? 'upgrades' : view === 'deploy' ? 'deployment' : view === 'settings' ? 'settings' : 'overview';
  $('page-title').textContent = t(`${key}_title`);
  $('page-description').textContent = t(`${key}_description`);
  screenPoller.sync();
  upgradePoller.sync();
  stats?.sync();
  if (view === 'settings' && online && link) { send('get_discord', {}, {quiet:true}); send('get_ai', {}, {quiet:true}); }
  if (view === 'buildings' && online && link && !buildings.rows) send('get_buildings', {list: buildings.list}, {quiet:true});
}
function updateRunMetrics() {
  if (!lastState) return;
  const args = Array.isArray(lastState.args) ? lastState.args : [];
  const running = lastState.state === 'running';
  const seconds = running && lastState.since ? Math.max(0, Math.floor(Date.now()/1000-lastState.since)) : 0;
  $('run-duration').textContent = running ? `${String(Math.floor(seconds/3600)).padStart(2,'0')}:${String(Math.floor(seconds/60)%60).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}` : '—';
  const task = args.includes('--walls') ? 'task_walls' : args.includes('--buildings') ? 'task_buildings' : args.includes('--until-full') || args.includes('--home') || args.includes('--builder') ? 'task_farm' : args.includes('--live') ? 'task_attack' : 'task_dry';
  $('current-task').textContent = args.length ? t(task) : '—';
  $('start').querySelector('[data-i18n]').textContent = t(running ? 'restart_run' : 'start');
  const villageIndex = args.indexOf('--walls');
  const target = villageIndex >= 0 ? args[villageIndex + 1] : args.includes('--buildings') ? 'home' : args.includes('--until-full') ? 'both' : args.includes('--home') && !args.includes('--builder') ? 'home' : args.includes('--builder') && !args.includes('--home') ? 'builder' : args.includes('--mode') ? args[args.indexOf('--mode') + 1] : 'both';
  $('task-village').textContent = running ? (target === 'home' ? t('home_village') : target === 'builder' ? t('builder_village') : `${t('home_village')} + ${t('builder_village')}`) : t('metric_task_note');
  const index = args.indexOf('--max-recoveries');
  const budget = index >= 0 ? args[index+1] : '20';
  let recovery = 0;
  for (const row of $('log').children) {
    const match = row.textContent.match(/(?:recovery|pemulihan) (\d+)\/(\d+)/i);
    if (match) recovery = Number(match[1]);
    if (/(?:Attack finished|Serangan selesai|Wall upgraded|Tembok di-upgrade|Building upgraded|Bangunan di-upgrade)/i.test(row.textContent)) recovery = 0;
  }
  $('recovery-value').textContent = running ? `${recovery} / ${budget}` : '—';
}
let screenInViewport = false;
let screenPageActive = true;
// One frame a second, local and online alike (the online relay is our own server, without a message quota).
// The switch stops only the automatic frames; Refresh still takes one on demand.
const screenPoller = new VisiblePoller({visible: () => $('screen-live').checked && shouldCaptureScreen(), request: refreshScreen, interval: 1000});
// Read cached inventory plus confirmed receipts; this never navigates or scans the game.
const upgradePoller = new VisiblePoller({
  visible: () => online && !!link && activeView === 'buildings' && !document.hidden && screenPageActive,
  request: () => Promise.all(BUILDING_LISTS.map(list => send('get_buildings', {list}, {quiet:true}))),
  interval: 15000,
});
function shouldCaptureScreen() {
  return !DEMO && online && link !== null && !document.hidden && screenPageActive &&
    activeView === 'overview' && !$('dashboard').hidden && screenInViewport;
}
let screenPending = false;
let screenGeneration = 0;
async function refreshScreen() {
  if (screenPending || !shouldCaptureScreen()) return;
  if (DEMO) { $('screen-status').textContent = t('screen_source'); return; }
  screenPending = true;
  const currentLink = link;
  const generation = screenGeneration;
  const nonce = crypto.randomUUID();
  const body = JSON.stringify({action:'get_screen', args:{}, ts:Date.now()/1000, nonce});
  try {
    const result = await currentLink.send(body, await sign(body), nonce);
    if (currentLink !== link || generation !== screenGeneration) return;
    if (!result.ok || !result.data?.image?.startsWith('data:image/jpeg;base64,')) throw new Error('capture unavailable');
    const image = $('device-shot');
    image.onload = () => { image.hidden = false; $('screen-empty').hidden = true; };
    image.src = result.data.image;
    $('screen-status').textContent = t('screen_live');
    $('screen-updated').textContent = t('screen_updated', {time: new Date(result.data.captured_at * 1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'})});
  } catch {
    if (currentLink === link) $('screen-status').textContent = t('screen_unavailable');
  } finally { screenPending = false; }
}
function resetScreen() {
  screenGeneration++;
  $('device-shot').hidden = true;
  $('device-shot').removeAttribute('src');
  $('screen-empty').hidden = false;
  $('screen-status').textContent = t('screen_source');
  $('screen-updated').textContent = t('screen_auto');
  screenPoller.sync();
}
function syncVillageControls() {
  const form = $('options');
  const task = form.elements.namedItem('task').value;
  const targetGroup = form.elements.namedItem('target_village');
  const target = targetGroup.value || 'home';
  if (!targetGroup.value) targetGroup.value = target;
  const payment = form.elements.namedItem('wall_resource');
  if (!payment.value) payment.value = 'both';
  for (const image of form.querySelectorAll('[data-wall-currency]')) {
    const name = image.dataset.wallCurrency;
    image.src = `icons/resources/${target === 'builder' ? `builder_${name}.webp` : `${name}.png`}`;
  }
  for (const image of form.querySelectorAll('.builder-resource-variant')) image.hidden = target !== 'both';
  form.elements.namedItem('walls').value = target;
  form.elements.namedItem('village').value = target;
  for (const village of ['home', 'builder']) {
    const enabled = task === 'farm' && (target === 'both' || target === village);
    const group = form.querySelector(`[data-village-resources="${village}"]`);
    group.hidden = !enabled;
    const inputs = [...group.querySelectorAll('input')];
    for (const input of inputs) { input.disabled = !enabled; input.setCustomValidity(''); }
    if (enabled && !inputs.some(input => input.checked)) inputs[0].setCustomValidity(t('choose_resource'));
  }
  for (const label of form.querySelectorAll('.resource-village-label')) label.hidden = target !== 'both';
  // The buildings task attacks in the villages of its picks, so no village or resource is chosen for it.
  form.querySelector('.target-section').hidden = task === 'buildings';
  form.querySelector('.first-village').hidden = target !== 'both' || !['farm','walls'].includes(task);
  const minimum = $('minimum-fields');
  minimum.hidden = target === 'builder' || task === 'dry';
  const next = task === 'farm' ? form.querySelector('[data-village-resources="builder"]') : form.querySelector('.first-village');
  if (minimum.parentElement !== next.parentElement || minimum.nextElementSibling !== next) next.before(minimum);
}
function setupRequirements() {
  const banner = $('requirements-banner');
  banner.hidden = stored.get(STORE_REQUIREMENTS) === 'yes';
  $('requirements-acknowledge').addEventListener('click', () => {
    stored.set(STORE_REQUIREMENTS, 'yes');
    banner.hidden = true;
  });
  window.addEventListener('storage', (event) => {
    if (event.key === STORE_REQUIREMENTS || event.key === null) {
      banner.hidden = stored.get(STORE_REQUIREMENTS) === 'yes';
    }
  });
}
function setupWorkspace() {
  document.documentElement.dataset.theme = 'clash';
  for (const button of document.querySelectorAll('[data-view]')) {
    button.addEventListener('click', () => switchView(button.dataset.view));
    button.addEventListener('keydown', (event) => {
      if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      const buttons = [...document.querySelectorAll('[data-view]')];
      const index = buttons.indexOf(button);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length-1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length)%buttons.length;
      switchView(buttons[next].dataset.view); buttons[next].focus();
    });
  }
  for (const button of document.querySelectorAll('[data-open-view]')) button.addEventListener('click', () => switchView(button.dataset.openView));
  $('screen-refresh').addEventListener('click', refreshScreen);
  $('screen-live').addEventListener('change', () => {
    if (!$('screen-live').checked) $('screen-status').textContent = t('screen_source');
    screenPoller.sync();
  });
  document.addEventListener('visibilitychange', () => { screenPoller.sync(); upgradePoller.sync(); });
  window.addEventListener('pagehide', () => { screenPageActive = false; screenPoller.sync(); upgradePoller.sync(); });
  window.addEventListener('pageshow', () => { screenPageActive = true; screenPoller.sync(); upgradePoller.sync(); });
  const observer = new IntersectionObserver(([entry]) => {
    screenInViewport = entry.isIntersecting;
    screenPoller.sync();
  });
  observer.observe(document.querySelector('.device-screen'));
  setInterval(updateRunMetrics, 1000);
}
class DemoLink {
  constructor() {
    this.state = {state:'running', args:['--walls','home','--first','builder','--max-recoveries','20','--live'], host:'LDPlayer 01', since:Date.now()/1000-1147};
    this.minimum = {gold:1000000,elixir:1000000,dark:0};
    this.picks = {buildings:{targets:[{id:'archer_tower',name:'Archer Tower',level:8},{id:'spring_trap',name:'Spring Trap',level:null}],keep_builders:1},
      lab:{targets:[{id:'giant',name:'Giant',level:null}]}, pets:{targets:[]}};
    this.discord = {configured:false,enabled:false,ping_id:'',events:['ended','recovery','summary'],interval:30,status:'Not connected'};
    this.ai = {enabled:false,base_url:'',model:'',goal:'loot',hint:'',timeout:20,max_tokens:4000,probe_points:6,probe_max:12,t_max:150,key_set:false};
  }
  async send(body) {
    const {action,args} = JSON.parse(body);
    let code = 'settings_loaded', data;
    if (action === 'get_stats') {code='stats_loaded';data=previewStatistics(args.period);}
    else if (action === 'get_discord') {code='discord_loaded';data=this.discord;}
    else if (action === 'set_discord') {this.discord={...this.discord,...args,configured:args.remove?false:Boolean(args.url||this.discord.configured)};delete this.discord.url;code='discord_saved';data=this.discord;}
    else if (action === 'test_discord') {code='discord_tested';data=this.discord;}
    else if (action === 'get_ai') {code='ai_loaded';data=this.ai;}
    else if (action === 'set_ai') {const {api_key,...rest}=args;this.ai={...this.ai,...rest,key_set:Boolean(api_key||this.ai.key_set)};code='ai_saved';data=this.ai;}
    else if (action === 'test_ai') {code='ai_tested';data={...this.ai,tested_seconds:1.2};}
    else if (action === 'start') {this.state = {...this.state,state:'running',args:previewArgs(args),since:Date.now()/1000};code='started';addLines(['REMOTE: Run started.']);}
    else if (action === 'stop') {this.state.state='idle';code='stopped';addLines(['REMOTE: Run stopped.']);}
    else if (action === 'restart') {this.state.state='running';this.state.since=Date.now()/1000;code='restarted';}
    else if (action === 'get_deploy') {data=structuredClone(DEMO_DEPLOY);code='deploy_loaded';}
    else if (action === 'set_deploy') {DEMO_DEPLOY.screens[args.screen].deploy=args.deploy;DEMO_DEPLOY.screens[args.screen].points=args.points;code='deploy_saved';}
    else if (action === 'set_minimum') {this.minimum=args.minimum;code='minimum_saved';}
    else if (action === 'set_setup') code='setup_saved';
    else if (action === 'set_language') code='language_saved';
    else if (action === 'set_buildings') {this.picks[args.list]=args.buildings;code='buildings_saved';}
    else if (action === 'set_goblin') {this.picks.goblin=args.goblin;code='goblin_saved';}
    else if (action === 'check_setup') {code='setup_checked';data={ready:true,screen:'home',display:this.display()};}
    else if (action === 'detect_setup') {code='setup_detected';data={folders:['Demo LDPlayer installation'],instances:[{index:0,name:'Demo device',running:true}],resolution:[1280,720],problems:[]};}
    else if (action === 'open_coc') code='coc_opened';
    else if (action === 'close_coc') {this.state.state='idle';code='coc_closed';}
    else if (action === 'kill_ld') {this.state.state='idle';code='ld_off';}
    else if (action === 'restart_ld') {this.state.state='idle';code='ld_restarted';}
    else if (action === 'fit_display' || action === 'restore_display') {this.state.state='idle';code=action==='fit_display'?'display_ok':'display_unchanged';data=this.display();}
    else if (action === 'scan_buildings') {code='buildings_scanned';data={list:args.list,scanned_at:Date.now()/1000,rows:DEMO_SCANS[args.list]};}
    else if (action === 'get_buildings') {code='buildings_loaded';data={list:args.list,scanned_at:Date.now()/1000-240,rows:DEMO_SCANS[args.list]};}
    if (SETTINGS_ANSWERS.includes(code)) data={minimum:this.minimum,setup:{adb:'C:\\LDPlayer\\adb.exe',ldconsole:'C:\\LDPlayer\\ldconsole.exe',serial:'emulator-5554',ld_index:0,touch_device:'/dev/input/event4'},language:document.documentElement.lang,display:this.display(),...this.picks};
    return {ok:true,code,state:this.state,data};
  }
  display() {return {current:{width:1280,height:720,dpi:240},target:{width:1280,height:720,dpi:240},saved:false};}
  close() {}
}
async function openDemo() {
  await useKey('coc_local_preview_not_a_real_device_key');
  link = new DemoLink(); settingsAsked = true;
  $('connect').hidden=true; $('dashboard').hidden=false;
  $('forget').hidden=true;
  $('options').elements.namedItem('task').value='walls';
  $('options').elements.namedItem('target_village').value='home';
  for (const checkbox of $('options').querySelectorAll('input[name=home],input[name=builder]')) checkbox.checked=['gold','elixir'].includes(checkbox.value);
  refreshOptions();
  setOnline(true); showState(link.state); $('notice').hidden=true;
  addLines([
    'REMOTE: Connected to LDPlayer 01',
    'INFO: Wall upgraded with gold',
    'INFO: Wall upgraded with elixir',
    'INFO: Storage below target. Starting Home Village farming.',
    "INFO: Loot read: {'gold': 1323687, 'elixir': 1323687}",
    'INFO: Deploy plan from bar: 8 cards',
    'INFO: Surrender: damage reached 50%',
    'INFO: Attack finished: home (20)',
    'INFO: Searching for the next opponent…'
  ]);
  editor.load(structuredClone(DEMO_DEPLOY));
  handleResult(await link.send(JSON.stringify({action:'get_settings',args:{}})),true);
  handleResult(await link.send(JSON.stringify({action:'get_discord',args:{}})),true);
  for (const list of BUILDING_LISTS) handleResult(await link.send(JSON.stringify({action:'get_buildings',args:{list}})),true);
  discord.stats(previewStatistics());
  const previewView = new URLSearchParams(location.search).get('tab');
  if (['overview', 'stats', 'buildings', 'deploy', 'settings'].includes(previewView)) switchView(previewView);
}
init();
