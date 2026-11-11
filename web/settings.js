// Device settings: the loot minimums the Home Village search skips opponents by, and where LDPlayer is on the
// device. Auto-detect only fills the form with what the device found; nothing changes until save is pressed.
import { t } from './i18n.js?v=5';
import { translate } from './messages.js?v=5';

const MINIMUM = ['gold', 'elixir', 'dark'];
const SETUP = ['adb', 'ldconsole', 'serial', 'ld_index', 'touch_device'];
const $ = (id) => document.getElementById(id);

/** @param {HTMLFormElement} form @param {Record<string, unknown> | undefined} values @param {string[]} names */
function fill(form, values, names) {
  if (!values || typeof values !== 'object') return;
  for (const name of names) {
    const value = values[name];
    if (typeof value === 'string' || typeof value === 'number') form.elements.namedItem(name).value = String(value);
  }
}

/** Group whole-number loot amounts without moving the caret past the digit being edited. */
function formatLoot(input) {
  const original = input.value;
  const raw = original.replaceAll(',', '');
  const valid = raw === '' || (/^\d+$/.test(raw) && Number(raw) <= 999999999);
  input.setCustomValidity(valid ? '' : t('minimum_invalid'));
  if (!valid || !raw) return;
  const digitsBeforeCaret = original.slice(0, input.selectionStart ?? original.length).replaceAll(',', '').length;
  const formatted = raw.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  input.value = formatted;
  if (document.activeElement === input) {
    let caret = 0, digits = 0;
    while (caret < formatted.length && digits < digitsBeforeCaret) {
      if (formatted[caret] !== ',') digits++;
      caret++;
    }
    input.setSelectionRange(caret, caret);
  }
}

export class SettingsPanel {
  /** @param {(action: string, args?: Record<string, unknown>) => void} send */
  constructor(send) {
    this.send = send;
    this.checked = null;
    this.isBusy = false;
    this.found = null;
    this.display = null;
    for (const name of MINIMUM) {
      const input = $('minimum-form').elements.namedItem(name);
      input.addEventListener('input', () => formatLoot(input));
    }
    $('settings-load').addEventListener('click', () => send('get_settings'));
    $('setup-check').addEventListener('click', () => { this.invalidate(); send('check_setup'); });
    $('setup-form').addEventListener('input', () => this.invalidate());
    $('setup-form').addEventListener('invalid', () => {
      const advanced = document.querySelector('.setup-advanced');
      if (advanced) advanced.open = true;
    }, true);
    $('setup-detect').addEventListener('click', () => send('detect_setup'));
    $('minimum-form').addEventListener('submit', (submitted) => {
      submitted.preventDefault();
      send('set_minimum', { minimum: this.minimum() });
    });
    $('setup-form').addEventListener('submit', (submitted) => {
      submitted.preventDefault();
      send('set_setup', { setup: this.setup() });
    });
  }

  /** @returns {Record<string, number>} whole numbers; anything else is refused by the device */
  minimum() {
    const form = $('minimum-form').elements;
    return Object.fromEntries(MINIMUM.map((name) => [name, Number(form.namedItem(name).value.replaceAll(',', '') || '0')]));
  }

  setup() {
    const form = $('setup-form').elements;
    const setup = Object.fromEntries(SETUP.map((name) => [name, form.namedItem(name).value.trim()]));
    return { ...setup, ld_index: Number.parseInt(setup.ld_index || '0', 10) };
  }

  /** @param {{minimum?: Record<string, number>, setup?: Record<string, unknown>}} data the device's settings now */
  load(data) {
    if (!data || typeof data !== 'object') return;
    fill($('minimum-form'), data.minimum, MINIMUM);
    for (const name of MINIMUM) formatLoot($('minimum-form').elements.namedItem(name));
    fill($('setup-form'), data.setup, SETUP);
    this.showDisplay(data.display);
    this.invalidate();
  }

  /** @param {{current: Record<string, number>, target: Record<string, number>, saved: boolean} | null} display */
  showDisplay(display) {
    this.display = display && typeof display === 'object' && display.current && display.target ? display : null;
    this.renderDisplay();
    this.invalidate();
  }

  displayMatches() {
    const display = this.display;
    return Boolean(display && ['width', 'height', 'dpi'].every((name) => display.current[name] === display.target[name]));
  }

  renderDisplay() {
    const display = this.display;
    const text = (screen) => `${screen.width}x${screen.height} DPI ${screen.dpi}`;
    $('display-now').textContent = display ? text(display.current) : '-';
    $('display-target').textContent = display ? text(display.target) : '-';
    const fits = this.displayMatches();
    const note = $('display-note');
    if (!display) note.textContent = t('display_unknown');
    else if (!fits) note.textContent = t('display_differs');
    else note.textContent = t(display.saved ? 'display_saved' : 'display_fits');
    note.className = `hint ${fits ? 'ok' : 'problem'}`;
  }

  /** @param {Record<string, unknown>} found what auto-detect found on the device, with its problems */
  detected(found) {
    if (!found || typeof found !== 'object') return;
    const form = $('setup-form');
    for (const name of SETUP) form.elements.namedItem(name).value = '';
    fill(form, found, SETUP);
    this.found = found;
    this.invalidate();
    this.renderInstances();
    this.render();
  }

  invalidate() {
    this.checked = null;
    this.refreshButtons();
    $('setup-check-result').textContent = t('setup_check_hint');
  }

  busy(value) {
    this.isBusy = value;
    this.refreshButtons();
  }

  refreshButtons() {
    $('setup-continue').disabled = this.isBusy || this.checked !== true;
    const complete = SETUP.every(name => $('setup-form').elements.namedItem(name).value.trim() !== '');
    $('setup-save').disabled = this.isBusy || !complete;
  }

  checkedSetup(data) {
    this.checked = data?.ready === true;
    this.refreshButtons();
    $('setup-check-result').textContent = t(this.checked ? 'setup_check_ready' : 'setup_check_failed');
    $('setup-check-result').className = `hint ${this.checked ? 'ok' : 'problem'}`;
  }

  renderInstances() {
    const group = $('setup-instances');
    group.replaceChildren();
    for (const instance of this.found?.instances || []) {
      const button = document.createElement('button');
      button.type = 'button'; button.setAttribute('role', 'radio');
      const selected = instance.index === this.found.ld_index;
      button.setAttribute('aria-checked', String(selected));
      button.tabIndex = selected ? 0 : -1;
      button.textContent = `${instance.name} · ${t(instance.running ? 'setup_on' : 'setup_off')}`;
      button.addEventListener('click', () => this.send('detect_setup', {index: instance.index}));
      button.addEventListener('keydown', event => {
        const choices = [...group.children];
        const current = choices.indexOf(button);
        let next;
        if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = choices[(current + 1) % choices.length];
        if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = choices[(current - 1 + choices.length) % choices.length];
        if (event.key === 'Home') next = choices[0];
        if (event.key === 'End') next = choices.at(-1);
        if (next) { event.preventDefault(); next.focus(); next.click(); }
      });
      group.append(button);
    }
  }

  render() {
    this.renderDisplay();
    const notes = $('setup-notes');
    notes.textContent = '';
    const diagnostics = $('setup-diagnostics');
    diagnostics.textContent = '';
    if (!this.found) return;
    const add = (text, kind, target = notes) => {
      const note = document.createElement('li');
      note.textContent = text;
      note.className = kind;
      target.append(note);
    };
    const list = (value) => (Array.isArray(value) ? value : []);
    for (const folder of list(this.found.folders)) add(`${t('setup_folder')}: ${folder}`, 'info', diagnostics);
    for (const instance of list(this.found.instances)) {
      add(`${t('setup_instance')} ${instance.index} "${instance.name}": ${t(instance.running ? 'setup_on' : 'setup_off')}`, 'info', diagnostics);
    }
    if (Array.isArray(this.found.resolution)) add(`${t('setup_resolution')}: ${this.found.resolution.join('x')}`, 'info');
    const problems = list(this.found.problems);
    for (const problem of problems) add(translate(String(problem)), 'problem');
    if (!problems.length) add(t('setup_all_found'), 'ok');
  }
}
