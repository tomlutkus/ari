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


@pytest.mark.parametrize(
    "content, message",
    [
        ("{not json", "not valid JSON"),
        ('{"version": 1, "hosts": [], "extra": 1}', "unknown keys"),
        ('{"version": 2}', "schema version 2"),
        ('{"version": 1, "hosts": [{"name": "a b", "hostname": "x"}]}', "not a valid ssh host name"),
        ('{"version": 1, "hosts": [{"name": "a", "hostname": "x", "port": 0}]}', "1-65535"),
        ('{"version": 1, "hosts": [{"name": "a", "hostname": "x"}, {"name": "A", "hostname": "y"}]}', "appears twice"),
    ],
)
def test_broken_files_fail_loudly(tmp_path, content, message):
    (tmp_path / "personal.json").write_text(content)
    with pytest.raises(HostsError, match=message):
        storage.load(ic(tmp_path))
