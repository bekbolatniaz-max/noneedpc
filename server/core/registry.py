"""Реестр плагинов по видам: input, output, storage."""

from server.core.interfaces import InputSource, OutputBackend, StorageProvider

_BASES = {"input": InputSource, "output": OutputBackend, "storage": StorageProvider}


class Registry:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, type]] = {kind: {} for kind in _BASES}

    def register(self, kind: str, cls: type) -> type:
        base = _BASES.get(kind)
        if base is None:
            raise KeyError(f"неизвестный вид плагина: {kind}")
        if not issubclass(cls, base):
            raise TypeError(f"{cls.__name__} не реализует {base.__name__}")
        plugin_id = cls.id
        if plugin_id in self._items[kind]:
            raise ValueError(f"плагин {kind}/{plugin_id} уже зарегистрирован")
        self._items[kind][plugin_id] = cls
        return cls

    def get(self, kind: str, plugin_id: str) -> type:
        try:
            return self._items[kind][plugin_id]
        except KeyError:
            known = ", ".join(sorted(self._items.get(kind, {}))) or "нет"
            raise KeyError(f"плагин {kind}/{plugin_id} не найден (есть: {known})") from None

    def all(self, kind: str) -> dict[str, type]:
        return dict(self._items[kind])
