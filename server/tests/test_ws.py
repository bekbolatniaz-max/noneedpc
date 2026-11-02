"""WebSocket /ws по contracts/protocol.md и ws-messages.schema.json."""

import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server.core.context import AppContext
from server.core.interfaces import NEUTRAL, PadState
from server.core.protocol import PING, PONG, decode_ping, encode_pad, encode_ping
from server.tests.schema import assert_valid, load_ws_schema

WS = load_ws_schema()


def hello(token: str, version: int = 1) -> str:
    return json.dumps({"type": "hello", "token": token, "protocol_version": version,
                       "client": {"platform": "test", "name": "pytest"}})


def recv_json(ws: Any) -> dict[str, Any]:
    msg = json.loads(ws.receive_text())
    assert_valid(msg, WS, WS)
    return msg


def welcome(ws: Any) -> dict[str, Any]:
    "welcome, затем событие player (номер игрока) — оно приходит сразу после рукопожатия."
    w = recv_json(ws)
    assert w["type"] == "welcome"
    player = recv_json(ws)
    assert player["name"] == "player" and player["data"]["number"] == w["slot"] + 1
    return w


def expect_close(ws: Any, code: int) -> None:
    with pytest.raises(WebSocketDisconnect) as e:
        ws.receive_text()
    assert e.value.code == code


def wait_for(cond: Any, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def test_hello_welcome_and_pad_state(client: TestClient, ctx: AppContext, token: str) -> None:
    out = ctx.router.backend
    with client.websocket_connect("/ws") as ws:
        ws.send_text(hello(token))
        w = welcome(ws)
        assert w["type"] == "welcome" and w["slot"] == 0 and w["deadman_ms"] == 250
        state = PadState(buttons=0x1000 | 0x0001, lx=12000, ly=-5000, rt=255)
        ws.send_bytes(encode_pad(1, state, flags=1))
        assert wait_for(lambda: out.states.get(0) == state)
        devices = client.get("/api/v1/devices", headers={"Authorization": f"Bearer {token}"}).json()
        assert devices["devices"][0]["kind"] == "pwa" and devices["devices"][0]["slot"] == 0
    # закрытие сокета сразу отпускает всё и освобождает слот
    assert wait_for(lambda: out.states.get(0) == NEUTRAL and not ctx.router.slots())


def test_bad_token_closes_4401(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_text(hello("x" * 43))
        assert recv_json(ws)["code"] == "auth_failed"
        expect_close(ws, 4401)


def test_protocol_mismatch_closes_4426(client: TestClient, token: str) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_text(hello(token, version=2))
        assert recv_json(ws)["code"] == "protocol_mismatch"
        expect_close(ws, 4426)


def test_binary_before_hello_rejected(client: TestClient, ctx: AppContext) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_bytes(encode_pad(1, PadState(buttons=0x1000)))
        assert recv_json(ws)["code"] == "bad_message"
        expect_close(ws, 4400)
    assert ctx.router.backend.history == []


def test_hello_timeout(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        assert recv_json(ws)["code"] == "timeout"  # hello_timeout_s=1 в conftest
        expect_close(ws, 4401)


def test_ping_gets_pong(client: TestClient, token: str) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_text(hello(token))
        welcome(ws)
        ws.send_bytes(encode_ping(PING, 42, 123456))
        assert decode_ping(ws.receive_bytes()) == (PONG, 42, 123456)


def test_server_pings_and_reports_stats(client: TestClient, token: str) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_text(hello(token))
        welcome(ws)
        kind, ping_id, t_ms = decode_ping(ws.receive_bytes())  # сервер пингует раз в секунду
        assert kind == PING
        ws.send_bytes(encode_ping(PONG, ping_id, t_ms))
        stats = recv_json(ws)
        assert stats["type"] == "stats"
        decode_ping(ws.receive_bytes())
        stats = recv_json(ws)
        assert stats["rtt_ms"] is not None and stats["rtt_ms"] >= 0


def test_garbage_frames_do_not_break_connection(client: TestClient, ctx: AppContext, token: str) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_text(hello(token))
        welcome(ws)
        ws.send_bytes(b"")
        ws.send_bytes(b"\x01\x00\x01")
        ws.send_bytes(b"\x7f" * 18)
        ws.send_text("не json")
        state = PadState(buttons=0x2000)
        ws.send_bytes(encode_pad(5, state))
        assert wait_for(lambda: ctx.router.backend.states.get(0) == state)


def test_old_seq_dropped(client: TestClient, ctx: AppContext, token: str) -> None:
    a, b = PadState(buttons=0x1000), PadState(buttons=0x2000)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(hello(token))
        welcome(ws)
        ws.send_bytes(encode_pad(10, a))
        ws.send_bytes(encode_pad(9, b))
        ws.send_bytes(encode_pad(11, a))
        assert wait_for(lambda: ctx.router.get(0) is not None and ctx.router.get(0).last_seq == 11)
        assert ctx.router.get(0).dropped == 1
        assert b not in [s for _, s in ctx.router.backend.history]


def test_deadman_over_ws(client: TestClient, ctx: AppContext, token: str) -> None:
    state = PadState(buttons=0x1000)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(hello(token))
        welcome(ws)
        ws.send_bytes(encode_pad(1, state))
        assert wait_for(lambda: ctx.router.backend.states.get(0) == state)
        t0 = time.monotonic()
        assert wait_for(lambda: ctx.router.backend.states.get(0) == NEUTRAL, timeout=1.0)
        assert 0.2 <= time.monotonic() - t0 <= 0.6


def test_same_device_reconnect_replaces_old(client: TestClient, ctx: AppContext, token: str) -> None:
    with client.websocket_connect("/ws") as old:
        old.send_text(hello(token))
        assert welcome(old)["slot"] == 0
        with client.websocket_connect("/ws") as new:
            new.send_text(hello(token))
            assert welcome(new)["slot"] == 0
            expect_close(old, 4000)
            assert len(ctx.router.slots()) == 1


def test_no_free_slot(client: TestClient, ctx: AppContext) -> None:
    ctx.router.max_slots = 1
    tokens = [client.post("/api/v1/pair", json={"code": "123456"}).json()["token"] for _ in range(2)]
    with client.websocket_connect("/ws") as first:
        first.send_text(hello(tokens[0]))
        welcome(first)
        with client.websocket_connect("/ws") as second:
            second.send_text(hello(tokens[1]))
            assert recv_json(second)["code"] == "no_free_slot"
            expect_close(second, 4409)


def test_events_broadcast(client: TestClient, token: str) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_text(hello(token))
        welcome(ws)
        client.post("/api/v1/games/1001/launch", headers={"Authorization": f"Bearer {token}"})
        msg = recv_json(ws)
        assert msg == {"type": "event", "name": "launch", "data": {"appid": 1001, "status": "launching"}}


def test_rumble_forwarded(client: TestClient, ctx: AppContext, token: str) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_text(hello(token))
        welcome(ws)
        ctx.router.backend.rumble(0, 200, 30)
        assert ws.receive_bytes() == bytes([0x10, 0, 200, 30])
