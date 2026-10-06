"""ssh import reads every line the way OpenSSH reads it: lines end at \\n alone, values split as
argv_split splits them (quotes, the backslashes it removes and the ones it keeps, a # that starts a
token ending the line), and the commands ssh takes whole stay whole."""

import getpass
import json
import shutil
import subprocess

import pytest

from conftest import PERSONAL_ONLY, write_config
from ari.cli import main
from ari.modules import ssh as ssh_module
from ari.modules.ssh import parse, to_hosts

SSH = shutil.which("ssh")


def run(*argv):
    return main(list(argv))


def read(text):
    """The hosts ari makes of text, and its warnings."""
    blocks, warnings = parse(text, "test")
    return [h for b in blocks for h in to_hosts(b, "test", warnings)], warnings


def one(text):
    hosts, warnings = read(text)
    assert len(hosts) == 1, (hosts, warnings)
    return hosts[0], warnings


def options(host):
    return host.modules.get("ssh", {}).get("options", {})


# The tokenizer: OpenSSH's argv_split, line for line


@pytest.mark.parametrize(
    "text, tokens",
    [
        ("a b\tc", ["a", "b", "c"]),
        ("corp\\alice", ["corp\\alice"]),  # a backslash before anything else stays
        ('"corp\\\\alice"', ["corp\\alice"]),
        ("a\\ b", ["a b"]),  # an escaped space outside quotes
        ('"a\\ b"', ["a\\ b"]),  # inside quotes it stays escaped
        ('a\\"b', ['a"b']),
        ("a\\'b", ["a'b"]),
        ("bob\\", ["bob\\"]),
        ('"a b"', ["a b"]),
        ("'a b'", ["a b"]),
        ('a"b c"d', ["ab cd"]),  # a token runs on after a closing quote
        ("'a\"b'", ['a"b']),
        ('""', [""]),
        ("x # comment", ["x"]),
        ("x\t#comment", ["x"]),
        ("x#y", ["x#y"]),  # a # inside a token is the token's
        ('"#x"', ["#x"]),
        ("# all comment", []),
        ("a\x0bb", ["a\x0bb"]),  # only space and tab separate tokens
    ],
)
def test_args_split_as_openssh_does(text, tokens):
    assert ssh_module._args(text)[0] == tokens


@pytest.mark.parametrize("text", ['"bob', "'bob", 'a "b c'])
def test_an_unclosed_quote_is_unreadable(text):
    assert ssh_module._args(text) is None


def test_args_say_where_a_comment_starts():
    text = 'LANG "LC_ X" # c'
    tokens, end = ssh_module._args(text)
    assert tokens == ["LANG", "LC_ X"] and text[:end].rstrip() == 'LANG "LC_ X"'


# H1: backslashes


def test_a_backslash_ssh_keeps_is_kept():
    host, warnings = one("Host winbox\n    HostName 192.0.2.40\n    User corp\\alice\n")
    assert host.user == "corp\\alice" and warnings == []


def test_quoting_and_escapes_come_off_as_ssh_takes_them_off():
    host, _ = one('Host h\n  HostName "192.0.2.5"\n  User a\\ b\n  IdentityFile "~/.ssh/k one"\n')
    assert (host.hostname, host.user) == ("192.0.2.5", "a b")


# H2: a # that starts a token ends the line


def test_a_trailing_comment_is_not_part_of_the_value():
    host, warnings = one("Host db\n  HostName 192.0.2.50 # lab\n  User postgres # service account\n  Port 2222 # alt\n")
    assert (host.hostname, host.user, host.port) == ("192.0.2.50", "postgres", 2222) and warnings == []


def test_a_comment_on_a_host_line_names_no_hosts():
    hosts, warnings = read("Host app1 app2 # old names kept\n    User deploy\n")
    assert [h.name for h in hosts] == ["app1", "app2"] and warnings == []


def test_a_hash_inside_a_token_or_quoted_is_kept():
    host, _ = one('Host h\n  HostName 192.0.2.1\n  User "#ops"\n')
    assert host.user == "#ops"
    host, _ = one("Host h\n  HostName 192.0.2.1\n  User po#stgres\n")
    assert host.user == "po#stgres"


def test_options_keep_their_text_up_to_a_comment():
    host, _ = one(
        'Host h\n  HostName 192.0.2.1\n  ServerAliveInterval 30 # keep\n  SendEnv LANG "LC_ X" # c\n  SetEnv A="b c"\n'
    )
    assert options(host) == {"ServerAliveInterval": "30", "SendEnv": 'LANG "LC_ X"', "SetEnv": 'A="b c"'}


def test_commands_ssh_takes_whole_stay_whole():
    host, _ = one(
        "Host h\n  HostName 192.0.2.1\n"
        '  ProxyCommand sh -c "nc %h %p" # x\n'
        "  RemoteCommand echo a # b\n"
        "  LocalCommand echo c # d\n"
        "  KnownHostsCommand /bin/echo %H # e\n"
    )
    assert options(host) == {
        "ProxyCommand": 'sh -c "nc %h %p" # x',
        "RemoteCommand": "echo a # b",
        "LocalCommand": "echo c # d",
        "KnownHostsCommand": "/bin/echo %H # e",
    }


def test_proxyjump_ends_at_its_first_hash_as_ssh_reads_it():
    host, _ = one("Host h\n  HostName 192.0.2.1\n  ProxyJump a,b # c\n")
    assert options(host) == {"ProxyJump": "a,b"}
    host, _ = one("Host h\n  HostName 192.0.2.1\n  ProxyJump bastion#1\n")
    assert options(host) == {"ProxyJump": "bastion"}


# H3: lines end at \n alone


@pytest.mark.parametrize("brk", ["\x85", " ", " ", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e"])
def test_text_inside_a_comment_stays_in_the_comment(brk):
    host, warnings = one(f"Host jump\n    HostName 192.0.2.31\n    # ProxyJump note{brk}ProxyJump evil.example\n    User ops\n")
    assert options(host) == {} and host.user == "ops" and warnings == []


def test_a_hidden_hostname_in_a_comment_is_not_the_first():
    host, warnings = one("Host h\n    # moved HostName 203.0.113.66\n    HostName 192.0.2.30\n")
    assert host.hostname == "192.0.2.30" and warnings == []


def test_crlf_and_a_nul_end_a_line_where_ssh_ends_it():
    host, _ = one("Host h\r\n  HostName 192.0.2.1\r\n  User bob\0junk\r\n")
    assert (host.name, host.hostname, host.user) == ("h", "192.0.2.1", "bob")


# A line ssh refuses: ssh refuses the whole file, so import skips the line and adopts nothing


@pytest.mark.parametrize(
    "line, why",
    [
        ("User bob extra", "more than one value"),
        ('User "bob', "an unclosed quote"),
        ("User # nobody", "no value"),
        ('User ""', "no value"),
        ("User", "no value"),
        ("HostName a b", "more than one value"),
        ("Port 22 23", "more than one value"),
        ("IdentityFile ~/.ssh/a ~/.ssh/b", "more than one value"),
    ],
)
def test_a_line_ssh_refuses_is_skipped_and_said(line, why):
    host, warnings = one(f"Host h\n  HostName 192.0.2.1\n  {line}\n  User fine\n" if not line.startswith("HostName") else f"Host h\n  {line}\n  User fine\n")
    assert len(warnings) == 1 and f"test:{3 if not line.startswith('HostName') else 2}:" in warnings[0]
    assert f"ssh refuses this line ({why})" in warnings[0] and "and with it the whole file" in warnings[0]
    assert host.user == "fine"


def test_an_unreadable_host_line_skips_its_block():
    hosts, warnings = read('Host "a\n  User x\nHost b\n  HostName 192.0.2.2\n')
    assert [h.name for h in hosts] == ["b"] and "ssh refuses this line (an unclosed quote)" in warnings[0]


def test_a_host_line_naming_no_host_skips_its_block():
    """Host with only a comment is a block ssh applies to nothing; Host with nothing is refused."""
    hosts, warnings = read("Host # retired\n  User x\nHost b\n  HostName 192.0.2.2\n")
    assert [h.name for h in hosts] == ["b"] and warnings == ["test:1: Host line names no host; block skipped"]
    hosts, warnings = read("Host\n  User x\nHost b\n  HostName 192.0.2.2\n")
    assert [h.name for h in hosts] == ["b"] and "ssh refuses this line (no value)" in warnings[0]


@pytest.mark.skipif(SSH is None, reason="needs the ssh client")
def test_ssh_takes_a_host_line_of_only_a_comment(tmp_path):
    config = tmp_path / "c"
    config.write_text("Host # retired\n  User x\n")
    assert subprocess.run([SSH, "-G", "-F", str(config), "t"], capture_output=True).returncode == 0
    config.write_text("Host\n  User x\n")
    assert subprocess.run([SSH, "-G", "-F", str(config), "t"], capture_output=True).returncode != 0


def test_an_import_with_a_line_ssh_refuses_is_not_adopted(home, tmp_path, capsys):
    write_config(home, PERSONAL_ONLY)
    source = tmp_path / "src.conf"
    source.write_text("Host h\n  HostName 192.0.2.1\n  User bob extra\n")
    assert run("import", "ssh", str(source)) == 0
    err = capsys.readouterr().err
    assert "ssh refuses this line (more than one value)" in err and "not adopted" in err


# The round trip, with ssh as the judge


ROUND_TRIP = r"""Host winbox
    HostName 192.0.2.40
    User corp\alice
    IdentityFile "~/.ssh/k one"
    IdentitiesOnly yes

Host db db.lab # an old alias
    HostName 192.0.2.50 # lab
    User postgres # service account
    Port 2222 # alt
    ServerAliveInterval 30 # keep
    SendEnv LANG "LC_ X" # c

Host jump
    HostName 192.0.2.31
    # ProxyJump note ProxyJump evil.example
    ProxyCommand sh -c "nc %h %p" # x
    User a\ b

Host hashes
    HostName 192.0.2.32
    User po#stgres
    ProxyJump bastion#1

Host quoted
    HostName 192.0.2.33
    User "#ops"
    IdentityFile "#keys/odd"

Host eq
    HostName 192.0.2.34
    User ==x
    IdentityFile "=keys/eq"
"""


@pytest.mark.skipif(SSH is None, reason="needs the ssh client")
def test_import_then_export_changes_nothing_ssh_resolves(home, tmp_path):
    write_config(home, PERSONAL_ONLY)
    source = tmp_path / "src.conf"
    source.write_text(ROUND_TRIP.replace("note ProxyJump", "note ProxyJump"))
    assert run("import", "ssh", str(source)) == 0
    assert run("export") == 0
    exported = home / "ssh" / "10-personal.conf"
    for name in ("winbox", "db", "db.lab", "jump", "hashes", "quoted", "eq", "old"):
        before = subprocess.run([SSH, "-G", "-F", str(source), name], capture_output=True, text=True, check=True).stdout
        after = subprocess.run([SSH, "-G", "-F", str(exported), name], capture_output=True, text=True, check=True).stdout
        assert before == after, name


# ssh itself, case by case, as tokenizer-check.sh runs it


USER_LINES = [
    "User corp\\alice", 'User "corp\\\\alice"', "User a\\ b", 'User a\\"b', "User bob\\", 'User "a b"', "User 'a b'",
    'User a"b c"d', 'User ""', "User=bob", "User = bob", "\tUser bob", "User postgres # svc", "User po#stgres",
    'User "#ops"', "User postgres\t#svc", "User # nobody", "User bob extra", "User bob\r", 'User "bob',
    "# note\x85User zed", "# note User zed", "# note\x0bUser zed", "# note\x0cUser zed",
]


@pytest.mark.skipif(SSH is None, reason="needs the ssh client")
@pytest.mark.parametrize("line", USER_LINES)
def test_ari_reads_a_user_line_the_way_ssh_does(tmp_path, line):
    text = f"Host t\n    HostName 192.0.2.1\n{line}\n"
    config = tmp_path / "c"
    config.write_text(text)
    resolved = subprocess.run([SSH, "-G", "-F", str(config), "t"], capture_output=True, text=True)
    host, warnings = one(text)
    if resolved.returncode != 0:
        assert any("ssh refuses this line" in w for w in warnings), resolved.stderr
    else:
        user = next(l[5:] for l in resolved.stdout.splitlines() if l.startswith("user "))
        assert (host.user or getpass.getuser()) == user and warnings == []
