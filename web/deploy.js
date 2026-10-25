// Deploy pin editor: the device's deploy points drawn over its last screenshot of each deploy screen. "D" is the
// deploy point (troops, siege and heroes; the Battle Machine/Copter in the Builder Base); numbered pins are the
// Home Village spell points (rounds of front, left, right) or the Builder Base points, one per troop card.
import { t } from './i18n.js';

const SCREENS = ['home_scout', 'builder_scout', 'builder_stage2'];
// Maps shipped with the dashboard, taken from a real attack screen; the device's own screenshot replaces them.
const BUNDLED_MAPS = Object.fromEntries(SCREENS.map((screen) => [screen, `maps/${screen}.jpg`]));
const MAX_POINTS = { spells: 30, points: 20 };
const $ = (id) => document.getElementById(id);

export class DeployEditor {
  /** @param {(screen: string, deploy: number[], points: number[][]) => void} onSave */
  constructor(onSave) {
    this.onSave = onSave;
    this.data = null;
    this.images = {};
    this.screen = SCREENS[0];
    this.selected = null;
    for (const tab of document.querySelectorAll('[data-screen]')) {
      tab.addEventListener('click', () => this.show(tab.dataset.screen));
    }
    $('pin-add').addEventListener('click', () => this.add());
    $('pin-remove').addEventListener('click', () => this.remove());
    $('deploy-save').addEventListener('click', () => {
      const screen = this.current();
      if (screen) this.onSave(this.screen, screen.deploy, screen.points);
    });
    this.render();
  }

  /** @param {{resolution: number[], screens: Record<string, {kind: string, deploy: number[], points: number[][], bar: number[]}>}} data */
  load(data) {
    this.data = structuredClone(data);
    this.selected = null;
    this.render();
  }

  /** @param {string} screen @param {string} source data URL of the device screenshot */
  image(screen, source) {
    if (!SCREENS.includes(screen) || typeof source !== 'string' || !source.startsWith('data:image/jpeg;base64,')) return;
    this.images[screen] = source;
    if (screen === this.screen) this.render();
  }

  show(screen) {
    this.screen = screen;
    this.selected = null;
    this.render();
  }

  current() {
    return this.data && this.data.screens[this.screen];
  }

  bounds() {
    const [width, height] = this.data.resolution;
    return { width, height, bottom: this.current().bar[1] };
  }

  add() {
    const screen = this.current();
    if (!screen || screen.points.length >= MAX_POINTS[screen.kind]) return;
    const { width, bottom } = this.bounds();
    screen.points.push([Math.round(width / 2), Math.round(bottom / 2)]);
    this.selected = screen.points.length - 1;
    this.render();
  }

  remove() {
    const screen = this.current();
    if (!screen || this.selected === null || screen.points.length <= 1) return;
    screen.points.splice(this.selected, 1);
    this.selected = null;
    this.render();
  }

  render() {
    for (const tab of document.querySelectorAll('[data-screen]')) {
      tab.classList.toggle('active', tab.dataset.screen === this.screen);
      tab.setAttribute('aria-selected', String(tab.dataset.screen === this.screen));
    }
    const screen = this.current();
    $('deploy-hint').textContent = t(this.screen === 'home_scout' ? 'deploy_hint_home' : 'deploy_hint_builder');
    for (const id of ['pin-add', 'pin-remove', 'deploy-save']) $(id).disabled = !screen;
    const field = $('field');
    const pins = $('pins');
    pins.textContent = '';
    const shot = $('field-shot');
    const own = this.images[this.screen];
    const source = own || BUNDLED_MAPS[this.screen];
    shot.hidden = !source;
    if (source) shot.src = source;
    if (!screen) {
      $('field-bar').hidden = true;
      $('deploy-message').textContent = '';
      return;
    }
    const { width, height, bottom } = this.bounds();
    field.style.aspectRatio = `${width} / ${height}`;
    const bar = $('field-bar');
    bar.hidden = false;
    bar.style.top = `${(bottom / height) * 100}%`;
    $('deploy-message').textContent = t(own ? 'deploy_bar' : source ? 'deploy_bundled' : 'deploy_no_shot');
    pins.append(this.pin('D', screen.deploy, 'deploy', null));
    screen.points.forEach((point, index) => pins.append(this.pin(String(index + 1), point, screen.kind, index)));
  }

  pin(label, point, kind, index) {
    const element = document.createElement('button');
    element.type = 'button';
    element.className = `pin ${kind}${index !== null && index === this.selected ? ' selected' : ''}`;
    element.textContent = label;
    element.title = `${label}: ${point[0]}, ${point[1]}`;
    const { width, height } = this.bounds();
    element.style.left = `${(point[0] / width) * 100}%`;
    element.style.top = `${(point[1] / height) * 100}%`;
    element.addEventListener('pointerdown', (pressed) => this.drag(pressed, element, point, index));
    return element;
  }

  drag(pressed, element, point, index) {
    pressed.preventDefault();
    element.setPointerCapture(pressed.pointerId);
    const { width, height, bottom } = this.bounds();
    // The pin keeps its distance from where it was grabbed instead of jumping its centre under the pointer, so it
    // is saved where it is seen.
    const pin = element.getBoundingClientRect();
    const grabX = pressed.clientX - (pin.left + pin.width / 2);
    const grabY = pressed.clientY - (pin.top + pin.height / 2);
    const move = (moved) => {
      const field = $('field').getBoundingClientRect();
      const x = ((moved.clientX - grabX - field.left) / field.width) * width;
      const y = ((moved.clientY - grabY - field.top) / field.height) * height;
      // Kept on the battlefield, never on the card bar.
      point[0] = Math.min(width - 1, Math.max(0, Math.round(x)));
      point[1] = Math.min(bottom - 1, Math.max(0, Math.round(y)));
      element.style.left = `${(point[0] / width) * 100}%`;
      element.style.top = `${(point[1] / height) * 100}%`;
      element.title = `${element.textContent}: ${point[0]}, ${point[1]}`;
    };
    const release = () => {
      element.removeEventListener('pointermove', move);
      element.removeEventListener('pointerup', release);
      element.removeEventListener('pointercancel', release);
      this.selected = index;
      this.render();
    };
    element.addEventListener('pointermove', move);
    element.addEventListener('pointerup', release);
    element.addEventListener('pointercancel', release);
  }
}
