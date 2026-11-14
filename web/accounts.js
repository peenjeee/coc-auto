// Accounts: the Supercell ID accounts of this device, one card each. The list is the device's own
// (config.local.json 'accounts'); the page shows it and sends it back whole after every change, and the device
// checks it again. Loading an account from here is the same checked switch a run makes.
import { t } from './i18n.js?v=5';

const $ = (id) => document.getElementById(id);
// A switch reads the game's export twice and reloads the game in between.
const SWITCH_WAIT_MS = 240000;
// The bot's own lines: an account taking its turn, and the village the game has loaded.
const TURN = /^INFO: (?:Giliran akun .+|Account .+ takes its turn) \((#[0289PYLQGRJCUV]+)\)$/;
const LOADED = /^INFO: (?:Akun .+ \((#[0289PYLQGRJCUV]+)\) (?:sudah )?dimuat|Account .+ \((#[0289PYLQGRJCUV]+)\) (?:is already )?loaded)$/;

/** @typedef {{name: string, tag: string, enabled: boolean}} Account */

export class AccountsPanel {
  /**
   * @param {(action: string, args?: Record<string, unknown>, options?: Record<string, unknown>) => void} send
   * @param {() => void} changed called when the accounts that take turns may have changed
   * @param {(tag: string) => void} [loaded] called when the bot's log says the game loaded that village
   */
  constructor(send, changed, loaded = () => {}) {
    this.send = send;
    this.changed = changed;
    this.loaded = loaded;
    /** @type {Account[]} */
    this.accounts = [];
    /** @type {string | null} the village the game has loaded, as far as the device knows */
    this.current = null;
    /** @type {string | null} the account whose turn a running bot is playing */
    this.playing = null;
    /** @type {Record<string, number>} upgrade targets picked per player tag */
    this.targets = {};
    this.isBusy = false;
    this.isRunning = false;
    this.adding = false;
    $('accounts-load').addEventListener('click', () => send('get_settings'));
    $('account-form').addEventListener('submit', (submitted) => {
      submitted.preventDefault();
      const form = $('account-form').elements;
      const tag = form.namedItem('tag').value.trim().toUpperCase();
      this.adding = true;
      this.save([...this.accounts, {
        name: form.namedItem('name').value.trim(), tag: tag.startsWith('#') ? tag : `#${tag}`, enabled: true,
      }]);
    });
  }

  /** @returns {Account[]} the accounts a run works through, in turn order */
  enabled() {
    return this.accounts.filter((account) => account.enabled);
  }

  /** @param {{accounts?: Account[], account?: string | null, account_targets?: Record<string, number>}} data */
  load(data) {
    if (!data || !Array.isArray(data.accounts)) return;
    // What was typed stays in the form until the device has taken it.
    if (this.adding && data.accounts.length > this.accounts.length) $('account-form').reset();
    this.adding = false;
    this.accounts = data.accounts;
    this.current = typeof data.account === 'string' ? data.account : null;
    this.targets = data.account_targets && typeof data.account_targets === 'object' ? data.account_targets : {};
    this.render();
    this.changed();
  }

  /** @param {string} line a line of the bot's log, as the device wrote it */
  seen(line) {
    const turn = TURN.exec(line);
    const loaded = LOADED.exec(line);
    if (turn) this.playing = turn[1];
    if (loaded) {
      this.current = loaded[1] ?? loaded[2];
      this.loaded(this.current);
    }
    if (turn || loaded) this.render();
  }

  /** @param {boolean} running whether the bot is running a task now */
  running(running) {
    if (running === this.isRunning) return;
    this.isRunning = running;
    if (!running) this.playing = null;
    this.render();
  }

  /** @param {Account[]} accounts */
  save(accounts) {
    this.send('set_accounts', { accounts });
  }

  busy(value) {
    this.isBusy = value;
    for (const control of $('account-list').querySelectorAll('button, input')) {
      control.disabled = value || control.dataset.locked === 'true';
    }
    $('account-form').querySelector('button').disabled = value;
  }

  /** @param {number} from @param {number} to */
  move(from, to) {
    const next = [...this.accounts];
    next.splice(to, 0, ...next.splice(from, 1));
    this.save(next);
  }

  render() {
    const list = $('account-list');
    list.replaceChildren();
    $('accounts-empty').hidden = this.accounts.length > 0;
    $('accounts-running').hidden = !this.isRunning || !this.accounts.length;
    this.accounts.forEach((account, index) => {
      const loaded = account.tag === this.current;
      const playing = this.isRunning && account.tag === this.playing;
      const card = document.createElement('article');
      card.className = `group account-card${loaded ? ' loaded' : ''}${playing ? ' playing' : ''}`;
      const head = document.createElement('div');
      head.className = 'account-head';
      const name = document.createElement('h3');
      name.textContent = account.name;
      head.append(name);
      const badge = (key, kind) => {
        const made = document.createElement('span');
        made.className = `account-badge ${kind}`;
        made.textContent = t(key);
        head.append(made);
      };
      if (playing) badge('account_playing', 'playing');
      if (loaded) badge('account_loaded', 'loaded');
      if (!account.enabled) badge('account_skipped', 'skipped');
      const tag = document.createElement('p');
      tag.className = 'account-tag';
      tag.textContent = account.tag;
      const count = Number(this.targets[account.tag]) || 0;
      const picks = document.createElement('p');
      picks.className = 'hint';
      picks.textContent = count ? t('account_targets', { count }) : t('account_no_targets');
      const turns = document.createElement('div');
      turns.className = 'checks';
      const label = document.createElement('label');
      const toggle = document.createElement('input');
      toggle.type = 'checkbox';
      toggle.checked = account.enabled;
      toggle.disabled = this.isBusy;
      toggle.addEventListener('change', () => this.save(this.accounts.map(
        (other, at) => (at === index ? { ...other, enabled: toggle.checked } : other))));
      const text = document.createElement('span');
      text.textContent = t('account_enabled');
      label.append(toggle, text);
      turns.append(label);
      const actions = document.createElement('div');
      actions.className = 'actions';
      const button = (key, className, action, locked = false, symbol = '') => {
        const made = document.createElement('button');
        made.type = 'button';
        made.className = className;
        made.textContent = symbol || t(key);
        if (symbol) { made.title = t(key); made.setAttribute('aria-label', `${t(key)}: ${account.name}`); }
        made.dataset.locked = String(locked);
        made.disabled = locked || this.isBusy;
        made.addEventListener('click', action);
        actions.append(made);
      };
      // Switching taps the game: never under a running bot, and only to an account on this list.
      button('account_switch', 'primary', () => {
        if (window.confirm(t('account_switch_confirm', { name: account.name }))) {
          this.send('switch_account', { tag: account.tag }, { wait: SWITCH_WAIT_MS, label: t('account_switch') });
        }
      }, loaded || this.isRunning);
      button('account_up', 'account-move', () => this.move(index, index - 1), index === 0, '↑');
      button('account_down', 'account-move', () => this.move(index, index + 1), index === this.accounts.length - 1, '↓');
      button('account_remove', 'danger', () => {
        if (window.confirm(t('account_remove_confirm', { name: account.name }))) {
          this.save(this.accounts.filter((_, at) => at !== index));
        }
      });
      card.append(head, tag, picks, turns, actions);
      list.append(card);
    });
  }
}
