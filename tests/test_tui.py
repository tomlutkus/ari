import asyncio
import contextlib
import json
import subprocess

import pytest
from rich.console import Console
from textual.widgets import DataTable, Input, Static

from conftest import write_mixed
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


def test_s_and_enter_do_nothing_when_no_host_matches(hosts, monkeypatch):
    monkeypatch.setattr(tui.subprocess, "run", lambda *a, **k: pytest.fail("ssh ran"))

    async def script(pilot):
        await find(pilot, "nothing-matches")
        await pilot.press("enter", "s")
        assert names(app) == [] and app.screen is app.hosts

    app = browser()
    drive(app, script)


def test_q_quits(hosts):
    app = drive(browser(), lambda pilot: pilot.press("q"))
    assert app.return_code == 0


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
