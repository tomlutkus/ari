"""ari key --new and --pub. --new runs a fake ssh-keygen on PATH here, which records its arguments
and writes a pair; the real one runs on a pty, where it asks for the passphrase itself."""

import json
import os
import pty
import pwd
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import PERSONAL_ONLY, write_config
from test_keyfiles import blob, private, public, put
from ari import cli, keyfiles
from ari.cli import main

USER = pwd.getpwuid(os.getuid()).pw_name
HOST = socket.gethostname()

FAKE = r'''#!PYTHON
import base64, json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_KEYGEN_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
how = os.environ.get("FAKE_KEYGEN", "pair")
if how == "fail":
    sys.exit(1)
path, note = args[args.index("-f") + 1], args[args.index("-C") + 1]
def s(b):
    return len(b).to_bytes(4, "big") + b
key = s(b"ssh-ed25519") + s(b"k" * 32)
body = b"openssh-key-v1\0" + s(b"aes256-ctr") + s(b"bcrypt") + s(b"") + (1).to_bytes(4, "big") + s(key) + s(bytes(64))
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
os.write(fd, ("-----BEGIN OPENSSH PRIVATE KEY-----\n" + base64.b64encode(body).decode() + "\n-----END OPENSSH PRIVATE KEY-----\n").encode())
os.close(fd)
if how != "no pub":
    other = s(b"ssh-ed25519") + s(b"x" * 32) if how == "mismatch" else key
    with open(path + ".pub", "w") as f:
        f.write("ssh-ed25519 " + base64.b64encode(other).decode() + " " + note + "\n")
if how.startswith("race "):
    # Someone else saves the inventory while the passphrase prompt waits.
    inventory = os.environ["FAKE_KEYGEN_INVENTORY"]
    with open(inventory) as f:
        data = json.load(f)
    data["keys"]["meanwhile"] = {"path": how.split(" ", 1)[1]}
    with open(inventory, "w") as f:
        json.dump(data, f)
'''


def run(*argv):
    return main(list(argv))


def path_of(home):
    return home / "config" / "ari" / "personal.json"


def keys(home):
    """The declared keys as saved; none when nothing has saved the inventory yet."""
    path = path_of(home)
    return json.loads(path.read_text()).get("keys", {}) if path.exists() else {}


class Keygen:
    def __init__(self, log: Path):
        self.log = log

    @property
    def calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []


@pytest.fixture
def keygen(home, monkeypatch):
    """A personal inventory, ~/.ssh under the test home, and the fake ssh-keygen first on PATH."""
    write_config(home, PERSONAL_ONLY)
    (home / "home" / ".ssh").mkdir()
    bin_dir = home / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "ssh-keygen"
    fake.write_text(FAKE.replace("PYTHON", sys.executable))
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_KEYGEN_LOG", str(home / "keygen.log"))
    monkeypatch.setenv("FAKE_KEYGEN_INVENTORY", str(path_of(home)))
    monkeypatch.setattr(cli, "_keyboard", lambda: True)
    return Keygen(home / "keygen.log")


# --new


def test_new_generates_at_the_declared_path_and_fills_pub(home, keygen, capsys):
    run("key", "laptop", "--path", "~/.ssh/laptop", "-i", "personal")
    capsys.readouterr()
    assert run("key", "laptop", "--new", "-i", "personal") == 0
    target = str(home / "home" / ".ssh" / "laptop")
    assert keygen.calls == [["-t", "ed25519", "-f", target, "-C", f"laptop {USER}@{HOST}"]]
    pub = (home / "home" / ".ssh" / "laptop.pub").read_text().strip()
    assert keys(home)["laptop"] == {"path": "~/.ssh/laptop", "pub": pub}
    assert pub.endswith(f" laptop {USER}@{HOST}")
    assert capsys.readouterr().out == "updated laptop (personal)\n"
    assert keyfiles.state("~/.ssh/laptop", pub) == ["ok"]


def test_path_and_new_declare_and_generate_together(home, keygen, capsys):
    assert run("key", "fresh", "--path", "~/.ssh/fresh", "--new", "-i", "personal") == 0
    assert capsys.readouterr().out == "declared fresh in personal\n"
    assert keys(home)["fresh"]["pub"] == (home / "home" / ".ssh" / "fresh.pub").read_text().strip()


@pytest.mark.parametrize("there", [[""], [".pub"], ["-cert.pub"], ["", ".pub", "-cert.pub"]])
def test_new_never_writes_over_a_key_file(home, keygen, capsys, there):
    run("key", "k", "--path", "~/.ssh/k", "-i", "personal")
    for suffix in there:
        put(home / "home" / ".ssh" / f"k{suffix}", "kept\n")
    before = path_of(home).read_bytes()
    capsys.readouterr()
    assert run("key", "k", "--new", "-i", "personal") == 1
    err = capsys.readouterr().err
    assert "no key generated, nothing saved" in err
    for suffix in there:
        assert f"~/.ssh/k{suffix} already exists" in err
    assert "move these away first" in err
    assert keygen.calls == []
    assert path_of(home).read_bytes() == before
    assert all((home / "home" / ".ssh" / f"k{s}").read_text() == "kept\n" for s in there)


def test_a_dangling_link_counts_as_there(home, keygen, capsys):
    (home / "home" / ".ssh" / "k.pub").symlink_to(home / "nowhere")
    assert run("key", "k", "--path", "~/.ssh/k", "--new", "-i", "personal") == 1
    assert "~/.ssh/k.pub already exists" in capsys.readouterr().err
    assert keygen.calls == [] and keys(home) == {}


def other_user() -> str:
    return next(p.pw_name for p in pwd.getpwall() if p.pw_uid != os.getuid())


@pytest.mark.parametrize(
    "path, why",
    [
        ("keys/k", "--new can't write to keys/k (relative)"),
        ("~/.ssh/%h", "--new can't write to ~/.ssh/%h (tokens)"),
        ("~ari-no-such-user/k", "--new can't write to ~ari-no-such-user/k (no user ari-no-such-user)"),
        ("~OTHER/.ssh/k", "--new can't write to ~OTHER/.ssh/k (another user's home)"),
        ("~/.ssh/nested/k", "~/.ssh/nested isn't a directory"),
    ],
)
def test_new_writes_only_where_it_can_own_the_file(home, keygen, capsys, path, why):
    path, why = path.replace("OTHER", other_user()), why.replace("OTHER", other_user())
    assert run("key", "k", "--path", path, "--new", "-i", "personal") == 1
    err = capsys.readouterr().err
    assert why in err and "no key generated" in err
    assert keygen.calls == [] and keys(home) == {}


def test_new_checks_the_declaration_before_ssh_keygen_runs(home, keygen, capsys):
    run("key", "first", "--path", "~/.ssh/shared", "-i", "personal")
    capsys.readouterr()
    assert run("key", "second", "--path", "~/.ssh/shared", "--new", "-i", "personal") == 1
    assert "~/.ssh/shared is already the file of key 'first'" in capsys.readouterr().err
    assert keygen.calls == [] and list(keys(home)) == ["first"]


@pytest.mark.parametrize(
    "how, message",
    [
        ("fail", "ssh-keygen exited 1; nothing saved"),
        ("mismatch", "ssh-keygen left no key pair at ~/.ssh/k; nothing saved"),
        ("no pub", "ssh-keygen left no key pair at ~/.ssh/k; nothing saved"),
    ],
)
def test_nothing_is_saved_without_a_pair(home, keygen, capsys, monkeypatch, how, message):
    run("key", "k", "--path", "~/.ssh/k", "-i", "personal")
    before = path_of(home).read_bytes()
    capsys.readouterr()
    monkeypatch.setenv("FAKE_KEYGEN", how)
    assert run("key", "k", "--new", "-i", "personal") == 1
    assert message in capsys.readouterr().err
    assert len(keygen.calls) == 1
    assert path_of(home).read_bytes() == before


def test_a_failed_declaration_saves_nothing_either(home, keygen, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_KEYGEN", "fail")
    assert run("key", "fresh", "--path", "~/.ssh/fresh", "--new", "-i", "personal") == 1
    assert keys(home) == {}


def test_new_saves_against_the_inventory_as_it_is_once_ssh_keygen_returns(home, keygen, capsys, monkeypatch):
    run("key", "k", "--path", "~/.ssh/k", "-i", "personal")
    monkeypatch.setenv("FAKE_KEYGEN", "race ~/.ssh/elsewhere")
    assert run("key", "k", "--new", "-i", "personal") == 0
    saved = keys(home)
    assert saved["meanwhile"] == {"path": "~/.ssh/elsewhere"} and "pub" in saved["k"]


def test_new_saves_nothing_when_the_inventory_changed_under_it(home, keygen, capsys, monkeypatch):
    run("key", "k", "--path", "~/.ssh/k", "-i", "personal")
    capsys.readouterr()
    monkeypatch.setenv("FAKE_KEYGEN", "race ~/.ssh/k")
    assert run("key", "k", "--new", "-i", "personal") == 1
    err = capsys.readouterr().err
    assert "generated ~/.ssh/k, but nothing saved" in err
    assert "~/.ssh/k is already the file of key 'meanwhile'" in err
    assert "pub" not in keys(home)["k"]


def test_new_needs_a_terminal(home, keygen, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_keyboard", lambda: False)
    assert run("key", "k", "--path", "~/.ssh/k", "--new", "-i", "personal") == 1
    assert "asks for the passphrase on a terminal" in capsys.readouterr().err
    assert keygen.calls == [] and keys(home) == {}


def test_new_without_ssh_keygen(home, keygen, capsys, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert run("key", "k", "--path", "~/.ssh/k", "--new", "-i", "personal") == 1
    assert "ssh-keygen isn't installed" in capsys.readouterr().err
    assert keygen.calls == [] and keys(home) == {}


@pytest.mark.parametrize(
    "argv, message",
    [
        (["key", "k", "--new", "--pub"], "--new fills pub itself; drop --pub"),
        (["key", "k", "--new", "--rm"], "--rm takes no other options"),
        (["key", "--new"], "--path, --new, --pub and --rm need a key NAME"),
        (["key", "--pub"], "--path, --new, --pub and --rm need a key NAME"),
        (["key", "nope", "--new"], "no key 'nope' in personal; --path PATH declares it"),
        (["key", "nope", "--pub"], "no key 'nope' in personal; --path PATH declares it"),
    ],
)
def test_key_flags_that_dont_go_together(home, keygen, capsys, argv, message):
    run("key", "k", "--path", "~/.ssh/k", "-i", "personal")
    capsys.readouterr()
    assert run(*argv, "-i", "personal") == 1
    assert message in capsys.readouterr().err
    assert keygen.calls == []


# --pub


def test_pub_takes_the_pub_file_line_when_it_holds_this_key(home, keygen, capsys):
    run("key", "k", "--path", "~/.ssh/k", "-i", "personal")
    put(home / "home" / ".ssh" / "k", private(blob("a")))
    put(home / "home" / ".ssh" / "k.pub", "# by hand\n" + public(blob("a"), "tom@laptop  spaced"))
    capsys.readouterr()
    assert run("key", "k", "--pub", "-i", "personal") == 0
    assert keys(home)["k"]["pub"] == public(blob("a"), "tom@laptop  spaced").strip()
    assert capsys.readouterr().out == "updated k (personal)\n"
    assert run("key", "k", "--pub", "-i", "personal") == 0
    assert capsys.readouterr().out == "no change to k (personal)\n"


@pytest.mark.parametrize("beside", [None, public(blob("other"))])
def test_pub_reads_the_private_key_when_no_pub_file_holds_it(home, keygen, beside):
    run("key", "k", "--path", "~/.ssh/k", "-i", "personal")
    put(home / "home" / ".ssh" / "k", private(blob("a")))
    if beside:
        put(home / "home" / ".ssh" / "k.pub", beside)
    assert run("key", "k", "--pub", "-i", "personal") == 0
    assert keys(home)["k"]["pub"] == " ".join(public(blob("a")).split()[:2])


def test_pub_clears_a_stale_state(home, keygen):
    run("key", "k", "--path", "~/.ssh/k", "-i", "personal")
    put(home / "home" / ".ssh" / "k", private(blob("a")))
    data = json.loads(path_of(home).read_text())
    data["keys"]["k"]["pub"] = public(blob("before")).strip()
    path_of(home).write_text(json.dumps(data))
    assert keyfiles.state("~/.ssh/k", keys(home)["k"]["pub"]) == ["pub stale"]
    assert run("key", "k", "--pub", "-i", "personal") == 0
    assert keyfiles.state("~/.ssh/k", keys(home)["k"]["pub"]) == ["ok"]


def test_pub_with_path_moves_and_fills(home, keygen, capsys):
    run("key", "k", "--path", "~/.ssh/k", "-i", "personal")
    put(home / "home" / ".ssh" / "b", private(blob("b")))
    assert run("key", "k", "--path", "~/.ssh/b", "--pub", "-i", "personal") == 0
    assert keys(home)["k"] == {"path": "~/.ssh/b", "pub": " ".join(public(blob("b")).split()[:2])}


@pytest.mark.parametrize(
    "path, text, message",
    [
        ("~/.ssh/k", None, "no public half to read from ~/.ssh/k (missing); nothing saved"),
        ("~/.ssh/k", "nonsense\n", "no public half to read from ~/.ssh/k (not a key); nothing saved"),
        (
            "~/.ssh/k",
            "-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----\n",
            "(pair unchecked); ssh-keygen -p -f ~/.ssh/k rewrites it in OpenSSH format; nothing saved",
        ),
        ("keys/k", None, "no public half to read from keys/k (relative); nothing saved"),
    ],
)
def test_pub_refuses_what_it_cant_read(home, keygen, capsys, path, text, message):
    run("key", "k", "--path", path, "-i", "personal")
    if text is not None:
        put(home / "home" / ".ssh" / "k", text)
    before = path_of(home).read_bytes()
    capsys.readouterr()
    assert run("key", "k", "--pub", "-i", "personal") == 1
    assert message in capsys.readouterr().err
    assert path_of(home).read_bytes() == before


# A real ssh-keygen on a real terminal

KEYGEN = shutil.which("ssh-keygen")
PROMPT = re.compile(r"passphrase[^\n]*?: ")


def on_a_pty(home: Path, argv: list[str], answers: list[str], timeout: float = 30, pause: float = 0) -> tuple[int, str]:
    """Run ari on a fresh pty that is its controlling terminal, as a shell would, answering each
    passphrase prompt in turn, pause seconds after it shows. Its exit status and everything it
    printed."""
    env = {**os.environ, "XDG_CONFIG_HOME": str(home / "config"), "XDG_STATE_HOME": str(home / "state")}
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(sys.executable, [sys.executable, "-m", "ari", *argv], env)
    seen, pending, deadline = "", list(answers), time.monotonic() + timeout
    while True:
        if time.monotonic() > deadline:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
            raise AssertionError(f"timed out; printed so far:\n{seen}")
        ready, _, _ = select.select([fd], [], [], 0.2)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 4096).decode(errors="replace")
        except OSError:
            break
        if not chunk:
            break
        seen += chunk
        # One answer per prompt, never ahead of it: ssh-keygen flushes input as it turns echo off.
        if pending and len(PROMPT.findall(seen)) > len(answers) - len(pending):
            time.sleep(pause)
            os.write(fd, pending.pop(0).encode())
    _, status = os.waitpid(pid, 0)
    os.close(fd)
    return os.waitstatus_to_exitcode(status), seen


@pytest.mark.skipif(KEYGEN is None, reason="needs ssh-keygen")
def test_new_on_a_real_terminal(home):
    write_config(home, PERSONAL_ONLY)
    target = home / "keys" / "real"
    target.parent.mkdir()
    code, printed = on_a_pty(
        home, ["key", "real", "--path", str(target), "--new", "-i", "personal"], ["correct horse\n", "correct horse\n"]
    )
    assert code == 0, printed
    assert "declared real in personal" in printed
    assert "correct horse" not in printed
    pub = target.with_name("real.pub").read_text().strip()
    assert keys(home)["real"] == {"path": str(target), "pub": pub}
    assert pub.startswith("ssh-ed25519 ") and pub.endswith(f" real {USER}@{HOST}")
    good = subprocess.run([KEYGEN, "-y", "-P", "correct horse", "-f", str(target)], capture_output=True, text=True)
    assert good.returncode == 0 and good.stdout.split()[:2] == pub.split()[:2]
    assert subprocess.run([KEYGEN, "-y", "-P", "wrong", "-f", str(target)], capture_output=True).returncode != 0
    assert oct(target.stat().st_mode & 0o777) == "0o600"


@pytest.mark.skipif(KEYGEN is None, reason="needs ssh-keygen")
def test_ctrl_c_at_the_passphrase_saves_nothing(home):
    write_config(home, PERSONAL_ONLY)
    target = home / "keys" / "real"
    target.parent.mkdir()
    # A person needs a moment to press it. ssh-keygen prints its prompt before it starts reading,
    # and a Ctrl-C in that instant only takes effect once Enter ends the read.
    argv = ["key", "real", "--path", str(target), "--new", "-i", "personal"]
    code, printed = on_a_pty(home, argv, ["\x03"], pause=0.5)
    assert code == 130, printed
    assert not target.exists() and not target.with_name("real.pub").exists()
    assert not (home / "config" / "ari" / "personal.json").exists()


# Ctrl-C while ssh-keygen has the terminal

INTERRUPTED = r'''#!PYTHON
import os, signal, sys
how = os.environ["FAKE_KEYGEN"]
if how in ("both", "parent"):
    os.kill(os.getppid(), signal.SIGINT)  # what Ctrl-C does to ari: the whole foreground group gets it
if how in ("both", "child"):
    os.kill(os.getpid(), signal.SIGINT)
'''


@pytest.mark.parametrize("how", ["both", "parent", "child"])
def test_ctrl_c_is_raised_only_once_ssh_keygen_is_gone(home, monkeypatch, how):
    """The interrupt never lands while ssh-keygen runs, or in whatever ari does as it ends: the TUI
    taking the terminal back. generate raises it afterwards, with ari's own handler back."""
    bin_dir = home / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "ssh-keygen"
    fake.write_text(INTERRUPTED.replace("PYTHON", sys.executable))
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_KEYGEN", how)
    before = signal.getsignal(signal.SIGINT)
    with pytest.raises(KeyboardInterrupt):
        keyfiles.generate(str(home / "k"), "k tom@laptop")
    assert signal.getsignal(signal.SIGINT) is before
