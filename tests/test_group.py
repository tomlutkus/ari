import json

import pytest
from rich.cells import cell_len
from rich.console import Console

from conftest import FIXTURES, PERSONAL_ONLY, write_config
from ari import cli
from ari.cli import main


def run(*argv):
    return main(list(argv))


def inventory_path(home, name="personal"):
    return home / "config" / "ari" / f"{name}.json"


def groups(home, name="personal"):
    return json.loads(inventory_path(home, name).read_text())["groups"]


@pytest.fixture
def roles(personal, capsys):
    """role_node holds role_cluster and role_standalone. laptop and nas are in role_cluster, nas is
    also in role_node itself, and vps is in role_standalone with a reason from no_auto_update."""
    run("import", "ssh", str(FIXTURES / "personal.conf"))
    for argv in (
        ("group", "role_cluster"),
        ("group", "role_standalone"),
        ("group", "role_node", "--child", "role_cluster", "--child", "role_standalone"),
        ("group", "no_auto_update", "--reason", "secrets=secrets and prod path", "--reason", "remote=remote access path"),
        ("edit", "laptop", "--group", "role_cluster"),
        ("edit", "nas", "--group", "role_cluster", "--group", "role_node"),
        ("edit", "vps", "--group", "role_standalone", "--group", "no_auto_update:remote"),
    ):
        assert run(*argv) == 0
    capsys.readouterr()
    return personal


def test_declare_then_change_each_part(personal, capsys):
    assert run("group", "backups") == 0
    assert "declared backups in personal" in capsys.readouterr().out
    assert groups(personal) == {"backups": {}}

    assert run("group", "backups", "--description", "nightly\nto the nas", "--reason", "a=first", "--reason", "b=second") == 0
    assert "updated backups (personal)" in capsys.readouterr().out
    assert groups(personal)["backups"] == {"description": "nightly\nto the nas", "reasons": {"a": "first", "b": "second"}}

    assert run("group", "backups", "--reason", "a=changed", "--description", "") == 0
    assert groups(personal)["backups"] == {"reasons": {"a": "changed", "b": "second"}}


def test_a_new_group_is_usable_at_once(personal, capsys):
    run("import", "ssh", str(FIXTURES / "personal.conf"))
    assert run("edit", "vps", "--group", "web") == 1
    assert "ari group web -i personal declares it" in capsys.readouterr().err
    assert run("group", "web", "--reason", "edge=public edge") == 0
    assert run("edit", "vps", "--group", "web:edge") == 0


def test_list_counts_hosts_through_children(roles, monkeypatch, capsys):
    monkeypatch.setattr(cli, "out", Console(width=200, highlight=False))
    assert run("group") == 0
    rows = {line.split()[0]: line for line in capsys.readouterr().out.splitlines() if line.strip()}
    assert "3 (1 direct)" in rows["role_node"] and "role_cluster, role_standalone" in rows["role_node"]
    assert rows["role_cluster"].split()[1] == "2"
    assert "secrets, remote" in rows["no_auto_update"]


def test_list_never_wraps_at_80_columns(roles, monkeypatch, capsys):
    monkeypatch.setattr(cli, "out", Console(width=80, highlight=False))
    run("group", "role_node", "--description", "proxmox nodes, clustered or standalone, " * 3)
    capsys.readouterr()
    assert run("group") == 0
    lines = capsys.readouterr().out.splitlines()
    assert all(cell_len(line) <= 80 for line in lines)
    assert sum("role_standalone" in line for line in lines) == 1


def test_list_covers_every_inventory_unless_narrowed(both, capsys):
    run("group", "backups")
    run("-i", "work", "group", "zone_app")
    capsys.readouterr()
    assert run("group") == 0
    listing = capsys.readouterr().out
    assert "backups" in listing and "zone_app" in listing
    assert run("group", "-i", "work") == 0
    listing = capsys.readouterr().out
    assert "zone_app" in listing and "backups" not in listing


def test_rm_names_every_host_and_how_it_gets_there(roles, capsys):
    assert run("group", "role_node", "--rm") == 1
    err = capsys.readouterr().err
    assert "'role_node' still has 1 host: nas" in err
    assert "still has 1 host through its child 'role_cluster': laptop" in err
    assert "still has 1 host through its child 'role_standalone': vps" in err


def test_rm_a_child_names_its_parents(roles, capsys):
    assert run("group", "role_standalone", "--rm") == 1
    err = capsys.readouterr().err
    assert "'role_standalone' still has 1 host: vps" in err
    assert "is a child of 'role_node'; --unchild it there first" in err


def test_unchild_is_refused_only_for_hosts_it_would_drop(roles, capsys):
    assert run("group", "role_node", "--unchild", "role_cluster") == 1
    assert "'role_node' would lose 1 host it reaches only through 'role_cluster': laptop" in capsys.readouterr().err
    run("edit", "laptop", "--group", "role_node")
    assert run("group", "role_node", "--unchild", "role_cluster") == 0
    assert groups(roles)["role_node"]["children"] == ["role_standalone"]


def test_a_reason_in_use_stays(roles, capsys):
    assert run("group", "no_auto_update", "--reason", "remote=") == 1
    assert "reason 'remote' is still used by 1 host: vps" in capsys.readouterr().err
    assert run("group", "no_auto_update", "--reason", "secrets=") == 0
    assert groups(roles)["no_auto_update"]["reasons"] == {"remote": "remote access path"}


def test_rm_an_unused_group(roles, capsys):
    run("group", "spare")
    assert run("group", "spare", "--rm") == 0
    assert "removed spare from personal" in capsys.readouterr().out
    assert "spare" not in groups(roles)


@pytest.mark.parametrize(
    "argv, message",
    [
        (["group", "role_node", "--child", "nope"], "has child 'nope', which isn't declared"),
        (["group", "role_cluster", "--child", "role_node"], "form a cycle"),
        (["group", "role_node", "--unchild", "nope"], "has no child 'nope'"),
        (["group", "no_auto_update", "--reason", "gone="], "has no reason 'gone' to remove"),
        (["group", "bad name"], "can't be a group name"),
        (["group", "a:b"], "can't be a group name"),
        (["group", "role_node", "--rm", "--description", "x"], "--rm takes no other options"),
        (["group", "nope", "--rm"], "no group 'nope' in personal"),
        (["group", "--child", "x"], "need a group NAME"),
        (["group", "role_cluster"], None),
    ],
)
def test_refusals_and_no_ops_save_nothing(roles, capsys, argv, message):
    before = inventory_path(roles).read_bytes()
    if message is None:
        assert run(*argv) == 0
        assert "no change to role_cluster (personal)" in capsys.readouterr().out
    else:
        assert run(*argv) == 1
        assert message in capsys.readouterr().err
    assert inventory_path(roles).read_bytes() == before


ANSIBLE = PERSONAL_ONLY + """
[inventories.work.ansible]
dir = "SSH/ansible"
zones = "zone_*"

[inventories.work.ansible.groups]
"10-zones.yml" = ["zone_*"]
"70-lifecycle.yml" = ["no_auto_update"]
"""


def test_descriptions_and_reasons_reach_the_ansible_files(home):
    write_config(home, ANSIBLE)
    assert run("-i", "work", "group", "zone_app", "--description", "app subnet (192.0.2.0/25)\nbehind the edge proxy") == 0
    assert run("-i", "work", "group", "no_auto_update", "--reason", "secrets=secrets and prod path") == 0
    assert run("-i", "work", "add", "vault-01", "192.0.2.30", "--group", "zone_app", "--group", "no_auto_update:secrets") == 0
    assert run("export") == 0
    hosts = (home / "ssh" / "ansible" / "00-hosts.yml").read_text()
    assert "    # ── app subnet (192.0.2.0/25) ─" in hosts and "    # behind the edge proxy\n" in hosts
    lifecycle = (home / "ssh" / "ansible" / "70-lifecycle.yml").read_text()
    assert "        # secrets and prod path\n        vault-01:\n" in lifecycle
