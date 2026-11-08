// Village sync: the game's JSON export supplies levels, counts and running timers for all six lists.
// Each list has an ordered queue and optional target levels. Syncing starts no upgrades.
import { t } from './i18n.js?v=3';
import { upgradePortraitSource, upgradePortraitFallback } from './upgrade-portrait.js?v=4';
import { isFullyUpgraded } from './upgrade-availability.js?v=1';

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
// The device navigates Settings and copies fresh village JSON before it answers.
export const SCAN_WAIT_MS = 180000;
const MAX_LEVEL = 200;
const MAX_KEEP = 10;
const MAX_GOBLIN_GEMS = 1000;
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
    this.baselines = {};
    this.keep = 0;
    this.search = '';
    $('upgrade-search').addEventListener('input', (event) => { this.search = event.target.value; this.renderCatalog(); });
    $('buildings-keep').addEventListener('input', () => { this.keep = Number($('buildings-keep').value); this.renderStatus(); });
    for (const id of ['goblin-builder', 'goblin-researcher', 'goblin-max']) $(id).addEventListener('input', () => { this.goblinDirty = true; });
    for (const tab of document.querySelectorAll('[data-list]')) {
      tab.addEventListener('click', () => { this.list = tab.dataset.list; this.search = ''; $('upgrade-search').value = ''; this.render(); });
    }
    for (const group of document.querySelectorAll('#view-buildings .tabs')) group.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      const tabs = [...group.querySelectorAll('button')].filter(tab => !tab.hidden && !tab.disabled);
      const current = tabs.indexOf(event.target);
      if (current < 0) return;
      event.preventDefault();
      const index = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      tabs[index].click(); tabs[index].focus();
    });
    $('buildings-scan').addEventListener('click', () => send('scan_buildings', { list: this.list },
      { wait: SCAN_WAIT_MS, label: this.text('buildings_scan') }));
    $('buildings-save').addEventListener('click', () => {
      const inputs = [...$('buildings-picked').querySelectorAll('input'), ...(this.list === 'buildings' ? [$('buildings-keep')] : [])];
      if (inputs.some((input) => !input.reportValidity())) return;
      const keep = Number.parseInt($('buildings-keep').value, 10);
      // Only buildings leave builders free; research and pets have their own queues.
      send('set_buildings', { list: this.list, buildings: this.list === 'buildings'
        ? { targets: this.targets, keep_builders: Number.isInteger(keep) ? Math.min(MAX_KEEP, Math.max(0, keep)) : 0 }
        : { targets: this.targets } });
    });
    // The only gems the bot ever pays: a Goblin hired for an upgrade, each kind only when switched on.
    $('goblin-save').addEventListener('click', () => {
      if (!$('goblin-max').reportValidity()) return;
      const gems = Number.parseInt($('goblin-max').value, 10);
      send('set_goblin', { goblin: { builder: $('goblin-builder').checked, researcher: $('goblin-researcher').checked,
        max_gems: Number.isInteger(gems) ? Math.min(MAX_GOBLIN_GEMS, Math.max(0, gems)) : 0 } });
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
    if (Array.isArray(data?.lists)) {
      for (const scan of data.lists) this.load(scan);
      return;
    }
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
      if (this.baselines[list] !== undefined && this.signature(list) !== this.baselines[list]) continue;
      const targets = Array.isArray(saved?.[list]?.targets) ? saved[list].targets : [];
      this.lists[list].targets = targets.filter((target) => target && typeof target.name === 'string').map((target) => ({
        id: typeof target.id === 'string' ? target.id : null,
        name: target.name,
        level: Number.isInteger(target.level) ? target.level : null,
      }));
      if (list === 'buildings') this.keep = Number.isInteger(saved?.buildings?.keep_builders) ? saved.buildings.keep_builders : 0;
      this.baselines[list] = this.signature(list);
    }
    $('buildings-keep').value = String(this.keep);
    // A device from before the Goblin switches has them off.
    if (!this.goblinDirty) {
      $('goblin-builder').checked = saved?.goblin?.builder === true;
      $('goblin-researcher').checked = saved?.goblin?.researcher === true;
      $('goblin-max').value = String(Number.isInteger(saved?.goblin?.max_gems) ? saved.goblin.max_gems : 0);
    }
    this.render();
  }

  /** The shown list's own wording: key for buildings, key_lab or key_pets for the others. */
  text(key) {
    return t(this.list === 'buildings' ? key : `${key}_${this.list}`);
  }

  render() {
    const village = VILLAGE[this.list];
    for (const tab of document.querySelectorAll('[data-list]')) {
      const selected = tab.dataset.list === this.list;
      tab.classList.toggle('active', selected);
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
    }
    $('buildings-title').textContent = this.text('buildings_title');
    $('buildings-hint').textContent = this.text('buildings_hint');
    $('buildings-pick-hint').textContent = this.text('buildings_pick_hint');
    $('buildings-keep').closest('label').hidden = this.list !== 'buildings';
    $('goblin').hidden = village !== 'home';
    this.renderPicked();
    $('upgrade-search').placeholder = t('upgrade_search');
    $('upgrade-workspace').setAttribute('aria-labelledby', `upgrade-tab-${this.list}`);
    $('upgrade-village').textContent = t(village === 'home' ? 'home_village' : 'builder_village');
    $('view-buildings').querySelector('.upgrade-navigation').setAttribute('aria-label', t('upgrade_categories'));
    $('buildings-scan').textContent = t(this.rows ? 'upgrade_rescan' : 'upgrade_scan');
    const scanned = $('buildings-scanned');
    scanned.hidden = !this.rows;
    if (this.rows) scanned.textContent = t('buildings_scanned_at', { time: new Date(this.scannedAt).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) });
    this.renderCatalog();
  }

  signature(list = this.list) {
    return JSON.stringify([this.lists[list].targets, list === 'buildings' ? this.keep : null]);
  }

  renderStatus() {
    $('upgrade-save-status').textContent = t(this.baselines[this.list] !== undefined && this.signature() === this.baselines[this.list] ? 'upgrade_saved' : 'upgrade_unsaved');
  }

  // Accept a successful save only when the returned queue matches this draft. Other settings replies
  // and edits made while the request was in flight must not overwrite unsaved work.
  acknowledge(saved) {
    for (const list of LISTS) {
      if (!saved?.[list]) continue;
      const signature = JSON.stringify([saved[list].targets ?? [], list === 'buildings' ? saved[list].keep_builders ?? 0 : null]);
      if (signature === this.signature(list)) this.baselines[list] = signature;
    }
    this.renderStatus();
  }

  acknowledgeGoblin(saved) {
    const settings = saved?.goblin;
    if (!settings) return;
    // The user can still edit inputs while a save is in flight. Only clear the draft when
    // the acknowledged values match the current form.
    this.goblinDirty = $('goblin-builder').checked !== (settings.builder === true)
      || $('goblin-researcher').checked !== (settings.researcher === true)
      || Number($('goblin-max').value) !== (settings.max_gems ?? 0);
  }

  renderCatalog() {
    this.clocks = [];
    const list = $('buildings-list');
    list.replaceChildren();
    const available = (this.rows ?? []).filter(row => !isFullyUpgraded(row));
    const rows = available.filter(row => String(row.name ?? '').toLocaleLowerCase().includes(this.search.trim().toLocaleLowerCase()));
    $('upgrade-count').textContent = this.rows ? t('upgrade_results', { count: rows.length }) : '';
    if (!rows.length) {
      list.append(element('p', t(!this.rows ? 'upgrade_scan_empty' : available.length ? 'upgrade_no_results' : 'buildings_empty'), 'upgrade-empty'));
      return;
    }
    for (const row of rows) list.append(this.catalogRow(row));
    this.tick();
  }

  renderPicked() {
    const list = $('buildings-picked');
    list.classList.remove('drop-at-end');
    $('upgrade-drop-hint').hidden = true;
    list.replaceChildren();
    $('upgrade-queue-count').textContent = String(this.targets.length);
    if (!this.targets.length) list.append(element('li', t('upgrade_queue_empty'), 'upgrade-empty'));
    this.targets.forEach((target, index) => {
      const item = element('li', undefined, 'upgrade-queue-item');
      const heading = element('div', undefined, 'upgrade-queue-name');
      const handle = element('button', undefined, 'upgrade-grip');
      handle.type = 'button';
      handle.setAttribute('aria-label', `${t('upgrade_reorder')}: ${target.name}`);
      handle.setAttribute('aria-describedby', 'upgrade-drag-help');
      for (let dot = 0; dot < 6; dot++) {
        const mark = element('span'); mark.setAttribute('aria-hidden', 'true'); handle.append(mark);
      }
      let portrait = this.portrait(target, true);
      const refreshPortrait = () => { const next = this.portrait(target, true); portrait.replaceWith(next); portrait = next; };
      heading.append(handle, element('span', String(index + 1), 'upgrade-rank'), portrait, element('strong', target.name));
      item.dataset.queueIndex = String(index);
      this.bindDrag(handle, item, target);
      const actions = element('div', undefined, 'upgrade-queue-actions');
      const remove = element('button', '×');
      remove.type = 'button';
      remove.setAttribute('aria-label', `${t('upgrade_remove')}: ${target.name}`);
      remove.addEventListener('click', () => { this.targets.splice(index, 1); this.render(); });
      actions.append(remove);
      heading.append(actions);
      const controls = element('div', undefined, 'upgrade-target-controls');
      const group = element('div', undefined, 'upgrade-target-mode');
      group.setAttribute('role', 'group');
      group.setAttribute('aria-label', `${t('buildings_target')}: ${target.name}`);
      const label = element('label', undefined, 'upgrade-level-label');
      const caption = element('span', t('upgrade_level'), 'upgrade-sr-only');
      const input = element('input', undefined, 'building-level');
      Object.assign(input, { type: 'number', min: 1, max: MAX_LEVEL, step: 1, required: true, value: target.level ?? '' });
      input.setAttribute('aria-label', `${t('buildings_target')}: ${target.name}`);
      input.disabled = target.level === null;
      label.hidden = target.level === null;
      input.addEventListener('input', () => { if (input.validity.valid) { target.level = Number(input.value); refreshPortrait(); this.renderStatus(); } });
      for (const [mode, text] of [['max', t('buildings_max')], ['level', t('upgrade_level')]]) {
        const button = element('button', text);
        button.type = 'button';
        button.dataset.targetMode = mode;
        button.setAttribute('aria-pressed', String((target.level === null) === (mode === 'max')));
        button.setAttribute('aria-label', `${text}: ${target.name}`);
        button.addEventListener('click', () => {
          const max = mode === 'max';
          target.level = max ? null : target.level ?? Math.min(MAX_LEVEL, Math.max(1, Number(this.rows?.find(row => sameBuilding(target, row))?.next_level) || 1));
          input.disabled = max; label.hidden = max;
          input.value = target.level ?? '';
          refreshPortrait();
          for (const option of group.querySelectorAll('button')) option.setAttribute('aria-pressed', String(option.dataset.targetMode === mode));
          if (!max) { input.focus(); input.select(); }
          this.renderStatus();
        });
        group.append(button);
      }
      label.append(caption, input);
      controls.append(group, label);
      item.append(heading, controls);
      list.append(item);
    });
    this.renderStatus();
  }

  moveTarget(from, to) {
    if (from === to || from < 0 || to < 0 || to >= this.targets.length) return;
    const [target] = this.targets.splice(from, 1);
    this.targets.splice(to, 0, target);
    this.render();
    this.focusGrip(to);
    $('upgrade-reorder-status').textContent = t('upgrade_moved', { name: target.name, position: to + 1, total: this.targets.length });
  }

  focusGrip(index) {
    $('buildings-picked').querySelectorAll('.upgrade-grip')[index]?.focus({ preventScroll: true });
  }

  bindDrag(handle, item, target) {
    let drag = null;
    handle.addEventListener('keydown', event => {
      if (drag && event.key === 'Escape') { event.preventDefault(); finish({ pointerId: drag.pointer }, true); return; }
      if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const from = this.targets.indexOf(target);
      const to = event.key === 'Home' ? 0 : event.key === 'End' ? this.targets.length - 1 : from + (event.key === 'ArrowUp' ? -1 : 1);
      this.moveTarget(from, to);
    });
    handle.addEventListener('pointerdown', event => {
      if (handle.disabled || event.isPrimary === false || event.button !== 0) return;
      event.preventDefault();
      handle.focus({ preventScroll: true });
      handle.setPointerCapture(event.pointerId);
      drag = { pointer: event.pointerId, targets: [...this.targets], list: this.list, startY: event.clientY, startScroll: item.ownerDocument.defaultView.scrollY, y: event.clientY, moved: false, before: null };
      $('upgrade-drop-hint').hidden = false;
      item.classList.add('is-dragging');
      handle.classList.add('is-dragging');
    });
    const view = item.ownerDocument.defaultView;
    let frame = null;
    const update = y => {
      if (!drag || !item.isConnected) return;
      const list = $('buildings-picked');
      const rows = [...list.querySelectorAll('.upgrade-queue-item')].filter(row => row !== item);
      const before = rows.find(row => {
        const rect = row.getBoundingClientRect();
        return y < rect.top + rect.height / 2;
      });
      drag.before = before ? drag.targets[Number(before.dataset.queueIndex)] : null;
      item.style.transform = `translateY(${y - drag.startY + view.scrollY - drag.startScroll}px)`;
      for (const row of rows) row.classList.toggle('drop-before', row === before);
      list.classList.toggle('drop-at-end', !before);
    };
    const scroll = () => {
      frame = null;
      if (!drag || !drag.moved || !item.isConnected) return;
      const height = view.innerHeight;
      const y = drag.y;
      const margin = Math.min(80, height / 4);
      const speed = y < margin ? -Math.ceil((margin - y) / 6) : y > height - margin ? Math.ceil((y - height + margin) / 6) : 0;
      if (speed) {
        const previous = view.scrollY;
        view.scrollBy(0, Math.max(-18, Math.min(18, speed)));
        if (previous !== view.scrollY) update(y);
      }
      frame = view.requestAnimationFrame(scroll);
    };
    handle.addEventListener('pointermove', event => {
      if (!drag || drag.pointer !== event.pointerId) return;
      event.preventDefault();
      drag.y = event.clientY;
      if (!drag.moved && Math.abs(event.clientY - drag.startY) < 4) return;
      drag.moved = true;
      update(event.clientY);
      if (frame === null && view.requestAnimationFrame && item.ownerDocument.documentElement.scrollHeight > view.innerHeight) frame = view.requestAnimationFrame(scroll);
    });
    const finish = (event, cancelled) => {
      if (!drag || drag.pointer !== event.pointerId) return;
      const current = drag;
      drag = null;
      if (frame !== null) { view.cancelAnimationFrame(frame); frame = null; }
      if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
      if (this.list !== current.list) return;
      if (cancelled) { this.render(); this.focusGrip(this.targets.indexOf(target)); return; }
      const order = [...current.targets];
      if (current.moved) {
        order.splice(order.indexOf(target), 1);
        order.splice(current.before ? order.indexOf(current.before) : order.length, 0, target);
      }
      this.targets = order;
      this.render();
      const position = this.targets.indexOf(target);
      this.focusGrip(position);
      $('upgrade-reorder-status').textContent = t('upgrade_moved', { name: target.name, position: position + 1, total: order.length });
      $('buildings-picked').dispatchEvent(new Event('change', { bubbles: true }));
    };
    handle.addEventListener('pointerup', event => finish(event, false));
    handle.addEventListener('pointercancel', event => finish(event, true));
    handle.addEventListener('lostpointercapture', event => finish(event, true));
  }

  toggle(row, picked) {
    this.targets = this.targets.filter((target) => !sameBuilding(target, row));
    if (picked) this.targets.push({ id: typeof row.id === 'string' ? row.id : null, name: String(row.name), level: null });
    this.render();
  }

  portrait(row, compact = false) {
    const portrait = element('span', undefined, `upgrade-portrait${compact ? ' compact' : ''}`);
    portrait.setAttribute('aria-hidden', 'true');
    const name = String(row.name ?? '');
    const source = upgradePortraitSource(this.list, row, compact, this.rows ?? []);
    const initials = element('span', name.trim().split(/\s+/).slice(0, 2).map(word => word[0]).join('').toUpperCase() || '?', 'upgrade-portrait-fallback');
    const fallback = () => {
      const image = element('img');
      Object.assign(image, { src: upgradePortraitFallback(this.list, row), alt: '', width: compact ? 28 : 44,
        height: compact ? 28 : 44, loading: 'lazy', decoding: 'async' });
      image.dataset.generic = 'true';
      image.addEventListener('error', () => image.replaceWith(initials), { once: true });
      return image;
    };
    if (!source) { portrait.append(fallback()); return portrait; }
    const image = element('img');
    Object.assign(image, { src: source, alt: '', width: compact ? 28 : 44, height: compact ? 28 : 44, loading: 'lazy', decoding: 'async' });
    image.addEventListener('error', () => image.replaceWith(fallback()), { once: true });
    portrait.append(image);
    return portrait;
  }

  catalogRow(row) {
    const item = element('article', undefined, 'upgrade-row');
    const info = element('div', undefined, 'upgrade-row-info');
    const name = element('strong', String(row.name ?? '?'));
    if (Number(row.count) > 1) name.append(element('small', ` ×${row.count}`));
    const title = element('div', undefined, 'upgrade-row-title');
    title.append(name);
    if (['in_progress', 'suggested', 'equipped', 'locked'].includes(row.section)) title.append(element('span', t(`upgrade_status_${row.section}`), `upgrade-status ${row.section}`));
    info.append(title);
    const meta = element('div', undefined, 'upgrade-meta');
    if (Number.isInteger(row.current_level)) meta.append(element('span', t('upgrade_current_level', { level: row.current_level })));
    if (Number.isInteger(row.next_level) && (row.source !== 'game_export' || row.section === 'in_progress')) meta.append(element('span', t('upgrade_to_level', { level: row.next_level })));
    const time = element('span', typeof row.time === 'string' ? row.time : this.list === 'gear' && row.section !== 'locked' ? t('buildings_instant') : '');
    if (Number.isInteger(row.seconds)) this.clocks.push([time, row.seconds]);
    if (time.textContent || Number.isInteger(row.seconds)) meta.append(time);
    if (meta.childElementCount) info.append(meta);
    const details = element('div', undefined, 'upgrade-row-details');
    const coins = COINS[VILLAGE[this.list]];
    const prices = row.costs && typeof row.costs === 'object'
      ? ORES.filter(ore => ore in row.costs).map(ore => [row.costs[ore], `icons/resources/ore_${ore}.png`, t(`buildings_ore_${ore}`)])
      : Number.isInteger(row.cost) ? [[row.cost, coins[row.currency], t(`resource_${row.currency}`)]] : [];
    for (const [amount, icon, label] of prices) {
      const price = element('span', Number.isInteger(amount) ? amount.toLocaleString('en-US') : '?', 'building-price');
      if (icon) { const image = element('img'); Object.assign(image, { src: icon, alt: label, width: 18, height: 18 }); price.prepend(image); }
      details.append(price);
    }
    const picked = this.targets.some(target => sameBuilding(target, row));
    const button = element('button', t(picked ? 'upgrade_remove' : 'upgrade_add'), picked ? 'upgrade-add danger' : 'upgrade-add');
    button.type = 'button';
    if (row.selectable === false && !picked) {
      button.disabled = true;
      button.textContent = t(row.id === 'wall' ? 'upgrade_wall_separate' : 'upgrade_export_readonly');
    }
    button.setAttribute('aria-pressed', String(picked));
    button.setAttribute('aria-label', `${t(picked ? 'upgrade_remove' : 'upgrade_add')}: ${row.name}`);
    button.addEventListener('click', () => this.toggle(row, !picked));
    const summary = element('div', undefined, 'upgrade-row-summary');
    summary.append(this.portrait(row), info);
    item.append(summary, details, button);
    return item;
  }
}
