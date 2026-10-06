"""Hash guard: never overwrite a generated file that someone edited since we wrote it."""

import hashlib
import json
from enum import Enum
from pathlib import Path

from .errors import HostsError
from .paths import state_dir, tilde
from .storage import Staged, atomic_write


class Status(Enum):
    NEW = "new"            # target doesn't exist yet
    SAME = "same"          # target already holds exactly what we'd write
    OURS = "ours"          # target is what we (or an import) last recorded
    CHANGED = "changed"    # recorded, but edited since
    UNKNOWN = "unknown"    # exists, never recorded

    @property
    def blocks(self) -> bool:
        return self in (Status.CHANGED, Status.UNKNOWN)


def _key(path: Path) -> str:
    return str(path.expanduser().resolve())


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _encode(hashes: dict[str, str]) -> bytes:
    return (json.dumps(hashes, indent=2, sort_keys=True) + "\n").encode("utf-8")


class Guard:
    def __init__(self, path: Path | None = None):
        self.path = path or state_dir() / "exports.json"
        self.hashes: dict[str, str] = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise HostsError(f"{tilde(self.path)}: not valid JSON ({e}); delete it to start over") from None
            if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
                raise HostsError(f"{tilde(self.path)}: unexpected content; delete it to start over")
            self.hashes = data

    def status(self, path: Path, new: bytes) -> Status:
        if not path.exists():
            return Status.NEW
        current = path.read_bytes()
        if current == new:
            return Status.SAME
        recorded = self.hashes.get(_key(path))
        if recorded is None:
            return Status.UNKNOWN
        return Status.OURS if recorded == digest(current) else Status.CHANGED

    def record(self, path: Path, data: bytes) -> None:
        self.hashes[_key(path)] = digest(data)

    def stage(self, staged: Staged, files: list[tuple[Path, bytes]]) -> None:
        """Write the state as it will be once files have landed, beside the files themselves, so
        a state file that can't be written stops an export before anything is renamed. Commit it
        after the last of them."""
        hashes = {**self.hashes, **{_key(path): digest(data) for path, data in files}}
        staged.add(self.path, _encode(hashes), 0o600)

    def save(self) -> None:
        atomic_write(self.path, _encode(self.hashes))
