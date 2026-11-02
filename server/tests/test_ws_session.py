"""Жизненный цикл WS-сессии на поддельном сокете: гонки и сбои, которые трудно поймать через TestClient."""

import asyncio
import json
from typing import Any

import pytest
from starlette.websockets import WebSocketDisconnect

from server.core.context import AppContext
from server.core.interfaces import PadState
from server.core.protocol import encode_pad
from server.plugins.input.pwa_ws import _Session, parse_hello


class FakeWS:
    def __init__(self, *, fail_send: bool = False, close_delay: float = 0.0) -> None:
        self.inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.sent: list[Any] = []
        self.closed: int | None = None
        self.fail_send = fail_send
        self.close_delay = close_delay

    async def accept(self) -> None: ...

    async def receive(self) -> dict[str, Any]:
        return await self.inbox.get()

    async def send_text(self, text: str) -> None:
        if self.fail_send:
            raise WebSocketDisconnect(1006)
        self.sent.append(json.loads(text))

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000) -> None:
        await asyncio.sleep(self.close_delay)
        self.closed = code
        self.inbox.put_nowait({"type": "websocket.disconnect", "code": code})

    def push_text(self, obj: dict[str, Any]) -> None:
        self.inbox.put_nowait({"type": "websocket.receive", "text": json.dumps(obj)})

    def push_bytes(self, data: bytes) -> None:
        self.inbox.put_nowait({"type": "websocket.receive", "bytes": data})


def hello(token: str) -> dict[str, Any]:
    return {"type": "hello", "token": token, "protocol_version": 1}


async def until(cond: Any, limit_s: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + limit_s
    while not cond():
        assert asyncio.get_running_loop().time() < deadline, "условие не выполнилось"
        await asyncio.sleep(0.005)


@pytest.fixture
def paired(ctx: AppContext) -> str:
    token, _ = ctx.pairing.pair("123456")
    return token


def test_welcome_send_failure_frees_slot(ctx: AppContext, paired: str) -> None:
    async def scenario() -> None:
        sessions: dict[str, _Session] = {}
        ws = FakeWS(fail_send=True)
        ws.push_text(hello(paired))
        await _Session(ws, ctx, sessions).run()  # type: ignore[arg-type]
        assert ctx.router.slots() == [] and sessions == {}

    asyncio.run(scenario())


def test_old_connection_frames_ignored_after_replace(ctx: AppContext, paired: str) -> None:
    async def scenario() -> None:
        sessions: dict[str, _Session] = {}
        out = ctx.router.backend
        old_ws = FakeWS(close_delay=0.2)  # закрытие старого сокета «висит»
        old_ws.push_text(hello(paired))
        old_task = asyncio.create_task(_Session(old_ws, ctx, sessions).run())  # type: ignore[arg-type]
        await until(lambda: len(ctx.router.slots()) == 1)

        new_ws = FakeWS()
        new_ws.push_text(hello(paired))
        new_task = asyncio.create_task(_Session(new_ws, ctx, sessions).run())  # type: ignore[arg-type]
        await until(lambda: any(isinstance(m, dict) and m.get("type") == "welcome" for m in new_ws.sent))

        old_ws.push_bytes(encode_pad(9999, PadState(buttons=0x1000)))
        await asyncio.sleep(0.05)
        assert out.states[0] != PadState(buttons=0x1000)
        new_ws.push_bytes(encode_pad(1, PadState(buttons=0x2000)))
        await until(lambda: out.states[0] == PadState(buttons=0x2000))

        await until(lambda: old_ws.closed == 4000)
        assert len(ctx.router.slots()) == 1
        new_ws.inbox.put_nowait({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(asyncio.gather(old_task, new_task), 2)
        assert ctx.router.slots() == [] and sessions == {}

    asyncio.run(scenario())


def test_concurrent_reconnects_keep_one_slot(ctx: AppContext, paired: str) -> None:
    async def scenario() -> None:
        sessions: dict[str, _Session] = {}
        socks = [FakeWS(close_delay=0.05) for _ in range(3)]
        for ws in socks:
            ws.push_text(hello(paired))
        tasks = [asyncio.create_task(_Session(ws, ctx, sessions).run()) for ws in socks]  # type: ignore[arg-type]
        await asyncio.sleep(0.3)
        assert len(ctx.router.slots()) == 1 and len(sessions) == 1
        assert sum(ws.closed == 4000 for ws in socks) == 2
        for ws in socks:
            ws.inbox.put_nowait({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(asyncio.gather(*tasks), 2)
        assert ctx.router.slots() == []

    asyncio.run(scenario())


def test_stuck_client_does_not_block_event_bus(ctx: AppContext, paired: str) -> None:
    class StuckWS(FakeWS):
        hang = False

        async def send_text(self, text: str) -> None:
            if self.hang:
                await asyncio.Event().wait()  # клиент перестал читать
            await super().send_text(text)

    async def scenario() -> None:
        ws = StuckWS()
        ws.push_text(hello(paired))
        task = asyncio.create_task(_Session(ws, ctx, {}).run())  # type: ignore[arg-type]
        await until(lambda: len(ctx.router.slots()) == 1)
        await asyncio.sleep(0.01)  # сессия подписалась на шину
        ws.hang = True
        await asyncio.wait_for(ctx.bus.publish("launch", {"appid": 1}), 0.2)
        await asyncio.wait_for(ctx.bus.publish("launch", {"appid": 2}), 0.2)
        ws.inbox.put_nowait({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, 1)
        assert ctx.router.slots() == []

    asyncio.run(scenario())


def test_send_failure_drops_session(ctx: AppContext, paired: str) -> None:
    """Сбой фоновой отправки (PING, событие) закрывает сессию и освобождает слот."""

    class BrokenBytesWS(FakeWS):
        async def send_bytes(self, data: bytes) -> None:
            raise ConnectionResetError

    async def scenario() -> None:
        sessions: dict[str, _Session] = {}
        ws = BrokenBytesWS()
        ws.push_text(hello(paired))
        task = asyncio.create_task(_Session(ws, ctx, sessions).run())  # type: ignore[arg-type]
        await until(lambda: len(ctx.router.slots()) == 1)
        ctx.router.backend.rumble(0, 100, 100)  # фоновая отправка упадёт
        await until(lambda: ctx.router.slots() == [] and sessions == {})
        await asyncio.wait_for(task, 1)
        assert ws.closed == 1011

    asyncio.run(scenario())


def test_event_flood_to_stuck_client_is_bounded(ctx: AppContext, paired: str) -> None:
    class StuckWS(FakeWS):
        hang = False

        async def send_text(self, text: str) -> None:
            if self.hang:
                await asyncio.Event().wait()
            await super().send_text(text)

    async def scenario() -> None:
        sessions: dict[str, _Session] = {}
        ws = StuckWS()
        ws.push_text(hello(paired))
        task = asyncio.create_task(_Session(ws, ctx, sessions).run())  # type: ignore[arg-type]
        await until(lambda: len(ctx.router.slots()) == 1)
        await asyncio.sleep(0.01)
        session = sessions[next(iter(sessions))]
        ws.hang = True
        for i in range(500):
            await ctx.bus.publish("launch", {"appid": i})
        assert len(session._tasks) <= 32
        await until(lambda: ctx.router.slots() == [] and sessions == {})
        await asyncio.wait_for(task, 1)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "msg",
    [
        {"type": "hello", "token": "t", "protocol_version": True},
        {"type": "hello", "token": "t", "protocol_version": 1.0},
        {"type": "hello", "token": "t", "protocol_version": 1, "extra": 1},
        {"type": "hello", "token": 5, "protocol_version": 1},
        {"type": "hello", "protocol_version": 1},
        {"type": "hello", "token": "t", "protocol_version": 1, "client": {"name": 123}},
        {"type": "hello", "token": "t", "protocol_version": 1, "client": {"name": "x" * 65}},
        {"type": "hello", "token": "t", "protocol_version": 1, "client": {"os": "ios"}},
        {"type": "hello", "token": "t", "protocol_version": 1, "client": {"name": None}},
        {"type": "hello", "token": "t", "protocol_version": 1, "client": {"platform": None}},
        {"type": "hello", "token": "t", "protocol_version": 1, "client": []},
        {"type": "welcome", "token": "t", "protocol_version": 1},
        [],
    ],
)
def test_parse_hello_rejects_invalid(msg: Any) -> None:
    assert parse_hello(json.dumps(msg)) is None


def test_parse_hello_accepts_valid() -> None:
    ok = {
        "type": "hello", "token": "t", "protocol_version": 1,
        "client": {"platform": "ios", "name": "iPhone"},
    }
    assert parse_hello(json.dumps(ok)) == ok
    assert parse_hello("не json") is None and parse_hello(None) is None
