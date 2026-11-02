# noneedpc — Nitro Console

Игровой ноутбук + ТВ по HDMI + **телефон как геймпад** + игры из Steam. Архитектура: [docs/architecture.md](docs/architecture.md).

Статус: **M0 (каркас)** — контракт v1 и мок-сервер. Ход работ: [PROGRESS.md](PROGRESS.md).

## Быстрый старт (мок, любая ОС)

```bash
python -m pip install -r server/requirements-dev.txt
python -m server.mock --pair-code 123456
```

Открыть `http://127.0.0.1:8765/api/v1/info`. Для телефона в той же Wi-Fi: `--host 0.0.0.0`.

Тесты: `python -m pytest -q`.

## Документы
- Протокол: [contracts/protocol.md](contracts/protocol.md), [contracts/openapi.yaml](contracts/openapi.yaml), [contracts/ws-messages.schema.json](contracts/ws-messages.schema.json)
- Фронтенд: [docs/onboarding-frontend.md](docs/onboarding-frontend.md)
- Решения: [DECISIONS.md](DECISIONS.md)
