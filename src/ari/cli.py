"""ari: keep your SSH hosts in one place, export ssh config and Ansible inventory."""

import argparse
import getpass
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
    inventory, host = core.find_host(cfg, args.name)
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
    grid.add_row("groups", Text(", ".join(host.groups) or "-"))
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
    return 0


def cmd_import(cfg: Config, args: argparse.Namespace) -> int:
    inventory = cfg.select(_inventory_arg(args)).name
    report = core.import_hosts(cfg, inventory, args.module, args.source, exclude=args.exclude)
    for warning in report.warnings:
        _note("warning", "yellow", warning)
    for conflict in report.conflicts:
        _note("conflict", "red", conflict)
    if report.defaults_set:
        values = ", ".join(f"{k} {v}" for k, v in report.defaults_set.items())
        out.print(f"{report.inventory}: defaults set from the most common values: {values}", soft_wrap=True)
    for label, names in (("added", report.added), ("merged", report.merged), ("unchanged", report.unchanged)):
        if names:
            listed = f" ({', '.join(names)})" if len(names) <= 12 else ""
            out.print(f"{report.inventory}: {len(names)} {label}{listed}", soft_wrap=True)
    if not (report.added or report.merged or report.unchanged):
        out.print(f"{report.inventory}: nothing imported from {report.source}")
    return 1 if report.conflicts else 0


def cmd_export(cfg: Config, args: argparse.Namespace) -> int:
    report = core.export(cfg, _inventory_arg(args), only=args.modules or None, force=args.force)
    for w in report.written:
        p = w.planned
        verb = "unchanged" if w.status is Status.SAME else "wrote"
        out.print(f"{verb} {tilde(p.path)} ({p.inventory}, {p.module}, {_plural(p.hosts, 'host')})", soft_wrap=True)
    for note in report.notes:
        _note("note", "dim", note)
    return 0


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

    ls = sub.add_parser("ls", parents=[common], help="list hosts across inventories")
    ls.add_argument("--search", metavar="TEXT", help="match any field, case-insensitively")
    ls.add_argument("--group", metavar="GROUP", help="only hosts in this group")
    ls.set_defaults(func=cmd_ls)

    show = sub.add_parser("show", parents=[common], help="show one host with its effective values")
    show.add_argument("name", help="host name or alias")
    show.set_defaults(func=cmd_show)

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
        return args.func(load_config(), args)
    except HostsError as e:
        _note("error", "bold red", str(e))
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
