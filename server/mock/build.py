"""Сборка мок-контекста: те же ядро, сопряжение и WS, но фальшивые сервисы и вывод."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from server.app import create_app
from server.core.config import CONFIG_DIR, load_config
from server.core.context import AppContext
from server.core.eventbus import EventBus
from server.core.registry import Registry
from server.core.router import InputRouter
from server.mock.services import MockConsoleMode, MockLauncher, MockLibrary, MockSystem
from server.plugins import register_builtin
from server.services.pairing import PairingService

MOCK_FEATURES = {
    "games": True,
    "launch": True,
    "console_mode": True,
    "system": True,
    "devices": True,
    "storage": True,
    "rumble": False,         # M2
    "backend_select": False,  # M2
}


def build_mock_context(
    *,
    pair_code: str | None = None,
    on_new_code: Callable[[str], None] | None = None,
    overrides: dict[str, Any] | None = None,
    config_dir: Path = CONFIG_DIR,
) -> AppContext:
    cfg = load_config(config_dir, {"output": {"backend": "mock"}, "storage": {"provider": "mock"}})
    if overrides:
        from server.core.config import deep_merge

        cfg = deep_merge(cfg, overrides)
    registry = register_builtin(Registry())
    backend = registry.get("output", "mock")()
    router = InputRouter(
        backend, max_slots=cfg["input"]["max_slots"], deadman_ms=cfg["input"]["deadman_ms"]
    )
    bus = EventBus()
    library = MockLibrary()
    pairing = PairingService(
        code_ttl_s=cfg["pairing"]["code_ttl_s"],
        max_attempts=cfg["pairing"]["max_attempts"],
        fixed_code=pair_code,
        on_new_code=on_new_code,
    )
    return AppContext(
        config=cfg,
        registry=registry,
        router=router,
        pairing=pairing,
        bus=bus,
        library=library,
        launcher=MockLauncher(library, bus),
        console_mode=MockConsoleMode(bus),
        system=MockSystem(),
        storage=registry.get("storage", "mock")(),
        inputs=[registry.get("input", "pwa_ws")()],
        features=dict(MOCK_FEATURES),
        mock=True,
    )


def build_mock_app(**kwargs: Any) -> FastAPI:
    return create_app(build_mock_context(**kwargs))
