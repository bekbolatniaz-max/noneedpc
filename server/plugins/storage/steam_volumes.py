"""Диски, на которых лежат библиотеки Steam: место и доступность (только чтение)."""

import shutil
import sys
from pathlib import Path

from server.core.interfaces import StorageProvider, VolumeInfo
from server.services.steam import SteamLibrary

_DRIVE_KINDS = {2: "external", 3: "internal", 4: "network"}  # GetDriveTypeW


def _drive_kind(root: str) -> str:
    if sys.platform != "win32":
        return "internal"
    import ctypes

    return _DRIVE_KINDS.get(ctypes.windll.kernel32.GetDriveTypeW(root), "internal")


class SteamVolumes(StorageProvider):
    id = "steam_volumes"

    def __init__(self, library: SteamLibrary) -> None:
        self._library = library

    def volumes(self) -> list[VolumeInfo]:
        by_root: dict[str, VolumeInfo] = {}
        for lib in self._library.libraries():
            root = Path(lib.path).anchor or "/"
            vol = by_root.get(root)
            if vol is None:
                try:
                    usage = shutil.disk_usage(root)
                    total, free, available = usage.total, usage.free, True
                except OSError:
                    total = free = 0
                    available = False
                vol = VolumeInfo(
                    id=f"vol-{root.rstrip(':/\\').lower() or 'root'}",
                    kind=_drive_kind(root) if available else "external",
                    label=root,
                    mount=root,
                    total_bytes=total,
                    free_bytes=free,
                    available=available,
                )
                by_root[root] = vol
            vol.libraries.append(lib)
        return list(by_root.values())
