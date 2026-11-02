"""Всё, что нужно HTTP-маршрутам и источникам ввода, в одном объекте."""

from dataclasses import dataclass, field
from typing import Any

from server.core.eventbus import EventBus
from server.core.interfaces import InputSource, StorageProvider
from server.core.registry import Registry
from server.core.router import InputRouter
from server.services.base import ConsoleModeService, Launcher, Library, SystemService
from server.services.pairing import PairingService


@dataclass
class AppContext:
    config: dict[str, Any]
    registry: Registry
    router: InputRouter
    pairing: PairingService
    bus: EventBus
    library: Library
    launcher: Launcher
    console_mode: ConsoleModeService
    system: SystemService
    storage: StorageProvider
    inputs: list[InputSource] = field(default_factory=list)
    features: dict[str, bool] = field(default_factory=dict)
    mock: bool = False
