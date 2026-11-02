# Онбординг фронтенда (PWA «Nitro Pad»)

## 1. Запуск мока (5 минут)

```bash
git clone https://github.com/bekbolatniaz-max/noneedpc.git
cd noneedpc
python -m pip install -r server/requirements-dev.txt
python -m server.mock --pair-code 123456
```

- `--pair-code 123456` — постоянный многоразовый код. **Есть только в моке**, на настоящем сервере код одноразовый и меняется.
- Для телефона: `--host 0.0.0.0`, брандмауэр — только «Частные сети». Сервер напечатает адрес.
- Мок раздаёт папку `pwa/` с корня `http://<ip>:8765/` — тот же origin, что API и WS, поэтому CORS не нужен. Папку `pwa/` создаёшь ты.
- Проверка: `python -m pytest -q` — контрактные тесты мока (должны быть зелёными).

## 2. Что прочитать

1. [`contracts/protocol.md`](../contracts/protocol.md) — главное. Бинарный снимок геймпада, PING/PONG, коды закрытия.
2. [`contracts/openapi.yaml`](../contracts/openapi.yaml) — HTTP.
3. [`contracts/ws-messages.schema.json`](../contracts/ws-messages.schema.json) — текстовые WS-сообщения.
4. `docs/architecture.md`, разделы 5 и 10.

## 3. Минимальный поток

```js
const info = await (await fetch('/api/v1/info')).json();
if (info.protocol_version !== 1) showError('Обнови приложение');

// сопряжение (код из QR #pair=XXXXXX или ввод руками)
const { token } = await (await fetch('/api/v1/pair', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ code, device_name: 'Pixel' }),
})).json();
localStorage.setItem('nitro.token', token);   // никогда не в URL и не в console.log

const ws = new WebSocket(`ws://${location.host}/ws`);
ws.binaryType = 'arraybuffer';
ws.onopen = () => ws.send(JSON.stringify({ type: 'hello', token, protocol_version: 1,
                                           client: { platform: 'android', name: 'Pixel' } }));

// снимок геймпада: один буфер на всё время жизни, без аллокаций в горячем пути
const buf = new ArrayBuffer(18), dv = new DataView(buf);
let seq = 0;
function sendPad(p) {               // p: {buttons, lx, ly, rx, ry, lt, rt, touching}
  dv.setUint8(0, 0x01);
  dv.setUint8(1, p.touching ? 1 : 0);
  dv.setUint16(2, seq = (seq + 1) & 0xffff, true);
  dv.setUint16(4, performance.now() & 0xffff, true);
  dv.setUint16(6, p.buttons, true);
  dv.setInt16(8, p.lx, true);  dv.setInt16(10, p.ly, true);   // ly: вверх = +
  dv.setInt16(12, p.rx, true); dv.setInt16(14, p.ry, true);
  dv.setUint8(16, p.lt); dv.setUint8(17, p.rt);
  ws.send(buf);
}

ws.onmessage = (e) => {
  if (typeof e.data === 'string') { const m = JSON.parse(e.data); /* welcome | error | stats | event */ return; }
  const v = new DataView(e.data);
  if (v.getUint8(0) === 0x02) {     // PING от сервера → сразу PONG с теми же байтами
    const pong = e.data.slice(0); new DataView(pong).setUint8(0, 0x03); ws.send(pong);
  }
};
```

Ключевое: отправлять при каждом изменении **и** 60 Гц (`welcome.send_hz`), пока палец на экране. Если перестать слать на 250 мс, сервер сам отпустит все кнопки — это нормально.

## 4. Данные мока

- Игры `1001`–`1004`: с обложкой, без обложки (`has_cover: false` → `404`), на отключённом диске (`available: false`, запуск → `409`).
- Режим приставки: `off → switching → on` за ~0,5 с, приходит WS `event` `console_mode`.
- Хранилище: внутренний SSD и отключённый внешний диск.
- `features.rumble` и `features.backend_select` пока `false` — эти экраны скрывай.

## 5. Правила

- Работаешь в `pwa/` и `docs/ui.md`. Нужен новый эндпоинт или поле — issue с меткой `contract-change`, не придумывай.
- Неизвестные поля в ответах игнорируй — сервер может добавлять их в новых версиях контракта.
- Ветка `feat/fe-T2xx-…`, один PR — одна задача; слияние делает лидер.
