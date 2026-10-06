"""The columns a host listing can have: ls --columns picks from them, ls --format and the table
module print them as Markdown or CSV. Values are the ones that take effect, defaults included."""

import csv
import io
from collections.abc import Callable
from dataclasses import dataclass

from .models import Host, Inventory


@dataclass(frozen=True)
class Column:
    """One column of a host listing: its name, as --columns takes it, its heading, and its value
    for a host, the one that takes effect. A list holds several values, shown joined with ", "."""

    name: str
    heading: str
    value: Callable[[Inventory, Host], str | list[str]]


COLUMNS: dict[str, Column] = {
    c.name: c
    for c in (
        Column("name", "NAME", lambda inventory, host: host.name),
        Column("hostname", "HOSTNAME", lambda inventory, host: host.hostname),
        Column("aliases", "ALIASES", lambda inventory, host: list(host.aliases)),
        # Blank when none is set: ssh then logs in as whoever connects, on whichever machine.
        Column("user", "USER", lambda inventory, host: inventory.user(host) or ""),
        Column("port", "PORT", lambda inventory, host: str(inventory.port(host))),
        Column("keys", "KEYS", lambda inventory, host: list(inventory.host_keys(host))),
        Column("os", "OS", lambda inventory, host: host.os),
        Column("notes", "NOTES", lambda inventory, host: host.notes),
        Column("groups", "GROUPS", lambda inventory, host: list(host.groups)),
        Column("exclude", "EXCLUDE", lambda inventory, host: list(host.exclude)),
        Column("inv", "INV", lambda inventory, host: inventory.name),
    )
}


def _joined(value: str | list[str]) -> str:
    return ", ".join(value) if isinstance(value, list) else value


def markdown_table(columns: list[Column], rows: list[tuple[Inventory, Host]]) -> str:
    """A Markdown table, one line per host: a value's lines join with <br>, and | is escaped."""

    def cell(value: str | list[str]) -> str:
        return "<br>".join(_joined(value).splitlines()).replace("|", "\\|")

    lines = ["| " + " | ".join(c.heading for c in columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    lines += ["| " + " | ".join(cell(c.value(inventory, host)) for c in columns) + " |" for inventory, host in rows]
    return "\n".join(lines) + "\n"


def csv_table(columns: list[Column], rows: list[tuple[Inventory, Host]]) -> str:
    """CSV under a header of column names, lines ending in \\n. A value with a comma, a quote or
    a line break is quoted, line breaks kept."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([c.name for c in columns])
    writer.writerows([_joined(c.value(inventory, host)) for c in columns] for inventory, host in rows)
    return out.getvalue()
