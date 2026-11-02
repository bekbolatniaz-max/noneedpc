// node --test pwa/tests — протокол и логика стика/D-pad без браузера.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  BTN, PAD_STATE_SIZE, PadEncoder, axis, makePing, newPadState, parseRumble, pongFor, rttFromPong,
} from '../js/protocol.js';
import { _test } from '../js/pad.js';

test('pad frame matches contract: 18 bytes LE', () => {
  const s = newPadState();
  Object.assign(s, { buttons: BTN.A | BTN.UP | 0x0800, lx: -32768, ly: 32767, rx: 1, ry: -1, lt: 255, rt: 7, touching: true });
  const enc = new PadEncoder();
  const v = new DataView(enc.encode(s, 70000));
  assert.equal(v.byteLength, PAD_STATE_SIZE);
  assert.equal(v.getUint8(0), 0x01);
  assert.equal(v.getUint8(1), 1);
  assert.equal(v.getUint16(2, true), 1);
  assert.equal(v.getUint16(4, true), 70000 & 0xffff);
  assert.equal(v.getUint16(6, true), 0x1001); // резервный бит 11 вырезан
  assert.equal(v.getInt16(8, true), -32768);
  assert.equal(v.getInt16(10, true), 32767);
  assert.equal(v.getUint8(16), 255);
  assert.equal(v.getUint8(17), 7);
});

test('seq wraps at 65536 and buffer is reused', () => {
  const enc = new PadEncoder();
  enc.seq = 0xffff;
  const b1 = enc.encode(newPadState(), 0);
  assert.equal(new DataView(b1).getUint16(2, true), 0);
  assert.equal(enc.encode(newPadState(), 0), b1);
});

test('axis clamps to int16', () => {
  assert.equal(axis(1.5), 32767);
  assert.equal(axis(-2), -32768);
  assert.equal(axis(0), 0);
  assert.equal(axis(0.5), 16384);
});

test('ping/pong roundtrip and rtt', () => {
  const ping = makePing(7, 1000);
  const pong = pongFor(ping);
  assert.equal(new DataView(pong).getUint8(0), 0x03);
  assert.equal(rttFromPong(pong, 7, 1025), 25);
  assert.equal(rttFromPong(pong, 8, 1025), null);
  assert.equal(pongFor(pong), null);
  assert.equal(rttFromPong(makePing(1, 2 ** 32 - 5), 1, 0), null);
});

test('rumble parse', () => {
  assert.deepEqual(parseRumble(new Uint8Array([0x10, 0, 200, 30]).buffer), { large: 200, small: 30 });
  assert.equal(parseRumble(new Uint8Array([0x11, 0, 1, 1]).buffer), null);
});

test('stick deadzone and clamp', () => {
  const { stickValue } = _test;
  assert.deepEqual(stickValue(5, 0, 100).slice(0, 2), [0, 0]);
  const [x, y] = stickValue(300, 0, 100);
  assert.equal(x, 1); assert.equal(y, 0);
  const [hx] = stickValue(56, 0, 100);
  assert.ok(hx > 0.4 && hx < 0.6);
});

test('dpad diagonals and center', () => {
  const { dpadMask } = _test;
  assert.equal(dpadMask(0, 0, 100), 0);
  assert.equal(dpadMask(0, -40, 100), BTN.UP);
  assert.equal(dpadMask(40, 40, 100), BTN.DOWN | BTN.RIGHT);
});
