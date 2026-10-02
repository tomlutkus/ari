---
title: HOSTS
section: 1
header: User Commands
footer: hosts-inventory-manager 0.1.0
date: October 2026
---

# NAME

hosts - keep SSH hosts in one place and generate ssh config from them

# SYNOPSIS

**hosts** [**-i** *INV*] *COMMAND* [*ARGS*]

**hosts ls** [**--search** *TEXT*] [**--group** *GROUP*]

**hosts show** *NAME*

**hosts import ssh** *FILE* [**--no-ansible**]

**hosts export** [**ssh** | **ansible**] [**--force**]

# DESCRIPTION

**hosts** keeps one record per SSH host in JSON inventories and writes ssh config files from them. Each inventory, such as *personal* or *work*, is declared in **config.toml** with its own export targets.

Names and aliases are unique across all inventories, compared case-insensitively the way ssh compares them. Every generated file lands in one ssh namespace where the first match wins, so a duplicate would silently shadow a host.

Generated files are output, not something to edit. **hosts** records a hash of every file it writes and refuses to overwrite one that changed since.

With no arguments, **hosts** prints help.

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

## import ssh *FILE*

Read the Host blocks in *FILE* into one inventory. The first token on a Host line becomes the name and the rest become aliases. **HostName**, **User**, **Port** and **IdentityFile** become fields, **IdentitiesOnly yes** is implied by a key, and every other keyword is kept as an ssh option, in order.

Pattern Host blocks, Match blocks, Include lines and options outside any Host block are skipped with a warning. When a keyword repeats inside a block, the first value is kept, as ssh does.

A host already in the inventory is merged: new aliases and options are added. A different HostName, User, Port or IdentityFile is reported as a conflict and left alone. A host whose name or alias already belongs to another inventory is refused.

Importing into an empty inventory sets its default user and key to the values most hosts share, and each host stores only what differs. Import records the hash of *FILE*, so exporting over that same file afterwards passes the guard.

| Option | Meaning |
|-------|-------------|
| **--no-ansible** | Mark imported hosts as ssh only, never exported to Ansible |

## export [ssh | ansible]

Write every target of every inventory, or one kind of output, or one inventory with **-i**. Validation and the guard check run first; if any fails, nothing is written.

Ansible export is not implemented yet. The target is accepted and skipped with a note.

| Option | Meaning |
|-------|-------------|
| **--force** | Overwrite files that were edited since the last export, or never written by **hosts** |

# OPTIONS

| Option | Meaning |
|-------|-------------|
| **-i**, **--inventory** *INV* | Inventory to act on; accepted before or after the command |
| **-h**, **--help** | Show help for **hosts** or for one command |
| **--version** | Show the version |

**import** writes to one inventory: **-i**, then **HOSTS_INVENTORY**, then **default** from config.toml. **ls** and **export** cover every inventory unless **-i** narrows them.

# CONFIGURATION

**config.toml** declares the inventories and where each one exports. **hosts** reads it and never writes it.

```toml
default = "personal"

[inventories.personal]
ssh = "~/.ssh/config.d/10-personal.conf"

[inventories.work]
file = "~/work/infra/hosts-inventory/work.json"
ssh = "~/.ssh/config.d/20-work.conf"
```

| Key | Meaning |
|-------|-------------|
| **default** | Inventory used by **import** when **-i** and **HOSTS_INVENTORY** are unset |
| `inventories.NAME.file` | Inventory data; defaults to `NAME.json` beside config.toml |
| `inventories.NAME.ssh` | Generated ssh config; absolute or starting with **~** |
| `inventories.NAME.ansible` | Reserved for Ansible export |

**~/.ssh/config** pulls the generated files in with one line, placed before any Host block:

```
Include config.d/*.conf
```

# INVENTORY FILES

Each inventory is one JSON file. Hosts store only what differs from the inventory **defaults**.

```json
{
  "version": 1,
  "last_updated": "2026-10-02T21:14:00+01:00",
  "defaults": {"user": "tom", "ssh_key": "~/.ssh/id-ed25519"},
  "groups": {},
  "hosts": [
    {
      "name": "nas",
      "hostname": "192.0.2.254",
      "aliases": ["storage"],
      "user": "root",
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
| **ssh_options** | Any other ssh_config keywords, written in order |
| **notes** | Written as a comment above the Host block |
| **groups**, **reasons** | Ansible group membership; used once Ansible export exists |
| **ansible** | **false** keeps the host out of Ansible export |

A file that fails to parse stops **hosts** with the path and the error. It is never treated as empty.

# GENERATED SSH CONFIG

Hosts are sorted by name. Each block gets **HostName** and **User**, **Port** when it isn't 22, and **IdentityFile** with **IdentitiesOnly yes** when a key is set, followed by the host's other options. There are no `Host *` blocks: they ignore file boundaries, and **IdentityFile** accumulates across matching blocks, so a default in one file would offer its key to every host.

Every write goes to `FILE.tmp` beside the target and is then renamed over it. `Include config.d/*.conf` never matches the `.tmp`, so ssh never reads a half-written file.

# FILES

| File | Contents |
|-------|-------------|
| `config.toml` | Inventories and their targets |
| `NAME.json` | Inventory data, one file per inventory |
| `exports.json` | Hashes of exported files, for the guard |

`config.toml` and the inventories live in `~/.config/hosts-inventory/`, and `exports.json` in `~/.local/state/hosts-inventory/`. **XDG_CONFIG_HOME** and **XDG_STATE_HOME** move them. Losing the state file only means the guard refuses existing targets until the next import or **--force**.

# ENVIRONMENT

| Variable | Effect |
|-------|-------------|
| **HOSTS_INVENTORY** | Inventory for **import** when **-i** is not given |
| **XDG_CONFIG_HOME** | Where config.toml and inventories live |
| **XDG_STATE_HOME** | Where the guard state lives |

# EXIT STATUS

**0** on success. **1** on an error, a validation failure, a guard refusal or an import conflict. **2** on a usage error. **130** when interrupted.

# EXAMPLES

Take over an existing ssh config file, then regenerate it:

```
hosts import ssh ~/.ssh/config.d/10-personal.conf -i personal
hosts export
```

Find every host on a subnet:

```
hosts ls --search 192.0.2.
```

Accept a file after reviewing a hand edit:

```
hosts export --force
```

# SEE ALSO

**ssh**(1), **ssh_config**(5)