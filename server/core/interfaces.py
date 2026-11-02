"""Точки расширения (docs/architecture.md, раздел 8).

Новый плагин = один файл в server/plugins/<kind>/ + регистрация в server/plugins/__init__.py.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from fastapi import FastAPI

    from server.core.context import AppContext


@dataclass(slots=True, frozen=True)
class PadState:
    """Снимок виртуального геймпада. Маска кнопок — как XInput wButtons."""

    buttons: int = 0
    lx: int = 0
    ly: int = 0
    rx: int = 0
    ry: int = 0
    lt: int = 0
    rt: int = 0


NEUTRAL = PadState()

RumbleHandler = Callable[[int, int, int], None]
"""(slot, large, small) — вызывается бэкендом, когда игра включает вибрацию."""


@dataclass(slots=True, frozen=True)
class DeviceInfo:
    """Описание источника ввода. kind задаёт плагин; ядро его не интерпретирует."""

    id: str
    kind: str
    name: str
    capabilities: tuple[str, ...] = ()
    mode: str = "route"


class OutputBackend(ABC):
    """Куда уходит ввод: виртуальный геймпад, клавиатура/мышь и т. п."""

    id: ClassVar[str]
    title: ClassVar[str]
    analog: ClassVar[bool]

    @classmethod
    def available(cls) -> bool:
        """Есть ли драйвер/зависимости на этой машине. Не должен ничего устанавливать."""
        return True

    def set_rumble_handler(self, handler: RumbleHandler | None) -> None:
        self._rumble = handler

    @abstractmethod
    def open(self, slot: int) -> None:
        """Создать устройство для слота."""

    @abstractmethod
    def apply(self, slot: int, state: PadState) -> None:
        """Применить снимок. Горячий путь: без блокировок и аллокаций сверх необходимого."""

    @abstractmethod
    def close(self, slot: int) -> None:
        """Отпустить всё и удалить устройство слота."""

    def shutdown(self) -> None:
        """Освободить ресурсы бэкенда целиком."""


class InputSource(ABC):
    """Откуда приходит ввод: PWA по WebSocket, WebRTC, физический геймпад."""

    id: ClassVar[str]

    @abstractmethod
    def mount(self, app: FastAPI, ctx: AppContext) -> None:
        """Подключить свои эндпоинты к приложению."""

    async def start(self, ctx: AppContext) -> None:
        """Фоновые задачи источника (опрос устройств и т. п.)."""

    async def stop(self) -> None:
        """Остановить фоновые задачи."""


@dataclass(slots=True)
class SteamLibraryInfo:
    id: str
    path: str
    game_count: int = 0
    size_bytes: int = 0


@dataclass(slots=True)
class VolumeInfo:
    id: str
    kind: str
    label: str
    mount: str
    total_bytes: int
    free_bytes: int
    available: bool
    libraries: list[SteamLibraryInfo] = field(default_factory=list)


class StorageProvider(ABC):
    """Диски и их библиотеки Steam (только чтение)."""

    id: ClassVar[str]

    @abstractmethod
    def volumes(self) -> list[VolumeInfo]:
        """Текущий список дисков."""
