"""Operations behind every command. No printing here: the CLI and the TUI both call these."""

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import sshconf, storage
from .config import Config, tilde
from .errors import HostsError
from .guard import Guard, Status
from .models import Host, Inventory, now


def load_all(cfg: Config) -> dict[str, Inventory]:
    return {name: storage.load(ic) for name, ic in cfg.inventories.items()}


# Reading


def _haystack(host: Host) -> list[str]:
    return [
        host.name,
        host.hostname,
        *host.aliases,
        host.user or "",
        host.ssh_key or "",
        host.notes,
        *host.groups,
        *host.ssh_options.values(),
    ]


def list_hosts(
    cfg: Config, scope: str | None = None, search: str | None = None, group: str | None = None
) -> list[tuple[Inventory, Host]]:
    rows = []
    needle = search.casefold() if search else None
    for ic in cfg.scope(scope):
        inventory = storage.load(ic)
        for host in inventory.hosts:
            if group and group not in host.groups:
                continue
            if needle and not any(needle in s.casefold() for s in _haystack(host)):
                continue
            rows.append((inventory, host))
    return rows


def find_host(cfg: Config, token: str) -> tuple[Inventory, Host]:
    for inventory in load_all(cfg).values():
        host = inventory.find(token)
        if host:
            return inventory, host
    raise HostsError(f"no host named {token!r} in any inventory")


# Validation


def name_problems(inventories: list[Inventory]) -> list[str]:
    """Every name and alias lands in one ssh namespace where the first match wins silently."""
    owner: dict[str, str] = {}
    problems = []
    for inventory in inventories:
        for host in inventory.hosts:
            where = f"{inventory.name}/{host.name}"
            for token in host.tokens():
                key = token.casefold()
                if key in owner and owner[key] != where:
                    problems.append(f"{token!r} is used by both {owner[key]} and {where}")
                owner.setdefault(key, where)
    return problems


# Import


@dataclass
class ImportReport:
    inventory: str
    source: str
    added: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    defaults_set: dict[str, str] = field(default_factory=dict)


def _infer_defaults(inventory: Inventory, hosts: list[Host]) -> dict[str, str]:
    """For a brand new inventory: the user and key most hosts share become defaults."""
    chosen = {}
    for attr in ("user", "ssh_key"):
        counts = Counter(getattr(h, attr) for h in hosts if getattr(h, attr))
        if counts:
            value, count = counts.most_common(1)[0]
            if count >= 2:
                setattr(inventory.defaults, attr, value)
                chosen[attr] = value
    return chosen


def _strip_defaults(inventory: Inventory, host: Host) -> None:
    """Store only what differs from the inventory defaults."""
    d = inventory.defaults
    if host.user == d.user:
        host.user = None
    if host.port == (d.port or 22):
        host.port = None
    if host.ssh_key is not None and host.ssh_key == d.ssh_key:
        host.ssh_key = None
    for k, v in d.ssh_options.items():
        if host.ssh_options.get(k) == v:
            del host.ssh_options[k]


def _difference(inventory: Inventory, existing: Host, incoming: Host) -> str | None:
    if incoming.hostname != existing.hostname:
        return f"HostName {incoming.hostname} differs from {existing.hostname}"
    if incoming.user != inventory.user(existing):
        return f"User {incoming.user} differs from {inventory.user(existing)}"
    if incoming.port != inventory.port(existing):
        return f"Port {incoming.port} differs from {inventory.port(existing)}"
    key = inventory.ssh_key(existing)
    if incoming.ssh_key and key and incoming.ssh_key != key:
        return f"IdentityFile {incoming.ssh_key} differs from {key}"
    return None


def _merge(inventory: Inventory, existing: Host, incoming: Host) -> bool:
    changed = False
    have = {t.casefold() for t in existing.tokens()}
    for alias in incoming.aliases:
        if alias.casefold() not in have:
            existing.aliases.append(alias)
            have.add(alias.casefold())
            changed = True
    if incoming.ssh_key and not inventory.ssh_key(existing):
        existing.ssh_key = incoming.ssh_key
        changed = True
    current = {k.casefold() for k in inventory.ssh_options(existing)}
    for k, v in incoming.ssh_options.items():
        if k.casefold() not in current:
            existing.ssh_options[k] = v
            changed = True
    return changed


def import_ssh(cfg: Config, inventory_name: str, file: Path, ansible: bool = True) -> ImportReport:
    path = file.expanduser()
    try:
        data = path.read_bytes()
        text = data.decode("utf-8")
    except OSError as e:
        raise HostsError(f"{tilde(path)}: {e.strerror}") from None
    except UnicodeDecodeError:
        raise HostsError(f"{tilde(path)}: not UTF-8 text") from None

    inventories = load_all(cfg)
    target = inventories[cfg.get(inventory_name).name]
    report = ImportReport(target.name, tilde(path))

    blocks, report.warnings = sshconf.parse(text, tilde(path))
    incoming = [h for b in blocks if (h := sshconf.to_host(b, tilde(path), report.warnings))]

    if not target.hosts and target.defaults.is_empty():
        report.defaults_set = _infer_defaults(target, incoming)

    elsewhere = {
        token.casefold(): f"{inv.name}/{host.name}"
        for inv in inventories.values()
        if inv is not target
        for host in inv.hosts
        for token in host.tokens()
    }

    for host in incoming:
        clash = next((elsewhere[t.casefold()] for t in host.tokens() if t.casefold() in elsewhere), None)
        if clash:
            report.conflicts.append(f"{host.name}: already defined as {clash}")
            continue
        existing = target.find(host.name)
        if existing is None:
            if host.ssh_key is None and target.defaults.ssh_key:
                report.warnings.append(f"{host.name}: no IdentityFile in the source; the default key will apply")
            _strip_defaults(target, host)
            host.ansible = ansible
            host.last_updated = now()
            target.hosts.append(host)
            report.added.append(host.name)
            continue
        difference = _difference(target, existing, host)
        if difference:
            report.conflicts.append(f"{host.name}: {difference}; not merged")
        elif _merge(target, existing, host):
            existing.last_updated = now()
            report.merged.append(host.name)
        else:
            report.unchanged.append(host.name)

    if report.added or report.merged or report.defaults_set:
        storage.save(target)
    guard = Guard()
    guard.record(path, data)
    guard.save()
    return report


# Export


@dataclass
class Planned:
    path: Path
    data: bytes
    inventory: str
    kind: str
    hosts: int


@dataclass
class Written:
    planned: Planned
    status: Status


@dataclass
class ExportReport:
    written: list[Written] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def export(cfg: Config, scope: str | None = None, targets: frozenset[str] = frozenset({"ssh", "ansible"}), force: bool = False) -> ExportReport:
    """Validate everything, check every target against the guard, then write. Any failure writes nothing."""
    inventories = load_all(cfg)
    problems = name_problems(list(inventories.values()))
    if problems:
        raise HostsError("export stopped, nothing written:\n  " + "\n  ".join(problems))

    report = ExportReport()
    planned: list[Planned] = []
    for ic in cfg.scope(scope):
        inventory = inventories[ic.name]
        if "ssh" in targets:
            if ic.ssh is None:
                report.notes.append(f"{ic.name}: no ssh target in config, skipped")
            else:
                data = sshconf.render(inventory).encode("utf-8")
                planned.append(Planned(ic.ssh, data, ic.name, "ssh", len(inventory.hosts)))
        if "ansible" in targets and ic.ansible is not None:
            report.notes.append(f"{ic.name}: Ansible export isn't built yet, skipped")

    paths = Counter(p.path.resolve() for p in planned)
    shared = [tilde(p) for p, n in paths.items() if n > 1]
    if shared:
        raise HostsError(f"export stopped, nothing written: several inventories write {', '.join(shared)}")

    guard = Guard()
    checked = [(p, guard.status(p.path, p.data)) for p in planned]
    blocked = [(p, s) for p, s in checked if s.blocks]
    if blocked and not force:
        lines = [
            f"{tilde(p.path)}: " + ("edited since the last export" if s is Status.CHANGED else "exists and wasn't written by ari")
            for p, s in blocked
        ]
        raise HostsError(
            "export stopped, nothing written:\n  " + "\n  ".join(lines) + "\nreview the file, then rerun with --force"
        )

    for p, s in checked:
        if s is not Status.SAME:
            storage.atomic_write(p.path, p.data)
        guard.record(p.path, p.data)
        report.written.append(Written(p, s))
    guard.save()
    return report
