"""Правило архитектуры: ядро не знает о конкретных плагинах и устройствах."""

import ast
from pathlib import Path

import pytest

from server.core.interfaces import OutputBackend
from server.core.registry import Registry
from server.plugins import register_builtin

CORE = Path(__file__).resolve().parents[1] / "core"


@pytest.mark.parametrize("path", sorted(CORE.glob("*.py")), ids=lambda p: p.name)
def test_core_does_not_import_plugins_or_mock(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            assert not name.startswith(("server.plugins", "server.mock")), f"{path.name} импортирует {name}"


def test_registry_rejects_wrong_kind_and_duplicates() -> None:
    reg = register_builtin(Registry())
    assert set(reg.all("output")) == {"mock"}
    with pytest.raises(ValueError):
        reg.register("output", reg.get("output", "mock"))
    with pytest.raises(TypeError):
        reg.register("storage", reg.get("output", "mock"))
    with pytest.raises(KeyError):
        reg.get("output", "vigem")


def test_new_output_plugin_needs_no_core_changes() -> None:
    class Dummy(OutputBackend):
        id, title, analog = "dummy", "Dummy", False

        def open(self, slot: int) -> None: ...
        def apply(self, slot: int, state) -> None: ...  # noqa: ANN001
        def close(self, slot: int) -> None: ...

    reg = register_builtin(Registry())
    reg.register("output", Dummy)
    assert reg.get("output", "dummy") is Dummy
