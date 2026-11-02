"""Фальшивые сервисы для мок-сервера. Ничего не меняют в системе."""

import asyncio
import logging
import struct
import zlib

from server.core.eventbus import EventBus
from server.services.base import (
    Conflict,
    ConsoleModeStatus,
    Cover,
    Game,
    NotFound,
    PowerAction,
)

log = logging.getLogger(__name__)

GB = 1024**3


def _png(rgb: tuple[int, int, int], w: int = 46, h: int = 21) -> bytes:
    """Однотонная PNG-заглушка обложки (пропорции капсулы Steam 460×215)."""
    row = b"\x00" + bytes(rgb) * w
    raw = zlib.compress(row * h)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


class MockLibrary:
    def __init__(self) -> None:
        self._games = [
            Game(1001, "Мок: Платформер", "lib-0", 2 * GB, True, True),
            Game(1002, "Мок: Гонки", "lib-0", 6 * GB, True, True),
            Game(1003, "Мок: Головоломка без обложки", "lib-0", 300 * 1024**2, True, False),
            Game(1004, "Мок: Игра на отключённом диске", "lib-1", 40 * GB, False, True),
        ]
        colors = [(220, 80, 60), (60, 140, 220), (0, 0, 0), (120, 120, 120)]
        self._covers = {g.appid: _png(c) for g, c in zip(self._games, colors, strict=True) if g.has_cover}

    def games(self) -> list[Game]:
        return list(self._games)

    def find(self, appid: int) -> Game | None:
        return next((g for g in self._games if g.appid == appid), None)

    def cover(self, appid: int) -> Cover | None:
        data = self._covers.get(appid)
        return Cover(data, "image/png") if data else None


class MockLauncher:
    def __init__(self, library: MockLibrary, bus: EventBus) -> None:
        self._library = library
        self._bus = bus

    async def launch(self, appid: int) -> None:
        game = self._library.find(appid)
        if game is None:
            raise NotFound(f"игра {appid} не найдена")
        if not game.available:
            raise Conflict("диск с игрой отключён")
        log.info("мок: запуск %s", game.name)
        await self._bus.publish("launch", {"appid": appid, "status": "launching"})


class MockConsoleMode:
    def __init__(self, bus: EventBus, switch_delay_s: float = 0.5) -> None:
        self._bus = bus
        self._delay = switch_delay_s
        self._status = ConsoleModeStatus("off", None)
        self._task: asyncio.Task[None] | None = None

    def status(self) -> ConsoleModeStatus:
        return self._status

    async def set(self, on: bool) -> ConsoleModeStatus:
        if self._status.state == "switching":
            raise Conflict("режим уже переключается")
        target = "on" if on else "off"
        if self._status.state == target:
            return self._status
        self._status = ConsoleModeStatus("switching", self._status.display_profile)
        await self._bus.publish("console_mode", {"state": "switching"})
        self._task = asyncio.create_task(self._finish(on))
        return self._status

    async def _finish(self, on: bool) -> None:
        await asyncio.sleep(self._delay)
        self._status = ConsoleModeStatus("on", "mock-tv") if on else ConsoleModeStatus("off", None)
        await self._bus.publish("console_mode", {"state": self._status.state})


class MockSystem:
    def __init__(self) -> None:
        self._volume = 40
        self._muted = False
        self.power_requests: list[str] = []

    def volume(self) -> tuple[int, bool]:
        return self._volume, self._muted

    async def set_volume(self, volume: int | None, muted: bool | None) -> tuple[int, bool]:
        if volume is not None:
            self._volume = volume
        if muted is not None:
            self._muted = muted
        return self.volume()

    async def power(self, action: PowerAction) -> None:
        # Мок ничего не выключает — только запоминает.
        self.power_requests.append(action)
        log.info("мок: питание %s (ничего не делаем)", action)
