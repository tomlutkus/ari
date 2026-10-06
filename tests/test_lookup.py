"""A token typed on the command line names one host or none: a token that more than one host answers
to is refused, naming each. The TUI acts on the record its row shows, never on a token."""

import json

import pytest
from textual.widgets import SelectionList, Static

from conftest import PERSONAL_ONLY, write_config
from test_tui import browser, drive, errors, fill, names, shown_text
from ari import tui
from ari.cli import main


def run(*argv):
    return main(list(argv))


def path(home, inventory="personal"):
    return home / "config" / "ari" / f"{inventory}.json"


def stored(home, inventory="personal") -> dict:
    return {h["name"]: h for h in json.loads(path(home, inventory).read_text())["hosts"]}


def write(home, inventory, hosts, groups=None):
    """An inventory as a hand edit, or a colleague's commit to a shared file, can leave it."""
    path(home, inventory).write_text(json.dumps({"version": 3, "groups": groups or {}, "hosts": hosts}))


CLASH = [
    {"name": "app", "hostname": "192.0.2.100", "aliases": ["db"]},
    {"name": "db", "hostname": "192.0.2.101"},
]


@pytest.fixture
def clash(home):
    write_config(home, PERSONAL_ONLY)
    write(home, "personal", CLASH, {"g": {}})
    return home


@pytest.mark.parametrize("argv", [["rm", "db"], ["show", "db"], ["edit", "db", "--user", "ops"], ["edit", "DB", "--user", "ops"]])
def test_a_token_two_hosts_answer_to_is_refused_naming_both(clash, capsys, argv):
    before = path(clash).read_bytes()
    assert run(*argv) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert f"error: {argv[1]!r} names more than one host: personal/app (alias), personal/db (name);" in err
    assert "use a name or alias only one of them has" in err and "-i" not in err
    assert path(clash).read_bytes() == before


def test_a_name_only_one_host_has_still_finds_it(clash, capsys):
    """The way out of the clash: app is a name only app has."""
    assert run("edit", "app", "--unalias", "db") == 0
    assert run("rm", "db") == 0
    assert list(stored(clash)) == ["app"] and "aliases" not in stored(clash)["app"]


def test_a_clash_across_inventories_is_refused_and_i_picks_one(home, capsys):
    shared = home / "shared" / "work.json"
    write_config(home, PERSONAL_ONLY + f'\n[inventories.work]\nfile = "{shared}"\n[inventories.work.ssh]\npath = "SSH/20-work.conf"\n')
    assert run("add", "app", "192.0.2.100", "--alias", "db") == 0
    shared.parent.mkdir()
    shared.write_text(json.dumps({"version": 3, "hosts": [{"name": "db", "hostname": "192.0.2.101"}]}))
    capsys.readouterr()
    assert run("rm", "db") == 1
    err = capsys.readouterr().err
    assert "'db' names more than one host: personal/app (alias), work/db (name);" in err
    assert "use a name or alias only one of them has, or -i to say which inventory" in err
    assert list(stored(home)) == ["app"]
    assert run("rm", "-i", "work", "db") == 0
    assert json.loads(shared.read_text())["hosts"] == [] and list(stored(home)) == ["app"]


def test_one_host_answering_twice_is_one_host(home, capsys):
    write_config(home, PERSONAL_ONLY)
    write(home, "personal", [{"name": "db", "hostname": "192.0.2.101", "aliases": ["DB"]}])
    assert run("show", "db") == 0
    assert "192.0.2.101" in capsys.readouterr().out


def test_no_host_still_says_so(clash, capsys):
    assert run("rm", "nope") == 1
    assert "no host named 'nope' in any inventory" in capsys.readouterr().err


# The TUI holds the record; it never looks it up again by a token


async def on_db(pilot):
    await pilot.pause()
    await pilot.press("down")
    assert pilot.app.hosts.selected()[1].name == "db"


def test_d_on_a_row_removes_that_record(clash):
    async def script(pilot):
        await on_db(pilot)
        await pilot.press("d", "y")
        assert names(app) == ["app"]

    app = browser()
    drive(app, script)
    assert list(stored(clash)) == ["app"]


def test_e_on_a_row_saves_that_record(clash):
    """Saving either host is refused while the clash stands; what the form says is about its own row."""
    before = path(clash).read_bytes()

    async def script(pilot):
        await on_db(pilot)
        await pilot.press("e")
        form = app.screen
        assert isinstance(form, tui.HostForm)
        fill(form, user="ops")
        await pilot.press("ctrl+s")
        assert app.screen is form
        assert errors(form) == {"name": "personal (db): 'db' is already used by personal/app"}
        await pilot.press("escape")
        await pilot.press("up", "e")
        fill(app.screen, aliases="")
        await pilot.press("ctrl+s")
        assert app.screen is app.hosts

    app = browser()
    drive(app, script)
    assert path(clash).read_bytes() != before
    assert "aliases" not in stored(clash)["app"] and "user" not in stored(clash)["db"]


def test_g_on_a_row_opens_and_saves_that_record(clash):
    before = path(clash).read_bytes()

    async def script(pilot):
        await on_db(pilot)
        await pilot.press("g")
        picker = app.screen
        assert isinstance(picker, tui.GroupPicker) and picker.host.name == "db"
        picker.query_one(SelectionList).focus()
        await pilot.press("space", "ctrl+s")
        error = picker.query_one("#error-general", Static)
        assert error.display and shown_text(error) == "personal (db): 'db' is already used by personal/app"

    app = browser()
    drive(app, script)
    assert path(clash).read_bytes() == before
