"""Мок-вывод: запоминает снимки, ничего не устанавливает. Для тестов и мок-сервера."""

import logging

from server.core.interfaces import NEUTRAL, OutputBackend, PadState

log = logging.getLogger(__name__)


class MockOutput(OutputBackend):
    id = "mock"
    title = "Мок (без драйвера)"
    analog = True

    def __init__(self) -> None:
        self.states: dict[int, PadState] = {}
        self.history: list[tuple[int, PadState]] = []
        self.opened: set[int] = set()
        self._rumble = None

    def open(self, slot: int) -> None:
        self.opened.add(slot)
        self.states[slot] = NEUTRAL

    def apply(self, slot: int, state: PadState) -> None:
        self.states[slot] = state
        self.history.append((slot, state))
        del self.history[:-1000]

    def close(self, slot: int) -> None:
        self.states[slot] = NEUTRAL
        self.opened.discard(slot)

    def player_number(self, slot: int) -> int | None:
        return slot + 1 if slot in self.opened else None

    def rumble(self, slot: int, large: int, small: int) -> None:
        """Имитация вибрации от игры (как callback ViGEm)."""
        if self._rumble is not None:
            self._rumble(slot, large, small)
