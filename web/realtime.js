// A small Supabase Realtime client (Phoenix protocol 1.0.0, broadcast only) for one channel. It is part of the
// page rather than supabase-js from a CDN, so no third-party script runs next to the device key.
const HEARTBEAT_MS = 20000;
const RECONNECT_MS = [1000, 2000, 5000, 10000, 20000];

export class RealtimeChannel {
  /**
   * @param {string} url project URL, https://<ref>.supabase.co
   * @param {string} apikey publishable key
   * @param {string} name channel name
   * @param {{ broadcast: (event: string, payload: unknown) => void, status: (joined: boolean) => void }} handlers
   */
  constructor(url, apikey, name, handlers) {
    this.address = `${url.replace(/^http/, 'ws')}/realtime/v1/websocket?apikey=${encodeURIComponent(apikey)}&vsn=1.0.0`;
    this.topic = `realtime:${name}`;
    this.handlers = handlers;
    this.ref = 0;
    this.joinRef = null;
    this.attempt = 0;
    this.closed = false;
    this.open();
  }

  open() {
    const socket = new WebSocket(this.address);
    this.socket = socket;
    socket.onopen = () => {
      this.joinRef = this.nextRef();
      const config = { broadcast: { self: false, ack: false }, presence: { enabled: false }, private: false };
      // The join's own ref is its join_ref: the reply carries that ref, and received() matches it.
      this.push('phx_join', { config }, this.topic, this.joinRef, this.joinRef);
      this.beat = setInterval(() => this.push('heartbeat', {}, 'phoenix', null), HEARTBEAT_MS);
    };
    socket.onmessage = (message) => this.received(message.data);
    socket.onclose = () => this.reconnect();
    socket.onerror = () => socket.close();
  }

  received(raw) {
    let message;
    try { message = JSON.parse(raw); } catch { return; }
    if (!message || typeof message !== 'object') return;
    if (message.event === 'phx_reply' && message.ref === this.joinRef) {
      if (message.payload && message.payload.status === 'ok') {
        this.attempt = 0;
        this.handlers.status(true);
      } else {
        this.socket.close();
      }
      return;
    }
    if ((message.event === 'phx_error' || message.event === 'phx_close') && message.topic === this.topic) {
      this.socket.close();
      return;
    }
    const payload = message.payload;
    if (message.event === 'broadcast' && payload && typeof payload === 'object') {
      this.handlers.broadcast(payload.event, payload.payload);
    }
  }

  reconnect() {
    clearInterval(this.beat);
    this.handlers.status(false);
    if (this.closed) return;
    const wait = RECONNECT_MS[Math.min(this.attempt, RECONNECT_MS.length - 1)];
    this.attempt += 1;
    this.timer = setTimeout(() => this.open(), wait);
  }

  /** @param {string} event @param {unknown} payload @returns {boolean} whether it went out */
  send(event, payload) {
    return this.push('broadcast', { type: 'broadcast', event, payload }, this.topic, this.joinRef);
  }

  push(event, payload, topic, joinRef, ref = this.nextRef()) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return false;
    this.socket.send(JSON.stringify({ topic, event, payload, ref, join_ref: joinRef }));
    return true;
  }

  nextRef() {
    this.ref += 1;
    return String(this.ref);
  }

  close() {
    this.closed = true;
    clearTimeout(this.timer);
    clearInterval(this.beat);
    if (this.socket) this.socket.close();
  }
}
