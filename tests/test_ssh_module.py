from conftest import FIXTURES
from ari.models import Defaults, Inventory, KeyDef
from ari.modules import registry
from ari.modules.ssh import SshModule, parse, render, to_hosts


def hosts_from(text, source="test"):
    """Hosts as a new import stores them, with what the blocks left unset filled in."""
    blocks, warnings = parse(text, source)
    hosts = [h for b in blocks for h in to_hosts(b, source, warnings)]
    for host in hosts:
        SshModule().complete(host)
    return hosts, warnings


def options(host):
    return host.modules.get("ssh", {}).get("options", {})


def test_registered_through_its_entry_point():
    module = registry().get("ssh")
    assert isinstance(module, SshModule) and module.imports and module.exports


def test_fixture_reads_cleanly():
    hosts, warnings = hosts_from((FIXTURES / "personal.conf").read_text())
    assert warnings == []
    assert [h.name for h in hosts] == ["github.com", "laptop", "nas", "vps"]
    nas = hosts[2]
    assert nas.aliases == ["storage", "192.0.2.254"]
    assert (nas.hostname, nas.user, nas.port, nas.keys) == ("192.0.2.254", "root", 22, ["personal-ed25519"])
    assert options(nas) == {}


def test_extra_options_keep_order():
    hosts, _ = hosts_from((FIXTURES / "personal.conf").read_text())
    assert list(options(hosts[3]).items()) == [("RequestTTY", "yes"), ("RemoteCommand", "sudo -i")]


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
    assert len(warnings) == 4


def test_missing_user_stays_missing_and_port_is_pinned():
    hosts, _ = hosts_from("Host a\n  HostName 192.0.2.5\n")
    assert hosts[0].user is None and hosts[0].port == 22


def test_what_a_block_leaves_out_stays_unset_until_complete():
    blocks, warnings = parse("Host a\n  User x\n", "test")
    [host] = to_hosts(blocks[0], "test", warnings)
    assert (host.hostname, host.port) == ("", None)
    SshModule().complete(host)
    assert (host.hostname, host.port) == ("a", 22)


def test_several_tokens_without_hostname_are_a_host_each():
    hosts, _ = hosts_from("Host web web.example.com\n  User deploy\n  Port 2200\n  ProxyJump j\n")
    assert [(h.name, h.hostname, h.aliases) for h in hosts] == [("web", "web", []), ("web.example.com", "web.example.com", [])]
    assert all((h.user, h.port, options(h)) == ("deploy", 2200, {"ProxyJump": "j"}) for h in hosts)


def test_with_hostname_the_other_tokens_are_aliases():
    hosts, _ = hosts_from("Host web web.example.com\n  HostName 192.0.2.10\n")
    assert [(h.name, h.hostname, h.aliases) for h in hosts] == [("web", "192.0.2.10", ["web.example.com"])]


def test_equals_form_and_first_value_wins():
    hosts, warnings = hosts_from("Host a\n  HostName=192.0.2.5\n  User=x\n  User y\n")
    assert (hosts[0].hostname, hosts[0].user) == ("192.0.2.5", "x")
    assert any("second User" in w for w in warnings)


def test_key_without_identities_only_keeps_ssh_default():
    hosts, _ = hosts_from("Host a\n  HostName 192.0.2.5\n  IdentityFile ~/.ssh/k\n")
    assert options(hosts[0]) == {"IdentitiesOnly": "no"}
    inventory = Inventory("t", FIXTURES / "t.json", hosts=hosts, keys={"k": KeyDef("~/.ssh/k")})
    rendered = render(inventory, inventory.hosts)
    assert "IdentitiesOnly no" in rendered and "IdentitiesOnly yes" not in rendered


def test_render_fills_in_defaults():
    hosts, _ = hosts_from("Host a b\n  HostName 192.0.2.5\n  Port 2222\n")
    inventory = Inventory("t", FIXTURES / "t.json", defaults=Defaults(user="tom", keys=["k"]), hosts=hosts, keys={"k": KeyDef("~/.ssh/k")})
    assert render(inventory, inventory.hosts).splitlines()[2:] == [
        "Host a b",
        "    HostName 192.0.2.5",
        "    User tom",
        "    Port 2222",
        "    IdentityFile ~/.ssh/k",
        "    IdentitiesOnly yes",
    ]


def test_no_user_anywhere_means_no_user_line():
    hosts, _ = hosts_from("Host a\n  HostName 192.0.2.5\n")
    inventory = Inventory("t", FIXTURES / "t.json", hosts=hosts)
    assert "User" not in render(inventory, inventory.hosts)


def test_default_options_merge_case_insensitively():
    hosts, _ = hosts_from("Host a\n  HostName 192.0.2.5\n  serveraliveinterval 10\n")
    defaults = Defaults(modules={"ssh": {"options": {"ServerAliveInterval": "30", "Compression": "yes"}}})
    inventory = Inventory("t", FIXTURES / "t.json", defaults=defaults, hosts=hosts)
    rendered = render(inventory, inventory.hosts)
    assert "serveraliveinterval 10" in rendered and "ServerAliveInterval 30" not in rendered
    assert "Compression yes" in rendered
