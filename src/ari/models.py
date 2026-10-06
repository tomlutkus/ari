"""Inventory data model and its JSON form."""

import copy
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import HostsError

SCHEMA_VERSION = 3

# One ssh Host token that names a single host: no whitespace, no pattern characters.
_TOKEN = re.compile(r"[^\s*?!#,\"'=]+")

# Module names, as registered in the ari.modules entry point group.
MODULE_NAME = re.compile(r"[a-z][a-z0-9_]*")

# Key names: no spaces, so a list of them can be typed on one line, and no / or ~ to read as a path.
KEY_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+@-]*")


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


def check_key_name(value: str, where: str) -> str:
    if not KEY_NAME.fullmatch(value):
        raise HostsError(f"{where}: {value!r} can't be a key name: letters, digits and _ . + @ -, starting with a letter, digit or _")
    return value


def numbered(base: str, taken: Iterable[str]) -> str:
    """base, or base-2, base-3 and so on, whichever isn't taken."""
    taken = set(taken)
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    return name


def key_name(path: str, taken: Iterable[str]) -> str:
    """A name for the key at path: the file's stem, made usable as a name, numbered when taken."""
    base = re.sub(r"[^A-Za-z0-9_.+@-]+", "-", Path(path).stem).lstrip(".+@-") or "key"
    return numbered(base, taken)


def declare_key(keys: dict[str, "KeyDef"], path: str) -> str:
    """The name of the first key declared at path, declaring one when there's none."""
    for name, key in keys.items():
        if key.path == path:
            return name
    name = key_name(path, keys)
    keys[name] = KeyDef(path)
    return name


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

    def module_names(self, key: str) -> list[str]:
        names = self.strings(key)
        for name in names:
            if not MODULE_NAME.fullmatch(name):
                raise HostsError(f"{self.where}: {key!r} holds {name!r}, which isn't a module name")
        return names

    def namespaces(self, key: str) -> dict[str, dict[str, Any]]:
        """Per-module data. Shape only: each module checks its own part once modules are loaded."""
        value = self.raw(key)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise HostsError(f"{self.where}: {key!r} must be an object keyed by module name")
        for name, data in value.items():
            if not MODULE_NAME.fullmatch(name):
                raise HostsError(f"{self.where}: {key!r} has {name!r}, which isn't a module name")
            if not isinstance(data, dict):
                raise HostsError(f"{self.where}: {key}.{name} must be an object")
        return copy.deepcopy(value)

    def done(self) -> None:
        unknown = set(self.data) - self.used
        if unknown:
            raise HostsError(f"{self.where}: unknown keys {sorted(unknown)}")


def check_line(value: str | None, what: str, where: str) -> str | None:
    if value is not None and (not value or "\n" in value or "\r" in value):
        raise HostsError(f"{where}: {what} must be one non-empty line")
    return value


@dataclass
class KeyDef:
    """A declared key: the private key file ssh offers, under a name hosts and defaults list.
    pub, the public half, is kept as written; ari key --new and --pub fill it and ari key checks it."""

    path: str
    pub: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"path": self.path}
        if self.pub is not None:
            out["pub"] = self.pub
        return out

    @classmethod
    def from_dict(cls, data: Any, where: str) -> "KeyDef":
        f = _Fields(data, where)
        key = cls(check_line(f.text("path", required=True), "path", where), check_line(f.text("pub"), "pub", where))
        f.done()
        return key


@dataclass
class Host:
    name: str
    hostname: str
    aliases: list[str] = field(default_factory=list)
    user: str | None = None
    port: int | None = None
    keys: list[str] = field(default_factory=list)
    os: str = ""  # what the host runs, as written by hand; the record's alone, no module exports it
    notes: str = ""
    groups: list[str] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)
    exclude: list[str] = field(default_factory=list)
    modules: dict[str, dict[str, Any]] = field(default_factory=dict)
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
        if self.keys:
            out["keys"] = self.keys
        if self.os:
            out["os"] = self.os
        if self.notes:
            out["notes"] = self.notes
        if self.groups:
            out["groups"] = self.groups
        if self.reasons:
            out["reasons"] = self.reasons
        if self.exclude:
            out["exclude"] = self.exclude
        modules = {name: data for name, data in self.modules.items() if data}
        if modules:
            out["modules"] = modules
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
            keys=f.strings("keys"),
            os=check_line(f.text("os"), "os", where) or "",
            notes=f.text("notes") or "",
            groups=f.strings("groups"),
            reasons=f.mapping("reasons"),
            exclude=f.module_names("exclude"),
            modules=f.namespaces("modules"),
            last_updated=f.text("last_updated") or "",
        )
        f.done()
        return host


@dataclass
class Defaults:
    user: str | None = None
    port: int | None = None
    keys: list[str] = field(default_factory=list)
    modules: dict[str, dict[str, Any]] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return self == Defaults()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.user is not None:
            out["user"] = self.user
        if self.port is not None:
            out["port"] = self.port
        if self.keys:
            out["keys"] = self.keys
        modules = {name: data for name, data in self.modules.items() if data}
        if modules:
            out["modules"] = modules
        return out

    @classmethod
    def from_dict(cls, data: Any, where: str) -> "Defaults":
        f = _Fields(data, where)
        port = f.integer("port")
        if port is not None:
            check_port(port, where)
        defaults = cls(f.text("user"), port, f.strings("keys"), f.namespaces("modules"))
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
    keys: dict[str, KeyDef] = field(default_factory=dict)

    def find(self, token: str) -> Host | None:
        """A host by name or alias, ignoring case: no two names differ only in case."""
        key = token.casefold()
        for host in self.hosts:
            if any(t.casefold() == key for t in host.tokens()):
                return host
        return None

    # Effective values: the host's own, else the inventory default. A user of None means
    # "no User line": ssh picks the login of whoever connects, on whichever machine.

    def user(self, host: Host) -> str | None:
        return host.user or self.defaults.user

    def port(self, host: Host) -> int:
        return host.port or self.defaults.port or 22

    def host_keys(self, host: Host) -> list[str]:
        """The names of the keys ssh offers the host, in order: its own list, else the default one."""
        return host.keys or self.defaults.keys

    def key_paths(self, names: list[str]) -> list[str]:
        """The files of the named keys, in order. A name nothing declares is left out; export refuses it."""
        return [self.keys[name].path for name in names if name in self.keys]

    def identity_files(self, host: Host) -> list[str]:
        return self.key_paths(self.host_keys(host))

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "last_updated": self.last_updated,
            "defaults": self.defaults.to_dict(),
            "keys": {name: key.to_dict() for name, key in self.keys.items()},
            "groups": {name: group.to_dict() for name, group in self.groups.items()},
            "hosts": [host.to_dict() for host in self.hosts],
        }

    @classmethod
    def from_dict(cls, data: Any, name: str, path: Path) -> "Inventory":
        where = str(path)
        if isinstance(data, dict) and data.get("version") == 1:
            data = _upgrade_v1(data)
        if isinstance(data, dict) and data.get("version") == 2:
            data = _upgrade_v2(data, where)
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
        keys_raw = f.raw("keys")
        keys_raw = {} if keys_raw is None else keys_raw
        if not isinstance(keys_raw, dict):
            raise HostsError(f"{where}: 'keys' must be an object")
        keys = {check_key_name(k, f"{where}: keys"): KeyDef.from_dict(v, f"{where}: keys.{k}") for k, v in keys_raw.items()}
        inventory = cls(
            name=name,
            path=path,
            defaults=Defaults.from_dict(f.raw("defaults") or {}, f"{where}: defaults"),
            groups={g: GroupDef.from_dict(v, f"{where}: groups.{g}") for g, v in groups_raw.items()},
            hosts=[Host.from_dict(h, f"{where}: hosts[{i}]") for i, h in enumerate(hosts_raw)],
            last_updated=f.text("last_updated") or "",
            keys=keys,
        )
        f.done()
        seen: set[str] = set()
        for host in inventory.hosts:
            key = host.name.casefold()
            if key in seen:
                raise HostsError(f"{where}: host {host.name!r} appears twice")
            seen.add(key)
        return inventory


def _upgrade_v1(data: dict[str, Any]) -> dict[str, Any]:
    """Version 1 kept ssh options and the Ansible opt-out on the host itself.
    Version 2 moves them into module namespaces. The file converts on its next save."""
    data = copy.deepcopy(data)
    data["version"] = 2
    defaults = data.get("defaults")
    if isinstance(defaults, dict) and "ssh_options" in defaults:
        defaults.setdefault("modules", {})["ssh"] = {"options": defaults.pop("ssh_options")}
    for host in data.get("hosts") or []:
        if not isinstance(host, dict):
            continue
        if "ssh_options" in host:
            host.setdefault("modules", {})["ssh"] = {"options": host.pop("ssh_options")}
        if host.pop("ansible", True) is False:
            host["exclude"] = ["ansible"]
    return data


def _upgrade_v2(data: dict[str, Any], where: str) -> dict[str, Any]:
    """Version 2 kept a key's path on each host and in the defaults. Version 3 declares each key
    once under keys, named after its file's stem and numbered on a clash, and hosts and defaults
    list the names. Paths don't change, so neither does anything exported. The file converts on
    its next save."""
    data = copy.deepcopy(data)
    if "keys" in data:
        raise HostsError(f"{where}: unknown keys ['keys']")
    data["version"] = 3
    keys: dict[str, KeyDef] = {}

    def convert(record: Any, label: str) -> None:
        if not isinstance(record, dict) or "ssh_key" not in record:
            return
        path = record.pop("ssh_key")
        if path is None or path == "":
            return  # version 2 read both as no key of its own
        if not isinstance(path, str):
            raise HostsError(f"{label}: 'ssh_key' must be a string")
        record["keys"] = [declare_key(keys, path)]

    convert(data.get("defaults"), f"{where}: defaults")
    hosts = data.get("hosts")
    for i, host in enumerate(hosts if isinstance(hosts, list) else []):
        convert(host, f"{where}: hosts[{i}]")
    data["keys"] = {name: key.to_dict() for name, key in keys.items()}
    return data
