"""ansible: an Ansible YAML inventory directory.

Export writes the hosts file plus one file per routing entry, with a small emitter of its own:
pyyaml can't write comments, and the shape is fixed, so the same inventory always gives the same
bytes. Import reads values through pyyaml, which drops comments; every comment it skips is listed
so zone headers and reasons can be filled in by hand.
"""

import fnmatch
import ipaddress
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..errors import HostsError
from ..models import Defaults, GroupDef, Host, Inventory, KeyDef, check_port, check_token, declare_key, unprintable
from ..paths import tilde
from . import ImportResult, Module, Output

GROUP_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
RESERVED_GROUPS = {"all", "ungrouped"}
HEADER_WIDTH = 62  # a zone header, after the indent, as the files have it today
MODE = 0o644  # inventory files are read by whoever runs the playbooks, not only by their owner

# all.vars and host vars that map onto host fields. The key file becomes a declared key.
_VARS = {"ansible_user": "user", "ansible_ssh_private_key_file": "keys", "ansible_port": "port"}

# The public halves of the keys ssh offers a host, for ansible.posix.authorized_key to deploy.
AUTHORIZED = "ari_authorized_keys"

# A host name Ansible's inventory reads as a host and a port: a name as its parse_address takes one,
# dot-separated labels of word characters and dashes, then a colon and digits. Anything else with
# a colon, IPv6 included, it takes whole.
_LABEL = r"\w[\w-]*(?<![_-])"
_HOST_PORT = re.compile(rf"({_LABEL}(?:\.{_LABEL})*):([0-9]+)")


def name_problem(name: str) -> str | None:
    """Why Ansible would read a host name as something else, or None. Its inventory expands
    web[01:02] into two hosts and refuses a whole file holding a [ that isn't a range, and reads
    db:2222 as db on port 2222."""
    if "[" in name:
        return f"name {name!r} can't be written: Ansible reads [ as the start of a range, and stops reading a file whose [ isn't one"
    port = _HOST_PORT.fullmatch(name)
    if port:
        return f"name {name!r} can't be written: Ansible reads it as host {port[1]} on port {int(port[2])}"
    return None


@dataclass(frozen=True)
class Settings:
    dir: Path
    hosts: str
    zones: tuple[str, ...]
    routes: tuple[tuple[str, tuple[str, ...]], ...]  # (file, globs), in config order
    authorized_keys: bool = False  # write AUTHORIZED


def _file_name(value: Any, where: str) -> str:
    if not isinstance(value, str) or "/" in value or not value.endswith((".yml", ".yaml")):
        raise HostsError(f"{where}: {value!r} must be a file name ending in .yml or .yaml")
    return value


def _globs(value: Any, where: str) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value or not all(isinstance(g, str) and g for g in value):
        raise HostsError(f"{where}: expected a glob or a list of globs")
    return tuple(value)


def _matches(name: str, globs: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(name, g) for g in globs)


# Emitter


def scalar(value: str | int) -> str:
    """Plain when YAML reads it back as the same string, otherwise double-quoted."""
    if isinstance(value, int):
        return str(value)
    try:
        if yaml.safe_load(value) == value:
            return value
    except yaml.YAMLError:
        pass
    quoted = json.dumps(value, ensure_ascii=False)
    try:
        if yaml.safe_load(quoted) == value:
            return quoted
    except yaml.YAMLError:
        pass
    return json.dumps(value)


def _address_key(host: Host) -> tuple:
    try:
        ip = ipaddress.ip_address(host.hostname)
        return (0, ip.version, int(ip), host.name.casefold())
    except ValueError:
        return (1, 0, 0, host.name.casefold())


def sections(inventory: Inventory, hosts: list[Host], settings: Settings) -> list[tuple[str | None, list[Host]]]:
    """Hosts as the hosts file lists them: by zone in declaration order, by address within a zone."""
    if not settings.zones:
        return [(None, sorted(hosts, key=_address_key))]
    out = []
    for group in inventory.groups:
        if _matches(group, settings.zones):
            members = [h for h in hosts if group in h.groups]
            if members:
                out.append((group, sorted(members, key=_address_key)))
    return out


def _header(text: str) -> str:
    line = f"# ── {text} "
    return "    " + line + "─" * max(HEADER_WIDTH - len(line), 3)


def _first_key(inventory: Inventory, names: list[str]) -> str | None:
    """ansible_ssh_private_key_file takes one file: the first key's."""
    paths = inventory.key_paths(names)
    return paths[0] if paths else None


def _pubs(inventory: Inventory, names: list[str]) -> list[str]:
    """The public halves of the named keys, as stored. With AUTHORIZED on, validate refuses a key
    without one before anything renders."""
    return [inventory.keys[name].pub or "" for name in names if name in inventory.keys]


Value = str | int | list[str]


def _host_vars(inventory: Inventory, host: Host, authorized: bool = False) -> list[tuple[str, Value]]:
    d = inventory.defaults
    out: list[tuple[str, Value]] = [("ansible_host", host.hostname)]
    if host.notes:
        out.append(("description", host.notes))
    if host.user is not None and host.user != d.user:
        out.append(("ansible_user", host.user))
    if host.port is not None and host.port != (d.port or 22):
        out.append(("ansible_port", host.port))
    key = _first_key(inventory, host.keys)
    if key is not None and key != _first_key(inventory, d.keys):
        out.append(("ansible_ssh_private_key_file", key))
    if authorized:
        # A host var replaces all.vars' list whole, as a host's keys replace the defaults', so it's
        # written only where the host's list differs.
        pubs = _pubs(inventory, inventory.host_keys(host))
        if pubs and pubs != _pubs(inventory, d.keys):
            out.append((AUTHORIZED, pubs))
    return out


def _var(indent: str, key: str, value: Value) -> list[str]:
    """One var: a scalar on its line, or a list as a block sequence below it."""
    if isinstance(value, list):
        return [f"{indent}{key}:"] + [f"{indent}  - {scalar(item)}" for item in value]
    return [f"{indent}{key}: {scalar(value)}"]


def render_hosts(inventory: Inventory, parts: list[tuple[str | None, list[Host]]], authorized: bool = False) -> str:
    d = inventory.defaults
    all_vars: list[tuple[str, Value]] = [
        (k, v)
        for k, v in (
            ("ansible_user", d.user),
            ("ansible_ssh_private_key_file", _first_key(inventory, d.keys)),
            ("ansible_port", d.port),
        )
        if v is not None
    ]
    if authorized and d.keys:
        all_vars.append((AUTHORIZED, _pubs(inventory, d.keys)))
    lines = ["all:"]
    if all_vars:
        lines.append("  vars:")
        for k, v in all_vars:
            lines += _var("    ", k, v)
    if any(members for _, members in parts):
        lines.append("  hosts:")
        for zone, members in parts:
            if zone is not None:
                first, *rest = inventory.groups[zone].description.splitlines() or [zone]
                lines += ["", _header(first)]
                lines += [f"    # {line}".rstrip() for line in rest]
            for host in members:
                lines.append(f"    {scalar(host.name)}:")
                for k, v in _host_vars(inventory, host, authorized):
                    lines += _var("      ", k, v)
    elif not all_vars:
        lines = ["all: {}"]
    return "\n".join(lines) + "\n"


def render_groups(inventory: Inventory, names: list[str], order: list[Host]) -> str:
    """One routed file. Hosts follow the hosts file's order; those with a reason come after the
    rest, under the reason's comment, reasons in declared order."""
    if not names:
        return "all:\n  children: {}\n"
    lines = ["all:", "  children:"]
    for name in names:
        group = inventory.groups[name]
        lines += ["", f"    {scalar(name)}:"]
        if group.children:
            lines.append("      children:")
            lines += [f"        {scalar(c)}:" for c in group.children]
        members = [h for h in order if name in h.groups]
        if members:
            lines.append("      hosts:")
            lines += [f"        {scalar(h.name)}:" for h in members if name not in h.reasons]
            for key, text in group.reasons.items():
                these = [h for h in members if h.reasons.get(name) == key]
                if these:
                    lines += [f"        # {line}".rstrip() for line in text.splitlines() or [""]]
                    lines += [f"        {scalar(h.name)}:" for h in these]
    return "\n".join(lines) + "\n"


def routing(inventory: Inventory, settings: Settings) -> dict[str, list[str]]:
    """Each routed file and its groups, in declaration order. A group goes to the first file that
    matches; validation refuses the ambiguous ones before anything is written."""
    out: dict[str, list[str]] = {name: [] for name, _ in settings.routes}
    for group in inventory.groups:
        target = next((name for name, globs in settings.routes if _matches(group, globs)), None)
        if target:
            out[target].append(group)
    return out


# Import


@dataclass
class _Reader:
    warnings: list[str] = field(default_factory=list)
    files: list[tuple[Path, bytes]] = field(default_factory=list)
    defaults: dict[str, Any] = field(default_factory=dict)
    saw_vars: bool = False
    hosts: dict[str, Host] = field(default_factory=dict)
    hosts_file: str | None = None
    groups: dict[str, GroupDef] = field(default_factory=dict)
    route: dict[str, str] = field(default_factory=dict)  # group: the file that defines it
    referenced: dict[str, str] = field(default_factory=dict)  # child named but not defined: where
    members: dict[str, list[str]] = field(default_factory=dict)
    loose: dict[str, str] = field(default_factory=dict)  # hosts ungrouped lists, by where it lists them
    keys: dict[str, KeyDef] = field(default_factory=dict)
    lossy: bool = False
    # AUTHORIZED, read to be checked against the record and never stored: all.vars' list and each host's own.
    authorized: bool = False
    default_pubs: list[str] | None = None
    pubs: dict[str, list[str]] = field(default_factory=dict)

    def _lose(self, message: str) -> None:
        """Something Ansible would use that the import leaves out."""
        self.warnings.append(message)
        self.lossy = True

    def _pubs(self, value: Any, where: str) -> list[str] | None:
        if not isinstance(value, list) or not all(isinstance(v, str) and v and "\n" not in v for v in value):
            self._lose(f"{where}: {AUTHORIZED} isn't a list of one-line strings; not imported")
            return None
        self.authorized = True
        return value

    def read(self, path: Path) -> None:
        where = tilde(path)
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except OSError as e:
            raise HostsError(f"{where}: {e.strerror}") from None
        except UnicodeDecodeError:
            raise HostsError(f"{where}: not UTF-8 text") from None
        self.files.append((path, raw))
        for n, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                self.warnings.append(f"{where}:{n}: comment not imported: {line.strip()}")
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise HostsError(f"{where}: not valid YAML ({e})") from None
        if data is None:
            return
        if not isinstance(data, dict):
            raise HostsError(f"{where}: expected a mapping at the top")
        for key, value in data.items():
            if key == "all":
                self._all(value, path)
            else:
                self._group(str(key), value, path)

    def _mapping(self, value: Any, where: str) -> dict:
        if value is None:
            return {}
        if not isinstance(value, dict):
            self._lose(f"{where}: not a mapping; skipped")
            return {}
        return value

    def _all(self, value: Any, path: Path) -> None:
        where = tilde(path)
        if value is None:
            return
        if not isinstance(value, dict):
            self._lose(f"{where}: all is not a mapping; skipped")
            return
        for key, data in value.items():
            if key == "vars":
                self._all_vars(data, where)
            elif key == "hosts":
                for name, host_vars in self._mapping(data, f"{where}: all.hosts").items():
                    self._define(str(name), host_vars, path)
            elif key == "children":
                for name, group in self._mapping(data, f"{where}: all.children").items():
                    self._group(str(name), group, path)
            else:
                self._lose(f"{where}: all.{key} not imported")

    def _all_vars(self, data: Any, where: str) -> None:
        if not isinstance(data, dict):
            self._lose(f"{where}: all.vars is not a mapping; skipped")
            return
        self.saw_vars = True
        for key, value in data.items():
            if key == AUTHORIZED:
                pubs = self._pubs(value, f"{where}: all.vars")
                if pubs is not None and self.default_pubs is not None and pubs != self.default_pubs:
                    self._lose(f"{where}: all.vars.{key} differs from an earlier file; kept the first")
                elif pubs is not None:
                    self.default_pubs = pubs
                continue
            field_name = _VARS.get(key)
            if field_name is None:
                self._lose(f"{where}: all.vars.{key} not imported")
                continue
            value = self._field(field_name, value, f"{where}: all.vars")
            if value is None:
                continue
            if field_name in self.defaults and self.defaults[field_name] != value:
                self._lose(f"{where}: all.vars.{key} differs from an earlier file; kept the first")
                continue
            if field_name == "keys":
                declare_key(self.keys, value)  # in the order it's read, so the default key is named first
            self.defaults[field_name] = value

    def _field(self, field_name: str, value: Any, where: str) -> Any:
        if field_name == "port":
            if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
                value = int(value)  # a quoted port; Ansible takes it as the number
            try:
                return check_port(value, where)
            except HostsError as e:
                self._lose(f"{e}; not imported")
                return None
        if not isinstance(value, str) or not value or "\n" in value:
            self._lose(f"{where}: {value!r} isn't a one-line string; not imported")
            return None
        return value

    def _define(self, name: str, data: Any, path: Path) -> None:
        where = f"{tilde(path)} ({name})"
        try:
            check_token(name, "name", tilde(path))
        except HostsError as e:
            self._lose(f"{e}; host skipped")
            return
        data = data or {}
        if not isinstance(data, dict):
            self._lose(f"{where}: host vars are not a mapping; host skipped")
            return
        hostname = data.get("ansible_host", name)
        if not isinstance(hostname, str):
            # YAML reads ansible_host: no as false; "False" would be an address nobody wrote.
            self._lose(f"{where}: ansible_host {hostname!r} isn't a string; host skipped")
            return
        if not hostname or any(c.isspace() for c in hostname):
            self._lose(f"{where}: ansible_host {hostname!r} has spaces; host skipped")
            return
        host = Host(name=name, hostname=hostname)
        pubs = None
        for key, value in data.items():
            if key == "ansible_host":
                continue
            if key == AUTHORIZED:
                pubs = self._pubs(value, where)
            elif key == "description":
                if isinstance(value, str):
                    host.notes = value
                elif value is not None:
                    self._lose(f"{where}: description {value!r} isn't a string; not imported")
            elif key in _VARS:
                value = self._field(_VARS[key], value, where)
                if _VARS[key] == "keys":
                    host.keys = [declare_key(self.keys, value)] if value is not None else []
                else:
                    setattr(host, _VARS[key], value)
            else:
                self._lose(f"{where}: {key} not imported")
        existing = self.hosts.get(name)
        if existing is None:
            self.hosts[name] = host
            self.hosts_file = self.hosts_file or path.name
            if pubs is not None:
                self.pubs[name] = pubs
        elif existing.to_dict() != host.to_dict() or self.pubs.get(name) != pubs:
            self._lose(f"{where}: defined again with different vars; kept the first")

    def _ungrouped(self, data: Any, path: Path) -> None:
        """Ansible puts every host in no other group into ungrouped itself, so a file that lists it
        says nothing ari doesn't: its hosts come in without it, and the group isn't declared."""
        where = f"{tilde(path)} (ungrouped)"
        self.warnings.append(f"{where}: ungrouped is the group Ansible gives hosts in no other; its hosts are imported without it")
        for key, value in self._mapping(data, where).items():
            if key != "hosts":
                self._lose(f"{where}: {key} not imported")
                continue
            for host, host_vars in self._mapping(value, f"{where}: hosts").items():
                if host_vars:
                    self._define(str(host), host_vars, path)
                self.loose.setdefault(str(host), path.name)

    def _group(self, name: str, data: Any, path: Path) -> None:
        if name == "ungrouped":
            self._ungrouped(data, path)
            return
        where = f"{tilde(path)} ({name})"
        if name not in self.groups:
            self.groups[name] = GroupDef()
            self.route[name] = path.name
            self.referenced.pop(name, None)
        elif self.route[name] != path.name:
            self.warnings.append(f"{where}: also defined in {self.route[name]}; routed there")
        if data is None:
            return
        if not isinstance(data, dict):
            self._lose(f"{where}: not a mapping; skipped")
            return
        group = self.groups[name]
        for key, value in data.items():
            if key == "hosts":
                for host, host_vars in self._mapping(value, f"{where}: hosts").items():
                    host = str(host)
                    if host_vars:
                        self._define(host, host_vars, path)
                    listed = self.members.setdefault(name, [])
                    if host not in listed:
                        listed.append(host)
            elif key == "children":
                for child, child_data in self._mapping(value, f"{where}: children").items():
                    child = str(child)
                    if child not in group.children:
                        group.children.append(child)
                    if child_data is not None:
                        self._group(child, child_data, path)
                    elif child not in self.groups:
                        self.referenced.setdefault(child, path.name)
            else:
                self._lose(f"{where}: {key} not imported")

    def result(self, directory: Path) -> ImportResult:
        for name, file in self.referenced.items():
            if name not in self.groups:
                self.groups[name] = GroupDef()
                self.route[name] = file
        rank = {name: i for i, name in enumerate(self.groups)}
        for group, names in self.members.items():
            for name in names:
                host = self.hosts.get(name)
                if host is None:
                    self._lose(f"{self.route[group]}: {group} lists {name}, which no hosts section defines; left out")
                elif group not in host.groups:
                    host.groups.append(group)
        for name, file in self.loose.items():
            if name not in self.hosts:
                self._lose(f"{file}: ungrouped lists {name}, which no hosts section defines; left out")
        for host in self.hosts.values():
            host.groups.sort(key=rank.__getitem__)

        routes: dict[str, list[str]] = {}
        for group, file in self.route.items():
            routes.setdefault(file, []).append(group)
        hosts_file = self.hosts_file or "00-hosts.yml"
        stray = routes.pop(hosts_file, [])
        if stray:
            # Export writes the hosts file with hosts only, so its groups need a file of their own.
            taken = {path.name for path, _ in self.files} | set(routes)
            stem, suffix = Path(hosts_file).stem, Path(hosts_file).suffix
            file, n = f"{stem}-groups{suffix}", 2
            while file in taken:
                file, n = f"{stem}-groups-{n}{suffix}", n + 1
            routes[file] = stray
            self.warnings.append(
                f"{hosts_file} defines groups as well as hosts ({', '.join(stray)}); export writes the hosts file with"
                f" hosts only, so they need a file in the groups table, and the suggested one is {file}"
            )
        settings: dict[str, Any] = {"dir": tilde(directory), "hosts": hosts_file}
        if self.authorized:
            settings["authorized_keys"] = True
        if routes:
            settings["groups"] = dict(sorted(routes.items()))
        defaults = None
        if self.saw_vars:
            path = self.defaults.get("keys")
            keys = [declare_key(self.keys, path)] if path is not None else []
            defaults = Defaults(self.defaults.get("user"), self.defaults.get("port"), keys)
        hosts = list(self.hosts.values())
        # A host's own list, else all.vars', as Ansible reads them.
        pubs = [self.pubs.get(h.name, self.default_pubs) for h in hosts] if self.authorized else None
        return ImportResult(
            hosts,
            self.warnings,
            self.files,
            defaults,
            self.groups,
            settings,
            self.lossy,
            keys=self.keys,
            first_key_only=True,
            pubs=pubs,
        )


class AnsibleModule(Module):
    name = "ansible"
    summary = "Ansible YAML inventory: a hosts file plus routed group files"
    exports = True
    imports = True

    def settings(self, table: dict[str, Any], where: str) -> Settings:
        table = dict(table)
        directory = table.pop("dir", None)
        hosts = _file_name(table.pop("hosts", "00-hosts.yml"), f"{where}.hosts")
        zones = _globs(table.pop("zones"), f"{where}.zones") if "zones" in table else ()
        groups = table.pop("groups", {})
        authorized = table.pop("authorized_keys", False)
        if table:
            raise HostsError(f"{where}: unknown keys {sorted(table)}")
        if not isinstance(authorized, bool):
            raise HostsError(f"{where}.authorized_keys: use true or false")
        if not isinstance(directory, str) or not directory:
            raise HostsError(f'{where}: needs dir = "~/path/to/inventory"')
        path = Path(directory).expanduser()
        if not path.is_absolute():
            raise HostsError(f"{where}.dir: use an absolute path or one starting with ~")
        if not isinstance(groups, dict):
            raise HostsError(f"{where}.groups: expected a table of FILE = [GLOB, ...]")
        routes = []
        for name, globs in groups.items():
            _file_name(name, f"{where}.groups")
            if name == hosts:
                raise HostsError(f"{where}.groups: {name} is the hosts file")
            routes.append((name, _globs(globs, f"{where}.groups.{name}")))
        return Settings(path, hosts, zones, tuple(routes), authorized)

    def host_data(self, data: dict[str, Any], where: str) -> dict[str, Any]:
        if data:
            raise HostsError(f"{where}: unknown keys {sorted(data)}")
        return data

    def defaults_data(self, data: dict[str, Any], where: str) -> dict[str, Any]:
        return self.host_data(data, where)

    def host_problems(self, inventory: Inventory, host: Host) -> list[tuple[str | None, str]]:
        problem = name_problem(host.name)
        return [("name", problem)] if problem else []

    def validate(self, inventory: Inventory, hosts: list[Host], settings: Settings) -> list[str]:
        problems = []
        for name, group in inventory.groups.items():
            # Descriptions and reasons go out as comments, and YAML refuses a file holding one of
            # these anywhere: Ansible would skip it whole.
            for what, text in [("description", group.description), *((f"reason {k!r}", t) for k, t in group.reasons.items())]:
                bad = unprintable(text)
                if bad:
                    problems.append(
                        f"group {name!r} {what} holds {', '.join(map(repr, bad))}, which YAML can't carry, so Ansible would skip"
                        f" the whole file; change it with ari group {name} -i {inventory.name}"
                    )
        for name in inventory.groups:
            if name in RESERVED_GROUPS:
                problems.append(f"group {name!r} is one Ansible makes itself; remove it with ari group {name} --rm -i {inventory.name}")
            elif not GROUP_NAME.fullmatch(name):
                problems.append(f"group {name!r} isn't a usable Ansible group name (letters, digits, _)")
            files = [file for file, globs in settings.routes if _matches(name, globs)]
            if not files:
                problems.append(f"group {name!r} matches no file in the groups table")
            elif len(files) > 1:
                problems.append(f"group {name!r} matches more than one file: {', '.join(files)}")
        if settings.zones:
            for host in hosts:
                zones = [g for g in host.groups if _matches(g, settings.zones)]
                if not zones:
                    problems.append(f"{host.name} is in no zone")
                elif len(zones) > 1:
                    problems.append(f"{host.name} is in more than one zone: {', '.join(zones)}")
        if settings.authorized_keys:
            # The keys AUTHORIZED carries: the defaults', in all.vars whatever the hosts do, and each host's own.
            names = [*inventory.defaults.keys, *(name for host in hosts for name in host.keys)]
            for name in dict.fromkeys(names):
                key = inventory.keys.get(name)
                if key is not None and key.pub is None:
                    problems.append(
                        f"key {name!r} has no pub to write in {AUTHORIZED}; fill it with ari key {name} --pub -i {inventory.name}"
                    )
        return problems

    def export(self, inventory: Inventory, hosts: list[Host], settings: Settings) -> list[Output]:
        """The hosts file and the routed group files, 0644 like the rest of the repository they live in."""
        parts = sections(inventory, hosts, settings)
        order = [h for _, members in parts for h in members]
        hosts_file = render_hosts(inventory, parts, settings.authorized_keys).encode("utf-8")
        outputs = [Output(settings.dir / settings.hosts, hosts_file, len(order), MODE)]
        for file, names in routing(inventory, settings).items():
            count = len({h.name for h in order if any(g in h.groups for g in names)})
            outputs.append(Output(settings.dir / file, render_groups(inventory, names, order).encode("utf-8"), count, MODE))
        return outputs

    def read(self, source: str | None) -> ImportResult:
        if not source:
            raise HostsError("ansible import needs the inventory directory: ari import ansible DIR")
        directory = Path(source).expanduser().resolve()
        if not directory.is_dir():
            raise HostsError(f"{tilde(directory)}: not a directory")
        paths = sorted(p for p in directory.iterdir() if p.is_file() and p.suffix in (".yml", ".yaml"))
        if not paths:
            raise HostsError(f"{tilde(directory)}: no .yml or .yaml files")
        reader = _Reader()
        for path in paths:
            reader.read(path)
        for name in ("group_vars", "host_vars"):
            # Ansible reads these beside the inventory files, and what they set wins over them.
            if (directory / name).is_dir():
                reader.warnings.append(
                    f"{tilde(directory / name)}: Ansible reads it too, and ari doesn't: ansible_host, ansible_user,"
                    " ansible_port or a key file set there overrides what export writes"
                )
        return reader.result(directory)
