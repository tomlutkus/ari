"""Loading and saving inventories. Every write is atomic."""

import errno
import fcntl
import json
import os
import secrets
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .config import InventoryConfig
from .errors import HostsError
from .models import Inventory, now
from .modules import check_data
from .paths import state_dir, tilde

# A temp is hidden, beside its target: .NAME.<8 hex digits>.ari-tmp. ssh's Include config.d/*.conf
# never matches it, and Ansible's inventory loader skips any name that starts with a dot.
TEMP_SUFFIX = ".ari-tmp"

LOCK_WAIT = 5.0  # seconds a write waits for another ari before it stops


def dir_mode(mode: int) -> int:
    """The mode of a directory ari makes for a file: private files get a private directory, files
    meant for others one they can enter."""
    return 0o700 if mode == 0o600 else 0o755


def _random() -> str:
    return secrets.token_hex(4)


def _open_temp(target: Path) -> tuple[Path, int]:
    """A new hidden file beside target. It's created, never opened: a name already taken, by a
    file or by a link, is passed over for another, so nothing already there is touched."""
    for _ in range(100):
        tmp = target.with_name(f".{target.name}.{_random()}{TEMP_SUFFIX}")
        try:
            return tmp, os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        except FileExistsError:
            continue
    raise OSError(errno.EEXIST, "no free name for a temporary file", str(target))


def _write_temp(target: Path, data: bytes, mode: int) -> Path:
    """Write and sync a new temp for target, and return it. One that fails partway is removed."""
    tmp, fd = _open_temp(target)
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp


def _make_directory(directory: Path, mode: int, made: list[Path] | None = None) -> None:
    """Make what's missing, each directory going into made as it's made, outermost first.
    Parents get the default mode; the leaf gets the mode its files need. A directory that already
    exists is never touched."""
    made = [] if made is None else made
    missing = []
    while not directory.exists():
        missing.append(directory)
        directory = directory.parent
    for d in reversed(missing):
        d.mkdir()
        made.append(d)
    if missing:
        os.chmod(missing[0], mode)


_depth = 0  # how deep this process is in lock(): flock never stacks across two opens of one file


@contextmanager
def lock() -> Iterator[None]:
    """One ari writes at a time. Every command that writes holds this from the moment it reads
    what it will change until it has saved, so two never both save from what they read before the
    other wrote. It waits LOCK_WAIT seconds for another ari, then stops with nothing changed.
    Nested holds in one process are one hold."""
    global _depth
    if _depth:
        _depth += 1
        try:
            yield
        finally:
            _depth -= 1
        return
    path = state_dir() / "lock"
    try:
        _make_directory(path.parent, 0o700)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except OSError as e:
        raise HostsError(f"{tilde(path)}: {e.strerror}; every write takes this lock, so nothing changed") from None
    try:
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise HostsError(
                        f"another ari is still writing after {LOCK_WAIT:g} seconds ({tilde(path)} is held); nothing changed, try again"
                    ) from None
                time.sleep(0.05)
        _depth = 1
        try:
            yield
        finally:
            _depth = 0
    finally:
        os.close(fd)  # and with it the lock


class Staged:
    """Files written beside their targets and synced, each renamed into place by commit. Writing
    every temp before any rename means a failure while writing changes no target."""

    def __init__(self) -> None:
        self.temps: dict[Path, Path] = {}
        self.made: list[Path] = []  # directories created along the way, outermost first

    def add(self, path: Path, data: bytes, mode: int) -> None:
        self._directory(path.parent, dir_mode(mode))
        self.temps[path] = _write_temp(path, data, mode)

    def _directory(self, directory: Path, mode: int) -> None:
        """Make what's missing, remembering it so cleanup can take it away again."""
        _make_directory(directory, mode, self.made)

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
