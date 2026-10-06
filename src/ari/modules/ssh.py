"""ssh: OpenSSH client config. Imports Host blocks; exports one file per inventory."""

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import HostsError
from ..models import Host, Inventory, KeyDef, check_port, declare_key
from ..paths import tilde
from . import ImportResult, Module, Output

# A keyword, then what ssh's strdelim skips after it: whitespace, or one = with whitespace around it.
_KEYWORD = re.compile(r"([^ \t\r\n\"=]+)(?:(?:[ \t\r\n]*=|[ \t\r\n])[ \t\r\n]*(.*))?", re.S)
_PATTERN_CHARS = set("*?!")

# Commands ssh takes as the rest of the line, quotes and any # included, for a shell to read.
_WHOLE = {"proxycommand", "localcommand", "remotecommand", "knownhostscommand"}

# Keywords that take exactly one value; more is an error that stops ssh reading the file.
_ONE = {"hostname", "user", "port", "identityfile"}


@dataclass
class Block:
    tokens: list[str]
    line: int
    # Each line as (keyword, its value as stored, its values as ssh splits them).
    options: list[tuple[str, str, list[str]]] = field(default_factory=list)


@dataclass(frozen=True)
class Settings:
    path: Path


def _args(text: str) -> tuple[list[str], int] | None:
    """The values on a line as OpenSSH's argv_split reads them, and where they end: at the end of
    text, or at a # that starts a value, which comments out the rest. Only space and tab separate
    values. Double and single quotes group, and a value runs on after a closing quote. A backslash
    goes only before a quote, another backslash, or a space outside quotes; anywhere else it stays.
    None when a quote never closes, which ssh refuses."""
    tokens: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] in " \t":
            i += 1
            continue
        if text[i] == "#":
            break
        quote, token = "", []
        while i < n:
            ch = text[i]
            following = text[i + 1] if i + 1 < n else ""
            if ch == "\\" and following and (following in "'\"\\" or (not quote and following == " ")):
                token.append(following)
                i += 1
            elif not quote and ch in " \t":
                break
            elif not quote and ch in "\"'":
                quote = ch
            elif quote and ch == quote:
                quote = ""
            else:
                token.append(ch)
            i += 1
        if quote:
            return None
        tokens.append("".join(token))
    return tokens, i


def _value(keyword: str, rest: str, args: list[str], end: int) -> tuple[str | None, str]:
    """What a line's keyword gets, as the record stores it, or None and why ssh refuses the line.
    The commands ssh takes whole keep the rest of the line; ProxyJump keeps it up to its first #, as
    ssh cuts it; a field takes its one value; any other option keeps its text up to a comment, quotes
    and all, so it's written back as ssh read it."""
    if keyword in _WHOLE or keyword == "proxyjump":
        value = rest.lstrip(" \t\r\n=")
        if keyword == "proxyjump":
            value = value.partition("#")[0].rstrip(" \t")
        return (value, "") if value else (None, "no value")
    if not args or (keyword in _ONE and not args[0]):
        return None, "no value"
    if keyword in _ONE:
        return (args[0], "") if len(args) == 1 else (None, "more than one value")
    return rest[:end].rstrip(" \t"), ""


def _clean(raw: str) -> str:
    """A line as ssh takes it before reading: cut at a NUL, its whitespace trimmed."""
    return raw.partition("\0")[0].rstrip(" \t\r\n\f").lstrip(" \t\r\n")


def _reading(keyword: str, rest: str) -> tuple[str | None, str, list[str]]:
    """What ssh makes of a keyword and the rest of its line: the value as the record stores it, or
    None and why ssh refuses the line, and the values as ssh splits them."""
    parsed = _args(rest)
    args, end = parsed if parsed is not None else ([], 0)
    if not rest:
        return None, "no value", args
    if parsed is None:
        return None, "an unclosed quote", args
    if keyword.lower() == "host":
        return (None, "an empty name", args) if "" in args else (rest, "", args)
    value, why = _value(keyword.lower(), rest, args, end)
    return value, why, args


def _quote(value: str) -> str:
    """A field as ssh_config text that ssh reads back as value: as it is when it reads back
    unchanged, else in double quotes with backslashes and quotes escaped. Unquoted, a leading #
    starts a comment, ssh takes a leading = as part of what follows the keyword, and two
    backslashes read as one."""
    bare = value and value[0] not in "#=" and not any(c.isspace() or c in "\"'" for c in value)
    if bare and _args(value) == ([value], len(value)):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _field_problem(what: str, value: str) -> str | None:
    """A field no quoting can write: ssh_config ends a line at a newline, and at a NUL."""
    if "\n" in value or "\0" in value:
        return f"{what} {value!r} can't be written: ssh_config ends a line at a newline or a NUL"
    return None


def unwritable(keyword: str, value: str) -> str | None:
    """Why ssh wouldn't read value from the line export writes for an option, or None when it
    would. Options are stored as ssh_config text, a line's worth of values, so export writes them
    as they are and can't quote them: one ssh reads differently can't be stored. The reading is
    import's, so what import stores always passes."""
    match = _KEYWORD.fullmatch(_clean(f"{keyword} {value}"))
    read, why, _ = _reading(keyword, match.group(2) or "") if match else (None, "", [])
    if read == value:
        return None
    if why == "an unclosed quote":
        return "an unclosed quote, which makes ssh refuse the whole file"
    reads = f"would read {read!r}" if read else "would read no value"
    parsed = _args(value)
    if keyword.lower() == "proxyjump" and "#" in value:
        return f"ssh cuts ProxyJump at its first #, quoted or not, and {reads}"
    if keyword.lower() not in _WHOLE and parsed is not None and value[parsed[1] :].strip(" \t"):
        return f"a # that starts a value comments out the rest, and ssh {reads}"
    if value and (value != value.strip(" \t\r\f") or value.startswith("=")):
        return f"ssh drops a leading = and whitespace at either end, and {reads}"
    return f"ssh {reads}"


# Keywords the host record or the block structure already covers. As options they'd be written a
# second time, and ssh would quietly take the first.
_OWN_FIELD = {"host": None, "match": None, "hostname": "hostname", "user": "user", "port": "port", "identityfile": "keys"}

# Keywords ssh uses every value of when they repeat, in a block or across the blocks a host
# matches. Every other keyword is first value wins.
ACCUMULATING = {"identityfile", "certificatefile", "localforward", "remoteforward", "dynamicforward", "sendenv"}

# Options that can hold a list, one line each: the accumulating ones, IdentityFile being the keys field.
LISTABLE = ACCUMULATING - {"identityfile"}

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


def _shown(value: Value) -> str:
    return ", ".join(value) if isinstance(value, list) else value


def lines(keyword: str, value: Value) -> list[str]:
    """An option as ssh_config lines, KEYWORD VALUE, one per value."""
    return [f"{keyword} {v}" for v in (value if isinstance(value, list) else [value])]


def option_problems(options: dict[str, Value]) -> list[str]:
    """Every value of these options that ssh wouldn't read as stored, one problem each."""
    return [
        f"ssh option {keyword} {one!r} can't be written: {why}"
        for keyword, value in options.items()
        for one in (value if isinstance(value, list) else [value])
        if (why := unwritable(keyword, one))
    ]


def options(inventory: Inventory, host: Host) -> dict[str, Value]:
    """Effective ssh options: inventory defaults, overridden by the host. Keywords compare case-insensitively."""
    own = host.modules.get("ssh", {}).get("options", {})
    taken = {k.casefold() for k in own}
    merged = {k: v for k, v in inventory.defaults.modules.get("ssh", {}).get("options", {}).items() if k.casefold() not in taken}
    merged.update(own)
    return merged


def parse(text: str, source: str) -> tuple[list[Block], list[str]]:
    """Host blocks that name concrete hosts, plus warnings for everything skipped. Lines are read
    the way ssh reads them: one ends only at \\n, or at a NUL, and its trailing whitespace goes. A
    line ssh refuses stops ssh reading the whole file, so it's skipped and warned about."""
    blocks: list[Block] = []
    warnings: list[str] = []
    current: Block | None = None
    skipping = False

    for n, raw in enumerate(text.split("\n"), 1):
        line = _clean(raw)
        if not line or line.startswith("#"):
            continue
        match = _KEYWORD.fullmatch(line)
        if not match:
            warnings.append(f"{source}:{n}: can't read {line!r}, skipped")
            continue
        keyword = match.group(1)
        lowered = keyword.lower()
        value, why, args = _reading(keyword, match.group(2) or "")
        refused = f"{source}:{n}: ssh refuses this line ({why}), and with it the whole file"

        if lowered == "host":
            if value is None:
                warnings.append(f"{refused}; block skipped")
                current, skipping = None, True
            elif not args:
                # Only a comment: ssh starts a block that matches nothing.
                warnings.append(f"{source}:{n}: Host line names no host; block skipped")
                current, skipping = None, True
            elif any(set(t) & _PATTERN_CHARS for t in args):
                warnings.append(f"{source}:{n}: Host {' '.join(args)} is a pattern, not a host; block skipped")
                current, skipping = None, True
            else:
                current, skipping = Block(args, n), False
                blocks.append(current)
        elif lowered == "match":
            warnings.append(f"{source}:{n}: Match blocks can't be imported; block skipped")
            current, skipping = None, True
        elif lowered == "include":
            warnings.append(f"{source}:{n}: Include not followed; import that file on its own")
        elif skipping:
            continue
        elif value is None:
            warnings.append(f"{refused}; skipped")
        elif current is None:
            warnings.append(f"{source}:{n}: {keyword} outside any Host block, skipped")
        else:
            current.options.append((keyword, value, args))

    return blocks, warnings


def to_hosts(
    block: Block,
    source: str,
    warnings: list[str],
    dropped: list[str] | None = None,
    keys: dict[str, KeyDef] | None = None,
    settle: bool = True,
) -> list[Host]:
    """The hosts one block names, or none when it can't be read. Whatever ssh would have used but
    the record can't hold goes into dropped as well as warnings. Every IdentityFile becomes one of
    the host's keys, in order, declared in keys, the source's own declarations, once per path.
    Every line of another accumulating keyword is kept, several making a list.

    What the block doesn't set stays unset, hostname empty and port None, so a block for a host
    the inventory already has, a later one or a re-import, compares and fills only what it states.
    complete() fills the rest for a host that's new. A block without User stays without one: ssh
    would use whoever connects, and pinning the importing login would be wrong on any other
    machine. settle=False leaves IdentitiesOnly as stated, for _gather to settle once every block
    for a name is in.

    Without HostName, ssh connects to whichever token was typed, so a Host line with several
    tokens names one host per token, each with the block's settings. With HostName, the tokens
    after the first are aliases of it."""
    dropped = [] if dropped is None else dropped
    keys = {} if keys is None else keys
    name, *aliases = block.tokens
    host = Host(name=name, hostname="", aliases=aliases)
    where = f"{source}:{block.line} ({name})"
    seen: set[str] = set()
    extra: dict[str, Value] = {}
    listed: dict[str, str] = {}  # an accumulating keyword's first spelling, which its list goes under

    for keyword, value, _ in block.options:
        lowered = keyword.lower()
        if lowered == "identityfile":
            name = declare_key(keys, value)
            if name in host.keys:
                warnings.append(f"{where}: IdentityFile {value} given twice; kept once")
            else:
                host.keys.append(name)
            continue
        if lowered in LISTABLE:
            first = listed.setdefault(lowered, keyword)
            have = extra.get(first)
            extra[first] = value if have is None else [*(have if isinstance(have, list) else [have]), value]
            continue
        if lowered in seen:
            warnings.append(f"{where}: second {keyword} ignored, ssh uses the first")
            continue
        seen.add(lowered)
        match lowered:
            case "hostname":
                host.hostname = value
            case "user":
                host.user = value
            case "port":
                try:
                    host.port = check_port(int(value), where)
                except (ValueError, HostsError):
                    message = f"{where}: Port {value!r} is not a port number; host skipped"
                    warnings.append(message)
                    dropped.append(message)
                    return []
            case _:
                extra[keyword] = value

    if extra:
        host.modules["ssh"] = {"options": extra}
    if settle:
        _settle(host)
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


def _tidy(host: Host) -> None:
    """Drop the ssh options table, and the module's entry, once they're empty."""
    ssh = host.modules.get("ssh")
    if ssh is not None and not ssh.get("options"):
        ssh.pop("options", None)
        if not ssh:
            del host.modules["ssh"]


def _settle(host: Host) -> None:
    """Export writes IdentitiesOnly yes after the keys, so a stated yes on a host with keys goes
    without saying. Keep anything that would resolve differently: a no, or keys with none stated."""
    options = host.modules.setdefault("ssh", {}).setdefault("options", {})
    stated = next((k for k in options if k.lower() == "identitiesonly"), None)
    if stated and host.keys and options[stated].lower() == "yes":
        del options[stated]
    elif host.keys and not stated:
        options["IdentitiesOnly"] = "no"
    _tidy(host)


def _gather(blocks: list[tuple[list[str], list[Host]]]) -> tuple[list[Host], list[set[str]]]:
    """ssh uses every value of an accumulating keyword from every block a name matches. A later
    block naming every token of a host from an earlier one applies wherever that host does, so its
    keys and accumulating options join that host's, and IdentitiesOnly is the first block's that
    states it. What else it says reaches import as before, filling what the first left unset. A
    later block naming only some of them applies to those alone, which one record can't say, so it
    stays as it is, and import refuses whatever it would change. ssh matches Host tokens as typed,
    case included, and so does this. Returns the hosts, and for each the tokens of its block."""
    out: list[Host] = []
    covers: list[set[str]] = []
    index: dict[str, Host] = {}  # each token, by the host that first had it
    for tokens, hosts in blocks:
        named = set(tokens)
        given: list[Host] = []  # earlier hosts this block's values went to: a block split by token adds once
        for host in hosts:
            out.append(host)
            covers.append(set(tokens))
            earlier = index.get(host.name)
            if earlier is None:
                for token in host.tokens():
                    index.setdefault(token, host)
            elif set(earlier.tokens()) <= named:
                if not any(e is earlier for e in given):
                    _add(earlier, host)
                    given.append(earlier)
                _strip(host)
    return out, covers


def _accumulated(host: Host) -> dict[str, Value]:
    """A host's accumulating options and IdentitiesOnly, taken off it."""
    options = host.modules.get("ssh", {}).get("options", {})
    taken = {k: options.pop(k) for k in list(options) if k.lower() in LISTABLE or k.lower() == "identitiesonly"}
    return taken


def _strip(host: Host) -> None:
    host.keys = []
    _accumulated(host)
    _tidy(host)


def _add(earlier: Host, host: Host) -> None:
    """A later block's keys and accumulating options after the earlier host's own, and its
    IdentitiesOnly where the earlier host states none."""
    earlier.keys += [k for k in host.keys if k not in earlier.keys]
    ours = earlier.modules.setdefault("ssh", {}).setdefault("options", {})
    for keyword, value in _accumulated(host).items():
        mine = next((k for k in ours if k.casefold() == keyword.casefold()), None)
        if keyword.lower() == "identitiesonly":
            if mine is None:
                ours[keyword] = value
            continue
        values = [*_values(ours.get(mine)), *_values(value)]
        ours[mine or keyword] = values[0] if len(values) == 1 else values
    _tidy(earlier)


def _values(value: Value | None) -> list[str]:
    return [] if value is None else value if isinstance(value, list) else [value]


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
        different value in the source, a list compared whole. Keywords compare the way ssh reads
        them, case-insensitively."""
        effective = {k.casefold(): (k, v) for k, v in defaults.get("options", {}).items()}
        effective.update({k.casefold(): (k, v) for k, v in existing.get("options", {}).items()})
        out = []
        for keyword, value in incoming.get("options", {}).items():
            have = effective.get(keyword.casefold())
            if have and have[1] != value:
                out.append(f"{keyword} {_shown(value)} differs from {_shown(have[1])}")
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

    def host_problems(self, inventory: Inventory, host: Host) -> list[str]:
        """The host's own options, each value as ssh would read it from the line export writes."""
        return option_problems(host.modules.get("ssh", {}).get("options", {}))

    def validate(self, inventory: Inventory, hosts: list[Host], settings: Settings) -> list[str]:
        """Everything export writes reads back in ssh as the record holds it: the defaults'
        options and user, and each host's names, address, user, key files and options."""
        problems = [f"defaults: {p}" for p in option_problems(inventory.defaults.modules.get("ssh", {}).get("options", {}))]
        if inventory.defaults.user and (p := _field_problem("user", inventory.defaults.user)):
            problems.append(f"defaults: {p}")
        for host in hosts:
            fields = [("name", host.name), *(("alias", a) for a in host.aliases), ("hostname", host.hostname)]
            fields += [("user", host.user)] if host.user else []
            problems += [f"{host.name}: {p}" for what, v in fields if (p := _field_problem(what, v))]
            problems += [f"{host.name}: {p}" for p in self.host_problems(inventory, host)]
        for name in dict.fromkeys(name for host in hosts for name in inventory.host_keys(host)):
            key = inventory.keys.get(name)
            if key is not None and (p := _field_problem("path", key.path)):
                problems.append(f"key {name}: {p}")
        return problems

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
        hosts, covers = _gather([(b.tokens, to_hosts(b, tilde(path), warnings, dropped, keys, settle=False)) for b in blocks])
        for host in hosts:
            _settle(host)
        return ImportResult(hosts, warnings, [(path, data)], lossy=skipped or bool(dropped), keys=keys, covers=covers)
