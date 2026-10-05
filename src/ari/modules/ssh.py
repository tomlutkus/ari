"""ssh: OpenSSH client config. Imports Host blocks; exports one file per inventory."""

import copy
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import HostsError
from ..models import Host, Inventory, KeyDef, check_port, declare_key
from ..paths import tilde
from . import ImportResult, Module, Output

_KEYWORD = re.compile(r"^(\w+)\s*(?:=\s*|\s+)(.*)$")
_PATTERN_CHARS = set("*?!")


@dataclass
class Block:
    tokens: list[str]
    line: int
    options: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class Settings:
    path: Path


def _split(value: str) -> list[str] | None:
    try:
        return shlex.split(value)
    except ValueError:
        return None


def _single(value: str) -> str:
    """A one-argument value with ssh-style quoting removed."""
    parts = _split(value)
    return parts[0] if parts and len(parts) == 1 else value


def _quote(value: str) -> str:
    if value and not any(c.isspace() or c in "\"'" for c in value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


# Keywords the host record or the block structure already covers. As options they'd be written a
# second time, and ssh would quietly take the first.
_OWN_FIELD = {"host": None, "match": None, "hostname": "hostname", "user": "user", "port": "port", "identityfile": "keys"}

# Options that can hold a list, one line each: ssh uses every line of these, where for any other
# keyword it reads the first and ignores the rest. (IdentityFile is the keys field.)
LISTABLE = {"localforward", "remoteforward", "dynamicforward", "sendenv"}

Value = str | list[str]


def _options_map(data: Any, where: str) -> dict[str, Value]:
    """Options as stored: each keyword maps to a string, or to a list for the ones ssh uses every
    line of. A list of one is stored as its string."""
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, (str, list)) for k, v in data.items()):
        raise HostsError(f"{where}: options must map ssh keywords to strings")
    seen: dict[str, str] = {}
    out: dict[str, Value] = {}
    for keyword, value in data.items():
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", keyword):
            raise HostsError(f"{where}: {keyword!r} isn't an ssh_config keyword")
        first = seen.setdefault(keyword.casefold(), keyword)
        if first != keyword:
            raise HostsError(f"{where}: options {first} and {keyword} are one keyword to ssh, which reads them without case")
        if keyword.lower() in _OWN_FIELD:
            own = _OWN_FIELD[keyword.lower()]
            raise HostsError(f"{where}: {keyword} can't be an option" + (f"; it's the {own} field" if own else ""))
        if isinstance(value, list):
            if keyword.lower() not in LISTABLE:
                raise HostsError(f"{where}: option {keyword} takes one value; ssh reads only the first")
            if not value or not all(isinstance(v, str) for v in value):
                raise HostsError(f"{where}: option {keyword} must be a string or a non-empty list of strings")
        for line in value if isinstance(value, list) else [value]:
            if "\n" in line or "\r" in line:
                raise HostsError(f"{where}: option {keyword} must be one line")
        out[keyword] = value[0] if isinstance(value, list) and len(value) == 1 else value
    return out


def lines(keyword: str, value: Value) -> list[str]:
    """An option as ssh_config lines, KEYWORD VALUE, one per value."""
    return [f"{keyword} {v}" for v in (value if isinstance(value, list) else [value])]


def options(inventory: Inventory, host: Host) -> dict[str, Value]:
    """Effective ssh options: inventory defaults, overridden by the host. Keywords compare case-insensitively."""
    own = host.modules.get("ssh", {}).get("options", {})
    taken = {k.casefold() for k in own}
    merged = {k: v for k, v in inventory.defaults.modules.get("ssh", {}).get("options", {}).items() if k.casefold() not in taken}
    merged.update(own)
    return merged


def parse(text: str, source: str) -> tuple[list[Block], list[str]]:
    """Host blocks that name concrete hosts, plus warnings for everything skipped."""
    blocks: list[Block] = []
    warnings: list[str] = []
    current: Block | None = None
    skipping = False

    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _KEYWORD.match(line)
        if not match:
            warnings.append(f"{source}:{n}: can't read {line!r}, skipped")
            continue
        keyword, value = match.group(1), match.group(2).strip()
        lowered = keyword.lower()

        if lowered == "host":
            tokens = _split(value)
            if not tokens:
                warnings.append(f"{source}:{n}: unreadable Host line, block skipped")
                current, skipping = None, True
            elif any(set(t) & _PATTERN_CHARS for t in tokens):
                warnings.append(f"{source}:{n}: Host {value} is a pattern, not a host; block skipped")
                current, skipping = None, True
            else:
                current, skipping = Block(tokens, n), False
                blocks.append(current)
        elif lowered == "match":
            warnings.append(f"{source}:{n}: Match blocks can't be imported; block skipped")
            current, skipping = None, True
        elif lowered == "include":
            warnings.append(f"{source}:{n}: Include not followed; import that file on its own")
        elif skipping:
            continue
        elif current is None:
            warnings.append(f"{source}:{n}: {keyword} outside any Host block, skipped")
        else:
            current.options.append((keyword, value))

    return blocks, warnings


# Keywords ssh uses every value of when they repeat. Every other keyword is first value wins.
# The record holds one value per keyword, so a repeat of these loses something ssh would use.
ACCUMULATING = {"identityfile", "certificatefile", "localforward", "remoteforward", "dynamicforward", "sendenv"}


def to_hosts(
    block: Block, source: str, warnings: list[str], dropped: list[str] | None = None, keys: dict[str, KeyDef] | None = None
) -> list[Host]:
    """The hosts one block names, or none when it can't be read. Whatever ssh would have used but
    the record can't hold goes into dropped as well as warnings. Each IdentityFile is declared in
    keys, the source's own declarations, once per path.

    What the block doesn't set stays unset, hostname empty and port None, so a block for a host
    the inventory already has, a later one or a re-import, compares and fills only what it states.
    complete() fills the rest for a host that's new. A block without User stays without one: ssh
    would use whoever connects, and pinning the importing login would be wrong on any other
    machine.

    Without HostName, ssh connects to whichever token was typed, so a Host line with several
    tokens names one host per token, each with the block's settings. With HostName, the tokens
    after the first are aliases of it."""
    dropped = [] if dropped is None else dropped
    keys = {} if keys is None else keys
    name, *aliases = block.tokens
    host = Host(name=name, hostname="", aliases=aliases)
    where = f"{source}:{block.line} ({name})"
    seen: set[str] = set()
    identities_only: tuple[str, str] | None = None
    extra: dict[str, str] = {}

    for keyword, value in block.options:
        lowered = keyword.lower()
        if lowered in seen:
            if lowered in ACCUMULATING:
                message = f"{where}: second {keyword} not kept; ssh uses every {keyword}, ari stores one"
                warnings.append(message)
                dropped.append(message)
            else:
                warnings.append(f"{where}: second {keyword} ignored, ssh uses the first")
            continue
        seen.add(lowered)
        match lowered:
            case "hostname":
                host.hostname = _single(value)
            case "user":
                host.user = _single(value)
            case "port":
                try:
                    host.port = check_port(int(_single(value)), where)
                except (ValueError, HostsError):
                    message = f"{where}: Port {value!r} is not a port number; host skipped"
                    warnings.append(message)
                    dropped.append(message)
                    return []
            case "identityfile":
                host.keys = [declare_key(keys, _single(value))]
            case "identitiesonly":
                identities_only = (keyword, value)
            case _:
                extra[keyword] = value

    # Export writes "IdentitiesOnly yes" after the keys. Keep anything that would resolve differently.
    if identities_only and not (host.keys and identities_only[1].lower() == "yes"):
        extra[identities_only[0]] = identities_only[1]
    elif host.keys and not identities_only:
        extra["IdentitiesOnly"] = "no"

    if extra:
        host.modules["ssh"] = {"options": extra}
    if host.hostname or not aliases:
        return [host]
    hosts = []
    for token in block.tokens:
        one = copy.deepcopy(host)
        one.name, one.aliases = token, []
        hosts.append(one)
    return hosts


def render(inventory: Inventory, hosts: list[Host]) -> str:
    out = [f"# Generated by ari from {inventory.path.name}. Do not edit.", ""]
    for host in sorted(hosts, key=lambda h: h.name.casefold()):
        for note in host.notes.splitlines():
            out.append(f"# {note}".rstrip())
        out.append("Host " + " ".join(_quote(t) for t in host.tokens()))
        out.append(f"    HostName {_quote(host.hostname)}")
        user = inventory.user(host)
        if user:
            out.append(f"    User {_quote(user)}")
        port = inventory.port(host)
        if port != 22:
            out.append(f"    Port {port}")
        extra = options(inventory, host)
        files = inventory.identity_files(host)
        out += [f"    IdentityFile {_quote(path)}" for path in files]
        if files and not any(k.lower() == "identitiesonly" for k in extra):
            out.append("    IdentitiesOnly yes")
        out += [f"    {line}" for keyword, value in extra.items() for line in lines(keyword, value)]
        out.append("")
    return "\n".join(out)


class SshModule(Module):
    name = "ssh"
    summary = "OpenSSH client config, one file per inventory"
    exports = True
    imports = True

    def settings(self, table: dict[str, Any], where: str) -> Settings:
        table = dict(table)
        path = table.pop("path", None)
        if table:
            raise HostsError(f"{where}: unknown keys {sorted(table)}")
        if not isinstance(path, str) or not path:
            raise HostsError(f'{where}: needs path = "~/.ssh/config.d/NAME.conf"')
        resolved = Path(path).expanduser()
        if not resolved.is_absolute():
            raise HostsError(f"{where}.path: use an absolute path or one starting with ~")
        return Settings(resolved)

    def host_data(self, data: dict[str, Any], where: str) -> dict[str, Any]:
        unknown = set(data) - {"options"}
        if unknown:
            raise HostsError(f"{where}: unknown keys {sorted(unknown)}")
        if "options" in data:
            return {"options": _options_map(data["options"], where)}
        return {}

    def defaults_data(self, data: dict[str, Any], where: str) -> dict[str, Any]:
        return self.host_data(data, where)

    def strip_defaults(self, defaults: dict[str, Any], data: dict[str, Any]) -> None:
        own = data.get("options", {})
        for k, v in defaults.get("options", {}).items():
            if own.get(k) == v:
                del own[k]
        if "options" in data and not own:
            del data["options"]

    def conflicts(self, existing: dict[str, Any], incoming: dict[str, Any], defaults: dict[str, Any]) -> list[str]:
        """An option the record already has, from the host or the inventory default, with a
        different value in the source. Keywords compare the way ssh reads them, case-insensitively."""
        effective = {k.casefold(): (k, v) for k, v in defaults.get("options", {}).items()}
        effective.update({k.casefold(): (k, v) for k, v in existing.get("options", {}).items()})
        out = []
        for keyword, value in incoming.get("options", {}).items():
            have = effective.get(keyword.casefold())
            if have and have[1] != value:
                out.append(f"{keyword} {value} differs from {have[1]}")
        return out

    def merge(self, existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
        own = existing.setdefault("options", {})
        taken = {k.casefold() for k in own}
        changed = False
        for k, v in incoming.get("options", {}).items():
            if k.casefold() not in taken:
                own[k] = v
                taken.add(k.casefold())
                changed = True
        if not own:
            del existing["options"]
        return changed

    def complete(self, host: Host) -> None:
        """Without HostName ssh connects to the name itself, and without Port to 22, ssh's own
        default. Like any field, the port is then stored only when it differs from the inventory
        default."""
        host.hostname = host.hostname or host.name
        if host.port is None:
            host.port = 22

    def describe(self, inventory: Inventory, host: Host) -> list[str]:
        return [line for k, v in options(inventory, host).items() for line in lines(k, v)]

    def export(self, inventory: Inventory, hosts: list[Host], settings: Settings) -> list[Output]:
        return [Output(settings.path, render(inventory, hosts).encode("utf-8"), len(hosts))]

    def read(self, source: str | None) -> ImportResult:
        if not source:
            raise HostsError("ssh import needs a file: ari import ssh FILE")
        path = Path(source).expanduser()
        try:
            data = path.read_bytes()
            text = data.decode("utf-8")
        except OSError as e:
            raise HostsError(f"{tilde(path)}: {e.strerror}") from None
        except UnicodeDecodeError:
            raise HostsError(f"{tilde(path)}: not UTF-8 text") from None
        blocks, warnings = parse(text, tilde(path))
        skipped = bool(warnings)  # everything parse warns about is left out
        dropped: list[str] = []
        keys: dict[str, KeyDef] = {}
        hosts = [h for b in blocks for h in to_hosts(b, tilde(path), warnings, dropped, keys)]
        return ImportResult(hosts, warnings, [(path, data)], lossy=skipped or bool(dropped), keys=keys)
