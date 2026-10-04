"""Formats as modules.

A module turns an inventory into files (export), reads a source into hosts (import), or both.
Modules never write to disk: export returns (path, bytes) and the core does the writing, behind
the hash guard and all-or-nothing validation. Every module, built in or not, is found through the
ari.modules entry point group.
"""

from dataclasses import dataclass, field
from functools import cache
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, ClassVar

from ..errors import HostsError
from ..models import MODULE_NAME, Defaults, GroupDef, Host, Inventory

GROUP = "ari.modules"
RESERVED = {"file"}  # keys an inventory table already uses


@dataclass(frozen=True)
class Output:
    """One file a module wants written. The mode travels with the bytes: the module knows who
    should read each file it writes, and 0600 keeps a module that never says so private."""

    path: Path
    data: bytes
    hosts: int
    mode: int = 0o600


@dataclass
class ImportResult:
    hosts: list[Host]
    warnings: list[str] = field(default_factory=list)
    # Sources that were read, so the guard can record them and a later export over them passes.
    files: list[tuple[Path, bytes]] = field(default_factory=list)
    # What a source says beyond its hosts, for formats that carry it.
    defaults: Defaults | None = None
    groups: dict[str, GroupDef] = field(default_factory=dict)
    # A config.toml table for this module that would write back to the source.
    settings: dict[str, Any] | None = None
    # Something in the source that changes behaviour couldn't be represented. An export over
    # these files would drop it, so the import doesn't adopt them for the guard.
    lossy: bool = False


class Module:
    """Base class. A module overrides what its format supports; every default here is a no-op."""

    name: ClassVar[str] = ""
    summary: ClassVar[str] = ""
    exports: ClassVar[bool] = False
    imports: ClassVar[bool] = False

    def settings(self, table: dict[str, Any], where: str) -> Any:
        """Check this module's table in config.toml ("enabled" already removed); return settings."""
        if table:
            raise HostsError(f"{where}: unknown keys {sorted(table)}")
        return None

    def host_data(self, data: dict[str, Any], where: str) -> dict[str, Any]:
        """Check this module's part of a host record."""
        return data

    def defaults_data(self, data: dict[str, Any], where: str) -> dict[str, Any]:
        """Check this module's part of the inventory defaults."""
        return data

    def strip_defaults(self, defaults: dict[str, Any], data: dict[str, Any]) -> None:
        """Remove from a newly imported host whatever its inventory defaults already say."""

    def complete(self, host: Host) -> None:
        """A host new to the inventory: fill what the source left unset the way this format reads
        a missing value. Until then unset means not stated, so a re-import, or a later entry for a
        host already read, compares and fills only what the source says."""

    def merge(self, existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
        """Fold re-imported data into a host without overwriting anything; report whether it changed."""
        changed = False
        for key, value in incoming.items():
            if key not in existing:
                existing[key] = value
                changed = True
        return changed

    def conflicts(self, existing: dict[str, Any], incoming: dict[str, Any], defaults: dict[str, Any]) -> list[str]:
        """On re-import: values the source sets differently from the record, which merge would
        otherwise drop silently. Each is reported and the host is left alone."""
        return []

    def describe(self, inventory: Inventory, host: Host) -> list[str]:
        """Lines for `ari show`."""
        return [f"{k} {v}" for k, v in host.modules.get(self.name, {}).items()]

    def validate(self, inventory: Inventory, hosts: list[Host], settings: Any) -> list[str]:
        """Format rules the hosts must meet before anything is written."""
        return []

    def export(self, inventory: Inventory, hosts: list[Host], settings: Any) -> list[Output]:
        return []

    def read(self, source: str | None) -> ImportResult:
        raise HostsError(f"the {self.name} module can't import")


@dataclass
class Registry:
    modules: dict[str, Module]
    failures: dict[str, str]

    def get(self, name: str) -> Module:
        if name in self.modules:
            return self.modules[name]
        if name in self.failures:
            raise HostsError(f"module {name!r} failed to load: {self.failures[name]}")
        if not self.modules:
            raise HostsError("no modules found; ari isn't installed as a package (uv sync, or uv tool install)")
        known = ", ".join(sorted(self.modules))
        raise HostsError(f"unknown module {name!r} (installed: {known})")


@cache
def registry() -> Registry:
    modules: dict[str, Module] = {}
    failures: dict[str, str] = {}
    for ep in entry_points(group=GROUP):
        if not MODULE_NAME.fullmatch(ep.name) or ep.name in RESERVED:
            failures[ep.name] = "not a usable module name"
            continue
        if ep.name in modules:
            failures[ep.name] = f"registered twice ({ep.value})"
            continue
        try:
            module = ep.load()()
        except Exception as e:  # a broken plugin must not take the rest of ari down with it
            failures[ep.name] = f"{type(e).__name__}: {e}"
            continue
        if module.name != ep.name:
            failures[ep.name] = f"registered as {ep.name!r} but calls itself {module.name!r}"
            continue
        modules[ep.name] = module
    return Registry(dict(sorted(modules.items())), failures)


def check_data(inventory: Inventory) -> None:
    """Let each installed module check its part of the records. Data for a module that isn't
    installed stays as it is, so removing a plugin never loses or blocks anything."""
    installed = registry().modules
    where = str(inventory.path)
    for name, data in list(inventory.defaults.modules.items()):
        if name in installed:
            inventory.defaults.modules[name] = installed[name].defaults_data(data, f"{where}: defaults.modules.{name}")
    for host in inventory.hosts:
        for name, data in list(host.modules.items()):
            if name in installed:
                host.modules[name] = installed[name].host_data(data, f"{where}: {host.name}: modules.{name}")
