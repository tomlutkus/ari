"""Import keeps what the source says or says why it can't: refused hosts never shift which Host line
the next block is checked against, another spelling of a name ari holds isn't taken for it,
defaults are only ever what someone declared, and what Ansible reads beside the inventory files is
named."""

import json
import shutil
import subprocess

import pytest

from conftest import PERSONAL_ONLY, write_config
from ari.cli import main

ANSIBLE = shutil.which("ansible-inventory")
SSH = shutil.which("ssh")


def run(*argv):
    return main(list(argv))


def stored(home, inventory="personal") -> dict:
    return json.loads((home / "config" / "ari" / f"{inventory}.json").read_text())


def hosts(home, inventory="personal") -> dict:
    return {h["name"]: h for h in stored(home, inventory)["hosts"]}


def guard(home) -> dict:
    path = home / "state" / "ari" / "exports.json"
    return json.loads(path.read_text()) if path.exists() else {}


def adopted(home, path) -> bool:
    return str(path.resolve()) in guard(home)


def source(tmp_path, text, name="src.conf"):
    path = tmp_path / name
    path.write_text(text)
    return path


ANSIBLE_ONLY = """
default = "work"

[inventories.work.ansible]
dir = "SSH/ansible"
"""


# G3: a refused host never shifts the Host lines the next blocks are checked against


def test_a_refused_block_leaves_the_next_one_checked_against_its_own_host_line(home, tmp_path, capsys):
    write_config(home, PERSONAL_ONLY)
    assert run("add", "web", "192.0.2.10", "--alias", "www") == 0
    src = source(tmp_path, 'Host web www\n    HostName "bad host"\n\nHost web\n    User root\n')
    capsys.readouterr()
    assert run("import", "ssh", str(src)) == 1
    err = capsys.readouterr().err
    assert "hostname must be non-empty" in err
    assert "web: its Host line leaves out www, so ssh applies it to the rest alone; not merged" in err
    assert "user" not in hosts(home)["web"] and not adopted(home, src)


# G2: another spelling of a name ari holds is not that name


def test_two_spellings_of_one_name_in_a_source_keep_the_second_out(home, tmp_path, capsys):
    write_config(home, PERSONAL_ONLY)
    src = source(tmp_path, "Host Web\n    HostName 192.0.2.1\n    User alice\n\nHost web\n    HostName 192.0.2.1\n    User alice\n")
    capsys.readouterr()
    assert run("import", "ssh", str(src)) == 1
    out, err = capsys.readouterr()
    assert "personal: 1 added (Web)" in out and "unchanged" not in out
    assert "web: ssh and Ansible tell it apart from Web, the name ari holds, and ari can't keep both spellings; not imported" in err
    assert list(hosts(home)) == ["Web"] and not adopted(home, src)


def test_reimporting_a_name_in_another_case_is_not_unchanged(home, tmp_path, capsys):
    write_config(home, PERSONAL_ONLY)
    assert run("add", "web", "192.0.2.1", "--alias", "www") == 0
    for text, held in (("Host Web\n    HostName 192.0.2.1\n", "web, the name"), ("Host WWW\n    HostName 192.0.2.1\n", "www, an alias")):
        src = source(tmp_path, text)
        capsys.readouterr()
        assert run("import", "ssh", str(src)) == 1
        out, err = capsys.readouterr()
        token = text.split()[1]
        assert f"{token}: ssh and Ansible tell it apart from {held} ari holds, and ari can't keep both spellings; not imported" in err
        assert "unchanged" not in out and not adopted(home, src)


def test_two_spellings_in_an_ansible_inventory_keep_the_second_out(home, capsys):
    write_config(home, ANSIBLE_ONLY)
    inv = home / "ssh" / "ansible"
    inv.mkdir()
    (inv / "00-hosts.yml").write_text(
        "all:\n  hosts:\n    Web:\n      ansible_host: 192.0.2.1\n    web:\n      ansible_host: 192.0.2.1\n"
    )
    capsys.readouterr()
    assert run("import", "ansible", str(inv)) == 1
    assert "web: ssh and Ansible tell it apart from Web, the name ari holds" in capsys.readouterr().err
    assert list(hosts(home, "work")) == ["Web"] and not adopted(home, inv / "00-hosts.yml")


# H4: import never makes up a default


SHARED = """Host web1
    HostName 192.0.2.21
    User deploy
    IdentityFile ~/.ssh/deploy_ed25519
Host web2
    HostName 192.0.2.22
    User deploy
    IdentityFile ~/.ssh/deploy_ed25519
Host box
    HostName 192.0.2.23
"""


def test_an_ssh_import_infers_no_defaults(home, tmp_path, capsys):
    write_config(home, PERSONAL_ONLY)
    src = source(tmp_path, SHARED)
    capsys.readouterr()
    assert run("import", "ssh", str(src)) == 0
    out = capsys.readouterr().out
    assert "defaults set" not in out
    data = stored(home)
    assert data["defaults"] == {}
    assert [(h["name"], h.get("user"), h.get("keys")) for h in data["hosts"]] == [
        ("box", None, None), ("web1", "deploy", ["deploy_ed25519"]), ("web2", "deploy", ["deploy_ed25519"])
    ]
    assert adopted(home, src)


@pytest.mark.skipif(SSH is None, reason="needs the ssh client")
def test_an_ssh_import_into_an_empty_inventory_exports_what_ssh_did(home, tmp_path):
    write_config(home, PERSONAL_ONLY)
    src = source(tmp_path, SHARED)
    assert run("import", "ssh", str(src)) == 0 and run("export", "--force") == 0
    out = home / "ssh" / "10-personal.conf"
    for name in ("web1", "web2", "box"):
        g = lambda f: subprocess.run([SSH, "-G", "-F", str(f), name], capture_output=True, text=True, check=True).stdout
        assert g(src) == g(out), name


def with_default_user(home):
    write_config(home, PERSONAL_ONLY)
    (home / "config" / "ari" / "personal.json").write_text(json.dumps({"version": 3, "defaults": {"user": "deploy"}, "hosts": []}))


def test_a_new_host_that_takes_a_default_it_never_stated_leaves_the_file_unadopted(home, tmp_path, capsys):
    with_default_user(home)
    src = source(tmp_path, "Host box\n    HostName 192.0.2.23\n")
    capsys.readouterr()
    assert run("import", "ssh", str(src)) == 0
    err = capsys.readouterr().err
    assert "box: no User in the source; the default user deploy will apply" in err and "not adopted" in err
    assert not adopted(home, src)


def test_a_new_host_stating_what_the_default_says_is_adopted(home, tmp_path):
    with_default_user(home)
    src = source(tmp_path, "Host box\n    HostName 192.0.2.23\n    User deploy\n")
    assert run("import", "ssh", str(src)) == 0
    assert adopted(home, src) and "user" not in hosts(home)["box"]


def ansible_source(home, text):
    inv = home / "ssh" / "ansible"
    inv.mkdir(exist_ok=True)
    (inv / "00-hosts.yml").write_text(text)
    return inv


def test_an_ansible_host_without_a_user_takes_a_default_only_with_the_file_left_unadopted(home, capsys):
    write_config(home, ANSIBLE_ONLY)
    (home / "config" / "ari" / "work.json").write_text(json.dumps({"version": 3, "defaults": {"user": "deploy"}, "hosts": []}))
    for text in (
        "all:\n  hosts:\n    batch01:\n      ansible_host: 192.0.2.77\n",
        "all:\n  vars:\n    ansible_port: 2222\n  hosts:\n    batch01:\n      ansible_host: 192.0.2.77\n",
    ):
        inv = ansible_source(home, text)
        capsys.readouterr()
        assert run("import", "ansible", str(inv)) == 0
        err = capsys.readouterr().err
        assert "batch01: no User in the source; the default user deploy will apply" in err and "not adopted" in err
        run("rm", "batch01")


def test_an_ansible_sources_own_defaults_are_declared_defaults(home, capsys):
    write_config(home, ANSIBLE_ONLY)
    inv = ansible_source(home, "all:\n  vars:\n    ansible_user: deploy\n  hosts:\n    a:\n      ansible_host: 192.0.2.1\n")
    capsys.readouterr()
    assert run("import", "ansible", str(inv)) == 0
    assert "work: defaults set from the source: user deploy" in capsys.readouterr().out
    assert stored(home, "work")["defaults"] == {"user": "deploy"} and adopted(home, inv / "00-hosts.yml")


# H9: what Ansible reads beside the inventory files is named


def test_group_vars_and_host_vars_are_named_and_the_files_still_adopted(home, capsys):
    write_config(home, ANSIBLE_ONLY)
    inv = ansible_source(home, "all:\n  hosts:\n    w1:\n      ansible_host: 192.0.2.191\n")
    (inv / "group_vars" / "all").mkdir(parents=True)
    (inv / "group_vars" / "all" / "main.yml").write_text("ansible_port: 2201\n")
    (inv / "host_vars").mkdir()
    capsys.readouterr()
    assert run("import", "ansible", str(inv)) == 0
    err = capsys.readouterr().err
    for name in ("group_vars", "host_vars"):
        assert f"{name}: Ansible reads it too, and ari doesn't" in err
    assert "overrides what export writes" in err and "not adopted" not in err
    assert adopted(home, inv / "00-hosts.yml")


# M4: ungrouped is Ansible's own


UNGROUPED = "all:\n  children:\n    ungrouped:\n      hosts:\n        solo:\n          ansible_host: 192.0.2.190\n"


def test_ungrouped_hosts_import_without_the_group(home, capsys):
    write_config(home, ANSIBLE_ONLY)
    inv = ansible_source(home, UNGROUPED)
    capsys.readouterr()
    assert run("import", "ansible", str(inv)) == 0
    out, err = capsys.readouterr()
    assert "ungrouped is the group Ansible gives hosts in no other; its hosts are imported without it" in err
    assert "group" not in out
    assert stored(home, "work")["groups"] == {} and "groups" not in hosts(home, "work")["solo"]
    assert run("export", "--force") == 0


@pytest.mark.skipif(ANSIBLE is None, reason="needs ansible-inventory")
def test_ungrouped_round_trips_through_ansible(home):
    write_config(home, ANSIBLE_ONLY)
    inv = ansible_source(home, UNGROUPED)
    listing = lambda: json.loads(subprocess.run([ANSIBLE, "-i", str(inv), "--list"], capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL).stdout)
    before = listing()
    assert run("import", "ansible", str(inv)) == 0 and run("export") == 0
    after = listing()
    assert before["ungrouped"] == after["ungrouped"] and before["_meta"] == after["_meta"]


def test_export_says_ungrouped_is_ansibles_own(home, capsys):
    write_config(home, ANSIBLE_ONLY)
    (home / "config" / "ari" / "work.json").write_text(json.dumps({"version": 3, "groups": {"ungrouped": {}}, "hosts": []}))
    capsys.readouterr()
    assert run("export") == 1
    assert "group 'ungrouped' is one Ansible makes itself; remove it with ari group ungrouped --rm -i work" in capsys.readouterr().err
