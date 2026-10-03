import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated config, state and ssh directory per test."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("ARI_INVENTORY", raising=False)
    (tmp_path / "ssh").mkdir()
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
