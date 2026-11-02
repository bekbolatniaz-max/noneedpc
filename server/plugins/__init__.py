"""Регистрация встроенных плагинов. Новый плагин — одна строка здесь."""

from server.core.registry import Registry


def register_builtin(registry: Registry) -> Registry:
    from server.plugins.input.pwa_ws import PwaWsInput
    from server.plugins.output.mock import MockOutput
    from server.plugins.output.vigem import VigemOutput
    from server.plugins.storage.mock import MockStorage
    from server.plugins.storage.steam_volumes import SteamVolumes

    registry.register("input", PwaWsInput)
    registry.register("output", VigemOutput)
    registry.register("output", MockOutput)
    registry.register("storage", SteamVolumes)
    registry.register("storage", MockStorage)
    # M2: registry.register("output", KbmOutput)
    return registry
