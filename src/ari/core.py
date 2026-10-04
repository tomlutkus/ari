"""Operations behind every command. No printing here: the CLI and the TUI both call these."""

import copy
import getpass
import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from . import storage
from .config import STARTER, Config
from .errors import HostsError
from .guard import Guard, Status
from .models import MODULE_NAME, GroupDef, Host, Inventory, check_port, check_token, now
from .modules import Module, registry
from .paths import config_file, tilde


def init_config(path: Path | None = None) -> Path:
    """Write the starter config.toml. The only write ari ever makes to it, and only when there's none."""
    path = path or config_file()
    try:
        storage.atomic_write(path, STARTER.encode("utf-8"), exclusive=True)
    except FileExistsError:
        raise HostsError(f"{tilde(path)} already exists; ari init never overwrites it") from None
    return path


def load_all(cfg: Config) -> dict[str, Inventory]:
    return {name: storage.load(ic) for name, ic in cfg.inventories.items()}


# Reading


def _haystack(inventory: Inventory, host: Host) -> list[str]:
    """What a search looks through: what ls shows, with user, port and key as they take effect,
    inherited from the inventory defaults or not, plus everything else on the record."""
    return [
        host.name,
        host.hostname,
        *host.aliases,
        inventory.user(host) or "",
        str(inventory.port(host)),
        inventory.ssh_key(host) or "",
        host.notes,
        *host.groups,
        *host.reasons.values(),
        *host.exclude,
        *(str(v) for data in host.modules.values() for v in _flatten(data)),
    ]


def _flatten(value: object) -> list[object]:
    if isinstance(value, dict):
        return [x for v in value.values() for x in _flatten(v)]
    if isinstance(value, list):
        return [x for v in value for x in _flatten(v)]
    return [value]


def matches(inventory: Inventory, host: Host, text: str) -> bool:
    """Whether text appears in anything a search looks through, ignoring case. Empty text matches every host."""
    needle = text.casefold()
    return any(needle in s.casefold() for s in _haystack(inventory, host))


def list_hosts(
    cfg: Config, scope: str | None = None, search: str | None = None, group: str | None = None
) -> list[tuple[Inventory, Host]]:
    rows = []
    for ic in cfg.scope(scope):
        inventory = storage.load(ic)
        for host in inventory.hosts:
            if group and group not in host.groups:
                continue
            if search and not matches(inventory, host, search):
                continue
            rows.append((inventory, host))
    return rows


class ModuleReach(Enum):
    """Whether export writes a host through a module. Each value is what ls and the TUI show."""

    WRITTEN = "✓"
    EXCLUDED = "excluded"  # the host lists the module in exclude
    OFF = "·"  # the host's inventory doesn't have the module enabled


def export_modules(cfg: Config, scope: str | None = None) -> list[str]:
    """Modules that write files for at least one inventory in scope, in the order config.toml first names them."""
    names: list[str] = []
    for ic in cfg.scope(scope):
        for mc in ic.enabled():
            if mc.module.exports and mc.module.name not in names:
                names.append(mc.module.name)
    return names


def module_reach(cfg: Config, inventory: Inventory, host: Host, module: str) -> ModuleReach:
    """The rule export follows: the inventory has the module enabled and the host doesn't exclude it."""
    mc = cfg.get(inventory.name).modules.get(module)
    if mc is None or not mc.enabled or not mc.module.exports:
        return ModuleReach.OFF
    return ModuleReach.EXCLUDED if module in host.exclude else ModuleReach.WRITTEN


@dataclass
class Detail:
    """One line of show: a label, its value, and a note after the value such as (default)."""

    label: str
    value: str
    note: str = ""


def host_details(inventory: Inventory, host: Host) -> list[Detail]:
    """Every field of a host with its effective value, as show prints it and the TUI shows it."""

    def inherited(label: str, own: object, effective: object) -> Detail:
        if effective is None:
            return Detail(label, "-")
        return Detail(label, str(effective), "" if own is not None else "(default)")

    user = inventory.user(host)
    groups = [f"{g} ({host.reasons[g]})" if g in host.reasons else g for g in host.groups]
    details = [
        Detail("inventory", inventory.name),
        Detail("name", host.name),
        Detail("aliases", " ".join(host.aliases) or "-"),
        Detail("hostname", host.hostname),
        inherited("user", host.user, user) if user else Detail("user", getpass.getuser(), "(whoever connects)"),
        inherited("port", host.port, inventory.port(host)),
        inherited("ssh key", host.ssh_key, inventory.ssh_key(host)),
        Detail("notes", host.notes or "-"),
        Detail("groups", ", ".join(groups) or "-"),
        Detail("exclude", ", ".join(host.exclude) or "-"),
    ]
    installed = registry().modules
    for name in sorted(set(installed) | set(host.modules)):
        if name in installed:
            lines = installed[name].describe(inventory, host)
        else:
            lines = [f"{k} {v}" for k, v in host.modules[name].items()] + ["(module not installed)"]
        if lines:
            details.append(Detail(name, "\n".join(lines)))
    details.append(Detail("updated", host.last_updated or "-"))
    return details


def _locate(cfg: Config, inventories: dict[str, Inventory], token: str, scope: str | None) -> tuple[Inventory, Host]:
    for ic in cfg.scope(scope):
        host = inventories[ic.name].find(token)
        if host:
            return inventories[ic.name], host
    raise HostsError(f"no host named {token!r} in " + (scope if scope else "any inventory"))


def find_host(cfg: Config, token: str, scope: str | None = None) -> tuple[Inventory, Host]:
    """A host by name or alias, in the inventory named by scope or in any. Only what's searched is read."""
    inventories = {ic.name: storage.load(ic) for ic in cfg.scope(scope)}
    return _locate(cfg, inventories, token, scope)


@dataclass
class ModuleStatus:
    module: Module
    inventories: dict[str, bool]  # inventory name: enabled


def module_status(cfg: Config) -> tuple[list[ModuleStatus], dict[str, str]]:
    """Every installed module, where it's switched on, and any that failed to load."""
    reg = registry()
    rows = []
    for name, module in reg.modules.items():
        where = {ic.name: ic.modules[name].enabled for ic in cfg.inventories.values() if name in ic.modules}
        rows.append(ModuleStatus(module, where))
    return rows, dict(reg.failures)


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


def _membership_problems(inventory: Inventory, host: Host) -> list[str]:
    """Every group a host is in is declared, so a typo fails instead of making a new group."""
    where = f"{inventory.name} ({host.name})"
    problems = []
    for group in host.groups:
        if group not in inventory.groups:
            problems.append(f"{where}: group {group!r} isn't declared; ari group {group} -i {inventory.name} declares it")
    for group, reason in host.reasons.items():
        if group not in host.groups:
            problems.append(f"{where}: has a reason for {group!r} without being in it")
        elif group in inventory.groups and reason not in inventory.groups[group].reasons:
            known = ", ".join(inventory.groups[group].reasons) or "none"
            problems.append(f"{where}: group {group!r} has no reason {reason!r} (declared: {known})")
    return problems


def group_problems(inventory: Inventory) -> list[str]:
    return [p for host in inventory.hosts for p in _membership_problems(inventory, host)] + _structure_problems(inventory)


def _structure_problems(inventory: Inventory) -> list[str]:
    """Every child is declared, and children form no cycle."""
    problems = []
    groups = inventory.groups
    for name, group in groups.items():
        for child in group.children:
            if child not in groups:
                problems.append(f"{inventory.name}: group {name!r} has child {child!r}, which isn't declared")

    finished: set[str] = set()
    reported: set[frozenset[str]] = set()

    def walk(name: str, trail: list[str]) -> None:
        for child in groups[name].children:
            if child not in groups or child in finished:
                continue
            if child in trail:
                cycle = trail[trail.index(child):] + [child]
                if frozenset(cycle) not in reported:
                    reported.add(frozenset(cycle))
                    problems.append(f"{inventory.name}: groups {' > '.join(cycle)} form a cycle")
            else:
                walk(child, trail + [child])
        finished.add(name)

    for name in groups:
        if name not in finished:
            walk(name, [name])
    return problems


# Groups


@dataclass
class GroupRow:
    inventory: Inventory
    name: str
    group: GroupDef
    direct: int  # hosts that list the group
    total: int  # those plus the hosts its children bring, as Ansible counts them


def _reach(inventory: Inventory, groups: dict[str, GroupDef], name: str, trail: tuple[str, ...] = ()) -> set[str]:
    """A group's hosts as Ansible sees them: the ones that list it, and the ones any group below it holds."""
    found = {h.name for h in inventory.hosts if name in h.groups}
    for child in groups[name].children if name in groups else ():
        if child in groups and child != name and child not in trail:
            found |= _reach(inventory, groups, child, (*trail, name))
    return found


def _hosts(inventory: Inventory, names: set[str] | list[str], how: str = "") -> str:
    """A count, how the hosts got there, then their names in inventory order: '2 hosts through 'x': a, b'."""
    ordered = [h.name for h in inventory.hosts if h.name in names]
    return f"{len(ordered)} host{'s' if len(ordered) != 1 else ''}{how}: {', '.join(ordered)}"


def list_groups(cfg: Config, scope: str | None = None) -> list[GroupRow]:
    """Declared groups in declaration order, every inventory unless scope names one."""
    rows = []
    for ic in cfg.scope(scope):
        inventory = storage.load(ic)
        for name, group in inventory.groups.items():
            direct = sum(name in h.groups for h in inventory.hosts)
            rows.append(GroupRow(inventory, name, group, direct, len(_reach(inventory, inventory.groups, name))))
    return rows


@dataclass
class GroupChanges:
    """What ari group changes. A description of None leaves it alone and '' clears it; a reason with
    empty text is removed. Values arrive as strings, as with Changes."""

    description: str | None = None
    children: list[str] = field(default_factory=list)
    unchild: list[str] = field(default_factory=list)
    reasons: list[tuple[str, str]] = field(default_factory=list)
    remove: bool = False


_BAD_GROUP_NAME = re.compile(r"[\s:]")


def _removal_problems(inventory: Inventory, name: str) -> list[str]:
    """A group can go only when no host is in it, directly or through a child, and no group lists it as a child."""
    where = f"{inventory.name}: group {name!r}"
    groups = inventory.groups
    problems = []
    direct = [h.name for h in inventory.hosts if name in h.groups]
    if direct:
        problems.append(f"{where} still has {_hosts(inventory, direct)}")
    seen = set(direct)
    for child in groups[name].children:
        if child in groups and child != name:
            via = _reach(inventory, groups, child, (name,)) - seen
            if via:
                problems.append(f"{where} still has {_hosts(inventory, via, f' through its child {child!r}')}")
            seen |= via
    parents = [repr(p) for p, g in groups.items() if name in g.children and p != name]
    if parents:
        problems.append(f"{where} is a child of {', '.join(parents)}; --unchild it there first")
    return problems


def write_group(cfg: Config, inventory_name: str, name: str, c: GroupChanges) -> tuple[Inventory, str]:
    """Declare, change or remove one group. Returns the inventory and declared, updated, unchanged or
    removed. Nothing is saved if any check fails, and every problem is listed."""
    inventory = storage.load(cfg.get(inventory_name))
    existing = inventory.groups.get(name)
    if c.remove:
        if c != GroupChanges(remove=True):
            raise HostsError("--rm takes no other options")
        if existing is None:
            raise HostsError(f"no group {name!r} in {inventory.name}")
        problems = _removal_problems(inventory, name)
        if problems:
            raise HostsError("nothing saved:\n  " + "\n  ".join(problems))
        del inventory.groups[name]
        storage.save(inventory)
        return inventory, "removed"

    where = f"{inventory.name}: group {name!r}"
    problems = []
    if existing is None and (not name or _BAD_GROUP_NAME.search(name)):
        problems.append(f"{inventory.name}: {name!r} can't be a group name: it needs a character, and no spaces or colons")
    group = copy.deepcopy(existing) if existing else GroupDef()
    if c.description is not None:
        group.description = c.description
    for child in c.unchild:  # removals first, as with --unalias
        if child in group.children:
            group.children.remove(child)
        else:
            problems.append(f"{where} has no child {child!r}")
    for child in c.children:
        if child not in group.children:
            group.children.append(child)
    for key, text in c.reasons:
        if text:
            group.reasons[key] = text
        elif key in group.reasons:
            del group.reasons[key]
        else:
            problems.append(f"{where} has no reason {key!r} to remove")

    after = copy.copy(inventory)
    after.groups = {**inventory.groups, name: group}
    if existing is not None:
        # Cutting a child takes the parent away from every host it reached only through that child.
        lost = _reach(inventory, inventory.groups, name) - _reach(after, after.groups, name)
        for child in existing.children:
            via = lost & _reach(inventory, inventory.groups, child, (name,))
            if via and child not in group.children:
                problems.append(f"{where} would lose {_hosts(inventory, via, f' it reaches only through {child!r}')}")
                lost -= via
        for key in existing.reasons:
            users = [h.name for h in inventory.hosts if h.reasons.get(name) == key]
            if key not in group.reasons and users:
                problems.append(f"{where}: reason {key!r} is still used by {_hosts(inventory, users)}")
    before = _structure_problems(inventory)
    problems += [p for p in _structure_problems(after) if p not in before]
    try:
        Inventory.from_dict(after.to_dict(), name=after.name, path=after.path)
    except HostsError as e:
        problems.append(str(e))
    if problems:
        raise HostsError("nothing saved:\n  " + "\n  ".join(problems))
    if existing is not None and group == existing:
        return inventory, "unchanged"
    inventory.groups[name] = group
    storage.save(inventory)
    return inventory, "declared" if existing is None else "updated"


# Import


@dataclass
class ImportReport:
    inventory: str
    source: str
    added: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)  # hosts that fail the checks every write runs
    warnings: list[str] = field(default_factory=list)
    unadopted: list[str] = field(default_factory=list)  # sources the guard didn't record
    defaults_set: dict[str, str] = field(default_factory=dict)
    defaults_from: str = "the most common values"
    groups_added: list[str] = field(default_factory=list)
    settings: dict | None = None  # a config table the source suggests, for the module that read it


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


def _module(name: str) -> Module:
    """The installed module, or the base behaviour for data whose module isn't installed."""
    return registry().modules.get(name) or Module()


def _strip_defaults(inventory: Inventory, host: Host) -> None:
    """Store only what differs from the inventory defaults."""
    d = inventory.defaults
    if host.user is not None and host.user == d.user:
        host.user = None
    if host.port == (d.port or 22):
        host.port = None
    if host.ssh_key is not None and host.ssh_key == d.ssh_key:
        host.ssh_key = None
    for name, data in host.modules.items():
        _module(name).strip_defaults(d.modules.get(name, {}), data)
    host.modules = {name: data for name, data in host.modules.items() if data}


def _difference(inventory: Inventory, existing: Host, incoming: Host) -> str | None:
    """What the source sets differently from the record. Merge only fills gaps, so anything
    returned here would otherwise be dropped while the report said nothing. A field the source
    leaves unset, an empty hostname or no port, says nothing either way."""
    if incoming.hostname and incoming.hostname != existing.hostname:
        return f"HostName {incoming.hostname} differs from {existing.hostname}"
    user = inventory.user(existing)
    if incoming.user and user and incoming.user != user:
        return f"User {incoming.user} differs from {user}"
    if incoming.port and incoming.port != inventory.port(existing):
        return f"Port {incoming.port} differs from {inventory.port(existing)}"
    key = inventory.ssh_key(existing)
    if incoming.ssh_key and key and incoming.ssh_key != key:
        return f"IdentityFile {incoming.ssh_key} differs from {key}"
    for name, data in incoming.modules.items():
        found = _module(name).conflicts(existing.modules.get(name, {}), data, inventory.defaults.modules.get(name, {}))
        if found:
            return "; ".join(found)
    return None


def _merge(inventory: Inventory, existing: Host, incoming: Host) -> bool:
    changed = False
    if incoming.notes and not existing.notes:
        existing.notes = incoming.notes
        changed = True
    for group in incoming.groups:
        if group not in existing.groups:
            existing.groups.append(group)
            changed = True
    have = {t.casefold() for t in existing.tokens()}
    for alias in incoming.aliases:
        if alias.casefold() not in have:
            existing.aliases.append(alias)
            have.add(alias.casefold())
            changed = True
    if incoming.user and not inventory.user(existing):
        existing.user = incoming.user
        changed = True
    if incoming.ssh_key and not inventory.ssh_key(existing):
        existing.ssh_key = incoming.ssh_key
        changed = True
    for name, data in incoming.modules.items():
        target = existing.modules.setdefault(name, {})
        changed |= _module(name).merge(target, data)
    existing.modules = {name: data for name, data in existing.modules.items() if data}
    return changed


def import_hosts(
    cfg: Config, inventory_name: str, module_name: str, source: str | None = None, exclude: list[str] | None = None
) -> ImportReport:
    module = registry().get(module_name)
    if not module.imports:
        raise HostsError(f"the {module_name} module can't import")
    exclude = sorted(set(exclude or []))
    for name in exclude:
        if not MODULE_NAME.fullmatch(name):
            raise HostsError(f"--exclude {name!r} isn't a module name")

    result = module.read(source)
    inventories = load_all(cfg)
    target = inventories[cfg.get(inventory_name).name]
    label = tilde(result.files[0][0]) if result.files else (source or module_name)
    report = ImportReport(target.name, label, warnings=list(result.warnings))

    # A host that would make the inventory unreadable never gets near it, nor near the defaults.
    readable = []
    for host in result.hosts:
        probe = copy.deepcopy(host)
        module.complete(probe)
        problems = _shape_problems(target, probe)
        if problems:
            report.refused += [p.message for p in problems]
        else:
            readable.append(host)
    result.hosts = readable

    fresh = not target.hosts and target.defaults.is_empty()
    if result.defaults is not None:
        # The source states its own defaults; hosts that leave a field unset inherit them there too.
        if fresh:
            d = result.defaults
            target.defaults.user, target.defaults.port, target.defaults.ssh_key = d.user, d.port, d.ssh_key
            report.defaults_set = {k: str(v) for k, v in (("user", d.user), ("port", d.port), ("ssh_key", d.ssh_key)) if v is not None}
            report.defaults_from = "the source"
        elif (result.defaults.user, result.defaults.port, result.defaults.ssh_key) != (
            target.defaults.user,
            target.defaults.port,
            target.defaults.ssh_key,
        ):
            report.warnings.append("the source's defaults differ from the inventory's; kept the inventory's")
    elif fresh:
        report.defaults_set = _infer_defaults(target, result.hosts)
    report.settings = result.settings

    groups_changed = False
    for name, group in result.groups.items():
        mine = target.groups.get(name)
        if mine is None:
            target.groups[name] = copy.deepcopy(group)
            report.groups_added.append(name)
            continue
        if group.description and not mine.description:
            mine.description = group.description
            groups_changed = True
        for child in group.children:
            if child not in mine.children:
                mine.children.append(child)
                groups_changed = True
        for key, text in group.reasons.items():
            if key not in mine.reasons:
                mine.reasons[key] = text
                groups_changed = True

    elsewhere = {
        token.casefold(): f"{inv.name}/{host.name}"
        for inv in inventories.values()
        if inv is not target
        for host in inv.hosts
        for token in host.tokens()
    }

    for host in result.hosts:
        clash = next((elsewhere[t.casefold()] for t in host.tokens() if t.casefold() in elsewhere), None)
        if clash:
            report.conflicts.append(f"{host.name}: already defined as {clash}")
            continue
        existing = target.find(host.name)
        if existing is None:
            inherits_user = result.defaults is None and host.user is None and target.defaults.user
            inherits_key = result.defaults is None and host.ssh_key is None and target.defaults.ssh_key
            module.complete(host)
            _strip_defaults(target, host)
            host.exclude = list(exclude)
            # Checked against everything already in the inventory, hosts added earlier in this
            # import included, so two blocks sharing an alias can't both get in.
            problems = _host_problems(inventories, target, host, None)
            if problems:
                report.refused += [p.message for p in problems]
                continue
            if inherits_user:
                report.warnings.append(f"{host.name}: no User in the source; the default user {target.defaults.user} will apply")
            if inherits_key:
                report.warnings.append(f"{host.name}: no IdentityFile in the source; the default key will apply")
            host.last_updated = now()
            target.hosts.append(host)
            report.added.append(host.name)
            continue
        difference = _difference(target, existing, host)
        if difference:
            report.conflicts.append(f"{host.name}: {difference}; not merged")
            continue
        candidate = copy.deepcopy(existing)
        _merge(target, candidate, host)
        _strip_defaults(target, candidate)
        if candidate.to_dict() == existing.to_dict():
            report.unchanged.append(host.name)
            continue
        problems = _host_problems(inventories, target, candidate, existing)
        if problems:
            report.refused += [p.message for p in problems]
            continue
        candidate.last_updated = now()
        target.hosts = [candidate if h is existing else h for h in target.hosts]
        report.merged.append(host.name)

    if report.added or report.merged or report.defaults_set or report.groups_added or groups_changed:
        storage.save(target)
    # The guard records a source only when the inventory now holds all of it, so an export over
    # that file loses nothing. Anything conflicted, refused or left out means a person looks first.
    if result.files:
        if report.conflicts or report.refused or result.lossy:
            report.unadopted = [tilde(path) for path, _ in result.files]
        else:
            guard = Guard()
            for path, data in result.files:
                guard.record(path, data)
            guard.save()
    return report


# Add, edit, rm


@dataclass
class Changes:
    """What add and edit change. None leaves a field alone. For user, port, key and notes an empty
    string clears the field, so the inventory default applies again; an option with an empty value
    is removed. Values arrive as strings, the way the CLI and the TUI's inputs hand them over."""

    hostname: str | None = None
    rename: str | None = None
    user: str | None = None
    port: str | None = None
    ssh_key: str | None = None
    notes: str | None = None
    aliases: list[str] = field(default_factory=list)
    unalias: list[str] = field(default_factory=list)
    options: list[tuple[str, str]] = field(default_factory=list)
    groups: list[tuple[str, str | None]] = field(default_factory=list)
    ungroup: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    include: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return self == Changes()


@dataclass(frozen=True)
class Problem:
    """Something that keeps a host from being saved, and the field it's about: one of the Changes
    fields, or None when it has none, like a module's data rules. The TUI shows each beside its input."""

    field: str | None
    message: str

    def __str__(self) -> str:
        return self.message


class HostRefused(HostsError):
    """add or edit saved nothing. problems holds every reason, each with its field."""

    def __init__(self, problems: list[Problem]) -> None:
        super().__init__("nothing saved:\n  " + "\n  ".join(p.message for p in problems))
        self.problems = problems


def _apply(host: Host, c: Changes) -> list[Problem]:
    """Apply changes in place. Returns what couldn't be applied; the record checks come after."""
    problems = []
    if c.rename is not None:
        host.name = c.rename
    if c.hostname is not None:
        host.hostname = c.hostname
    if c.user is not None:
        host.user = c.user or None
    if c.ssh_key is not None:
        host.ssh_key = c.ssh_key or None
    if c.notes is not None:
        host.notes = c.notes
    if c.port is not None:
        try:
            host.port = int(c.port) if c.port else None
        except ValueError:
            problems.append(Problem("port", f"port must be an integer 1-65535, got {c.port!r}"))
    # Removals first, so --unalias old --alias new swaps one for the other, a change of case included.
    for alias in c.unalias:
        match = next((a for a in host.aliases if a.casefold() == alias.casefold()), None)
        if match is None:
            problems.append(Problem("aliases", f"has no alias {alias!r}"))
        else:
            host.aliases.remove(match)
    host.aliases.extend(c.aliases)

    if c.options:
        own = host.modules.setdefault("ssh", {}).setdefault("options", {})
        for key, value in c.options:
            current = next((k for k in own if k.casefold() == key.casefold()), None)
            if value:
                own[current or key] = value  # a different spelling of a keyword it has keeps its place
            elif current:
                del own[current]
            else:
                problems.append(Problem("options", f"no ssh option {key} to clear"))

    for group, reason in c.groups:
        if group not in host.groups:
            host.groups.append(group)
        if reason:
            host.reasons[group] = reason
        else:
            host.reasons.pop(group, None)
    for group in c.ungroup:
        if group in host.groups:
            host.groups.remove(group)
            host.reasons.pop(group, None)
        else:
            problems.append(Problem("groups", f"isn't in group {group!r}"))

    exclude = set(host.exclude) | set(c.exclude)
    for module in c.include:
        if module in exclude:
            exclude.discard(module)
        else:
            problems.append(Problem("exclude", f"doesn't exclude {module!r}"))
    host.exclude = sorted(exclude)
    return problems


def _shape_problems(inventory: Inventory, host: Host) -> list[Problem]:
    """The record on its own: the rules load enforces, and each installed module's data rules.
    A host that fails these would make the inventory unreadable once saved. Name, hostname,
    port and aliases are each checked on their own, so every bad field is reported at once."""
    where = f"{inventory.name} ({host.name})"
    problems = []

    def check(field: str, test, *args) -> None:
        try:
            test(*args)
        except HostsError as e:
            problems.append(Problem(field, str(e)))

    # In the order load checks them, so a lone problem reads exactly as load would put it.
    check("name", check_token, host.name, "name", inventory.name)
    if not host.hostname or any(c.isspace() for c in host.hostname):
        problems.append(Problem("hostname", f"{where}: hostname must be non-empty, without spaces"))
    if host.port is not None:
        check("port", check_port, host.port, where)
    for alias in host.aliases:
        check("aliases", check_token, alias, "alias", where)
    if not problems:
        # Everything else load enforces, from the record as it would be saved.
        check(None, Host.from_dict, host.to_dict(), inventory.name)

    installed = registry().modules
    for name, data in host.modules.items():
        if name in installed:
            try:
                installed[name].host_data(copy.deepcopy(data), f"{where}: modules.{name}")
            except HostsError as e:
                # ssh options are the one module field add and edit set directly.
                problems.append(Problem("options" if name == "ssh" else None, str(e)))
    for field_name, label, value in (("user", "user", host.user), ("ssh_key", "key", host.ssh_key)):
        if value and ("\n" in value or "\r" in value):
            problems.append(Problem(field_name, f"{where}: {label} must be one line"))
    return problems


def _host_problems(
    inventories: dict[str, Inventory], inventory: Inventory, host: Host, original: Host | None
) -> list[Problem]:
    """Everything a host must meet before it's saved, by add, edit or import: its shape, names
    unique across all inventories, declared groups and valid reasons."""
    where = f"{inventory.name} ({host.name})"
    problems = _shape_problems(inventory, host)

    def field_of(token: str) -> str:
        return "name" if token == host.name else "aliases"

    seen: set[str] = set()
    for token in host.tokens():
        if token.casefold() in seen:
            problems.append(Problem("aliases", f"{where}: {token!r} appears twice among its names"))
        seen.add(token.casefold())
    owner = {
        token.casefold(): f"{inv.name}/{other.name}"
        for inv in inventories.values()
        for other in inv.hosts
        if other is not original
        for token in other.tokens()
    }
    for token in host.tokens():
        if token.casefold() in owner:
            problems.append(Problem(field_of(token), f"{where}: {token!r} is already used by {owner[token.casefold()]}"))
    return problems + [Problem("groups", p) for p in _membership_problems(inventory, host)]


def _write(inventories: dict[str, Inventory], inventory: Inventory, original: Host | None, host: Host, c: Changes) -> bool:
    """Apply, check, save. A host that fails any check is refused and nothing is saved."""
    problems = [Problem(p.field, f"{inventory.name} ({host.name}): {p.message}") for p in _apply(host, c)]
    _strip_defaults(inventory, host)
    problems += _host_problems(inventories, inventory, host, original)
    if problems:
        raise HostRefused(problems)
    if original is not None and host.to_dict() == original.to_dict():
        return False
    host.last_updated = now()
    if original is None:
        inventory.hosts.append(host)
    else:
        inventory.hosts = [host if h is original else h for h in inventory.hosts]
    storage.save(inventory)
    return True


def add_host(cfg: Config, inventory_name: str, name: str, hostname: str, changes: Changes) -> tuple[Inventory, Host]:
    for flag, used in (("--rename", changes.rename), ("--hostname", changes.hostname)):
        if used is not None:
            raise HostsError(f"{flag} is for edit; add takes the name and hostname as arguments")
    if changes.ungroup or changes.unalias or changes.include:
        raise HostsError("--ungroup, --unalias and --include are for edit")
    inventories = load_all(cfg)
    inventory = inventories[cfg.get(inventory_name).name]
    host = Host(name=name, hostname=hostname)
    _write(inventories, inventory, None, host, changes)
    return inventory, host


def edit_host(cfg: Config, token: str, changes: Changes, scope: str | None = None) -> tuple[Inventory, Host, bool]:
    """The host after editing, and whether anything changed."""
    if changes.is_empty():
        raise HostsError("nothing to change; pass at least one option")
    inventories = load_all(cfg)
    inventory, original = _locate(cfg, inventories, token, scope)
    host = copy.deepcopy(original)
    changed = _write(inventories, inventory, original, host, changes)
    return inventory, host, changed


def remove_host(cfg: Config, token: str, scope: str | None = None) -> tuple[Inventory, Host]:
    """Removing a host removes its group memberships with it: they live on the record."""
    inventories = load_all(cfg)
    inventory, host = _locate(cfg, inventories, token, scope)
    inventory.hosts = [h for h in inventory.hosts if h is not host]
    storage.save(inventory)
    return inventory, host


# Export


@dataclass
class Planned:
    path: Path
    data: bytes
    inventory: str
    module: str
    hosts: int
    mode: int


@dataclass
class Written:
    planned: Planned
    status: Status
    mode_fixed: bool = False  # the bytes already matched; only the file mode was corrected

    def summary(self) -> str:
        """The line export prints for this file."""
        p = self.planned
        verb = "unchanged" if self.status is Status.SAME else "wrote"
        hosts = f"{p.hosts} host{'s' if p.hosts != 1 else ''}"
        mode = f"; mode set to {p.mode:04o}" if self.mode_fixed else ""
        return f"{verb} {tilde(p.path)} ({p.inventory}, {p.module}, {hosts}){mode}"


class ExportStopped(HostsError):
    """An export that wrote nothing: every problem that stopped it, one each, and what to do next, if anything."""

    def __init__(self, message: str, problems: list[str], hint: str = "") -> None:
        super().__init__(message)
        self.problems = problems
        self.hint = hint


def _stopped(problems: list[str], hint: str = "") -> ExportStopped:
    message = "export stopped, nothing written:\n  " + "\n  ".join(problems) + (f"\n{hint}" if hint else "")
    return ExportStopped(message, problems, hint)


@dataclass
class ExportReport:
    written: list[Written] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def export(cfg: Config, scope: str | None = None, only: list[str] | None = None, force: bool = False) -> ExportReport:
    """Validate everything, check every target against the guard, then write. Any failure writes nothing."""
    for name in only or []:
        registry().get(name)
    inventories = load_all(cfg)
    problems = name_problems(list(inventories.values()))

    report = ExportReport()
    targets = []
    for ic in cfg.scope(scope):
        inventory = inventories[ic.name]
        problems += group_problems(inventory)
        for mc in ic.enabled():
            module = mc.module
            if not module.exports or (only and module.name not in only):
                continue
            hosts = [h for h in inventory.hosts if module.name not in h.exclude]
            problems += [f"{ic.name}/{module.name}: {p}" for p in module.validate(inventory, hosts, mc.settings)]
            targets.append((ic.name, inventory, mc, hosts))

    # Every check, every inventory, before any module renders: export only ever sees data that passed.
    if problems:
        raise _stopped(problems)
    planned = [
        Planned(out.path, out.data, name, mc.module.name, out.hosts, out.mode)
        for name, inventory, mc, hosts in targets
        for out in mc.module.export(inventory, hosts, mc.settings)
    ]
    if not planned:
        report.notes.append("nothing to export: no enabled module writes files here")
        return report

    paths = Counter(p.path.resolve() for p in planned)
    shared = [tilde(p) for p, n in paths.items() if n > 1]
    if shared:
        problem = f"more than one target writes {', '.join(shared)}"
        raise ExportStopped(f"export stopped, nothing written: {problem}", [problem])

    guard = Guard()
    checked = [(p, guard.status(p.path, p.data)) for p in planned]
    blocked = [(p, s) for p, s in checked if s.blocks]
    if blocked and not force:
        lines = [
            f"{tilde(p.path)}: " + ("edited since the last export" if s is Status.CHANGED else "exists and wasn't written by ari")
            for p, s in blocked
        ]
        raise _stopped(lines, "review the file, then rerun with --force")

    for p, s in checked:
        fixed = False
        if s is Status.SAME:
            fixed = storage.set_mode(p.path, p.mode)  # no write to carry the mode, so set it here
        else:
            storage.atomic_write(p.path, p.data, p.mode)
        guard.record(p.path, p.data)
        report.written.append(Written(p, s, fixed))
    guard.save()
    return report
