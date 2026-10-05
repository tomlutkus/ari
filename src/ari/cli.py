"""ari: keep your SSH hosts in one place, export ssh config and Ansible inventory."""

import argparse
import getpass
import json
import re
import sys

from rich import box
from rich.cells import cell_len
from rich.console import Console
from rich.measure import Measurement
from rich.table import Table
from rich.text import Text

from . import __version__, core, keyfiles
from .config import Config, load_config
from .errors import HostsError
from .paths import tilde

out = Console(highlight=False)
_UNBOUNDED = 10_000  # wider than any table, for measuring one with nothing cut
err = Console(stderr=True, highlight=False)


def _inventory_arg(args: argparse.Namespace) -> str | None:
    return getattr(args, "inventory", None)


def _note(label: str, style: str, message: str) -> None:
    err.print(Text.assemble((f"{label}: ", style), message), soft_wrap=True)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def _user(inventory, host) -> Text:
    """The User ssh will use. With none set anywhere, that's whoever connects."""
    user = inventory.user(host)
    return Text(user) if user else Text(getpass.getuser(), style="dim")


def _fit_names(names: list[str], width: int) -> Text:
    """As many names as fit in width, in the order given, then +N for the rest."""
    if not names:
        return Text("-")
    for shown in range(len(names), 0, -1):
        hidden = len(names) - shown
        text = ", ".join(names[:shown]) + (f" +{hidden}" if hidden else "")
        if cell_len(text) <= width:
            return Text(text)
    # Not even the first name fits: cut it short and keep the count.
    rest = f" +{len(names) - 1}" if len(names) > 1 else ""
    first = Text(names[0])
    first.truncate(max(width - len(rest), 1), overflow="ellipsis")
    return first + rest


def _natural_width(table: Table) -> int:
    """How wide table is with nothing cut, however narrow the console."""
    return Measurement.get(out, out.options.update_width(_UNBOUNDED), table).maximum


def _print_whole(table: Table) -> None:
    """Print table with every column whole. When that's wider than the console, the rows run past
    the edge at full length instead: a name or an address cut short would read as a real one."""
    natural = _natural_width(table)
    if natural > out.width:
        table.width = natural
        out.print(table, crop=False)
    else:
        out.print(table)


def _mark(cfg: Config, inventory, host, module: str) -> Text:
    reach = core.module_reach(cfg, inventory, host, module)
    return Text(reach.value, style="dim" if reach is core.ModuleReach.OFF else "")


def _ls_table(cfg: Config, modules: list[str], rows: list, groups: list[Text]) -> Table:
    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    for column in ("NAME", "HOSTNAME", "USER", "PORT", *(m.upper() for m in modules), "GROUPS", "INV"):
        table.add_column(column, justify="right" if column == "PORT" else "left", no_wrap=column == "GROUPS")
    for (inventory, host), cell in zip(rows, groups):
        table.add_row(
            Text(host.name),
            Text(host.hostname),
            _user(inventory, host),
            Text(str(inventory.port(host))),
            *(_mark(cfg, inventory, host, m) for m in modules),
            cell,
            Text(inventory.name, style="dim"),
        )
    return table


def cmd_ls(cfg: Config, args: argparse.Namespace) -> int:
    rows = core.list_hosts(cfg, _inventory_arg(args), args.search, args.group)
    if not rows:
        out.print("no hosts")
        return 0
    modules = core.export_modules(cfg, _inventory_arg(args))
    # GROUPS gets whatever width the other columns leave, so a row never wraps on its account.
    bare = _natural_width(_ls_table(cfg, modules, rows, [Text("")] * len(rows)))
    room = max(out.width - (bare - len("GROUPS")), len("GROUPS"))
    _print_whole(_ls_table(cfg, modules, rows, [_fit_names(host.groups, room) for _, host in rows]))
    out.print(Text(_plural(len(rows), "host"), style="dim"))
    return 0


def cmd_show(cfg: Config, args: argparse.Namespace) -> int:
    inventory, host = core.find_host(cfg, args.name, _inventory_arg(args))
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim")
    grid.add_column()
    for d in core.host_details(inventory, host):
        grid.add_row(d.label, Text.assemble(d.value, (f"  {d.note}", "dim")) if d.note else Text(d.value))
    out.print(grid)
    return 0


def _changes(args: argparse.Namespace) -> core.Changes:
    return core.Changes(
        hostname=getattr(args, "new_hostname", None),
        rename=getattr(args, "new_name", None),
        user=args.user,
        port=args.port,
        keys=args.key,
        unkey=getattr(args, "unkey", []),
        notes=args.notes,
        aliases=args.alias,
        unalias=getattr(args, "unalias", []),
        options=args.opt,
        groups=args.group,
        ungroup=getattr(args, "ungroup", []),
        exclude=args.exclude,
        include=getattr(args, "include", []),
    )


def cmd_add(cfg: Config, args: argparse.Namespace) -> int:
    inventory = cfg.select(_inventory_arg(args)).name
    inventory, host = core.add_host(cfg, inventory, args.name, args.hostname, _changes(args))
    out.print(f"added {host.name} to {inventory.name}", soft_wrap=True)
    return 0


def cmd_edit(cfg: Config, args: argparse.Namespace) -> int:
    inventory, host, changed = core.edit_host(cfg, args.name, _changes(args), _inventory_arg(args))
    out.print(f"{'updated' if changed else 'no change to'} {host.name} ({inventory.name})", soft_wrap=True)
    return 0


def cmd_rm(cfg: Config, args: argparse.Namespace) -> int:
    inventory, host = core.remove_host(cfg, args.name, _inventory_arg(args))
    out.print(f"removed {host.name} from {inventory.name}", soft_wrap=True)
    return 0


GROUP_COLUMNS = ("GROUP", "HOSTS", "CHILDREN", "REASONS", "ABOUT", "INV")


def _group_table(rows: list[core.GroupRow], cells: list[tuple[Text, Text, Text]]) -> Table:
    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    for column in GROUP_COLUMNS:
        table.add_column(column, no_wrap=True)
    for row, (children, reasons, description) in zip(rows, cells):
        hosts = Text(str(row.total))
        if row.direct != row.total:
            hosts.append(f" ({row.direct} direct)", style="dim")
        table.add_row(Text(row.name), hosts, children, reasons, description, Text(row.inventory.name, style="dim"))
    return table


def _print_groups(rows: list[core.GroupRow]) -> None:
    """GROUP, HOSTS and INV stay whole. CHILDREN, REASONS and ABOUT (the description's first line)
    share what they leave, the widest giving way first, so a row never wraps."""
    lists = [(row.group.children, list(row.group.reasons), row.group.description.partition("\n")[0]) for row in rows]
    bare = _natural_width(_group_table(rows, [(Text(""),) * 3] * len(rows)))
    floors = [len(h) for h in GROUP_COLUMNS[2:5]]
    room = out.width - bare + sum(floors)
    give = [
        max(floor, *(cell_len(", ".join(c[i]) if i < 2 else c[i]) for c in lists))
        for i, floor in enumerate(floors)
    ]
    while sum(give) > room and any(g > f for g, f in zip(give, floors)):
        widest = max((i for i in range(3) if give[i] > floors[i]), key=lambda i: give[i])
        give[widest] -= 1
    cells = []
    for children, reasons, description in lists:
        text = Text(description or "-", style="dim")
        text.truncate(give[2], overflow="ellipsis")
        cells.append((_fit_names(children, give[0]), _fit_names(reasons, give[1]), text))
    _print_whole(_group_table(rows, cells))


def cmd_group(cfg: Config, args: argparse.Namespace) -> int:
    changes = core.GroupChanges(
        description=args.description, children=args.child, unchild=args.unchild, reasons=args.reason, remove=args.rm
    )
    if args.name is None:
        if changes != core.GroupChanges():
            raise HostsError("--description, --child, --unchild, --reason and --rm need a group NAME")
        rows = core.list_groups(cfg, _inventory_arg(args))
        if not rows:
            out.print("no groups")
            return 0
        _print_groups(rows)
        out.print(Text(_plural(len(rows), "group"), style="dim"))
        return 0
    inventory, status = core.write_group(cfg, cfg.select(_inventory_arg(args)).name, args.name, changes)
    done = {
        "declared": f"declared {args.name} in {inventory.name}",
        "updated": f"updated {args.name} ({inventory.name})",
        "unchanged": f"no change to {args.name} ({inventory.name})",
        "removed": f"removed {args.name} from {inventory.name}",
    }
    out.print(done[status], soft_wrap=True)
    return 0


def _key_table(rows: list[core.KeyRow]) -> Table:
    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    for column in ("KEY", "HOSTS", "PATH", "STATE", "INV"):
        table.add_column(column, no_wrap=True)
    for row in rows:
        hosts = Text(str(row.total))
        if row.direct != row.total:
            hosts.append(f" ({row.direct} direct)", style="dim")
        state = Text(", ".join(row.state), style="dim" if row.state == [keyfiles.OK] else "")
        table.add_row(Text(row.name), hosts, Text(row.key.path), state, Text(row.inventory.name, style="dim"))
    return table


def cmd_key(cfg: Config, args: argparse.Namespace) -> int:
    changes = core.KeyChanges(path=args.path, remove=args.rm)
    if args.name is None:
        if changes != core.KeyChanges():
            raise HostsError("--path and --rm need a key NAME")
        rows = core.list_keys(cfg, _inventory_arg(args))
        if not rows:
            out.print("no keys")
            return 0
        # Every column is whole: a name or a path cut short would read as a real one.
        _print_whole(_key_table(rows))
        out.print(Text(_plural(len(rows), "key"), style="dim"))
        return 0
    inventory, status = core.write_key(cfg, cfg.select(_inventory_arg(args)).name, args.name, changes)
    done = {
        "declared": f"declared {args.name} in {inventory.name}",
        "updated": f"updated {args.name} ({inventory.name})",
        "unchanged": f"no change to {args.name} ({inventory.name})",
        "removed": f"removed {args.name} from {inventory.name}",
    }
    out.print(done[status], soft_wrap=True)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    out.print(f"wrote {tilde(core.init_config())}", soft_wrap=True)
    return 0


def cmd_modules(cfg: Config, args: argparse.Namespace) -> int:
    rows, failures = core.module_status(cfg)
    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    for column in ("MODULE", "IMPORT", "EXPORT", "INVENTORIES", "ABOUT"):
        table.add_column(column)
    for row in rows:
        where = Text()
        for i, (inventory, enabled) in enumerate(row.inventories.items()):
            if i:
                where.append(", ")
            where.append(inventory if enabled else f"{inventory} (off)", style=None if enabled else "dim")
        table.add_row(
            Text(row.module.name),
            Text("yes" if row.module.imports else "-"),
            Text("yes" if row.module.exports else "-"),
            where if row.inventories else Text("-", style="dim"),
            Text(row.module.summary, style="dim"),
        )
    out.print(table)
    for name, reason in failures.items():
        _note("failed", "red", f"{name}: {reason}")
    if not cfg.inventories:
        _note("note", "dim", f"no config at {tilde(cfg.path)}; ari init writes a starter")
    return 0


def cmd_import(cfg: Config, args: argparse.Namespace) -> int:
    inventory = cfg.select(_inventory_arg(args)).name
    report = core.import_hosts(cfg, inventory, args.module, args.source, exclude=args.exclude)
    for warning in report.warnings:
        _note("warning", "yellow", warning)
    for conflict in report.conflicts:
        _note("conflict", "red", conflict)
    for refused in report.refused:
        _note("refused", "red", refused)
    if report.defaults_set:
        values = ", ".join(f"{k} {v}" for k, v in report.defaults_set.items())
        out.print(f"{report.inventory}: defaults set from {report.defaults_from}: {values}", soft_wrap=True)
    for label, names in (("added", report.added), ("merged", report.merged), ("unchanged", report.unchanged)):
        if names:
            listed = f" ({', '.join(names)})" if len(names) <= 12 else ""
            out.print(f"{report.inventory}: {len(names)} {label}{listed}", soft_wrap=True)
    if report.groups_added:
        out.print(f"{report.inventory}: {_plural(len(report.groups_added), 'group')} declared", soft_wrap=True)
    if report.keys_added:
        declared = f"{_plural(len(report.keys_added), 'key')} declared ({', '.join(report.keys_added)})"
        out.print(f"{report.inventory}: {declared}", soft_wrap=True)
    if not (report.added or report.merged or report.unchanged):
        out.print(f"{report.inventory}: nothing imported from {report.source}")
    for path in report.unadopted:
        _note("note", "yellow", f"{path} not adopted, so an export over it needs --force once you've checked it")
    if report.settings and args.module not in cfg.get(report.inventory).modules:
        out.print(f"\nadd to {tilde(cfg.path)}:\n")
        out.print(_toml_table(f"inventories.{report.inventory}.{args.module}", report.settings), soft_wrap=True, markup=False)
    return 1 if report.conflicts or report.refused else 0


def _toml_key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key)


def _toml_value(value) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False)


def _toml_table(name: str, table: dict) -> str:
    """A table to paste into config.toml: plain keys first, then each subtable."""
    lines = [f"[{name}]"]
    lines += [f"{_toml_key(k)} = {_toml_value(v)}" for k, v in table.items() if not isinstance(v, dict)]
    for key, sub in table.items():
        if isinstance(sub, dict):
            lines += ["", f"[{name}.{_toml_key(key)}]"]
            lines += [f"{_toml_key(k)} = {_toml_value(v)}" for k, v in sub.items()]
    return "\n".join(lines)


def cmd_export(cfg: Config, args: argparse.Namespace) -> int:
    report = core.export(cfg, _inventory_arg(args), only=args.modules or None, force=args.force)
    for w in report.written:
        out.print(w.summary(), soft_wrap=True)
    for note in report.notes:
        _note("note", "dim", note)
    return 0


def _key_value(text: str) -> tuple[str, str]:
    key, sep, value = text.partition("=")
    if not sep or not key:
        raise argparse.ArgumentTypeError(f"expected KEY=VALUE, got {text!r}")
    return key, value


def _group_reason(text: str) -> tuple[str, str | None]:
    group, sep, reason = text.partition(":")
    if not group or (sep and not reason):
        raise argparse.ArgumentTypeError(f"expected GROUP or GROUP:REASON, got {text!r}")
    return group, reason or None


def _host_options(p: argparse.ArgumentParser, edit: bool) -> None:
    clear = "; '' goes back to the default" if edit else ""
    p.add_argument("--user", metavar="USER", help="login name" + clear)
    p.add_argument("--port", metavar="PORT", help="ssh port" + clear)
    p.add_argument(
        "--key",
        metavar="NAME",
        action="append",
        default=[],
        help="a declared key, or its file; repeatable, in the order ssh offers them"
        + ("; '' goes back to the defaults' keys" if edit else ""),
    )
    p.add_argument("--notes", metavar="TEXT", help="free text, a comment above the Host block")
    p.add_argument("--alias", metavar="ALIAS", action="append", default=[], help="another name; repeatable")
    p.add_argument(
        "--opt",
        metavar="KEY=VALUE",
        action="append",
        default=[],
        type=_key_value,
        help="ssh option; repeatable, every value for one KEY together" + ("; KEY= removes it" if edit else ""),
    )
    p.add_argument(
        "--group",
        metavar="GROUP[:REASON]",
        action="append",
        default=[],
        type=_group_reason,
        help="join a declared group, with an optional reason; repeatable",
    )
    p.add_argument("--exclude", metavar="MODULE", action="append", default=[], help="keep out of MODULE's output; repeatable")
    if edit:
        p.add_argument("--hostname", metavar="HOSTNAME", dest="new_hostname", help="new address")
        p.add_argument("--rename", metavar="NAME", dest="new_name", help="new name")
        p.add_argument("--unalias", metavar="ALIAS", action="append", default=[], help="drop an alias; repeatable")
        p.add_argument("--unkey", metavar="NAME", action="append", default=[], help="drop one of its keys; repeatable")
        p.add_argument("--ungroup", metavar="GROUP", action="append", default=[], help="leave a group; repeatable")
        p.add_argument("--include", metavar="MODULE", action="append", default=[], help="undo an --exclude; repeatable")


def build_parser() -> argparse.ArgumentParser:
    # -i works before or after the subcommand; SUPPRESS keeps a later parser from erasing an earlier value.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-i", "--inventory", metavar="INV", default=argparse.SUPPRESS, help="inventory to act on")

    parser = argparse.ArgumentParser(
        prog="ari",
        parents=[common],
        description="Keep SSH hosts in one place; export ssh config and Ansible inventory from it.",
        epilog="With no command on a terminal, ari opens the TUI. See ari(1).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    init = sub.add_parser("init", help="write a starter config.toml, only if there's none")
    init.set_defaults(func=cmd_init)

    ls = sub.add_parser("ls", parents=[common], help="list hosts across inventories")
    ls.add_argument(
        "--search", metavar="TEXT", help="match any field, effective user, port and key included; case-insensitive"
    )
    ls.add_argument("--group", metavar="GROUP", help="only hosts in this group")
    ls.set_defaults(func=cmd_ls)

    show = sub.add_parser("show", parents=[common], help="show one host with its effective values")
    show.add_argument("name", metavar="NAME", help="host name or alias")
    show.set_defaults(func=cmd_show)

    add = sub.add_parser("add", parents=[common], help="add a host to one inventory")
    add.add_argument("name", metavar="NAME", help="ssh alias")
    add.add_argument("hostname", metavar="HOSTNAME", help="address ssh connects to")
    _host_options(add, edit=False)
    add.set_defaults(func=cmd_add)

    edit = sub.add_parser("edit", parents=[common], help="change a host")
    edit.add_argument("name", metavar="NAME", help="host name or alias")
    _host_options(edit, edit=True)
    edit.set_defaults(func=cmd_edit)

    rm = sub.add_parser("rm", parents=[common], help="remove a host")
    rm.add_argument("name", metavar="NAME", help="host name or alias")
    rm.set_defaults(func=cmd_rm)

    grp = sub.add_parser("group", parents=[common], help="list groups, or declare, change or remove one")
    grp.add_argument("name", metavar="NAME", nargs="?", help="group to declare or change; without it, list groups")
    grp.add_argument("--description", metavar="TEXT", help="a zone's section header, further lines below it; '' clears it")
    grp.add_argument("--child", metavar="GROUP", action="append", default=[], help="add a child group; repeatable")
    grp.add_argument("--unchild", metavar="GROUP", action="append", default=[], help="drop a child group; repeatable")
    grp.add_argument(
        "--reason",
        metavar="KEY=TEXT",
        action="append",
        default=[],
        type=_key_value,
        help="add or change a reason hosts can give; KEY= removes it; repeatable",
    )
    grp.add_argument("--rm", action="store_true", help="remove the group")
    grp.set_defaults(func=cmd_group)

    key = sub.add_parser("key", parents=[common], help="list keys, or declare, move or remove one")
    key.add_argument("name", metavar="NAME", nargs="?", help="key to declare or change; without it, list keys")
    key.add_argument("--path", metavar="PATH", help="the private key file, written as IdentityFile")
    key.add_argument("--rm", action="store_true", help="remove the key")
    key.set_defaults(func=cmd_key)

    mods = sub.add_parser("modules", parents=[common], help="list installed modules and where they're on")
    mods.set_defaults(func=cmd_modules)

    imp = sub.add_parser("import", parents=[common], help="import hosts through a module")
    imp.add_argument("module", metavar="MODULE", help="module to read with, e.g. ssh")
    imp.add_argument("source", metavar="SOURCE", nargs="?", help="what to read, e.g. an ssh config file")
    imp.add_argument(
        "--exclude", metavar="MODULE", action="append", default=[], help="keep imported hosts out of MODULE's output"
    )
    imp.set_defaults(func=cmd_import)

    exp = sub.add_parser("export", parents=[common], help="write the generated files")
    exp.add_argument("modules", metavar="MODULE", nargs="*", help="only these modules (default: all enabled)")
    exp.add_argument("--force", action="store_true", help="overwrite files edited since the last export")
    exp.set_defaults(func=cmd_export)

    return parser


def _terminal() -> bool:
    """Whether there's a terminal to draw the TUI on: input from one and output to one."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command is None:
            if not _terminal():
                parser.print_help()
                return 0
            from . import tui  # textual loads only when the TUI opens

            return tui.run(load_config(), _inventory_arg(args))
        # init writes the config and modules lists what's installed, so neither needs one.
        if args.func is cmd_init:
            return cmd_init(args)
        return args.func(load_config(missing_ok=args.func is cmd_modules), args)
    except HostsError as e:
        _note("error", "bold red", str(e))
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
