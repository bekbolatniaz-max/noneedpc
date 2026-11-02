// Nitro Pad: сопряжение → геймпад; меню с играми и режимом приставки.
// Данные сервера выводятся только через textContent: разметку из данных не собираем.

import { ApiError, api, clearToken, getToken, setToken } from './api.js';
import { CLOSE_AUTH, Connection } from './conn.js';
import { PadControls } from './pad.js';
import { PROTOCOL_VERSION, newPadState } from './protocol.js';

const $ = (id) => document.getElementById(id);
const screens = ['screen-pair', 'screen-fatal', 'screen-pad'];

function show(id) {
  $('menu').hidden = true; // меню не должно перекрывать сопряжение или ошибку
  for (const s of screens) $(s).hidden = s !== id;
}

function platform() {
  const ua = navigator.userAgent;
  if (/iPhone|iPad|iPod/.test(ua)) return 'ios';
  if (/Android/.test(ua)) return 'android';
  return 'other';
}

function deviceName() {
  return { ios: 'iPhone', android: 'Android' }[platform()] || 'Телефон';
}

// --- состояние ---
const state = newPadState();
let conn = null;
let controls = null;
let sendTimer = 0;
let features = {};
let playerNumber = null; // номер игрока в игре (событие player); до него показываем слот

function fatal(text) {
  $('fatal-text').textContent = text;
  show('screen-fatal');
}

// --- сопряжение ---
function readPairCodeFromHash() {
  const m = /(?:^|[#&])pair=(\d{6})/.exec(location.hash);
  if (m) history.replaceState(null, '', location.pathname); // код не остаётся в адресе и истории
  return m ? m[1] : null;
}

async function pair(code) {
  $('pair-error').textContent = '';
  try {
    const { token } = await api.pair(code, deviceName());
    const saved = setToken(token);
    startPad();
    if (!saved) setStatus('connecting', 'Код не запомнен (приватный режим)');
  } catch (e) {
    const msg = e instanceof ApiError
      ? ({ forbidden: 'Неверный или устаревший код', rate_limited: 'Много попыток — на ноутбуке новый код' }[e.code] || e.message)
      : 'Сервер недоступен';
    $('pair-error').textContent = msg;
  }
}

function showPair(message = '', code = null) {
  show('screen-pair');
  $('pair-error').textContent = message;
  if (code) {
    $('pair-code').value = code;
    pair(code);
  } else {
    $('pair-code').focus();
  }
}

$('pair-form').addEventListener('submit', (e) => {
  e.preventDefault();
  const code = $('pair-code').value.trim();
  if (/^\d{6}$/.test(code)) pair(code);
  else $('pair-error').textContent = 'Код — 6 цифр';
});

// --- геймпад ---
function setStatus(kind, text) {
  $('status-dot').className = `dot ${kind}`;
  // После переподключения conn пишет «Слот N»; если номер игрока уже известен — он важнее.
  $('status-text').textContent = kind === 'online' && playerNumber ? `Игрок ${playerNumber}` : text;
}

function sendNow() {
  if (conn) conn.sendPad(state);
}

/** Пока палец на экране — снимок каждые 1/send_hz с, плюс сразу при каждом изменении. */
function onPadChange() {
  sendNow();
  if (state.touching && !sendTimer) {
    sendTimer = setInterval(() => {
      if (!state.touching) { clearInterval(sendTimer); sendTimer = 0; return; }
      sendNow();
    }, 1000 / (conn ? conn.sendHz : 60));
  }
}

let rumbleTimer = 0;
function onRumble({ large, small }) {
  const strength = Math.max(large, small);
  if (typeof navigator.vibrate === 'function') {
    navigator.vibrate(strength > 0 ? 60 : 0);
  } else {
    // iOS: Vibration API нет — визуальный отклик.
    const el = $('rumble-flash');
    el.classList.toggle('active', strength > 0);
    clearTimeout(rumbleTimer);
    if (strength > 0) rumbleTimer = setTimeout(() => el.classList.remove('active'), 300);
  }
}

function startPad() {
  playerNumber = null;
  show('screen-pad');
  if (!controls) controls = new PadControls($('screen-pad'), state, onPadChange);
  if (conn) conn.stop();
  conn = new Connection(getToken(), { platform: platform(), name: deviceName() }, {
    onStatus: setStatus,
    onWelcome: () => {},
    onStats: ({ rtt, serverRtt }) => {
      const v = rtt ?? serverRtt;
      if (v !== null && v !== undefined) $('rtt-text').textContent = `${Math.round(v)} мс`;
    },
    onEvent: (name, data) => {
      if (name === 'console_mode') renderConsole(data.state);
      if (name === 'player' && Number.isInteger(data.number)) {
        playerNumber = data.number;
        setStatus('online', `Игрок ${playerNumber}`);
      }
    },
    onRumble,
    onFatal: (code, text) => {
      controls.releaseAll();
      if (code === CLOSE_AUTH) { clearToken(); showPair(text); } else fatal(text);
    },
  });
  conn.start();
}

// Свернули приложение — всё отпустить (сервер и так отпустит через deadman, но не ждём).
document.addEventListener('visibilitychange', () => {
  if (document.hidden && controls) controls.releaseAll();
});
window.addEventListener('blur', () => controls && controls.releaseAll());

// --- меню: игры и режим приставки ---
let consoleState = 'off';
// Каждое применённое состояние увеличивает версию; ответ HTTP применяется, только если
// с момента запроса не пришло ничего новее (WS-событие или другой ответ).
let consoleVersion = 0;

function renderConsole(s, message = '') {
  consoleVersion += 1;
  consoleState = s;
  const label = { on: 'вкл', off: 'выкл', switching: '…', error: 'ошибка' }[s] || s;
  $('console-btn').textContent = `Приставка: ${label}`;
  $('console-btn').disabled = s === 'switching';
  $('console-msg').textContent = message;
}

async function loadConsole() {
  if (!features.console_mode) { $('console-btn').hidden = true; return; }
  const asked = consoleVersion;
  try {
    const c = await api.consoleMode();
    if (consoleVersion === asked) renderConsole(c.state, c.message || '');
  } catch (e) {
    $('console-msg').textContent = e.message;
  }
}

$('console-btn').addEventListener('click', async () => {
  const asked = consoleVersion;
  try {
    const c = await api.setConsoleMode(consoleState !== 'on');
    if (consoleVersion === asked) renderConsole(c.state, c.message || '');
  } catch (e) {
    $('console-msg').textContent = e.message;
  }
});

const coverUrls = [];
let gamesLoad = 0; // номер текущей загрузки меню: поздние обложки старой загрузки выбрасываются
async function loadGames() {
  const load = ++gamesLoad;
  const list = $('games');
  const msg = $('games-msg');
  list.replaceChildren();
  for (const u of coverUrls.splice(0)) URL.revokeObjectURL(u);
  if (!features.games) { msg.textContent = 'Список игр недоступен'; return; }
  msg.textContent = 'Загрузка…';
  try {
    const { games } = await api.games();
    if (load !== gamesLoad) return;
    msg.textContent = games.length ? '' : 'Игр не найдено';
    const tpl = $('game-tpl');
    for (const g of games) {
      const node = tpl.content.firstElementChild.cloneNode(true);
      node.querySelector('.title').textContent = g.name;
      node.disabled = !g.available;
      if (!features.launch) {
        node.classList.add('no-launch'); // запуск выключен сервером: только список, без действия
        node.tabIndex = -1;
      } else node.addEventListener('click', async () => {
        node.disabled = true;
        try {
          await api.launch(g.appid);
          msg.textContent = `Запускаю «${g.name}»…`;
          closeMenu();
        } catch (e) {
          msg.textContent = e.message;
        } finally {
          setTimeout(() => { node.disabled = !g.available; }, 2000);
        }
      });
      list.append(node);
      if (g.has_cover) {
        api.cover(g.appid).then((url) => {
          if (load !== gamesLoad) { URL.revokeObjectURL(url); return; }
          coverUrls.push(url);
          node.querySelector('img').src = url;
        }).catch(() => { /* без обложки карточка остаётся плашкой — это не ошибка для игрока */ });
      }
    }
  } catch (e) {
    msg.textContent = e.message;
  }
}

async function loadPlayers() {
  if (!features.devices) return;
  try {
    const { devices } = await api.devices();
    const names = devices.filter((d) => d.slot !== null).map((d) => d.name);
    $('players').textContent = names.length ? `Подключены: ${names.join(', ')}` : '';
  } catch {
    $('players').textContent = '';
  }
}

$('invite-btn').addEventListener('click', async () => {
  const box = $('invite');
  if (!box.hidden) { box.hidden = true; return; }
  try {
    const { code, expires_in_s: ttl } = await api.pairCode();
    $('invite-code').textContent = code;
    $('invite-link').textContent = `${location.origin}/#pair=${code}`;
    $('invite-ttl').textContent = ttl === null ? '' : `Код действует ещё ${Math.ceil(ttl / 60)} мин и одноразовый`;
    box.hidden = false;
  } catch (e) {
    $('console-msg').textContent = e.message;
  }
});

function openMenu() {
  controls.releaseAll();
  $('menu').hidden = false;
  $('invite').hidden = true;
  loadConsole();
  loadPlayers();
  loadGames();
}

function closeMenu() {
  $('menu').hidden = true;
}

$('menu-btn').addEventListener('click', openMenu);
$('menu-close').addEventListener('click', closeMenu);
$('fatal-retry').addEventListener('click', () => location.reload());

// --- старт ---
async function main() {
  const code = readPairCodeFromHash();
  let info;
  try {
    info = await api.info();
  } catch {
    fatal('Сервер недоступен. Проверь, что ноутбук и телефон в одной Wi-Fi.');
    return;
  }
  if (info.protocol_version !== PROTOCOL_VERSION) {
    fatal(`Версия протокола сервера (${info.protocol_version}) не совпадает с приложением (${PROTOCOL_VERSION}). Обнови страницу.`);
    return;
  }
  features = info.features || {};
  document.title = info.name ? `${info.name} — Pad` : 'Nitro Pad';
  if (code || !getToken()) showPair('', code);
  else startPad();
}

main();
