"""ssh export writes every value so ssh reads back what the record holds. Fields are quoted
wherever ssh would read the bare text differently; options are stored as ssh_config text, which
export can't quote, so one ssh would read differently is refused by add, edit, import and export."""

import json
import shutil
import subprocess

import pytest
from textual.widgets import TextArea

from conftest import PERSONAL_ONLY, write_config
from test_tui import browser, drive, errors, fill
from ari import core, tui
from ari.cli import main
from ari.config import load_config
from ari.modules import Module
from ari.modules.ssh import _quote, parse, unwritable

SSH = shutil.which("ssh")
needs_ssh = pytest.mark.skipif(SSH is None, reason="needs the ssh client")


def run(*argv):
    return main(list(argv))


def path(home, inventory="personal"):
    return home / "config" / "ari" / f"{inventory}.json"


def stored(home) -> dict:
    return {h["name"]: h for h in json.loads(path(home).read_text())["hosts"]}


def write(home, hosts, defaults=None, keys=None):
    """An inventory as a hand edit leaves it."""
    write_config(home, PERSONAL_ONLY)
    data = {"version": 3, "defaults": defaults or {}, "keys": keys or {}, "groups": {}, "hosts": hosts}
    path(home).write_text(json.dumps(data))


def resolve(config, name) -> subprocess.CompletedProcess:
    return subprocess.run([SSH, "-G", "-F", str(config), name], capture_output=True, text=True, stdin=subprocess.DEVNULL)


def setting(resolved, keyword) -> str:
    return next(line.split(" ", 1)[1] for line in resolved.stdout.splitlines() if line.startswith(keyword + " "))


# Fields: quoted wherever ssh would read them bare as something else

FIELDS = ["#ops", "=x", "==x", "#", "=", "a\\\\b", "\\\\", "a b", 'a"b', "a'b", "a\tb", "corp\\alice", "bob\\", "a#b", "a=b", "bob"]


@pytest.mark.parametrize("value", FIELDS)
def test_a_field_reads_back_as_itself_from_the_line_export_writes(value):
    blocks, warnings = parse(f"Host t\n    User {_quote(value)}\n", "test")
    assert warnings == [] and blocks[0].options[0][1] == value


@pytest.mark.parametrize("value", ["#ops", "=x", "==x", "a\\\\b", "#", "="])
def test_what_ssh_misreads_bare_is_quoted(value):
    assert _quote(value).startswith('"')


@pytest.mark.parametrize("value, text", [("bob", "bob"), ("corp\\alice", "corp\\alice"), ("a#b", "a#b"), ("a=b", "a=b"),
                                         ("bob\\", "bob\\"), ("a b", '"a b"'), ('a"b', '"a\\"b"'), ("a'b", '"a\'b"')])
def test_what_was_written_before_is_written_the_same(value, text):
    assert _quote(value) == text


@needs_ssh
@pytest.mark.parametrize("value", FIELDS)
def test_ssh_reads_each_field_export_writes_as_the_record_holds_it(home, value):
    write(home, [{"name": "good", "hostname": "192.0.2.70"}, {"name": "odd", "hostname": "192.0.2.71", "user": value}])
    assert run("export") == 0
    config = home / "ssh" / "10-personal.conf"
    assert setting(resolve(config, "good"), "hostname") == "192.0.2.70"
    assert setting(resolve(config, "odd"), "user") == value


@needs_ssh
def test_a_user_starting_with_hash_no_longer_breaks_the_file(home, capsys):
    """H7: User #ops reads as no value, which makes ssh refuse the whole file, every host in it."""
    write_config(home, PERSONAL_ONLY)
    assert run("add", "good", "192.0.2.70") == 0
    assert run("add", "odd", "192.0.2.71", "--user", "#ops") == 0
    assert run("export") == 0
    config = home / "ssh" / "10-personal.conf"
    assert '    User "#ops"\n' in config.read_text()
    assert setting(resolve(config, "good"), "hostname") == "192.0.2.70"
    assert setting(resolve(config, "odd"), "user") == "#ops"


@needs_ssh
def test_a_hostname_key_file_and_name_ssh_misreads_are_quoted(home):
    keys = {"odd": {"path": "#keys/odd"}, "eq": {"path": "=keys/eq"}}
    write(home, [
        {"name": "a\\\\b", "hostname": "#nope", "keys": ["odd"]},
        {"name": "eq", "hostname": "=h", "user": "=x", "keys": ["eq"]},
        {"name": "good", "hostname": "192.0.2.70"},
    ], keys=keys)
    assert run("export") == 0
    config = home / "ssh" / "10-personal.conf"
    text = config.read_text()
    assert 'Host "a\\\\\\\\b"\n    HostName "#nope"\n    IdentityFile "#keys/odd"\n' in text
    assert 'Host eq\n    HostName "=h"\n    User "=x"\n    IdentityFile "=keys/eq"\n' in text
    eq = resolve(config, "eq")
    assert (setting(eq, "hostname"), setting(eq, "user"), setting(eq, "identityfile")) == ("=h", "=x", "=keys/eq")
    assert setting(resolve(config, "good"), "hostname") == "192.0.2.70"


@pytest.mark.parametrize("field, value", [("user", "a\nb"), ("user", "a\0b"), ("hostname", "a\0b")])
def test_a_field_no_quoting_writes_stops_export(home, capsys, field, value):
    host = {"name": "odd", "hostname": "192.0.2.71"}
    host[field] = value
    write(home, [host])
    assert run("export") == 1
    err = capsys.readouterr().err
    assert f"personal/ssh: odd: {field} {value!r} can't be written: ssh_config ends a line at a newline or a NUL" in err
    assert not (home / "ssh" / "10-personal.conf").exists()


def test_a_key_file_no_quoting_writes_stops_export(home, capsys):
    write(home, [{"name": "odd", "hostname": "192.0.2.71"}], defaults={"keys": ["k"]}, keys={"k": {"path": "~/.ssh/a\0b"}})
    assert run("export") == 1
    assert "personal/ssh: key k: path '~/.ssh/a\\x00b' can't be written" in capsys.readouterr().err


# Options: refused wherever ssh would read the line export writes as something else

REFUSED = [
    ("ProxyJump", "bastion#1", "ssh cuts ProxyJump at its first #, quoted or not, and would read 'bastion'"),
    ("ProxyJump", '"bastion#1"', "ssh cuts ProxyJump at its first #, quoted or not, and would read '\"bastion'"),
    ("ProxyJump", "a,b#c", "ssh cuts ProxyJump at its first #, quoted or not, and would read 'a,b'"),
    ("SetEnv", "#x", "a # that starts a value comments out the rest, and ssh would read no value"),
    ("SendEnv", "LANG #x", "a # that starts a value comments out the rest, and ssh would read 'LANG'"),
    ("LocalForward", "8080 localhost:80 #c", "a # that starts a value comments out the rest, and ssh would read '8080 localhost:80'"),
    ("SetEnv", 'A="b', "an unclosed quote, which makes ssh refuse the whole file"),
    ("ProxyCommand", "sh -c 'nc %h", "an unclosed quote, which makes ssh refuse the whole file"),
    ("ServerAliveInterval", " 30", "ssh drops a leading = and whitespace at either end, and would read '30'"),
    ("SetEnv", "FOO=1 ", "ssh drops a leading = and whitespace at either end, and would read 'FOO=1'"),
    ("SetEnv", "=FOO=1", "ssh drops a leading = and whitespace at either end, and would read 'FOO=1'"),
    ("ProxyCommand", "=nc %h %p", "ssh drops a leading = and whitespace at either end, and would read 'nc %h %p'"),
    ("ProxyJump", "=bastion", "ssh drops a leading = and whitespace at either end, and would read 'bastion'"),
]

KEPT = [
    ("ProxyCommand", "nc %h %p # via the jump"),  # the commands keep the rest of the line, # and all
    ("RemoteCommand", "echo #fine"),
    ("LocalCommand", "echo a # b"),
    ("KnownHostsCommand", "/bin/echo %H #x"),
    ("ProxyCommand", "nc #it's"),  # a quote after a comment never opens
    ("SetEnv", "FOO=a#b"),  # a # inside a value is the value's
    ("SetEnv", '"A=b c"'),
    ("SendEnv", "LANG LC_*"),
    ("LocalForward", "8080 localhost:80"),
    ("ProxyJump", "user@bastion:22,other"),
    ("User2", "x"),
]


@pytest.mark.parametrize("keyword, value, why", REFUSED)
def test_an_option_ssh_reads_differently_is_unwritable(keyword, value, why):
    assert unwritable(keyword, value) == why


@pytest.mark.parametrize("keyword, value", KEPT)
def test_an_option_ssh_reads_as_stored_is_writable(keyword, value):
    assert unwritable(keyword, value) is None


@needs_ssh
@pytest.mark.parametrize("keyword, value, why", REFUSED)
def test_ssh_reads_each_refused_option_as_the_refusal_says(tmp_path, keyword, value, why):
    """The judge is ssh itself: what it makes of the line export would write."""
    config = tmp_path / "c"
    config.write_text(f"Host t\n    HostName 192.0.2.1\n    {keyword} {value}\n")
    resolved = resolve(config, "t")
    if "unclosed quote" in why:
        assert resolved.returncode != 0 and "invalid quotes" in resolved.stderr
        return
    if "would read no value" in why and resolved.returncode != 0:
        return  # OpenSSH 10 refuses SetEnv and SendEnv with nothing after the comment; 9.6 takes them as nothing
    assert resolved.returncode == 0, resolved.stderr
    read = [line.split(" ", 1)[1] for line in resolved.stdout.splitlines() if line.startswith(keyword.lower() + " ")]
    assert read != [value]
    if "would read no value" in why:
        assert read == []
    elif keyword not in ("LocalForward",):  # ssh -G prints a forward its own way
        assert read == [why.rsplit("would read ", 1)[1].strip("'")]


@needs_ssh
@pytest.mark.parametrize("keyword, value", [k for k in KEPT if k[0] not in ("User2", "LocalForward", "SetEnv", "SendEnv")])
def test_ssh_reads_each_kept_option_as_stored(tmp_path, keyword, value):
    config = tmp_path / "c"
    config.write_text(f"Host t\n    HostName 192.0.2.1\n    {keyword} {value}\n")
    resolved = resolve(config, "t")
    assert resolved.returncode == 0, resolved.stderr
    assert setting(resolved, keyword.lower()) == value


@pytest.mark.parametrize("keyword, value, why", REFUSED)
def test_add_refuses_an_option_ssh_reads_differently_and_saves_nothing(home, capsys, keyword, value, why):
    write_config(home, PERSONAL_ONLY)
    assert run("add", "good", "192.0.2.70") == 0
    before = path(home).read_bytes()
    assert run("add", "odd", "192.0.2.71", "--opt", f"{keyword}={value}") == 1
    assert f"personal (odd): ssh option {keyword} {value!r} can't be written: {why}" in capsys.readouterr().err
    assert path(home).read_bytes() == before


@pytest.mark.parametrize("keyword, value", KEPT)
def test_add_keeps_an_option_ssh_reads_as_stored(home, keyword, value):
    write_config(home, PERSONAL_ONLY)
    assert run("add", "web", "192.0.2.70", "--opt", f"{keyword}={value}") == 0
    assert stored(home)["web"]["modules"]["ssh"]["options"] == {keyword: value}


def test_every_value_of_a_list_is_checked(home, capsys):
    write_config(home, PERSONAL_ONLY)
    assert run("add", "web", "192.0.2.70", "--opt", "SendEnv=LANG", "--opt", "SendEnv=#x") == 1
    assert "ssh option SendEnv '#x' can't be written" in capsys.readouterr().err


def test_the_refusal_is_a_problem_with_the_options_and_lists_with_the_rest(home):
    write_config(home, PERSONAL_ONLY)
    cfg = load_config()
    changes = core.Changes(port="0", options=[("ProxyJump", "bastion#1"), ("SetEnv", "#x")])
    with pytest.raises(core.HostRefused) as refused:
        core.add_host(cfg, "personal", "web", "192.0.2.70", changes)
    fields = [p.field for p in refused.value.problems]
    assert fields == ["port", "options", "options"]


def test_a_stored_value_still_loads_and_edit_clears_it(home, capsys):
    """Load never refuses one: a hand edit or an older ari may have stored it, and edit puts it right."""
    write(home, [{"name": "web", "hostname": "192.0.2.70", "modules": {"ssh": {"options": {"ProxyJump": "bastion#1", "Compression": "yes"}}}}])
    assert run("ls") == 0
    assert run("edit", "web", "--user", "ops") == 1
    assert "ssh option ProxyJump 'bastion#1' can't be written" in capsys.readouterr().err
    assert run("edit", "web", "--user", "ops", "--opt", "ProxyJump=") == 0
    assert stored(home)["web"]["modules"]["ssh"]["options"] == {"Compression": "yes"}
    assert stored(home)["web"]["user"] == "ops"


def test_export_refuses_an_option_ssh_reads_differently_and_writes_nothing(home, capsys):
    write(home, [
        {"name": "good", "hostname": "192.0.2.70"},
        {"name": "web", "hostname": "192.0.2.71", "modules": {"ssh": {"options": {"ProxyJump": "bastion#1"}}}},
    ], defaults={"modules": {"ssh": {"options": {"LocalForward": ["8080 localhost:80", "9090 localhost:90 #old"]}}}})
    assert run("export") == 1
    err = capsys.readouterr().err
    assert "personal/ssh: defaults: ssh option LocalForward '9090 localhost:90 #old' can't be written" in err
    assert "personal (web): ssh option ProxyJump 'bastion#1' can't be written" in err
    assert not (home / "ssh" / "10-personal.conf").exists()


def test_export_leaves_out_a_host_the_ssh_module_never_writes(home):
    write(home, [{"name": "web", "hostname": "192.0.2.71", "exclude": ["ssh"], "user": "a\nb"}])
    assert run("export") == 0


def test_import_refuses_a_host_whose_option_it_couldnt_write_back(home, tmp_path, capsys):
    """SetEnv ==x is SetEnv =x to ssh, which export would write as SetEnv =x, read as x."""
    write_config(home, PERSONAL_ONLY)
    source = tmp_path / "src.conf"
    source.write_text("Host odd\n    HostName 192.0.2.71\n    SetEnv ==x\n\nHost good\n    HostName 192.0.2.70\n")
    assert run("import", "ssh", str(source)) == 1
    err = capsys.readouterr().err
    assert "ssh option SetEnv '=x' can't be written" in err and "not adopted" in err
    assert list(stored(home)) == ["good"]


def test_a_host_the_ssh_module_never_writes_keeps_its_options(home, capsys):
    """Write rules apply where the module writes the host: this one excludes ssh."""
    write_config(home, PERSONAL_ONLY)
    assert run("add", "web", "192.0.2.70", "--exclude", "ssh", "--opt", "ProxyJump=bastion#1") == 0
    assert run("export") == 0
    assert "web" not in (home / "ssh" / "10-personal.conf").read_text()
    assert run("edit", "web", "--include", "ssh") == 1
    assert "ssh option ProxyJump 'bastion#1' can't be written" in capsys.readouterr().err


def test_an_inventory_with_ssh_off_keeps_its_options(home):
    write_config(home, PERSONAL_ONLY + "enabled = false\n")
    assert run("add", "web", "192.0.2.70", "--opt", "ProxyJump=bastion#1") == 0


def test_a_module_that_says_nothing_has_no_host_problems():
    assert Module().host_problems(None, None) == []


def test_the_form_shows_the_refusal_under_the_options(home):
    write_config(home, PERSONAL_ONLY)
    assert run("add", "seed", "192.0.2.1") == 0
    before = path(home).read_bytes()

    async def script(pilot):
        await pilot.press("a")
        form = app.screen
        fill(form, name="web", hostname="192.0.2.70", options="ProxyJump bastion#1")
        await pilot.press("ctrl+s")
        assert app.screen is form
        shown = errors(form)
        assert set(shown) == {"options"}
        assert "ssh cuts ProxyJump at its first #, quoted or not" in shown["options"]
        assert form.query_one("#f-options", TextArea).text == "ProxyJump bastion#1"

    app = browser()
    drive(app, script)
    assert path(home).read_bytes() == before
    assert tui.parse_options("ProxyJump bastion#1") == [("ProxyJump", "bastion#1")]
