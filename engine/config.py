"""Загрузка и валидация конфигурации из YAML."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Cfg:
    """Тонкая обёртка над словарём конфига с доступом через точку."""

    raw: dict[str, Any] = field(default_factory=dict)

    def __getattr__(self, name: str) -> Any:
        try:
            val = self.raw[name]
        except KeyError as exc:  # noqa: PERF203
            raise AttributeError(f"config key missing: {name}") from exc
        return Cfg(val) if isinstance(val, dict) else val

    def get(self, name: str, default: Any = None) -> Any:
        val = self.raw.get(name, default)
        return Cfg(val) if isinstance(val, dict) else val


def load_config(path: str | Path) -> Cfg:
    """Читает YAML-конфиг.

    Args:
        path: путь к config.yaml.

    Returns:
        Cfg: типобезопасная обёртка конфигурации.
    """
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError("config.yaml: корень должен быть mapping")
    return Cfg(data)
