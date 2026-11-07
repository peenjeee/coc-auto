// Building scan: every row of the game's builder list ("Buildings & Heroes") read on the device, grouped by
// category. Rows are ticked here for the "Upgrade buildings" task, first ticked first, each up to a level or Max; the
// scan itself upgrades nothing.
import { t } from './i18n.js';

const CATEGORIES = ['defense', 'army', 'resource', 'trap', 'hero', 'other'];
const COINS = { gold: 'icons/resources/gold.png', elixir: 'icons/resources/elixir.png', dark: 'icons/resources/dark_elixir.png' };
// The device opens, swipes and reads the whole list before it answers, which takes about a minute.
export const SCAN_WAIT_MS = 180000;
const MAX_LEVEL = 200;
const MAX_KEEP = 10;
const $ = (id) => document.getElementById(id);

/** @param {string} tag @param {string} [text] @param {string} [className] */
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

/** Time left, in the game's own style: the two largest units (Indonesian units when the page is Indonesian). */
function left(seconds) {
  const [d, h, m, s] = document.documentElement.lang === 'id' ? ['h', 'j', 'm', 'd'] : ['d', 'h', 'm', 's'];
  const days = Math.floor(seconds / 86400), hours = Math.floor(seconds / 3600) % 24, minutes = Math.floor(seconds / 60) % 60;
  if (days) return `${days}${d} ${hours}${h}`;
  if (hours) return `${hours}${h} ${minutes}${m}`;
  return minutes ? `${minutes}${m}` : seconds > 0 ? `${Math.ceil(seconds)}${s}` : t('buildings_done');
}

/** A target and a row are the same building by catalog id, or by name for a row the catalog does not know. */
function sameBuilding(target, row) {
  return target.id ? target.id === row.id : !row.id && String(target.name).toLowerCase() === String(row.name).toLowerCase();
}

export class BuildingsPanel {
  /** @param {(action: string, args?: Record<string, unknown>, options?: Record<string, unknown>) => void} send */
  constructor(send) {
    /** @type {Array<Record<string, unknown>> | null} */
    this.rows = null;
    /** @type {Array<{id: string | null, name: string, level: number | null}>} the pick, first one first */
    this.targets = [];
    $('buildings-scan').addEventListener('click', () => send('scan_buildings', {}, { wait: SCAN_WAIT_MS }));
    $('buildings-save').addEventListener('click', () => {
      const keep = Number.parseInt($('buildings-keep').value, 10);
      send('set_buildings', { buildings: { targets: this.targets, keep_builders: Number.isInteger(keep) ? Math.min(MAX_KEEP, Math.max(0, keep)) : 0 } });
    });
  }

  /** @param {{rows?: unknown}} data the rows the device read, top of the list first */
  load(data) {
    this.rows = Array.isArray(data?.rows) ? data.rows.filter((row) => row && typeof row === 'object') : [];
    // Running upgrades count down from the scan; the next scan reads the game's own timers again.
    this.scannedAt = Date.now();
    this.ticker ??= setInterval(() => this.tick(), 30000);
    this.render();
  }

  tick() {
    const passed = (Date.now() - this.scannedAt) / 1000;
    for (const [cell, total] of this.clocks ?? []) cell.textContent = left(Math.max(0, total - passed));
  }

  /** @param {{targets?: unknown, keep_builders?: unknown} | undefined} saved the pick the device keeps */
  select(saved) {
    const targets = Array.isArray(saved?.targets) ? saved.targets : [];
    this.targets = targets.filter((target) => target && typeof target.name === 'string').map((target) => ({
      id: typeof target.id === 'string' ? target.id : null,
      name: target.name,
      level: Number.isInteger(target.level) ? target.level : null,
    }));
    $('buildings-keep').value = String(Number.isInteger(saved?.keep_builders) ? saved.keep_builders : 0);
    this.render();
  }

  render() {
    this.renderPicked();
    this.clocks = [];
    const list = $('buildings-list');
    list.textContent = '';
    if (!this.rows) return;
    if (!this.rows.length) {
      list.append(element('p', t('buildings_empty'), 'hint'));
      return;
    }
    for (const category of CATEGORIES) {
      const rows = this.rows.filter((row) => (CATEGORIES.includes(row.category) ? row.category : 'other') === category);
      if (rows.length) list.append(this.group(category, rows));
    }
  }

  renderPicked() {
    const level = (target) => (target.level === null ? t('buildings_max') : `→ ${target.level}`);
    $('buildings-picked').textContent = this.targets.length
      ? t('buildings_picked', { list: this.targets.map((target, index) => `${index + 1}. ${target.name} ${level(target)}`).join(', ') })
      : t('buildings_picked_none');
  }

  toggle(row, picked) {
    this.targets = this.targets.filter((target) => !sameBuilding(target, row));
    if (picked) this.targets.push({ id: typeof row.id === 'string' ? row.id : null, name: String(row.name), level: null });
    this.render();
  }

  setLevel(row, value) {
    const level = Number.parseInt(value, 10);
    const target = this.targets.find((candidate) => sameBuilding(candidate, row));
    if (target) target.level = Number.isInteger(level) && level >= 1 && level <= MAX_LEVEL ? level : null;
    this.renderPicked();
  }

  /** The tick and target level of a row that can be upgraded; a running upgrade gets neither. */
  pickCells(row, line, name) {
    const target = row.section === 'in_progress' ? null : this.targets.find((candidate) => sameBuilding(candidate, row));
    const goal = line.insertCell();
    if (row.section === 'in_progress') return;
    const box = element('input', undefined, 'building-pick');
    Object.assign(box, { type: 'checkbox', checked: Boolean(target), title: t('buildings_pick') });
    box.setAttribute('aria-label', `${t('buildings_pick')}: ${row.name ?? '?'}`);
    box.addEventListener('change', () => this.toggle(row, box.checked));
    name.prepend(box);
    if (!target) return;
    name.append(element('em', `#${this.targets.indexOf(target) + 1}`, 'building-tag priority'));
    const level = element('input', undefined, 'building-level');
    Object.assign(level, { type: 'number', min: 1, max: MAX_LEVEL, placeholder: t('buildings_max'), value: target.level ?? '' });
    level.setAttribute('aria-label', `${t('buildings_target')}: ${row.name ?? '?'}`);
    level.addEventListener('change', () => this.setLevel(row, level.value));
    goal.append(level);
  }

  group(category, rows) {
    const section = element('section', undefined, 'building-group');
    section.append(element('h4', `${t(`buildings_${category}`)} · ${rows.length}`));
    const table = element('table', undefined, 'building-table');
    const head = table.createTHead().insertRow();
    for (const key of ['buildings_name', 'buildings_level', 'buildings_time', 'buildings_cost', 'buildings_target']) head.append(element('th', t(key)));
    const body = table.createTBody();
    for (const row of rows) {
      const line = body.insertRow();
      const name = line.insertCell();
      name.append(element('span', String(row.name ?? '?')));
      if (Number(row.count) > 1) name.append(element('small', ` ×${row.count}`));
      if (['in_progress', 'suggested'].includes(row.section)) name.append(element('em', t(`buildings_${row.section}`), `building-tag ${row.section}`));
      line.insertCell().textContent = Number.isInteger(row.next_level) ? `→ ${row.next_level}` : '–';
      const time = line.insertCell();
      time.textContent = typeof row.time === 'string' ? row.time : '–';
      if (Number.isInteger(row.seconds)) this.clocks.push([time, row.seconds]);
      const cost = line.insertCell();
      if (Number.isInteger(row.cost)) {
        cost.append(element('span', row.cost.toLocaleString('en-US')));
        if (COINS[row.currency]) {
          const coin = element('img');
          Object.assign(coin, { src: COINS[row.currency], alt: t(`resource_${row.currency}`), width: 16, height: 16 });
          cost.append(coin);
        }
      } else cost.textContent = '–';
      this.pickCells(row, line, name);
    }
    section.append(table);
    return section;
  }
}
