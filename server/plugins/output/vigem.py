"""Виртуальный Xbox 360 через ViGEmBus + vgamepad (docs/architecture.md, раздел 4, вариант A).

Маска кнопок протокола совпадает с XInput wButtons (D-003), поэтому передаётся без перекодирования.
Плагин ничего не устанавливает: если драйвера или vgamepad нет, available() == False.
"""

import importlib
import importlib.util
import logging
import sys
from typing import Any

from server.core.interfaces import OutputBackend, PadState

log = logging.getLogger(__name__)


def _driver_installed() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\ViGEmBus"):
            return True
    except OSError:
        return False


class VigemOutput(OutputBackend):
    id = "vigem"
    title = "Xbox 360 (ViGEmBus)"
    analog = True

    def __init__(self, vg: Any = None) -> None:
        """vg — модуль vgamepad (подставляется в тестах); по умолчанию импортируется лениво."""
        self._vg = vg
        self._pads: dict[int, Any] = {}
        self._callbacks: dict[int, Any] = {}
        self._rumble = None

    @classmethod
    def available(cls) -> bool:
        return _driver_installed() and importlib.util.find_spec("vgamepad") is not None

    def _module(self) -> Any:
        if self._vg is None:
            self._vg = importlib.import_module("vgamepad")
        return self._vg

    def open(self, slot: int) -> None:
        pad = self._module().VX360Gamepad()

        def on_notification(client: Any, target: Any, large_motor: int, small_motor: int,
                            led_number: int, user_data: Any) -> None:
            # Поток драйвера: роутер перекинет в event loop (call_soon_threadsafe).
            if self._rumble is not None:
                self._rumble(slot, int(large_motor), int(small_motor))

        pad.register_notification(callback_function=on_notification)
        self._pads[slot] = pad
        self._callbacks[slot] = on_notification  # держим ссылку, иначе ctypes-callback соберёт GC
        log.info("vigem: создан геймпад слота %d", slot)

    def apply(self, slot: int, state: PadState) -> None:
        pad = self._pads.get(slot)
        if pad is None:
            return
        r = pad.report
        r.wButtons = state.buttons & 0xF7FF  # бит 11 зарезервирован
        r.bLeftTrigger = state.lt
        r.bRightTrigger = state.rt
        r.sThumbLX = state.lx
        r.sThumbLY = state.ly
        r.sThumbRX = state.rx
        r.sThumbRY = state.ry
        pad.update()

    def close(self, slot: int) -> None:
        pad = self._pads.pop(slot, None)
        self._callbacks.pop(slot, None)
        if pad is None:
            return
        # Каждый шаг отдельно: сбой отпускания не должен оставить подписку на вибрацию.
        for step in (pad.reset, pad.update, pad.unregister_notification):
            try:
                step()
            except Exception:
                log.exception("vigem: %s при закрытии слота %d", step.__name__, slot)
        # Устройство удаляется из системы при сборке объекта (VX360Gamepad.__del__).
        del pad

    def shutdown(self) -> None:
        for slot in list(self._pads):
            self.close(slot)
