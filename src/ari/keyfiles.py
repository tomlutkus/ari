"""Key files: what ssh would find at a declared key's path, as ari key's STATE, the public half
pub stores, and the ssh-keygen run behind --new. Only ari key reads or writes them: export never
does, so it works on a machine that doesn't hold every key."""

import base64
import binascii
import hashlib
import os
import pwd
import re
import shutil
import socket
import stat
import subprocess
from datetime import datetime

OK = "ok"
_READ = 1 << 20  # a key file is a few kB; this only bounds a path that names something else
_OPENSSH = ("-----BEGIN OPENSSH PRIVATE KEY-----", "-----END OPENSSH PRIVATE KEY-----")
_MAGIC = b"openssh-key-v1\0"
_PEM = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")


def _home(user: str | None) -> str | None:
    """The home ssh expands ~ or ~user to: the passwd entry, not $HOME."""
    try:
        entry = pwd.getpwnam(user) if user else pwd.getpwuid(os.getuid())
    except KeyError:
        return None
    return entry.pw_dir


def resolve(path: str) -> tuple[str | None, str | None]:
    """The file ssh opens for an IdentityFile, or why ari can't name one: ssh expands ~ and ~user
    through passwd, then tokens, then opens what's left from its own working directory."""
    if "%" in path or "${" in path:
        return None, "tokens"
    if path.startswith("~"):
        user, _, rest = path[1:].partition("/")
        home = _home(user or None)
        if home is None:
            return None, f"no user {user}" if user else "no home"
        rest = rest.lstrip("/")
        path = os.path.join(home, rest) if rest else home
    if not os.path.isabs(path):
        return None, "relative"
    return path, None


def _string(data: bytes, at: int) -> tuple[bytes, int]:
    if at + 4 > len(data):
        raise ValueError("short")
    size = int.from_bytes(data[at : at + 4], "big")
    end = at + 4 + size
    if end > len(data):
        raise ValueError("short")
    return data[at + 4 : end], end


def private_public(text: str) -> tuple[bytes | None, bool]:
    """The public key blob an OpenSSH private key keeps in clear ahead of its encrypted part, so no
    passphrase is needed. The flag says the file is a private key of another format, whose
    public half can't be read without decrypting it."""
    begin, end = _OPENSSH
    start, stop = text.find(begin), text.find(end)
    if start < 0:
        return None, bool(_PEM.search(text))
    if stop < start:
        return None, False
    try:
        body = base64.b64decode("".join(text[start + len(begin) : stop].split()), validate=True)
        if not body.startswith(_MAGIC):
            return None, False
        at = len(_MAGIC)
        for _ in ("cipher", "kdf", "kdf options"):
            _, at = _string(body, at)
        if at + 4 > len(body) or int.from_bytes(body[at : at + 4], "big") < 1:
            return None, False
        blob, _ = _string(body, at + 4)
    except (binascii.Error, ValueError):
        return None, False
    return blob, False


def _public_line(text: str) -> tuple[bytes, str] | None:
    """The first key in a public key file and its line: the first whose second field decodes."""
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 2 or fields[0].startswith("#"):
            continue
        try:
            return base64.b64decode(fields[1], validate=True), line.strip()
        except binascii.Error:
            continue
    return None


def public_blob(text: str) -> bytes | None:
    """The key in a public key line or file."""
    found = _public_line(text)
    return found[0] if found else None


def fingerprint(blob: bytes) -> str:
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def _read(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            return f.read(_READ).decode("utf-8", "replace")
    except OSError:
        return None


_VALID = re.compile(r"^\s*Valid: (.*)$", re.M)
_CERT_KEY = re.compile(r"^\s*Public key: \S+ (SHA256:\S+)", re.M)
_WHEN = r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)"


def _cert(path: str, blob: bytes | None, now: datetime) -> str:
    """The certificate ssh loads beside a key, read with ssh-keygen -L: whose key it certifies,
    and its validity in local time, as ssh-keygen prints it."""
    keygen = shutil.which("ssh-keygen")
    if keygen is None:
        return "cert unchecked"
    try:
        result = subprocess.run(
            [keygen, "-L", "-f", path], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return "cert unreadable"
    certified, valid = _CERT_KEY.search(result.stdout), _VALID.search(result.stdout)
    if result.returncode != 0 or certified is None or valid is None:
        return "cert unreadable"
    if blob is not None and certified.group(1) != fingerprint(blob):
        return "cert for another key"
    span = valid.group(1).strip()
    start = end = None
    if m := re.fullmatch(rf"from {_WHEN} to {_WHEN}", span):
        start, end = m.groups()
    elif m := re.fullmatch(rf"after {_WHEN}", span):
        start = m.group(1)
    elif m := re.fullmatch(rf"before {_WHEN}", span):
        end = m.group(1)
    elif span != "forever":
        return "cert unreadable"
    if start is not None and now < datetime.fromisoformat(start):
        return f"cert from {start[:10]}"
    if end is not None:
        return f"cert expired {end[:10]}" if now >= datetime.fromisoformat(end) else f"cert to {end[:10]}"
    return "cert forever"


def _private(path: str) -> tuple[str, str, os.stat_result] | str:
    """The file ssh would read as the private key, its text and stat, or the word for why not."""
    target, why = resolve(path)
    if target is None:
        return why
    try:
        st = os.stat(target)
    except (FileNotFoundError, NotADirectoryError):
        return "missing"
    except OSError:
        return "unreadable"
    if not stat.S_ISREG(st.st_mode):
        return "not a file"
    text = _read(target)
    if text is None:
        return "unreadable"
    return target, text, st


def _blob_type(blob: bytes) -> str:
    kind, _ = _string(blob, 0)
    return kind.decode("ascii", "replace")


def pub_file_line(target: str, blob: bytes) -> str | None:
    """The line of target's .pub, comment and all, when it holds the key blob."""
    beside = _read(target + ".pub")
    found = _public_line(beside) if beside is not None else None
    return found[1] if found and found[0] == blob else None


def public_line(path: str) -> tuple[str | None, str]:
    """The public half of the key at path as pub stores it, or why there's none: the .pub line
    when it's this key's, comment and all, else the key the private key file holds."""
    found = _private(path)
    if isinstance(found, str):
        return None, found
    target, text, _ = found
    blob, other_format = private_public(text)
    if other_format:
        return None, "pair unchecked"
    try:
        kind = _blob_type(blob) if blob is not None else None
    except ValueError:
        kind = None
    if kind is None:
        return None, "not a key"
    return pub_file_line(target, blob) or f"{kind} {base64.b64encode(blob).decode()}", ""


def pair_line(path: str) -> str | None:
    """The .pub line of the key pair at path, when both files are there and hold the same key."""
    found = _private(path)
    if isinstance(found, str):
        return None
    target, text, _ = found
    blob, _ = private_public(text)
    return pub_file_line(target, blob) if blob is not None else None


def own_name() -> str:
    """This user's login, as ssh-keygen writes it in a key's default comment."""
    return pwd.getpwuid(os.getuid()).pw_name


def comment(name: str) -> str:
    """A new key's comment: ari's name for it, then ssh-keygen's own user@host."""
    return f"{name} {own_name()}@{socket.gethostname()}"


def generate(target: str, text: str) -> int:
    """ssh-keygen for a new ed25519 key at target, on this terminal: it asks for the passphrase
    itself, so ari never sees it. Its exit status."""
    keygen = shutil.which("ssh-keygen")
    if keygen is None:
        raise FileNotFoundError("ssh-keygen")
    return subprocess.run([keygen, "-t", "ed25519", "-f", target, "-C", text]).returncode


def state(path: str, pub: str | None, now: datetime | None = None) -> list[str]:
    """What ssh, run by this user, would hit at the key's path, in words; ["ok"] when nothing.
    A key ssh can't read stops there. Otherwise its mode as ssh checks it, the public halves
    against the one the private key holds, and the certificate beside it."""
    found = _private(path)
    if isinstance(found, str):
        return [found]
    target, text, st = found
    words = []
    mode = stat.S_IMODE(st.st_mode)
    # ssh ignores a private key of yours that group or others can touch, and only one of yours.
    if st.st_uid == os.getuid() and mode & 0o077:
        words.append(f"mode {mode:04o}")
    blob, other_format = private_public(text)
    if other_format:
        words.append("pair unchecked")
    elif blob is None:
        words.append("not a key")
    else:
        # ssh offers the key a readable .pub describes, then won't sign with a private key that
        # isn't its pair. A .pub that holds no key it skips, so that says nothing either way.
        beside = _read(target + ".pub")
        other = public_blob(beside) if beside is not None else None
        if other is not None and other != blob:
            words.append(".pub mismatch")
        if pub is not None and public_blob(pub) != blob:
            words.append("pub stale")
    if os.path.exists(target + "-cert.pub"):
        words.append(_cert(target + "-cert.pub", blob, now or datetime.now()))
    return words or [OK]
