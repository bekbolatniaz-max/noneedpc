"""Плагин vigem на подставном модуле vgamepad (драйвер не нужен)."""

from types import SimpleNamespace
from typing import Any

import pytest

from server.core.interfaces import DeviceInfo, PadState
from server.core.router import InputRouter
from server.plugins.output.vigem import VigemOutput


class FakePad:
    instances: list["FakePad"] = []

    def __init__(self) -> None:
        self.report = SimpleNamespace(wButtons=0, bLeftTrigger=0, bRightTrigger=0,
                                      sThumbLX=0, sThumbLY=0, sThumbRX=0, sThumbRY=0)
        self.sent: list[dict[str, int]] = []
        self.callback: Any = None
        self.unregistered = False
        FakePad.instances.append(self)

    def register_notification(self, callback_function: Any) -> None:
        self.callback = callback_function

    def unregister_notification(self) -> None:
        self.unregistered = True

    def update(self) -> None:
        self.sent.append(dict(vars(self.report)))

    def reset(self) -> None:
        for k in vars(self.report):
            setattr(self.report, k, 0)


@pytest.fixture
def vg() -> SimpleNamespace:
    FakePad.instances = []
    return SimpleNamespace(VX360Gamepad=FakePad)


def test_state_maps_directly_to_xusb_report(vg: SimpleNamespace) -> None:
    out = VigemOutput(vg)
    out.open(0)
    out.apply(0, PadState(buttons=0x1000 | 0x0800 | 0x0001, lx=-32768, ly=32767, rx=5, ry=-5, lt=255, rt=1))
    sent = FakePad.instances[0].sent[-1]
    assert sent == {"wButtons": 0x1001, "bLeftTrigger": 255, "bRightTrigger": 1,
                    "sThumbLX": -32768, "sThumbLY": 32767, "sThumbRX": 5, "sThumbRY": -5}


def test_close_releases_and_unregisters(vg: SimpleNamespace) -> None:
    out = VigemOutput(vg)
    out.open(0)
    out.apply(0, PadState(buttons=0x1000))
    out.close(0)
    pad = FakePad.instances[0]
    assert pad.sent[-1]["wButtons"] == 0 and pad.unregistered
    out.apply(0, PadState(buttons=0x1000))  # после close — тихо игнорируется
    assert len(pad.sent) == 2


def test_close_always_unregisters_even_if_update_fails(vg: SimpleNamespace) -> None:
    class Broken(FakePad):
        def update(self) -> None:
            raise OSError("bus gone")

    vg.VX360Gamepad = Broken
    out = VigemOutput(vg)
    out.open(0)
    out.close(0)
    assert FakePad.instances[0].unregistered


def test_player_number_from_led_notification(vg: SimpleNamespace) -> None:
    out = VigemOutput(vg)
    out.open(0)
    assert out.player_number(0) is None  # Windows ещё не назначил номер
    pad = FakePad.instances[0]
    pad.callback(None, None, 0, 0, 2, None)  # led_number = XInput index 2
    assert out.player_number(0) == 3
    out.close(0)
    assert out.player_number(0) is None
    pad.callback(None, None, 0, 0, 1, None)  # запоздавшее уведомление закрытого устройства
    out.open(0)
    assert out.player_number(0) is None


def test_register_failure_rolls_back(vg: SimpleNamespace) -> None:
    class NoNotify(FakePad):
        def register_notification(self, callback_function: Any) -> None:
            raise OSError("notify failed")

    vg.VX360Gamepad = NoNotify
    out = VigemOutput(vg)
    with pytest.raises(OSError):
        out.open(0)
    assert out._pads == {} and out._callbacks == {} and out.player_number(0) is None


def test_rumble_callback_reaches_router(vg: SimpleNamespace) -> None:
    out = VigemOutput(vg)
    router = InputRouter(out, max_slots=2)
    got: list[tuple[int, int]] = []
    slot = router.attach(DeviceInfo("d", "test", "D"))
    router.set_rumble_listener(slot, lambda large, small: got.append((large, small)))
    FakePad.instances[0].callback(None, None, 200, 40, 1, None)
    assert got == [(200, 40)]


def test_deadman_via_router_sends_neutral(vg: SimpleNamespace) -> None:
    t = [0.0]
    out = VigemOutput(vg)
    router = InputRouter(out, deadman_ms=250, clock=lambda: t[0])
    slot = router.attach(DeviceInfo("d", "test", "D"))
    router.submit(slot, 1, PadState(buttons=0x1000, lx=100))
    t[0] = 0.3
    router.tick()
    assert all(v == 0 for v in FakePad.instances[0].sent[-1].values())


@pytest.mark.windows
def test_real_vigem_if_installed() -> None:
    if not VigemOutput.available():
        pytest.skip("ViGEmBus или vgamepad не установлены")
    out = VigemOutput()
    out.open(0)
    out.apply(0, PadState(buttons=0x1000))
    out.close(0)
