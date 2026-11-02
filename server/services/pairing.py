"""Сопряжение: одноразовый 6-значный код → токен. Храним только хэши токенов."""

import hashlib
import hmac
import json
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


class PairingError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code  # forbidden | rate_limited | internal
        self.message = message


@dataclass(slots=True)
class PairedDevice:
    device_id: str
    name: str
    token_hash: str


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class PairingService:
    def __init__(
        self,
        *,
        code_ttl_s: float = 300,
        max_attempts: int = 5,
        fixed_code: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_new_code: Callable[[str], None] | None = None,
        store_path: Path | None = None,
    ) -> None:
        """fixed_code — только для мок-сервера: постоянный многоразовый код для тестов PWA.
        on_new_code — показать новый код (консоль, QR на ТВ); вызывается при каждой смене.
        store_path — файл сопряжённых устройств (только хэши токенов), переживает перезапуск."""
        self._on_new_code = on_new_code
        self._ttl = code_ttl_s
        self._max_attempts = max_attempts
        self._fixed = fixed_code
        self._clock = clock
        self._code: str | None = None
        self._expires = 0.0
        self._attempts = 0
        self._devices: dict[str, PairedDevice] = {}
        self._store = store_path
        if store_path is not None and store_path.is_file():
            for item in json.loads(store_path.read_text(encoding="utf-8")):
                self._devices[item["device_id"]] = PairedDevice(
                    item["device_id"], item.get("name", ""), item["token_hash"]
                )
        # Синхронные HTTP-обработчики FastAPI идут в пуле потоков: проверка и погашение кода атомарны.
        self._lock = threading.RLock()

    def current_code(self) -> str:
        with self._lock:
            return self._current_code()

    def _current_code(self) -> str:
        if self._fixed is not None:
            return self._fixed
        if self._code is None or self._clock() >= self._expires:
            self._rotate()
        assert self._code is not None
        return self._code

    def _rotate(self) -> None:
        self._code = f"{secrets.randbelow(1_000_000):06d}"
        self._expires = self._clock() + self._ttl
        self._attempts = 0
        if self._on_new_code is not None:
            self._on_new_code(self._code)

    def pair(self, code: str, device_name: str = "") -> tuple[str, str]:
        """Возвращает (token, device_id). Токен отдаётся один раз и нигде не хранится."""
        with self._lock:
            return self._pair(code, device_name)

    def _pair(self, code: str, device_name: str) -> tuple[str, str]:
        expected = self._current_code()
        if not hmac.compare_digest(code.encode(), expected.encode()):
            if self._fixed is None:
                self._attempts += 1
                if self._attempts >= self._max_attempts:
                    self._rotate()
                    raise PairingError("rate_limited", "слишком много попыток, код обновлён")
            raise PairingError("forbidden", "неверный или просроченный код")
        if self._fixed is None:
            self._rotate()  # одноразовый: сразу новый код для следующего телефона
        token = secrets.token_urlsafe(32)
        device_id = secrets.token_hex(8)
        devices = {**self._devices, device_id: PairedDevice(device_id, device_name[:64], _hash(token))}
        self._save(devices)  # сначала файл: при ошибке записи память не меняется
        self._devices = devices
        return token, device_id

    def _save(self, devices: dict[str, PairedDevice]) -> None:
        if self._store is None:
            return
        try:
            self._store.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._store.with_suffix(".tmp")
            tmp.write_text(json.dumps([
                {"device_id": d.device_id, "name": d.name, "token_hash": d.token_hash}
                for d in devices.values()
            ], ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self._store)
        except OSError as e:
            raise PairingError("internal", f"не удалось сохранить сопряжение: {e.strerror or e}") from e

    def verify(self, token: str | None) -> PairedDevice | None:
        if not token:
            return None
        digest = _hash(token)
        with self._lock:
            devices = list(self._devices.values())
        for device in devices:
            if hmac.compare_digest(device.token_hash, digest):
                return device
        return None

    def revoke(self, device_id: str) -> None:
        with self._lock:
            devices = {k: v for k, v in self._devices.items() if k != device_id}
            self._save(devices)
            self._devices = devices
