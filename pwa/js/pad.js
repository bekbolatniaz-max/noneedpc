// Экранный геймпад: мультитач через Pointer Events, каждый палец отслеживается отдельно (pointerId).
// Обработчики лёгкие: состояние меняется сразу, перерисовка — в requestAnimationFrame.

import { BTN, axis } from './protocol.js';

const DEADZONE = 0.12;
const canVibrate = typeof navigator !== 'undefined' && typeof navigator.vibrate === 'function';

function stickValue(dx, dy, radius) {
  let nx = dx / radius;
  let ny = dy / radius;
  const mag = Math.hypot(nx, ny);
  if (mag > 1) { nx /= mag; ny /= mag; }
  if (mag < DEADZONE) return [0, 0, nx, ny];
  // Плавный выход из мёртвой зоны, без скачка.
  const scale = Math.min(1, (Math.min(mag, 1) - DEADZONE) / (1 - DEADZONE)) / Math.min(mag, 1);
  return [nx * scale, ny * scale, nx, ny];
}

function dpadMask(dx, dy, size) {
  const t = size * 0.18; // центр — ничего не нажато
  let m = 0;
  if (dy < -t) m |= BTN.UP;
  if (dy > t) m |= BTN.DOWN;
  if (dx < -t) m |= BTN.LEFT;
  if (dx > t) m |= BTN.RIGHT;
  return m;
}

export class PadControls {
  constructor(root, state, onChange) {
    this.root = root;
    this.state = state;
    this.onChange = onChange;
    this.pointers = new Map(); // pointerId → {kind, el, ...}
    this.dirty = new Set();
    this.raf = 0;

    root.addEventListener('pointerdown', (e) => this._down(e));
    root.addEventListener('pointermove', (e) => this._move(e), { passive: true });
    root.addEventListener('pointerup', (e) => this._up(e), { passive: true });
    root.addEventListener('pointercancel', (e) => this._up(e), { passive: true });
    root.addEventListener('contextmenu', (e) => e.preventDefault());
  }

  /** Отпустить всё (потеря фокуса, сворачивание). */
  releaseAll() {
    for (const id of [...this.pointers.keys()]) this._release(id);
    const s = this.state;
    s.buttons = 0; s.lx = s.ly = s.rx = s.ry = 0; s.lt = s.rt = 0; s.touching = false;
    this.onChange();
  }

  _down(e) {
    const el = e.target.closest('[data-btn],[data-trigger],[data-stick],[data-dpad]');
    if (!el || !this.root.contains(el)) return;
    e.preventDefault();
    this.root.setPointerCapture(e.pointerId);
    const rect = el.getBoundingClientRect();
    const p = { el, rect, mask: 0 };
    if (el.dataset.btn) {
      p.kind = 'btn';
      p.mask = BTN[el.dataset.btn];
    } else if (el.dataset.trigger) {
      p.kind = 'trigger';
      this.state[el.dataset.trigger] = 255;
    } else if (el.dataset.stick) {
      p.kind = 'stick';
      p.side = el.dataset.stick === 'left' ? 'l' : 'r';
      p.knob = el.querySelector('.knob');
      p.cx = rect.left + rect.width / 2;
      p.cy = rect.top + rect.height / 2;
      p.radius = rect.width * 0.4;
    } else {
      p.kind = 'dpad';
    }
    this.pointers.set(e.pointerId, p);
    if (canVibrate && p.kind !== 'stick') navigator.vibrate(8);
    this._apply(p, e.clientX, e.clientY, true); // первый снимок уходит всегда, даже из центра D-pad
  }

  _move(e) {
    const p = this.pointers.get(e.pointerId);
    if (p && (p.kind === 'stick' || p.kind === 'dpad')) this._apply(p, e.clientX, e.clientY);
  }

  _up(e) {
    if (this.pointers.has(e.pointerId)) {
      this._release(e.pointerId);
      this.onChange();
    }
  }

  _apply(p, x, y, force = false) {
    const s = this.state;
    if (p.kind === 'stick') {
      const [vx, vy, kx, ky] = stickValue(x - p.cx, y - p.cy, p.radius);
      s[`${p.side}x`] = axis(vx);
      s[`${p.side}y`] = axis(-vy); // экран: вниз = +, протокол: вверх = +
      p.kx = kx * p.radius;
      p.ky = ky * p.radius;
    } else if (p.kind === 'dpad') {
      const r = p.rect;
      const mask = dpadMask(x - (r.left + r.width / 2), y - (r.top + r.height / 2), r.width);
      if (mask === p.mask && !force) return; // палец сдвинулся, но направление то же
      p.mask = mask;
    }
    this._recomputeButtons();
    s.touching = this.pointers.size > 0;
    this.dirty.add(p);
    this._schedule();
    this.onChange();
  }

  _release(id) {
    const p = this.pointers.get(id);
    this.pointers.delete(id);
    if (!p) return;
    const s = this.state;
    if (p.kind === 'btn' || p.kind === 'dpad') {
      p.mask = 0;
      this._recomputeButtons(); // бит остаётся, если его держит другой палец
    } else if (p.kind === 'trigger') {
      const held = [...this.pointers.values()].some((q) => q.kind === 'trigger' && q.el === p.el);
      if (!held) s[p.el.dataset.trigger] = 0;
    } else {
      s[`${p.side}x`] = 0;
      s[`${p.side}y`] = 0;
      p.kx = p.ky = 0;
    }
    s.touching = this.pointers.size > 0;
    p.released = true;
    this.dirty.add(p);
    this._schedule();
  }

  /** Кнопки = объединение масок всех пальцев: каждый палец владеет только своей маской. */
  _recomputeButtons() {
    let m = 0;
    for (const q of this.pointers.values()) if (q.kind === 'btn' || q.kind === 'dpad') m |= q.mask;
    this.state.buttons = m;
  }

  _schedule() {
    if (!this.raf) this.raf = requestAnimationFrame(() => this._render());
  }

  _render() {
    this.raf = 0;
    for (const p of this.dirty) {
      if (p.kind === 'stick') {
        p.knob.style.transform = `translate(${p.kx || 0}px, ${p.ky || 0}px)`;
        p.el.classList.toggle('on', !p.released);
      } else if (p.kind === 'dpad') {
        for (const arm of p.el.querySelectorAll('[data-arm]')) {
          arm.classList.toggle('on', (this.state.buttons & BTN[arm.dataset.arm]) !== 0);
        }
      } else {
        p.el.classList.toggle('on', !p.released);
      }
    }
    this.dirty.clear();
  }
}

export const _test = { stickValue, dpadMask };
