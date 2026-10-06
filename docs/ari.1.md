---
title: ARI
section: 1
header: User Commands
footer: ari 0.9.0
date: October 2026
---

# NAME

ari - keep SSH hosts in one place and generate ssh config and Ansible inventory from them

# SYNOPSIS

**ari** [**-i** *INV*]

**ari** [**-i** *INV*] *COMMAND* [*ARGS*]

**ari init**

**ari ls** [**--search** *TEXT*] [**--group** *GROUP*] [**--columns** *NAME*,...] [**--format** **md** | **csv**]

**ari show** *NAME*

**ari add** *NAME* *HOSTNAME* [*OPTIONS*]

**ari edit** *NAME* [*OPTIONS*]

**ari rm** *NAME*

**ari group** [*NAME* [*OPTIONS*]]

**ari key** [*NAME* [**--path** *PATH*] [**--new** | **--pub**] | *NAME* **--rm**]

**ari modules**

**ari import** *MODULE* [*SOURCE*] [**--exclude** *MODULE*]

**ari export** [*MODULE* ...] [**--force**]

# DESCRIPTION

**ari** keeps one record per SSH host in JSON inventories and generates files from them through modules. Each inventory, such as *personal* or *work*, is declared in **config.toml** with the modules it uses. The built-in **ssh** module imports and writes OpenSSH client config; the built-in **ansible** module imports and writes an Ansible YAML inventory.

Group membership lives on the host record, so removing a host removes it from every group, and no group can name a host that doesn't exist.

Names and aliases are unique across all inventories, compared case-insensitively the way ssh compares them. Every generated file lands in one ssh namespace where the first match wins, so a duplicate would silently shadow a host.

Generated files are output, not something to edit. **ari** records a hash of every file it writes and refuses to overwrite one that changed since.

With no command on a terminal, **ari** opens the TUI, described under **TUI**. When input or output isn't a terminal, it prints help instead.

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

GROUPS shows as many of a host's groups as fit beside the other columns, in the order the host lists them, then +*N* for the rest, so a row never wraps on their account; **show** lists them all. Output to a pipe is laid out for 80 columns unless **COLUMNS** says otherwise. The other columns are never cut: when they alone don't fit, GROUPS shrinks to the width of its heading and the rows run past the edge, every name and address whole.

| Option | Meaning |
|-------|-------------|
| **--search** *TEXT* | Only hosts with *TEXT* in any field, case-insensitive. User, port and keys match their effective values, defaults included, and keys match by name and by file |
| **--group** *GROUP* | Only hosts in *GROUP* |
| **--columns** *NAME*,... | These columns, in this order, instead of the usual ones; see below |
| **--format** *FORMAT* | Print the columns as **md**, a Markdown table, or **csv**, every value whole |

**--columns** takes the names below, separated by commas, and the name of each module that has a column above. Each shows the value that takes effect, inherited from the defaults or not.

| Column | Shows |
|-------|-------------|
| **name** | The host's name |
| **hostname** | The address it connects to |
| **aliases** | Its other names |
| **user** | The login ssh uses. With none set on the host or in the defaults, the table shows yours, dimmed, and **--format** leaves it blank |
| **port** | The port ssh uses |
| **keys** | The keys ssh offers, by name, in order |
| **os** | What it runs |
| **notes** | Its notes |
| **groups** | The groups it lists, in its order |
| **exclude** | The modules it stays out of |
| **inv** | Its inventory |

A list shows its values joined by a comma and a space, and an empty value shows as **-**. On screen, aliases, keys, groups, exclude and notes share the width the other columns leave, the widest giving way first and none below its heading, so a row never wraps: a list shows as many values as fit, then +*N*, and notes show their first line, cut short. The other columns are never cut, as for GROUPS above.

With **--format**, every value is whole and nothing is dimmed or cut. **md** prints a heading row, a separator, then one line per host, with a value's lines joined by `<br>` and every `|` escaped as `\|`. **csv** prints a header of column names, then one row per host, each line ending in a newline; a value holding a comma, a quote or a line break is quoted, its line breaks kept. Hosts come in the order **ls** lists them, and with none only the heading prints. Without **--columns**, both take the usual ones.

## show *NAME*

Show one host, found by name or alias in any inventory, with every field. **-i** narrows the search to one inventory. Values inherited from inventory defaults are marked **(default)**, and a group's reason shows in parentheses after it. **keys** lists each key ssh offers, in order, one per line with its file.

## add *NAME* *HOSTNAME*

Add a host to one inventory: **-i**, then **ARI_INVENTORY**, then **default** from config.toml. A value equal to the inventory default isn't stored, so the host follows the default if it changes.

| Option | Meaning |
|-------|-------------|
| **--user** *USER* | Login name |
| **--port** *PORT* | ssh port, 1-65535 |
| **--key** *NAME* | A declared key, by name or by its file; repeatable, in the order ssh offers them |
| **--os** *TEXT* | What the host runs, as you'd write it: *Ubuntu 24.04*. One line, kept in the inventory only |
| **--notes** *TEXT* | Free text: a comment above the Host block, and the Ansible description |
| **--alias** *ALIAS* | Another name on the Host line; repeatable |
| **--opt** *KEY*=*VALUE* | Another ssh option; repeatable, every value for one *KEY* making its value |
| **--group** *GROUP*[:*REASON*] | Join a declared group, with an optional reason key; repeatable |
| **--exclude** *MODULE* | Keep the host out of *MODULE*'s output; repeatable |

## edit *NAME*

Change a host, found by name or alias. **-i** narrows the search to one inventory. Takes every **add** option, plus these:

| Option | Meaning |
|-------|-------------|
| **--hostname** *HOSTNAME* | New address |
| **--rename** *NAME* | New name |
| **--unalias** *ALIAS* | Drop an alias, matched case-insensitively; repeatable |
| **--unkey** *NAME* | Drop one of the host's own keys, by name or file; repeatable |
| **--ungroup** *GROUP* | Leave a group, and its reason with it; repeatable |
| **--include** *MODULE* | Undo an **--exclude**; repeatable |

An empty value clears a field: **--user ''** and **--port ''** go back to the inventory default, **--key ''** drops the host's own keys so the defaults' apply again, **--os ''** and **--notes ''** empty those fields, and **--opt** *KEY*= removes that option.

A host that lists keys uses those instead of the defaults' keys, so the first **--key** on a host that follows the defaults gives it a list of its own. Each **--key** appends to that list after **--unkey** has run, so **--unkey** *OLD* **--key** *NEW* swaps one key for another, and **--key ''** **--key** *A* **--key** *B* sets the list outright. A key that isn't declared is refused, with the **ari key** command that declares it.

Every **--opt** given for one keyword in one command is that keyword's new value. Two or more make a list, which only **CertificateFile**, **LocalForward**, **RemoteForward**, **DynamicForward** and **SendEnv** accept, since ssh reads only the first line of any other keyword. So **--opt** *LocalForward=A* **--opt** *LocalForward=B* sets both forwards, and a later **--opt** *LocalForward=C* replaces them with one. A keyword both set and cleared in one command is refused. **--group** sets membership exactly as given, so naming a group the host is already in without a reason drops its reason. **--unalias** runs before **--alias**, so **--unalias** *OLD* **--alias** *NEW* swaps one alias for another, a change of case included. **--ungroup** a group the host isn't in, **--unalias** a name that isn't one of its aliases, **--unkey** a key it doesn't list itself, **--include** a module it doesn't exclude, and clearing an option it doesn't have are errors, not silent no-ops. An edit that changes nothing saves nothing.

## rm *NAME*

Remove a host, found by name or alias, from whichever inventory holds it. **-i** narrows the search. Its group memberships go with it.

## group [*NAME*]

Without *NAME*, list the declared groups of every inventory, or only the one named with **-i**, in the order they're declared. HOSTS counts a group's hosts as Ansible sees them: the ones that list it and the ones any group below it holds, with the direct count beside it when children bring more. CHILDREN, REASONS and ABOUT, the description's first line, share the width the other columns leave and never wrap. GROUP, HOSTS and INV are never cut: when they alone don't fit, the other three shrink to the width of their headings and the rows run past the edge.

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

## key [*NAME*]

Without *NAME*, list the declared keys of every inventory, or only the one named with **-i**, in the order they're declared. HOSTS counts the hosts ssh offers the key to, including those that take it from the defaults, with the hosts that list it themselves beside it when the two differ. Every column is whole: when the table doesn't fit, its rows run past the edge, so a name or a path never reads cut short.

STATE says what ssh, run by you on this machine, would find at the key's path: **ok** when nothing is wrong, otherwise every problem, comma separated. The path is resolved as ssh resolves IdentityFile: `~` and `~user` through the password database, and a relative path from wherever ssh runs. A path **ari** can't turn into one file shows only why: **relative**, **tokens** for one with `%` or `${`, which can name a different file per host, and **no user** *NAME* for a `~user` the system doesn't know. A file ssh can't read shows only **missing**, **unreadable** or **not a file**. Otherwise:

| State | Meaning |
|-------|-------------|
| **mode** *MODE* | The file is yours and group or others have access, so ssh ignores the key |
| **not a key** | The file holds no private key, a public key for instance |
| **pair unchecked** | A PEM or PKCS#8 private key, whose public half can't be read without its passphrase; `ssh-keygen -p -f` *PATH* rewrites it in OpenSSH format |
| **.pub mismatch** | *PATH*`.pub` holds another key. ssh offers that one and then won't sign with this file, so the key fails |
| **pub stale** | The declared **pub** isn't this file's public half |
| **cert to** *DATE* | *PATH*`-cert.pub` is valid until *DATE*; also **cert forever**, **cert from** *DATE* while not yet valid, and **cert expired** *DATE* |
| **cert for another key** | *PATH*`-cert.pub` certifies a different key |
| **cert unreadable** | `ssh-keygen -L` can't read *PATH*`-cert.pub`; **cert unchecked** when there's no ssh-keygen |

The private key is the reference: an OpenSSH key keeps its public half unencrypted, so **ari** reads it with no passphrase. Public keys compare on their key, not their comment, and a *PATH*`.pub` that holds no key says nothing, since ssh skips it. Certificate dates are local time, as `ssh-keygen -L` prints them. These checks run only here: **export** and every other command leave key files alone, so they work on a machine that doesn't hold every key.

With *NAME*, declare that key in one inventory, **-i**, then **ARI_INVENTORY**, then **default** from config.toml, or change it if it's declared already. A name has no spaces: letters, digits and `_ . + @ -`, starting with a letter, digit or `_`.

| Option | Meaning |
|-------|-------------|
| **--path** *PATH* | The private key file, written as IdentityFile. Moving a key moves every host that uses it |
| **--new** | Generate the key at its path with ssh-keygen, then fill **pub** |
| **--pub** | Fill **pub** from the key file |
| **--rm** | Remove the key; takes no other option |

Two keys in one inventory can't share a file, since **--key** *PATH* has to name one of them. **--rm** is refused while the defaults or any host list the key, naming them, and nothing is saved. A command that changes nothing saves nothing.

**--new** runs `ssh-keygen -t ed25519 -f` *PATH* `-C "`*NAME* *USER*`@`*HOST*`"` on the terminal, so ssh-keygen asks for the passphrase itself and **ari** never sees it, then stores the new *PATH*`.pub` line as **pub**. With **--path** it declares or moves the key first, in the same command. It needs a terminal, and everything is checked before ssh-keygen runs, so a refusal leaves no key behind: the path has to be one **ari** can name in your own home or elsewhere, its directory has to exist, and *PATH*, *PATH*`.pub` and *PATH*`-cert.pub` must all be free. ssh-keygen only asks before replacing the private key and writes the `.pub` over whatever is there, and an old `.pub` can be the last trace of a key a server still trusts, so **ari** never removes or overwrites key files: move them away first. If ssh-keygen fails or is interrupted, or doesn't leave a matching pair, nothing is saved.

**--pub** stores the key file's public half: the *PATH*`.pub` line, comment included, when it holds this key, otherwise the key type and key read from the private key file. A key **ari** can't read, or a PEM key, whose public half needs its passphrase, is refused and nothing is saved.

## modules

List the installed modules, whether each can import and export, and which inventories switch it on. Modules that failed to load are listed with the reason. Without config.toml it still lists the modules, with no inventories.

## import *MODULE* [*SOURCE*]

Read hosts into one inventory through *MODULE*. Every host goes through the same checks as **add**: one that fails is refused and reported, and the rest still import. A host already in the inventory is merged: new aliases, groups, options and missing notes are added. A value the source sets differently, whether HostName, User, Port, IdentityFile, notes or an ssh option such as ProxyJump, is a conflict, and that host is left alone. Hosts that merged are saved even when others conflicted or were refused; the exit status is then 1.

Importing into an empty inventory sets its defaults: from the source's own defaults when it has them, like Ansible's **all.vars**, otherwise to the user and key most hosts share. Each host stores only what differs. An inventory with hosts or defaults keeps its own, and a host that leaves a field to the source's defaults takes the source's value, stored where it differs from the inventory's. A host already in the inventory whose value would change that way is a conflict, and the warning names the hosts that were given one.

A clean import adopts the files it read: their hashes go to the guard, so exporting over them afterwards passes. If anything conflicted, was refused, or couldn't be represented, such as a skipped `Host *` block, the files are not adopted, and an export over one of them needs **--force** once you've checked it.

| Option | Meaning |
|-------|-------------|
| **--exclude** *MODULE* | Keep the imported hosts out of *MODULE*'s output; repeatable |

Each key file the source names becomes the inventory's key at that path. A path the inventory has no key for is declared under the file's stem, numbered when the name is taken, and the report lists the keys it declared. A key only refused or conflicting hosts would have used isn't declared.

With the **ssh** module, *SOURCE* is a config file. The first token on a Host line becomes the name and the rest become aliases. A block without **HostName** is read the way ssh reads it, which connects to whichever token was typed: each token becomes a host of its own, connecting to its own name, with the block's user, port, key and options. A block for a host the inventory already has, a later block in the same file or the same host on re-import, compares and fills only what it sets, so without **HostName** or **Port** it can still add a user, key, alias or option. A new host without **Port** gets 22, ssh's own default, stored only when the inventory's default port differs. **HostName**, **User** and **Port** become fields, each **IdentityFile** one of the host's keys in order, and every other keyword is kept as an ssh option, in order. Export writes **IdentitiesOnly yes** after the keys unless the host sets IdentitiesOnly itself, so a block with a key and no IdentitiesOnly is stored with **IdentitiesOnly no**, which is what ssh did with it. Pattern Host blocks, Match blocks, Include lines and options outside any Host block are skipped with a warning. When a keyword repeats inside a block, ssh uses the first value and so does ari, except for **IdentityFile**, **CertificateFile**, **LocalForward**, **RemoteForward**, **DynamicForward** and **SendEnv**: ssh uses every one of those, and so does ari, as a list. ssh also adds those from every later block a name matches, so a later block naming every token a host has, exactly as written, since ssh matches Host tokens case and all, adds its keys and lines of those keywords after the host's own, and IdentitiesOnly is the first block's that sets it. A block whose Host line leaves out a name the host answers to, or writes one in another case, applies only to the names it has, which one record can't hold. Whether it comes later in the same file or in a later import, it can still add aliases, and anything else it would change is a conflict: its keys and lines of those keywords are compared, and a setting it would fill is refused. A block without **User** stays without one, so ssh uses whoever connects. Point **ssh.path** only at a file ari owns: an Include, Match or `Host *` in it would be gone after the next export.

With the **ansible** module, *SOURCE* is an inventory directory, and every **.yml** and **.yaml** file at its top level is read. **all.vars** become the defaults, each host's **ansible_host**, **ansible_user**, **ansible_port**, **ansible_ssh_private_key_file** and **description** become its fields, and group membership, child groups and the file each group came from carry over. **ansible_host** and **description** must be strings: YAML reads `ansible_host: no` as false, so a host like that is skipped and a description like that left out, each reported. A quoted **ansible_port** such as `"2222"` is the number, as Ansible reads it. **ansible_ssh_private_key_file** names only a host's first key, so it agrees with a record whose keys start with that file: re-importing what export wrote for a host with several keys changes nothing, and a new host naming the first of the defaults' keys takes their whole list. Anything else is reported as not imported: other vars, group vars, and members that no hosts section defines. Comments are read by nothing and listed with their file and line, so zone headers and reasons can be put back with **ari group**. When the inventory has no **ansible** table yet, import prints one to paste into config.toml, with each file's groups listed by name. Export writes the hosts file with hosts only, so groups defined there get a file of their own in that table, named in a warning. The groups the source brings get the check **group** runs: if they would add a cycle or an undeclared child, the problem is refused and the inventory's groups stay as they were, and a host in a group that didn't get declared is refused with it.

## export [*MODULE* ...]

Write the output of every enabled module of every inventory, or only the named modules, or one inventory with **-i**. Validation and the guard check run first; if any fails, nothing is written. Every file is then written beside its target before any is renamed into place, so a write that fails, on a full disk say, leaves every file and the guard as they were. A file whose contents are already right is left alone, apart from its mode if that differs.

| Option | Meaning |
|-------|-------------|
| **--force** | Overwrite files that were edited since the last export, or never written by **ari** |

# TUI

**ari** with no command, when input and output are both a terminal, opens a list of every host, or of the inventory **-i** names. It reads config.toml and the inventories before the screen changes, so a missing config, a broken inventory or an undeclared **-i** is an error, as for any command.

The list shows NAME, HOSTNAME, USER, INV, a column per module as in **ls**, and GROUPS. The filter above it narrows the list as you type and matches what **ls --search** matches. The cursor stays on its host while the filter changes, as long as the host still matches.

| Key | Action |
|-------|-------------|
| **/** | In the list, move to the filter. Letters typed there go into the filter, never to the keys below |
| **Enter** | In the filter, back to the list. In the list, the selected host's details, as **show** prints them. In an export report, back to the list. In the keys screen, back to the list showing only the hosts that use the selected key |
| **Escape** | In the list or the filter, clear the filter, and show every host again after the keys screen narrowed the list. In a host's details, an export report or the keys screen, back to the list. In the form, the group picker or a delete prompt, back without saving |
| **s** | In the list or a host's details, **ssh** to that host. The TUI hands the terminal to ssh and comes back when the session ends; if ssh fails to connect (exit 255), the TUI says so once it's back. A host the ssh column doesn't mark ✓ has no block in the ssh config ari writes, so ssh would go wherever DNS sends its name: the TUI says why and doesn't run ssh |
| **a** | In the list, add a host in the form below |
| **e** | In the list or a host's details, edit that host in the same form, filled with its own values |
| **g** | In the list or a host's details, that host's groups, in the picker below |
| **d** | In the list or a host's details, delete that host once **y** answers the prompt; **n** or **Escape** keeps it. The list reads the inventories again, and the cursor lands on the row that took the host's place. In the keys screen, remove the selected key the same way, as **ari key** *NAME* **--rm** does; its files stay. A key the defaults or a host still list isn't offered: the TUI says why instead of asking |
| **x** | In the list, export what the TUI lists, as **ari export** does: each file as export reports it, or every problem that stopped it, with nothing written. A hand-edited target stays refused; overwriting it takes **ari export --force** |
| **k** | In the list, the keys screen below |
| **n** | In the keys screen, generate the selected key's file, as **ari key** *NAME* **--new** does. The TUI hands the terminal to ssh-keygen, which asks for the passphrase, and comes back when it's done. Anything **--new** would refuse, the TUI says before handing the terminal over |
| **q** | In the list, a host's details, an export report or the keys screen, quit. In the form, the group picker and the delete prompts it's an ordinary key, so nothing unsaved is lost to it |

The form holds the inventory (on **a** only), name, hostname, user, port, keys by name in the order ssh offers them, os, notes, aliases separated by spaces, ssh options one per line as ssh_config takes them (*KEYWORD VALUE* or *KEYWORD*=*VALUE*), and a box per module to exclude the host from. Groups aren't on it. An empty user, port or keys follows the inventory default, which shows in the empty field; emptying one that's set goes back to the default, as **''** does for **edit**. A keyword on several lines of the ssh options holds them all, as several **--opt** for it would.

**Tab** moves to the next field, and so does **Enter** in a one-line field; in the ssh options it starts a new line. **Ctrl+S** saves and **Escape** cancels. Saving runs the checks **add** and **edit** run and saves nothing if any fails: each problem shows under the field it's about, and one that belongs to no field at the top. After a save the list reads the inventories again with the cursor on the host, and says so when the filter hides it.

The keys screen lists the declared keys of every inventory, or of the one **-i** names, as **ari key** does: KEY, HOSTS, PATH, STATE and INV. **Enter** on a key goes back to the list showing only the hosts ssh offers it to, the ones HOSTS counts, with a line above the list naming the key. The filter still narrows within them, and **Escape** shows every host again. ssh-keygen stopped with **Ctrl+C**, or failing, brings the TUI back with nothing saved.

The group picker lists the inventory's declared groups in the order they're declared, each with the first line of its description, checked where the host is a member. A group the host names without a declaration shows too, marked as not declared, so it can be unchecked. **Space** checks or unchecks the highlighted group, and leaving a group drops its reason. When the highlighted group is checked and declares reasons, a reason picker below it offers them and *no reason*; the reason chosen shows beside the group. **Ctrl+S** saves what changed, as **edit --group** and **--ungroup** would and through the same checks, and **Escape** cancels. A refusal shows above the list and saves nothing. Belonging to two zones passes here, as it does for **edit**; **export** refuses it.

# OPTIONS

| Option | Meaning |
|-------|-------------|
| **-i**, **--inventory** *INV* | Inventory to act on; accepted before or after the command |
| **-h**, **--help** | Show help for **ari** or for one command |
| **--version** | Show the version |

**add**, **import** and **group** *NAME* write to one inventory: **-i**, then **ARI_INVENTORY**, then **default** from config.toml, which is also where the TUI's add form starts. The TUI, **ls**, **show**, **export** and **group** without a name cover every inventory unless **-i** narrows them, and **edit** and **rm** find the host wherever it is. An inventory **-i** names that config.toml doesn't declare is an error.

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

[[inventories.work.table.outputs]]
path = "~/work/infra/docs/hosts.md"
columns = ["name", "hostname", "os", "groups", "notes"]
```

| Key | Meaning |
|-------------|------------|
| **default** | Inventory used by **add**, **import**, **group** *NAME* and the TUI's add form when **-i** and **ARI_INVENTORY** are unset |
| `inventories.NAME.file` | Inventory data; defaults to `NAME.json` beside config.toml |
| `inventories.NAME.MODULE` | A module this inventory uses, with that module's settings |
| `inventories.NAME.MODULE.enabled` | **false** parks the module without losing its settings |
| `inventories.NAME.ssh.path` | Generated ssh config; absolute or starting with **~** |
| `inventories.NAME.ansible.dir` | Ansible inventory directory; absolute or starting with **~** |
| `inventories.NAME.ansible.hosts` | Hosts file in that directory; defaults to `00-hosts.yml` |
| `inventories.NAME.ansible.zones` | Glob, or list of globs, naming the zone groups |
| `inventories.NAME.ansible.groups` | Each group file, with the globs of the groups it holds |
| `inventories.NAME.ansible.authorized_keys` | **true** lists each host's public keys in **ari_authorized_keys**; defaults to **false**; see GENERATED ANSIBLE INVENTORY |
| `inventories.NAME.table.outputs` | Each table to write, with its **path** and **columns**, and optionally **format** and **mode**; see GENERATED TABLES |

A table for a module that isn't installed is an error, unless it says **enabled = false**.

**~/.ssh/config** pulls the generated files in with one line, placed before any Host block:

```
Include config.d/*.conf
```

# MODULES

A module turns an inventory into files, reads a source into hosts, or both. Modules never write to disk themselves: they hand **ari** paths and contents, and **ari** does the writing, so the guard, the atomic writes and the all-or-nothing validation cover every module the same way. A host that lists a module in **exclude** never reaches it.

Modules are found through the Python entry point group **ari.modules**. The built-in **ssh**, **ansible** and **table** modules register there like any other. A third-party module is a package that registers in that group; install it into ari's environment with **uv tool install ari --with** *PACKAGE*.

# INVENTORY FILES

Each inventory is one JSON file. Hosts store only what differs from the inventory **defaults**.

```json
{
  "version": 3,
  "last_updated": "2026-10-02T21:14:00+01:00",
  "defaults": {"user": "deploy", "keys": ["lab-ed25519"]},
  "keys": {
    "lab-ed25519": {"path": "~/.ssh/lab-ed25519"},
    "lab-rsa": {"path": "~/.ssh/lab-rsa"}
  },
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
      "os": "Rocky 10.1",
      "notes": "secrets store",
      "groups": ["zone_app", "no_auto_update"],
      "reasons": {"no_auto_update": "secrets"},
      "modules": {"ssh": {"options": {"LocalForward": ["8200 localhost:8200", "8201 localhost:8201"]}}},
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
| **user**, **port** | Override the inventory defaults |
| **keys** | Declared keys ssh offers, in order, instead of the default list |
| **os** | What the host runs, written by hand, one line. Only **ari** reads it: no module exports it and **import** leaves it alone |
| **notes** | A comment above the Host block, and the Ansible description |
| **groups** | Groups the host is in, each declared under **groups** |
| **reasons** | Why the host is in a group: a reason key that group declares |
| **exclude** | Modules whose output leaves this host out |
| **modules** | Each module's own data, under its name |
| `modules.ssh.options` | Any other ssh_config keywords, written in order; a list for one used on every line |

Every group a host uses is declared under **groups**, even as an empty `{}`, so a typo fails instead of creating a group. **ari group** declares and changes them. A declaration takes three optional keys: **description**, whose first line is a zone's section header in the hosts file and whose further lines become comments under it; **children**, the group's child groups; and **reasons**, an ordered map of reason key to text.

Every key a host or the defaults list is declared under **keys**, by a name with no spaces: letters, digits and `_ . + @ -`, starting with a letter, digit or `_`. **ari key** declares, moves and removes them. A declaration holds the private key's **path**, and **pub**, the public key, which **ari key** fills with **--new** or **--pub** and checks against the key file. A host that lists keys uses those instead of the defaults' list, not on top of them.

Options take a string. **CertificateFile**, **LocalForward**, **RemoteForward**, **DynamicForward** and **SendEnv** can also take a list, since ssh uses every line of those; any other keyword uses only its first line, so a list there doesn't load. A list of one is stored as its string. A host's value for a keyword replaces the default's, lists included.

Data for a module that isn't installed is kept as it is, so removing a plugin never loses anything. Older files are upgraded when they're read and saved as version 3 on their next write. A version 1 file, from ari 0.2, moves its ssh options and Ansible opt-out under **modules**. A version 2 file, up to ari 0.6, keeps one key path per host and in the defaults; each path becomes a declared key named after the file's stem, numbered when two files share one, so every generated file stays byte for byte the same.

A file that fails to parse stops **ari** with the path and the error. It is never treated as empty. Two ssh options that differ only in case are one keyword to ssh, so a file that holds both doesn't load.

# VALIDATION

**add** and **edit** check the host they write and save nothing if any check fails, listing every problem at once: the name and aliases are valid ssh host names and unique across all inventories, the hostname has no spaces, the port is 1-65535, os is one line, every key is declared and listed once, every group is declared, every reason is one its group declares, and each installed module accepts the host's data. ssh options can't repeat a keyword the host has a field for, like **User** or **Port**. The TUI's form and group picker save through these same checks.

**group** checks the group it writes the same way: a new name is usable, every child is declared, children form no cycle, and nothing a host relies on is removed. Only problems the write would add stop it; one already elsewhere in the inventory doesn't.

**export** checks every inventory it covers before writing anything. Beyond names, keys, groups and reasons, including the keys the defaults list, every child group must be declared, children may not form a cycle, and no alias may be its host's own name in another case. The **ansible** module adds its own rules: every declared group is a valid Ansible group name and matches exactly one entry in the **groups** table; when **zones** is set, every host it exports is in exactly one zone; and with **authorized_keys** on, every key **ari_authorized_keys** would list has a **pub**.

# GENERATED SSH CONFIG

Hosts are sorted by name. Each block gets **HostName**, **User** when one is set, **Port** when it isn't 22, an **IdentityFile** for each of its keys in order, then **IdentitiesOnly yes** once when there is a key, followed by the host's other options, one line for each value of a list. There are no `Host *` blocks: they ignore file boundaries, and **IdentityFile** accumulates across matching blocks, so a default in one file would offer its key to every host.

Every write goes to `FILE.tmp` beside the target and is then renamed over it. `Include config.d/*.conf` never matches the `.tmp`, so ssh never reads a half-written file. The file gets mode 0600, as do the inventories, and a directory **ari** creates for them gets 0700.

# GENERATED ANSIBLE INVENTORY

The hosts file gets **all.vars** from the defaults, in the order **ansible_user**, **ansible_ssh_private_key_file**, **ansible_port**. With **zones** set, hosts come in sections, one per zone in the order the zones are declared, each opened by a blank line and a header comment padded to 62 characters. A zone without a description uses its group name. Within a section hosts sort by IP address, with names that aren't addresses after them. Each host gets **ansible_host**, **description** from its notes, and **ansible_user**, **ansible_port** or **ansible_ssh_private_key_file** only where it differs from the defaults. **ansible_ssh_private_key_file** takes one file, its first key's.

With **authorized_keys = true** in the inventory's **ansible** table, the hosts file also lists public keys in **ari_authorized_keys**, for **ansible.posix.authorized_key** to deploy: the **pub** of every key ssh offers the host, in order, exactly as stored. The defaults' list goes in **all.vars** after **ansible_port**, and a host gets one of its own, after **ansible_ssh_private_key_file**, only where its keys make a different list. Ansible takes a host's list in place of the one in **all.vars**, as ssh takes a host's keys in place of the defaults'. A host with no keys anywhere gets none. Every key the var lists needs a **pub**, so export refuses one without and names the **ari key** *NAME* **--pub** that fills it. The var comes from the inventory alone: export still never reads a key file, and **ari** never connects to a host. A playbook deploys the keys, for instance:

```yaml
- ansible.posix.authorized_key:
    user: "{{ ansible_user }}"
    key: "{{ ari_authorized_keys | join('\n') }}"
    exclusive: true
  when: ari_authorized_keys is defined
```

**exclusive** removes every key the list leaves out, so skip a host without the var rather than giving it an empty list.

Each file in the **groups** table holds the groups its globs match, in declaration order, under `all: children:`, one blank line before each group. A group lists its children first, then its hosts in the hosts file's order. Hosts without a reason come first; then each reason, in the order its group declares them, writes its text as a comment followed by its hosts.

Values are written plain when YAML reads them back unchanged, and double-quoted otherwise. The same inventory always produces the same bytes, so a diff of the generated files shows only real changes. Files in the directory that the **groups** table doesn't name are never touched.

The files get mode 0644, so anyone who runs playbooks from the repository can read them, and a directory **ari** creates for them gets 0755. A directory that already exists keeps its mode.

# GENERATED TABLES

The **table** module writes an inventory's hosts as tables, one file for each entry under `outputs`, with the columns that entry names. It only exports: nothing reads a table back.

| Key | Meaning |
|-------|-------------|
| **path** | The file to write; absolute or starting with **~** |
| **columns** | The columns, in order, from those **ls --columns** takes: name, hostname, aliases, user, port, keys, os, notes, groups, exclude and inv. A module's column isn't one, since it says what export writes rather than what the record holds |
| **format** | **md** or **csv**; needed only when the path ends in neither `.md` nor `.csv` |
| **mode** | The file's mode, as an octal string you can read and write; defaults to **"0644"** |

A file holds what **ls -i** *NAME* **--format** prints for those columns: every value whole, as it takes effect, hosts in the order the inventory saves them. A Markdown file opens with a comment naming the inventory it comes from, then a blank line; a CSV file has no such line, since it would read as a row. A host that lists **table** in **exclude** stays out of every table of its inventory. Nothing in a table depends on the time or the machine, so an export with nothing changed reports every table unchanged, and the guard refuses a hand edit until **export --force**, as for any generated file.

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
| **ARI_INVENTORY** | Inventory for **add**, **import**, **group** *NAME* and the TUI's add form when **-i** is not given |
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

What the work hosts run, as CSV:

```
ari -i work ls --columns name,hostname,os --format csv
```

Browse the work hosts in the TUI, then filter with **/** and ssh to one with **s**:

```
ari -i work
```

Accept a file after reviewing a hand edit:

```
ari export --force
```

# SEE ALSO

**ssh**(1), **ssh_config**(5), **ansible-inventory**(1)
