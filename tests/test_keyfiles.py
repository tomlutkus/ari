"""ari key's STATE: what ssh, run by this user, would find at each key's path. Key files are built
here, so most tests need no ssh-keygen; those reading real keys and certificates skip without it."""

import base64
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from conftest import FIXTURES, PERSONAL_ONLY, write_config, write_mixed
from ari import core, keyfiles
from ari.cli import main
from ari.config import load_config

KEYGEN = shutil.which("ssh-keygen")
needs_keygen = pytest.mark.skipif(KEYGEN is None, reason="needs ssh-keygen")
needs_user = pytest.mark.skipif(os.geteuid() == 0, reason="root reads every file")


def run(*argv):
    return main(list(argv))


def _s(data: bytes) -> bytes:
    return len(data).to_bytes(4, "big") + data


def blob(seed: str) -> bytes:
    return _s(b"ssh-ed25519") + _s(seed.encode().ljust(32, b".")[:32])


def private(key: bytes, cipher: bytes = b"aes256-ctr") -> str:
    """An OpenSSH private key file holding key's public half, its private part never read."""
    kdf = b"none" if cipher == b"none" else b"bcrypt"
    body = b"openssh-key-v1\0" + _s(cipher) + _s(kdf) + _s(b"") + (1).to_bytes(4, "big") + _s(key) + _s(b"\0" * 64)
    text = base64.b64encode(body).decode()
    lines = [text[i : i + 70] for i in range(0, len(text), 70)]
    return "-----BEGIN OPENSSH PRIVATE KEY-----\n" + "\n".join(lines) + "\n-----END OPENSSH PRIVATE KEY-----\n"


def public(key: bytes, comment: str = "tom@laptop") -> str:
    return f"ssh-ed25519 {base64.b64encode(key).decode()} {comment}\n"


def put(path: Path, text: str, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)
    return path


def keygen(*argv: str) -> None:
    subprocess.run([KEYGEN, "-q", *argv], check=True, stdin=subprocess.DEVNULL)


@pytest.fixture
def ssh(home):
    return home / "home" / ".ssh"


# Paths, resolved as ssh resolves them


@pytest.mark.parametrize(
    "given, rest, why",
    [
        ("~/.ssh/k", ".ssh/k", None),
        ("~//.ssh/k", ".ssh/k", None),
        ("~", "", None),
        ("keys/k", None, "relative"),
        ("./k", None, "relative"),
        ("~/.ssh/%h", None, "tokens"),
        ("${HOME}/.ssh/k", None, "tokens"),
        ("~ari-no-such-user/k", None, "no user ari-no-such-user"),
    ],
)
def test_paths_resolve_as_ssh_resolves_them(home, given, rest, why):
    target, reason = keyfiles.resolve(given)
    assert reason == why
    if why is None:
        assert target == (os.path.join(home / "home", rest) if rest else str(home / "home"))
    else:
        assert target is None


def test_an_absolute_path_is_itself(home):
    assert keyfiles.resolve("/etc/ssh/k") == ("/etc/ssh/k", None)


def test_another_users_home_comes_from_passwd(home):
    import pwd

    entry = pwd.getpwuid(os.getuid())
    assert keyfiles.resolve(f"~{entry.pw_name}/k") == (os.path.join(entry.pw_dir, "k"), None)


@pytest.mark.parametrize("given", ["keys/k", "~/.ssh/%h", "~ari-no-such-user/k"])
def test_a_path_ari_cant_name_is_the_whole_state(home, given):
    assert keyfiles.state(given, None) == [keyfiles.resolve(given)[1]]


# The private key


def test_a_key_with_its_pair_is_ok(ssh):
    put(ssh / "k", private(blob("a")))
    put(ssh / "k.pub", public(blob("a")), 0o644)
    assert keyfiles.state("~/.ssh/k", None) == ["ok"]
    assert keyfiles.state("~/.ssh/k", public(blob("a"), "another comment")) == ["ok"]


def test_a_key_without_pub_or_cert_is_ok(ssh):
    put(ssh / "k", private(blob("a"), cipher=b"none"), 0o400)
    assert keyfiles.state("~/.ssh/k", None) == ["ok"]


@pytest.mark.parametrize("make", ["nothing", "parent is a file"])
def test_missing(ssh, make):
    if make == "parent is a file":
        put(ssh / "k", private(blob("a")))
        assert keyfiles.state("~/.ssh/k/inner", None) == ["missing"]
    else:
        assert keyfiles.state("~/.ssh/k", None) == ["missing"]


def test_a_directory_is_not_a_file(ssh):
    (ssh / "k").mkdir(parents=True)
    assert keyfiles.state("~/.ssh/k", None) == ["not a file"]


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o610])
def test_a_key_others_can_touch_shows_its_mode(ssh, mode):
    put(ssh / "k", private(blob("a")), mode)
    assert keyfiles.state("~/.ssh/k", None) == [f"mode {mode:04o}"]


@needs_user
def test_unreadable(ssh):
    put(ssh / "k", private(blob("a")), 0o000)
    assert keyfiles.state("~/.ssh/k", None) == ["unreadable"]
    (ssh / "k").chmod(0o600)
    ssh.chmod(0o600)
    try:
        assert keyfiles.state("~/.ssh/k", None) == ["unreadable"]
    finally:
        ssh.chmod(0o700)


@pytest.mark.parametrize(
    "text",
    [
        "-----BEGIN RSA PRIVATE KEY-----\nProc-Type: 4,ENCRYPTED\n\nAAAA\n-----END RSA PRIVATE KEY-----\n",
        "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n",
        "-----BEGIN ENCRYPTED PRIVATE KEY-----\nAAAA\n-----END ENCRYPTED PRIVATE KEY-----\n",
        "-----BEGIN EC PRIVATE KEY-----\nAAAA\n-----END EC PRIVATE KEY-----\n",
    ],
)
def test_another_private_key_format_cant_be_paired(ssh, text):
    put(ssh / "k", text)
    put(ssh / "k.pub", public(blob("other")))
    assert keyfiles.state("~/.ssh/k", public(blob("other"))) == ["pair unchecked"]


@pytest.mark.parametrize(
    "text",
    [
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGFh tom@laptop\n",
        "",
        "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEA\n",
        "-----BEGIN OPENSSH PRIVATE KEY-----\n!!!\n-----END OPENSSH PRIVATE KEY-----\n",
        "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n",
    ],
)
def test_a_file_holding_no_private_key_is_not_a_key(ssh, text):
    put(ssh / "k", text)
    assert keyfiles.state("~/.ssh/k", None) == ["not a key"]


# The public halves


def test_a_pub_file_of_another_key_is_a_mismatch(ssh):
    put(ssh / "k", private(blob("a")))
    put(ssh / "k.pub", public(blob("b")))
    assert keyfiles.state("~/.ssh/k", None) == [".pub mismatch"]


@pytest.mark.parametrize("text", ["", "nonsense\n", "# just a comment\n", "ssh-ed25519 !!! x\n"])
def test_a_pub_file_holding_no_key_says_nothing(ssh, text):
    """ssh skips it and reads the public half from the private key."""
    put(ssh / "k", private(blob("a")))
    put(ssh / "k.pub", text)
    assert keyfiles.state("~/.ssh/k", None) == ["ok"]


def test_the_pub_file_is_read_past_comments(ssh):
    put(ssh / "k", private(blob("a")))
    put(ssh / "k.pub", "# made by hand\n\n" + public(blob("a")))
    assert keyfiles.state("~/.ssh/k", None) == ["ok"]


@pytest.mark.parametrize("pub", [public(blob("b")), "", "ssh-ed25519", "not a key at all"])
def test_a_declared_pub_of_another_key_is_stale(ssh, pub):
    put(ssh / "k", private(blob("a")))
    assert keyfiles.state("~/.ssh/k", pub) == ["pub stale"]


def test_every_problem_is_listed_in_order(ssh):
    put(ssh / "k", private(blob("a")), 0o644)
    put(ssh / "k.pub", public(blob("b")))
    assert keyfiles.state("~/.ssh/k", public(blob("c"))) == ["mode 0644", ".pub mismatch", "pub stale"]


# Real keys


@needs_keygen
@pytest.mark.parametrize("argv", [["-t", "ed25519", "-N", "a passphrase"], ["-t", "rsa", "-b", "2048", "-N", ""]])
def test_real_keys_pair_without_their_passphrase(ssh, argv):
    ssh.mkdir(parents=True)
    keygen(*argv, "-C", "tom@laptop", "-f", str(ssh / "k"))
    pub = (ssh / "k.pub").read_text()
    assert keyfiles.state("~/.ssh/k", pub) == ["ok"]
    printed = subprocess.run([KEYGEN, "-l", "-f", str(ssh / "k.pub")], capture_output=True, text=True).stdout
    assert keyfiles.fingerprint(keyfiles.public_blob(pub)) == printed.split()[1]
    (ssh / "k.pub").unlink()
    assert keyfiles.state("~/.ssh/k", pub) == ["ok"]


@needs_keygen
def test_a_real_pem_key_cant_be_paired(ssh):
    ssh.mkdir(parents=True)
    keygen("-t", "rsa", "-b", "2048", "-m", "PEM", "-N", "", "-f", str(ssh / "k"))
    assert keyfiles.state("~/.ssh/k", None) == ["pair unchecked"]


# Certificates


@pytest.fixture
def signed(ssh):
    """A key and a CA to sign it with, by real ssh-keygen."""
    ssh.mkdir(parents=True)
    keygen("-t", "ed25519", "-N", "", "-f", str(ssh / "ca"))
    keygen("-t", "ed25519", "-N", "", "-f", str(ssh / "k"))
    return ssh


def sign(ssh: Path, *validity: str) -> None:
    keygen("-s", str(ssh / "ca"), "-I", "tom", "-n", "tom", *validity, str(ssh / "k.pub"))


NOW = datetime(2026, 10, 5, 12, 0)


@needs_keygen
@pytest.mark.parametrize(
    "validity, word",
    [
        ([], "cert forever"),
        (["-V", "20260101:20991231"], "cert to 2099-12-31"),
        (["-V", "20250101:20250201"], "cert expired 2025-02-01"),
        (["-V", "20261005:20261006"], "cert to 2026-10-06"),
        (["-V", "20261005120000:20261006"], "cert to 2026-10-06"),
        (["-V", "20261001:20261005120000"], "cert expired 2026-10-05"),
        (["-V", "20990101:20991231"], "cert from 2099-01-01"),
        (["-V", "20200101:forever"], "cert forever"),
        (["-V", "20990101:forever"], "cert from 2099-01-01"),
        (["-V", "always:20200101"], "cert expired 2020-01-01"),
        (["-V", "always:20991231"], "cert to 2099-12-31"),
    ],
)
def test_a_cert_beside_the_key_shows_its_validity(signed, validity, word):
    sign(signed, *validity)
    assert keyfiles.state("~/.ssh/k", None, NOW) == [word]


@needs_keygen
def test_a_cert_for_another_key(signed):
    keygen("-t", "ed25519", "-N", "", "-f", str(signed / "other"))
    keygen("-s", str(signed / "ca"), "-I", "tom", str(signed / "other.pub"))
    shutil.copy(signed / "other-cert.pub", signed / "k-cert.pub")
    assert keyfiles.state("~/.ssh/k", None, NOW) == ["cert for another key"]


@needs_keygen
@pytest.mark.parametrize("text", ["not a cert\n", "plain"])
def test_a_cert_ssh_keygen_cant_read(signed, text):
    if text == "plain":
        shutil.copy(signed / "k.pub", signed / "k-cert.pub")
    else:
        put(signed / "k-cert.pub", text)
    assert keyfiles.state("~/.ssh/k", None, NOW) == ["cert unreadable"]


def test_a_cert_without_ssh_keygen_is_unchecked(ssh, monkeypatch):
    put(ssh / "k", private(blob("a")))
    put(ssh / "k-cert.pub", "anything\n")
    monkeypatch.setattr(keyfiles.shutil, "which", lambda name: None)
    assert keyfiles.state("~/.ssh/k", None, NOW) == ["cert unchecked"]


@needs_keygen
def test_a_cert_follows_the_other_words(signed):
    sign(signed, "-V", "20250101:20250201")
    (signed / "k").chmod(0o644)
    assert keyfiles.state("~/.ssh/k", public(blob("b")), NOW) == ["mode 0644", "pub stale", "cert expired 2025-02-01"]


# ari key


@needs_keygen
def test_ari_key_shows_each_state(home, capsys, monkeypatch):
    """Every state a row can show, against tests/fixtures/keys.expected. Rows keep every column
    whole; the cert dates are fixed, so they read the same on any day before 2099."""
    write_config(home, PERSONAL_ONLY)
    ssh = home / "home" / ".ssh"
    ssh.mkdir()
    keygen("-t", "ed25519", "-N", "", "-f", str(ssh / "ca"))
    paths = {
        "fine": "~/.ssh/fine", "gone": "~/.ssh/gone", "open": "~/.ssh/open", "swapped": "~/.ssh/swapped",
        "stale": "~/.ssh/stale", "legacy": "~/.ssh/legacy", "inverted": "~/.ssh/inverted.pub",
        "folder": "~/.ssh/folder", "rel": "keys/rel", "tokens": "~/.ssh/%h", "elsewhere": "~ari-no-such-user/k",
        "forever": "~/.ssh/forever", "current": "~/.ssh/current", "expired": "~/.ssh/expired",
        "early": "~/.ssh/early", "borrowed": "~/.ssh/borrowed",
    }
    keys = {name: {"path": path} for name, path in paths.items()}
    put(ssh / "fine", private(blob("fine")))
    put(ssh / "fine.pub", public(blob("fine")))
    keys["fine"]["pub"] = public(blob("fine")).strip()
    put(ssh / "open", private(blob("open")), 0o644)
    put(ssh / "swapped", private(blob("swapped")))
    put(ssh / "swapped.pub", public(blob("another")))
    put(ssh / "stale", private(blob("stale")))
    keys["stale"]["pub"] = public(blob("before")).strip()
    put(ssh / "legacy", "-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----\n")
    put(ssh / "inverted.pub", public(blob("inverted")))
    (ssh / "folder").mkdir()
    for name, validity in [
        ("forever", []), ("current", ["-V", "20200101:20991231"]),
        ("expired", ["-V", "20200101:20200201"]), ("early", ["-V", "20990101:20991231"]),
        ("borrowed", []),
    ]:
        keygen("-t", "ed25519", "-N", "", "-f", str(ssh / name))
        keygen("-s", str(ssh / "ca"), "-I", "tom", *validity, str(ssh / f"{name}.pub"))
    shutil.copy(ssh / "forever-cert.pub", ssh / "borrowed-cert.pub")
    hosts = [{"name": "nas", "hostname": "192.0.2.254", "keys": ["fine", "open"]}]
    (home / "config" / "ari" / "personal.json").write_text(
        json.dumps({"version": 3, "defaults": {"keys": ["stale"]}, "keys": keys, "hosts": hosts})
    )
    monkeypatch.setenv("COLUMNS", "200")
    assert run("key") == 0
    out = [line.rstrip() for line in capsys.readouterr().out.splitlines()]
    assert out == (FIXTURES / "keys.expected").read_text().splitlines()
    monkeypatch.setenv("COLUMNS", "40")
    assert run("key") == 0
    narrow = capsys.readouterr().out
    assert "mode 0644" in narrow and "cert for another key" in narrow and "cert expired 2020-02-01" in narrow


def test_key_rows_carry_their_state(home):
    write_config(home, PERSONAL_ONLY)
    run("key", "k", "--path", "~/.ssh/k", "-i", "personal")
    put(home / "home" / ".ssh" / "k", private(blob("a")), 0o644)
    (row,) = core.list_keys(load_config())
    assert row.state == ["mode 0644"]


def test_nothing_but_ari_key_reads_key_files(home, capsys, monkeypatch):
    """ls, show, add, edit, import and export never look at a key file, so they work on a
    machine that doesn't hold every key."""
    write_mixed(home)

    def refuse(*args, **kwargs):
        raise AssertionError("a key file was checked")

    monkeypatch.setattr(keyfiles, "state", refuse)
    assert run("ls") == 0
    assert run("show", "web-01") == 0
    assert run("key", "laptop", "--path", "~/.ssh/laptop", "-i", "personal") == 0
    assert run("add", "lab", "192.0.2.40", "--key", "laptop", "-i", "personal") == 0
    assert run("edit", "web-01", "--key", "lab-ed25519") == 0
    assert run("export") == 0
    assert run("import", "ssh", str(home / "ssh" / "10-personal.conf"), "-i", "personal") == 0
    with pytest.raises(AssertionError, match="a key file was checked"):
        run("key")
