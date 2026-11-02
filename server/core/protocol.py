"""Бинарные WS-кадры протокола v1 (contracts/protocol.md, раздел 2.2). Little-endian."""

import struct
from dataclasses import dataclass

from server.core.interfaces import PadState

PROTOCOL_VERSION = 1

PAD_STATE = 0x01
PING = 0x02
PONG = 0x03
RUMBLE = 0x10

FLAG_TOUCHING = 0x01

# type, flags, seq, t, buttons, lx, ly, rx, ry, lt, rt
_PAD = struct.Struct("<BBHHHhhhhBB")
# type, 0, id, t_ms
_PING = struct.Struct("<BBHI")
# type, 0, large, small
_RUMBLE = struct.Struct("<BBBB")

PAD_STATE_SIZE = _PAD.size  # 18
PING_SIZE = _PING.size  # 8
RUMBLE_SIZE = _RUMBLE.size  # 4


class FrameError(ValueError):
    """Кадр неверной длины или типа. Соединение не рвём, кадр игнорируем."""


@dataclass(slots=True, frozen=True)
class PadFrame:
    flags: int
    seq: int
    t: int
    state: PadState


def frame_type(buf: bytes) -> int | None:
    return buf[0] if buf else None


def encode_pad(seq: int, state: PadState, *, t: int = 0, flags: int = 0) -> bytes:
    return _PAD.pack(
        PAD_STATE, flags, seq & 0xFFFF, t & 0xFFFF, state.buttons,
        state.lx, state.ly, state.rx, state.ry, state.lt, state.rt,
    )


def decode_pad(buf: bytes) -> PadFrame:
    if len(buf) != PAD_STATE_SIZE or buf[0] != PAD_STATE:
        raise FrameError(f"PAD_STATE: ожидалось {PAD_STATE_SIZE} байт, получено {len(buf)}")
    _, flags, seq, t, buttons, lx, ly, rx, ry, lt, rt = _PAD.unpack(buf)
    return PadFrame(flags, seq, t, PadState(buttons, lx, ly, rx, ry, lt, rt))


def encode_ping(kind: int, ping_id: int, t_ms: int) -> bytes:
    if kind not in (PING, PONG):
        raise ValueError("kind должен быть PING или PONG")
    return _PING.pack(kind, 0, ping_id & 0xFFFF, t_ms & 0xFFFFFFFF)


def decode_ping(buf: bytes) -> tuple[int, int, int]:
    """Возвращает (kind, id, t_ms)."""
    if len(buf) != PING_SIZE or buf[0] not in (PING, PONG):
        raise FrameError(f"PING/PONG: ожидалось {PING_SIZE} байт, получено {len(buf)}")
    kind, _, ping_id, t_ms = _PING.unpack(buf)
    return kind, ping_id, t_ms


def encode_rumble(large: int, small: int) -> bytes:
    return _RUMBLE.pack(RUMBLE, 0, large & 0xFF, small & 0xFF)


def seq_newer(seq: int, last: int | None) -> bool:
    """Кадр новее последнего с учётом переполнения u16. Первый кадр (last=None) новый всегда."""
    if last is None:
        return True
    diff = (seq - last) & 0xFFFF
    return 0 < diff < 0x8000
