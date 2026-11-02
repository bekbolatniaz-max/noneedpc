# Как добавить плагин

Ядро (`server/core/`) не знает о конкретных устройствах: тест `server/tests/test_architecture.py` запрещает ему импортировать плагины.

## Новый способ вывода (`OutputBackend`)

1. Файл `server/plugins/output/<id>.py`, класс-наследник `OutputBackend` с `id`, `title`, `analog`.
2. `available()` — проверить наличие драйвера/библиотеки, **ничего не устанавливая**.
3. `open(slot)` / `apply(slot, state)` / `close(slot)`; `apply` — горячий путь, без блокирующих вызовов.
4. Вибрация от игры: вызвать `self._rumble(slot, large, small)` (можно из другого потока).
5. Одна строка в `server/plugins/__init__.py`: `registry.register("output", MyBackend)`.
6. Выбор — `output.backend: <id>` в `config/local.yaml`.

## Новый источник ввода (`InputSource`)

`mount(app, ctx)` — свои эндпоинты; `ctx.router.attach(DeviceInfo(kind=...))` — занять слот, `ctx.router.submit(slot, seq, state)` — ввод, `ctx.router.detach(slot)` — освободить. `kind` и `capabilities` уходят в `/api/v1/devices` как есть, UI строится по ним.

## Новый провайдер хранилища (`StorageProvider`)

`volumes()` → список `VolumeInfo`; регистрация `registry.register("storage", ...)`, выбор — `storage.provider` в конфиге.
