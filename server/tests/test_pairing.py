import re
import threading

import pytest

from server.services.pairing import PairingError, PairingService


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_code_is_six_digits_and_one_time() -> None:
    p = PairingService()
    code = p.current_code()
    assert re.fullmatch(r"[0-9]{6}", code)
    token, device_id = p.pair(code, "iPhone")
    assert len(token) >= 32 and p.verify(token).device_id == device_id
    with pytest.raises(PairingError):
        p.pair(code)  # повторно нельзя (код сменился); шанс совпадения 1e-6 игнорируем


def test_code_expires() -> None:
    clock = Clock()
    p = PairingService(code_ttl_s=300, clock=clock)
    old = p.current_code()
    clock.t = 301
    new = p.current_code()
    if new != old:
        with pytest.raises(PairingError):
            p.pair(old)


def test_too_many_attempts_rotates_code() -> None:
    p = PairingService(max_attempts=3)
    code = p.current_code()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(2):
        with pytest.raises(PairingError) as e:
            p.pair(wrong)
        assert e.value.code == "forbidden"
    with pytest.raises(PairingError) as e:
        p.pair(wrong)
    assert e.value.code == "rate_limited"


def test_tokens_stored_only_as_hashes() -> None:
    p = PairingService()
    token, device_id = p.pair(p.current_code())
    stored = p._devices[device_id]
    assert token not in repr(stored)
    assert p.verify("wrong") is None and p.verify(None) is None and p.verify("") is None
    p.revoke(device_id)
    assert p.verify(token) is None


def test_one_code_one_token_under_concurrency() -> None:
    p = PairingService()
    code = p.current_code()
    barrier = threading.Barrier(16)
    tokens: list[str] = []

    def worker() -> None:
        barrier.wait()
        try:
            tokens.append(p.pair(code)[0])
        except PairingError:
            pass

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(tokens) == 1


def test_new_code_is_announced_on_every_change() -> None:
    shown: list[str] = []
    clock = Clock()
    p = PairingService(max_attempts=2, clock=clock, on_new_code=shown.append)
    first = p.current_code()
    assert shown == [first]
    p.pair(first)  # успех → сразу новый код
    assert len(shown) == 2 and shown[-1] == p.current_code()
    wrong = "000000" if shown[-1] != "000000" else "111111"
    for _ in range(2):
        with pytest.raises(PairingError):
            p.pair(wrong)  # лимит → новый код
    assert len(shown) == 3
    clock.t += 301
    p.current_code()  # TTL → новый код
    assert len(shown) == 4


def test_fixed_code_is_reusable_mock_only() -> None:
    p = PairingService(fixed_code="123456", max_attempts=1)
    t1, _ = p.pair("123456")
    t2, _ = p.pair("123456")
    assert t1 != t2
    with pytest.raises(PairingError) as e:
        p.pair("000000")
    assert e.value.code == "forbidden"
