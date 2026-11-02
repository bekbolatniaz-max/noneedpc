"""Steam: библиотеки, игры, обложки (только чтение манифестов) и запуск через steam:// URL."""

import asyncio
import logging
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from server.core.eventbus import EventBus
from server.core.interfaces import SteamLibraryInfo
from server.services import vdf
from server.services.base import Conflict, Cover, Game, NotFound

log = logging.getLogger(__name__)

STATE_FULLY_INSTALLED = 4
# Не игры: Steamworks Common Redistributables, Steam Linux Runtime, Proton и т. п.
DEFAULT_HIDDEN_APPIDS = {228980, 1070560, 1391110, 1628350, 1493710, 2180100, 2348590}
COVER_NAMES = ("header.jpg", "library_header.jpg", "library_600x900.jpg")


def find_steam_path(configured: str | None = None) -> Path | None:
    if configured:
        p = Path(configured)
        return p if p.is_dir() else None
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                value, _ = winreg.QueryValueEx(key, "SteamPath")
            p = Path(value)
            return p if p.is_dir() else None
        except OSError:
            return None
    for p in (Path.home() / ".steam/steam", Path.home() / ".local/share/Steam"):
        if p.is_dir():
            return p
    return None


def _same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


@dataclass(slots=True)
class _Library:
    id: str
    path: Path


class SteamLibrary:
    """Каждый запрос перечитывает манифесты (их единицы, это миллисекунды): кэш не устаревает."""

    def __init__(self, steam_path: Path | None, hidden_appids: set[int] | None = None) -> None:
        self.steam_path = steam_path
        self.hidden = DEFAULT_HIDDEN_APPIDS | (hidden_appids or set())

    def _libraries(self) -> list[_Library]:
        if self.steam_path is None:
            return []
        vdf_path = self.steam_path / "steamapps" / "libraryfolders.vdf"
        try:
            data = vdf.loads(vdf_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, vdf.VdfError) as e:
            log.warning("не прочитан %s: %s", vdf_path, e)
            return [_Library("lib-0", self.steam_path)]
        folders = data.get("libraryfolders") or data.get("LibraryFolders") or {}
        libs = []
        if isinstance(folders, dict):
            for key, entry in folders.items():
                # Новый формат: "1" { "path" "D:\\SteamLibrary" }; старый: "1" "D:\\SteamLibrary".
                path = entry.get("path") if isinstance(entry, dict) else entry
                if key.isdigit() and isinstance(path, str) and path:
                    libs.append(_Library(f"lib-{key}", Path(path)))
        # В старом формате основная папка Steam не перечислена — она библиотека всегда.
        if not any(_same_path(lib.path, self.steam_path) for lib in libs):
            libs.insert(0, _Library("lib-0", self.steam_path))
        return libs

    def _games_with_size(self) -> list[Game]:
        games: dict[int, Game] = {}
        for lib in self._libraries():
            apps = lib.path / "steamapps"
            if not apps.is_dir():
                continue  # диск отключён — игры этой библиотеки появятся снова, когда подключат
            for acf in apps.glob("appmanifest_*.acf"):
                game = self._parse_manifest(acf, lib.id)
                if game is not None:
                    games[game.appid] = game
        return sorted(games.values(), key=lambda g: g.name.casefold())

    def _parse_manifest(self, acf: Path, library_id: str) -> Game | None:
        try:
            state = vdf.loads(acf.read_text(encoding="utf-8", errors="replace")).get("AppState", {})
            if not isinstance(state, dict):
                raise ValueError("AppState не блок")
            appid = int(state["appid"])
            flags = int(state.get("StateFlags", "0"))
        except (OSError, vdf.VdfError, KeyError, ValueError, TypeError) as e:
            log.warning("пропущен манифест %s: %s", acf.name, e)
            return None
        if appid < 1 or appid in self.hidden or not flags & STATE_FULLY_INSTALLED:
            return None
        name = str(state.get("name") or f"App {appid}")
        try:
            size = max(0, int(state.get("SizeOnDisk", "0")))
        except (ValueError, TypeError):
            size = 0
        return Game(appid, name, library_id, size, True, self._cover_path(appid) is not None)

    def games(self) -> list[Game]:
        return self._games_with_size()

    def find(self, appid: int) -> Game | None:
        return next((g for g in self.games() if g.appid == appid), None)

    def libraries(self) -> list[SteamLibraryInfo]:
        games = self.games()
        out = []
        for lib in self._libraries():
            mine = [g for g in games if g.library_id == lib.id]
            out.append(SteamLibraryInfo(lib.id, str(lib.path), len(mine), sum(g.size_bytes for g in mine)))
        return out

    def _cover_path(self, appid: int) -> Path | None:
        if self.steam_path is None:
            return None
        cache = self.steam_path / "appcache" / "librarycache"
        candidates = [cache / str(appid) / name for name in COVER_NAMES]
        candidates += [cache / f"{appid}_{name}" for name in COVER_NAMES]  # старая плоская раскладка
        return next((p for p in candidates if p.is_file()), None)

    def cover(self, appid: int) -> Cover | None:
        path = self._cover_path(appid)
        if path is None:
            return None
        media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return Cover(path.read_bytes(), media)


def _open_url(url: str) -> None:
    if sys.platform == "win32":
        os.startfile(url)  # noqa: S606 — только steam:// URL, собранный из целого appid
    else:
        raise OSError("запуск Steam поддерживается только на Windows")


class SteamLauncher:
    def __init__(
        self,
        library: SteamLibrary,
        bus: EventBus,
        *,
        opener: Callable[[str], None] = _open_url,
        cooldown_s: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._library = library
        self._bus = bus
        self._open = opener
        self._cooldown = cooldown_s
        self._clock = clock
        self._last: dict[int, float] = {}  # appid → время последнего запуска

    async def launch(self, appid: int) -> None:
        # Чтение манифестов и ShellExecute — в пуле потоков: event loop держит deadman 250 мс.
        game = await asyncio.to_thread(self._library.find, appid)
        if game is None:
            raise NotFound(f"игра {appid} не установлена")
        if not game.available:
            raise Conflict("диск с игрой отключён")
        now = self._clock()
        last = self._last.get(appid)
        if last is not None and now - last < self._cooldown:
            raise Conflict("игра уже запускается")
        self._last[appid] = now
        try:
            await asyncio.to_thread(self._open, f"steam://rungameid/{int(appid)}")
        except OSError as e:
            raise Conflict(f"не удалось открыть Steam: {e}") from e
        log.info("запуск %s (%d)", game.name, appid)
        await self._bus.publish("launch", {"appid": appid, "status": "launching"})
