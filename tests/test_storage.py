import json
import stat

import pytest

from ari import storage
from ari.config import InventoryConfig
from ari.errors import HostsError
from ari.models import Host, Inventory


def ic(tmp_path):
    return InventoryConfig("personal", tmp_path / "personal.json")


def test_missing_file_is_an_empty_inventory(tmp_path):
    inventory = storage.load(ic(tmp_path))
    assert inventory.hosts == [] and inventory.name == "personal"


def test_exclusive_write_never_replaces(tmp_path):
    path = tmp_path / "config.toml"
    storage.atomic_write(path, b"first", exclusive=True)
    with pytest.raises(FileExistsError):
        storage.atomic_write(path, b"second", exclusive=True)
    assert path.read_bytes() == b"first"
    assert not (tmp_path / "config.toml.tmp").exists()


def test_round_trip_sorts_and_protects(tmp_path):
    inventory = Inventory("personal", tmp_path / "personal.json")
    inventory.hosts = [Host("vps", "198.51.100.1"), Host("Alpha", "192.0.2.1", port=2222)]
    storage.save(inventory)
    path = tmp_path / "personal.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not (tmp_path / "personal.json.tmp").exists()
    loaded = storage.load(ic(tmp_path))
    assert [h.name for h in loaded.hosts] == ["Alpha", "vps"]
    assert loaded.hosts[0].port == 2222


def test_version_1_upgrades_in_memory_and_converts_on_save(tmp_path):
    path = tmp_path / "personal.json"
    path.write_text(json.dumps({
        "version": 1,
        "defaults": {"user": "tom", "ssh_options": {"Compression": "yes"}},
        "hosts": [
            {"name": "vps", "hostname": "198.51.100.1", "ssh_options": {"RequestTTY": "yes"}, "ansible": False},
            {"name": "nas", "hostname": "192.0.2.254"},
        ],
    }))
    inventory = storage.load(ic(tmp_path))
    vps = inventory.find("vps")
    assert vps.modules == {"ssh": {"options": {"RequestTTY": "yes"}}}
    assert vps.exclude == ["ansible"]
    assert inventory.defaults.modules == {"ssh": {"options": {"Compression": "yes"}}}
    storage.save(inventory)
    saved = json.loads(path.read_text())
    assert saved["version"] == 2
    assert "ssh_options" not in json.dumps(saved) and '"ansible": false' not in json.dumps(saved)


def test_data_for_a_module_that_isnt_installed_survives(tmp_path):
    path = tmp_path / "personal.json"
    path.write_text(json.dumps({
        "version": 2,
        "hosts": [{"name": "vps", "hostname": "198.51.100.1", "modules": {"netbox": {"id": 42}}}],
    }))
    inventory = storage.load(ic(tmp_path))
    storage.save(inventory)
    assert json.loads(path.read_text())["hosts"][0]["modules"] == {"netbox": {"id": 42}}


@pytest.mark.parametrize(
    "content, message",
    [
        ("{not json", "not valid JSON"),
        ('{"version": 2, "hosts": [], "extra": 1}', "unknown keys"),
        ('{"version": 3}', "schema version 3"),
        ('{"version": 2, "hosts": [{"name": "a b", "hostname": "x"}]}', "not a valid ssh host name"),
        ('{"version": 2, "hosts": [{"name": "a", "hostname": "x", "port": 0}]}', "1-65535"),
        ('{"version": 2, "hosts": [{"name": "a", "hostname": "x"}, {"name": "A", "hostname": "y"}]}', "appears twice"),
        ('{"version": 2, "hosts": [{"name": "a", "hostname": "x", "exclude": ["Bad Name"]}]}', "isn't a module name"),
        ('{"version": 2, "hosts": [{"name": "a", "hostname": "x", "modules": {"ssh": {"options": 5}}}]}', "options must map"),
        ('{"version": 2, "hosts": [{"name": "a", "hostname": "x", "modules": {"ssh": {"bogus": 1}}}]}', "unknown keys"),
    ],
)
def test_broken_files_fail_loudly(tmp_path, content, message):
    (tmp_path / "personal.json").write_text(content)
    with pytest.raises(HostsError, match=message):
        storage.load(ic(tmp_path))
