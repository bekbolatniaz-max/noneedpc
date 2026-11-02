// PadControls на поддельном DOM: мультитач D-pad и кнопок, первый снимок из центра D-pad.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { PadControls } from '../js/pad.js';
import { BTN, newPadState } from '../js/protocol.js';

globalThis.requestAnimationFrame = () => 1;

function el(dataset, rect) {
  return {
    dataset,
    classList: { toggle() {} },
    style: {},
    getBoundingClientRect: () => rect,
    querySelector: () => ({ style: {} }),
    querySelectorAll: () => [],
    closest() { return this; },
  };
}

function setup() {
  const handlers = {};
  const root = {
    addEventListener: (type, fn) => { handlers[type] = fn; },
    setPointerCapture() {},
    contains: () => true,
  };
  const state = newPadState();
  let changes = 0;
  const pad = new PadControls(root, state, () => { changes += 1; });
  const fire = (type, target, pointerId, x, y) =>
    handlers[type]({ target, pointerId, clientX: x, clientY: y, preventDefault() {} });
  return { state, fire, pad, changes: () => changes };
}

const DPAD = el({ dpad: '' }, { left: 0, top: 0, width: 100, height: 100 });
const A = el({ btn: 'A' }, { left: 200, top: 0, width: 40, height: 40 });

test('first touch at D-pad center still sends a snapshot', () => {
  const { state, fire, changes } = setup();
  fire('pointerdown', DPAD, 1, 50, 50);
  assert.equal(state.touching, true);
  assert.equal(changes(), 1);
  assert.equal(state.buttons, 0);
});

test('two fingers on UP: moving one to center keeps UP held', () => {
  const { state, fire } = setup();
  fire('pointerdown', DPAD, 1, 50, 5);
  fire('pointerdown', DPAD, 2, 50, 5);
  fire('pointermove', DPAD, 1, 50, 50);
  assert.equal(state.buttons & BTN.UP, BTN.UP);
  fire('pointerup', DPAD, 2, 50, 5);
  assert.equal(state.buttons, 0);
});

test('stick-independent buttons: A + D-pad combine and release separately', () => {
  const { state, fire } = setup();
  fire('pointerdown', A, 1, 210, 10);
  fire('pointerdown', DPAD, 2, 95, 50);
  assert.equal(state.buttons, BTN.A | BTN.RIGHT);
  fire('pointerup', A, 1, 210, 10);
  assert.equal(state.buttons, BTN.RIGHT);
  fire('pointercancel', DPAD, 2, 95, 50);
  assert.equal(state.buttons, 0);
  assert.equal(state.touching, false);
});
