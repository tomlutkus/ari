"""The TUI's keys screen: k from the host list. It holds no logic: rows come from core.list_keys,
Enter narrows the list with core.uses_key, d and n go through core.check_key and write_key."""

import contextlib
import json
import os
import sys

import pytest
from textual.widgets import DataTable, Input, Static

from conftest import write_mixed
from test_keyfiles import blob, private, public, put
from test_new_key import FAKE, Keygen
from test_tui import browser, drive, names, shown
from ari import core, keyfiles, tui
from ari.cli import main
from ari.config import load_config


def run(*argv):
    return main(list(argv))


@pytest.fixture
def keyed(home):
    """The mixed inventories with keys in several states. work: lab-ed25519 in the defaults, so
    fw and vault-01 inherit it; web-01 lists old and lab-ed25519; spare is unused. personal:
    laptop, used by nas."""
    write_mixed(home)
    ssh = home / "home" / ".ssh"
    put(ssh / "lab-ed25519", private(blob("lab")))
    put(ssh / "lab-ed25519.pub", public(blob("lab")))
    put(ssh / "old", private(blob("old")), 0o644)
    for argv in (
        ["key", "old", "--path", "~/.ssh/old", "-i", "work"],
        ["key", "old-2", "--path", "~/.ssh/old-2", "-i", "work"],
        ["key", "spare", "--path", "~/.ssh/spare", "-i", "work"],
        ["key", "laptop", "--path", "~/.ssh/laptop", "-i", "personal"],
        ["edit", "web-01", "--key", "old", "--key", "lab-ed25519"],
        ["edit", "nas", "--key", "laptop"],
    ):
        assert run(*argv) == 0
    return home


def keys_table(app) -> DataTable:
    return app.screen.query_one(DataTable)


def key_rows(app) -> dict[str, dict[str, str]]:
    t = keys_table(app)
    labels = [c.label.plain for c in t.columns.values()]
    return {key.value: dict(zip(labels, (c.plain for c in t.get_row(key)))) for key in t.rows}


async def to_key(pilot, app, name: str, inventory: str) -> None:
    """Put the keys screen's cursor on one key."""
    row = app.screen.shown.index((inventory, name))
    keys_table(app).move_cursor(row=row)
    await pilot.pause()


def stored_keys(home, inventory="work") -> dict:
    return json.loads((home / "config" / "ari" / f"{inventory}.json").read_text())["keys"]


def notes_of(app, monkeypatch) -> list[tuple[str, str | None]]:
    notes = []
    monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append((message, kw.get("severity"))))
    return notes


# The screen


def test_k_lists_every_key_as_ari_key_does(keyed):
    async def script(pilot):
        await pilot.press("k")
        assert isinstance(app.screen, tui.Declarations)
        t = keys_table(app)
        assert [c.label.plain for c in t.columns.values()] == ["KEY", "HOSTS", "PATH", "STATE", "INV"]
        rows = key_rows(app)
        expected = core.list_keys(load_config())
        assert app.screen.shown == [(r.inventory.name, r.name) for r in expected]
        assert rows["work\tlab-ed25519"] == {
            "KEY": "lab-ed25519", "HOSTS": "3 (1 direct)", "PATH": "~/.ssh/lab-ed25519", "STATE": "ok", "INV": "work",
        }
        assert rows["work\told"]["STATE"] == "mode 0644" and rows["work\told"]["HOSTS"] == "1"
        assert rows["work\tspare"]["STATE"] == "missing" and rows["work\tspare"]["HOSTS"] == "0"
        assert rows["personal\tlaptop"]["INV"] == "personal"
        assert not app.screen.query_one("#empty").display

    app = browser()
    drive(app, script)


def test_the_inventory_flag_narrows_the_keys_too(keyed):
    async def script(pilot):
        await pilot.press("k")
        assert app.screen.shown == [("personal", "laptop")]

    app = browser("personal")
    drive(app, script)


def test_no_keys_says_how_to_declare_one(home):
    write_mixed(home)

    async def script(pilot):
        await pilot.press("k")
        assert app.screen.shown == [("work", "lab-ed25519")]

    app = browser()
    drive(app, script)

    async def empty(pilot):
        await pilot.press("k")
        assert app2.screen.shown == [] and app2.screen.query_one("#empty").display
        assert "ari key NAME --path PATH declares one" in shown(app2.screen)
        await pilot.press("enter", "d", "n")
        assert isinstance(app2.screen, tui.Declarations)

    app2 = browser("personal")
    drive(app2, empty)


def test_escape_goes_back_and_q_quits(keyed):
    async def script(pilot):
        await pilot.press("k", "escape")
        assert app.screen is app.hosts

    app = browser()
    drive(app, script)
    quit_app = drive(browser(), lambda pilot: pilot.press("k", "q"))
    assert quit_app.return_code == 0 and not quit_app.is_running


def test_k_typed_into_the_filter_is_just_a_letter(keyed):
    async def script(pilot):
        await pilot.press("slash", "k")
        assert app.screen is app.hosts

    app = browser()
    assert drive(app, script).seen["filter"] == "k"


# Enter


def test_enter_narrows_the_list_to_the_hosts_that_use_the_key(keyed):
    async def script(pilot):
        await pilot.press("k")
        await to_key(pilot, app, "lab-ed25519", "work")
        await pilot.press("enter")
        assert app.screen is app.hosts and app.focused is app.hosts.query_one(DataTable)
        assert names(app) == ["fw", "vault-01", "web-01"]
        narrow = app.hosts.query_one("#narrow", Static)
        assert narrow.display and "hosts using key lab-ed25519 (work)" in shown_of(narrow)
        await pilot.press("slash", "v", "a", "u", "enter")
        assert names(app) == ["vault-01"]
        await pilot.press("escape")
        assert len(names(app)) == 5 and not narrow.display and app.hosts.query_one(Input).value == ""

    app = browser()
    drive(app, script)


def shown_of(static: Static) -> str:
    content = static.content
    return getattr(content, "plain", content)


@pytest.mark.parametrize(
    "key, inventory, hosts",
    [("old", "work", ["web-01"]), ("old-2", "work", []), ("spare", "work", []), ("laptop", "personal", ["nas"])],
)
def test_the_narrowed_list_is_exactly_what_hosts_counts(keyed, key, inventory, hosts):
    """old is a prefix of old-2, and a text search for it would show both."""

    async def script(pilot):
        await pilot.press("k")
        await to_key(pilot, app, key, inventory)
        count = key_rows(app)[f"{inventory}\t{key}"]["HOSTS"].split()[0]
        await pilot.press("enter")
        assert names(app) == hosts and int(count) == len(hosts)

    app = browser()
    drive(app, script)


# d


def test_d_asks_and_removes_an_unused_key(keyed):
    async def script(pilot):
        await pilot.press("k")
        await to_key(pilot, app, "spare", "work")
        await pilot.press("d")
        assert isinstance(app.screen, tui.Confirm) and shown(app.screen) == "Remove key spare from work? Its files stay."
        await pilot.press("y")
        assert ("work", "spare") not in app.screen.shown
        assert app.screen.selected() == ("work", "old-2")

    app = browser()
    drive(app, script)
    assert "spare" not in stored_keys(keyed)


@pytest.mark.parametrize("answer", ["n", "escape"])
def test_n_or_escape_keeps_the_key(keyed, answer):
    before = stored_keys(keyed)

    async def script(pilot):
        await pilot.press("k")
        await to_key(pilot, app, "spare", "work")
        await pilot.press("d", answer)
        assert isinstance(app.screen, tui.Declarations) and ("work", "spare") in app.screen.shown

    app = browser()
    drive(app, script)
    assert stored_keys(keyed) == before


@pytest.mark.parametrize(
    "key, inventory, message",
    [
        ("lab-ed25519", "work", "work: key 'lab-ed25519' is in the defaults' keys"),
        ("old", "work", "work: key 'old' is still listed by"),
        ("laptop", "personal", "personal: key 'laptop' is still listed by"),
    ],
)
def test_d_on_a_key_in_use_shows_cores_refusal_without_asking(keyed, monkeypatch, key, inventory, message):
    before = stored_keys(keyed, inventory)
    app = browser()
    notes = notes_of(app, monkeypatch)

    async def script(pilot):
        await pilot.press("k")
        await to_key(pilot, app, key, inventory)
        await pilot.press("d")
        assert isinstance(app.screen, tui.Declarations)

    drive(app, script)
    with pytest.raises(Exception) as refused:
        core.check_key(load_config(), inventory, key, core.KeyChanges(remove=True))
    assert notes == [(str(refused.value), "error")] and message in notes[0][0]
    assert stored_keys(keyed, inventory) == before


# n


@pytest.fixture
def keygen(keyed, monkeypatch):
    """The fake ssh-keygen first on PATH, and a suspend that does nothing, as the headless driver
    can't suspend."""
    bin_dir = keyed / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "ssh-keygen"
    fake.write_text(FAKE.replace("PYTHON", sys.executable))
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_KEYGEN_LOG", str(keyed / "keygen.log"))
    suspended = []

    @contextlib.contextmanager
    def suspend(self):
        suspended.append(True)
        yield

    monkeypatch.setattr(tui.Browser, "suspend", suspend)
    calls = Keygen(keyed / "keygen.log")
    calls.suspended = suspended
    return calls


def test_n_hands_the_terminal_to_ssh_keygen_and_fills_pub(keyed, keygen, monkeypatch):
    app = browser()
    notes = notes_of(app, monkeypatch)

    async def script(pilot):
        await pilot.press("k")
        await to_key(pilot, app, "spare", "work")
        await pilot.press("n")
        assert app.screen.selected() == ("work", "spare")
        assert key_rows(app)["work\tspare"]["STATE"] == "ok"

    drive(app, script)
    target = str(keyed / "home" / ".ssh" / "spare")
    assert keygen.calls == [["-t", "ed25519", "-f", target, "-C", keyfiles.comment("spare")]]
    assert keygen.suspended == [True]
    assert notes == [("generated ~/.ssh/spare for spare (work)", None)]
    assert stored_keys(keyed)["spare"]["pub"] == (keyed / "home" / ".ssh" / "spare.pub").read_text().strip()


def test_n_says_why_before_it_hands_anything_over(keyed, keygen, monkeypatch):
    app = browser()
    notes = notes_of(app, monkeypatch)

    async def script(pilot):
        await pilot.press("k")
        await to_key(pilot, app, "old", "work")
        await pilot.press("n")

    drive(app, script)
    assert keygen.calls == [] and keygen.suspended == []
    assert len(notes) == 1 and notes[0][1] == "error"
    assert "~/.ssh/old already exists" in notes[0][0] and "no key generated" in notes[0][0]


@pytest.mark.parametrize(
    "how, message",
    [("fail", "ssh-keygen exited 1; nothing saved"), ("mismatch", "ssh-keygen left no key pair at ~/.ssh/spare; nothing saved")],
)
def test_a_failed_ssh_keygen_is_reported_and_the_tui_carries_on(keyed, keygen, monkeypatch, how, message):
    monkeypatch.setenv("FAKE_KEYGEN", how)
    before = stored_keys(keyed)
    app = browser()
    notes = notes_of(app, monkeypatch)

    async def script(pilot):
        await pilot.press("k")
        await to_key(pilot, app, "spare", "work")
        await pilot.press("n")
        assert isinstance(app.screen, tui.Declarations)

    drive(app, script)
    assert notes == [(message, "error")] and keygen.suspended == [True]
    assert stored_keys(keyed) == before


def test_ctrl_c_in_ssh_keygen_comes_back_to_the_tui(keyed, keygen, monkeypatch):
    def interrupted(target, text):
        raise KeyboardInterrupt

    monkeypatch.setattr(keyfiles, "generate", interrupted)
    before = stored_keys(keyed)
    app = browser()
    notes = notes_of(app, monkeypatch)

    async def script(pilot):
        await pilot.press("k")
        await to_key(pilot, app, "spare", "work")
        await pilot.press("n")
        assert isinstance(app.screen, tui.Declarations)

    drive(app, script)
    assert notes == [("ssh-keygen interrupted; nothing saved", "error")] and app.return_code is None
    assert stored_keys(keyed) == before


def test_without_a_terminal_to_suspend_ssh_keygen_never_runs(keyed, monkeypatch):
    monkeypatch.setattr(keyfiles, "generate", lambda target, text: pytest.fail("ssh-keygen ran"))
    app = browser()
    notes = notes_of(app, monkeypatch)

    async def script(pilot):
        await pilot.press("k")
        await to_key(pilot, app, "spare", "work")
        await pilot.press("n")

    drive(app, script)
    assert notes == [("this terminal can't hand over to ssh-keygen", "error")]


# The screen takes a kind


def test_a_kind_without_a_generator_offers_no_n(keyed, monkeypatch):
    """Groups will reuse the screen with a kind of their own; one that can't generate hides n."""
    plain = tui.Kind(**{**tui.KEYS.__dict__, "generate": None, "check_generate": None, "generator": ""})
    monkeypatch.setattr(keyfiles, "generate", lambda target, text: pytest.fail("ssh-keygen ran"))

    async def script(pilot):
        app.push_screen(tui.Declarations(load_config(), None, plain))
        await pilot.pause()
        assert app.screen.check_action("generate", ()) is False
        await pilot.press("n")
        assert isinstance(app.screen, tui.Declarations)

    app = browser()
    drive(app, script)
