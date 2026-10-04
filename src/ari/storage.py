"""Loading and saving inventories. Every write is atomic."""

import json
import os
import stat
from pathlib import Path

from .config import InventoryConfig
from .errors import HostsError
from .models import Inventory, now
from .modules import check_data
from .paths import tilde


def dir_mode(mode: int) -> int:
    """The mode of a directory ari makes for a file: private files get a private directory, files
    meant for others one they can enter."""
    return 0o700 if mode == 0o600 else 0o755


def _write_temp(tmp: Path, data: bytes, mode: int) -> None:
    """Write and sync one temp. One that fails partway is removed; one that can't be opened was never ours."""
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class Staged:
    """Files written beside their targets and synced, each renamed into place by commit. Writing
    every temp before any rename means a failure while writing changes no target."""

    def __init__(self) -> None:
        self.temps: dict[Path, Path] = {}
        self.made: list[Path] = []  # directories created along the way, outermost first

    def add(self, path: Path, data: bytes, mode: int) -> None:
        self._directory(path.parent, dir_mode(mode))
        tmp = path.with_name(path.name + ".tmp")
        _write_temp(tmp, data, mode)
        self.temps[path] = tmp

    def _directory(self, directory: Path, mode: int) -> None:
        """Make what's missing. Parents get the default mode; the leaf gets the mode its files
        need. A directory that already exists is never touched."""
        missing = []
        while not directory.exists():
            missing.append(directory)
            directory = directory.parent
        for d in reversed(missing):
            d.mkdir()
            self.made.append(d)
        if missing:
            os.chmod(missing[0], mode)

    def commit(self, path: Path, exclusive: bool = False) -> None:
        """Rename one temp over its target. exclusive links it into place instead, so an existing
        target raises FileExistsError and stays as it was."""
        tmp = self.temps[path]
        if exclusive:
            os.link(tmp, path)
            tmp.unlink()
        else:
            os.replace(tmp, path)
        del self.temps[path]

    def cleanup(self) -> None:
        """Remove the temps not renamed, then every directory made for them that's still empty."""
        for path in [*self.temps.values(), *reversed(self.made)]:
            try:
                path.rmdir() if path in self.made else path.unlink()
            except OSError:
                pass  # a directory that holds a file that landed; never mask the error being handled
        self.temps.clear()
        self.made.clear()


def atomic_write(path: Path, data: bytes, mode: int = 0o600, exclusive: bool = False) -> None:
    """Write beside the target, then rename over it: readers see the old file or the new one, never half.
    exclusive links the file into place instead, so an existing target raises FileExistsError untouched."""
    staged = Staged()
    try:
        staged.add(path, data, mode)
        staged.commit(path, exclusive)
    except BaseException:
        staged.cleanup()
        raise


def set_mode(path: Path, mode: int) -> bool:
    """Give an existing file the mode it would get if written now. Reports whether it changed."""
    if stat.S_IMODE(path.stat().st_mode) == mode:
        return False
    os.chmod(path, mode)
    return True


def load(ic: InventoryConfig) -> Inventory:
    """A declared inventory whose file doesn't exist yet starts empty. A broken file is an error, never empty."""
    if not ic.file.exists():
        return Inventory(name=ic.name, path=ic.file)
    try:
        data = json.loads(ic.file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise HostsError(f"{tilde(ic.file)}: not valid JSON ({e})") from None
    except OSError as e:
        raise HostsError(f"{tilde(ic.file)}: {e.strerror}") from None
    inventory = Inventory.from_dict(data, name=ic.name, path=ic.file)
    check_data(inventory)
    return inventory


def save(inventory: Inventory) -> None:
    inventory.last_updated = now()
    inventory.hosts.sort(key=lambda h: h.name.casefold())
    text = json.dumps(inventory.to_dict(), indent=2, ensure_ascii=False) + "\n"
    atomic_write(inventory.path, text.encode("utf-8"))
