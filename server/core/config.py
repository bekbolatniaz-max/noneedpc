"""Конфиг: config/default.yaml (в git) + config/local.yaml (у каждого свой, в .gitignore)."""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: ожидался словарь верхнего уровня")
    return data


def load_config(
    config_dir: Path = CONFIG_DIR, overrides: dict[str, Any] | None = None
) -> dict[str, Any]:
    cfg = deep_merge(_read(config_dir / "default.yaml"), _read(config_dir / "local.yaml"))
    if overrides:
        cfg = deep_merge(cfg, overrides)
    return cfg
