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


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Write beside the target, then rename over it: readers see the old file or the new one, never half."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
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
