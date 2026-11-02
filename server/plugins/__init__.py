"""Регистрация встроенных плагинов. Новый плагин — одна строка здесь."""

from server.core.registry import Registry


def register_builtin(registry: Registry) -> Registry:
    from server.plugins.input.pwa_ws import PwaWsInput
    from server.plugins.output.mock import MockOutput
    from server.plugins.storage.mock import MockStorage

    registry.register("input", PwaWsInput)
    registry.register("output", MockOutput)
    registry.register("storage", MockStorage)
    # M1: registry.register("output", VigemOutput)
    # M2: registry.register("output", KbmOutput); registry.register("storage", WindowsVolumes)
    return registry
