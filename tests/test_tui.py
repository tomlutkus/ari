import asyncio
import contextlib
import json
import subprocess

import pytest
from rich.console import Console
from textual.widgets import Checkbox, DataTable, Input, Select, SelectionList, Static, TextArea

from conftest import MIXED, write_config, write_mixed
from ari import cli, core, tui
from ari.cli import main
from ari.config import load_config

@pytest.fixture
def hosts(home):
    write_mixed(home)
    return home


def browser(scope=None):
    cfg = load_config()
    return tui.Browser(cfg, scope, core.export_modules(cfg, scope), core.list_hosts(cfg, scope))


def drive(app, script):
    """Run script against the app, then note what the list shows before the app closes and its widgets go."""

    async def go():
        async with app.run_test() as pilot:
            await script(pilot)
            await pilot.pause()
            if app.is_running:
                app.seen = seen(app)

    asyncio.run(go())
    return app


def seen(app) -> dict:
    t = app.hosts.query_one(DataTable)
    labels = [c.label.plain for c in t.columns.values()]
    return {
        "columns": labels,
        "names": [key.value for key in t.rows],
        "cells": {key.value: dict(zip(labels, (c.plain for c in t.get_row(key)))) for key in t.rows},
        "filter": app.hosts.query_one(Input).value,
    }


def table(app) -> DataTable:
    return app.hosts.query_one(DataTable)


def names(app) -> list[str]:
    return [key.value for key in table(app).rows]


async def find(pilot, text):
    """Filter down to one host and go back to the list."""
    await pilot.press("slash", *text, "enter")


def test_rows_cover_every_inventory_with_a_column_per_exporting_module(hosts):
    seen = drive(browser(), lambda pilot: pilot.pause()).seen
    assert seen["columns"] == ["NAME", "HOSTNAME", "USER", "INV", "SSH", "ANSIBLE", "GROUPS"]
    assert seen["names"] == ["nas", "scratch", "fw", "vault-01", "web-01"]
    cells = seen["cells"]
    assert cells["vault-01"]["USER"] == "deploy" and cells["web-01"]["USER"] == "admin"
    assert cells["vault-01"]["GROUPS"] == "zone_app, no_auto_update" and cells["fw"]["GROUPS"] == "-"
    assert [cells["web-01"][m] for m in ("SSH", "ANSIBLE")] == ["✓", "✓"]
    assert [cells["fw"][m] for m in ("SSH", "ANSIBLE")] == ["✓", "excluded"]
    assert [cells["scratch"][m] for m in ("SSH", "ANSIBLE")] == ["excluded", "·"]
    assert cells["nas"]["ANSIBLE"] == "·"


def test_the_inventory_flag_narrows_rows_and_columns(hosts):
    seen = drive(browser("personal"), lambda pilot: pilot.pause()).seen
    assert seen["names"] == ["nas", "scratch"]
    assert seen["columns"] == ["NAME", "HOSTNAME", "USER", "INV", "SSH", "GROUPS"]


@pytest.mark.parametrize("needle", ["2222", "deploy", "lab", "SECRETS", "ansible", "storage", "zone", "0.2.1"])
def test_the_filter_narrows_the_way_ls_search_does(hosts, needle):
    seen = drive(browser(), lambda pilot: pilot.press("slash", *needle)).seen
    assert seen["filter"] == needle
    assert seen["names"] == [host.name for _, host in core.list_hosts(load_config(), search=needle)]


def test_letters_typed_into_the_filter_never_trigger_keys(hosts, monkeypatch):
    monkeypatch.setattr(tui.subprocess, "run", lambda *a, **k: pytest.fail("ssh ran"))
    app = drive(browser(), lambda pilot: pilot.press("slash", "s", "q"))
    assert app.seen["filter"] == "sq" and app.return_code is None


def test_enter_leaves_the_filter_and_escape_clears_it(hosts):
    async def script(pilot):
        await find(pilot, "web")
        assert app.focused is table(app) and names(app) == ["web-01"]
        await pilot.press("escape")
        assert app.focused is table(app) and app.hosts.query_one(Input).value == ""

    app = browser()
    assert len(drive(app, script).seen["names"]) == 5


def test_the_cursor_stays_on_its_host_while_the_filter_changes(hosts):
    async def script(pilot):
        await pilot.press("down", "down", "down", "down")
        assert app.hosts.selected()[1].name == "web-01"
        await pilot.press("slash", "z", "o", "n", "e")
        assert names(app) == ["vault-01", "web-01"] and app.hosts.selected()[1].name == "web-01"

    app = browser()
    drive(app, script)


def test_enter_shows_what_show_prints_and_escape_comes_back(hosts):
    async def script(pilot):
        await find(pilot, "vault")
        await pilot.press("enter")
        assert isinstance(app.screen, tui.Details)
        console = Console(width=100, record=True)
        console.print(app.screen.query_one(Static).content)
        shown = console.export_text()
        inventory, host = core.find_host(load_config(), "vault-01")
        for d in core.host_details(inventory, host):
            assert d.label in shown and d.value.splitlines()[0] in shown
        assert "(default)" in shown and "no_auto_update (secrets)" in shown
        await pilot.press("escape")
        assert app.screen is app.hosts

    app = browser()
    drive(app, script)


class Calls(list):
    code = 0


@pytest.fixture
def ssh(monkeypatch):
    """Record ssh instead of running it; the headless driver can't suspend, so suspending does nothing."""
    calls = Calls()

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, calls.code)

    monkeypatch.setattr(tui.subprocess, "run", run)
    monkeypatch.setattr(tui.Browser, "suspend", lambda self: contextlib.nullcontext())
    return calls


def test_s_hands_the_terminal_to_ssh_and_comes_back_to_the_list(hosts, ssh):
    async def script(pilot):
        await find(pilot, "fw")
        await pilot.press("s")
        assert app.screen is app.hosts
        await pilot.press("enter", "s")
        assert isinstance(app.screen, tui.Details)

    app = browser()
    drive(app, script)
    assert ssh == [["ssh", "fw"], ["ssh", "fw"]]


def test_a_failed_ssh_is_reported(hosts, ssh, monkeypatch):
    notes = []
    app = browser()
    monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append(message))
    ssh.code = 255
    drive(app, lambda pilot: pilot.press("s"))
    assert ssh == [["ssh", "nas"]] and notes == ["ssh nas failed (exit 255)"]


def test_without_a_terminal_to_suspend_ssh_never_runs(hosts, monkeypatch):
    notes = []
    monkeypatch.setattr(tui.subprocess, "run", lambda *a, **k: pytest.fail("ssh ran"))
    app = browser()
    monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append(message))
    drive(app, lambda pilot: pilot.press("s"))
    assert notes == ["this terminal can't hand over to ssh"]


@pytest.mark.parametrize("on", ["list", "details"])
def test_s_refuses_a_host_the_ssh_module_doesnt_write(hosts, ssh, monkeypatch, on):
    """ssh scratch would fall through to DNS: scratch excludes ssh, and work's ssh module is parked."""
    write_config(hosts, MIXED.replace('path = "SSH/20-work.conf"', 'path = "SSH/20-work.conf"\nenabled = false'))
    notes = []
    app = browser()
    monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append((message, kw.get("severity"))))
    keys = ["s"] if on == "list" else ["enter", "s"]

    async def script(pilot):
        for name in ("scratch", "fw", "nas"):
            await find(pilot, name)
            await pilot.press(*keys)
            if on == "details":
                await pilot.press("escape")
            await pilot.press("escape")

    drive(app, script)
    assert notes == [
        ("scratch isn't in the ssh config ari writes (it excludes ssh), so ssh scratch would go wherever DNS says", "warning"),
        ("fw isn't in the ssh config ari writes (work has the ssh module off), so ssh fw would go wherever DNS says", "warning"),
    ]
    assert ssh == [["ssh", "nas"]]


def test_s_and_enter_do_nothing_when_no_host_matches(hosts, monkeypatch):
    monkeypatch.setattr(tui.subprocess, "run", lambda *a, **k: pytest.fail("ssh ran"))

    async def script(pilot):
        await find(pilot, "nothing-matches")
        await pilot.press("enter", "s")
        assert names(app) == [] and app.screen is app.hosts

    app = browser()
    drive(app, script)


@pytest.mark.parametrize("keys", [["q"], ["enter", "q"], ["x", "q"]])
def test_q_quits_from_the_list_a_hosts_details_and_an_export_report(hosts, keys):
    app = drive(browser(), lambda pilot: pilot.press(*keys))
    assert app.return_code == 0 and not app.is_running


def test_q_is_just_a_key_where_something_is_unsaved(hosts):
    async def script(pilot):
        await pilot.press("d", "q")
        assert isinstance(app.screen, tui.Confirm)
        await pilot.press("n", "g", "q")
        assert isinstance(app.screen, tui.GroupPicker)
        await pilot.press("escape", "e", "q")
        assert isinstance(app.screen, tui.HostForm) and app.screen.query_one("#f-name", Input).value == "q"
        app.screen.query_one("#x-ssh", Checkbox).focus()
        await pilot.press("q")
        assert isinstance(app.screen, tui.HostForm)

    app = browser()
    drive(app, script)
    assert app.return_code is None


def work_json(home):
    return home / "config" / "ari" / "work.json"


def shown(screen) -> str:
    """What a screen's text says, as plain text."""
    content = screen.query_one(Static).content
    return getattr(content, "plain", content)


def test_d_asks_first_and_y_removes_the_host(hosts):
    async def script(pilot):
        await pilot.press("down", "down", "down")
        await pilot.press("d")
        assert isinstance(app.screen, tui.Confirm) and shown(app.screen) == "Delete vault-01 from work?"
        await pilot.press("y")
        assert app.screen is app.hosts
        assert names(app) == ["nas", "scratch", "fw", "web-01"]
        assert app.hosts.selected()[1].name == "web-01"

    app = browser()
    drive(app, script)
    assert [h["name"] for h in json.loads(work_json(hosts).read_text())["hosts"]] == ["fw", "web-01"]


@pytest.mark.parametrize("answer", ["n", "escape"])
def test_n_or_escape_leaves_the_inventory_alone(hosts, answer):
    before = work_json(hosts).read_bytes()

    async def script(pilot):
        await find(pilot, "fw")
        await pilot.press("d", answer)
        assert app.screen is app.hosts

    app = browser()
    drive(app, script)
    assert work_json(hosts).read_bytes() == before and len(app.seen["names"]) == 1


def test_d_from_a_hosts_details_deletes_it_and_returns_to_the_list(hosts):
    async def script(pilot):
        await find(pilot, "web")
        await pilot.press("enter", "d")
        assert isinstance(app.screen, tui.Confirm)
        await pilot.press("y")
        assert app.screen is app.hosts and names(app) == []
        await pilot.press("escape")

    app = browser()
    assert drive(app, script).seen["names"] == ["nas", "scratch", "fw", "vault-01"]


def test_the_last_row_deleted_leaves_the_cursor_on_the_new_last_row(hosts):
    async def script(pilot):
        await pilot.press("down", "down", "down", "down", "d", "y")
        assert app.hosts.selected()[1].name == "vault-01"

    app = browser()
    drive(app, script)


def test_a_delete_that_fails_says_why_and_keeps_the_list(hosts, monkeypatch):
    notes = []
    app = browser()
    monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append(message))

    async def script(pilot):
        work_json(hosts).write_text("{not json")
        await pilot.press("d", "y")

    drive(app, script)
    assert notes and all("work.json: not valid JSON" in n for n in notes)
    assert len(app.seen["names"]) == 5


def test_d_does_nothing_when_no_host_matches(hosts):
    async def script(pilot):
        await find(pilot, "nothing-matches")
        await pilot.press("d")
        assert app.screen is app.hosts

    app = browser()
    drive(app, script)


def test_x_exports_and_lists_each_file(hosts):
    async def script(pilot):
        await pilot.press("x")
        assert isinstance(app.screen, tui.Report)
        text = shown(app.screen)
        for line in (
            f"wrote {hosts}/ssh/10-personal.conf (personal, ssh, 1 host)",
            f"wrote {hosts}/ssh/20-work.conf (work, ssh, 3 hosts)",
            f"wrote {hosts}/ssh/ansible/00-hosts.yml (work, ansible, 2 hosts)",
        ):
            assert line in text.splitlines()
        await pilot.press("escape")
        assert app.screen is app.hosts
        await pilot.press("x")
        assert all(line.startswith("unchanged ") for line in shown(app.screen).splitlines())

    app = browser()
    drive(app, script)
    assert (hosts / "ssh" / "10-personal.conf").exists()


def test_x_covers_only_the_inventory_flag_names(hosts):
    async def script(pilot):
        await pilot.press("x")
        assert shown(app.screen) == f"wrote {hosts}/ssh/10-personal.conf (personal, ssh, 1 host)"

    app = browser("personal")
    drive(app, script)
    assert sorted(p.name for p in (hosts / "ssh").iterdir()) == ["10-personal.conf"]


def test_x_lists_every_problem_and_writes_nothing(hosts):
    data = json.loads(work_json(hosts).read_text())
    data["hosts"] += [
        {"name": "stray", "hostname": "192.0.2.200"},
        {"name": "typo", "hostname": "192.0.2.201", "groups": ["zone_app"], "reasons": {"zone_app": "nope"}},
    ]
    work_json(hosts).write_text(json.dumps(data))

    async def script(pilot):
        await pilot.press("x")
        lines = shown(app.screen).splitlines()
        assert lines[0] == "export stopped, nothing written"
        assert "  work/ansible: stray is in no zone" in lines
        assert any("typo" in line and "nope" in line for line in lines[1:])

    app = browser()
    drive(app, script)
    assert list((hosts / "ssh").iterdir()) == []


def test_x_shows_a_guard_refusal_and_its_hint(hosts):
    core.export(load_config())
    edited = hosts / "ssh" / "20-work.conf"
    edited.write_text(edited.read_text() + "# hand edit\n")
    before = {p: p.read_bytes() for p in (hosts / "ssh").rglob("*") if p.is_file()}

    async def script(pilot):
        await pilot.press("x")
        assert shown(app.screen).splitlines() == [
            "export stopped, nothing written",
            f"  {edited}: edited since the last export",
            "review the file, then rerun with --force",
        ]

    app = browser()
    drive(app, script)
    assert {p: p.read_bytes() for p in (hosts / "ssh").rglob("*") if p.is_file()} == before


def stored(home, inventory="work") -> dict:
    return {h["name"]: h for h in json.loads((home / "config" / "ari" / f"{inventory}.json").read_text())["hosts"]}


def fill(form, **values):
    """Set the form's inputs as if typed: name, hostname, user, port, keys, notes, aliases, options."""
    for field, value in values.items():
        if field == "options":
            form.query_one("#f-options", TextArea).text = value
        else:
            form.query_one(f"#f-{field}", Input).value = value


def errors(form) -> dict[str, str]:
    """What each error line under the form's inputs says, for the ones showing."""
    return {e.id.removeprefix("error-"): shown_text(e) for e in form.query(".error") if e.display}


def shown_text(static) -> str:
    content = static.content
    return getattr(content, "plain", str(content))


async def edit(pilot, name):
    await find(pilot, name)
    await pilot.press("e")
    assert isinstance(pilot.app.screen, tui.HostForm)
    return pilot.app.screen


def test_a_adds_a_host_by_typing_and_lands_on_it(hosts):
    async def script(pilot):
        await pilot.press("a")
        form = app.screen
        assert isinstance(form, tui.HostForm) and form.inventory == "personal"
        await pilot.press(*"db-01", "tab", *"192.0.2.60", "ctrl+s")
        assert app.screen is app.hosts and app.hosts.selected()[1].name == "db-01"

    app = browser()
    drive(app, script)
    assert stored(hosts, "personal")["db-01"]["hostname"] == "192.0.2.60"


def test_a_puts_the_host_where_the_picker_says_with_everything_the_form_holds(hosts):
    async def script(pilot):
        await pilot.press("a")
        form = app.screen
        form.query_one("#f-inventory", Select).value = "work"
        await pilot.pause()
        assert form.query_one("#f-user", Input).placeholder == "deploy (default)"
        fill(form, name="db-02", hostname="192.0.2.61", port="2200", aliases="db2 192.0.2.61",
             notes="primary", options="ServerAliveInterval 30\nCompression=yes")
        form.query_one("#x-ansible", Checkbox).value = True
        await pilot.press("ctrl+s")
        assert app.screen is app.hosts

    app = browser()
    drive(app, script)
    host = stored(hosts)["db-02"]
    assert host["port"] == 2200 and host["aliases"] == ["db2", "192.0.2.61"] and host["notes"] == "primary"
    assert host["modules"]["ssh"]["options"] == {"ServerAliveInterval": "30", "Compression": "yes"}
    assert host["exclude"] == ["ansible"] and "user" not in host


def test_every_problem_shows_beside_its_input_and_nothing_is_saved(hosts):
    before = work_json(hosts).read_bytes()

    async def script(pilot):
        await pilot.press("a")
        form = app.screen
        form.query_one("#f-inventory", Select).value = "work"
        fill(form, name="a b", hostname="x y", port="0", aliases="c* storage", options="HostName elsewhere")
        await pilot.press("ctrl+s")
        assert app.screen is form
        shown = errors(form)
        assert set(shown) == {"name", "hostname", "port", "aliases", "options"}
        assert "name 'a b' is not a valid ssh host name" in shown["name"]
        assert "port must be an integer 1-65535, got 0" in shown["port"]
        assert "alias 'c*'" in shown["aliases"] and "'storage' is already used by personal/nas" in shown["aliases"]
        assert "HostName can't be an option" in shown["options"]
        assert app.focused is form.query_one("#f-name", Input)
        fill(form, name="ok-01", hostname="192.0.2.62", port="", aliases="", options="")
        await pilot.press("ctrl+s")
        assert app.screen is app.hosts

    app = browser()
    drive(app, script)
    assert work_json(hosts).read_bytes() != before and "ok-01" in stored(hosts)


def test_a_problem_with_no_input_of_its_own_shows_at_the_top(hosts):
    data = json.loads(work_json(hosts).read_text())
    data["hosts"][0]["groups"] = ["zone_typo"]
    work_json(hosts).write_text(json.dumps(data))
    before = work_json(hosts).read_bytes()

    async def script(pilot):
        form = await edit(pilot, "fw")
        fill(form, notes="edge")
        await pilot.press("ctrl+s")
        assert list(errors(form)) == ["general"] and "group 'zone_typo' isn't declared" in errors(form)["general"]

    drive(browser(), script)
    assert work_json(hosts).read_bytes() == before


def test_e_shows_the_hosts_own_values_and_the_defaults_it_inherits(hosts):
    async def script(pilot):
        form = await edit(pilot, "vault")
        assert form.query_one("#f-user", Input).value == "" and form.query_one("#f-port", Input).value == ""
        assert form.query_one("#f-user", Input).placeholder == "deploy (default)"
        assert form.query_one("#f-port", Input).placeholder == "2222 (default)"
        assert form.query_one("#f-keys", Input).placeholder == "lab-ed25519 (default)"
        await pilot.press("escape", "escape")
        form = await edit(pilot, "web-01")
        assert form.query_one("#f-user", Input).value == "admin" and form.query_one("#f-port", Input).value == "22"
        await pilot.press("escape", "escape")
        form = await edit(pilot, "fw")
        assert form.query_one("#x-ansible", Checkbox).value and not form.query_one("#x-ssh", Checkbox).value

    drive(browser(), script)


def test_clearing_a_field_goes_back_to_the_default(hosts):
    async def script(pilot):
        form = await edit(pilot, "web-01")
        fill(form, user="", port="")
        await pilot.press("ctrl+s")

    drive(browser(), script)
    host = stored(hosts)["web-01"]
    assert "user" not in host and "port" not in host


def test_e_renames_swaps_aliases_and_lands_on_the_new_name(hosts):
    async def script(pilot):
        form = await edit(pilot, "nas")
        fill(form, name="nas2", aliases="STORE")
        await pilot.press("ctrl+s")
        await pilot.press("escape")
        assert app.hosts.selected()[1].name == "nas2"

    app = browser()
    drive(app, script)
    personal = stored(hosts, "personal")
    assert "nas" not in personal and personal["nas2"]["aliases"] == ["STORE"]


def test_e_changes_options_and_exclude_like_edit_does(hosts):
    async def script(pilot):
        form = await edit(pilot, "fw")
        fill(form, options="ServerAliveInterval 30\nCompression yes")
        form.query_one("#x-ansible", Checkbox).value = False
        await pilot.press("ctrl+s")
        form = await edit(pilot, "fw")
        assert form.query_one("#f-options", TextArea).text == "ServerAliveInterval 30\nCompression yes"
        fill(form, options="compression=no")
        await pilot.press("ctrl+s")

    drive(browser(), script)
    host = stored(hosts)["fw"]
    assert "exclude" not in host and host["modules"]["ssh"]["options"] == {"Compression": "no"}


def test_the_form_reaches_the_same_record_as_ari_edit(hosts):
    """The same change through the form and through the CLI leaves the same JSON, last_updated aside."""
    async def script(pilot):
        form = await edit(pilot, "vault-01")
        fill(form, name="vault-1", user="ops", aliases="v1", notes="kept", options="Compression yes")
        form.query_one("#x-ssh", Checkbox).value = True
        await pilot.press("ctrl+s")

    drive(browser(), script)
    by_form = stored(hosts)["vault-1"]
    write_mixed(hosts)
    assert main(["edit", "vault-01", "--rename", "vault-1", "--user", "ops", "--alias", "v1", "--notes", "kept",
                 "--opt", "Compression=yes", "--exclude", "ssh"]) == 0
    by_cli = stored(hosts)["vault-1"]
    assert {k: v for k, v in by_form.items() if k != "last_updated"} == {k: v for k, v in by_cli.items() if k != "last_updated"}


@pytest.mark.parametrize("keys", [["escape"], ["ctrl+s"]])
def test_escape_or_saving_nothing_new_writes_nothing(hosts, keys):
    before = work_json(hosts).read_bytes()

    async def script(pilot):
        await edit(pilot, "vault")
        await pilot.press(*keys)
        assert app.screen is app.hosts and app.hosts.selected()[1].name == "vault-01"

    app = browser()
    drive(app, script)
    assert work_json(hosts).read_bytes() == before


def test_e_from_details_and_a_hidden_new_host_is_reported(hosts, monkeypatch):
    notes = []
    app = browser()
    monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append(message))

    async def script(pilot):
        await find(pilot, "fw")
        await pilot.press("enter", "e")
        assert isinstance(app.screen, tui.HostForm) and app.screen.host.name == "fw"
        await pilot.press("escape", "a")
        fill(app.screen, name="db-03", hostname="192.0.2.63")
        await pilot.press("ctrl+s")

    drive(app, script)
    assert notes == ["added db-03 to personal", "the filter hides db-03"]


@pytest.mark.parametrize(
    "text, pairs",
    [
        ("Compression yes", [("Compression", "yes")]),
        ("Compression=yes", [("Compression", "yes")]),
        ("  Compression = yes  ", [("Compression", "yes")]),
        ("SetEnv FOO=bar BAZ=1", [("SetEnv", "FOO=bar BAZ=1")]),
        ("Compression", [("Compression", "")]),
        ("a 1\n\n  \nb 2", [("a", "1"), ("b", "2")]),
    ],
)
def test_options_read_the_way_ssh_config_does(text, pairs):
    assert tui.parse_options(text) == pairs


def add_reason(home, key, text):
    data = json.loads(work_json(home).read_text())
    data["groups"]["no_auto_update"]["reasons"][key] = text
    work_json(home).write_text(json.dumps(data))


async def groups(pilot, name):
    await find(pilot, name)
    await pilot.press("g")
    picker = pilot.app.screen
    assert isinstance(picker, tui.GroupPicker)
    return picker


async def highlight(pilot, picker, group):
    """Move the list's highlight onto group with the arrow keys."""
    options = picker.query_one(SelectionList)
    options.focus()
    for _ in range(options.option_count):
        if options.get_option_at_index(options.highlighted).value == group:
            return
        await pilot.press("down")
    pytest.fail(f"{group} isn't in the picker")


def prompt(picker, group) -> str:
    return picker.query_one(SelectionList).get_option(group).prompt.plain


def test_g_lists_declared_groups_checked_where_the_host_is_a_member(hosts):
    async def script(pilot):
        picker = await groups(pilot, "vault")
        options = picker.query_one(SelectionList)
        assert [options.get_option_at_index(i).value for i in range(options.option_count)] == ["zone_app", "no_auto_update"]
        assert sorted(options.selected) == ["no_auto_update", "zone_app"]
        assert prompt(picker, "zone_app") == "zone_app  app subnet (192.0.2.0/25)"
        assert prompt(picker, "no_auto_update") == "no_auto_update (secrets)"

    drive(browser(), script)


def test_space_checks_a_group_and_ctrl_s_saves_it(hosts):
    async def script(pilot):
        picker = await groups(pilot, "fw")
        await highlight(pilot, picker, "zone_app")
        assert not picker.query_one("#reason-row").display
        await pilot.press("space", "ctrl+s")
        assert app.screen is app.hosts and app.hosts.selected()[1].name == "fw"

    app = browser()
    assert drive(app, script).seen["cells"]["fw"]["GROUPS"] == "zone_app"
    assert stored(hosts)["fw"]["groups"] == ["zone_app"]


def test_unchecking_a_group_drops_it_and_its_reason(hosts):
    async def script(pilot):
        picker = await groups(pilot, "vault")
        await highlight(pilot, picker, "no_auto_update")
        assert picker.query_one("#reason-row").display
        await pilot.press("space")
        assert not picker.query_one("#reason-row").display and prompt(picker, "no_auto_update") == "no_auto_update"
        await pilot.press("ctrl+s")

    drive(browser(), script)
    host = stored(hosts)["vault-01"]
    assert host["groups"] == ["zone_app"] and "reasons" not in host


def test_a_reason_is_set_changed_and_cleared(hosts):
    add_reason(hosts, "remote", "remote access path")

    async def pick(pilot, name, reason, check=False):
        picker = await groups(pilot, name)
        await highlight(pilot, picker, "no_auto_update")
        if check:
            await pilot.press("space")
        row, select = picker.query_one("#reason-row"), picker.query_one("#reason", Select)
        assert row.display
        select.value = reason
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.press("escape")

    async def script(pilot):
        await pick(pilot, "web-01", "remote", check=True)
        assert stored(hosts)["web-01"]["reasons"] == {"no_auto_update": "remote"}
        await pick(pilot, "web-01", "secrets")
        assert stored(hosts)["web-01"]["reasons"] == {"no_auto_update": "secrets"}
        await pick(pilot, "web-01", tui.NO_REASON)
        host = stored(hosts)["web-01"]
        assert "no_auto_update" in host["groups"] and "reasons" not in host

    drive(browser(), script)


def test_the_reason_picker_follows_the_highlight(hosts):
    add_reason(hosts, "remote", "remote access path")

    async def script(pilot):
        picker = await groups(pilot, "vault")
        select = picker.query_one("#reason", Select)
        await highlight(pilot, picker, "no_auto_update")
        assert select.value == "secrets"
        select.value = "remote"
        await pilot.pause()
        assert prompt(picker, "no_auto_update") == "no_auto_update (remote)"
        await highlight(pilot, picker, "zone_app")
        assert not picker.query_one("#reason-row").display
        await highlight(pilot, picker, "no_auto_update")
        assert select.value == "remote"
        await pilot.press("ctrl+s")

    drive(browser(), script)
    assert stored(hosts)["vault-01"]["reasons"] == {"no_auto_update": "remote"}


@pytest.mark.parametrize("keys", [["escape"], ["ctrl+s"]])
def test_escape_or_an_unchanged_save_writes_nothing(hosts, keys):
    before = work_json(hosts).read_bytes()

    async def script(pilot):
        picker = await groups(pilot, "vault")
        await highlight(pilot, picker, "no_auto_update")
        await pilot.press(*keys)
        assert app.screen is app.hosts

    app = browser()
    drive(app, script)
    assert work_json(hosts).read_bytes() == before


def test_a_stale_membership_shows_and_its_refusal_names_it(hosts):
    data = json.loads(work_json(hosts).read_text())
    data["hosts"][0]["groups"] = ["zone_typo"]
    work_json(hosts).write_text(json.dumps(data))
    before = work_json(hosts).read_bytes()

    async def script(pilot):
        picker = await groups(pilot, "fw")
        assert picker.query_one(SelectionList).selected == ["zone_typo"]
        assert prompt(picker, "zone_typo") == "zone_typo  not declared"
        await highlight(pilot, picker, "zone_app")
        await pilot.press("space", "ctrl+s")
        error = picker.query_one("#error-general", Static)
        assert error.display and "group 'zone_typo' isn't declared" in shown_text(error)
        assert work_json(hosts).read_bytes() == before
        await highlight(pilot, picker, "zone_typo")
        await pilot.press("space", "ctrl+s")
        assert app.screen is app.hosts

    app = browser()
    drive(app, script)
    assert stored(hosts)["fw"]["groups"] == ["zone_app"]


def test_the_picker_reaches_the_same_record_as_ari_edit(hosts):
    async def script(pilot):
        picker = await groups(pilot, "web-01")
        await highlight(pilot, picker, "zone_app")
        await pilot.press("space")
        await highlight(pilot, picker, "no_auto_update")
        await pilot.press("space")
        picker.query_one("#reason", Select).value = "secrets"
        await pilot.pause()
        await pilot.press("ctrl+s")

    drive(browser(), script)
    by_picker = stored(hosts)["web-01"]
    write_mixed(hosts)
    assert main(["edit", "web-01", "--ungroup", "zone_app", "--group", "no_auto_update:secrets"]) == 0
    by_cli = stored(hosts)["web-01"]
    assert {k: v for k, v in by_picker.items() if k != "last_updated"} == {k: v for k, v in by_cli.items() if k != "last_updated"}


def test_g_from_details_and_an_inventory_without_groups(hosts):
    async def script(pilot):
        await find(pilot, "fw")
        await pilot.press("enter", "g")
        assert isinstance(app.screen, tui.GroupPicker) and app.screen.host.name == "fw"
        await pilot.press("escape", "escape")
        picker = await groups(pilot, "nas")
        assert "personal declares no groups" in shown_text(picker.query_one("#empty", Static))
        await pilot.press("ctrl+s")
        assert app.screen is app.hosts

    app = browser()
    drive(app, script)


# The switch in ari's main(): a terminal opens the TUI, anything else gets help.


@pytest.fixture
def terminal(monkeypatch):
    monkeypatch.setattr(cli, "_terminal", lambda: True)


def test_no_arguments_on_a_terminal_opens_the_tui(hosts, terminal, monkeypatch):
    opened = []
    monkeypatch.setattr(tui, "run", lambda cfg, scope: opened.append(scope) or 0)
    assert main([]) == 0
    assert main(["-i", "work"]) == 0
    assert opened == [None, "work"]


@pytest.mark.parametrize(
    "argv, setup, message",
    [
        (["-i", "nope"], write_mixed, "no inventory 'nope'"),
        ([], lambda home: None, "no config at"),
    ],
)
def test_the_tui_errors_like_any_command_before_the_screen_changes(home, terminal, monkeypatch, capsys, argv, setup, message):
    setup(home)
    monkeypatch.setattr(tui.Browser, "run", lambda self: pytest.fail("the TUI opened"))
    assert main(argv) == 1
    assert message in capsys.readouterr().err


@pytest.mark.parametrize("stdin, stdout, terminal", [(True, True, True), (True, False, False), (False, True, False)])
def test_a_terminal_means_input_and_output_both(monkeypatch, stdin, stdout, terminal):
    monkeypatch.setattr(cli.sys, "stdin", type("In", (), {"isatty": lambda self: stdin})())
    monkeypatch.setattr(cli.sys, "stdout", type("Out", (), {"isatty": lambda self: stdout})())
    assert cli._terminal() is terminal


def test_no_arguments_without_a_terminal_prints_help(hosts, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_terminal", lambda: False)
    monkeypatch.setattr(tui, "run", lambda cfg, scope: pytest.fail("the TUI opened"))
    assert main([]) == 0
    assert "usage: ari" in capsys.readouterr().out


def test_the_form_shows_key_names_and_each_line_of_a_listed_option(hosts):
    data = json.loads(work_json(hosts).read_text())
    for host in data["hosts"]:
        if host["name"] == "web-01":
            host["ssh_key"] = "~/.ssh/web"
            host["modules"] = {"ssh": {"options": {"LocalForward": ["5432 localhost:5432", "8080 localhost:80"]}}}
    work_json(hosts).write_text(json.dumps(data))

    async def script(pilot):
        form = await edit(pilot, "web-01")
        assert form.query_one("#f-keys", Input).value == "web"
        assert form.query_one("#f-options", TextArea).text == "LocalForward 5432 localhost:5432\nLocalForward 8080 localhost:80"
        fill(form, notes="db tunnel")
        await pilot.press("ctrl+s")
        assert app.screen is app.hosts

    app = browser()
    drive(app, script)
    web = stored(hosts)["web-01"]
    assert web["notes"] == "db tunnel" and web["keys"] == ["web"]
    assert web["modules"]["ssh"]["options"] == {"LocalForward": ["5432 localhost:5432", "8080 localhost:80"]}


def test_the_keys_field_sets_the_list_in_order_and_shows_problems_under_it(hosts):
    for name in ("a", "b"):
        main(["key", name, "--path", f"~/.ssh/{name}", "-i", "work"])

    async def script(pilot):
        form = await edit(pilot, "web-01")
        assert form.query_one("#f-keys", Input).value == ""
        fill(form, keys="b nope")
        await pilot.press("ctrl+s")
        assert app.screen is form and list(errors(form)) == ["keys"]
        assert "key 'nope' isn't declared" in errors(form)["keys"]
        fill(form, keys="b a")
        await pilot.press("ctrl+s")
        assert app.screen is app.hosts
        form = await edit(pilot, "web-01")
        assert form.query_one("#f-keys", Input).value == "b a"
        fill(form, keys="")
        await pilot.press("ctrl+s")

    app = browser()
    drive(app, script)
    assert "keys" not in stored(hosts)["web-01"]


def test_details_list_each_key_with_its_file(hosts):
    main(["key", "a", "--path", "~/.ssh/a", "-i", "work"])
    main(["edit", "web-01", "--key", "a", "--key", "lab-ed25519"])

    async def script(pilot):
        await find(pilot, "web-01")
        await pilot.press("enter")
        console = Console(width=100, record=True)
        console.print(app.screen.query_one(Static).content)
        text = "\n".join(line.rstrip() for line in console.export_text().splitlines())
        assert "keys       a (~/.ssh/a)\n           lab-ed25519 (~/.ssh/lab-ed25519)\n" in text

    app = browser()
    drive(app, script)
