"""The TUI: ari with no command on a terminal. It holds no logic; every action calls core, as the CLI does."""

import getpass
import re
import subprocess

from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Checkbox, DataTable, Footer, Input, Label, Select, Static, TextArea

from . import core
from .config import Config
from .errors import HostsError
from .models import Host, Inventory
from .modules import registry

Row = tuple[Inventory, Host]


def run(cfg: Config, scope: str | None) -> int:
    # Read every inventory before the screen changes, so a bad file or -i errors like any other command.
    rows = core.list_hosts(cfg, scope)
    Browser(cfg, scope, core.export_modules(cfg, scope), rows).run()
    return 0


class Confirm(ModalScreen[bool]):
    """A yes or no question over the screen below it."""

    BINDINGS = [
        Binding("y", "answer(True)", "Yes"),
        Binding("n", "answer(False)", "No"),
        Binding("escape", "answer(False)", "No", show=False),
    ]

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.question)
        yield Footer()

    def action_answer(self, yes: bool) -> None:
        self.dismiss(yes)


class Report(Screen):
    """What an export did, or every reason it wrote nothing."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("enter", "app.pop_screen", "Back", show=False),
    ]

    def __init__(self, text: Text) -> None:
        super().__init__()
        self.text = text

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="report"):
            yield Static(self.text)
        yield Footer()


class Details(Screen):
    """One host, field by field, as ari show prints it."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("s", "ssh", "ssh"),
        Binding("e", "edit", "Edit"),
        Binding("d", "delete", "Delete"),
    ]

    def __init__(self, row: Row) -> None:
        super().__init__()
        self.row = row

    def compose(self) -> ComposeResult:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="dim")
        grid.add_column()
        for d in core.host_details(*self.row):
            grid.add_row(d.label, Text.assemble(d.value, (f"  {d.note}", "dim")) if d.note else Text(d.value))
        yield Static(grid, id="details")
        yield Footer()

    def action_ssh(self) -> None:
        self.app.ssh(self.row[1].name)

    def action_edit(self) -> None:
        # The list's cursor is still on this host, so the list opens the form and reloads after it.
        self.app.pop_screen()
        self.app.hosts.action_edit()

    def action_delete(self) -> None:
        self.app.pop_screen()
        self.app.hosts.action_delete()


# The form's text inputs: field, as core.Problem names it, and label.
TEXT_FIELDS = (
    ("name", "name"),
    ("hostname", "hostname"),
    ("user", "user"),
    ("port", "port"),
    ("ssh_key", "key"),
    ("notes", "notes"),
    ("aliases", "aliases"),
)
FORM_FIELDS = {f for f, _ in TEXT_FIELDS} | {"options", "exclude"}

# One ssh option per line, the way ssh_config(5) takes them: a keyword, then whitespace or one =, then the value.
_OPTION = re.compile(r"\s*([^\s=]+)(?:\s*=\s*|\s+)?(.*?)\s*")


def parse_options(text: str) -> list[tuple[str, str]]:
    pairs = []
    for line in text.splitlines():
        if line.strip():
            m = _OPTION.fullmatch(line)
            pairs.append((m[1], m[2]) if m else (line.strip(), ""))
    return pairs


class HostForm(Screen[str | None]):
    """Add a host, or edit one. Saving builds the same Changes ari add and ari edit build, so the same
    checks run; each problem shows beside the input it's about. Dismisses with the saved host's name."""

    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("escape", "app.pop_screen", "Cancel"),
    ]

    def __init__(self, cfg: Config, inventories: dict[str, Inventory], inventory: str, host: Host | None = None) -> None:
        super().__init__()
        self.cfg = cfg
        self.inventories = inventories
        self.inventory = inventory
        self.host = host
        self.modules = [m.name for m in registry().modules.values() if m.exports]

    def _own(self, field: str) -> str:
        """The host's own value for a text field, as the input shows it."""
        if self.host is None:
            return ""
        value = getattr(self.host, field)
        if field == "aliases":
            return " ".join(value)
        return "" if value is None else str(value)

    def _placeholder(self, field: str) -> str:
        defaults = self.inventories[self.inventory].defaults
        if field == "user":
            return f"{defaults.user} (default)" if defaults.user else f"{getpass.getuser()} (whoever connects)"
        if field == "port":
            return f"{defaults.port or 22} (default)"
        if field == "ssh_key":
            return f"{defaults.ssh_key} (default)" if defaults.ssh_key else ""
        if field == "aliases":
            return "space-separated"
        return ""

    def compose(self) -> ComposeResult:
        title = f"edit {self.host.name} ({self.inventory})" if self.host else "add a host"
        with VerticalScroll(id="form"):
            yield Static(title, id="title")
            yield Static(id="error-general", classes="error")
            if self.host is None:
                with Horizontal(classes="row"):
                    yield Label("inventory")
                    yield Select([(n, n) for n in self.cfg.inventories], value=self.inventory, allow_blank=False, id="f-inventory")
            for field, label in TEXT_FIELDS:
                with Horizontal(classes="row"):
                    yield Label(label)
                    yield Input(self._own(field), placeholder=self._placeholder(field), id=f"f-{field}")
                yield Static(id=f"error-{field}", classes="error")
            options = self.host.modules.get("ssh", {}).get("options", {}) if self.host else {}
            with Horizontal(classes="row tall"):
                yield Label("ssh options")
                yield TextArea("\n".join(f"{k} {v}" for k, v in options.items()), id="f-options")
            yield Static(id="error-options", classes="error")
            with Horizontal(classes="row"):
                yield Label("exclude")
                for module in self.modules:
                    yield Checkbox(module, module in (self.host.exclude if self.host else []), id=f"x-{module}")
            yield Static(id="error-exclude", classes="error")
        yield Footer()

    def on_mount(self) -> None:
        for error in self.query(".error"):
            error.display = False
        self.query_one("#f-name", Input).focus()

    def on_select_changed(self, event: Select.Changed) -> None:
        # Placeholders show the defaults of the inventory the host is going into.
        self.inventory = str(event.value)
        for field in ("user", "port", "ssh_key"):
            self.query_one(f"#f-{field}", Input).placeholder = self._placeholder(field)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.focus_next()

    def changes(self) -> core.Changes:
        """What the form says, as the Changes ari add or ari edit would build from the same values."""
        value = {field: self.query_one(f"#f-{field}", Input).value for field, _ in TEXT_FIELDS}
        c = core.Changes()
        if self.host is not None:
            if value["name"] != self.host.name:
                c.rename = value["name"]
            if value["hostname"] != self.host.hostname:
                c.hostname = value["hostname"]
        for field in ("user", "port", "ssh_key", "notes"):
            if value[field] != self._own(field):
                setattr(c, field, value[field])  # an empty one clears the field, so the default applies

        old = self.host.aliases if self.host else []
        new = value["aliases"].split()
        c.unalias = [a for a in old if a not in new]
        c.aliases = [a for a in new if a not in old]

        current = self.host.modules.get("ssh", {}).get("options", {}) if self.host else {}
        wanted = parse_options(self.query_one("#f-options", TextArea).text)
        keys = {k.casefold() for k, _ in wanted}
        c.options = [(k, "") for k in current if k.casefold() not in keys]
        for key, val in wanted:
            mine = next((v for k, v in current.items() if k.casefold() == key.casefold()), None)
            if val != mine:
                c.options.append((key, val))

        excluded = set(self.host.exclude) if self.host else set()
        checked = {m for m in self.modules if self.query_one(f"#x-{m}", Checkbox).value}
        c.exclude = [m for m in self.modules if m in checked - excluded]
        c.include = [m for m in self.modules if m in excluded - checked]
        return c

    def show(self, problems: list[core.Problem]) -> None:
        """Each problem under the input it's about; the rest at the top."""
        by_field: dict[str, list[str]] = {}
        for p in problems:
            by_field.setdefault(p.field if p.field in FORM_FIELDS else "general", []).append(p.message)
        for error in self.query(".error"):
            messages = by_field.get(error.id.removeprefix("error-"), [])
            error.update("\n".join(messages))
            error.display = bool(messages)
        first = next((f for f, _ in TEXT_FIELDS if f in by_field), None)
        if first:
            self.query_one(f"#f-{first}", Input).focus()

    def action_save(self) -> None:
        c = self.changes()
        try:
            if self.host is None:
                name = self.query_one("#f-name", Input).value
                hostname = self.query_one("#f-hostname", Input).value
                inventory, host = core.add_host(self.cfg, self.inventory, name, hostname, c)
                done = f"added {host.name} to {inventory.name}"
            elif c.is_empty():
                self.app.notify(f"no change to {self.host.name} ({self.inventory})")
                self.dismiss(self.host.name)
                return
            else:
                inventory, host, changed = core.edit_host(self.cfg, self.host.name, c, self.inventory)
                done = f"{'updated' if changed else 'no change to'} {host.name} ({inventory.name})"
        except core.HostRefused as e:
            self.show(e.problems)
            return
        except HostsError as e:
            self.show([core.Problem(None, str(e))])
            return
        self.app.notify(done)
        self.dismiss(host.name)


class HostList(Screen):
    """Every host in scope, narrowed by the filter the way ls --search narrows."""

    BINDINGS = [
        Binding("slash", "filter", "Filter"),
        Binding("enter", "details", "Details"),
        Binding("s", "ssh", "ssh"),
        Binding("a", "add", "Add"),
        Binding("e", "edit", "Edit"),
        Binding("d", "delete", "Delete"),
        Binding("x", "export", "Export"),
        Binding("escape", "clear", "Clear filter", show=False),
    ]

    def __init__(self, cfg: Config, scope: str | None, modules: list[str], rows: list[Row]) -> None:
        super().__init__()
        self.cfg = cfg
        self.scope = scope
        self.modules = modules
        self.rows = rows
        self.shown: list[Row] = []

    def compose(self) -> ComposeResult:
        yield Input(placeholder="filter", id="filter")
        yield DataTable(id="hosts", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        for label in ("NAME", "HOSTNAME", "USER", "INV", *(m.upper() for m in self.modules), "GROUPS"):
            table.add_column(label)
        self.refilter("")
        table.focus()

    def _cells(self, inventory: Inventory, host: Host) -> list[Text]:
        user = inventory.user(host)
        marks = []
        for module in self.modules:
            reach = core.module_reach(self.cfg, inventory, host, module)
            marks.append(Text(reach.value, style="dim" if reach is core.ModuleReach.OFF else ""))
        return [
            Text(host.name),
            Text(host.hostname),
            Text(user) if user else Text(getpass.getuser(), style="dim"),
            Text(inventory.name, style="dim"),
            *marks,
            Text(", ".join(host.groups) or "-"),
        ]

    def refilter(self, text: str, fallback: int = 0, prefer: str | None = None) -> None:
        """Rebuild the table from the hosts that match. The cursor goes to the host named prefer, or
        stays on its host, if that one still shows, and otherwise to row fallback, or the last row."""
        table = self.query_one(DataTable)
        current = self.selected()
        keep = prefer or (current[1].name if current else None)
        self.shown = [row for row in self.rows if core.matches(*row, text)]
        table.clear()
        for inventory, host in self.shown:
            table.add_row(*self._cells(inventory, host), key=host.name)
        names = [host.name for _, host in self.shown]
        if keep in names:
            table.move_cursor(row=names.index(keep))
        elif names:
            table.move_cursor(row=min(fallback, len(names) - 1))

    def reload(self, fallback: int = 0, prefer: str | None = None) -> None:
        """Read the inventories again after a change, keeping the filter."""
        try:
            self.rows = core.list_hosts(self.cfg, self.scope)
        except HostsError as e:
            self.app.notify(str(e), severity="error")
            return
        self.refilter(self.query_one(Input).value, fallback, prefer)

    def selected(self) -> Row | None:
        table = self.query_one(DataTable)
        if not 0 <= table.cursor_row < len(self.shown):
            return None
        return self.shown[table.cursor_row]

    def on_input_changed(self, event: Input.Changed) -> None:
        self.refilter(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.query_one(DataTable).focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_details()

    def action_filter(self) -> None:
        self.query_one(Input).focus()

    def action_clear(self) -> None:
        self.query_one(Input).value = ""
        self.query_one(DataTable).focus()

    def action_details(self) -> None:
        row = self.selected()
        if row:
            self.app.push_screen(Details(row))

    def action_ssh(self) -> None:
        row = self.selected()
        if row:
            self.app.ssh(row[1].name)

    def _form(self, inventory: str, host: Host | None) -> None:
        try:
            inventories = core.load_all(self.cfg)
        except HostsError as e:
            self.app.notify(str(e), severity="error")
            return

        def saved(name: str | None) -> None:
            self.reload(self.query_one(DataTable).cursor_row, prefer=name)
            if name and name not in [h.name for _, h in self.shown]:
                self.app.notify(f"the filter hides {name}")

        self.app.push_screen(HostForm(self.cfg, inventories, inventory, host), saved)

    def action_add(self) -> None:
        try:
            inventory = self.cfg.select(self.scope).name
        except HostsError:
            inventory = next(iter(self.cfg.inventories))  # nothing names one: the form's picker starts at the first
        self._form(inventory, None)

    def action_edit(self) -> None:
        row = self.selected()
        if row:
            self._form(row[0].name, row[1])

    def action_delete(self) -> None:
        row = self.selected()
        if not row:
            return
        inventory, host = row

        def answered(yes: bool | None) -> None:
            if yes:
                self.delete(row)

        self.app.push_screen(Confirm(f"Delete {host.name} from {inventory.name}?"), answered)

    def delete(self, row: Row) -> None:
        inventory, host = row
        index = self.query_one(DataTable).cursor_row
        try:
            core.remove_host(self.cfg, host.name, inventory.name)
        except HostsError as e:
            self.app.notify(str(e), severity="error")
        else:
            self.app.notify(f"removed {host.name} from {inventory.name}")
        self.reload(fallback=index)

    def action_export(self) -> None:
        stopped = Text("export stopped, nothing written", style="bold red")
        try:
            report = core.export(self.cfg, self.scope)
        except core.ExportStopped as e:
            lines = [stopped, *(Text(f"  {p}") for p in e.problems)]
            if e.hint:
                lines.append(Text(e.hint, style="dim"))
        except HostsError as e:
            lines = [stopped, Text(f"  {e}")]
        else:
            lines = [Text(w.summary()) for w in report.written]
            lines += [Text(note, style="dim") for note in report.notes]
        self.app.push_screen(Report(Text("\n").join(lines)))


class Browser(App):
    TITLE = "ari"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    #details, #report, #form { padding: 1 2; }
    #form .row { height: auto; }
    #form .row Label { width: 14; padding: 1 1 0 0; }
    #form .row Input, #form .row Select { width: 1fr; }
    #form .row.tall TextArea { height: 6; width: 1fr; }
    #form .error { color: $error; padding: 0 0 0 14; }
    #form #title { text-style: bold; padding: 0 0 1 0; }
    Confirm { align: center middle; }
    #dialog { width: auto; height: auto; padding: 1 3; border: thick $error; background: $surface; }
    """
    BINDINGS = [Binding("q", "quit", "Quit")]

    def __init__(self, cfg: Config, scope: str | None, modules: list[str], rows: list[Row]) -> None:
        super().__init__()
        self.hosts = HostList(cfg, scope, modules, rows)

    def get_default_screen(self) -> Screen:
        return self.hosts

    def ssh(self, name: str) -> None:
        """Hand the terminal to ssh, and take it back when the session ends."""
        try:
            with self.suspend():
                code = subprocess.run(["ssh", name]).returncode
        except SuspendNotSupported:
            self.notify("this terminal can't hand over to ssh", severity="error")
            return
        except FileNotFoundError:
            self.notify("ssh isn't installed", severity="error")
            return
        if code == 255:
            self.notify(f"ssh {name} failed (exit 255)", severity="warning")
