"""Where ari keeps things, and how paths are shown to people."""

import os
from pathlib import Path

APP = "ari"


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser() / APP


def config_file() -> Path:
    return config_dir() / "config.toml"


def state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or "~/.local/state").expanduser() / APP


def tilde(path: Path) -> str:
    """Shorten a path under $HOME for display."""
    home = Path.home()
    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)
