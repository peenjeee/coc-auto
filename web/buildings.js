// Building scan: every row of the game's builder list ("Buildings & Heroes") read on the device, grouped by
// category. Shown only; nothing is upgraded or bought from here.
import { t } from './i18n.js';

const CATEGORIES = ['defense', 'army', 'resource', 'trap', 'hero', 'other'];
const COINS = { gold: 'icons/resources/gold.png', elixir: 'icons/resources/elixir.png', dark: 'icons/resources/dark_elixir.png' };
// The device opens, swipes and reads the whole list before it answers, which takes about a minute.
export const SCAN_WAIT_MS = 180000;
const $ = (id) => document.getElementById(id);

/** @param {string} tag @param {string} [text] @param {string} [className] */
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

export class BuildingsPanel {
  /** @param {(action: string, args?: Record<string, unknown>, options?: Record<string, unknown>) => void} send */
  constructor(send) {
    /** @type {Array<Record<string, unknown>> | null} */
    this.rows = null;
    $('buildings-scan').addEventListener('click', () => send('scan_buildings', {}, { wait: SCAN_WAIT_MS }));
  }

  /** @param {{rows?: unknown}} data the rows the device read, top of the list first */
  load(data) {
    this.rows = Array.isArray(data?.rows) ? data.rows.filter((row) => row && typeof row === 'object') : [];
    this.render();
  }

  render() {
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

  group(category, rows) {
    const section = element('section', undefined, 'building-group');
    section.append(element('h4', `${t(`buildings_${category}`)} · ${rows.length}`));
    const table = element('table', undefined, 'building-table');
    const head = table.createTHead().insertRow();
    for (const key of ['buildings_name', 'buildings_level', 'buildings_time', 'buildings_cost']) head.append(element('th', t(key)));
    const body = table.createTBody();
    for (const row of rows) {
      const line = body.insertRow();
      const name = line.insertCell();
      name.append(element('span', String(row.name ?? '?')));
      if (Number(row.count) > 1) name.append(element('small', ` ×${row.count}`));
      if (['in_progress', 'suggested'].includes(row.section)) name.append(element('em', t(`buildings_${row.section}`), `building-tag ${row.section}`));
      line.insertCell().textContent = Number.isInteger(row.next_level) ? `→ ${row.next_level}` : '–';
      line.insertCell().textContent = typeof row.time === 'string' ? row.time : '–';
      const cost = line.insertCell();
      if (Number.isInteger(row.cost)) {
        cost.append(element('span', row.cost.toLocaleString('en-US')));
        if (COINS[row.currency]) {
          const coin = element('img');
          Object.assign(coin, { src: COINS[row.currency], alt: t(`resource_${row.currency}`), width: 16, height: 16 });
          cost.append(coin);
        }
      } else cost.textContent = '–';
    }
    section.append(table);
    return section;
  }
}
