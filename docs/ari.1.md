---
title: ARI
section: 1
header: User Commands
footer: ari 0.5.0
date: October 2026
---

# NAME

ari - keep SSH hosts in one place and generate ssh config and Ansible inventory from them

# SYNOPSIS

**ari** [**-i** *INV*]

**ari** [**-i** *INV*] *COMMAND* [*ARGS*]

**ari init**

**ari ls** [**--search** *TEXT*] [**--group** *GROUP*]

**ari show** *NAME*

**ari add** *NAME* *HOSTNAME* [*OPTIONS*]

**ari edit** *NAME* [*OPTIONS*]

**ari rm** *NAME*

**ari group** [*NAME* [*OPTIONS*]]

**ari modules**

**ari import** *MODULE* [*SOURCE*] [**--exclude** *MODULE*]

**ari export** [*MODULE* ...] [**--force**]

# DESCRIPTION

**ari** keeps one record per SSH host in JSON inventories and generates files from them through modules. Each inventory, such as *personal* or *work*, is declared in **config.toml** with the modules it uses. The built-in **ssh** module imports and writes OpenSSH client config; the built-in **ansible** module imports and writes an Ansible YAML inventory.

Group membership lives on the host record, so removing a host removes it from every group, and no group can name a host that doesn't exist.

Names and aliases are unique across all inventories, compared case-insensitively the way ssh compares them. Every generated file lands in one ssh namespace where the first match wins, so a duplicate would silently shadow a host.

Generated files are output, not something to edit. **ari** records a hash of every file it writes and refuses to overwrite one that changed since.

With no command on a terminal, **ari** opens a browser over the hosts; see **TUI**. When input or output isn't a terminal, it prints help instead.

# THE NAME

**ari** is Turkish for bee. Bees spend their days building the hive and stocking it, and that is the whole job here: keep every host in one place, and build ssh config and Ansible inventory from it.

The name started with a plush hive. My son has one with five bees, and he loves it. One of the bees lives on my desk.

In Hebrew, ari means lion. When Samson came back to the lion he had killed, he found bees and honey inside it:

> And after a time he returned to take her, and he turned aside to see the carcase of the lion: and, behold, there was a swarm of bees and honey in the carcase of the lion.
>
> Judges 14:8 (KJV)

Out of the strong came forth sweetness.

# COMMANDS

## init

Write a starter **config.toml** and print where it went. The starter declares one inventory, *personal*, with the **ssh** module on, and carries a second, *work*, commented out with an **ansible** table to adapt. When config.toml exists, whatever it holds, **init** refuses and leaves it alone.

## ls

List hosts across every inventory, or only the one named with **-i**. User and port show effective values, with inventory defaults filled in.

Between PORT and GROUPS, each module that writes files for an inventory listed gets a column, headed by its name: **✓** when **export** writes the host there, *excluded* when the host lists that module in **exclude**, and **·** when the host's inventory doesn't have the module on. A parked module gets no column.

GROUPS shows as many of a host's groups as fit beside the other columns, in the order the host lists them, then +*N* for the rest, so a row never wraps on their account; **show** lists them all. Output to a pipe is laid out for 80 columns unless **COLUMNS** says otherwise.

| Option | Meaning |
|-------|-------------|
| **--search** *TEXT* | Only hosts with *TEXT* in any field, case-insensitive. User, port and key match their effective values, defaults included |
| **--group** *GROUP* | Only hosts in *GROUP* |

## show *NAME*

Show one host, found by name or alias in any inventory, with every field. **-i** narrows the search to one inventory. Values inherited from inventory defaults are marked **(default)**, and a group's reason shows in parentheses after it.

## add *NAME* *HOSTNAME*

Add a host to one inventory: **-i**, then **ARI_INVENTORY**, then **default** from config.toml. A value equal to the inventory default isn't stored, so the host follows the default if it changes.

| Option | Meaning |
|-------|-------------|
| **--user** *USER* | Login name |
| **--port** *PORT* | ssh port, 1-65535 |
| **--key** *PATH* | Private key, written as IdentityFile |
| **--notes** *TEXT* | Free text: a comment above the Host block, and the Ansible description |
| **--alias** *ALIAS* | Another name on the Host line; repeatable |
| **--opt** *KEY*=*VALUE* | Another ssh option; repeatable |
| **--group** *GROUP*[:*REASON*] | Join a declared group, with an optional reason key; repeatable |
| **--exclude** *MODULE* | Keep the host out of *MODULE*'s output; repeatable |

## edit *NAME*

Change a host, found by name or alias. **-i** narrows the search to one inventory. Takes every **add** option, plus these:

| Option | Meaning |
|-------|-------------|
| **--hostname** *HOSTNAME* | New address |
| **--rename** *NAME* | New name |
| **--unalias** *ALIAS* | Drop an alias, matched case-insensitively; repeatable |
| **--ungroup** *GROUP* | Leave a group, and its reason with it; repeatable |
| **--include** *MODULE* | Undo an **--exclude**; repeatable |

An empty value clears a field: **--user ''**, **--port ''** and **--key ''** go back to the inventory default, **--notes ''** empties the notes, and **--opt** *KEY*= removes that option. **--group** sets membership exactly as given, so naming a group the host is already in without a reason drops its reason. **--unalias** runs before **--alias**, so **--unalias** *OLD* **--alias** *NEW* swaps one alias for another, a change of case included. **--ungroup** a group the host isn't in, **--unalias** a name that isn't one of its aliases, **--include** a module it doesn't exclude, and clearing an option it doesn't have are errors, not silent no-ops. An edit that changes nothing saves nothing.

## rm *NAME*

Remove a host, found by name or alias, from whichever inventory holds it. **-i** narrows the search. Its group memberships go with it.

## group [*NAME*]

Without *NAME*, list the declared groups of every inventory, or only the one named with **-i**, in the order they're declared. HOSTS counts a group's hosts as Ansible sees them: the ones that list it and the ones any group below it holds, with the direct count beside it when children bring more. CHILDREN, REASONS and ABOUT, the description's first line, share the width the other columns leave and never wrap.

With *NAME*, declare that group in one inventory, **-i**, then **ARI_INVENTORY**, then **default** from config.toml, or change it if it's declared already. A new name needs at least one character and no spaces or colons, so **--group** *GROUP*[:*REASON*] can name it.

| Option | Meaning |
|-------|-------------|
| **--description** *TEXT* | A zone's section header in the hosts file; further lines become comments under it. **''** clears it |
| **--child** *GROUP* | Add a declared group as a child; repeatable |
| **--unchild** *GROUP* | Drop a child; repeatable, and runs before **--child** |
| **--reason** *KEY*=*TEXT* | Add a reason hosts can give for being in the group, or change its text; *KEY*= removes it; repeatable |
| **--rm** | Remove the group; takes no other option |

Nothing a host relies on can go. **--rm** is refused while any host is in the group, directly or through a child, or while another group lists it as a child. **--unchild** is refused when the parent would lose a host it reaches only through that child. Removing a reason is refused while a host gives it. Each refusal names the hosts and how they get there, and nothing is saved.

The other checks match **add** and **edit**: every child is declared, children form no cycle, and every problem is listed at once. Dropping a child or a reason the group doesn't have is an error. A command that changes nothing saves nothing. **export** still applies each module's own rules, such as which Ansible file a group is routed to.

## modules

List the installed modules, whether each can import and export, and which inventories switch it on. Modules that failed to load are listed with the reason. Without config.toml it still lists the modules, with no inventories.

## import *MODULE* [*SOURCE*]

Read hosts into one inventory through *MODULE*. Every host goes through the same checks as **add**: one that fails is refused and reported, and the rest still import. A host already in the inventory is merged: new aliases, groups, options and missing notes are added. A value the source sets differently, whether HostName, User, Port, IdentityFile or an ssh option such as ProxyJump, is a conflict, and that host is left alone. Hosts that merged are saved even when others conflicted or were refused; the exit status is then 1.

Importing into an empty inventory sets its defaults: from the source's own defaults when it has them, like Ansible's **all.vars**, otherwise to the user and key most hosts share. Each host stores only what differs.

A clean import adopts the files it read: their hashes go to the guard, so exporting over them afterwards passes. If anything conflicted, was refused, or couldn't be represented, such as a skipped `Host *` block or a second LocalForward, the files are not adopted, and an export over one of them needs **--force** once you've checked it.

| Option | Meaning |
|-------|-------------|
| **--exclude** *MODULE* | Keep the imported hosts out of *MODULE*'s output; repeatable |

With the **ssh** module, *SOURCE* is a config file. The first token on a Host line becomes the name and the rest become aliases. **HostName**, **User**, **Port** and **IdentityFile** become fields, and every other keyword is kept as an ssh option, in order. Export writes **IdentitiesOnly yes** after a key unless the host sets IdentitiesOnly itself, so a block with a key and no IdentitiesOnly is stored with **IdentitiesOnly no**, which is what ssh did with it. Pattern Host blocks, Match blocks, Include lines and options outside any Host block are skipped with a warning. When a keyword repeats inside a block, ssh uses the first value and so does ari, except for **IdentityFile**, **CertificateFile**, **LocalForward**, **RemoteForward**, **DynamicForward** and **SendEnv**: ssh uses every one of those, ari keeps the first and warns. A block without **User** stays without one, so ssh uses whoever connects. Point **ssh.path** only at a file ari owns: an Include, Match or `Host *` in it would be gone after the next export.

With the **ansible** module, *SOURCE* is an inventory directory, and every **.yml** and **.yaml** file at its top level is read. **all.vars** become the defaults, each host's **ansible_host**, **ansible_user**, **ansible_port**, **ansible_ssh_private_key_file** and **description** become its fields, and group membership, child groups and the file each group came from carry over. Anything else is reported as not imported: other vars, group vars, and members that no hosts section defines. Comments are read by nothing and listed with their file and line, so zone headers and reasons can be put back with **ari group**. When the inventory has no **ansible** table yet, import prints one to paste into config.toml, with each file's groups listed by name.

## export [*MODULE* ...]

Write the output of every enabled module of every inventory, or only the named modules, or one inventory with **-i**. Validation and the guard check run first; if any fails, nothing is written. A file whose contents are already right is left alone, apart from its mode if that differs.

| Option | Meaning |
|-------|-------------|
| **--force** | Overwrite files that were edited since the last export, or never written by **ari** |

# TUI

**ari** with no command, when input and output are both a terminal, opens a list of every host, or of the inventory **-i** names. It reads config.toml and the inventories before the screen changes, so a missing config, a broken inventory or an undeclared **-i** is an error, as for any command.

The list shows NAME, HOSTNAME, USER, INV, a column per module as in **ls**, and GROUPS. The filter above it narrows the list as you type and matches what **ls --search** matches. The cursor stays on its host while the filter changes, as long as the host still matches.

| Key | Action |
|-------|-------------|
| **/** | Move to the filter. Letters typed there go into the filter, never to the keys below |
| **Enter** | In the filter, back to the list. In the list, the selected host, as **show** prints it |
| **Escape** | In the list or the filter, clear the filter. In a host's details, back to the list |
| **s** | **ssh** to the selected host, from the list or its details. The TUI hands the terminal to ssh and comes back when the session ends; if ssh fails to connect (exit 255), the TUI says so once it's back |
| **q** | Quit |

# OPTIONS

| Option | Meaning |
|-------|-------------|
| **-i**, **--inventory** *INV* | Inventory to act on; accepted before or after the command |
| **-h**, **--help** | Show help for **ari** or for one command |
| **--version** | Show the version |

**add**, **import** and **group** *NAME* write to one inventory: **-i**, then **ARI_INVENTORY**, then **default** from config.toml. The TUI, **ls**, **show**, **export** and **group** without a name cover every inventory unless **-i** narrows them, and **edit** and **rm** find the host wherever it is. An inventory **-i** names that config.toml doesn't declare is an error.

# CONFIGURATION

**config.toml** declares the inventories and the modules each one uses, one table per module. **ari init** writes a starter when there's none; after that **ari** only reads it. Only **init**, **modules**, **--help** and **--version** run without it.

```toml
default = "personal"

[inventories.personal.ssh]
path = "~/.ssh/config.d/10-personal.conf"

[inventories.work]
file = "~/work/infra/ari/work.json"

[inventories.work.ssh]
path = "~/.ssh/config.d/20-work.conf"

[inventories.work.ansible]
dir = "~/work/infra/ansible/inventory"
zones = "zone_*"

[inventories.work.ansible.groups]
"10-zones.yml" = ["zone_*"]
"40-roles.yml" = ["role_*", "do_not_touch"]
"70-lifecycle.yml" = ["lifecycle_*", "no_auto_update"]
```

| Key | Meaning |
|-------------|------------|
| **default** | Inventory used by **add**, **import** and **group** *NAME* when **-i** and **ARI_INVENTORY** are unset |
| `inventories.NAME.file` | Inventory data; defaults to `NAME.json` beside config.toml |
| `inventories.NAME.MODULE` | A module this inventory uses, with that module's settings |
| `inventories.NAME.MODULE.enabled` | **false** parks the module without losing its settings |
| `inventories.NAME.ssh.path` | Generated ssh config; absolute or starting with **~** |
| `inventories.NAME.ansible.dir` | Ansible inventory directory; absolute or starting with **~** |
| `inventories.NAME.ansible.hosts` | Hosts file in that directory; defaults to `00-hosts.yml` |
| `inventories.NAME.ansible.zones` | Glob, or list of globs, naming the zone groups |
| `inventories.NAME.ansible.groups` | Each group file, with the globs of the groups it holds |

A table for a module that isn't installed is an error, unless it says **enabled = false**.

**~/.ssh/config** pulls the generated files in with one line, placed before any Host block:

```
Include config.d/*.conf
```

# MODULES

A module turns an inventory into files, reads a source into hosts, or both. Modules never write to disk themselves: they hand **ari** paths and contents, and **ari** does the writing, so the guard, the atomic writes and the all-or-nothing validation cover every module the same way. A host that lists a module in **exclude** never reaches it.

Modules are found through the Python entry point group **ari.modules**. The built-in **ssh** and **ansible** modules register there like any other. A third-party module is a package that registers in that group; install it into ari's environment with **uv tool install ari --with** *PACKAGE*.

# INVENTORY FILES

Each inventory is one JSON file. Hosts store only what differs from the inventory **defaults**.

```json
{
  "version": 2,
  "last_updated": "2026-10-02T21:14:00+01:00",
  "defaults": {"user": "deploy", "ssh_key": "~/.ssh/lab-ed25519"},
  "groups": {
    "zone_app": {"description": "app subnet (192.0.2.0/25)"},
    "role_node": {"children": ["role_cluster", "role_standalone"]},
    "no_auto_update": {"reasons": {"secrets": "secrets and prod path"}}
  },
  "hosts": [
    {
      "name": "vault-01",
      "hostname": "192.0.2.30",
      "aliases": ["vault"],
      "notes": "secrets store",
      "groups": ["zone_app", "no_auto_update"],
      "reasons": {"no_auto_update": "secrets"},
      "last_updated": "2026-10-02T21:14:00+01:00"
    }
  ]
}
```

| Field | Meaning |
|-------|-------------|
| **name** | ssh alias and Ansible inventory hostname; required |
| **hostname** | Address ssh and Ansible connect to; required |
| **aliases** | More names on the Host line |
| **user**, **port**, **ssh_key** | Override the inventory defaults |
| **notes** | A comment above the Host block, and the Ansible description |
| **groups** | Groups the host is in, each declared under **groups** |
| **reasons** | Why the host is in a group: a reason key that group declares |
| **exclude** | Modules whose output leaves this host out |
| **modules** | Each module's own data, under its name |
| `modules.ssh.options` | Any other ssh_config keywords, written in order |

Every group a host uses is declared under **groups**, even as an empty `{}`, so a typo fails instead of creating a group. **ari group** declares and changes them. A declaration takes three optional keys: **description**, whose first line is a zone's section header in the hosts file and whose further lines become comments under it; **children**, the group's child groups; and **reasons**, an ordered map of reason key to text.

Data for a module that isn't installed is kept as it is, so removing a plugin never loses anything. A version 1 file, from ari 0.2, is upgraded when it's read and saved as version 2 on its next write.

A file that fails to parse stops **ari** with the path and the error. It is never treated as empty.

# VALIDATION

**add** and **edit** check the host they write and save nothing if any check fails, listing every problem at once: the name and aliases are valid ssh host names and unique across all inventories, the hostname has no spaces, the port is 1-65535, every group is declared, every reason is one its group declares, and each installed module accepts the host's data. ssh options can't repeat a keyword the host has a field for, like **User** or **Port**.

**group** checks the group it writes the same way: a new name is usable, every child is declared, children form no cycle, and nothing a host relies on is removed. Only problems the write would add stop it; one already elsewhere in the inventory doesn't.

**export** checks every inventory it covers before writing anything. Beyond names, groups and reasons, every child group must be declared and children may not form a cycle. The **ansible** module adds its own rules: every declared group is a valid Ansible group name and matches exactly one entry in the **groups** table, and, when **zones** is set, every host it exports is in exactly one zone.

# GENERATED SSH CONFIG

Hosts are sorted by name. Each block gets **HostName**, **User** when one is set, **Port** when it isn't 22, and **IdentityFile** with **IdentitiesOnly yes** when a key is set, followed by the host's other options. There are no `Host *` blocks: they ignore file boundaries, and **IdentityFile** accumulates across matching blocks, so a default in one file would offer its key to every host.

Every write goes to `FILE.tmp` beside the target and is then renamed over it. `Include config.d/*.conf` never matches the `.tmp`, so ssh never reads a half-written file. The file gets mode 0600, as do the inventories.

# GENERATED ANSIBLE INVENTORY

The hosts file gets **all.vars** from the defaults, in the order **ansible_user**, **ansible_ssh_private_key_file**, **ansible_port**. With **zones** set, hosts come in sections, one per zone in the order the zones are declared, each opened by a blank line and a header comment padded to 62 characters. A zone without a description uses its group name. Within a section hosts sort by IP address, with names that aren't addresses after them. Each host gets **ansible_host**, **description** from its notes, and **ansible_user**, **ansible_port** or **ansible_ssh_private_key_file** only where it differs from the defaults.

Each file in the **groups** table holds the groups its globs match, in declaration order, under `all: children:`, one blank line before each group. A group lists its children first, then its hosts in the hosts file's order. Hosts without a reason come first; then each reason, in the order its group declares them, writes its text as a comment followed by its hosts.

Values are written plain when YAML reads them back unchanged, and double-quoted otherwise. The same inventory always produces the same bytes, so a diff of the generated files shows only real changes. Files in the directory that the **groups** table doesn't name are never touched.

The files get mode 0644, so anyone who runs playbooks from the repository can read them.

# FILES

| File | Contents |
|-------|-------------|
| `config.toml` | Inventories and their modules; **ari init** writes a starter |
| `NAME.json` | Inventory data, one file per inventory |
| `exports.json` | Hashes of exported files, for the guard |

`config.toml` and the inventories live in `~/.config/ari/`, and `exports.json` in `~/.local/state/ari/`. **XDG_CONFIG_HOME** and **XDG_STATE_HOME** move them. Losing the state file only means the guard refuses existing targets until the next import or **--force**.

# ENVIRONMENT

| Variable | Effect |
|-------|-------------|
| **ARI_INVENTORY** | Inventory for **add**, **import** and **group** *NAME* when **-i** is not given |
| **XDG_CONFIG_HOME** | Where config.toml and inventories live |
| **XDG_STATE_HOME** | Where the guard state lives |

# EXIT STATUS

**0** on success. **1** on an error, a validation failure, a guard refusal, or an import conflict or refusal. **2** on a usage error. **130** when interrupted.

# EXAMPLES

Take over an existing ssh config file, then regenerate it:

```
ari import ssh ~/.ssh/config.d/10-personal.conf -i personal
ari export
```

Bring an Ansible inventory in, then the ssh-only hosts beside it:

```
ari import ansible ~/work/infra/ansible/inventory -i work
ari import ssh ~/.ssh/config.d/20-work.conf -i work --exclude ansible
```

Declare a zone with its header, and a group with a reason hosts can give:

```
ari -i work group zone_app --description 'app subnet (192.0.2.0/25)'
ari -i work group no_auto_update --reason 'secrets=secrets and prod path'
```

Add a host to a zone and a monitoring group, then give it a reason to stay out of updates:

```
ari -i work add vault-03 192.0.2.32 --group zone_app --group monitoring_db
ari edit vault-03 --group no_auto_update:secrets
ari export
```

Move a host to a new address, and retire another:

```
ari edit vault-03 --hostname 192.0.2.33
ari rm oldbox
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

**ssh**(1), **ssh_config**(5), **ansible-inventory**(1)
