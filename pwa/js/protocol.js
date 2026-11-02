// Бинарный протокол v1 (contracts/protocol.md, раздел 2.2). Чистый модуль: без DOM, тестируется в Node.

export const PROTOCOL_VERSION = 1;
export const PAD_STATE = 0x01;
export const PING = 0x02;
export const PONG = 0x03;
export const RUMBLE = 0x10;
export const PAD_STATE_SIZE = 18;

// Совпадает с XInput wButtons (D-003).
export const BTN = Object.freeze({
  UP: 0x0001, DOWN: 0x0002, LEFT: 0x0004, RIGHT: 0x0008,
  START: 0x0010, SELECT: 0x0020, L3: 0x0040, R3: 0x0080,
  LB: 0x0100, RB: 0x0200, HOME: 0x0400,
  A: 0x1000, B: 0x2000, X: 0x4000, Y: 0x8000,
});

export function newPadState() {
  return { buttons: 0, lx: 0, ly: 0, rx: 0, ry: 0, lt: 0, rt: 0, touching: false };
}

/** -1..1 → int16; вверх = + (как XInput), поэтому ly/ry вызывающий передаёт уже инвертированными. */
export function axis(v) {
  if (v >= 1) return 32767;
  if (v <= -1) return -32768;
  return Math.round(v * (v < 0 ? 32768 : 32767));
}

/** Один буфер на всё время жизни: в горячем пути нет аллокаций. WebSocket.send копирует данные. */
export class PadEncoder {
  constructor() {
    this.buffer = new ArrayBuffer(PAD_STATE_SIZE);
    this.view = new DataView(this.buffer);
    this.seq = 0;
  }

  encode(s, nowMs) {
    const v = this.view;
    this.seq = (this.seq + 1) & 0xffff;
    v.setUint8(0, PAD_STATE);
    v.setUint8(1, s.touching ? 1 : 0);
    v.setUint16(2, this.seq, true);
    v.setUint16(4, nowMs & 0xffff, true);
    v.setUint16(6, s.buttons & 0xf7ff, true); // бит 11 зарезервирован
    v.setInt16(8, s.lx, true);
    v.setInt16(10, s.ly, true);
    v.setInt16(12, s.rx, true);
    v.setInt16(14, s.ry, true);
    v.setUint8(16, s.lt);
    v.setUint8(17, s.rt);
    return this.buffer;
  }
}

/** PING → PONG с теми же id и t_ms. null — это не PING. */
export function pongFor(data) {
  if (!(data instanceof ArrayBuffer) || data.byteLength !== 8) return null;
  if (new DataView(data).getUint8(0) !== PING) return null;
  const copy = data.slice(0);
  new DataView(copy).setUint8(0, PONG);
  return copy;
}

export function makePing(id, nowMs) {
  const b = new ArrayBuffer(8);
  const v = new DataView(b);
  v.setUint8(0, PING);
  v.setUint16(2, id & 0xffff, true);
  v.setUint32(4, nowMs >>> 0, true);
  return b;
}

/** PONG на наш PING → RTT в мс, иначе null. */
export function rttFromPong(data, expectedId, nowMs) {
  if (!(data instanceof ArrayBuffer) || data.byteLength !== 8) return null;
  const v = new DataView(data);
  if (v.getUint8(0) !== PONG || v.getUint16(2, true) !== (expectedId & 0xffff)) return null;
  return ((nowMs >>> 0) - v.getUint32(4, true)) >>> 0;
}

/** RUMBLE → {large, small} (0..255), иначе null. */
export function parseRumble(data) {
  if (!(data instanceof ArrayBuffer) || data.byteLength !== 4) return null;
  const v = new DataView(data);
  if (v.getUint8(0) !== RUMBLE) return null;
  return { large: v.getUint8(2), small: v.getUint8(3) };
}
