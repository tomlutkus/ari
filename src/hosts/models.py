"""Inventory data model and its JSON form."""

import getpass
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import HostsError

SCHEMA_VERSION = 1

# One ssh Host token that names a single host: no whitespace, no pattern characters.
_TOKEN = re.compile(r"[^\s*?!#,\"'=]+")


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def check_token(value: str, what: str, where: str) -> str:
    if not _TOKEN.fullmatch(value):
        raise HostsError(f"{where}: {what} {value!r} is not a valid ssh host name")
    return value


def check_port(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise HostsError(f"{where}: port must be an integer 1-65535, got {value!r}")
    return value


def _fold_get(options: dict[str, str], key: str) -> str | None:
    """ssh keywords are case-insensitive; look one up that way."""
    for k, v in options.items():
        if k.casefold() == key.casefold():
            return v
    return None


class _Fields:
    """Reads typed fields from one JSON object and rejects keys nobody asked for."""

    def __init__(self, data: Any, where: str):
        if not isinstance(data, dict):
            raise HostsError(f"{where}: expected an object")
        self.data = data
        self.where = where
        self.used: set[str] = set()

    def raw(self, key: str, required: bool = False) -> Any:
        self.used.add(key)
        if key not in self.data:
            if required:
                raise HostsError(f"{self.where}: missing {key!r}")
            return None
        return self.data[key]

    def text(self, key: str, required: bool = False) -> str | None:
        value = self.raw(key, required)
        if value is not None and not isinstance(value, str):
            raise HostsError(f"{self.where}: {key!r} must be a string")
        return value

    def integer(self, key: str, required: bool = False) -> int | None:
        value = self.raw(key, required)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise HostsError(f"{self.where}: {key!r} must be an integer")
        return value

    def boolean(self, key: str, default: bool) -> bool:
        value = self.raw(key)
        if value is None:
            return default
        if not isinstance(value, bool):
            raise HostsError(f"{self.where}: {key!r} must be true or false")
        return value

    def strings(self, key: str) -> list[str]:
        value = self.raw(key)
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise HostsError(f"{self.where}: {key!r} must be a list of strings")
        return list(value)

    def mapping(self, key: str) -> dict[str, str]:
        value = self.raw(key)
        if value is None:
            return {}
        if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
            raise HostsError(f"{self.where}: {key!r} must map strings to strings")
        return dict(value)

    def done(self) -> None:
        unknown = set(self.data) - self.used
        if unknown:
            raise HostsError(f"{self.where}: unknown keys {sorted(unknown)}")


@dataclass
class Host:
    name: str
    hostname: str
    aliases: list[str] = field(default_factory=list)
    user: str | None = None
    port: int | None = None
    ssh_key: str | None = None
    ssh_options: dict[str, str] = field(default_factory=dict)
    notes: str = ""
    groups: list[str] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)
    ansible: bool = True
    last_updated: str = ""

    def tokens(self) -> list[str]:
        """Everything that lands on the ssh Host line."""
        return [self.name, *self.aliases]

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "hostname": self.hostname}
        if self.aliases:
            out["aliases"] = self.aliases
        if self.user is not None:
            out["user"] = self.user
        if self.port is not None:
            out["port"] = self.port
        if self.ssh_key is not None:
            out["ssh_key"] = self.ssh_key
        if self.ssh_options:
            out["ssh_options"] = self.ssh_options
        if self.notes:
            out["notes"] = self.notes
        if self.groups:
            out["groups"] = self.groups
        if self.reasons:
            out["reasons"] = self.reasons
        if not self.ansible:
            out["ansible"] = False
        out["last_updated"] = self.last_updated
        return out

    @classmethod
    def from_dict(cls, data: Any, where: str) -> "Host":
        f = _Fields(data, where)
        name = check_token(f.text("name", required=True), "name", where)
        f.where = where = f"{where} ({name})"
        hostname = f.text("hostname", required=True)
        if not hostname or any(c.isspace() for c in hostname):
            raise HostsError(f"{where}: hostname must be non-empty, without spaces")
        port = f.integer("port")
        if port is not None:
            check_port(port, where)
        host = cls(
            name=name,
            hostname=hostname,
            aliases=[check_token(a, "alias", where) for a in f.strings("aliases")],
            user=f.text("user"),
            port=port,
            ssh_key=f.text("ssh_key"),
            ssh_options=f.mapping("ssh_options"),
            notes=f.text("notes") or "",
            groups=f.strings("groups"),
            reasons=f.mapping("reasons"),
            ansible=f.boolean("ansible", default=True),
            last_updated=f.text("last_updated") or "",
        )
        f.done()
        return host


@dataclass
class Defaults:
    user: str | None = None
    port: int | None = None
    ssh_key: str | None = None
    ssh_options: dict[str, str] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return self == Defaults()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.user is not None:
            out["user"] = self.user
        if self.port is not None:
            out["port"] = self.port
        if self.ssh_key is not None:
            out["ssh_key"] = self.ssh_key
        if self.ssh_options:
            out["ssh_options"] = self.ssh_options
        return out

    @classmethod
    def from_dict(cls, data: Any, where: str) -> "Defaults":
        f = _Fields(data, where)
        port = f.integer("port")
        if port is not None:
            check_port(port, where)
        defaults = cls(f.text("user"), port, f.text("ssh_key"), f.mapping("ssh_options"))
        f.done()
        return defaults


@dataclass
class GroupDef:
    description: str = ""
    children: list[str] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.description:
            out["description"] = self.description
        if self.children:
            out["children"] = self.children
        if self.reasons:
            out["reasons"] = self.reasons
        return out

    @classmethod
    def from_dict(cls, data: Any, where: str) -> "GroupDef":
        f = _Fields(data, where)
        group = cls(f.text("description") or "", f.strings("children"), f.mapping("reasons"))
        f.done()
        return group


@dataclass
class Inventory:
    name: str
    path: Path
    defaults: Defaults = field(default_factory=Defaults)
    groups: dict[str, GroupDef] = field(default_factory=dict)
    hosts: list[Host] = field(default_factory=list)
    last_updated: str = ""

    def find(self, token: str) -> Host | None:
        """A host by name or alias, compared the way ssh does: case-insensitively."""
        key = token.casefold()
        for host in self.hosts:
            if any(t.casefold() == key for t in host.tokens()):
                return host
        return None

    # Effective values: the host's own, else the inventory default, else what ssh would use.

    def user(self, host: Host) -> str:
        return host.user or self.defaults.user or getpass.getuser()

    def port(self, host: Host) -> int:
        return host.port or self.defaults.port or 22

    def ssh_key(self, host: Host) -> str | None:
        return host.ssh_key or self.defaults.ssh_key

    def ssh_options(self, host: Host) -> dict[str, str]:
        merged = {k: v for k, v in self.defaults.ssh_options.items() if _fold_get(host.ssh_options, k) is None}
        merged.update(host.ssh_options)
        return merged

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "last_updated": self.last_updated,
            "defaults": self.defaults.to_dict(),
            "groups": {name: group.to_dict() for name, group in self.groups.items()},
            "hosts": [host.to_dict() for host in self.hosts],
        }

    @classmethod
    def from_dict(cls, data: Any, name: str, path: Path) -> "Inventory":
        where = str(path)
        f = _Fields(data, where)
        version = f.integer("version", required=True)
        if version != SCHEMA_VERSION:
            raise HostsError(f"{where}: schema version {version} is not supported (expected {SCHEMA_VERSION})")
        groups_raw = f.raw("groups") or {}
        if not isinstance(groups_raw, dict):
            raise HostsError(f"{where}: 'groups' must be an object")
        hosts_raw = f.raw("hosts") or []
        if not isinstance(hosts_raw, list):
            raise HostsError(f"{where}: 'hosts' must be a list")
        inventory = cls(
            name=name,
            path=path,
            defaults=Defaults.from_dict(f.raw("defaults") or {}, f"{where}: defaults"),
            groups={g: GroupDef.from_dict(v, f"{where}: groups.{g}") for g, v in groups_raw.items()},
            hosts=[Host.from_dict(h, f"{where}: hosts[{i}]") for i, h in enumerate(hosts_raw)],
            last_updated=f.text("last_updated") or "",
        )
        f.done()
        seen: set[str] = set()
        for host in inventory.hosts:
            key = host.name.casefold()
            if key in seen:
                raise HostsError(f"{where}: host {host.name!r} appears twice")
            seen.add(key)
        return inventory
