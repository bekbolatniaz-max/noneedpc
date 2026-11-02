"""Сборка контекста настоящего сервера: Steam, режим консоли, выбранный бэкенд вывода."""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from server.core.config import CONFIG_DIR, ROOT, load_config
from server.core.context import AppContext
from server.core.eventbus import EventBus
from server.core.registry import Registry
from server.core.router import InputRouter
from server.plugins import register_builtin
from server.services.console_mode import ScriptConsoleMode
from server.services.pairing import PairingService
from server.services.steam import SteamLauncher, SteamLibrary, find_steam_path
from server.services.system import UnavailableSystem

log = logging.getLogger(__name__)

DATA_DIR = ROOT / "data"


def build_context(
    *,
    overrides: dict[str, Any] | None = None,
    config_dir: Path = CONFIG_DIR,
    data_dir: Path = DATA_DIR,
    on_new_code: Callable[[str], None] | None = None,
) -> tuple[AppContext, list[str]]:
    """Возвращает контекст и список предупреждений для показа в консоли."""
    cfg = load_config(config_dir, overrides)
    warnings: list[str] = []
    registry = register_builtin(Registry())

    wanted = cfg["output"]["backend"]
    backend_cls = registry.get("output", wanted)
    if not backend_cls.available():
        warnings.append(
            f"бэкенд вывода «{wanted}» недоступен (нет драйвера/библиотеки) — включён «mock»: "
            "игры НЕ увидят геймпад"
        )
        backend_cls = registry.get("output", "mock")
    backend = backend_cls()
    router = InputRouter(
        backend, max_slots=cfg["input"]["max_slots"], deadman_ms=cfg["input"]["deadman_ms"]
    )

    steam_cfg = cfg.get("steam", {})
    steam_path = find_steam_path(steam_cfg.get("path"))
    if steam_path is None:
        warnings.append("Steam не найден — список игр пуст (укажи steam.path в config/local.yaml)")
    library = SteamLibrary(steam_path, set(steam_cfg.get("hide_appids") or []))

    bus = EventBus()
    cm_cfg = cfg.get("console_mode", {})
    dry_run = bool(cm_cfg.get("dry_run", True))
    if dry_run:
        warnings.append("режим приставки в пробном режиме (console_mode.dry_run: true) — система не меняется")

    pairing = PairingService(
        code_ttl_s=cfg["pairing"]["code_ttl_s"],
        max_attempts=cfg["pairing"]["max_attempts"],
        on_new_code=on_new_code,
        store_path=data_dir / "paired-devices.json",
    )
    ctx = AppContext(
        config=cfg,
        registry=registry,
        router=router,
        pairing=pairing,
        bus=bus,
        library=library,
        launcher=SteamLauncher(library, bus),
        console_mode=ScriptConsoleMode(
            bus, dry_run=dry_run, snapshot_path=data_dir / "console-snapshot.json"
        ),
        system=UnavailableSystem(),
        storage=registry.get("storage", "steam_volumes")(library),
        inputs=[registry.get("input", "pwa_ws")()],
        features={
            "games": True,
            "launch": True,
            "console_mode": True,
            "system": False,  # M2
            "devices": True,
            "storage": True,
            "rumble": backend.id == "vigem",
            "backend_select": False,  # M2
        },
        mock=False,
    )
    return ctx, warnings
