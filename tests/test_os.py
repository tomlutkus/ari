"""The os field: what a host runs, written by hand. It lives on the record alone, so no module
writes it and no import reads or compares it."""

import json

import pytest

from conftest import write_mixed
from ari import core
from ari.cli import main
from ari.config import load_config
from ari.errors import HostsError
from ari.models import Inventory


def run(*argv):
    return main(list(argv))


def inventory_json(home, name="work") -> dict:
    return json.loads((home / "config" / "ari" / f"{name}.json").read_text())


def record(home, host, name="work") -> dict:
    return next(h for h in inventory_json(home, name)["hosts"] if h["name"] == host)


@pytest.fixture
def mixed(home):
    write_mixed(home)
    return home


def test_add_and_edit_set_it_and_an_empty_one_clears_it(mixed, capsys):
    assert run("add", "db-01", "192.0.2.20", "-i", "work", "--group", "zone_app", "--os", "Rocky 10.1") == 0
    assert record(mixed, "db-01")["os"] == "Rocky 10.1"
    assert run("edit", "db-01", "--os", "Rocky 10.2") == 0
    assert record(mixed, "db-01")["os"] == "Rocky 10.2"
    assert run("edit", "db-01", "--os", "Rocky 10.2") == 0
    assert "no change to db-01 (work)" in capsys.readouterr().out
    assert run("edit", "db-01", "--os", "") == 0
    assert "os" not in record(mixed, "db-01")


def test_it_sits_between_keys_and_notes_in_the_record(mixed):
    assert run("key", "web", "--path", "~/.ssh/web", "-i", "work") == 0
    assert run("edit", "web-01", "--key", "web", "--os", "Ubuntu 24.04", "--notes", "front") == 0
    assert list(record(mixed, "web-01")) == [
        "name", "hostname", "user", "port", "keys", "os", "notes", "groups", "last_updated"
    ]


def test_a_host_without_it_stores_nothing_for_it(mixed):
    assert run("edit", "web-01", "--notes", "front") == 0
    assert all("os" not in h for name in ("personal", "work") for h in inventory_json(mixed, name)["hosts"])
    assert inventory_json(mixed)["version"] == 3


@pytest.mark.parametrize("value", ["Rocky\n10.1", "Rocky 10.1\r"])
def test_more_than_one_line_is_refused_under_its_own_field(mixed, capsys, value):
    before = (mixed / "config" / "ari" / "work.json").read_bytes()
    assert run("edit", "web-01", "--os", value, "--port", "0") == 1
    err = capsys.readouterr().err
    assert "nothing saved" in err and "os must be one non-empty line" in err and "port must be" in err
    assert (mixed / "config" / "ari" / "work.json").read_bytes() == before
    with pytest.raises(core.HostRefused) as refused:
        core.edit_host(load_config(), "web-01", core.Changes(os=value))
    assert [p.field for p in refused.value.problems] == ["os"]


@pytest.mark.parametrize(
    "value, message",
    [
        ("", "os must be one non-empty line"),
        ("a\nb", "os must be one non-empty line"),
        (24.04, "'os' must be a string"),
    ],
)
def test_loading_refuses_a_value_no_write_would_leave(tmp_path, value, message):
    data = {"version": 3, "hosts": [{"name": "a", "hostname": "192.0.2.1", "os": value}]}
    with pytest.raises(HostsError, match=message):
        Inventory.from_dict(data, "work", tmp_path / "work.json")


def test_it_belongs_to_hosts_never_to_the_defaults(tmp_path):
    data = {"version": 3, "defaults": {"os": "Debian 13"}, "hosts": []}
    with pytest.raises(HostsError, match=r"defaults: unknown keys \['os'\]"):
        Inventory.from_dict(data, "work", tmp_path / "work.json")


def test_show_prints_it_between_keys_and_notes(mixed, capsys):
    assert run("edit", "web-01", "--os", "Ubuntu 24.04") == 0
    capsys.readouterr()
    assert run("show", "web-01") == 0
    labels = [line.split()[0] for line in capsys.readouterr().out.splitlines() if line.strip() and not line[0].isspace()]
    assert labels[labels.index("keys") + 1 : labels.index("keys") + 3] == ["os", "notes"]
    inventory, host = core.find_host(load_config(), "web-01")
    assert core.Detail("os", "Ubuntu 24.04") in core.host_details(inventory, host)
    inventory, host = core.find_host(load_config(), "fw")
    assert core.Detail("os", "-") in core.host_details(inventory, host)


def test_search_finds_it(mixed, capsys):
    assert run("edit", "web-01", "--os", "Ubuntu 24.04") == 0
    assert run("edit", "vault-01", "--os", "Rocky 10.1") == 0
    assert [h.name for _, h in core.list_hosts(load_config(), search="ubuntu")] == ["web-01"]
    assert [h.name for _, h in core.list_hosts(load_config(), search="10.1")] == ["vault-01"]
    capsys.readouterr()
    assert run("ls", "--search", "rocky") == 0
    out = capsys.readouterr().out
    assert "vault-01" in out and "web-01" not in out


def generated(home) -> dict:
    return {p.relative_to(home): p.read_bytes() for p in (home / "ssh").rglob("*") if p.is_file()}


def test_no_generated_file_changes(mixed, capsys):
    """ssh config and Ansible inventory are the same bytes with os set as without, and export says so."""
    assert run("export") == 0
    before = generated(mixed)
    assert run("edit", "web-01", "--os", "Ubuntu 24.04") == 0
    assert run("edit", "nas", "--os", "TrueNAS 25.04") == 0
    capsys.readouterr()
    assert run("export") == 0
    out = capsys.readouterr().out
    assert "wrote" not in out and out.count("unchanged") == len(before)
    assert generated(mixed) == before


def test_re_import_leaves_it_alone(mixed, capsys):
    assert run("edit", "web-01", "--os", "Ubuntu 24.04") == 0
    assert run("edit", "nas", "--os", "TrueNAS 25.04") == 0
    assert run("export") == 0
    work, personal = inventory_json(mixed), inventory_json(mixed, "personal")
    capsys.readouterr()
    assert run("import", "ssh", str(mixed / "ssh" / "10-personal.conf"), "-i", "personal") == 0
    assert run("import", "ssh", str(mixed / "ssh" / "20-work.conf"), "-i", "work") == 0
    assert run("import", "ansible", str(mixed / "ssh" / "ansible"), "-i", "work") == 0
    out = capsys.readouterr()
    assert "merged" not in out.out and "added" not in out.out and "conflict" not in out.err
    assert inventory_json(mixed) == work and inventory_json(mixed, "personal") == personal
    assert record(mixed, "web-01")["os"] == "Ubuntu 24.04"
    assert record(mixed, "nas", "personal")["os"] == "TrueNAS 25.04"
