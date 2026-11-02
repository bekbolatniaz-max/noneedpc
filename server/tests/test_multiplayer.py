"""Несколько телефонов: отдельные слоты, номер игрока, код приглашения."""

import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from server.core.context import AppContext
from server.core.interfaces import PadState
from server.core.protocol import encode_pad
from server.plugins.output.vigem import VigemOutput
from server.services.pairing import PairingService
from server.tests.schema import assert_valid, load_ws_schema
from server.tests.test_contract_http import check

WS = load_ws_schema()


def hello(token: str) -> str:
    return json.dumps({"type": "hello", "token": token, "protocol_version": 1})


def recv(ws: Any) -> dict[str, Any]:
    msg = json.loads(ws.receive_text())
    assert_valid(msg, WS, WS)
    return msg


def recv_until(ws: Any, name: str) -> dict[str, Any]:
    """Ждёт событие по имени, пропуская остальные текстовые и бинарные (PING) кадры."""
    for _ in range(20):
        raw = ws.receive()
        if not raw.get("text"):
            continue
        msg = json.loads(raw["text"])
        assert_valid(msg, WS, WS)
        if msg.get("name") == name:
            return msg
    raise AssertionError(f"нет события {name}")


def test_two_phones_two_slots_independent_input(client: TestClient, ctx: AppContext) -> None:
    t1 = client.post("/api/v1/pair", json={"code": "123456", "device_name": "iPhone"}).json()["token"]
    t2 = client.post("/api/v1/pair", json={"code": "123456", "device_name": "Pixel"}).json()["token"]
    out = ctx.router.backend
    with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
        a.send_text(hello(t1))
        b.send_text(hello(t2))
        wa, wb = recv(a), recv(b)
        assert {wa["slot"], wb["slot"]} == {0, 1}
        assert recv_until(a, "player")["data"]["number"] == wa["slot"] + 1
        assert recv_until(b, "player")["data"]["number"] == wb["slot"] + 1
        sa, sb = PadState(buttons=0x1000, lx=100), PadState(buttons=0x2000, lx=-100)
        a.send_bytes(encode_pad(1, sa))
        b.send_bytes(encode_pad(1, sb))
        deadline = time.monotonic() + 1
        both = lambda: out.states.get(wa["slot"]) == sa and out.states.get(wb["slot"]) == sb  # noqa: E731
        while time.monotonic() < deadline and not both():
            time.sleep(0.01)
        assert out.states[wa["slot"]] == sa and out.states[wb["slot"]] == sb
        devices = client.get("/api/v1/devices", headers={"Authorization": f"Bearer {t1}"}).json()["devices"]
        assert {d["name"] for d in devices} == {"iPhone", "Pixel"}


def test_player_number_change_is_reannounced(client: TestClient, ctx: AppContext, token: str) -> None:
    out = ctx.router.backend
    numbers = {0: None}
    out.player_number = lambda slot: numbers.get(slot)  # Windows ещё не назначил номер
    with client.websocket_connect("/ws") as ws:
        ws.send_text(hello(token))
        assert recv(ws)["type"] == "welcome"
        time.sleep(0.3)
        numbers[0] = 3  # назначил позже
        assert recv_until(ws, "player")["data"]["number"] == 3
        numbers[0] = 2  # и поменял
        assert recv_until(ws, "player")["data"]["number"] == 2


def test_devices_event_when_another_phone_joins_or_leaves(client: TestClient) -> None:
    t1 = client.post("/api/v1/pair", json={"code": "123456"}).json()["token"]
    t2 = client.post("/api/v1/pair", json={"code": "123456"}).json()["token"]
    with client.websocket_connect("/ws") as a:
        a.send_text(hello(t1))
        recv(a)
        with client.websocket_connect("/ws") as b:
            b.send_text(hello(t2))
            recv(b)
            assert recv_until(a, "devices")["data"] == {}
        assert recv_until(a, "devices")["data"] == {}


def test_invite_code_requires_auth(client: TestClient, auth: dict[str, str]) -> None:
    r = client.get("/api/v1/pair/code")
    assert r.status_code == 401
    check(r, "/pair/code", "get")
    body = check(client.get("/api/v1/pair/code", headers=auth), "/pair/code", "get")
    assert body == {"code": "123456", "expires_in_s": None}  # мок: постоянный код


def test_code_info_rotating() -> None:
    t = [0.0]
    p = PairingService(code_ttl_s=300, clock=lambda: t[0])
    code, left = p.code_info()
    assert left == 300
    t[0] = 100
    assert p.code_info() == (code, 200)
    p.pair(code)  # успешное сопряжение сразу выдаёт новый код с полным TTL
    new, left = p.code_info()
    assert new == p.current_code() and left == 300


@pytest.mark.windows
def test_real_two_phones_two_xinput_pads(tmp_path: Any) -> None:
    """E2E на настоящем ViGEm: два телефона → два независимых XInput-геймпада с верными номерами."""
    import ctypes
    import sys

    if sys.platform != "win32" or not VigemOutput.available():
        pytest.skip("нужны Windows и ViGEmBus")
    from server.app import create_app
    from server.build import build_context

    class Gamepad(ctypes.Structure):
        _fields_ = [("wButtons", ctypes.c_ushort), ("bLeftTrigger", ctypes.c_ubyte),
                    ("bRightTrigger", ctypes.c_ubyte), ("sThumbLX", ctypes.c_short),
                    ("sThumbLY", ctypes.c_short), ("sThumbRX", ctypes.c_short), ("sThumbRY", ctypes.c_short)]

    class State(ctypes.Structure):
        _fields_ = [("dwPacketNumber", ctypes.c_uint), ("Gamepad", Gamepad)]

    xinput = ctypes.windll.LoadLibrary("XInput1_4.dll")

    def pads() -> dict[int, tuple[int, int]]:
        out = {}
        for i in range(4):
            s = State()
            if xinput.XInputGetState(i, ctypes.byref(s)) == 0:
                out[i] = (s.Gamepad.wButtons, s.Gamepad.sThumbLX)
        return out

    before = set(pads())
    if len(before) > 2:
        pytest.skip("заняты XInput-слоты: нужно минимум 2 свободных")
    ctx, _ = build_context(overrides={"server": {"serve_pwa": False}}, data_dir=tmp_path)
    assert ctx.router.backend.id == "vigem"
    with TestClient(create_app(ctx)) as c:
        t1 = c.post("/api/v1/pair", json={"code": ctx.pairing.current_code()}).json()["token"]
        t2 = c.post("/api/v1/pair", json={"code": ctx.pairing.current_code()}).json()["token"]
        with c.websocket_connect("/ws") as a, c.websocket_connect("/ws") as b:
            a.send_text(hello(t1))
            b.send_text(hello(t2))
            recv(a), recv(b)
            na = recv_until(a, "player")["data"]["number"]
            nb = recv_until(b, "player")["data"]["number"]
            new = set(pads()) - before
            assert {na - 1, nb - 1} == new  # номера на телефонах = XInput-индексы
            a.send_bytes(encode_pad(1, PadState(buttons=0x1000, lx=30000)))
            b.send_bytes(encode_pad(1, PadState(buttons=0x2000, lx=-30000)))
            time.sleep(0.1)
            state = pads()
            assert state[na - 1] == (0x1000, 30000) and state[nb - 1] == (0x2000, -30000)
        time.sleep(0.5)
        assert set(pads()) == before


@pytest.mark.windows
def test_real_vigem_player_number() -> None:
    if not VigemOutput.available():
        pytest.skip("ViGEmBus не установлен")
    out = VigemOutput()
    out.open(0)
    try:
        number = None
        for _ in range(30):
            number = out.player_number(0)
            if number:
                break
            time.sleep(0.1)
        assert number in (1, 2, 3, 4)
    finally:
        out.close(0)
