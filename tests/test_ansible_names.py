"""What the Ansible files hold must read back in Ansible as the record says. A host name Ansible
reads as a range or as host:port is refused wherever the ansible module writes the host, and a group
description or reason YAML can't carry in a comment is refused by ari group and by export."""

import json
import shutil
import subprocess

import pytest
import yaml

from conftest import PERSONAL_ONLY, write_config
from ari import core
from ari.cli import main
from ari.config import load_config
from ari.models import _UNPRINTABLE, unprintable
from ari.modules.ansible import name_problem, scalar

ANSIBLE_INVENTORY = shutil.which("ansible-inventory")
needs_ansible = pytest.mark.skipif(ANSIBLE_INVENTORY is None, reason="needs ansible-inventory")

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


def path(home, inventory="work"):
    return home / "config" / "ari" / f"{inventory}.json"


def stored(home, inventory="work") -> dict:
    return {h["name"]: h for h in json.loads(path(home, inventory).read_text())["hosts"]}


def write(home, hosts, groups=None, inventory="work"):
    write_config(home, WITH_ANSIBLE)
    data = {"version": 3, "defaults": {}, "keys": {}, "groups": groups or {}, "hosts": hosts}
    path(home, inventory).write_text(json.dumps(data))


def listed(directory) -> subprocess.CompletedProcess:
    return subprocess.run([ANSIBLE_INVENTORY, "-i", str(directory), "--list"], capture_output=True, text=True, stdin=subprocess.DEVNULL)


# Names: a range, or a host and a port, to Ansible

REFUSED_NAMES = ["db.lab.example:2222", "db:22", "192.0.2.5:22", "x:0", "a_b:22", "é:22", "1.2.3:22", "300.1.1.1:22",
                 "web[01:02].lab.example", "app[1:2]", "web[a:c]", "web[1]", "x[", "[x]", "web-[1:2]"]
KEPT_NAMES = ["db", "db:x", "a:b:c", "2001:db8::1", "fe80::1", "fe80::1:22", "x]", "a]b", "a]:22", ":22", "a@b:22", "-x:22",
              "a/b:22", "x.:22", ".x:22", "a..b:22", "x_:22", "x:22:33", "a%b", "nas.lan", "host-1"]


@pytest.mark.parametrize("name", REFUSED_NAMES)
def test_a_name_ansible_reads_as_something_else_is_unwritable(name):
    assert name_problem(name) is not None


@pytest.mark.parametrize("name", KEPT_NAMES)
def test_a_name_ansible_reads_as_itself_is_writable(name):
    assert name_problem(name) is None


@needs_ansible
@pytest.mark.parametrize("name", REFUSED_NAMES + KEPT_NAMES)
def test_ansible_reads_each_name_as_the_rule_says(tmp_path, name):
    """The judge is ansible-inventory, one name to a file, since a bad one makes it skip the file."""
    (tmp_path / "00-hosts.yml").write_text(f"all:\n  hosts:\n    {json.dumps(name)}:\n      ansible_host: 192.0.2.9\n")
    result = listed(tmp_path)
    hostvars = json.loads(result.stdout)["_meta"]["hostvars"] if result.returncode == 0 else {}
    itself = hostvars == {name: {"ansible_host": "192.0.2.9"}}
    assert itself == (name_problem(name) is None), (result.stderr, hostvars)


def test_add_refuses_such_a_name_where_ansible_writes_it(home, capsys):
    write_config(home, WITH_ANSIBLE)
    assert run("add", "-i", "work", "db:2222", "192.0.2.5") == 1
    assert "work (db:2222): name 'db:2222' can't be written: Ansible reads it as host db on port 2222" in capsys.readouterr().err
    assert run("add", "-i", "work", "web[01:02]", "192.0.2.6") == 1
    assert "Ansible reads [ as the start of a range" in capsys.readouterr().err
    assert not path(home).exists()


def test_the_refusal_names_the_name_field(home):
    write_config(home, WITH_ANSIBLE)
    with pytest.raises(core.HostRefused) as refused:
        core.add_host(load_config(), "work", "db:22", "192.0.2.5", core.Changes())
    assert [p.field for p in refused.value.problems] == ["name"]


def test_an_inventory_without_ansible_or_a_host_that_excludes_it_keeps_the_name(home):
    write_config(home, WITH_ANSIBLE)
    assert run("add", "-i", "personal", "db:2222", "192.0.2.5") == 0
    assert run("add", "-i", "work", "app[1:2]", "192.0.2.6", "--exclude", "ansible") == 0
    assert run("export") == 0
    assert "app[1:2]" not in (home / "ssh" / "ansible" / "00-hosts.yml").read_text()


def test_edit_refuses_such_a_name_by_rename_or_include(home, capsys):
    write_config(home, WITH_ANSIBLE)
    assert run("add", "-i", "work", "db", "192.0.2.5") == 0
    assert run("add", "-i", "work", "app[1:2]", "192.0.2.6", "--exclude", "ansible") == 0
    assert run("edit", "db", "--rename", "db:22") == 1
    assert run("edit", "app[1:2]", "--include", "ansible") == 1
    assert sorted(stored(home)) == ["app[1:2]", "db"]


def test_ansible_import_refuses_such_names_and_keeps_the_rest_unadopted(home, tmp_path, capsys):
    """H6: these imported cleanly, and export wrote db.lab.example:2222 back with ansible_host
    db.lab.example:2222, which Ansible read as a host db.lab.example on port 2222."""
    write_config(home, WITH_ANSIBLE)
    source = tmp_path / "inv"
    source.mkdir()
    (source / "00-hosts.yml").write_text(
        "all:\n  hosts:\n    db.lab.example:2222:\n    web[01:02].lab.example:\n    good:\n      ansible_host: 192.0.2.70\n"
    )
    assert run("-i", "work", "import", "ansible", str(source)) == 1
    err = capsys.readouterr().err
    assert "name 'db.lab.example:2222' can't be written" in err and "name 'web[01:02].lab.example' can't be written" in err
    assert "not adopted" in err
    assert list(stored(home)) == ["good"]


def test_export_refuses_such_a_name_where_ansible_writes_it(home, capsys):
    write(home, [{"name": "db:22", "hostname": "192.0.2.5"}, {"name": "web", "hostname": "192.0.2.6"}])
    assert run("export", "ssh") == 0  # only the ssh files, which take the name as it is
    assert run("export") == 1
    assert "work (db:22): name 'db:22' can't be written" in capsys.readouterr().err
    assert not (home / "ssh" / "ansible").exists()


# Descriptions and reasons: comments, which YAML reads too

BAD = ["\x1b", "\x00", "\x01", "\x08", "\x0e", "\x1f", "\x7f", "\x80", "\x9f", "￾", "￿", "\ud800"]
GOOD = ["\t", "\xa0", "﻿", "", "�", "\U0001f41d", "é"]
BREAKS = ["\n", "\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " "]


def test_the_set_is_the_one_yaml_refuses():
    assert _UNPRINTABLE.pattern == yaml.reader.Reader.NON_PRINTABLE.pattern


@pytest.mark.parametrize("ch", BAD)
def test_a_character_yaml_refuses_is_unprintable(ch):
    assert unprintable(f"a{ch}b") == [ch]


@pytest.mark.parametrize("ch", GOOD + BREAKS)
def test_a_character_yaml_carries_or_a_line_break_is_not(ch):
    assert unprintable(f"a{ch}b") == []


@pytest.mark.parametrize("option", ["--description", "--reason"])
def test_ari_group_refuses_what_yaml_cant_carry_and_saves_nothing(home, capsys, option):
    """H8: an ESC in a zone header made ansible-inventory skip the whole hosts file."""
    write_config(home, WITH_ANSIBLE)
    value = "app subnet\x1b[0m" if option == "--description" else "k=old\x7f path"
    assert run("group", "-i", "work", "zone_a", option, value) == 1
    err = capsys.readouterr().err
    assert "holds '\\x1b'" in err or "holds '\\x7f'" in err
    assert "which YAML can't carry, so Ansible would skip the whole file" in err
    assert not path(home).exists()


def test_ari_group_keeps_a_line_break_yaml_reads_as_one(home):
    write_config(home, WITH_ANSIBLE)
    assert run("group", "-i", "work", "zone_a", "--description", "a b: c", "--reason", "k=one\x85two") == 0


@needs_ansible
def test_export_refuses_what_yaml_cant_carry_and_ansible_reads_the_rest(home, capsys):
    groups = {"zone_a": {"description": "app\x1b[0m", "reasons": {"k": "ok"}}, "zone_b": {"reasons": {"r": "old\x80"}}}
    hosts = [{"name": "h1", "hostname": "192.0.2.80", "groups": ["zone_a"], "reasons": {"zone_a": "k"}}]
    write(home, hosts, groups)
    assert run("export") == 1
    err = capsys.readouterr().err
    assert "work/ansible: group 'zone_a' description holds '\\x1b'" in err
    assert "work/ansible: group 'zone_b' reason 'r' holds '\\x80'" in err
    groups["zone_a"]["description"] = "app subnet"
    groups["zone_b"]["reasons"]["r"] = "old"
    write(home, hosts, groups)
    assert run("export") == 0
    result = listed(home / "ssh" / "ansible")
    assert "Unable to parse" not in result.stderr
    assert json.loads(result.stdout)["zone_a"]["hosts"] == ["h1"]


def test_an_inventory_without_ansible_never_gets_the_check(home):
    write_config(home, WITH_ANSIBLE)
    path(home, "personal").write_text(json.dumps({"version": 3, "groups": {"g": {"description": "x\x1b"}}, "hosts": []}))
    assert run("export", "-i", "personal") == 0


@pytest.mark.parametrize("ch", BAD[:-1] + GOOD + BREAKS)
def test_a_value_the_emitter_writes_reads_back_whole(ch):
    """Notes and names go out as scalars, which escape what YAML can't carry."""
    value = f"n{ch}x"
    assert yaml.safe_load(scalar(value)) == value


@needs_ansible
def test_ansible_reads_a_note_holding_such_characters_back_whole(home):
    notes = "a\x1bb\x7fc\x80d￾e f"
    write(home, [{"name": "h1", "hostname": "192.0.2.80", "notes": notes}])
    assert run("export") == 0
    result = listed(home / "ssh" / "ansible")
    assert json.loads(result.stdout)["_meta"]["hostvars"]["h1"]["description"] == notes
