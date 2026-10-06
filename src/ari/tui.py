"""The TUI: ari with no command on a terminal. It holds no logic; every action calls core, as the CLI does."""

import getpass
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Checkbox, DataTable, Footer, Input, Label, Select, SelectionList, Static, TextArea
from textual.widgets.selection_list import Selection

from . import core, keyfiles
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
        Binding("q", "app.quit", "Quit"),
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
        Binding("g", "groups", "Groups"),
        Binding("d", "delete", "Delete"),
        Binding("q", "app.quit", "Quit"),
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
        self.app.ssh(self.row)

    def action_edit(self) -> None:
        # The list's cursor is still on this host, so the list opens the form and reloads after it.
        self.app.pop_screen()
        self.app.hosts.action_edit()

    def action_groups(self) -> None:
        self.app.pop_screen()
        self.app.hosts.action_groups()

    def action_delete(self) -> None:
        self.app.pop_screen()
        self.app.hosts.action_delete()


# The form's text inputs: field, as core.Problem names it, and label.
TEXT_FIELDS = (
    ("name", "name"),
    ("hostname", "hostname"),
    ("user", "user"),
    ("port", "port"),
    ("keys", "keys"),
    ("os", "os"),
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
        if field in ("aliases", "keys"):
            return " ".join(value)
        return "" if value is None else str(value)

    def _placeholder(self, field: str) -> str:
        defaults = self.inventories[self.inventory].defaults
        if field == "user":
            return f"{defaults.user} (default)" if defaults.user else f"{getpass.getuser()} (whoever connects)"
        if field == "port":
            return f"{defaults.port or 22} (default)"
        if field == "keys":
            return f"{' '.join(defaults.keys)} (default)" if defaults.keys else "declared names, space-separated"
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
                lines = [f"{k} {v}" for k, value in options.items() for v in (value if isinstance(value, list) else [value])]
                yield TextArea("\n".join(lines), id="f-options")
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
        for field in ("user", "port", "keys"):
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
        for field in ("user", "port", "os", "notes"):
            if value[field] != self._own(field):
                setattr(c, field, value[field])  # an empty one clears the field, so the default applies
        if value["keys"].split() != (self.host.keys if self.host else []):
            c.keys = ["", *value["keys"].split()]  # the list as typed, in order; empty goes back to the defaults

        old = self.host.aliases if self.host else []
        new = value["aliases"].split()
        c.unalias = [a for a in old if a not in new]
        c.aliases = [a for a in new if a not in old]

        current = self.host.modules.get("ssh", {}).get("options", {}) if self.host else {}
        wanted = parse_options(self.query_one("#f-options", TextArea).text)
        keys = {k.casefold() for k, _ in wanted}
        c.options = [(k, "") for k in current if k.casefold() not in keys]
        # A keyword on several lines holds them all, as a list; only one that changed is sent.
        given: dict[str, list[str]] = {}
        for key, val in wanted:
            given.setdefault(key.casefold(), []).append(val)
        for key, val in wanted:
            mine = next((v for k, v in current.items() if k.casefold() == key.casefold()), None)
            values = given[key.casefold()]
            if (values[0] if len(values) == 1 else values) != mine:
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
                inventory, host, changed = core.edit_host(self.cfg, self.host.name, c, self.inventory, by_name=True)
                done = f"{'updated' if changed else 'no change to'} {host.name} ({inventory.name})"
        except core.HostRefused as e:
            self.show(e.problems)
            return
        except HostsError as e:
            self.show([core.Problem(None, str(e))])
            return
        self.app.notify(done)
        self.dismiss(host.name)


NO_REASON = ""


class GroupPicker(Screen[str | None]):
    """The host's groups, as a list of the inventory's declared ones to check, and a reason for each
    checked group that declares reasons. Saving runs what ari edit --group and --ungroup run."""

    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("escape", "app.pop_screen", "Cancel"),
    ]

    def __init__(self, cfg: Config, inventory: Inventory, host: Host) -> None:
        super().__init__()
        self.cfg = cfg
        self.inventory = inventory
        self.host = host
        self.reasons = dict(host.reasons)  # the reason picked for each group, as the picker stands
        # Declared groups in declaration order, then any the record names without a declaration,
        # so a stale membership shows and can be unchecked.
        self.groups = list(inventory.groups) + [g for g in host.groups if g not in inventory.groups]
        self.current: str | None = None  # the group the reason picker speaks for

    def _prompt(self, group: str) -> Text:
        declared = self.inventory.groups.get(group)
        about = declared.description.partition("\n")[0] if declared else "not declared"
        prompt = Text(group)
        if self.reasons.get(group):
            prompt.append(f" ({self.reasons[group]})")
        if about:
            prompt.append(f"  {about}", style="dim")
        return prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Static(f"groups for {self.host.name} ({self.inventory.name})", id="title")
            yield Static(id="error-general", classes="error")
            if not self.groups:
                yield Static(
                    f"{self.inventory.name} declares no groups; ari group NAME -i {self.inventory.name} declares one",
                    id="empty",
                )
            yield SelectionList[str](
                *(Selection(self._prompt(g), g, g in self.host.groups, id=g) for g in self.groups), id="groups"
            )
            with Horizontal(id="reason-row"):
                yield Label("reason")
                yield Select([(NO_REASON, NO_REASON)], value=NO_REASON, allow_blank=False, id="reason")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#error-general").display = False
        self.query_one("#reason-row").display = False
        self.query_one(SelectionList).focus()

    def _reason_options(self, group: str) -> list[tuple[str, str]]:
        declared = self.inventory.groups.get(group)
        options = [("no reason", NO_REASON)]
        options += [(f"{key}: {text}", key) for key, text in (declared.reasons.items() if declared else [])]
        mine = self.reasons.get(group)
        if mine and all(key != mine for _, key in options):
            options.append((f"{mine} (not declared)", mine))
        return options

    def _follow(self, group: str | None) -> None:
        """Point the reason picker at group, showing it only when that group is checked and has reasons to give."""
        self.current = group
        row = self.query_one("#reason-row")
        checked = group is not None and group in self.query_one(SelectionList).selected
        options = self._reason_options(group) if checked else []
        row.display = len(options) > 1
        if row.display:
            select = self.query_one("#reason", Select)
            with self.prevent(Select.Changed):
                select.set_options(options)
                select.value = self.reasons.get(group, NO_REASON)

    def on_selection_list_selection_highlighted(self, event: SelectionList.SelectionHighlighted) -> None:
        self._follow(event.selection.value)

    def on_selection_list_selection_toggled(self, event: SelectionList.SelectionToggled) -> None:
        group = event.selection.value
        if group not in self.query_one(SelectionList).selected:
            self.reasons.pop(group, None)  # leaving a group drops its reason, as --ungroup does
            self.query_one(SelectionList).replace_option_prompt(group, self._prompt(group))
        self._follow(group)

    def on_select_changed(self, event: Select.Changed) -> None:
        if self.current is None:
            return
        if event.value == NO_REASON:
            self.reasons.pop(self.current, None)
        else:
            self.reasons[self.current] = str(event.value)
        self.query_one(SelectionList).replace_option_prompt(self.current, self._prompt(self.current))

    def changes(self) -> core.Changes:
        """The memberships that differ from the record, as ari edit --group and --ungroup would take them."""
        picked = set(self.query_one(SelectionList).selected)
        c = core.Changes()
        c.ungroup = [g for g in self.host.groups if g not in picked]
        for group in self.groups:
            if group not in picked:
                continue
            reason = self.reasons.get(group) or None
            if group not in self.host.groups or self.host.reasons.get(group) != reason:
                c.groups.append((group, reason))
        return c

    def action_save(self) -> None:
        c = self.changes()
        if c.is_empty():
            self.app.notify(f"no change to {self.host.name} ({self.inventory.name})")
            self.dismiss(self.host.name)
            return
        try:
            inventory, host, changed = core.edit_host(self.cfg, self.host.name, c, self.inventory.name, by_name=True)
        except HostsError as e:
            problems = e.problems if isinstance(e, core.HostRefused) else [core.Problem(None, str(e))]
            error = self.query_one("#error-general", Static)
            error.update("\n".join(p.message for p in problems))
            error.display = True
            return
        self.app.notify(f"{'updated' if changed else 'no change to'} {host.name} ({inventory.name})")
        self.dismiss(host.name)


Declared = tuple[str, str]  # inventory, name


@dataclass(frozen=True)
class Kind:
    """What a declarations screen lists, and the core calls behind its keys: keys now, groups
    next. rows gives each declaration's inventory, name and cells, in the order core lists them."""

    noun: str
    columns: tuple[str, ...]
    rows: Callable[[Config, str | None], list[tuple[str, str, list[Text]]]]
    uses: Callable[[Inventory, Host, str], bool]  # the hosts Enter narrows the list to
    check_remove: Callable[[Config, str, str], None]
    remove: Callable[[Config, str, str], object]
    removing: str  # the question d asks, with {name} and {inventory}
    empty: str  # what the screen says when nothing is declared
    check_generate: Callable[[Config, str, str], None] | None = None
    generate: Callable[[Config, str, str], str] | None = None  # what to report once it's done
    generator: str = ""  # the program generate hands the terminal to


def _key_rows(cfg: Config, scope: str | None) -> list[tuple[str, str, list[Text]]]:
    rows = []
    for row in core.list_keys(cfg, scope):
        hosts = Text(str(row.total))
        if row.direct != row.total:
            hosts.append(f" ({row.direct} direct)", style="dim")
        state = Text(", ".join(row.state), style="dim" if row.state == [keyfiles.OK] else "")
        cells = [Text(row.name), hosts, Text(row.key.path), state, Text(row.inventory.name, style="dim")]
        rows.append((row.inventory.name, row.name, cells))
    return rows


def _new_key(cfg: Config, inventory: str, name: str) -> str:
    saved, _ = core.write_key(cfg, inventory, name, core.KeyChanges(new=True))
    return f"generated {saved.keys[name].path} for {name} ({inventory})"


KEYS = Kind(
    noun="key",
    columns=("KEY", "HOSTS", "PATH", "STATE", "INV"),
    rows=_key_rows,
    uses=core.uses_key,
    check_remove=lambda cfg, inventory, name: core.check_key(cfg, inventory, name, core.KeyChanges(remove=True)),
    remove=lambda cfg, inventory, name: core.write_key(cfg, inventory, name, core.KeyChanges(remove=True)),
    removing="Remove key {name} from {inventory}? Its files stay.",
    empty="no keys declared; ari key NAME --path PATH declares one",
    check_generate=lambda cfg, inventory, name: core.check_key(cfg, inventory, name, core.KeyChanges(new=True)),
    generate=_new_key,
    generator="ssh-keygen",
)


class Declarations(Screen):
    """Every declaration of one kind in scope. Enter goes back to the host list narrowed to the
    hosts that use the one under the cursor; d removes it, or says why core won't; n, for a kind
    that generates, hands the terminal over, as s does for ssh."""

    BINDINGS = [
        Binding("enter", "narrow", "Hosts"),
        Binding("n", "generate", "New key"),
        Binding("d", "remove", "Delete"),
        Binding("escape", "back", "Back"),
        Binding("q", "app.quit", "Quit"),
    ]

    def __init__(self, cfg: Config, scope: str | None, kind: Kind) -> None:
        super().__init__()
        self.cfg = cfg
        self.scope = scope
        self.kind = kind
        self.shown: list[Declared] = []

    def compose(self) -> ComposeResult:
        yield Static(self.kind.empty, id="empty")
        yield DataTable(id="declarations", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        for label in self.kind.columns:
            table.add_column(label)
        self.reload()
        table.focus()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        return action != "generate" or self.kind.generate is not None

    def reload(self, fallback: int = 0, prefer: Declared | None = None) -> None:
        """Read the declarations again, the cursor on prefer, or its own row, or row fallback."""
        table = self.query_one(DataTable)
        keep = prefer or self.selected()
        try:
            rows = self.kind.rows(self.cfg, self.scope)
        except HostsError as e:
            self.app.notify(str(e), severity="error")
            return
        table.clear()
        self.shown = []
        for inventory, name, cells in rows:
            table.add_row(*cells, key=f"{inventory}\t{name}")
            self.shown.append((inventory, name))
        self.query_one("#empty").display = not self.shown
        if keep in self.shown:
            table.move_cursor(row=self.shown.index(keep))
        elif self.shown:
            table.move_cursor(row=min(fallback, len(self.shown) - 1))

    def selected(self) -> Declared | None:
        table = self.query_one(DataTable)
        if not 0 <= table.cursor_row < len(self.shown):
            return None
        return self.shown[table.cursor_row]

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_narrow()

    def action_back(self) -> None:
        self.app.pop_screen()
        self.app.hosts.reload(self.app.hosts.query_one(DataTable).cursor_row)

    def action_narrow(self) -> None:
        row = self.selected()
        if row:
            self.app.pop_screen()
            self.app.hosts.narrow_to(self.kind, *row)

    def action_remove(self) -> None:
        row = self.selected()
        if not row:
            return
        inventory, name = row
        try:
            self.kind.check_remove(self.cfg, inventory, name)
        except HostsError as e:
            self.app.notify(str(e), severity="error")
            return
        index = self.query_one(DataTable).cursor_row

        def answered(yes: bool | None) -> None:
            if not yes:
                return
            try:
                self.kind.remove(self.cfg, inventory, name)
            except HostsError as e:
                self.app.notify(str(e), severity="error")
            else:
                self.app.notify(f"removed {name} from {inventory}")
            self.reload(fallback=index)

        self.app.push_screen(Confirm(self.kind.removing.format(name=name, inventory=inventory)), answered)

    def action_generate(self) -> None:
        row = self.selected()
        if not row or self.kind.generate is None or self.kind.check_generate is None:
            return
        inventory, name = row
        # Everything core would refuse is said here, before the terminal is handed over.
        try:
            self.kind.check_generate(self.cfg, inventory, name)
        except HostsError as e:
            self.app.notify(str(e), severity="error")
            return
        done: str | None = None
        failed: str | None = None
        try:
            with self.app.suspend():
                # Nothing may leave this block by raising: suspend only resumes the app when it ends.
                try:
                    done = self.kind.generate(self.cfg, inventory, name)
                except HostsError as e:
                    failed = str(e)
                except KeyboardInterrupt:
                    failed = f"{self.kind.generator} interrupted; nothing saved"
        except SuspendNotSupported:
            self.app.notify(f"this terminal can't hand over to {self.kind.generator}", severity="error")
            return
        if failed:
            self.app.notify(failed, severity="error")
        else:
            self.app.notify(done or "")
        self.reload(prefer=row)


class HostList(Screen):
    """Every host in scope, narrowed by the filter the way ls --search narrows."""

    BINDINGS = [
        Binding("slash", "filter", "Filter"),
        Binding("enter", "details", "Details"),
        Binding("s", "ssh", "ssh"),
        Binding("a", "add", "Add"),
        Binding("e", "edit", "Edit"),
        Binding("g", "groups", "Groups"),
        Binding("d", "delete", "Delete"),
        Binding("x", "export", "Export"),
        Binding("k", "keys", "Keys"),
        Binding("q", "app.quit", "Quit"),
        Binding("escape", "clear", "Clear filter", show=False),
    ]

    def __init__(self, cfg: Config, scope: str | None, modules: list[str], rows: list[Row]) -> None:
        super().__init__()
        self.cfg = cfg
        self.scope = scope
        self.modules = modules
        self.rows = rows
        self.shown: list[Row] = []
        # Set by Enter on a declarations screen: only the hosts that use that declaration show.
        self.narrow: tuple[Kind, str, str] | None = None

    def compose(self) -> ComposeResult:
        yield Input(placeholder="filter", id="filter")
        yield Static(id="narrow")
        yield DataTable(id="hosts", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        for label in ("NAME", "HOSTNAME", "USER", "INV", *(m.upper() for m in self.modules), "GROUPS"):
            table.add_column(label)
        self.query_one("#narrow").display = False
        self.refilter("")
        table.focus()

    def _narrowed(self, inventory: Inventory, host: Host) -> bool:
        if self.narrow is None:
            return True
        kind, where, name = self.narrow
        return inventory.name == where and kind.uses(inventory, host, name)

    def narrow_to(self, kind: Kind, inventory: str, name: str) -> None:
        """Show only the hosts that use one declaration, as its HOSTS column counts them. The
        filter still narrows within them, and Escape clears both."""
        self.narrow = (kind, inventory, name)
        label = self.query_one("#narrow", Static)
        label.update(Text.assemble(f"hosts using {kind.noun} {name} ({inventory})", ("  Escape shows every host", "dim")))
        label.display = True
        self.reload()
        self.query_one(DataTable).focus()

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
        self.shown = [row for row in self.rows if core.matches(*row, text) and self._narrowed(*row)]
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
        self.narrow = None
        self.query_one("#narrow").display = False
        self.query_one(Input).value = ""
        self.refilter("")
        self.query_one(DataTable).focus()

    def action_keys(self) -> None:
        self.app.push_screen(Declarations(self.cfg, self.scope, KEYS))

    def action_details(self) -> None:
        row = self.selected()
        if row:
            self.app.push_screen(Details(row))

    def action_ssh(self) -> None:
        row = self.selected()
        if row:
            self.app.ssh(row)

    def _saved(self, name: str | None) -> None:
        """After a form or the picker saves: read the inventories again, with the cursor on the host."""
        self.reload(self.query_one(DataTable).cursor_row, prefer=name)
        if name and name not in [h.name for _, h in self.shown]:
            self.app.notify(f"the filter hides {name}")

    def _form(self, inventory: str, host: Host | None) -> None:
        try:
            inventories = core.load_all(self.cfg)
        except HostsError as e:
            self.app.notify(str(e), severity="error")
            return
        self.app.push_screen(HostForm(self.cfg, inventories, inventory, host), self._saved)

    def action_groups(self) -> None:
        row = self.selected()
        if not row:
            return
        # The picker works from the record as it is on disk now, declared groups included.
        try:
            inventory = core.load_all(self.cfg)[row[0].name]
        except HostsError as e:
            self.app.notify(str(e), severity="error")
            return
        host = inventory.named(row[1].name)
        if host is None:
            self.app.notify(f"{row[1].name} is no longer in {inventory.name}", severity="error")
            self.reload()
            return
        self.app.push_screen(GroupPicker(self.cfg, inventory, host), self._saved)

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
            core.remove_host(self.cfg, host.name, inventory.name, by_name=True)
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
    """The TUI: the host list, and every screen it opens."""

    TITLE = "ari"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    #details, #report, #form, #picker { padding: 1 2; }
    #empty { padding: 1 2; }
    #narrow { padding: 0 1; }
    #form .row { height: auto; }
    #form .row Label { width: 14; padding: 1 1 0 0; }
    #form .row Input, #form .row Select { width: 1fr; }
    #form .row.tall TextArea { height: 6; width: 1fr; }
    #form .error { color: $error; padding: 0 0 0 14; }
    #form #title, #picker #title { text-style: bold; padding: 0 0 1 0; }
    #picker .error { color: $error; padding: 0 0 1 0; }
    #picker SelectionList { height: 1fr; }
    #reason-row { height: auto; padding: 1 0 0 0; }
    #reason-row Label { width: 8; padding: 1 1 0 0; }
    #reason-row Select { width: 1fr; }
    Confirm { align: center middle; }
    #dialog { width: auto; height: auto; padding: 1 3; border: thick $error; background: $surface; }
    """
    # q quits only where nothing is unsaved: the list, a host's details, an export report and the
    # keys screen bind it. In the form, the picker and the delete prompts it's just a key, and
    # Escape is the way out.

    def __init__(self, cfg: Config, scope: str | None, modules: list[str], rows: list[Row]) -> None:
        super().__init__()
        self.hosts = HostList(cfg, scope, modules, rows)

    def get_default_screen(self) -> Screen:
        return self.hosts

    def ssh(self, row: Row) -> None:
        """Hand the terminal to ssh, and take it back when the session ends. Only for a host the ssh
        module writes: for any other, ssh <name> would go wherever DNS sends that name."""
        inventory, host = row
        name = host.name
        reach = core.module_reach(self.hosts.cfg, inventory, host, "ssh")
        if reach is not core.ModuleReach.WRITTEN:
            why = "it excludes ssh" if reach is core.ModuleReach.EXCLUDED else f"{inventory.name} has the ssh module off"
            self.notify(f"{name} isn't in the ssh config ari writes ({why}), so ssh {name} would go wherever DNS says", severity="warning")
            return
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
