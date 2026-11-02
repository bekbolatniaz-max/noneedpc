"""Steam: парсер VDF и библиотека на фикстурах (без настоящего Steam)."""

import asyncio
from pathlib import Path

import pytest

from server.core.eventbus import EventBus
from server.plugins.storage.steam_volumes import SteamVolumes
from server.services import vdf
from server.services.base import Conflict, NotFound
from server.services.steam import SteamLauncher, SteamLibrary


def test_vdf_parses_nested_escapes_and_comments() -> None:
    text = '\ufeff// comment\n"Root"\n{\n\t"path"\t\t"C:\\\\Games\\\\Steam"\n\t"q"\t"say \\"hi\\""\n' \
           '\t"apps"\n\t{\n\t\t"105600"\t\t"1"\n\t}\n}\n'
    data = vdf.loads(text)
    assert data == {"Root": {"path": "C:\\Games\\Steam", "q": 'say "hi"', "apps": {"105600": "1"}}}


@pytest.mark.parametrize("bad", ['"a" {', '"a" "b" }', '{ "a" "b" }', '"a" "unterminated'])
def test_vdf_rejects_broken(bad: str) -> None:
    with pytest.raises(vdf.VdfError):
        vdf.loads(bad)


def manifest(appid: int, name: str, flags: int = 4, size: int = 1000) -> str:
    return (f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"name"\t\t"{name}"\n'
            f'\t"StateFlags"\t\t"{flags}"\n\t"SizeOnDisk"\t\t"{size}"\n}}\n')


@pytest.fixture
def steam(tmp_path: Path) -> Path:
    root = tmp_path / "Steam"
    second = tmp_path / "Games2"
    gone = tmp_path / "Unplugged"  # библиотека на отключённом диске
    (root / "steamapps").mkdir(parents=True)
    (second / "steamapps").mkdir(parents=True)
    esc = lambda p: str(p).replace("\\", "\\\\")  # noqa: E731
    (root / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n'
        f'\t"0"\n\t{{\n\t\t"path"\t\t"{esc(root)}"\n\t}}\n'
        f'\t"1"\n\t{{\n\t\t"path"\t\t"{esc(second)}"\n\t}}\n'
        f'\t"2"\n\t{{\n\t\t"path"\t\t"{esc(gone)}"\n\t}}\n'
        '}\n', encoding="utf-8")
    apps = root / "steamapps"
    (apps / "appmanifest_105600.acf").write_text(manifest(105600, "Terraria", size=800), encoding="utf-8")
    (apps / "appmanifest_228980.acf").write_text(manifest(228980, "Steamworks Common Redistributables"),
                                                 encoding="utf-8")
    (apps / "appmanifest_7.acf").write_text(manifest(7, "Updating", flags=6 & ~4 | 2), encoding="utf-8")
    (apps / "appmanifest_8.acf").write_text("garbage {", encoding="utf-8")
    (second / "steamapps" / "appmanifest_1966720.acf").write_text(manifest(1966720, "Lethal Company", size=5),
                                                                  encoding="utf-8")
    cache = root / "appcache" / "librarycache"
    (cache / "105600").mkdir(parents=True)
    (cache / "105600" / "header.jpg").write_bytes(b"\xff\xd8jpeg")
    (cache / "1966720_library_600x900.jpg").write_bytes(b"\xff\xd8old")
    return root


def test_library_lists_installed_games_only(steam: Path) -> None:
    lib = SteamLibrary(steam)
    games = lib.games()
    assert [(g.appid, g.name, g.library_id, g.size_bytes) for g in games] == [
        (1966720, "Lethal Company", "lib-1", 5),
        (105600, "Terraria", "lib-0", 800),
    ]
    assert all(g.has_cover for g in games)


def test_hidden_appids_from_config(steam: Path) -> None:
    assert [g.appid for g in SteamLibrary(steam, {1966720}).games()] == [105600]


def test_covers(steam: Path) -> None:
    lib = SteamLibrary(steam)
    assert lib.cover(105600).data == b"\xff\xd8jpeg"
    assert lib.cover(1966720).media_type == "image/jpeg"
    assert lib.cover(999) is None


def test_libraries_and_volumes(steam: Path) -> None:
    lib = SteamLibrary(steam)
    libs = {x.id: x for x in lib.libraries()}
    assert libs["lib-0"].game_count == 1 and libs["lib-0"].size_bytes == 800
    assert libs["lib-2"].game_count == 0
    vols = SteamVolumes(lib).volumes()
    assert sum(len(v.libraries) for v in vols) == 3
    assert all(v.available for v in vols)  # tmp_path на одном существующем диске


def test_no_steam_is_empty() -> None:
    lib = SteamLibrary(None)
    assert lib.games() == [] and lib.libraries() == [] and lib.cover(1) is None


def test_launcher(steam: Path) -> None:
    opened: list[str] = []
    events: list[tuple[str, dict]] = []
    bus = EventBus()

    async def listen(name: str, data: dict) -> None:
        events.append((name, data))

    bus.subscribe(listen)
    clock = [0.0]
    launcher = SteamLauncher(SteamLibrary(steam), bus, opener=opened.append, clock=lambda: clock[0])

    async def scenario() -> None:
        await launcher.launch(105600)
        with pytest.raises(Conflict):
            await launcher.launch(105600)  # двойное нажатие
        clock[0] = 10
        await launcher.launch(105600)
        with pytest.raises(NotFound):
            await launcher.launch(228980)  # скрытый

    asyncio.run(scenario())
    assert opened == ["steam://rungameid/105600"] * 2
    assert events[0] == ("launch", {"appid": 105600, "status": "launching"})


def test_launcher_open_failure_is_conflict(steam: Path) -> None:
    def broken(url: str) -> None:
        raise OSError("нет steam")

    launcher = SteamLauncher(SteamLibrary(steam), EventBus(), opener=broken)
    with pytest.raises(Conflict):
        asyncio.run(launcher.launch(105600))


def test_old_libraryfolders_format(tmp_path: Path) -> None:
    root, extra = tmp_path / "Steam", tmp_path / "D"
    (root / "steamapps").mkdir(parents=True)
    (extra / "steamapps").mkdir(parents=True)
    extra_esc = str(extra).replace("\\", "\\\\")
    (root / "steamapps" / "libraryfolders.vdf").write_text(
        f'"LibraryFolders"\n{{\n\t"TimeNextStatsReport"\t"1"\n\t"1"\t"{extra_esc}"\n}}\n',
        encoding="utf-8")
    (extra / "steamapps" / "appmanifest_42.acf").write_text(manifest(42, "Old"), encoding="utf-8")
    (root / "steamapps" / "appmanifest_7.acf").write_text(manifest(7, "Main"), encoding="utf-8")
    lib = SteamLibrary(root)
    # основная папка Steam в старом формате не перечислена, но остаётся библиотекой
    assert sorted((g.appid, g.library_id) for g in lib.games()) == [(7, "lib-0"), (42, "lib-1")]


def test_malformed_appstate_is_skipped(steam: Path) -> None:
    (steam / "steamapps" / "appmanifest_5.acf").write_text('"AppState" "broken"', encoding="utf-8")
    assert 5 not in {g.appid for g in SteamLibrary(steam).games()}


def test_negative_numbers_in_manifest(steam: Path) -> None:
    apps = steam / "steamapps"
    (apps / "appmanifest_neg.acf").write_text(manifest(-1, "Neg"), encoding="utf-8")
    (apps / "appmanifest_9.acf").write_text(manifest(9, "Size", size=-5), encoding="utf-8")
    games = {g.appid: g for g in SteamLibrary(steam).games()}
    assert -1 not in games and games[9].size_bytes == 0


def test_cooldown_is_per_game(steam: Path) -> None:
    opened: list[str] = []
    launcher = SteamLauncher(SteamLibrary(steam), EventBus(), opener=opened.append, clock=lambda: 0.0)

    async def scenario() -> None:
        await launcher.launch(105600)
        await launcher.launch(1966720)
        with pytest.raises(Conflict):
            await launcher.launch(105600)  # A→B→A: повтор A всё ещё блокируется

    asyncio.run(scenario())
    assert len(opened) == 2


@pytest.mark.windows
def test_real_steam_install_is_readable() -> None:
    from server.services.steam import find_steam_path

    path = find_steam_path()
    if path is None:
        pytest.skip("Steam не установлен")
    lib = SteamLibrary(path)
    assert lib.libraries()
    for g in lib.games():
        assert g.appid > 0 and g.name
