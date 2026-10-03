---
title: ARI
section: 1
header: User Commands
footer: ari 0.3.0
date: October 2026
---

# NAME

ari - keep SSH hosts in one place and generate ssh config from them

# SYNOPSIS

**ari** [**-i** *INV*] *COMMAND* [*ARGS*]

**ari ls** [**--search** *TEXT*] [**--group** *GROUP*]

**ari show** *NAME*

**ari modules**

**ari import** *MODULE* [*SOURCE*] [**--exclude** *MODULE*]

**ari export** [*MODULE* ...] [**--force**]

# DESCRIPTION

**ari** keeps one record per SSH host in JSON inventories and generates files from them through modules. Each inventory, such as *personal* or *work*, is declared in **config.toml** with the modules it uses. The built-in **ssh** module imports and writes OpenSSH client config.

Names and aliases are unique across all inventories, compared case-insensitively the way ssh compares them. Every generated file lands in one ssh namespace where the first match wins, so a duplicate would silently shadow a host.

Generated files are output, not something to edit. **ari** records a hash of every file it writes and refuses to overwrite one that changed since.

With no arguments, **ari** prints help.

# THE NAME

**ari** is Turkish for bee. Bees spend their days building the hive and stocking it, and that is the whole job here: keep every host in one place, and build ssh config and Ansible inventory from it.

The name started with a plush hive. My son has one with five bees, and he loves it. One of the bees lives on my desk.

In Hebrew, ari means lion. When Samson came back to the lion he had killed, he found bees and honey inside it:

> And after a time he returned to take her, and he turned aside to see the carcase of the lion: and, behold, there was a swarm of bees and honey in the carcase of the lion.
>
> Judges 14:8 (KJV)

Out of the strong came forth sweetness.

# COMMANDS

## ls

List hosts across every inventory, or only the one named with **-i**. User and port show effective values, with inventory defaults filled in.

| Option | Meaning |
|-------|-------------|
| **--search** *TEXT* | Only hosts with *TEXT* in any field, case-insensitive |
| **--group** *GROUP* | Only hosts in *GROUP* |

## show *NAME*

Show one host, found by name or alias in any inventory, with every field. Values inherited from inventory defaults are marked **(default)**.

## modules

List the installed modules, whether each can import and export, and which inventories switch it on. Modules that failed to load are listed with the reason.

## import *MODULE* [*SOURCE*]

Read hosts into one inventory through *MODULE*. A host already in the inventory is merged: new aliases and module data are added. A different HostName, User, Port or IdentityFile is reported as a conflict and left alone. A host whose name or alias already belongs to another inventory is refused.

Importing into an empty inventory sets its default user and key to the values most hosts share, and each host stores only what differs. A host the source gives no user or key inherits the defaults, with a warning naming them. Import records the hash of every file it reads, so exporting over that same file afterwards passes the guard.

| Option | Meaning |
|-------|-------------|
| **--exclude** *MODULE* | Keep the imported hosts out of *MODULE*'s output; repeatable |

With the **ssh** module, *SOURCE* is a config file. It reads the Host blocks in that file. The first token on a Host line becomes the name and the rest become aliases. **HostName**, **User**, **Port** and **IdentityFile** become fields, **IdentitiesOnly yes** is implied by a key, and every other keyword is kept as an ssh option, in order.

Pattern Host blocks, Match blocks, Include lines and options outside any Host block are skipped with a warning. When a keyword repeats inside a block, the first value is kept, as ssh does. A block without **User** stays without one, so ssh uses whoever connects; a block without **Port** is stored as 22, ssh's own default.

## export [*MODULE* ...]

Write the output of every enabled module of every inventory, or only the named modules, or one inventory with **-i**. Validation and the guard check run first; if any fails, nothing is written.

| Option | Meaning |
|-------|-------------|
| **--force** | Overwrite files that were edited since the last export, or never written by **ari** |

# OPTIONS

| Option | Meaning |
|-------|-------------|
| **-i**, **--inventory** *INV* | Inventory to act on; accepted before or after the command |
| **-h**, **--help** | Show help for **ari** or for one command |
| **--version** | Show the version |

**import** writes to one inventory: **-i**, then **ARI_INVENTORY**, then **default** from config.toml. **ls** and **export** cover every inventory unless **-i** narrows them.

# CONFIGURATION

**config.toml** declares the inventories and the modules each one uses, one table per module. **ari** reads it and never writes it.

```toml
default = "personal"

[inventories.personal.ssh]
path = "~/.ssh/config.d/10-personal.conf"

[inventories.work]
file = "~/work/infra/ari/work.json"

[inventories.work.ssh]
path = "~/.ssh/config.d/20-work.conf"
enabled = false
```

| Key | Meaning |
|-------|-------------|
| **default** | Inventory used by **import** when **-i** and **ARI_INVENTORY** are unset |
| `inventories.NAME.file` | Inventory data; defaults to `NAME.json` beside config.toml |
| `inventories.NAME.MODULE` | A module this inventory uses, with that module's settings |
| `inventories.NAME.MODULE.enabled` | **false** parks the module without losing its settings |
| `inventories.NAME.ssh.path` | Generated ssh config; absolute or starting with **~** |

A table for a module that isn't installed is an error, unless it says **enabled = false**.

**~/.ssh/config** pulls the generated files in with one line, placed before any Host block:

```
Include config.d/*.conf
```

# MODULES

A module turns an inventory into files, reads a source into hosts, or both. Modules never write to disk themselves: they hand **ari** paths and contents, and **ari** does the writing, so the guard, the atomic writes and the all-or-nothing validation cover every module the same way.

Modules are found through the Python entry point group **ari.modules**. The built-in **ssh** module registers there like any other. A third-party module is a package that registers in that group; install it into ari's environment with **uv tool install ari --with** *PACKAGE*.

# INVENTORY FILES

Each inventory is one JSON file. Hosts store only what differs from the inventory **defaults**.

```json
{
  "version": 2,
  "last_updated": "2026-10-02T21:14:00+01:00",
  "defaults": {"user": "tom", "ssh_key": "~/.ssh/id-ed25519"},
  "groups": {},
  "hosts": [
    {
      "name": "nas",
      "hostname": "192.0.2.254",
      "aliases": ["storage"],
      "user": "root",
      "modules": {"ssh": {"options": {"RequestTTY": "yes"}}},
      "last_updated": "2026-10-02T21:14:00+01:00"
    }
  ]
}
```

| Field | Meaning |
|-------|-------------|
| **name** | ssh alias; required |
| **hostname** | Address ssh connects to; required |
| **aliases** | More names on the Host line |
| **user**, **port**, **ssh_key** | Override the inventory defaults |
| **notes** | Written as a comment above the Host block |
| **groups**, **reasons** | Group membership, and why; for modules that use groups |
| **exclude** | Modules whose output leaves this host out |
| **modules** | Each module's own data, under its name |
| `modules.ssh.options` | Any other ssh_config keywords, written in order |

Data for a module that isn't installed is kept as it is, so removing a plugin never loses anything. A version 1 file, from ari 0.2, is upgraded when it's read and saved as version 2 on its next write.

A file that fails to parse stops **ari** with the path and the error. It is never treated as empty.

# GENERATED SSH CONFIG

Hosts are sorted by name. Each block gets **HostName**, **User** when one is set, **Port** when it isn't 22, and **IdentityFile** with **IdentitiesOnly yes** when a key is set, followed by the host's other options. There are no `Host *` blocks: they ignore file boundaries, and **IdentityFile** accumulates across matching blocks, so a default in one file would offer its key to every host.

Every write goes to `FILE.tmp` beside the target and is then renamed over it. `Include config.d/*.conf` never matches the `.tmp`, so ssh never reads a half-written file.

# FILES

| File | Contents |
|-------|-------------|
| `config.toml` | Inventories and their modules |
| `NAME.json` | Inventory data, one file per inventory |
| `exports.json` | Hashes of exported files, for the guard |

`config.toml` and the inventories live in `~/.config/ari/`, and `exports.json` in `~/.local/state/ari/`. **XDG_CONFIG_HOME** and **XDG_STATE_HOME** move them. Losing the state file only means the guard refuses existing targets until the next import or **--force**.

# ENVIRONMENT

| Variable | Effect |
|-------|-------------|
| **ARI_INVENTORY** | Inventory for **import** when **-i** is not given |
| **XDG_CONFIG_HOME** | Where config.toml and inventories live |
| **XDG_STATE_HOME** | Where the guard state lives |

# EXIT STATUS

**0** on success. **1** on an error, a validation failure, a guard refusal or an import conflict. **2** on a usage error. **130** when interrupted.

# EXAMPLES

Take over an existing ssh config file, then regenerate it:

```
ari import ssh ~/.ssh/config.d/10-personal.conf -i personal
ari export
```

Bring in hand-kept hosts that must never reach Ansible:

```
ari import ssh ~/.ssh/config.d/20-work.conf -i work --exclude ansible
```

Find every host on a subnet:

```
ari ls --search 192.0.2.
```

Accept a file after reviewing a hand edit:

```
ari export --force
```

# SEE ALSO

**ssh**(1), **ssh_config**(5)
