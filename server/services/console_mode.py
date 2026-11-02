"""Режим приставки: вызывает scripts/console-mode.ps1 (снимок + откат).

По умолчанию console_mode.dry_run = true: скрипт только показывает план. Настоящие изменения
включаются в config/local.yaml (dry_run: false) после того, как лидер посмотрел план.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from server.core.config import ROOT
from server.core.eventbus import EventBus
from server.services.base import Conflict, ConsoleModeStatus

log = logging.getLogger(__name__)

SCRIPT = ROOT / "scripts" / "console-mode.ps1"

Runner = Callable[[list[str]], Awaitable[tuple[int, str]]]


async def run_powershell(args: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(SCRIPT), *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    except BaseException:  # таймаут или отмена (остановка сервера): скрипт не должен работать дальше
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    return proc.returncode or 0, out.decode("utf-8", errors="replace")


def _last_json(output: str) -> dict[str, Any] | None:
    for line in reversed(output.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                return None
    return None


class ScriptConsoleMode:
    def __init__(
        self,
        bus: EventBus,
        *,
        dry_run: bool = True,
        snapshot_path: Path = ROOT / "data" / "console-snapshot.json",
        runner: Runner = run_powershell,
    ) -> None:
        self._bus = bus
        self._dry_run = dry_run
        self._snapshot = snapshot_path
        self._run = runner
        self._switching = False
        self._task: asyncio.Task[None] | None = None
        self._error: str | None = None
        self.last_steps: list[str] = []

    def status(self) -> ConsoleModeStatus:
        if self._switching:
            return ConsoleModeStatus("switching", None)
        if self._error:
            return ConsoleModeStatus("error", None, self._error)
        on = self._snapshot.is_file()
        msg = "пробный режим (dry_run): система не меняется" if self._dry_run else None
        return ConsoleModeStatus("on" if on else "off", "default" if on else None, msg)

    async def set(self, on: bool) -> ConsoleModeStatus:
        if self._switching:
            raise Conflict("режим уже переключается")
        self._switching = True
        self._error = None
        await self._bus.publish("console_mode", {"state": "switching"})
        self._task = asyncio.create_task(self._switch(on))
        return self.status()

    async def _switch(self, on: bool) -> None:
        args = ["-Action", "on" if on else "off", "-SnapshotPath", str(self._snapshot)]
        if self._dry_run:
            args.append("-DryRun")
        try:
            code, output = await self._run(args)
            result = _last_json(output)
            steps = result.get("steps") if result else None
            self.last_steps = [str(s) for s in steps] if isinstance(steps, list) else []
            for step in self.last_steps:
                log.info("console-mode: %s", step)
            if result is None:
                self._error = f"скрипт не вернул результат (код {code})"
            elif result.get("ok") is not True or code != 0:
                self._error = str(result.get("message") or f"ошибка скрипта (код {code})")
        except (OSError, TimeoutError) as e:
            self._error = f"не удалось выполнить скрипт: {e!r}"
        finally:
            self._switching = False
        s = self.status()
        if s.state == "error":
            log.warning("console-mode: %s", s.message)
        await self._bus.publish("console_mode", {"state": s.state})

    async def aclose(self) -> None:
        """Остановка сервера: отменить переключение и дождаться, пока процесс скрипта будет убит."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
