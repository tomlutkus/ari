import json
import shutil
import subprocess

import pytest

from conftest import FIXTURES
from ari.cli import main


def run(*argv):
    return main(list(argv))


def inventory_path(home, name="personal"):
    return home / "config" / "ari" / f"{name}.json"


def stored(home, name, inventory="personal"):
    hosts = json.loads(inventory_path(home, inventory).read_text())["hosts"]
    return next(h for h in hosts if h["name"] == name)


def declare_groups(home, groups, inventory="personal"):
    """Write groups straight into the inventory file, without ari group's checks."""
    path = inventory_path(home, inventory)
    data = json.loads(path.read_text()) if path.exists() else {"version": 2, "hosts": []}
    data["groups"] = groups
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


@pytest.fixture
def imported(personal, capsys):
    run("import", "ssh", str(FIXTURES / "personal.conf"))
    capsys.readouterr()
    return personal


GROUPS = {
    "backups": {},
    "no_auto_update": {"reasons": {"remote": "remote access path", "secrets": "secrets and prod path"}},
}


# add


def test_add_stores_only_what_differs(imported, capsys):
    assert run("add", "pi", "192.0.2.40", "--user", "tom", "--port", "22", "--key", "~/.ssh/personal-ed25519") == 0
    assert "added pi to personal" in capsys.readouterr().out
    pi = stored(imported, "pi")
    assert set(pi) == {"name", "hostname", "last_updated"}


def test_add_keeps_differences_and_options_in_order(imported):
    assert run(
        "add", "jump", "198.51.100.5",
        "--user", "admin", "--port", "2222", "--alias", "j", "--notes", "bastion",
        "--opt", "ServerAliveInterval=30", "--opt", "RequestTTY=yes", "--exclude", "ansible",
    ) == 0
    jump = stored(imported, "jump")
    assert (jump["user"], jump["port"], jump["aliases"], jump["notes"]) == ("admin", 2222, ["j"], "bastion")
    assert list(jump["modules"]["ssh"]["options"].items()) == [("ServerAliveInterval", "30"), ("RequestTTY", "yes")]
    assert jump["exclude"] == ["ansible"]


def test_add_goes_to_the_named_inventory(both):
    assert run("-i", "work", "add", "fw", "203.0.113.1") == 0
    assert stored(both, "fw", "work")["hostname"] == "203.0.113.1"
    assert not inventory_path(both).exists()


@pytest.mark.parametrize(
    "argv, message",
    [
        (["add", "a b", "192.0.2.1"], "not a valid ssh host name"),
        (["add", "x", ""], "hostname must be non-empty"),
        (["add", "x", "192.0.2.1", "--port", "0"], "1-65535"),
        (["add", "x", "192.0.2.1", "--port", "70000"], "1-65535"),
        (["add", "x", "192.0.2.1", "--port", "ssh"], "integer 1-65535, got 'ssh'"),
        (["add", "x", "192.0.2.1", "--alias", "x"], "appears twice"),
        (["add", "x", "192.0.2.1", "--alias", "a*"], "not a valid ssh host name"),
        (["add", "x", "192.0.2.1", "--opt", "User=root"], "User can't be an option; it's the user field"),
        (["add", "x", "192.0.2.1", "--opt", "Bad-Key=1"], "isn't an ssh_config keyword"),
        (["add", "x", "192.0.2.1", "--opt", "RemoteCommand=a\nb"], "must be one line"),
        (["add", "x", "192.0.2.1", "--user", "a\nb"], "user must be one line"),
        (["add", "x", "192.0.2.1", "--exclude", "Bad Name"], "isn't a module name"),
        (["add", "x", "192.0.2.1", "--group", "web"], "group 'web' isn't declared"),
    ],
)
def test_add_refuses_bad_records(personal, capsys, argv, message):
    assert run(*argv) == 1
    assert message in capsys.readouterr().err
    assert not inventory_path(personal).exists()


def test_names_and_aliases_are_unique_across_inventories(both, capsys):
    run("import", "ssh", str(FIXTURES / "personal.conf"))
    before = inventory_path(both).read_bytes()
    assert run("-i", "work", "add", "Storage", "203.0.113.9") == 1
    assert run("-i", "work", "add", "fw", "203.0.113.9", "--alias", "LAPTOP") == 1
    err = capsys.readouterr().err
    assert "'Storage' is already used by personal/nas" in err
    assert "'LAPTOP' is already used by personal/laptop" in err
    assert not inventory_path(both, "work").exists()
    assert inventory_path(both).read_bytes() == before


def test_every_problem_is_listed_at_once(personal, capsys):
    declare_groups(personal, GROUPS)
    argv = ["--port", "0", "--opt", "Port=2", "--group", "web", "--group", "no_auto_update:typo"]
    assert run("add", "x", "192.0.2.1", *argv) == 1
    err = capsys.readouterr().err
    assert "nothing saved" in err and "1-65535" in err and "Port can't be an option" in err
    assert "'web' isn't declared" in err and "no reason 'typo' (declared: remote, secrets)" in err


def test_bad_flag_shapes_are_usage_errors(personal, capsys):
    with pytest.raises(SystemExit) as e:
        run("add", "x", "192.0.2.1", "--opt", "novalue")
    assert e.value.code == 2
    with pytest.raises(SystemExit):
        run("add", "x", "192.0.2.1", "--group", "backups:")
    assert "expected GROUP or GROUP:REASON" in capsys.readouterr().err


# edit


def test_edit_changes_fields_and_finds_hosts_by_alias(imported, capsys):
    assert run("edit", "storage", "--port", "2222", "--user", "admin", "--alias", "nas2") == 0
    assert "updated nas (personal)" in capsys.readouterr().out
    nas = stored(imported, "nas")
    assert (nas["port"], nas["user"], nas["aliases"]) == (2222, "admin", ["storage", "192.0.2.254", "nas2"])


def test_edit_empty_value_goes_back_to_the_default(imported):
    assert run("edit", "nas", "--user", "") == 0
    assert "user" not in stored(imported, "nas")
    run("edit", "nas", "--port", "2222")
    assert run("edit", "nas", "--port", "") == 0
    assert "port" not in stored(imported, "nas")


def test_edit_setting_the_default_value_stores_nothing(imported):
    assert run("edit", "nas", "--user", "tom") == 0
    assert "user" not in stored(imported, "nas")


def test_edit_options_set_replace_in_place_and_clear(imported):
    assert run("edit", "vps", "--opt", "requesttty=no", "--opt", "Compression=yes") == 0
    assert list(stored(imported, "vps")["modules"]["ssh"]["options"].items()) == [
        ("RequestTTY", "no"),
        ("RemoteCommand", "sudo -i"),
        ("Compression", "yes"),
    ]
    assert run("edit", "vps", "--opt", "RequestTTY=", "--opt", "RemoteCommand=", "--opt", "Compression=") == 0
    assert "modules" not in stored(imported, "vps")


def test_edit_hostname_and_rename(imported, capsys):
    assert run("edit", "laptop", "--hostname", "192.0.2.12", "--rename", "thinkpad") == 0
    assert "updated thinkpad" in capsys.readouterr().out
    host = stored(imported, "thinkpad")
    assert host["hostname"] == "192.0.2.12"
    assert run("edit", "thinkpad", "--rename", "nas") == 1
    assert "'nas' is already used by personal/nas" in capsys.readouterr().err


def test_edit_can_change_the_case_of_its_own_name(imported):
    assert run("edit", "nas", "--rename", "NAS") == 0
    assert stored(imported, "NAS")["hostname"] == "192.0.2.254"


def test_unalias_drops_aliases_and_the_host_line_follows(imported):
    assert run("edit", "nas", "--unalias", "STORAGE", "--unalias", "192.0.2.254") == 0
    assert "aliases" not in stored(imported, "nas")
    run("export")
    assert "Host nas\n" in (imported / "ssh" / "10-personal.conf").read_text()


def test_unalias_runs_before_alias(imported):
    assert run("edit", "storage", "--unalias", "storage", "--alias", "files") == 0
    assert stored(imported, "nas")["aliases"] == ["192.0.2.254", "files"]
    assert run("edit", "nas", "--unalias", "files", "--alias", "Files") == 0
    assert stored(imported, "nas")["aliases"] == ["192.0.2.254", "Files"]


def test_edit_groups_and_reasons(imported, capsys):
    declare_groups(imported, GROUPS)
    assert run("edit", "vps", "--group", "backups", "--group", "no_auto_update:remote") == 0
    vps = stored(imported, "vps")
    assert vps["groups"] == ["backups", "no_auto_update"] and vps["reasons"] == {"no_auto_update": "remote"}

    capsys.readouterr()
    run("show", "vps")
    assert "no_auto_update (remote)" in capsys.readouterr().out

    assert run("edit", "vps", "--group", "no_auto_update") == 0
    assert "reasons" not in stored(imported, "vps")

    assert run("edit", "vps", "--ungroup", "no_auto_update", "--ungroup", "backups") == 0
    assert "groups" not in stored(imported, "vps")


def test_ungroup_drops_the_reason_with_it(imported):
    declare_groups(imported, GROUPS)
    run("edit", "vps", "--group", "no_auto_update:secrets")
    assert run("edit", "vps", "--ungroup", "no_auto_update") == 0
    vps = stored(imported, "vps")
    assert "groups" not in vps and "reasons" not in vps


def test_exclude_and_include(imported):
    assert run("edit", "vps", "--exclude", "ssh", "--exclude", "ansible") == 0
    assert stored(imported, "vps")["exclude"] == ["ansible", "ssh"]
    run("export")
    assert "vps" not in (imported / "ssh" / "10-personal.conf").read_text()
    assert run("edit", "vps", "--include", "ssh") == 0
    assert stored(imported, "vps")["exclude"] == ["ansible"]


@pytest.mark.parametrize(
    "argv, message",
    [
        (["edit", "vps", "--ungroup", "backups"], "isn't in group 'backups'"),
        (["edit", "vps", "--include", "ansible"], "doesn't exclude 'ansible'"),
        (["edit", "vps", "--unalias", "vps"], "has no alias 'vps'"),
        (["edit", "vps", "--unalias", "storage"], "has no alias 'storage'"),
        (["edit", "nas", "--unalias", "storage", "--unalias", "storage"], "has no alias 'storage'"),
        (["edit", "vps", "--opt", "Compression="], "no ssh option Compression to clear"),
        (["edit", "vps", "--hostname", ""], "hostname must be non-empty"),
        (["edit", "vps", "--hostname", "198.51.100.1 x"], "without spaces"),
        (["edit", "vps", "--rename", ""], "not a valid ssh host name"),
        (["edit", "vps", "--alias", "laptop"], "already used by personal/laptop"),
        (["edit", "vps"], "nothing to change"),
        (["edit", "nope", "--port", "22"], "no host named 'nope' in any inventory"),
    ],
)
def test_edit_refuses_and_saves_nothing(imported, capsys, argv, message):
    before = inventory_path(imported).read_bytes()
    assert run(*argv) == 1
    assert message in capsys.readouterr().err
    assert inventory_path(imported).read_bytes() == before


def test_edit_with_nothing_different_saves_nothing(imported, capsys):
    before = inventory_path(imported).read_bytes()
    assert run("edit", "nas", "--user", "root") == 0
    assert "no change to nas" in capsys.readouterr().out
    assert inventory_path(imported).read_bytes() == before


def test_edit_and_rm_honour_the_inventory_flag(both, capsys):
    run("import", "ssh", str(FIXTURES / "personal.conf"))
    capsys.readouterr()
    assert run("-i", "work", "edit", "nas", "--port", "2222") == 1
    assert run("rm", "nas", "-i", "work") == 1
    assert capsys.readouterr().err.count("no host named 'nas' in work") == 2


# rm


def test_rm_by_alias(imported, capsys):
    assert run("rm", "storage") == 0
    assert "removed nas from personal" in capsys.readouterr().out
    names = [h["name"] for h in json.loads(inventory_path(imported).read_text())["hosts"]]
    assert names == ["github.com", "laptop", "vps"]
    assert run("rm", "nas") == 1


# Export checks groups too


def test_export_refuses_undeclared_groups_and_bad_children(imported, capsys):
    path = inventory_path(imported)
    data = json.loads(path.read_text())
    data["groups"] = {"a": {"children": ["b"]}, "b": {"children": ["a", "c"]}}
    data["hosts"][0]["groups"] = ["typo"]
    path.write_text(json.dumps(data))
    assert run("export") == 1
    err = capsys.readouterr().err
    assert "group 'typo' isn't declared" in err
    assert "child 'c', which isn't declared" in err
    assert "groups a > b > a form a cycle" in err
    assert not (imported / "ssh" / "10-personal.conf").exists()


@pytest.mark.skipif(shutil.which("ssh") is None, reason="needs the ssh client")
def test_added_host_resolves_in_ssh(imported):
    run("add", "jump", "198.51.100.5", "--user", "admin", "--port", "2222", "--alias", "j", "--opt", "ServerAliveInterval=30")
    run("export")
    config = imported / "ssh" / "10-personal.conf"
    resolved = subprocess.run(["ssh", "-G", "-F", str(config), "j"], capture_output=True, text=True, check=True).stdout
    lines = set(resolved.splitlines())
    assert {"hostname 198.51.100.5", "user admin", "port 2222", "serveraliveinterval 30"} <= lines
    assert "identityfile ~/.ssh/personal-ed25519" in lines
