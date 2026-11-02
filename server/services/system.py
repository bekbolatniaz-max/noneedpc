"""Громкость и питание — M2. Пока features.system = false, а эндпоинты отвечают 404."""

from server.services.base import NotFound, PowerAction


class UnavailableSystem:
    def volume(self) -> tuple[int, bool]:
        raise NotFound("управление системой появится в M2")

    async def set_volume(self, volume: int | None, muted: bool | None) -> tuple[int, bool]:
        raise NotFound("управление системой появится в M2")

    async def power(self, action: PowerAction) -> None:
        raise NotFound("управление системой появится в M2")
