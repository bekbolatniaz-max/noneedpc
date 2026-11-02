"""Мок-хранилище: внутренний SSD и отключённый внешний диск."""

from server.core.interfaces import SteamLibraryInfo, StorageProvider, VolumeInfo

GB = 1024**3


class MockStorage(StorageProvider):
    id = "mock"

    def volumes(self) -> list[VolumeInfo]:
        return [
            VolumeInfo(
                id="vol-c", kind="internal", label="SSD", mount="C:\\",
                total_bytes=476 * GB, free_bytes=120 * GB, available=True,
                libraries=[SteamLibraryInfo("lib-0", "C:\\Program Files (x86)\\Steam", 3, 18 * GB)],
            ),
            VolumeInfo(
                id="vol-e", kind="external", label="Внешний", mount="E:\\",
                total_bytes=931 * GB, free_bytes=0, available=False,
                libraries=[SteamLibraryInfo("lib-1", "E:\\SteamLibrary", 1, 40 * GB)],
            ),
        ]
