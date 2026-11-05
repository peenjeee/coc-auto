// No interval runs while the viewer is hidden. Requests finish before another is scheduled.
export class VisiblePoller {
  constructor({ visible, request, interval = 1000, schedule = (callback, delay) => setTimeout(callback, delay), cancel = (timer) => clearTimeout(timer) }) {
    Object.assign(this, { visible, request, interval, schedule, cancel });
    this.timer = null;
    this.pending = false;
  }
  sync() {
    if (!this.visible()) {
      if (this.timer !== null) this.cancel(this.timer);
      this.timer = null;
      return;
    }
    if (!this.pending && this.timer === null) this.tick();
  }
  async tick() {
    this.timer = null;
    if (this.pending || !this.visible()) return;
    this.pending = true;
    try { await this.request(); }
    finally {
      this.pending = false;
      if (this.visible()) this.timer = this.schedule(() => this.tick(), this.interval);
    }
  }
}
