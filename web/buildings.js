// Building scan: every row of one of the game's builder list tabs (in the Home Village buildings, Laboratory, pets
// and hero equipment; in the Builder Base buildings and Star Laboratory) read on the device, grouped by category.
// Rows are ticked here, first ticked first, each up to a level or Max, a running upgrade too: the Home Village lists
// for the "Upgrade buildings" task, the Builder Base lists only kept for now. The scan itself upgrades nothing.
import { t } from './i18n.js';

// Each list's categories, in the order shown; a row the device could not name comes under other.
const CATEGORIES = {
  buildings: ['defense', 'army', 'resource', 'trap', 'hero', 'other'],
  lab: ['troop', 'dark_troop', 'spell', 'dark_spell', 'siege', 'other'],
  pets: ['pet', 'other'],
  gear: ['equipment', 'other'],
  builder: ['defense', 'army', 'resource', 'trap', 'hero', 'other'],
  star: ['troop', 'other'],
};
export const LISTS = Object.keys(CATEGORIES);
// The village whose builder list has each list; the first list of a village opens when it is chosen.
const VILLAGE = { buildings: 'home', lab: 'home', pets: 'home', gear: 'home', builder: 'builder', star: 'builder' };
const COINS = {
  home: { gold: 'icons/resources/gold.png', elixir: 'icons/resources/elixir.png', dark: 'icons/resources/dark_elixir.png' },
  builder: { gold: 'icons/resources/builder_gold.webp', elixir: 'icons/resources/builder_elixir.webp' },
};
const ORES = ['shiny', 'glowy', 'starry'];
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
    /** @type {Record<string, {rows: Array<Record<string, unknown>> | null, scannedAt: number, targets: Array<{id: string | null, name: string, level: number | null}>}>}
     * per list: the rows read (null while the device keeps no scan of it), when, and the pick, first one first */
    this.lists = Object.fromEntries(LISTS.map((list) => [list, { rows: null, scannedAt: Date.now(), targets: [] }]));
    this.list = 'buildings';
    for (const tab of document.querySelectorAll('[data-list]')) {
      tab.addEventListener('click', () => { this.list = tab.dataset.list; this.render(); });
    }
    for (const tab of document.querySelectorAll('[data-village]')) {
      tab.addEventListener('click', () => { this.list = LISTS.find((list) => VILLAGE[list] === tab.dataset.village); this.render(); });
    }
    $('buildings-scan').addEventListener('click', () => send('scan_buildings', { list: this.list },
      { wait: SCAN_WAIT_MS, label: this.text('buildings_scan') }));
    $('buildings-save').addEventListener('click', () => {
      const keep = Number.parseInt($('buildings-keep').value, 10);
      // Only buildings leave builders free; research and pets have their own queues.
      send('set_buildings', { list: this.list, buildings: this.list === 'buildings'
        ? { targets: this.targets, keep_builders: Number.isInteger(keep) ? Math.min(MAX_KEEP, Math.max(0, keep)) : 0 }
        : { targets: this.targets } });
    });
  }

  /** The list shown: its rows (null until a scan of it is known), when they were read and its pick. */
  get rows() { return this.lists[this.list].rows; }
  get scannedAt() { return this.lists[this.list].scannedAt; }
  get targets() { return this.lists[this.list].targets; }
  set targets(targets) { this.lists[this.list].targets = targets; }

  /** @param {{list?: unknown, rows?: unknown, scanned_at?: unknown}} data the rows the device read from one list, top
   * first, and when (unix seconds); rows null when the device keeps no scan of it */
  load(data) {
    // A device from before the lab and pet lists answers for the buildings only.
    const kept = this.lists[LISTS.includes(data?.list) ? data.list : 'buildings'];
    kept.rows = Array.isArray(data?.rows) ? data.rows.filter((row) => row && typeof row === 'object') : null;
    // Running upgrades count down from the scan itself, so a scan kept on the device and reopened later shows the
    // time left now; the next scan reads the game's own timers again.
    kept.scannedAt = Number.isFinite(data?.scanned_at) ? data.scanned_at * 1000 : Date.now();
    this.ticker ??= setInterval(() => this.tick(), 30000);
    this.render();
  }

  tick() {
    const passed = (Date.now() - this.scannedAt) / 1000;
    for (const [cell, total] of this.clocks ?? []) cell.textContent = left(Math.max(0, total - passed));
  }

  /** @param {Record<string, {targets?: unknown, keep_builders?: unknown} | undefined> | undefined} saved the
   * settings the device keeps, each list's pick under its name */
  select(saved) {
    for (const list of LISTS) {
      const targets = Array.isArray(saved?.[list]?.targets) ? saved[list].targets : [];
      this.lists[list].targets = targets.filter((target) => target && typeof target.name === 'string').map((target) => ({
        id: typeof target.id === 'string' ? target.id : null,
        name: target.name,
        level: Number.isInteger(target.level) ? target.level : null,
      }));
    }
    $('buildings-keep').value = String(Number.isInteger(saved?.buildings?.keep_builders) ? saved.buildings.keep_builders : 0);
    this.render();
  }

  /** The shown list's own wording: key for buildings, key_lab or key_pets for the others. */
  text(key) {
    return t(this.list === 'buildings' ? key : `${key}_${this.list}`);
  }

  render() {
    const village = VILLAGE[this.list];
    for (const tab of document.querySelectorAll('[data-list], [data-village]')) {
      const selected = tab.dataset.list ? tab.dataset.list === this.list : tab.dataset.village === village;
      tab.classList.toggle('active', selected);
      tab.setAttribute('aria-selected', String(selected));
      if (tab.dataset.list) tab.hidden = VILLAGE[tab.dataset.list] !== village;
    }
    $('buildings-title').textContent = this.text('buildings_title');
    $('buildings-hint').textContent = this.text('buildings_hint');
    $('buildings-pick-hint').textContent = this.text('buildings_pick_hint');
    $('buildings-keep').closest('label').hidden = this.list !== 'buildings';
    this.renderPicked();
    this.clocks = [];
    $('buildings-scan').textContent = this.rows ? t('buildings_rescan') : this.text('buildings_scan');
    const scanned = $('buildings-scanned');
    scanned.hidden = !this.rows;
    if (this.rows) scanned.textContent = t('buildings_scanned_at', { time: new Date(this.scannedAt).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) });
    const list = $('buildings-list');
    list.textContent = '';
    if (!this.rows) return;
    if (!this.rows.length) {
      list.append(element('p', t('buildings_empty'), 'hint'));
      return;
    }
    const categories = CATEGORIES[this.list];
    for (const category of categories) {
      const rows = this.rows.filter((row) => (categories.includes(row.category) ? row.category : 'other') === category);
      if (rows.length) list.append(this.group(category, rows));
    }
    this.tick();
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

  /** The tick and target level of a row; a running upgrade can be ticked too, to go on once it is done. */
  pickCells(row, line, name) {
    const target = this.targets.find((candidate) => sameBuilding(candidate, row));
    const goal = line.insertCell();
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
    head.append(element('th', this.text('buildings_name')));
    for (const key of ['buildings_level', 'buildings_time', 'buildings_cost', 'buildings_target']) head.append(element('th', t(key)));
    const body = table.createTBody();
    for (const row of rows) {
      const line = body.insertRow();
      const name = line.insertCell();
      name.append(element('span', String(row.name ?? '?')));
      if (Number(row.count) > 1) name.append(element('small', ` ×${row.count}`));
      if (['in_progress', 'suggested', 'equipped'].includes(row.section)) name.append(element('em', t(`buildings_${row.section}`), `building-tag ${row.section}`));
      line.insertCell().textContent = Number.isInteger(row.next_level) ? `→ ${row.next_level}` : '–';
      const time = line.insertCell();
      // Hero equipment is upgraded at once; it has no timer to read.
      time.textContent = typeof row.time === 'string' ? row.time : this.list === 'gear' ? t('buildings_instant') : '–';
      if (Number.isInteger(row.seconds)) this.clocks.push([time, row.seconds]);
      const cost = line.insertCell();
      const coins = COINS[VILLAGE[this.list]];
      // Hero equipment costs up to three ores, each on its own line; an amount not read shows as '?'.
      const prices = row.costs && typeof row.costs === 'object'
        ? ORES.filter((ore) => ore in row.costs).map((ore) => [row.costs[ore], `icons/resources/ore_${ore}.png`, t(`buildings_ore_${ore}`)])
        : Number.isInteger(row.cost) ? [[row.cost, coins[row.currency], coins[row.currency] && t(`resource_${row.currency}`)]] : [];
      for (const [amount, icon, label] of prices) {
        const price = element('span', Number.isInteger(amount) ? amount.toLocaleString('en-US') : '?', 'building-price');
        if (icon) {
          const coin = element('img');
          Object.assign(coin, { src: icon, alt: label, width: 16, height: 16 });
          price.append(coin);
        }
        cost.append(price);
      }
      if (!prices.length) cost.textContent = '–';
      this.pickCells(row, line, name);
    }
    section.append(table);
    return section;
  }
}
