import json
import shutil
import stat
import subprocess

import pytest
import yaml

from conftest import FIXTURES, write_config
from ari.cli import main
from ari.errors import HostsError
from ari.models import Defaults, GroupDef, Host, Inventory
from ari.modules import registry
from ari.modules.ansible import AnsibleModule, Settings, render_groups, render_hosts, scalar, sections
from ari.modules.ssh import SshModule

WORK = """
default = "work"

[inventories.work.ssh]
path = "SSH/20-work.conf"

[inventories.work.ansible]
dir = "SSH/ansible"
zones = "zone_*"

[inventories.work.ansible.groups]
"10-zones.yml" = "zone_*"
"40-roles.yml" = ["role_*", "do_not_touch"]
"60-monitoring.yml" = "monitoring_*"
"70-lifecycle.yml" = ["lifecycle_*", "no_auto_update"]
"""

FILES = ["00-hosts.yml", "10-zones.yml", "40-roles.yml", "60-monitoring.yml", "70-lifecycle.yml"]


def run(*argv):
    return main(list(argv))


def work_json(home):
    return home / "config" / "ari" / "work.json"


def hand_fill(home):
    """Migration step 6: what pyyaml drops, put back by hand."""
    path = work_json(home)
    data = json.loads(path.read_text())
    groups = data["groups"]
    groups["zone_app"]["description"] = "app subnet (192.0.2.0/25)"
    groups["zone_mgmt"]["description"] = (
        "mgmt subnet (192.0.2.128/25)\nnode hosts connect as deploy for ansible. Root admin uses separate ssh aliases."
    )
    groups["zone_cloud"]["description"] = "cloud (public)"
    groups["no_auto_update"]["reasons"] = {
        "secrets": "secrets and prod path",
        "remote": "remote access path, reboot kills the session running the play",
    }
    path.write_text(json.dumps(data))
    for name, reason in (("vault-01", "secrets"), ("vault-02", "secrets"), ("jump", "remote")):
        assert run("edit", name, "--group", f"no_auto_update:{reason}") == 0


@pytest.fixture
def work(home, capsys):
    """The fixture inventory copied into place, configured, imported and filled in."""
    write_config(home, WORK)
    shutil.copytree(FIXTURES / "ansible", home / "ssh" / "ansible")
    assert run("import", "ansible", str(home / "ssh" / "ansible")) == 0
    hand_fill(home)
    capsys.readouterr()
    return home


def settings(**kw):
    base = dict(dir=FIXTURES, hosts="00-hosts.yml", zones=("zone_*",), routes=(("10-zones.yml", ("zone_*",)),))
    base.update(kw)
    return Settings(**base)


# Emitter


@pytest.mark.parametrize(
    "value, expected",
    [
        ("192.0.2.10", "192.0.2.10"),
        ("~/.ssh/lab-ed25519", "~/.ssh/lab-ed25519"),
        ("front end", "front end"),
        ("storage: backups", '"storage: backups"'),
        ("a #b", '"a #b"'),
        ("yes", '"yes"'),
        ("123", '"123"'),
        ("", '""'),
        ("~", '"~"'),
        ("line\nbreak", '"line\\nbreak"'),
        (" padded", '" padded"'),
        (22, "22"),
    ],
)
def test_scalars_are_plain_only_when_they_read_back_the_same(value, expected):
    assert scalar(value) == expected
    assert yaml.safe_load(f"k: {scalar(value)}")["k"] == value


def test_sections_follow_zone_order_then_address():
    inventory = Inventory("t", FIXTURES / "t.json", groups={"zone_b": GroupDef(), "zone_a": GroupDef()})
    hosts = [
        Host("v6", "2001:db8::1", groups=["zone_a"]),
        Host("named", "box.example.net", groups=["zone_a"]),
        Host("high", "192.0.2.200", groups=["zone_a"]),
        Host("low", "192.0.2.9", groups=["zone_a"]),
        Host("other", "198.51.100.1", groups=["zone_b"]),
    ]
    parts = sections(inventory, hosts, settings())
    assert [(z, [h.name for h in m]) for z, m in parts] == [
        ("zone_b", ["other"]),
        ("zone_a", ["low", "high", "v6", "named"]),
    ]


def test_header_pads_to_62_and_falls_back_to_the_group_name():
    inventory = Inventory("t", FIXTURES / "t.json", groups={"zone_a": GroupDef("app subnet (192.0.2.0/25)"), "zone_b": GroupDef()})
    hosts = [Host("a", "192.0.2.1", groups=["zone_a"]), Host("b", "192.0.2.2", groups=["zone_b"])]
    text = render_hosts(inventory, sections(inventory, hosts, settings()))
    headers = [line.strip() for line in text.splitlines() if "──" in line]
    assert [len(h) for h in headers] == [62, 62]
    assert headers[1].startswith("# ── zone_b ")


def test_host_vars_only_where_they_differ():
    defaults = Defaults(user="deploy", port=22, ssh_key="~/.ssh/k")
    inventory = Inventory("t", FIXTURES / "t.json", defaults=defaults)
    hosts = [Host("a", "192.0.2.1", user="deploy", port=22, ssh_key="~/.ssh/k"), Host("b", "192.0.2.2", user="admin", port=2222)]
    loaded = yaml.safe_load(render_hosts(inventory, sections(inventory, hosts, settings(zones=()))))
    assert loaded["all"]["vars"] == {"ansible_user": "deploy", "ansible_ssh_private_key_file": "~/.ssh/k", "ansible_port": 22}
    assert loaded["all"]["hosts"] == {
        "a": {"ansible_host": "192.0.2.1"},
        "b": {"ansible_host": "192.0.2.2", "ansible_user": "admin", "ansible_port": 2222},
    }


def test_group_file_children_first_then_plain_hosts_then_reasons_in_declared_order():
    groups = {
        "parent": GroupDef(children=["child"]),
        "child": GroupDef(),
        "frozen": GroupDef(reasons={"r1": "first reason", "r2": "second\nreason"}),
    }
    inventory = Inventory("t", FIXTURES / "t.json", groups=groups)
    order = [
        Host("a", "192.0.2.1", groups=["frozen"], reasons={"frozen": "r2"}),
        Host("b", "192.0.2.2", groups=["frozen"]),
        Host("c", "192.0.2.3", groups=["frozen"], reasons={"frozen": "r1"}),
    ]
    assert render_groups(inventory, ["parent", "child", "frozen"], order) == (
        "all:\n  children:\n\n"
        "    parent:\n      children:\n        child:\n\n"
        "    child:\n\n"
        "    frozen:\n      hosts:\n        b:\n        # first reason\n        c:\n        # second\n        # reason\n        a:\n"
    )


# Settings and validation


@pytest.mark.parametrize(
    "table, message",
    [
        ({}, "needs dir"),
        ({"dir": "relative"}, "absolute"),
        ({"dir": "~/inv", "hosts": "hosts.txt"}, "ending in .yml"),
        ({"dir": "~/inv", "groups": {"00-hosts.yml": ["x"]}}, "is the hosts file"),
        ({"dir": "~/inv", "groups": {"sub/10.yml": ["x"]}}, "ending in .yml"),
        ({"dir": "~/inv", "groups": {"10.yml": []}}, "glob or a list of globs"),
        ({"dir": "~/inv", "zones": 5}, "glob or a list of globs"),
        ({"dir": "~/inv", "colour": "blue"}, "unknown keys"),
    ],
)
def test_bad_settings_explain_themselves(table, message):
    with pytest.raises(HostsError, match=message):
        AnsibleModule().settings(table, "config.toml: inventories.work.ansible")


def test_registered_through_its_entry_point():
    module = registry().get("ansible")
    assert isinstance(module, AnsibleModule) and module.imports and module.exports


def test_validation_routing_zones_and_names():
    groups = {name: GroupDef() for name in ("zone_a", "zone_b", "role_x", "stray", "bad-name")}
    inventory = Inventory("t", FIXTURES / "t.json", groups=groups)
    s = settings(routes=(("10.yml", ("zone_*",)), ("40.yml", ("role_*", "zone_b"))))
    hosts = [Host("none", "192.0.2.1"), Host("both", "192.0.2.2", groups=["zone_a", "zone_b"]), Host("ok", "192.0.2.3", groups=["zone_a"])]
    problems = AnsibleModule().validate(inventory, hosts, s)
    assert "group 'stray' matches no file in the groups table" in problems
    assert "group 'zone_b' matches more than one file: 10.yml, 40.yml" in problems
    assert "group 'bad-name' isn't a usable Ansible group name (letters, digits, _)" in problems
    assert "none is in no zone" in problems
    assert "both is in more than one zone: zone_a, zone_b" in problems
    assert not any(p.startswith("ok ") for p in problems)


# Import


def test_import_reads_hosts_defaults_groups_and_lists_comments():
    result = AnsibleModule().read(str(FIXTURES / "ansible"))
    assert result.defaults == Defaults(user="deploy", port=22, ssh_key="~/.ssh/lab-ed25519")
    by_name = {h.name: h for h in result.hosts}
    assert by_name["store1"].notes == "storage: backups"
    assert by_name["store1"].groups == ["zone_mgmt", "role_standalone", "do_not_touch", "monitoring_infra"]
    assert (by_name["jump"].port, by_name["status"].user, by_name["mirror"].hostname) == (2222, "admin", "mirror.example.net")
    assert list(result.groups)[:5] == ["zone_app", "zone_mgmt", "zone_cloud", "role_node", "role_cluster"]
    assert result.groups["role_node"].children == ["role_cluster", "role_standalone"]
    assert result.settings["groups"]["70-lifecycle.yml"] == ["lifecycle_frozen", "no_auto_update"]
    comments = [w for w in result.warnings if "comment not imported" in w]
    assert len(comments) == 7
    assert any("40-roles.yml:17: comment not imported: # old-store:" in w for w in comments)


def test_import_reports_what_it_leaves_out(tmp_path):
    (tmp_path / "00-hosts.yml").write_text(
        "all:\n  vars:\n    ansible_become: true\n  hosts:\n"
        "    a:\n      ansible_host: 192.0.2.1\n      ansible_python_interpreter: /usr/bin/python3\n"
        "    b:\n      ansible_host: 192.0.2.2\n      ansible_port: 99999\n"
    )
    (tmp_path / "10-groups.yml").write_text(
        "all:\n  children:\n    web:\n      vars:\n        x: 1\n      hosts:\n        a:\n        ghost:\n"
        "    outer:\n      children:\n        inner:\n          hosts:\n            b:\n        named_only:\n"
    )
    result = AnsibleModule().read(str(tmp_path))
    text = "\n".join(result.warnings)
    assert "all.vars.ansible_become not imported" in text
    assert "ansible_python_interpreter not imported" in text
    assert "port must be an integer 1-65535" in text
    assert "(web): vars not imported" in text
    assert "web lists ghost, which no hosts section defines; left out" in text
    assert list(result.groups) == ["web", "outer", "inner", "named_only"]
    assert {h.name: h.groups for h in result.hosts} == {"a": ["web"], "b": ["inner"]}


@pytest.mark.parametrize(
    "setup, message",
    [
        (lambda p: None, "no .yml or .yaml files"),
        (lambda p: (p / "x.yml").write_text("all: [\n"), "not valid YAML"),
        (lambda p: (p / "x.yml").write_text("- a\n"), "expected a mapping"),
    ],
)
def test_import_refuses_what_it_cant_read(tmp_path, setup, message):
    setup(tmp_path)
    with pytest.raises(HostsError, match=message):
        AnsibleModule().read(str(tmp_path))


# Through the CLI


def test_import_prints_the_config_table_until_it_is_configured(home, capsys):
    write_config(home, '[inventories.work.ssh]\npath = "SSH/20-work.conf"\n')
    assert run("-i", "work", "import", "ansible", str(FIXTURES / "ansible")) == 0
    out = capsys.readouterr().out
    assert "work: defaults set from the source: user deploy, port 22, ssh_key ~/.ssh/lab-ed25519" in out
    assert "11 groups declared" in out
    assert '[inventories.work.ansible.groups]\n"10-zones.yml" = ["zone_app", "zone_mgmt", "zone_cloud"]' in out
    write_config(home, WORK)
    assert run("import", "ansible", str(FIXTURES / "ansible")) == 0
    assert "[inventories.work.ansible]" not in capsys.readouterr().out


def test_first_export_writes_the_expected_files_then_stays_quiet(work, capsys):
    assert run("export") == 0
    out = capsys.readouterr().out
    assert "unchanged" in out and "00-hosts.yml (work, ansible, 10 hosts)" in out
    for name in FILES:
        assert (work / "ssh" / "ansible" / name).read_text() == (FIXTURES / "ansible.expected" / name).read_text(), name
    assert run("export") == 0
    assert capsys.readouterr().out.count("unchanged") == 6


def test_ssh_side_of_the_same_records(work):
    run("export")
    conf = (work / "ssh" / "20-work.conf").read_text()
    assert "Host jump\n    HostName 192.0.2.150\n    User deploy\n    Port 2222\n" in conf
    assert "Host status\n    HostName 198.51.100.20\n    User admin\n" in conf


def test_excluded_hosts_leave_every_ansible_file(work):
    assert run("edit", "status", "--exclude", "ansible") == 0
    run("export")
    for name in FILES:
        assert "status" not in (work / "ssh" / "ansible" / name).read_text().replace("# ── cloud", ""), name
    assert "Host status" in (work / "ssh" / "20-work.conf").read_text()


def test_export_refuses_an_unzoned_host_and_writes_nothing(work, capsys):
    before = {n: (work / "ssh" / "ansible" / n).read_bytes() for n in FILES}
    assert run("add", "loose", "192.0.2.99") == 0
    assert run("export") == 1
    assert "work/ansible: loose is in no zone" in capsys.readouterr().err
    assert {n: (work / "ssh" / "ansible" / n).read_bytes() for n in FILES} == before
    assert not (work / "ssh" / "20-work.conf").exists()


def test_a_failing_module_check_stops_every_module_before_it_renders(work, monkeypatch, capsys):
    def export(*args):
        raise AssertionError("module.export called after validation failed")

    monkeypatch.setattr(AnsibleModule, "export", export)
    monkeypatch.setattr(SshModule, "export", export)
    assert run("add", "loose", "192.0.2.99") == 0
    assert run("export") == 1
    assert "work/ansible: loose is in no zone" in capsys.readouterr().err


def modes(home):
    paths = [home / "ssh" / "ansible" / n for n in FILES] + [home / "ssh" / "20-work.conf", work_json(home)]
    return {p.name: stat.S_IMODE(p.stat().st_mode) for p in paths}


def test_ansible_files_are_0644_ssh_config_and_inventory_0600(work):
    assert run("export") == 0
    assert modes(work) == {**dict.fromkeys(FILES, 0o644), "20-work.conf": 0o600, "work.json": 0o600}


def test_export_fixes_the_mode_of_files_it_leaves_unchanged(work, capsys):
    run("export")
    directory = work / "ssh" / "ansible"
    before = {n: (directory / n).read_bytes() for n in FILES}
    for name in FILES:
        (directory / name).chmod(0o600)
    (work / "ssh" / "20-work.conf").chmod(0o644)
    capsys.readouterr()
    assert run("export") == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 6 and all(line.startswith("unchanged ") for line in lines)
    assert sum(line.endswith("; mode set to 0644") for line in lines) == 5
    assert sum(line.endswith("; mode set to 0600") for line in lines) == 1
    assert modes(work) == {**dict.fromkeys(FILES, 0o644), "20-work.conf": 0o600, "work.json": 0o600}
    assert {n: (directory / n).read_bytes() for n in FILES} == before
    assert run("export") == 0
    assert "mode set" not in capsys.readouterr().out


def test_a_refused_export_leaves_modes_alone(work, capsys):
    run("export")
    directory = work / "ssh" / "ansible"
    (directory / "00-hosts.yml").chmod(0o600)
    (directory / "10-zones.yml").write_text((directory / "10-zones.yml").read_text() + "# hand edit\n")
    assert run("export") == 1
    assert "edited since the last export" in capsys.readouterr().err
    assert stat.S_IMODE((directory / "00-hosts.yml").stat().st_mode) == 0o600


def test_new_host_lands_in_its_zone_and_groups(work):
    assert run("add", "web-03", "192.0.2.12", "--group", "zone_app", "--group", "monitoring_web", "--notes", "front end") == 0
    run("export")
    hosts = (work / "ssh" / "ansible" / "00-hosts.yml").read_text()
    assert "    web-02:\n      ansible_host: 192.0.2.11\n      description: front end\n    web-03:\n" in hosts
    assert "        web-02:\n        web-03:\n        status:\n" in (work / "ssh" / "ansible" / "60-monitoring.yml").read_text()


def test_import_merges_groups_into_hosts_from_ssh(home, tmp_path, capsys):
    write_config(home, WORK)
    ssh = tmp_path / "work.conf"
    ssh.write_text("Host jump 192.0.2.150\n    HostName 192.0.2.150\n    User deploy\n    Port 2222\n")
    run("import", "ssh", str(ssh))
    assert run("import", "ansible", str(FIXTURES / "ansible")) == 0
    out = capsys.readouterr().out
    assert "1 merged (jump)" in out
    jump = next(h for h in json.loads(work_json(home).read_text())["hosts"] if h["name"] == "jump")
    assert jump["aliases"] == ["192.0.2.150"]
    assert jump["groups"] == ["zone_mgmt", "monitoring_infra", "no_auto_update"]


@pytest.mark.skipif(shutil.which("ansible-inventory") is None, reason="needs ansible-inventory")
def test_ansible_sees_the_same_inventory(work):
    """The Ansible counterpart of the ssh -G proof: same hosts, vars and groups before and after."""
    run("export")

    def listing(directory):
        done = subprocess.run(["ansible-inventory", "-i", str(directory), "--list"], capture_output=True, text=True, check=True)
        return json.loads(done.stdout)

    assert listing(FIXTURES / "ansible") == listing(work / "ssh" / "ansible")


@pytest.mark.skipif(shutil.which("ansible-inventory") is None, reason="needs ansible-inventory")
def test_ansible_reads_empty_groups_and_files(tmp_path):
    inventory = Inventory("t", tmp_path / "t.json", groups={"empty": GroupDef(), "parent": GroupDef(children=["empty"])})
    (tmp_path / "a.yml").write_text(render_groups(inventory, ["empty", "parent"], []))
    (tmp_path / "b.yml").write_text(render_groups(inventory, [], []))
    (tmp_path / "c.yml").write_text(render_hosts(inventory, []))
    done = subprocess.run(["ansible-inventory", "-i", str(tmp_path), "--list"], capture_output=True, text=True, check=True)
    assert json.loads(done.stdout)["parent"]["children"] == ["empty"]
