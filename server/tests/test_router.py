import pytest

from server.core.interfaces import NEUTRAL, DeviceInfo, PadState
from server.core.router import InputRouter
from server.plugins.output.mock import MockOutput

A = PadState(buttons=0x1000, lx=1000)
B = PadState(buttons=0x2000, ly=-1000)


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def out() -> MockOutput:
    return MockOutput()


@pytest.fixture
def router(out: MockOutput, clock: Clock) -> InputRouter:
    return InputRouter(out, max_slots=2, deadman_ms=250, clock=clock)


def dev(n: int) -> DeviceInfo:
    return DeviceInfo(id=f"d{n}", kind="test", name=f"D{n}")


def test_slots_fill_and_free(router: InputRouter, out: MockOutput) -> None:
    assert router.attach(dev(1)) == 0
    assert router.attach(dev(2)) == 1
    assert router.attach(dev(3)) is None
    router.detach(0)
    assert out.opened == {1}
    assert router.attach(dev(3)) == 0


def test_only_newest_seq_applies(router: InputRouter, out: MockOutput) -> None:
    s = router.attach(dev(1))
    assert router.submit(s, 10, A)
    assert not router.submit(s, 9, B)  # опоздал
    assert not router.submit(s, 10, B)  # повтор
    assert out.states[s] == A
    assert router.get(s).dropped == 2
    assert router.submit(s, 11, B) and out.states[s] == B


def test_submit_unknown_slot_ignored(router: InputRouter, out: MockOutput) -> None:
    assert not router.submit(3, 1, A)
    assert out.history == []


def test_deadman_releases_after_250ms(router: InputRouter, out: MockOutput, clock: Clock) -> None:
    s = router.attach(dev(1))
    router.submit(s, 1, A)
    clock.t += 0.249
    assert router.tick() == []
    assert out.states[s] == A
    clock.t += 0.002
    assert router.tick() == [s]
    assert out.states[s] == NEUTRAL
    # повторно не отпускаем
    clock.t += 1
    assert router.tick() == []


def test_after_deadman_any_seq_accepted(router: InputRouter, out: MockOutput, clock: Clock) -> None:
    s = router.attach(dev(1))
    router.submit(s, 500, A)
    clock.t += 0.3
    router.tick()
    # клиент перезапустился и начал seq заново
    assert router.submit(s, 1, B)
    assert out.states[s] == B


def test_deadman_ignores_idle_slot(router: InputRouter, clock: Clock) -> None:
    router.attach(dev(1))
    clock.t += 10
    assert router.tick() == []


def test_detach_neutralizes(router: InputRouter, out: MockOutput) -> None:
    s = router.attach(dev(1))
    router.submit(s, 1, A)
    router.detach(s)
    assert out.states[s] == NEUTRAL and s not in out.opened


def test_rumble_routed_to_slot_listener(router: InputRouter, out: MockOutput) -> None:
    got: list[tuple[int, int]] = []
    s = router.attach(dev(1))
    router.set_rumble_listener(s, lambda large, small: got.append((large, small)))
    out.rumble(s, 200, 50)
    out.rumble(1, 1, 1)  # чужой/пустой слот — тихо игнорируется
    assert got == [(200, 50)]
