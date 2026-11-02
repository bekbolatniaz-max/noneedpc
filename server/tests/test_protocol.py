import struct

import pytest

from server.core.interfaces import PadState
from server.core.protocol import (
    PAD_STATE_SIZE,
    PING,
    PONG,
    RUMBLE,
    FrameError,
    decode_pad,
    decode_ping,
    encode_pad,
    encode_ping,
    encode_rumble,
    seq_newer,
)


def test_pad_frame_is_18_bytes_little_endian() -> None:
    state = PadState(buttons=0x1001, lx=-32768, ly=32767, rx=1, ry=-1, lt=255, rt=7)
    buf = encode_pad(0x1234, state, t=0xBEEF, flags=1)
    assert len(buf) == PAD_STATE_SIZE == 18
    assert buf[0] == 0x01 and buf[1] == 1
    assert buf[2:4] == b"\x34\x12"  # seq LE
    assert struct.unpack_from("<H", buf, 6)[0] == 0x1001
    assert buf[16] == 255 and buf[17] == 7


def test_pad_roundtrip() -> None:
    state = PadState(buttons=0xFFFF, lx=100, ly=-200, rx=300, ry=-400, lt=10, rt=20)
    frame = decode_pad(encode_pad(65535, state, t=70000))
    assert frame.seq == 65535 and frame.t == 70000 & 0xFFFF and frame.state == state


@pytest.mark.parametrize("buf", [b"", b"\x01" * 17, b"\x01" * 19, b"\x02" + b"\x00" * 17])
def test_pad_rejects_bad_frames(buf: bytes) -> None:
    with pytest.raises(FrameError):
        decode_pad(buf)


def test_ping_pong_roundtrip() -> None:
    assert decode_ping(encode_ping(PING, 7, 2**32 + 5)) == (PING, 7, 5)
    assert decode_ping(encode_ping(PONG, 65536 + 1, 9)) == (PONG, 1, 9)
    with pytest.raises(FrameError):
        decode_ping(b"\x02\x00\x01")


def test_rumble_frame() -> None:
    assert encode_rumble(255, 10) == bytes([RUMBLE, 0, 255, 10])


@pytest.mark.parametrize(
    ("seq", "last", "newer"),
    [
        (0, None, True),
        (5, 4, True),
        (4, 4, False),  # повтор
        (3, 4, False),  # опоздавший
        (0, 65535, True),  # переполнение
        (2, 65530, True),
        (65530, 2, False),
        (4 + 0x7FFF, 4, True),
        (4 + 0x8000, 4, False),  # дальше половины круга — считаем старым
    ],
)
def test_seq_newer(seq: int, last: int | None, newer: bool) -> None:
    assert seq_newer(seq & 0xFFFF, last) is newer
