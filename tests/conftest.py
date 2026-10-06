import json
import shutil
from pathlib import Path

import pytest

from ari import keyfiles

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated config, state and ssh directory per test. ari key reads key files under the
    passwd home, as ssh does, so ~ there is tmp_path/home too."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("ARI_INVENTORY", raising=False)
    (tmp_path / "ssh").mkdir()
    (tmp_path / "home").mkdir()
    real = keyfiles._home
    monkeypatch.setattr(keyfiles, "_home", lambda user: str(tmp_path / "home") if user is None else real(user))
    return tmp_path


def write_config(home: Path, text: str) -> Path:
    path = home / "config" / "ari" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.replace("SSH", str(home / "ssh")))
    return path


PERSONAL_ONLY = """
default = "personal"

[inventories.personal.ssh]
path = "SSH/10-personal.conf"
"""

PERSONAL_AND_WORK = PERSONAL_ONLY + """
[inventories.work.ssh]
path = "SSH/20-work.conf"

[inventories.work.ansible]
dir = "SSH/ansible"
enabled = false
"""


MIXED = """
default = "personal"

[inventories.personal.ssh]
path = "SSH/10-personal.conf"

[inventories.work.ssh]
path = "SSH/20-work.conf"

[inventories.work.ansible]
dir = "SSH/ansible"
zones = "zone_*"

[inventories.work.ansible.groups]
"10-zones.yml" = ["zone_*"]
"70-lifecycle.yml" = ["no_auto_update"]
"""


def write_mixed(home):
    """Two inventories, ssh in both and ansible in work, with a host excluded from each module.
    Hosts are sorted by name, as every save leaves them."""
    write_config(home, MIXED)
    root = home / "config" / "ari"
    (root / "personal.json").write_text(json.dumps({
        "version": 2,
        "defaults": {"user": "tom"},
        "hosts": [
            {"name": "nas", "hostname": "192.0.2.254", "aliases": ["storage"]},
            {"name": "scratch", "hostname": "192.0.2.77", "exclude": ["ssh"]},
        ],
    }))
    (root / "work.json").write_text(json.dumps({
        "version": 2,
        "defaults": {"user": "deploy", "port": 2222, "ssh_key": "~/.ssh/lab-ed25519"},
        "groups": {
            "zone_app": {"description": "app subnet (192.0.2.0/25)"},
            "no_auto_update": {"reasons": {"secrets": "secrets and prod path"}},
        },
        "hosts": [
            {"name": "fw", "hostname": "203.0.113.1", "exclude": ["ansible"]},
            {"name": "vault-01", "hostname": "192.0.2.30", "groups": ["zone_app", "no_auto_update"],
             "reasons": {"no_auto_update": "secrets"}},
            {"name": "web-01", "hostname": "192.0.2.10", "user": "admin", "port": 22, "groups": ["zone_app"]},
        ],
    }))


@pytest.fixture
def personal(home):
    write_config(home, PERSONAL_ONLY)
    return home


@pytest.fixture
def both(home):
    write_config(home, PERSONAL_AND_WORK)
    return home


def copy_fixture(name: str, dest: Path) -> Path:
    shutil.copy(FIXTURES / name, dest)
    return dest


def write_listed(home, config: str = MIXED):
    """Two inventories with a value for every column: personal sets no user anywhere, a note runs
    to two lines with a pipe and quotes in it, and one host lists keys of its own."""
    write_config(home, config)
    root = home / "config" / "ari"
    (root / "personal.json").write_text(json.dumps({
        "version": 3,
        "defaults": {"keys": ["home"]},
        "keys": {"home": {"path": "~/.ssh/home"}},
        "hosts": [
            {"name": "nas", "hostname": "192.0.2.254", "aliases": ["storage"], "os": "TrueNAS 25.04",
             "notes": "basement NAS"},
            {"name": "pi", "hostname": "192.0.2.50", "user": "pi", "port": 2200, "os": "Raspberry Pi OS 12",
             "notes": 'two lines\nthe second | with a pipe, "quoted"'},
            {"name": "scratch", "hostname": "192.0.2.77", "exclude": ["ssh"]},
        ],
    }))
    (root / "work.json").write_text(json.dumps({
        "version": 3,
        "defaults": {"user": "deploy", "port": 2222, "keys": ["lab"]},
        "keys": {"lab": {"path": "~/.ssh/lab"}, "old": {"path": "~/.ssh/old"}},
        "groups": {
            "zone_app": {"description": "app subnet (192.0.2.0/25)"},
            "no_auto_update": {"reasons": {"secrets": "secrets and prod path"}},
        },
        "hosts": [
            {"name": "fw", "hostname": "203.0.113.1", "keys": ["old", "lab"], "exclude": ["ansible"]},
            {"name": "vault-01", "hostname": "192.0.2.30", "os": "Rocky 10.1", "groups": ["zone_app", "no_auto_update"],
             "reasons": {"no_auto_update": "secrets"}},
            {"name": "web-01", "hostname": "192.0.2.10", "aliases": ["www", "192.0.2.10"], "user": "admin", "port": 22,
             "groups": ["zone_app"], "notes": "front door"},
        ],
    }))
