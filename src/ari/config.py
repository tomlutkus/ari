"""config.toml: which inventories exist, and which modules each one uses."""

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import HostsError
from .modules import Module, registry
from .paths import config_dir, tilde

SAMPLE = """\
default = "personal"

[inventories.personal.ssh]
path = "~/.ssh/config.d/10-personal.conf"
"""

_INVENTORY_NAME = re.compile(r"[A-Za-z0-9_-]+")


@dataclass
class ModuleConfig:
    module: Module
    enabled: bool
    settings: Any


@dataclass
class InventoryConfig:
    name: str
    file: Path
    modules: dict[str, ModuleConfig] = field(default_factory=dict)

    def enabled(self) -> list[ModuleConfig]:
        return [m for m in self.modules.values() if m.enabled]


@dataclass
class Config:
    path: Path
    inventories: dict[str, InventoryConfig]
    default: str | None = None

    def get(self, name: str) -> InventoryConfig:
        try:
            return self.inventories[name]
        except KeyError:
            known = ", ".join(self.inventories) or "none"
            raise HostsError(f"no inventory {name!r} in {tilde(self.path)} (known: {known})") from None

    def select(self, requested: str | None) -> InventoryConfig:
        """The one inventory a command acts on: -i, then $ARI_INVENTORY, then the config default."""
        name = requested or os.environ.get("ARI_INVENTORY") or self.default
        if not name:
            raise HostsError("no inventory selected: pass -i NAME, set ARI_INVENTORY, or set default in config.toml")
        return self.get(name)

    def scope(self, requested: str | None) -> list[InventoryConfig]:
        """Inventories a read command covers: the one named with -i, otherwise all."""
        return [self.get(requested)] if requested else list(self.inventories.values())


def _module_config(key: str, table: dict[str, Any], where: str) -> ModuleConfig | None:
    table = dict(table)
    enabled = table.pop("enabled", True)
    if not isinstance(enabled, bool):
        raise HostsError(f"{where}.enabled: use true or false")
    try:
        module = registry().get(key)
    except HostsError as e:
        if not enabled:
            return None  # a parked table for a module that isn't installed is fine
        raise HostsError(f"{where}: {e}") from None
    return ModuleConfig(module, enabled, module.settings(table, where))


def load_config(path: Path | None = None) -> Config:
    path = path or config_dir() / "config.toml"
    if not path.exists():
        raise HostsError(f"no config at {tilde(path)}; create it, for example:\n\n{SAMPLE}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise HostsError(f"{tilde(path)}: {e}") from None

    unknown = set(data) - {"default", "inventories"}
    if unknown:
        raise HostsError(f"{tilde(path)}: unknown keys {sorted(unknown)}")
    tables = data.get("inventories")
    if not isinstance(tables, dict) or not tables:
        raise HostsError(f"{tilde(path)}: declare at least one inventory, e.g. [inventories.personal.ssh]")

    inventories: dict[str, InventoryConfig] = {}
    for name, table in tables.items():
        where = f"{tilde(path)}: inventories.{name}"
        if not _INVENTORY_NAME.fullmatch(name):
            raise HostsError(f"{where}: names use letters, digits, _ and - only")
        if not isinstance(table, dict):
            raise HostsError(f"{where}: expected a table")

        file = Path(f"{name}.json")
        modules: dict[str, ModuleConfig] = {}
        for key, value in table.items():
            if key == "file":
                if not isinstance(value, str) or not value:
                    raise HostsError(f"{where}.file: expected a path string")
                file = Path(value).expanduser()
            elif isinstance(value, dict):
                configured = _module_config(key, value, f"{where}.{key}")
                if configured:
                    modules[key] = configured
            elif key in registry().modules:
                hint = f"\npath = {json.dumps(value)}" if key == "ssh" and isinstance(value, str) else ""
                raise HostsError(
                    f"{where}.{key}: module settings are a table now. Replace it with:\n\n"
                    f"[inventories.{name}.{key}]{hint}\n"
                )
            else:
                raise HostsError(f"{where}: unknown key {key!r}")
        if not file.is_absolute():
            file = path.parent / file
        inventories[name] = InventoryConfig(name, file, modules)

    default = data.get("default")
    if default is not None and default not in inventories:
        raise HostsError(f"{tilde(path)}: default {default!r} is not a declared inventory")
    return Config(path, inventories, default)
