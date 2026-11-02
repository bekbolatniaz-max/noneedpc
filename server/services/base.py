"""Интерфейсы сервисов. M0: реализации в server/mock/, M1: настоящие (Steam, PowerShell)."""

from dataclasses import dataclass
from typing import Literal, Protocol


class ServiceError(Exception):
    """Ошибка, которую HTTP-слой отдаёт клиенту как {"error": {code, message}}."""

    status = 400
    code = "bad_request"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFound(ServiceError):
    status = 404
    code = "not_found"


class Conflict(ServiceError):
    status = 409
    code = "conflict"


@dataclass(slots=True)
class Game:
    appid: int
    name: str
    library_id: str
    size_bytes: int
    available: bool
    has_cover: bool


@dataclass(slots=True)
class Cover:
    data: bytes
    media_type: str


class Library(Protocol):
    def games(self) -> list[Game]: ...
    def cover(self, appid: int) -> Cover | None: ...


class Launcher(Protocol):
    async def launch(self, appid: int) -> None:
        """NotFound — игры нет, Conflict — игра недоступна или уже запускается."""
        ...


ConsoleState = Literal["off", "on", "switching", "error"]


@dataclass(slots=True)
class ConsoleModeStatus:
    state: ConsoleState
    display_profile: str | None = None
    message: str | None = None


class ConsoleModeService(Protocol):
    def status(self) -> ConsoleModeStatus: ...
    async def set(self, on: bool) -> ConsoleModeStatus:
        """Начать переключение; Conflict — уже переключается."""
        ...


PowerAction = Literal["sleep", "restart", "shutdown"]


class SystemService(Protocol):
    def volume(self) -> tuple[int, bool]: ...
    async def set_volume(self, volume: int | None, muted: bool | None) -> tuple[int, bool]: ...
    async def power(self, action: PowerAction) -> None: ...
