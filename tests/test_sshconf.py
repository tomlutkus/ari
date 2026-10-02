import getpass

from conftest import FIXTURES
from ari import sshconf
from ari.models import Defaults, Inventory


def hosts_from(text, source="test"):
    blocks, warnings = sshconf.parse(text, source)
    return [h for b in blocks if (h := sshconf.to_host(b, source, warnings))], warnings


def test_fixture_reads_cleanly():
    hosts, warnings = hosts_from((FIXTURES / "personal.conf").read_text())
    assert warnings == []
    assert [h.name for h in hosts] == ["github.com", "laptop", "nas", "vps"]
    nas = hosts[2]
    assert nas.aliases == ["storage", "192.0.2.254"]
    assert (nas.hostname, nas.user, nas.port, nas.ssh_key) == ("192.0.2.254", "root", 22, "~/.ssh/personal-ed25519")
    assert nas.ssh_options == {}


def test_extra_options_keep_order():
    hosts, _ = hosts_from((FIXTURES / "personal.conf").read_text())
    assert list(hosts[3].ssh_options.items()) == [("RequestTTY", "yes"), ("RemoteCommand", "sudo -i")]


def test_skips_patterns_match_include_and_globals():
    text = """
ServerAliveInterval 30
Include config.d/*.conf
Host *
    User nobody
Match host foo
    User nobody
Host real
    HostName 192.0.2.1
"""
    hosts, warnings = hosts_from(text)
    assert [h.name for h in hosts] == ["real"]
    assert hosts[0].user == getpass.getuser()
    assert len(warnings) == 4


def test_equals_form_and_first_value_wins():
    hosts, warnings = hosts_from("Host a\n  HostName=192.0.2.5\n  User=x\n  User y\n")
    assert (hosts[0].hostname, hosts[0].user) == ("192.0.2.5", "x")
    assert any("second User" in w for w in warnings)


def test_key_without_identities_only_keeps_ssh_default():
    hosts, _ = hosts_from("Host a\n  HostName 192.0.2.5\n  IdentityFile ~/.ssh/k\n")
    assert hosts[0].ssh_options == {"IdentitiesOnly": "no"}
    inventory = Inventory("t", FIXTURES / "t.json", hosts=hosts)
    rendered = sshconf.render(inventory)
    assert "IdentitiesOnly no" in rendered and "IdentitiesOnly yes" not in rendered


def test_render_fills_in_defaults():
    hosts, _ = hosts_from("Host a b\n  HostName 192.0.2.5\n  Port 2222\n")
    h = hosts[0]
    h.user = None
    inventory = Inventory("t", FIXTURES / "t.json", defaults=Defaults(user="tom", ssh_key="~/.ssh/k"), hosts=[h])
    assert sshconf.render(inventory).splitlines()[2:] == [
        "Host a b",
        "    HostName 192.0.2.5",
        "    User tom",
        "    Port 2222",
        "    IdentityFile ~/.ssh/k",
        "    IdentitiesOnly yes",
    ]
