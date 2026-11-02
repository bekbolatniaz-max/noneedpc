"""Виртуальный Xbox 360 через ViGEmBus + vgamepad (docs/architecture.md, раздел 4, вариант A).

Маска кнопок протокола совпадает с XInput wButtons (D-003), поэтому передаётся без перекодирования.
Плагин ничего не устанавливает: если драйвера или vgamepad нет, available() == False.
"""

import importlib
import importlib.util
import logging
import sys
import threading
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
        self._owners: dict[int, object] = {}  # slot → маркер текущего устройства (для уведомлений)
        self._leds: dict[int, int] = {}  # slot → led_number из последнего уведомления
        self._lock = threading.Lock()  # уведомления драйвера приходят из его потока
        self._rumble = None

    @classmethod
    def available(cls) -> bool:
        return _driver_installed() and importlib.util.find_spec("vgamepad") is not None

    @classmethod
    def probe(cls) -> str | None:
        """Создать и сразу удалить геймпад: ловит неработающую службу и ошибку загрузки DLL."""
        try:
            pad = importlib.import_module("vgamepad").VX360Gamepad()
            del pad
        except Exception as e:
            return f"{type(e).__name__}: {e}"
        return None

    def _module(self) -> Any:
        if self._vg is None:
            self._vg = importlib.import_module("vgamepad")
        return self._vg

    def open(self, slot: int) -> None:
        pad = self._module().VX360Gamepad()
        # Маркер владельца вместо ссылки на pad: замыкание pad → callback → pad дало бы цикл,
        # и виртуальное устройство исчезало бы из Windows только после сборки мусора.
        owner = object()

        def on_notification(client: Any, target: Any, large_motor: int, small_motor: int,
                            led_number: int, user_data: Any) -> None:
            # Поток драйвера. Проверка «это всё ещё устройство слота» и запись — под одним замком
            # с close/open, иначе запоздавшее уведомление старого устройства достанется новому.
            # Под замком только запись в dict и вызов роутера (он лишь ставит задачу в event loop).
            with self._lock:
                if self._owners.get(slot) is not owner:
                    return
                self._leds[slot] = int(led_number)
                if self._rumble is not None:
                    self._rumble(slot, int(large_motor), int(small_motor))

        # Сначала регистрируем слот: первое уведомление (с номером игрока) может прийти сразу.
        with self._lock:
            self._pads[slot] = pad
            self._owners[slot] = owner
            self._callbacks[slot] = on_notification  # держим ссылку, иначе ctypes-callback соберёт GC
        try:
            pad.register_notification(callback_function=on_notification)
        except Exception:
            with self._lock:
                self._pads.pop(slot, None)
                self._owners.pop(slot, None)
                self._callbacks.pop(slot, None)
                self._leds.pop(slot, None)
            del pad  # устройство удаляется из системы при сборке объекта
            raise
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

    def player_number(self, slot: int) -> int | None:
        """Номер игрока = led_number из уведомления драйвера + 1 (это XInput user index).

        vigem_target_x360_get_user_index на практике всегда отвечает 0, поэтому ему не верим.
        Уведомление приходит вскоре после open(), до этого номер неизвестен (None).
        """
        led = self._leds.get(slot)
        return led + 1 if led is not None and 0 <= led <= 3 else None

    def close(self, slot: int) -> None:
        with self._lock:
            pad = self._pads.pop(slot, None)
            self._owners.pop(slot, None)
            self._callbacks.pop(slot, None)
            self._leds.pop(slot, None)
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
