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
    for _ in range(10):
        msg = recv(ws)
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
