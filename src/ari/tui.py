"""The TUI: ari with no command on a terminal. It holds no logic; every action calls core, as the CLI does."""

import getpass
import subprocess

from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Footer, Input, Static

from . import core
from .config import Config
from .errors import HostsError
from .models import Host, Inventory

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

    def action_delete(self) -> None:
        # The list's cursor is still on this host, so the list asks and deletes.
        self.app.pop_screen()
        self.app.hosts.action_delete()


class HostList(Screen):
    """Every host in scope, narrowed by the filter the way ls --search narrows."""

    BINDINGS = [
        Binding("slash", "filter", "Filter"),
        Binding("enter", "details", "Details"),
        Binding("s", "ssh", "ssh"),
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

    def refilter(self, text: str, fallback: int = 0) -> None:
        """Rebuild the table from the hosts that match. The cursor stays on its host if it's still there,
        and goes to row fallback, or the last row, if it isn't."""
        table = self.query_one(DataTable)
        current = self.selected()
        self.shown = [row for row in self.rows if core.matches(*row, text)]
        table.clear()
        for inventory, host in self.shown:
            table.add_row(*self._cells(inventory, host), key=host.name)
        names = [host.name for _, host in self.shown]
        if current and current[1].name in names:
            table.move_cursor(row=names.index(current[1].name))
        elif names:
            table.move_cursor(row=min(fallback, len(names) - 1))

    def reload(self, fallback: int = 0) -> None:
        """Read the inventories again after a change, keeping the filter."""
        try:
            self.rows = core.list_hosts(self.cfg, self.scope)
        except HostsError as e:
            self.app.notify(str(e), severity="error")
            return
        self.refilter(self.query_one(Input).value, fallback)

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
    #details, #report { padding: 1 2; }
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
