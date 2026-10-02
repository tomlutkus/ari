"""config.toml: which inventories exist and where each one exports."""

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import HostsError

APP = "ari"

SAMPLE = """\
default = "personal"

[inventories.personal]
ssh = "~/.ssh/config.d/10-personal.conf"
"""

_INVENTORY_NAME = re.compile(r"[A-Za-z0-9_-]+")
_INVENTORY_KEYS = {"file", "ssh", "ansible"}


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser() / APP


def state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or "~/.local/state").expanduser() / APP


def tilde(path: Path) -> str:
    """Shorten a path under $HOME for display."""
    home = Path.home()
    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)


@dataclass
class InventoryConfig:
    name: str
    file: Path
    ssh: Path | None = None
    ansible: dict[str, Any] | None = None


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


def _path(value: Any, where: str) -> Path:
    if not isinstance(value, str) or not value:
        raise HostsError(f"{where}: expected a path string")
    return Path(value).expanduser()


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
        raise HostsError(f"{tilde(path)}: declare at least one [inventories.NAME] table")

    inventories: dict[str, InventoryConfig] = {}
    for name, table in tables.items():
        where = f"{tilde(path)}: inventories.{name}"
        if not _INVENTORY_NAME.fullmatch(name):
            raise HostsError(f"{where}: names use letters, digits, _ and - only")
        if not isinstance(table, dict):
            raise HostsError(f"{where}: expected a table")
        unknown = set(table) - _INVENTORY_KEYS
        if unknown:
            raise HostsError(f"{where}: unknown keys {sorted(unknown)}")

        file = _path(table.get("file", f"{name}.json"), f"{where}.file")
        if not file.is_absolute():
            file = path.parent / file
        ssh = None
        if "ssh" in table:
            ssh = _path(table["ssh"], f"{where}.ssh")
            if not ssh.is_absolute():
                raise HostsError(f"{where}.ssh: use an absolute path or one starting with ~")
        ansible = table.get("ansible")
        if ansible is not None and not isinstance(ansible, dict):
            raise HostsError(f"{where}.ansible: expected a table")
        inventories[name] = InventoryConfig(name, file, ssh, ansible)

    default = data.get("default")
    if default is not None and default not in inventories:
        raise HostsError(f"{tilde(path)}: default {default!r} is not a declared inventory")
    return Config(path, inventories, default)
