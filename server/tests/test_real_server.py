"""Настоящий сервер (python -m server) на фикстурах: контракт, режим консоли, токены на диске."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.build import build_context
from server.core.eventbus import EventBus
from server.services.console_mode import ScriptConsoleMode
from server.services.pairing import PairingService
from server.tests.test_contract_http import check
from server.tests.test_steam import steam  # noqa: F401 — фикстура


@pytest.fixture
def real(tmp_path: Path, steam: Path) -> Any:  # noqa: F811
    codes: list[str] = []
    ctx, warnings = build_context(
        overrides={"server": {"serve_pwa": False}, "steam": {"path": str(steam)},
                   "output": {"backend": "mock"}},
        data_dir=tmp_path / "data",
        on_new_code=codes.append,
    )
    return ctx, warnings, codes


def pair(client: TestClient, code: str) -> dict[str, str]:
    token = client.post("/api/v1/pair", json={"code": code}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_real_server_follows_contract(real: Any) -> None:
    ctx, warnings, codes = real
    assert any("dry_run" in w for w in warnings)
    with TestClient(create_app(ctx)) as client:
        info = check(client.get("/api/v1/info"), "/info", "get")
        assert info["mock"] is False and info["features"]["system"] is False
        assert {b["id"] for b in info["capabilities"]["output_backends"]} == {"vigem", "mock"}
        auth = pair(client, ctx.pairing.current_code())
        games = check(client.get("/api/v1/games", headers=auth), "/games", "get")["games"]
        assert [g["name"] for g in games] == ["Lethal Company", "Terraria"]
        r = client.get("/api/v1/games/105600/cover", headers=auth)
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        vols = check(client.get("/api/v1/storage", headers=auth), "/storage", "get")["volumes"]
        assert sum(len(v["libraries"]) for v in vols) == 3
        for path, method in (("/system", "get"), ("/system/volume", "post")):
            r = client.request(method, f"/api/v1{path}", headers=auth, json={} if method == "post" else None)
            assert r.status_code == 404
            check(r, path, method)


def test_unavailable_backend_falls_back_with_warning(tmp_path: Path) -> None:
    ctx, warnings = build_context(
        overrides={"output": {"backend": "vigem"}, "steam": {"path": str(tmp_path / "none")}},
        data_dir=tmp_path,
    )
    from server.plugins.output.vigem import VigemOutput

    if VigemOutput.available():
        pytest.skip("ViGEmBus установлен — фолбэк не проверить")
    assert ctx.router.backend.id == "mock"
    assert any("vigem" in w for w in warnings) and any("Steam" in w for w in warnings)


def test_paired_devices_survive_restart(tmp_path: Path) -> None:
    store = tmp_path / "paired.json"
    p1 = PairingService(store_path=store)
    token, device_id = p1.pair(p1.current_code(), "iPhone")
    raw = store.read_text(encoding="utf-8")
    assert token not in raw and json.loads(raw)[0]["device_id"] == device_id
    p2 = PairingService(store_path=store)
    assert p2.verify(token).device_id == device_id
    p2.revoke(device_id)
    assert PairingService(store_path=store).verify(token) is None


def test_pair_save_failure_keeps_memory_consistent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path / "paired.json"
    p = PairingService(store_path=store)
    token, device_id = p.pair(p.current_code())

    def broken_replace(self: Path, target: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "replace", broken_replace)
    from server.services.pairing import PairingError

    with pytest.raises(PairingError) as e:
        p.pair(p.current_code())
    assert e.value.code == "internal" and len(p._devices) == 1
    with pytest.raises(PairingError):
        p.revoke(device_id)
    assert p.verify(token) is not None  # память не разошлась с файлом


def test_pair_endpoint_returns_contract_500(real: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, _, _ = real
    from server.services.pairing import PairingError

    def boom(code: str, name: str = "") -> Any:
        raise PairingError("internal", "диск полон")

    monkeypatch.setattr(ctx.pairing, "pair", boom)
    with TestClient(create_app(ctx)) as client:
        r = client.post("/api/v1/pair", json={"code": "123456"})
        assert r.status_code == 500
        assert check(r, "/pair", "post")["error"]["code"] == "internal"


class FakeRunner:
    def __init__(self, result: dict[str, Any] | None, code: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.result = result
        self.code = code

    async def __call__(self, args: list[str]) -> tuple[int, str]:
        self.calls.append(args)
        await asyncio.sleep(0.01)
        return self.code, "noise\n" + (json.dumps(self.result) if self.result else "")


def test_console_mode_uses_dry_run_by_default(tmp_path: Path) -> None:
    runner = FakeRunner({"ok": True, "state": "off", "steps": ["[dry-run] display"]})
    cm = ScriptConsoleMode(EventBus(), snapshot_path=tmp_path / "snap.json", runner=runner)

    async def scenario() -> None:
        assert (await cm.set(True)).state == "switching"
        await cm._task
        assert cm.status().state == "off" and "dry_run" in cm.status().message

    asyncio.run(scenario())
    assert "-DryRun" in runner.calls[0] and runner.calls[0][1] == "on"
    assert cm.last_steps == ["[dry-run] display"]


def test_console_mode_real_and_errors(tmp_path: Path) -> None:
    snap = tmp_path / "snap.json"

    async def scenario() -> None:
        ok = FakeRunner({"ok": True, "state": "on", "steps": []})
        cm = ScriptConsoleMode(EventBus(), dry_run=False, snapshot_path=snap, runner=ok)
        await cm.set(True)
        snap.write_text('{"complete": false}')  # прерванное переключение
        await cm._task
        assert cm.status().state == "error"
        snap.write_text('{"complete": true}')  # скрипт завершил снимок
        assert "-DryRun" not in ok.calls[0]
        assert cm.status().state == "on"

        bad = ScriptConsoleMode(EventBus(), dry_run=False, snapshot_path=snap,
                                runner=FakeRunner({"ok": False, "message": "TV not detected"}, 1))
        await bad.set(False)
        await bad._task
        s = bad.status()
        assert s.state == "error" and s.message == "TV not detected"

        silent = ScriptConsoleMode(EventBus(), snapshot_path=snap, runner=FakeRunner(None, 1))
        await silent.set(True)
        await silent._task
        assert silent.status().state == "error"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("result", "code"),
    [
        ({"ok": True, "steps": []}, 1),  # ok, но процесс упал
        ({"ok": True, "steps": None}, 0),  # неверный тип steps — не падаем
        ({"ok": "yes", "steps": []}, 0),
    ],
)
def test_console_mode_strict_result(tmp_path: Path, result: dict[str, Any], code: int) -> None:
    events: list[str] = []
    bus = EventBus()

    async def listen(name: str, data: dict[str, Any]) -> None:
        events.append(data["state"])

    bus.subscribe(listen)
    cm = ScriptConsoleMode(bus, snapshot_path=tmp_path / "s.json", runner=FakeRunner(result, code))

    async def scenario() -> None:
        await cm.set(True)
        await cm._task

    asyncio.run(scenario())
    ok = result.get("ok") is True and code == 0
    assert cm.status().state == ("off" if ok else "error")
    assert events[-1] == cm.status().state


def test_run_powershell_kills_process_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from server.services import console_mode

    class Proc:
        returncode = None
        killed = waited = False

        async def communicate(self) -> tuple[bytes, bytes]:
            await asyncio.sleep(10)
            return b"", b""

        def kill(self) -> None:
            Proc.killed = True

        async def wait(self) -> int:
            Proc.waited = True
            return -9

    async def fake_exec(*args: Any, **kwargs: Any) -> Proc:
        return Proc()

    monkeypatch.setattr(console_mode.asyncio, "create_subprocess_exec", fake_exec)

    async def scenario() -> None:
        task = asyncio.create_task(console_mode.run_powershell(["-Action", "on"]))
        await asyncio.sleep(0.01)
        task.cancel()  # как при остановке сервера
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert Proc.killed and Proc.waited


@pytest.mark.windows
def test_console_mode_script_dry_run_changes_nothing(tmp_path: Path) -> None:
    import sys

    if sys.platform != "win32":
        pytest.skip("только Windows")
    from server.services.console_mode import run_powershell

    snap = tmp_path / "snap.json"
    args = ["-Action", "on", "-DryRun", "-Force", "-SnapshotPath", str(snap)]
    code, out = asyncio.run(run_powershell(args))
    result = json.loads(out.strip().splitlines()[-1])
    assert code == 0 and result["ok"] and result["dry_run"]
    assert all(s.startswith("[dry-run]") for s in result["steps"])
    assert not snap.exists()
