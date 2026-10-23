// Device settings: the loot minimums the Home Village search skips opponents by, and where LDPlayer is on the
// device. Auto-detect only fills the form with what the device found; nothing changes until save is pressed.
import { t } from './i18n.js';

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

export class SettingsPanel {
  /** @param {(action: string, args?: Record<string, unknown>) => void} send */
  constructor(send) {
    this.found = null;
    this.display = null;
    this.language = null;
    $('settings-load').addEventListener('click', () => send('get_settings'));
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
    return Object.fromEntries(MINIMUM.map((name) => [name, Number.parseInt(form.namedItem(name).value || '0', 10)]));
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
    fill($('setup-form'), data.setup, SETUP);
    this.showDisplay(data.display);
    this.showLanguage(data.language);
  }

  /** @param {{current: Record<string, number>, target: Record<string, number>, saved: boolean} | null} display */
  showDisplay(display) {
    this.display = display && typeof display === 'object' && display.current && display.target ? display : null;
    this.renderDisplay();
  }

  renderDisplay() {
    const display = this.display;
    const text = (screen) => `${screen.width}x${screen.height} DPI ${screen.dpi}`;
    $('display-now').textContent = display ? text(display.current) : '-';
    $('display-target').textContent = display ? text(display.target) : '-';
    const fits = display && ['width', 'height', 'dpi'].every((name) => display.current[name] === display.target[name]);
    const note = $('display-note');
    if (!display) note.textContent = t('display_unknown');
    else if (!fits) note.textContent = t('display_differs');
    else note.textContent = t(display.saved ? 'display_saved' : 'display_fits');
    note.className = `hint ${fits ? 'ok' : 'problem'}`;
  }

  /** @param {{current: string, target: string, saved: boolean} | null} language CoC's language on the device */
  showLanguage(language) {
    this.language = language && typeof language === 'object' && typeof language.current === 'string' ? language : null;
    this.renderLanguage();
  }

  renderLanguage() {
    const language = this.language;
    const indonesian = Boolean(language) && ['id', 'in'].includes(language.current.toLowerCase().split(/[-_]/)[0]);
    $('language-now').textContent = language ? language.current : '-';
    $('language-target').textContent = language ? language.target : '-';
    const note = $('language-note');
    if (!language) note.textContent = t('language_unknown');
    else if (!indonesian) note.textContent = t('language_differs');
    else note.textContent = t(language.saved ? 'language_saved' : 'language_fits');
    note.className = `hint ${indonesian ? 'ok' : 'problem'}`;
  }

  /** @param {Record<string, unknown>} found what auto-detect found on the device, with its problems */
  detected(found) {
    if (!found || typeof found !== 'object') return;
    fill($('setup-form'), found, SETUP);
    this.found = found;
    this.render();
  }

  render() {
    this.renderDisplay();
    this.renderLanguage();
    const notes = $('setup-notes');
    notes.textContent = '';
    if (!this.found) return;
    const add = (text, kind) => {
      const note = document.createElement('li');
      note.textContent = text;
      note.className = kind;
      notes.append(note);
    };
    const list = (value) => (Array.isArray(value) ? value : []);
    for (const folder of list(this.found.folders)) add(`${t('setup_folder')}: ${folder}`, 'info');
    for (const instance of list(this.found.instances)) {
      add(`${t('setup_instance')} ${instance.index} "${instance.name}": ${t(instance.running ? 'setup_on' : 'setup_off')}`, 'info');
    }
    if (Array.isArray(this.found.resolution)) add(`${t('setup_resolution')}: ${this.found.resolution.join('x')}`, 'info');
    const problems = list(this.found.problems);
    for (const problem of problems) add(String(problem), 'problem');
    if (!problems.length) add(t('setup_all_found'), 'ok');
  }
}
