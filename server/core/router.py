"""InputRouter: слоты, «самый свежий seq», deadman (docs/architecture.md, раздел 5)."""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from server.core.interfaces import NEUTRAL, DeviceInfo, OutputBackend, PadState
from server.core.protocol import seq_newer

log = logging.getLogger(__name__)

RumbleListener = Callable[[int, int], None]


@dataclass(slots=True)
class Slot:
    index: int
    device: DeviceInfo
    last_seq: int | None = None
    last_seen: float | None = None
    dropped: int = 0
    released: bool = True
    rumble: RumbleListener | None = None


class InputRouter:
    def __init__(
        self,
        backend: OutputBackend,
        *,
        max_slots: int = 4,
        deadman_ms: int = 250,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.backend = backend
        self.max_slots = max_slots
        self.deadman_s = deadman_ms / 1000
        self._clock = clock
        self._slots: dict[int, Slot] = {}
        backend.set_rumble_handler(self._on_rumble)

    # --- слоты ---

    def attach(self, device: DeviceInfo) -> int | None:
        """Занять свободный слот. None — свободных нет."""
        for index in range(self.max_slots):
            if index not in self._slots:
                self.backend.open(index)
                self._slots[index] = Slot(index, device)
                return index
        return None

    def detach(self, slot: int) -> None:
        if self._slots.pop(slot, None) is not None:
            self.backend.close(slot)

    def set_rumble_listener(self, slot: int, listener: RumbleListener | None) -> None:
        if slot in self._slots:
            self._slots[slot].rumble = listener

    def slots(self) -> list[Slot]:
        return [self._slots[i] for i in sorted(self._slots)]

    def get(self, slot: int) -> Slot | None:
        return self._slots.get(slot)

    # --- ввод ---

    def submit(self, slot: int, seq: int, state: PadState) -> bool:
        """Применить снимок, если он новее последнего. True — применён."""
        s = self._slots.get(slot)
        if s is None:
            return False
        if not seq_newer(seq, s.last_seq):
            s.dropped += 1
            return False
        s.last_seq = seq
        s.last_seen = self._clock()
        s.released = False
        self.backend.apply(slot, state)
        return True

    def tick(self) -> list[int]:
        """Deadman: отпустить слоты без кадров дольше deadman_ms. Возвращает отпущенные слоты."""
        now = self._clock()
        released = []
        for s in self._slots.values():
            if not s.released and s.last_seen is not None and now - s.last_seen >= self.deadman_s:
                self.backend.apply(s.index, NEUTRAL)
                s.released = True
                # Следующий кадр принимается как первый: клиент мог перезапустить seq.
                s.last_seq = None
                released.append(s.index)
        if released:
            log.info("deadman: отпущены слоты %s", released)
        return released

    async def run_deadman(self, interval_s: float = 0.025) -> None:
        while True:
            self.tick()
            await asyncio.sleep(interval_s)

    # --- вибрация ---

    def _on_rumble(self, slot: int, large: int, small: int) -> None:
        s = self._slots.get(slot)
        if s is not None and s.rumble is not None:
            s.rumble(large, small)
