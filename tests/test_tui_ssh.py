"""s hands the terminal to ssh only when ssh NAME reaches the host the record describes: the file ssh
reads holds its block as export writes it now, and the name is one ssh takes as itself on its command
line. Ctrl+Q and Ctrl+C follow q: they never throw away a form, a picker or a prompt."""

import contextlib
import json
import shutil
import subprocess

import pytest
from textual.widgets import Input

from conftest import PERSONAL_ONLY, write_config, write_mixed
from test_tui import Calls, browser, drive, find, names
from ari import core, tui
from ari.cli import main
from ari.config import load_config
from ari.modules.ssh import SshModule, _quote, command_line_problem, render
from ari.paths import tilde

SSH = shutil.which("ssh")


def run(*argv):
    return main(list(argv))


@pytest.fixture
def hosts(home):
    write_mixed(home)
    return home


@pytest.fixture
def ssh(monkeypatch):
    calls = Calls()

    def run_ssh(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, calls.code)

    monkeypatch.setattr(tui.subprocess, "run", run_ssh)
    monkeypatch.setattr(tui.Browser, "suspend", lambda self: contextlib.nullcontext())
    return calls


def noted(app, monkeypatch) -> list:
    notes = []
    monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append((message, kw.get("severity"))))
    return notes


# H10: the file ssh reads holds the host as export writes it now


def test_s_refuses_a_host_edited_since_the_last_export_until_x(hosts, ssh, monkeypatch):
    """H10: ssh nas went to the address the last export wrote, not the one the inventory held."""
    core.export(load_config())
    assert run("edit", "nas", "--hostname", "203.0.113.99") == 0
    app = browser()
    notes = noted(app, monkeypatch)

    async def script(pilot):
        await find(pilot, "nas")
        await pilot.press("s")
        assert ssh == []
        await pilot.press("x", "escape", "s")

    drive(app, script)
    assert notes[0] == (f"ssh nas wouldn't reach it as the inventory says: {tilde(hosts / 'ssh' / '10-personal.conf')}"
                        " doesn't hold nas as the inventory has it; export first", "warning")
    assert ssh == [["ssh", "--", "nas"]]


def test_s_refuses_a_host_never_exported(hosts, ssh, monkeypatch):
    app = browser()
    notes = noted(app, monkeypatch)
    drive(app, lambda pilot: pilot.press("s"))
    assert ssh == [] and notes == [(f"ssh nas wouldn't reach it as the inventory says: {tilde(hosts / 'ssh' / '10-personal.conf')}"
                                    " isn't there to read; export first", "warning")]


def test_another_hosts_change_doesnt_hold_s_back(hosts, ssh):
    core.export(load_config())
    assert run("edit", "fw", "--hostname", "203.0.113.2") == 0
    drive(browser(), lambda pilot: pilot.press("s"))
    assert ssh == [["ssh", "--", "nas"]]


def test_holds_finds_the_block_as_export_writes_it(hosts):
    core.export(load_config())
    inventories = core.load_all(load_config())
    work = inventories["work"]
    data = (hosts / "ssh" / "20-work.conf").read_bytes()
    module = SshModule()
    settings = load_config().get("work").modules["ssh"].settings
    assert all(module.holds(work, h, settings, data) for h in work.hosts if "ssh" not in h.exclude)
    fw = next(h for h in work.hosts if h.name == "fw")
    fw.notes = "a note is part of the block"
    assert module.holds(work, fw, settings, data) is False
    fw.notes = ""
    fw.aliases = ["fw2"]
    assert module.holds(work, fw, settings, data) is False


def test_holds_never_takes_a_block_that_only_starts_the_same(hosts):
    inventories = core.load_all(load_config())
    personal = inventories["personal"]
    nas = personal.hosts[0]
    longer = render(personal, [nas]).replace("    HostName 192.0.2.254\n", "    HostName 192.0.2.254\n    Port 2200\n")
    assert SshModule().holds(personal, nas, None, longer.encode()) is False
    assert SshModule().holds(personal, nas, None, render(personal, [nas]).encode()) is True


# G4: a name ssh takes as itself on its command line

REFUSED = ["-V", "-W192.0.2.50:22", "a$b", "a;b", "a&b", "a|b", "a(b", "a)b", "a{b", "a}b", "a<b", "a>b", "a`b", "a\\b",
           "a\x01b", "a\x7fb", "ops@db", "ssh://x"]
KEPT = ["web", "web-01", "a%b", "a]b", "a:b", "a+b", "a~b", "a/b", "a.b", "_x"]


@pytest.mark.parametrize("token", REFUSED)
def test_a_name_ssh_takes_as_something_else_is_refused(token):
    assert command_line_problem(token) is not None


@pytest.mark.parametrize("token", KEPT)
def test_a_name_ssh_takes_as_itself_is_kept(token):
    assert command_line_problem(token) is None


@pytest.mark.skipif(SSH is None, reason="needs the ssh client")
@pytest.mark.parametrize("token", REFUSED + KEPT)
def test_ssh_takes_each_name_as_the_rule_says(tmp_path, token):
    """The judge is ssh -G on the name after --, as s runs it: does it reach the block?"""
    config = tmp_path / "c"
    config.write_text(f"Host {_quote(token)}\n    HostName 192.0.2.99\n")
    resolved = subprocess.run([SSH, "-G", "-F", str(config), "--", token], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    reached = resolved.returncode == 0 and "hostname 192.0.2.99" in resolved.stdout.splitlines()
    assert reached == (command_line_problem(token) is None), resolved.stderr


@pytest.mark.parametrize("argv", [["add", "--", "-V", "192.0.2.10"], ["add", "a$b", "192.0.2.10"], ["add", "ops@db", "192.0.2.10"],
                                  ["add", "web", "192.0.2.10", "--alias", "ssh://web"]])
def test_add_refuses_such_a_name_or_alias(home, capsys, argv):
    write_config(home, PERSONAL_ONLY)
    assert run(*argv) == 1
    assert "can't be typed: ssh" in capsys.readouterr().err
    assert not (home / "config" / "ari" / "personal.json").exists()


def test_the_refusal_names_the_field(home):
    write_config(home, PERSONAL_ONLY)
    with pytest.raises(core.HostRefused) as refused:
        core.add_host(load_config(), "personal", "ops@db", "192.0.2.10", core.Changes(aliases=["a|b"]))
    assert [p.field for p in refused.value.problems] == ["name", "aliases"]


def test_a_host_ssh_never_writes_keeps_such_a_name(home):
    write_config(home, PERSONAL_ONLY)
    assert run("add", "ops@db", "192.0.2.10", "--exclude", "ssh") == 0


def test_import_and_export_refuse_such_a_name(home, tmp_path, capsys):
    write_config(home, PERSONAL_ONLY)
    source = tmp_path / "src.conf"
    source.write_text("Host ops@db\n    HostName 192.0.2.10\n\nHost good\n    HostName 192.0.2.11\n")
    assert run("import", "ssh", str(source)) == 1
    assert "name 'ops@db' can't be typed: ssh reads it on its command line as user ops at host db" in capsys.readouterr().err
    path = home / "config" / "ari" / "personal.json"
    data = json.loads(path.read_text())
    data["hosts"].append({"name": "-V", "hostname": "192.0.2.12"})
    path.write_text(json.dumps(data))
    assert run("export") == 1
    assert "personal (-V): name '-V' can't be typed: ssh refuses a host name starting with - on its command line" in capsys.readouterr().err


def test_s_refuses_such_a_name_stored_before_and_never_runs_ssh(home, ssh, monkeypatch):
    write_config(home, PERSONAL_ONLY)
    (home / "config" / "ari" / "personal.json").write_text(json.dumps({"version": 3, "hosts": [{"name": "-V", "hostname": "192.0.2.12"}]}))
    app = browser()
    notes = noted(app, monkeypatch)
    drive(app, lambda pilot: pilot.press("s"))
    assert ssh == []
    assert notes == [("ssh -V wouldn't reach it as the inventory says: name '-V' can't be typed: ssh refuses a host name"
                      " starting with - on its command line", "warning")]


# ssh missing: the suspend block ends normally, and the app comes back


def test_ssh_not_installed_is_said_and_the_app_resumes(hosts, monkeypatch):
    core.export(load_config())
    ended = []

    @contextlib.contextmanager
    def suspend(self):
        yield
        ended.append("normally")  # only reached when nothing raised out of the block

    def missing(argv, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "ssh")

    monkeypatch.setattr(tui.Browser, "suspend", suspend)
    monkeypatch.setattr(tui.subprocess, "run", missing)
    app = browser()
    notes = noted(app, monkeypatch)
    drive(app, lambda pilot: pilot.press("s"))
    assert ended == ["normally"] and notes == [("ssh isn't installed", "error")]
    assert app.seen["names"] == ["nas", "scratch", "fw", "vault-01", "web-01"]


# L6: Ctrl+Q and Ctrl+C where q is just a key


@pytest.mark.parametrize("key", ["ctrl+q", "ctrl+c"])
def test_ctrl_q_and_ctrl_c_never_throw_away_the_form(hosts, monkeypatch, key):
    """L6: Ctrl+C pointed at Ctrl+Q, and Ctrl+Q quit with the form half filled."""
    app = browser()
    notes = noted(app, monkeypatch)

    async def script(pilot):
        await pilot.press("a", *"newhost")
        await pilot.press(key)
        assert app.is_running and isinstance(app.screen, tui.HostForm)
        assert app.screen.query_one("#f-name", Input).value == "newhost"

    drive(app, script)
    assert notes == [("Escape leaves the form without saving, Ctrl+S saves", None)]


@pytest.mark.parametrize("keys, screen, leave", [
    (["g"], tui.GroupPicker, "Escape leaves the picker without saving, Ctrl+S saves"),
    (["d"], tui.Confirm, "y answers yes; n or Escape answers no"),
])
def test_ctrl_q_says_how_to_leave_the_picker_and_the_prompt(hosts, monkeypatch, keys, screen, leave):
    app = browser()
    notes = noted(app, monkeypatch)

    async def script(pilot):
        await pilot.press(*keys, "ctrl+q")
        assert app.is_running and isinstance(app.screen, screen)

    drive(app, script)
    assert notes == [(leave, None)]


@pytest.mark.parametrize("keys", [[], ["enter"], ["x"], ["k"]])
def test_ctrl_q_quits_where_q_does(hosts, keys):
    app = drive(browser(), lambda pilot: pilot.press(*keys, "ctrl+q"))
    assert app.return_code == 0 and not app.is_running


def test_ctrl_c_on_the_list_still_says_how_to_quit(hosts, monkeypatch):
    app = browser()
    notes = []
    monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append(message))
    drive(app, lambda pilot: pilot.press("ctrl+c"))
    assert notes == ["Press [b]q[/b] to quit the app"] and app.seen["names"]
