"""Простая шина событий: сервисы публикуют, транспорты рассылают клиентам."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

Listener = Callable[[str, dict[str, Any]], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._listeners: list[Listener] = []

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    async def publish(self, name: str, data: dict[str, Any]) -> None:
        for listener in list(self._listeners):
            try:
                await listener(name, data)
            except Exception:
                # Один упавший подписчик (оборванный сокет) не должен ломать остальных.
                log.exception("подписчик события %s упал", name)
