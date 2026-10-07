"""Operations behind every command. No printing here: the CLI and the TUI both call these."""

import copy
import getpass
import os
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from . import keyfiles, storage
from .columns import COLUMNS, Column
from .config import STARTER, Config
from .errors import HostsError
from .guard import Guard, Status
from .models import (
    KEY_NAME,
    MODULE_NAME,
    GroupDef,
    Host,
    Inventory,
    KeyDef,
    check_key_name,
    check_line,
    check_port,
    check_token,
    key_name,
    now,
    numbered,
    unprintable,
)
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
        *inventory.host_keys(host),
        *inventory.identity_files(host),
        host.os,
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


def on_disk(cfg: Config, inventory: Inventory, host: Host, module: str) -> str | None:
    """None when the module's files hold the host exactly as export would write it now; otherwise
    why not: the module wouldn't write it as its record stands, or a file is missing or older
    than the record. The TUI asks before s hands the terminal to ssh, which reads its own file
    rather than the inventory."""
    mc = cfg.get(inventory.name).modules[module]
    for _, problem in mc.module.host_problems(inventory, host):
        return problem
    for out in mc.module.export(inventory, [host], mc.settings):
        try:
            data = out.path.read_bytes()
        except OSError:
            return f"{tilde(out.path)} isn't there to read; export first"
        if mc.module.holds(inventory, host, mc.settings, data) is False:
            return f"{tilde(out.path)} doesn't hold {host.name} as the inventory has it; export first"
    return None


@dataclass
class Detail:
    """One line of show: a label, its value, and a note after the value such as (default)."""

    label: str
    value: str
    note: str = ""


def _keys_detail(inventory: Inventory, host: Host) -> Detail:
    """Each key the host offers, in order, one per line: its name and its file."""
    names = inventory.host_keys(host)
    lines = [f"{n} ({inventory.keys[n].path})" if n in inventory.keys else f"{n} (not declared)" for n in names]
    return Detail("keys", "\n".join(lines) or "-", "(default)" if names and not host.keys else "")


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
        _keys_detail(inventory, host),
        Detail("os", host.os or "-"),
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


# Columns ls shows


def ls_columns(cfg: Config, scope: str | None = None, names: list[str] | None = None) -> list[Column]:
    """The columns ls shows: those named, in order, else its own set. Beside the record's columns,
    each module that exports for an inventory in scope has one saying whether export writes the
    host there. A name that's neither, or given twice, is refused, naming the ones there are."""
    modules = export_modules(cfg, scope)
    if not names:
        names = ["name", "hostname", "user", "port", *modules, "groups", "inv"]
    columns = []
    for name in names:
        if name in COLUMNS:
            columns.append(COLUMNS[name])
        elif name in modules:
            columns.append(Column(name, name.upper(), lambda inventory, host, m=name: module_reach(cfg, inventory, host, m).value))
        else:
            known = ", ".join([*COLUMNS, *modules])
            raise HostsError(f"no column {name!r}; there are {known}")
        if names.count(name) > 1:
            raise HostsError(f"column {name!r} is named twice")
    return columns


def _locate(
    cfg: Config, inventories: dict[str, Inventory], token: str, scope: str | None, by_name: bool = False
) -> tuple[Inventory, Host]:
    """The one host token names, in the inventory scope names or in any. A token is a name or an
    alias, in any case. One that more than one host answers to, as a hand edit or a shared
    inventory can leave, is refused, naming each, so a command never acts on a host nobody meant.
    by_name is a host's own name exactly as its record has it, for a caller that holds the record."""
    found = []
    for ic in cfg.scope(scope):
        inventory = inventories[ic.name]
        if by_name:
            host = inventory.named(token)
            found += [(inventory, host)] if host else []
        else:
            found += [(inventory, h) for h in inventory.hosts if any(t.casefold() == token.casefold() for t in h.tokens())]
    if not found:
        raise HostsError(f"no host named {token!r} in " + (scope if scope else "any inventory"))
    if len(found) > 1:
        listed = ", ".join(
            f"{inventory.name}/{host.name} ({'name' if host.name.casefold() == token.casefold() else 'alias'})"
            for inventory, host in found
        )
        across = len({inventory.name for inventory, _ in found}) > 1
        raise HostsError(
            f"{token!r} names more than one host: {listed}; use a name or alias only one of them has"
            + (", or -i to say which inventory" if across else "")
        )
    return found[0]


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
            for alias in host.aliases:
                if alias.casefold() == host.name.casefold():
                    problems.append(f"{where}: alias {alias!r} is the host's own name")
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


def _key_problems(inventory: Inventory, names: list[str], where: str) -> list[str]:
    """Every key a host or the defaults list is declared, and listed once."""
    problems = []
    for name in sorted({n for n in names if names.count(n) > 1}, key=names.index):
        problems.append(f"{where}: key {name!r} is listed twice")
    for name in dict.fromkeys(names):
        if name not in inventory.keys:
            problems.append(f"{where}: key {name!r} isn't declared")
    return problems


def key_problems(inventory: Inventory) -> list[str]:
    problems = _key_problems(inventory, inventory.defaults.keys, f"{inventory.name}: defaults")
    for host in inventory.hosts:
        problems += _key_problems(inventory, host.keys, f"{inventory.name} ({host.name})")
    return problems


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


def _unprintable(text: str) -> str:
    shown = ", ".join(repr(c) for c in unprintable(text))
    return f"holds {shown}, which YAML can't carry, so Ansible would skip the whole file"


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


@storage.lock()
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
    # Descriptions and reasons go into the Ansible files as comments, which YAML reads too.
    written = [("description", c.description or "")] + [(f"reason {key!r}", text) for key, text in c.reasons]
    problems += [f"{where}: {what} {_unprintable(text)}" for what, text in written if unprintable(text)]

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


# Keys


@dataclass
class KeyRow:
    inventory: Inventory
    name: str
    key: KeyDef
    direct: int  # hosts that list the key
    total: int  # those plus the hosts that take it from the defaults
    state: list[str]  # what ssh would find at its path, keyfiles.state


def list_keys(cfg: Config, scope: str | None = None) -> list[KeyRow]:
    """Declared keys in declaration order, every inventory unless scope names one, each with the
    state of its files on this machine."""
    rows = []
    for ic in cfg.scope(scope):
        inventory = storage.load(ic)
        for name, key in inventory.keys.items():
            direct = sum(name in h.keys for h in inventory.hosts)
            total = sum(uses_key(inventory, h, name) for h in inventory.hosts)
            rows.append(KeyRow(inventory, name, key, direct, total, keyfiles.state(key.path, key.pub)))
    return rows


@dataclass
class KeyChanges:
    """What ari key changes: the file a key names, a new key generated there, pub read from the
    key file, or the key itself with remove."""

    path: str | None = None
    new: bool = False
    pub: bool = False
    remove: bool = False


def _declaration_problems(inventory: Inventory, name: str, key: KeyDef, where: str) -> list[str]:
    problems = []
    try:
        check_key_name(name, inventory.name)
    except HostsError as e:
        problems.append(str(e))
    try:
        KeyDef.from_dict(key.to_dict(), where)
    except HostsError as e:
        problems.append(str(e))
    other = next((n for n, k in inventory.keys.items() if n != name and k.path == key.path), None)
    if other:
        problems.append(f"{where}: {key.path} is already the file of key {other!r}")
    return problems


def _new_key_problems(path: str, where: str) -> list[str]:
    """Why --new can't write at path. It only writes into an empty slot of this user's: ssh-keygen
    asks only about the private file, and would overwrite a .pub left there, which may be the
    last trace of a key some server still trusts."""
    target, why = keyfiles.resolve(path)
    if target is None:
        return [f"{where}: --new can't write to {path} ({why})"]
    user = path[1:].partition("/")[0] if path.startswith("~") else ""
    if user and user != keyfiles.own_name():
        return [f"{where}: --new can't write to {path} (another user's home)"]
    problems = []
    if not os.path.isdir(os.path.dirname(target)):
        problems.append(f"{where}: {os.path.dirname(path)} isn't a directory")
    for suffix in ("", ".pub", "-cert.pub"):
        if os.path.lexists(target + suffix):
            problems.append(f"{where}: {path}{suffix} already exists")
    if shutil.which("ssh-keygen") is None:
        problems.append("ssh-keygen isn't installed")
    return problems


def _key_target(inventory: Inventory, name: str, c: KeyChanges) -> tuple[KeyDef | None, KeyDef | None]:
    """The key as it is and as it would be, after every check write_key runs before it writes or
    runs ssh-keygen. Raises with every problem; None as it would be when removing."""
    existing = inventory.keys.get(name)
    where = f"{inventory.name}: key {name!r}"
    if c.remove:
        if c != KeyChanges(remove=True):
            raise HostsError("--rm takes no other options")
        if existing is None:
            raise HostsError(f"no key {name!r} in {inventory.name}")
        # The defaults count as a user, so the hosts that inherit the key are covered by them.
        problems = []
        if name in inventory.defaults.keys:
            problems.append(f"{where} is in the defaults' keys")
        users = [h.name for h in inventory.hosts if name in h.keys]
        if users:
            problems.append(f"{where} is still listed by {_hosts(inventory, users)}")
        if problems:
            raise HostsError("nothing saved:\n  " + "\n  ".join(problems))
        return existing, None
    if c.new and c.pub:
        raise HostsError("--new fills pub itself; drop --pub")
    if c.path is None:
        if existing is None:
            raise HostsError(f"no key {name!r} in {inventory.name}; --path PATH declares it")
        if not (c.new or c.pub):
            raise HostsError("nothing to change; pass --path, --new, --pub or --rm")
    key = KeyDef(c.path if c.path is not None else existing.path, existing.pub if existing else None)
    problems = _declaration_problems(inventory, name, key, where)
    if c.new:
        problems += _new_key_problems(key.path, where)
        if problems:
            exist = any(p.endswith("already exists") for p in problems)
            hint = "\nari never removes or overwrites key files; move these away first" if exist else ""
            raise HostsError("no key generated, nothing saved:\n  " + "\n  ".join(problems) + hint)
    elif problems:
        raise HostsError("nothing saved:\n  " + "\n  ".join(problems))
    return existing, key


def check_key(cfg: Config, inventory_name: str, name: str, c: KeyChanges) -> None:
    """Raise what write_key would refuse before it writes anything or runs ssh-keygen, so the TUI
    can say why before it asks a question or hands the terminal over."""
    _key_target(storage.load(cfg.get(inventory_name)), name, c)


def write_key(cfg: Config, inventory_name: str, name: str, c: KeyChanges) -> tuple[Inventory, str]:
    """Declare a key, move it to another file, generate it, fill its pub, or remove it. Returns
    the inventory and declared, updated, unchanged or removed. Nothing is saved if any check
    fails, and every problem is listed. --new checks everything before ssh-keygen runs, so a
    refusal leaves no key behind, and saves nothing unless ssh-keygen leaves a pair."""
    with storage.lock():
        inventory = storage.load(cfg.get(inventory_name))
        existing, key = _key_target(inventory, name, c)
        if key is not None and c.pub:
            where = f"{inventory.name}: key {name!r}"
            line, why = keyfiles.public_line(key.path)
            if line is None:
                hint = f"; ssh-keygen -p -f {key.path} rewrites it in OpenSSH format" if why == "pair unchecked" else ""
                raise HostsError(f"{where}: no public half to read from {key.path} ({why}){hint}; nothing saved")
            key.pub = line
        if key is None or not c.new:
            return _save_key(inventory, name, existing, key)
    # ssh-keygen holds the terminal for as long as the passphrase prompt sits, so it runs without
    # the lock, and the key is saved against the inventory as it is once it's done.
    target = keyfiles.resolve(key.path)[0]
    try:
        code = keyfiles.generate(target, keyfiles.comment(name))
    except FileNotFoundError:
        raise HostsError("ssh-keygen isn't installed; nothing saved") from None
    if code != 0:
        raise HostsError(f"ssh-keygen exited {code}; nothing saved")
    key.pub = keyfiles.pair_line(key.path)
    if key.pub is None:
        raise HostsError(f"ssh-keygen left no key pair at {key.path}; nothing saved")
    with storage.lock():
        inventory = storage.load(cfg.get(inventory_name))
        existing = inventory.keys.get(name)
        problems = _declaration_problems(inventory, name, key, f"{inventory.name}: key {name!r}")
        if problems:
            raise HostsError(f"generated {key.path}, but nothing saved:\n  " + "\n  ".join(problems))
        return _save_key(inventory, name, existing, key)


def _save_key(inventory: Inventory, name: str, existing: KeyDef | None, key: KeyDef | None) -> tuple[Inventory, str]:
    if key is None:
        del inventory.keys[name]
        storage.save(inventory)
        return inventory, "removed"
    if existing == key:
        return inventory, "unchanged"
    inventory.keys[name] = key
    storage.save(inventory)
    return inventory, "declared" if existing is None else "updated"


def uses_key(inventory: Inventory, host: Host, name: str) -> bool:
    """Whether ssh offers the host the key: in its own list, or the defaults' when it has none."""
    return name in inventory.host_keys(host)


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
    defaults_set: dict[str, str] = field(default_factory=dict)  # the source's own defaults, taken by a new inventory
    groups_added: list[str] = field(default_factory=list)
    keys_added: list[str] = field(default_factory=list)  # keys declared for paths the inventory had none for
    settings: dict | None = None  # a config table the source suggests, for the module that read it


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
    if host.keys and host.keys == d.keys:
        host.keys = []
    for name, data in host.modules.items():
        _module(name).strip_defaults(d.modules.get(name, {}), data)
    host.modules = {name: data for name, data in host.modules.items() if data}


def _same_keys(stated: list[str], keys: list[str], first_only: bool) -> bool:
    """Whether the key files a source states agree with a record's. A source that names only the
    first key agrees with any list that starts with it."""
    if first_only and stated:
        return stated == keys[: len(stated)]
    return stated == keys


def _difference(inventory: Inventory, existing: Host, incoming: Host, first_only: bool = False) -> str | None:
    """What the source sets differently from the record. Merge only fills gaps, so anything
    returned here would otherwise be dropped while the report said nothing. A field the source
    leaves unset, an empty hostname, no port or no notes, says nothing either way."""
    if incoming.hostname and incoming.hostname != existing.hostname:
        return f"HostName {incoming.hostname} differs from {existing.hostname}"
    user = inventory.user(existing)
    if incoming.user and user and incoming.user != user:
        return f"User {incoming.user} differs from {user}"
    if incoming.port and incoming.port != inventory.port(existing):
        return f"Port {incoming.port} differs from {inventory.port(existing)}"
    stated, keys = inventory.key_paths(incoming.keys), inventory.identity_files(existing)
    if stated and keys and not _same_keys(stated, keys, first_only):
        return f"IdentityFile {', '.join(stated)} differs from {', '.join(keys)}"
    if incoming.notes and existing.notes and incoming.notes != existing.notes:
        return f"notes {incoming.notes!r} differ from {existing.notes!r}"
    for name, data in incoming.modules.items():
        found = _module(name).conflicts(existing.modules.get(name, {}), data, inventory.defaults.modules.get(name, {}))
        if found:
            return "; ".join(found)
    return None


def _pub_difference(inventory: Inventory, host: Host, listed: list[str]) -> str | None:
    """How the public keys a source lists for a host differ from the pub of the keys ssh offers it,
    in order: "keys" when any is another key, a key has no pub or the counts differ, "comments"
    when only the rest of a line does, None when they agree. Keys compare on type and key, as ssh
    compares them."""
    record = [inventory.keys[name].pub for name in inventory.host_keys(host) if name in inventory.keys]

    def key(line: str) -> list[str]:
        return line.split()[:2]

    if len(record) != len(listed) or any(pub is None or key(pub) != key(line) for pub, line in zip(record, listed)):
        return "keys"
    return "comments" if record != listed else None


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
    if incoming.keys and not inventory.host_keys(existing):
        existing.keys = list(incoming.keys)
        changed = True
    for name, data in incoming.modules.items():
        target = existing.modules.setdefault(name, {})
        changed |= _module(name).merge(target, data)
    existing.modules = {name: data for name, data in existing.modules.items() if data}
    return changed


def _settings(host: Host) -> dict:
    """What a host's record says about connecting to it, whatever name is typed."""
    data = host.to_dict()
    for name_only in ("aliases", "last_updated"):
        data.pop(name_only, None)
    return data


def _adopt_keys(target: Inventory, keys: dict[str, KeyDef], hosts: list[Host], defaults: list[str]) -> list[str]:
    """Put the source's keys in the inventory's terms. A path the inventory declares keeps the
    inventory's name; a new one is declared under the source's name, numbered when that's taken.
    Hosts and the source's defaults are renamed in place. Returns the keys declared."""
    rename: dict[str, str] = {}
    added = []
    for name, key in keys.items():
        mine = next((n for n, k in target.keys.items() if k.path == key.path), None)
        if mine is None:
            mine = numbered(name, target.keys) if KEY_NAME.fullmatch(name) else key_name(key.path, target.keys)
            target.keys[mine] = copy.deepcopy(key)
            added.append(mine)
        rename[name] = mine
    for host in hosts:
        host.keys = [rename.get(n, n) for n in host.keys]
    defaults[:] = [rename.get(n, n) for n in defaults]
    return added


@storage.lock()
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
    writers = _writers(cfg, target)
    label = tilde(result.files[0][0]) if result.files else (source or module_name)
    report = ImportReport(target.name, label, warnings=list(result.warnings))
    added_keys = _adopt_keys(target, result.keys, result.hosts, result.defaults.keys if result.defaults else [])
    # Each host's public keys and Host line, by host rather than by position, since refused hosts drop out below.
    listed = {id(h): pubs for h, pubs in zip(result.hosts, result.pubs or []) if pubs is not None}
    covers = None if result.covers is None else {id(h): tokens for h, tokens in zip(result.hosts, result.covers)}
    reworded: list[str] = []  # the same keys under other comments
    unlisted: list[str] = []  # new hosts whose keys don't hold the public keys the source lists

    # A host that would make the inventory unreadable never gets near it, nor near the defaults.
    readable = []
    for host in result.hosts:
        probe = copy.deepcopy(host)
        module.complete(probe)
        problems = _shape_problems(target, probe, [])  # write rules wait for exclude, below
        if problems:
            report.refused += [p.message for p in problems]
        else:
            readable.append(host)
    result.hosts = readable

    fresh = not target.hosts and target.defaults.is_empty()
    differ = False
    leaning: set[str] = set()  # hosts that took a source default the inventory's differs from
    if result.defaults is not None:
        # The source states its own defaults; hosts that leave a field unset inherit them there too.
        d = result.defaults
        if fresh:
            target.defaults.user, target.defaults.port, target.defaults.keys = d.user, d.port, list(d.keys)
            keys = " ".join(d.keys) or None
            report.defaults_set = {k: str(v) for k, v in (("user", d.user), ("port", d.port), ("keys", keys)) if v is not None}
        else:
            # The inventory keeps its own, so each host gets what the source gave it: a host that
            # leaves a field unset takes the source's default. Stored where it differs from the
            # inventory's; on a host already here, a value that would change is a conflict.
            mine = target.defaults
            ours = {"user": mine.user, "port": mine.port or 22, "keys": target.key_paths(mine.keys)}
            theirs = {"user": d.user, "port": d.port, "keys": target.key_paths(d.keys)}
            differs = {attr: theirs[attr] != ours[attr] for attr in ("user", "port")}
            differs["keys"] = not _same_keys(theirs["keys"], ours["keys"], result.first_key_only)
            differ = (d.user, d.port) != (mine.user, mine.port) or differs["keys"]
            for host in result.hosts:
                for attr, value, unset in (
                    ("user", d.user, host.user is None),
                    ("port", d.port, host.port is None),
                    ("keys", list(d.keys) or None, not host.keys),
                ):
                    if value is not None and unset:
                        setattr(host, attr, value)
                        if differs[attr]:
                            leaning.add(host.name)
    # Defaults are only ever declared: by the inventory, or by a source of its own for a new one.
    # Never inferred from what hosts happen to share.
    declared_here = fresh and result.defaults is not None
    report.settings = result.settings

    groups_changed = False
    previous = copy.deepcopy(target.groups)
    before = _structure_problems(target)
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
    # The check ari group runs: only problems the source would add count. Any of them and the
    # groups stay as they were; hosts in a group that never got declared are refused below.
    added = [p for p in _structure_problems(target) if p not in before]
    if added:
        target.groups = previous
        report.groups_added = []
        groups_changed = False
        report.refused += [f"{p}; the inventory's groups are kept as they were" for p in added]

    elsewhere = {
        token.casefold(): f"{inv.name}/{host.name}"
        for inv in inventories.values()
        if inv is not target
        for host in inv.hosts
        for token in host.tokens()
    }

    inherited: list[str] = []  # new hosts that take a default the source never stated for them
    for host in result.hosts:
        clash = next((elsewhere[t.casefold()] for t in host.tokens() if t.casefold() in elsewhere), None)
        if clash:
            report.conflicts.append(f"{host.name}: already defined as {clash}")
            continue
        existing = target.find(host.name)
        if existing is not None and host.name not in existing.tokens():
            # ssh matches Host tokens as typed and Ansible keeps host names as written, so this is
            # another host to them; ari's names are unique ignoring case, so it can't hold both.
            held = next(t for t in existing.tokens() if t.casefold() == host.name.casefold())
            what = "the name" if held == existing.name else "an alias"
            report.conflicts.append(
                f"{host.name}: ssh and Ansible tell it apart from {held}, {what} ari holds, and ari can't keep both spellings;"
                " not imported"
            )
            continue
        if existing is None:
            inherits_user = host.user is None and target.defaults.user and not declared_here
            inherits_key = not host.keys and target.defaults.keys and not declared_here
            module.complete(host)
            default_keys = target.key_paths(target.defaults.keys)
            if result.first_key_only and host.keys and _same_keys(target.key_paths(host.keys), default_keys, True):
                host.keys = []  # the source names the defaults' first key, so the host takes their whole list
            _strip_defaults(target, host)
            host.exclude = list(exclude)
            # Checked against everything already in the inventory, hosts added earlier in this
            # import included, so two blocks sharing an alias can't both get in.
            problems = _host_problems(inventories, target, host, None, writers)
            if problems:
                report.refused += [p.message for p in problems]
                continue
            if inherits_user:
                report.warnings.append(f"{host.name}: no User in the source; the default user {target.defaults.user} will apply")
            if inherits_key:
                report.warnings.append(f"{host.name}: no IdentityFile in the source; the default key will apply")
            if inherits_user or inherits_key:
                inherited.append(host.name)
            host.last_updated = now()
            target.hosts.append(host)
            report.added.append(host.name)
            found = _pub_difference(target, host, listed[id(host)]) if id(host) in listed else None
            if found:
                (reworded if found == "comments" else unlisted).append(host.name)
            continue
        difference = _difference(target, existing, host, result.first_key_only)
        if difference:
            report.conflicts.append(f"{host.name}: {difference}; not merged")
            continue
        candidate = copy.deepcopy(existing)
        _merge(target, candidate, host)
        _strip_defaults(target, candidate)
        # The public keys the source lists are checked against the keys the host ends up with, and never stored.
        found = _pub_difference(target, candidate, listed[id(host)]) if id(host) in listed else None
        if found == "keys":
            report.conflicts.append(f"{host.name}: the public keys the source lists differ from the pub of its keys; not merged")
            continue
        if candidate.to_dict() == existing.to_dict():
            report.unchanged.append(host.name)
            if found:
                reworded.append(host.name)
            continue
        # ssh gives a block's settings only to the tokens it names, as typed: one that leaves out a
        # name the host answers to, or writes it in another case, can't change what the whole
        # record says. New aliases change nothing for the names it leaves out, so they still merge.
        left_out = [t for t in existing.tokens() if covers is not None and t not in covers[id(host)]]
        if left_out and _settings(candidate) != _settings(existing):
            report.conflicts.append(
                f"{host.name}: its Host line leaves out {', '.join(left_out)}, so ssh applies it to the rest alone; not merged"
            )
            continue
        problems = _host_problems(inventories, target, candidate, existing, writers)
        if problems:
            report.refused += [p.message for p in problems]
            continue
        candidate.last_updated = now()
        target.hosts = [candidate if h is existing else h for h in target.hosts]
        report.merged.append(host.name)
        if found:
            reworded.append(host.name)

    if differ:
        pinned = [name for name in report.added + report.merged if name in leaning]
        report.warnings.append(
            "the source's defaults differ from the inventory's; kept the inventory's"
            + (f", and pinned the source's on {', '.join(pinned)}" if pinned else "")
        )
    if reworded:
        report.warnings.append(
            f"the public keys the source lists for {', '.join(reworded)} differ from the pub of their keys only in"
            " their comments; kept the inventory's"
        )
    if unlisted:
        report.warnings.append(
            f"the public keys the source lists for {', '.join(unlisted)} aren't the pub of their keys, and import"
            " never sets a pub; not imported"
        )

    # A key declared for this import stays only when something saved uses it.
    used = set(target.defaults.keys) | {k for h in target.hosts for k in h.keys}
    for name in added_keys:
        if name in used:
            report.keys_added.append(name)
        else:
            del target.keys[name]
    if report.added or report.merged or report.defaults_set or report.groups_added or groups_changed:
        storage.save(target)
    # The guard records a source only when the inventory now holds all of it, so an export over
    # that file loses nothing. Anything conflicted, refused or left out, or a host that now takes a
    # default it never had, means a person looks first.
    if result.files:
        if report.conflicts or report.refused or result.lossy or reworded or unlisted or inherited:
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
    """What add and edit change. None leaves a field alone. For user, port, os and notes an empty
    string clears the field, so the inventory default, if any, applies again. keys names declared keys, or
    their files, appended in order after unkey removes its own; an empty one drops the host's own
    list, so the defaults' applies again. Every value given for one ssh option is its new value,
    several making a list, and an empty one removes it. Values arrive as strings, the way the CLI
    and the TUI's inputs hand them over."""

    hostname: str | None = None
    rename: str | None = None
    user: str | None = None
    port: str | None = None
    keys: list[str] = field(default_factory=list)
    unkey: list[str] = field(default_factory=list)
    os: str | None = None
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


def resolve_key(inventory: Inventory, given: str) -> str | None:
    """A declared key by name, or by its file: the first declared there."""
    if given in inventory.keys:
        return given
    return next((name for name, key in inventory.keys.items() if key.path == given), None)


def _apply(inventory: Inventory, host: Host, c: Changes) -> list[Problem]:
    """Apply changes in place. Returns what couldn't be applied; the record checks come after."""
    problems = []
    if c.rename is not None:
        host.name = c.rename
    if c.hostname is not None:
        host.hostname = c.hostname
    if c.user is not None:
        host.user = c.user or None
    # Removals first, so --unkey old --key new swaps one for the other.
    for given in c.unkey:
        name = resolve_key(inventory, given)
        if name in host.keys:
            host.keys.remove(name)
        elif name in inventory.defaults.keys and not host.keys:
            problems.append(Problem("keys", f"key {given!r} comes from the defaults; --key names the keys to use instead"))
        else:
            problems.append(Problem("keys", f"doesn't list key {given!r}"))
    for given in c.keys:
        name = resolve_key(inventory, given)
        if not given:
            host.keys = []
        elif name is None:
            problems.append(Problem("keys", f"key {given!r} isn't declared; ari key NAME --path PATH -i {inventory.name} declares one"))
        else:
            host.keys.append(name)
    if c.os is not None:
        host.os = c.os
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
        given: dict[str, tuple[str, list[str]]] = {}  # every value per keyword, ssh reading keywords without case
        for key, value in c.options:
            given.setdefault(key.casefold(), (key, []))[1].append(value)
        for key, values in given.values():
            current = next((k for k in own if k.casefold() == key.casefold()), None)
            if all(values):
                # A different spelling of a keyword it has keeps its place. A list on a keyword ssh
                # reads once is refused by the ssh module's own check, after this.
                own[current or key] = values[0] if len(values) == 1 else values
            elif any(values):
                problems.append(Problem("options", f"ssh option {key} is both set and cleared"))
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


def _expands(user: str | None) -> bool:
    """OpenSSH 10 expands % tokens and ${VAR} in User, from a config or from Ansible's -o, and 9.x
    doesn't, so no spelling of a user holding either reads the same on both."""
    return bool(user) and ("%" in user or "${" in user)


def _user_problem(user: str) -> str:
    return f"user {user!r} can't be written: OpenSSH 10 expands % and ${{}} in User, and earlier versions don't"


def user_problems(inventory: Inventory) -> list[str]:
    """A user ssh would expand, for export: the defaults' and each host's own."""
    records = [(f"{inventory.name} defaults", inventory.defaults.user)]
    records += [(f"{inventory.name} ({h.name})", h.user) for h in inventory.hosts]
    return [f"{where}: {_user_problem(user)}" for where, user in records if user and _expands(user)]


def _writers(cfg: Config, inventory: Inventory) -> list[Module]:
    """The modules the inventory turns on, whose write rules its hosts must meet."""
    return [mc.module for mc in cfg.get(inventory.name).enabled()]


def _shape_problems(inventory: Inventory, host: Host, writers: list[Module]) -> list[Problem]:
    """The record on its own: the rules load enforces, each installed module's data rules, the
    write rules of each module in writers that the host doesn't exclude, and a user ssh would
    expand. A host that fails the first two would make the inventory unreadable once saved. Name,
    hostname, port and aliases are each checked on their own, so every bad field is reported at
    once."""
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
    check("os", check_line, host.os or None, "os", where)
    if not problems:
        # Everything else load enforces, from the record as it would be saved.
        check(None, Host.from_dict, host.to_dict(), inventory.name)

    installed = registry().modules
    unreadable = set()
    for name, data in host.modules.items():
        if name in installed:
            try:
                installed[name].host_data(copy.deepcopy(data), f"{where}: modules.{name}")
            except HostsError as e:
                # ssh options are the one module field add and edit set directly.
                problems.append(Problem("options" if name == "ssh" else None, str(e)))
                unreadable.add(name)
    for module in writers:
        # What a module couldn't write as the record says, though load would take it.
        if module.name not in unreadable and module.name not in host.exclude:
            problems += [Problem(field, f"{where}: {p}") for field, p in module.host_problems(inventory, host)]
    if host.user and ("\n" in host.user or "\r" in host.user):
        problems.append(Problem("user", f"{where}: user must be one line"))
    elif _expands(host.user):
        problems.append(Problem("user", f"{where}: {_user_problem(host.user)}"))
    return problems



def _host_problems(
    inventories: dict[str, Inventory], inventory: Inventory, host: Host, original: Host | None, writers: list[Module]
) -> list[Problem]:
    """Everything a host must meet before it's saved, by add, edit or import: its shape, names
    unique across all inventories, declared groups and valid reasons."""
    where = f"{inventory.name} ({host.name})"
    problems = _shape_problems(inventory, host, writers)

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
    problems += [Problem("keys", p) for p in _key_problems(inventory, host.keys, where)]
    return problems + [Problem("groups", p) for p in _membership_problems(inventory, host)]


def _write(
    inventories: dict[str, Inventory], inventory: Inventory, original: Host | None, host: Host, c: Changes, writers: list[Module]
) -> bool:
    """Apply, check, save. A host that fails any check is refused and nothing is saved."""
    problems = [Problem(p.field, f"{inventory.name} ({host.name}): {p.message}") for p in _apply(inventory, host, c)]
    _strip_defaults(inventory, host)
    problems += _host_problems(inventories, inventory, host, original, writers)
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


@storage.lock()
def add_host(cfg: Config, inventory_name: str, name: str, hostname: str, changes: Changes) -> tuple[Inventory, Host]:
    for flag, used in (("--rename", changes.rename), ("--hostname", changes.hostname)):
        if used is not None:
            raise HostsError(f"{flag} is for edit; add takes the name and hostname as arguments")
    if changes.ungroup or changes.unalias or changes.unkey or changes.include:
        raise HostsError("--ungroup, --unalias, --unkey and --include are for edit")
    inventories = load_all(cfg)
    inventory = inventories[cfg.get(inventory_name).name]
    host = Host(name=name, hostname=hostname)
    _write(inventories, inventory, None, host, changes, _writers(cfg, inventory))
    return inventory, host


@storage.lock()
def edit_host(
    cfg: Config, token: str, changes: Changes, scope: str | None = None, by_name: bool = False
) -> tuple[Inventory, Host, bool]:
    """The host after editing, and whether anything changed. by_name as for _locate."""
    if changes.is_empty():
        raise HostsError("nothing to change; pass at least one option")
    inventories = load_all(cfg)
    inventory, original = _locate(cfg, inventories, token, scope, by_name)
    host = copy.deepcopy(original)
    changed = _write(inventories, inventory, original, host, changes, _writers(cfg, inventory))
    return inventory, host, changed


@storage.lock()
def remove_host(cfg: Config, token: str, scope: str | None = None, by_name: bool = False) -> tuple[Inventory, Host]:
    """Removing a host removes its group memberships with it: they live on the record. by_name as
    for _locate."""
    inventories = load_all(cfg)
    inventory, host = _locate(cfg, inventories, token, scope, by_name)
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


@storage.lock()
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
        problems += group_problems(inventory) + key_problems(inventory) + user_problems(inventory)
        for mc in ic.enabled():
            module = mc.module
            if not module.exports or (only and module.name not in only):
                continue
            hosts = [h for h in inventory.hosts if module.name not in h.exclude]
            problems += [f"{ic.name}/{module.name}: {p}" for p in module.validate(inventory, hosts, mc.settings)]
            problems += [f"{ic.name} ({h.name}): {p}" for h in hosts for _, p in module.host_problems(inventory, h)]
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

    # Every temp first, the guard's state among them, then every rename, the guard's last. A
    # failure while writing leaves every target and the guard as they were. Past that point, the
    # guard records exactly what landed.
    staged = storage.Staged()
    writing = None
    try:
        for p, s in checked:
            if s is not Status.SAME:
                writing = p.path
                staged.add(p.path, p.data, p.mode)
        writing = guard.path
        guard.stage(staged, [(p.path, p.data) for p in planned])
    except BaseException as e:
        staged.cleanup()
        if isinstance(e, OSError) and writing is not None:
            temp = not e.filename or str(e.filename).endswith(storage.TEMP_SUFFIX)
            raise _stopped([f"{tilde(writing if temp else Path(e.filename))}: {e.strerror or e}"]) from None
        raise
    try:
        for p, s in checked:
            fixed = False
            if s is Status.SAME:
                fixed = storage.set_mode(p.path, p.mode)  # no write to carry the mode, so set it here
            else:
                staged.commit(p.path)
            guard.record(p.path, p.data)
            report.written.append(Written(p, s, fixed))
        staged.commit(guard.path)
    finally:
        if guard.path in staged.temps:
            # Stopped between two renames: the staged state would record files that never landed.
            staged.cleanup()
            guard.save()
    return report
