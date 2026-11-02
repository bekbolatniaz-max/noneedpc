# Nitro Pad — UI (PWA)

Чистый JS, ES-модули, без сборщика и внешних CDN. Раздаётся сервером с того же origin (`http://<ip>:8765/`).

| Файл | Что делает |
|---|---|
| `js/protocol.js` | бинарный протокол v1: снимок 18 байт, PING/PONG, RUMBLE. Без DOM — тесты в Node |
| `js/api.js` | HTTP по OpenAPI; токен только в `localStorage` и заголовке `Authorization` |
| `js/conn.js` | WebSocket: hello/welcome, PING раз в секунду, переподключение 0,5→5 с, коды закрытия 4000/4400/4401/4426 — без переподключения |
| `js/pad.js` | экранный геймпад: Pointer Events, мультитач по `pointerId`, стики с мёртвой зоной 12 %, D-pad зоной (диагонали), перерисовка в `requestAnimationFrame` |
| `js/app.js` | экраны: сопряжение (`#pair=XXXXXX` из QR, сразу убирается из адреса) → геймпад; меню (плашка статуса): игры, «Приставка вкл/выкл» |

Отправка: при каждом изменении + `send_hz` (60 Гц), пока палец на экране; при сворачивании всё отпускается. Вибрация: `navigator.vibrate` (Android), на iOS — подсветка рамки.

Тесты: `node --test pwa/tests/*.test.mjs`; статические проверки (нет `innerHTML`, внешних ресурсов, токена в URL) — `server/tests/test_pwa_static.py`.

M2 (друг): редактор раскладки, Wake Lock после HTTPS, экран «Хранилище», выбор бэкенда, Playwright-тесты iPhone/Pixel.
