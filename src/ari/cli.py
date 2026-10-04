"""ari: keep your SSH hosts in one place, export ssh config and Ansible inventory."""

import argparse
import getpass
import json
import re
import sys

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from . import __version__, core
from .config import Config, load_config
from .errors import HostsError
from .guard import Status
from .modules import registry
from .paths import tilde

out = Console(highlight=False)
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


def cmd_ls(cfg: Config, args: argparse.Namespace) -> int:
    rows = core.list_hosts(cfg, _inventory_arg(args), args.search, args.group)
    if not rows:
        out.print("no hosts")
        return 0
    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    for column in ("NAME", "HOSTNAME", "USER", "PORT", "GROUPS", "INV"):
        table.add_column(column, justify="right" if column == "PORT" else "left")
    for inventory, host in rows:
        table.add_row(
            Text(host.name),
            Text(host.hostname),
            _user(inventory, host),
            Text(str(inventory.port(host))),
            Text(", ".join(host.groups) or "-"),
            Text(inventory.name, style="dim"),
        )
    out.print(table)
    out.print(Text(_plural(len(rows), "host"), style="dim"))
    return 0


def cmd_show(cfg: Config, args: argparse.Namespace) -> int:
    inventory, host = core.find_host(cfg, args.name, _inventory_arg(args))
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim")
    grid.add_column()

    def inherited(own, effective) -> Text:
        if effective is None:
            return Text("-")
        return Text(str(effective)) if own is not None else Text.assemble(str(effective), ("  (default)", "dim"))

    user = inventory.user(host)
    grid.add_row("inventory", Text(inventory.name))
    grid.add_row("name", Text(host.name))
    grid.add_row("aliases", Text(" ".join(host.aliases) or "-"))
    grid.add_row("hostname", Text(host.hostname))
    grid.add_row(
        "user",
        inherited(host.user, user) if user else Text.assemble(getpass.getuser(), ("  (whoever connects)", "dim")),
    )
    grid.add_row("port", inherited(host.port, inventory.port(host)))
    grid.add_row("ssh key", inherited(host.ssh_key, inventory.ssh_key(host)))
    grid.add_row("notes", Text(host.notes or "-"))
    groups = [f"{g} ({host.reasons[g]})" if g in host.reasons else g for g in host.groups]
    grid.add_row("groups", Text(", ".join(groups) or "-"))
    grid.add_row("exclude", Text(", ".join(host.exclude) or "-"))
    installed = registry().modules
    for name in sorted(set(installed) | set(host.modules)):
        if name in installed:
            lines = installed[name].describe(inventory, host)
        else:
            lines = [f"{k} {v}" for k, v in host.modules[name].items()] + ["(module not installed)"]
        if lines:
            grid.add_row(name, Text("\n".join(lines)))
    grid.add_row("updated", Text(host.last_updated or "-"))
    out.print(grid)
    return 0


def _changes(args: argparse.Namespace) -> core.Changes:
    return core.Changes(
        hostname=getattr(args, "new_hostname", None),
        rename=getattr(args, "new_name", None),
        user=args.user,
        port=args.port,
        ssh_key=args.key,
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
        p = w.planned
        verb = "unchanged" if w.status is Status.SAME else "wrote"
        mode = f"; mode set to {p.mode:04o}" if w.mode_fixed else ""
        out.print(f"{verb} {tilde(p.path)} ({p.inventory}, {p.module}, {_plural(p.hosts, 'host')}){mode}", soft_wrap=True)
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
    p.add_argument("--key", metavar="PATH", help="private key (IdentityFile)" + clear)
    p.add_argument("--notes", metavar="TEXT", help="free text, a comment above the Host block")
    p.add_argument("--alias", metavar="ALIAS", action="append", default=[], help="another name; repeatable")
    p.add_argument(
        "--opt",
        metavar="KEY=VALUE",
        action="append",
        default=[],
        type=_key_value,
        help="ssh option; repeatable" + ("; KEY= removes it" if edit else ""),
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
    show.add_argument("name", help="host name or alias")
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        # The TUI will live here; until then, and whenever stdout isn't a terminal, print help.
        parser.print_help()
        return 0
    try:
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
