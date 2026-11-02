"""PWA по WebSocket (транспорт v1): hello → welcome → бинарные снимки, PING/PONG, события."""

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from server.core.context import AppContext
from server.core.interfaces import DeviceInfo, InputSource
from server.core.protocol import (
    PAD_STATE,
    PING,
    PONG,
    PROTOCOL_VERSION,
    FrameError,
    decode_pad,
    decode_ping,
    encode_ping,
    encode_rumble,
)

log = logging.getLogger(__name__)

CLOSE_REPLACED = 4000
CLOSE_BAD_MESSAGE = 4400
CLOSE_AUTH = 4401
CLOSE_NO_SLOT = 4409
CLOSE_VERSION = 4426

SEND_TIMEOUT_S = 2.0
MAX_PENDING_SENDS = 32  # клиент, который не читает, отключаем, а не копим ему очередь

_HELLO_KEYS = {"type", "token", "protocol_version", "client"}
_CLIENT_LIMITS = {"platform": 32, "name": 64}


def _now_ms() -> int:
    return int(time.monotonic() * 1000) & 0xFFFFFFFF


def parse_hello(text: str | None) -> dict[str, Any] | None:
    """Строгая проверка hello по contracts/ws-messages.schema.json. None — сообщение неверное."""
    try:
        msg = json.loads(text or "")
    except ValueError:
        return None
    if not isinstance(msg, dict) or msg.get("type") != "hello" or not set(msg) <= _HELLO_KEYS:
        return None
    if not isinstance(msg.get("token"), str):
        return None
    version = msg.get("protocol_version")
    if not isinstance(version, int) or isinstance(version, bool):
        return None
    client = msg.get("client", {})
    if not isinstance(client, dict) or not set(client) <= set(_CLIENT_LIMITS):
        return None
    for key, limit in _CLIENT_LIMITS.items():
        if key in client and (not isinstance(client[key], str) or len(client[key]) > limit):
            return None
    return msg


def _log_task_error(task: "asyncio.Task[Any]") -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None and not isinstance(exc, WebSocketDisconnect | ConnectionError):
        log.warning("фоновая задача WS упала: %r", exc)


class PwaWsInput(InputSource):
    id = "pwa_ws"

    def __init__(self) -> None:
        self._sessions: dict[str, _Session] = {}

    def mount(self, app: FastAPI, ctx: AppContext) -> None:
        @app.websocket("/ws")
        async def ws_endpoint(ws: WebSocket) -> None:
            await _Session(ws, ctx, self._sessions).run()


class _Session:
    def __init__(self, ws: WebSocket, ctx: AppContext, sessions: dict[str, "_Session"]) -> None:
        self.ws = ws
        self.ctx = ctx
        self.sessions = sessions
        self.slot: int | None = None
        self.device_id: str | None = None
        self.rtt_ms: float | None = None
        self.bad_frames = 0
        self._ping_id = 0
        self._send_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closed = False

    # --- отправка (из нескольких задач, поэтому под замком) ---

    async def _locked(self, send: Any, payload: Any) -> None:
        async with self._send_lock:
            await send(payload)

    async def _send_bytes(self, data: bytes) -> None:
        # Таймаут покрывает и ожидание замка, и саму отправку.
        await asyncio.wait_for(self._locked(self.ws.send_bytes, data), SEND_TIMEOUT_S)

    async def _send_json(self, obj: dict[str, Any]) -> None:
        text = json.dumps(obj, ensure_ascii=False)
        await asyncio.wait_for(self._locked(self.ws.send_text, text), SEND_TIMEOUT_S)

    async def _fail(self, code: str, message: str, close_code: int) -> None:
        try:
            await self._send_json({"type": "error", "code": code, "message": message})
            await self.ws.close(close_code)
        except (WebSocketDisconnect, RuntimeError, TimeoutError) as e:
            log.debug("не удалось отправить error %s: %r", code, e)

    def _spawn(self, coro: Any) -> None:
        if self._closed:
            coro.close()
            return
        if len(self._tasks) >= MAX_PENDING_SENDS:
            coro.close()
            log.warning("клиент не успевает читать, отключаем")
            self._drop()
            return
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(self._on_send_done)

    def _on_send_done(self, task: "asyncio.Task[Any]") -> None:
        if not task.cancelled() and task.exception() is not None:
            _log_task_error(task)
            self._drop()

    def _drop(self) -> None:
        """Сбой отправки: отпустить слот сразу и закрыть сокет (цикл приёма получит disconnect)."""
        if not self._closed:
            self._release()
            self._spawn_close(1011)

    # --- жизненный цикл ---

    async def run(self) -> None:
        await self.ws.accept()
        try:
            if await self._handshake():
                await self._loop()
        except (WebSocketDisconnect, RuntimeError, TimeoutError) as e:
            log.debug("соединение закрыто: %r", e)
        finally:
            self._release()

    async def _handshake(self) -> bool:
        cfg = self.ctx.config["input"]
        try:
            msg = await asyncio.wait_for(self.ws.receive(), timeout=cfg["hello_timeout_s"])
        except TimeoutError:
            await self._fail("timeout", "нет hello", CLOSE_AUTH)
            return False
        if msg["type"] == "websocket.disconnect":
            return False
        hello = parse_hello(msg.get("text"))
        if hello is None:
            await self._fail("bad_message", "первым должно быть корректное текстовое hello", CLOSE_BAD_MESSAGE)
            return False
        if hello["protocol_version"] != PROTOCOL_VERSION:
            await self._fail(
                "protocol_mismatch",
                f"сервер поддерживает protocol_version={PROTOCOL_VERSION}",
                CLOSE_VERSION,
            )
            return False
        paired = self.ctx.pairing.verify(hello["token"])
        if paired is None:
            await self._fail("auth_failed", "неверный токен, нужно сопряжение", CLOSE_AUTH)
            return False

        # Атомарно (без await): освободить слот старого соединения этого телефона и занять новый.
        old = self.sessions.get(paired.device_id)
        if old is not None:
            old._release()
            old._spawn_close(CLOSE_REPLACED)
        client = hello.get("client", {})
        name = (client.get("name") or paired.name or "Телефон")[:64]
        device = DeviceInfo(id=f"pwa-{paired.device_id}", kind="pwa", name=name, capabilities=("analog",))
        slot = self.ctx.router.attach(device)
        if slot is None:
            await self._fail("no_free_slot", "все слоты заняты", CLOSE_NO_SLOT)
            return False
        self.slot = slot
        self.device_id = paired.device_id
        self.sessions[paired.device_id] = self

        loop = asyncio.get_running_loop()
        # Бэкенд может звать вибрацию из своего потока (ViGEm callback).
        self.ctx.router.set_rumble_listener(
            slot,
            lambda large, small: loop.call_soon_threadsafe(
                self._spawn, self._send_bytes(encode_rumble(large, small))
            ),
        )
        await self._send_json({
            "type": "welcome",
            "protocol_version": PROTOCOL_VERSION,
            "slot": slot,
            "session_id": uuid.uuid4().hex,
            "deadman_ms": cfg["deadman_ms"],
            "send_hz": cfg["send_hz"],
            "capabilities": {
                "rumble": bool(self.ctx.features.get("rumble")),
                "active_backend": self.ctx.router.backend.id,
            },
        })
        log.info("подключён %s, слот %d", name, slot)
        return True

    async def _loop(self) -> None:
        assert self.slot is not None
        slot = self.slot
        router = self.ctx.router
        unsubscribe = self.ctx.bus.subscribe(self._on_event)
        pinger = asyncio.create_task(self._pinger())
        pinger.add_done_callback(self._on_send_done)
        try:
            while not self._closed:
                msg = await self.ws.receive()
                if msg["type"] == "websocket.disconnect" or self._closed:
                    return
                data = msg.get("bytes")
                if not data:
                    continue  # текст после hello пока не используется
                kind = data[0]
                try:
                    if kind == PAD_STATE:
                        frame = decode_pad(data)
                        router.submit(slot, frame.seq, frame.state)
                    elif kind == PING:
                        _, ping_id, t_ms = decode_ping(data)
                        await self._send_bytes(encode_ping(PONG, ping_id, t_ms))
                    elif kind == PONG:
                        _, ping_id, t_ms = decode_ping(data)
                        if ping_id == self._ping_id:
                            self.rtt_ms = float((_now_ms() - t_ms) & 0xFFFFFFFF)
                    else:
                        self.bad_frames += 1
                except FrameError:
                    self.bad_frames += 1
        finally:
            unsubscribe()
            pinger.cancel()

    async def _pinger(self) -> None:
        while not self._closed:
            await asyncio.sleep(1.0)
            self._ping_id = (self._ping_id + 1) & 0xFFFF
            await self._send_bytes(encode_ping(PING, self._ping_id, _now_ms()))
            s = self.ctx.router.get(self.slot) if self.slot is not None else None
            await self._send_json({
                "type": "stats",
                "rtt_ms": self.rtt_ms,
                "last_seq": s.last_seq if s else None,
                "dropped": s.dropped if s else 0,
            })

    async def _on_event(self, name: str, data: dict[str, Any]) -> None:
        # Не ждём отправку: медленный клиент не должен тормозить шину и HTTP-ответы.
        self._spawn(self._send_json({"type": "event", "name": name, "data": data}))

    def _spawn_close(self, code: int) -> None:
        async def close() -> None:
            try:
                await asyncio.wait_for(self.ws.close(code), SEND_TIMEOUT_S)
            except (WebSocketDisconnect, RuntimeError, TimeoutError) as e:
                log.debug("закрытие старого соединения: %r", e)

        task = asyncio.ensure_future(close())
        task.add_done_callback(_log_task_error)

    def _release(self) -> None:
        """Идемпотентно: отпустить слот, снять регистрацию, отменить фоновые отправки."""
        if self._closed:
            return
        self._closed = True
        if self.slot is not None:
            self.ctx.router.detach(self.slot)
        if self.device_id is not None and self.sessions.get(self.device_id) is self:
            del self.sessions[self.device_id]
        for task in list(self._tasks):
            task.cancel()
