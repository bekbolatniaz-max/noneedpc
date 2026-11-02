// WebSocket /ws: hello → welcome, бинарные снимки, PING/PONG, события, переподключение.

import { PROTOCOL_VERSION, PadEncoder, makePing, parseRumble, pongFor, rttFromPong } from './protocol.js';

const CLOSE_REPLACED = 4000;
const CLOSE_BAD_MESSAGE = 4400;
const CLOSE_AUTH = 4401;
const CLOSE_NO_SLOT = 4409;
const CLOSE_VERSION = 4426;

/**
 * Колбэки: onStatus(state, text), onWelcome(msg), onStats({rtt, serverRtt}), onEvent(name, data),
 * onRumble({large, small}), onFatal(code, text) — переподключаться бессмысленно (токен, версия, вытеснение).
 */
export class Connection {
  constructor(token, client, callbacks) {
    this.token = token;
    this.client = client;
    this.cb = callbacks;
    this.ws = null;
    this.ready = false;
    this.stopped = false;
    this.retryMs = 500;
    this.encoder = new PadEncoder();
    this.sendHz = 60;
    this.pingId = 0;
    this.pingTimer = 0;
    this.retryTimer = 0;
  }

  start() {
    this.stopped = false;
    this._open();
  }

  stop() {
    this.stopped = true;
    clearTimeout(this.retryTimer);
    clearInterval(this.pingTimer);
    if (this.ws) this.ws.close(1000);
  }

  /** Горячий путь: без аллокаций, только если сокет готов. */
  sendPad(state) {
    if (!this.ready || this.ws.bufferedAmount > 1024) return; // не копим очередь при плохой сети
    this.ws.send(this.encoder.encode(state, performance.now()));
  }

  _open() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.binaryType = 'arraybuffer';
    this.ws = ws;
    this.ready = false;
    this.cb.onStatus('connecting', 'Подключение…');

    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'hello', token: this.token, protocol_version: PROTOCOL_VERSION, client: this.client }));
    };

    ws.onmessage = (e) => {
      if (typeof e.data === 'string') { this._onText(e.data); return; }
      const pong = pongFor(e.data);
      if (pong) { ws.send(pong); return; }
      const rtt = rttFromPong(e.data, this.pingId, performance.now());
      if (rtt !== null) { this.cb.onStats({ rtt }); return; }
      const rumble = parseRumble(e.data);
      if (rumble) this.cb.onRumble(rumble);
    };

    ws.onclose = (e) => {
      this.ready = false;
      clearInterval(this.pingTimer);
      if (this.stopped) return;
      const fatal = {
        [CLOSE_AUTH]: 'Нужно заново сопрячь телефон',
        [CLOSE_VERSION]: 'Версия приложения не совпадает с сервером — обнови страницу',
        [CLOSE_REPLACED]: 'Этот телефон подключился в другой вкладке',
        [CLOSE_BAD_MESSAGE]: 'Сервер не понял приложение — обнови страницу',
      }[e.code];
      if (fatal) { this.stopped = true; this.cb.onFatal(e.code, fatal); return; }
      const text = e.code === CLOSE_NO_SLOT ? 'Все слоты заняты, жду…' : 'Нет связи, переподключаюсь…';
      this.cb.onStatus('offline', text);
      this.retryTimer = setTimeout(() => this._open(), this.retryMs);
      this.retryMs = Math.min(this.retryMs * 2, 5000);
    };
  }

  _onText(text) {
    let msg;
    try { msg = JSON.parse(text); } catch { return; }
    switch (msg.type) {
      case 'welcome':
        this.ready = true;
        this.retryMs = 500;
        this.sendHz = msg.send_hz || 60;
        this.cb.onWelcome(msg);
        this.cb.onStatus('online', `Слот ${msg.slot + 1}`);
        clearInterval(this.pingTimer);
        this.pingTimer = setInterval(() => {
          this.pingId = (this.pingId + 1) & 0xffff;
          if (this.ready) this.ws.send(makePing(this.pingId, performance.now()));
        }, 1000);
        break;
      case 'stats':
        this.cb.onStats({ serverRtt: msg.rtt_ms });
        break;
      case 'event':
        this.cb.onEvent(msg.name, msg.data || {});
        break;
      case 'error':
        // Подробности придут кодом закрытия; здесь только показать текст.
        this.cb.onStatus('offline', msg.message || msg.code);
        break;
      default:
        break; // неизвестные сообщения игнорируем (совместимость вперёд)
    }
  }
}

export { CLOSE_AUTH, CLOSE_VERSION, CLOSE_REPLACED };
