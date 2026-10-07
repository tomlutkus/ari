"""ssh expands % tokens: %h and %% in HostName, and since OpenSSH 10 % and ${VAR} in User, from a
config or from Ansible's -o User. The record holds the address and the user as they are: export
writes a hostname's % as %%, and a user no spelling keeps the same on every ssh is refused."""

import json
import shutil
import subprocess

import pytest

from conftest import PERSONAL_ONLY, write_config
from ari import core
from ari.cli import main
from ari.config import load_config

SSH = shutil.which("ssh")
ANSIBLE_INVENTORY = shutil.which("ansible-inventory")
needs_ssh = pytest.mark.skipif(SSH is None, reason="needs the ssh client")

WITH_ANSIBLE = PERSONAL_ONLY + """
[inventories.work.ssh]
path = "SSH/20-work.conf"

[inventories.work.ansible]
dir = "SSH/ansible"

[inventories.work.ansible.groups]
"10-groups.yml" = ["*"]
"""


def run(*argv):
    return main(list(argv))


def path(home, inventory="personal"):
    return home / "config" / "ari" / f"{inventory}.json"


def stored(home, inventory="personal") -> dict:
    return {h["name"]: h for h in json.loads(path(home, inventory).read_text())["hosts"]}


def write(home, hosts, inventory="personal", defaults=None, config=PERSONAL_ONLY):
    """An inventory as a hand edit, or an earlier ari, leaves it."""
    write_config(home, config)
    data = {"version": 3, "defaults": defaults or {}, "keys": {}, "groups": {}, "hosts": hosts}
    path(home, inventory).write_text(json.dumps(data))


def resolve(config, name) -> subprocess.CompletedProcess:
    return subprocess.run([SSH, "-G", "-F", str(config), name], capture_output=True, text=True, stdin=subprocess.DEVNULL)


def hostname(config, name) -> str:
    resolved = resolve(config, name)
    assert resolved.returncode == 0, resolved.stderr
    return next(line[9:] for line in resolved.stdout.splitlines() if line.startswith("hostname "))


# HostName: the address as it stands, each % doubled on the way out


@needs_ssh
def test_a_hostname_with_a_percent_reaches_ssh_as_it_stands(home):
    """H5: fe80::1%eth0 went out bare, and ssh failed on it with unknown key %e."""
    write_config(home, PERSONAL_ONLY)
    assert run("add", "ll6", "fe80::1%eth0") == 0
    assert run("add", "good", "192.0.2.70") == 0
    assert run("export") == 0
    config = home / "ssh" / "10-personal.conf"
    assert "    HostName fe80::1%%eth0\n" in config.read_text()
    assert hostname(config, "ll6") == "fe80::1%eth0"
    assert hostname(config, "good") == "192.0.2.70"


@pytest.mark.skipif(ANSIBLE_INVENTORY is None, reason="needs ansible-inventory")
def test_ansible_gets_the_address_as_it_stands(home):
    """Ansible hands ansible_host to ssh on its command line, where ssh expands nothing."""
    write_config(home, WITH_ANSIBLE)
    assert run("add", "-i", "work", "ll6", "fe80::1%eth0") == 0
    assert run("export") == 0
    listed = subprocess.run([ANSIBLE_INVENTORY, "-i", str(home / "ssh" / "ansible"), "--list"],
                            capture_output=True, text=True, stdin=subprocess.DEVNULL, check=True)
    assert json.loads(listed.stdout)["_meta"]["hostvars"]["ll6"]["ansible_host"] == "fe80::1%eth0"


@needs_ssh
def test_import_reads_a_doubled_percent_back_and_the_round_trip_holds(home, tmp_path):
    write_config(home, PERSONAL_ONLY)
    source = tmp_path / "src.conf"
    source.write_text("Host ll6\n    HostName fe80::1%%eth0\n")
    assert run("import", "ssh", str(source)) == 0
    assert stored(home)["ll6"]["hostname"] == "fe80::1%eth0"
    assert run("export") == 0
    assert hostname(home / "ssh" / "10-personal.conf", "ll6") == hostname(source, "ll6") == "fe80::1%eth0"


@pytest.mark.parametrize("value", ["%h.lab.example", "fe80::1%eth0", "a%", "%%%h"])
def test_import_skips_a_hostname_with_ssh_tokens_and_keeps_the_file_unadopted(home, tmp_path, capsys, value):
    write_config(home, PERSONAL_ONLY)
    source = tmp_path / "src.conf"
    source.write_text(f"Host alpha beta\n    HostName {value}\n\nHost good\n    HostName 192.0.2.70\n")
    assert run("import", "ssh", str(source)) == 0
    err = capsys.readouterr().err
    assert f"HostName {value} uses ssh's % tokens, which a record can't hold; host skipped" in err and "not adopted" in err
    assert list(stored(home)) == ["good"]


@pytest.mark.parametrize("value", ["%h.lab.example", "a%%b", "fe80::1%hn0"])
def test_a_stored_hostname_with_ssh_tokens_is_refused_until_written_out(home, capsys, value):
    """An earlier ari stored %h from an import, where ssh read it as the name typed; written as it
    stands now, it would send ssh somewhere else, so it's refused rather than changed."""
    write(home, [{"name": "alpha", "hostname": value, "aliases": ["beta"]}])
    assert run("ls") == 0
    assert run("export") == 1
    err = capsys.readouterr().err
    assert f"personal (alpha): hostname {value!r} can't be written: ssh reads %h and %% in HostName as tokens" in err
    assert not (home / "ssh" / "10-personal.conf").exists()
    assert run("edit", "alpha", "--user", "ops") == 1
    assert run("edit", "alpha", "--hostname", "alpha.lab.example") == 0
    assert run("export") == 0


def test_add_refuses_a_hostname_holding_an_ssh_token_under_the_hostname(home):
    write_config(home, PERSONAL_ONLY)
    with pytest.raises(core.HostRefused) as refused:
        core.add_host(load_config(), "personal", "alpha", "%h.lab.example", core.Changes())
    assert [p.field for p in refused.value.problems] == ["hostname"]


def test_a_host_ssh_never_writes_keeps_such_a_hostname(home):
    write_config(home, PERSONAL_ONLY)
    assert run("add", "alpha", "%h.lab.example", "--exclude", "ssh") == 0


# User: OpenSSH 10 expands % and ${VAR} in it, and 9.x doesn't


@pytest.mark.parametrize("user", ["a%b", "%u", "a%%b", "${USER}", "x${HOME}"])
def test_add_and_edit_refuse_a_user_ssh_would_expand(home, capsys, user):
    write_config(home, PERSONAL_ONLY)
    assert run("add", "web", "192.0.2.70", "--user", user) == 1
    assert f"personal (web): user {user!r} can't be written: OpenSSH 10 expands % and ${{}} in User" in capsys.readouterr().err
    assert run("add", "web", "192.0.2.70") == 0
    assert run("edit", "web", "--user", user) == 1
    assert "user" not in stored(home)["web"]


@pytest.mark.parametrize("user", ["a$b", "a{b}", "$", "corp\\alice"])
def test_a_user_without_tokens_is_kept(home, user):
    write_config(home, PERSONAL_ONLY)
    assert run("add", "web", "192.0.2.70", "--user", user) == 0


def test_the_user_refusal_names_the_user_field(home):
    write_config(home, PERSONAL_ONLY)
    with pytest.raises(core.HostRefused) as refused:
        core.add_host(load_config(), "personal", "web", "192.0.2.70", core.Changes(user="a%b"))
    assert [p.field for p in refused.value.problems] == ["user"]


def test_the_rule_holds_where_no_module_writes_the_host(home, capsys):
    """Every module ari has hands the user to ssh, Ansible through -o User, so it's ari's own rule."""
    write_config(home, PERSONAL_ONLY)
    assert run("add", "web", "192.0.2.70", "--exclude", "ssh", "--user", "a%b") == 1


def test_ssh_import_refuses_a_user_ssh_would_expand(home, tmp_path, capsys):
    write_config(home, PERSONAL_ONLY)
    source = tmp_path / "src.conf"
    source.write_text("Host odd\n    HostName 192.0.2.71\n    User a%b\n\nHost good\n    HostName 192.0.2.70\n")
    assert run("import", "ssh", str(source)) == 1
    err = capsys.readouterr().err
    assert "user 'a%b' can't be written" in err and "not adopted" in err
    assert list(stored(home)) == ["good"]


def test_ansible_import_refuses_a_user_ssh_would_expand(home, tmp_path, capsys):
    write_config(home, WITH_ANSIBLE)
    source = tmp_path / "inv"
    source.mkdir()
    (source / "00-hosts.yml").write_text(
        "all:\n  hosts:\n    odd:\n      ansible_host: 192.0.2.71\n      ansible_user: '${USER}'\n"
        "    good:\n      ansible_host: 192.0.2.70\n"
    )
    assert run("-i", "work", "import", "ansible", str(source)) == 1
    assert "user '${USER}' can't be written" in capsys.readouterr().err
    assert list(stored(home, "work")) == ["good"]


def test_export_refuses_a_user_ssh_would_expand_the_defaults_included(home, capsys):
    write(home, [{"name": "web", "hostname": "192.0.2.70", "user": "a%b"}, {"name": "db", "hostname": "192.0.2.71"}],
          defaults={"user": "${USER}"})
    assert run("export") == 1
    err = capsys.readouterr().err
    assert "personal defaults: user '${USER}' can't be written" in err
    assert "personal (web): user 'a%b' can't be written" in err
    assert not (home / "ssh" / "10-personal.conf").exists()
