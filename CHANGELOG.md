# Changelog

Versions follow [semantic versioning](https://semver.org/). Before 1.0, a minor version adds features or changes the inventory format, and a patch version only fixes bugs. Each release is a `vX.Y.Z` tag on GitHub.

## Unreleased

- `authorized_keys = true` in an inventory's `ansible` table lists public keys in the hosts file as `ari_authorized_keys`, for `ansible.posix.authorized_key` to deploy: the `pub` of every key ssh offers a host, in order. The defaults' list goes in `all.vars` and a host gets its own only where its keys make a different one, as with `ansible_ssh_private_key_file`. Export refuses a key the var would list without a `pub`, naming the `ari key NAME --pub` that fills it. It's off by default, and off, every file stays the same.
- ansible import checks `ari_authorized_keys` against the `pub` of each host's keys and stores nothing from it, so re-importing what export wrote with it on is unchanged. Another key, or a key without a `pub`, is a conflict on a host already in the inventory; on a new host the list is reported as not imported. The same keys under other comments are reported and the inventory's kept. When the source has the var, the table import prints for config.toml turns `authorized_keys` on.
- ansible import reads `ansible_ssh_private_key_file` as what it is, a host's first key, and agrees with a record whose keys start with that file. Re-importing what export wrote for a host with several keys, its own or the defaults', was a conflict, `IdentityFile ~/.ssh/one differs from ~/.ssh/one, ~/.ssh/two`, that kept the files from being adopted, and several default keys also drew a warning that the source's defaults differed. A new host naming the first of the defaults' keys now takes their whole list instead of that one key. A first key that differs is still a conflict.

## 0.9.0 (2026-10-06)

- Each host can say what it runs: `os`, written by hand, one line. `add` and `edit` set it with `--os 'Ubuntu 24.04'` and `edit --os ''` clears it; the TUI's form has a field for it. `show` and the TUI's details print it, and `ls --search` and the TUI filter match it. No module exports it and import leaves it alone, so every generated file stays the same. The inventory format stays version 3, and a host without `os` stores nothing for it; ari 0.8 refuses a file in which any host has one.
- `ls --columns name,os,notes` shows the columns you name, in that order: name, hostname, aliases, user, port, keys, os, notes, groups, exclude, inv, and each exporting module's column. Values are the ones that take effect, defaults included. Lists and notes share the width the other columns leave, so a row never wraps, and the rest are never cut.
- `ls --format md` and `ls --format csv` print the same columns as a Markdown table or CSV, every value whole: a user set nowhere is blank rather than your login, a value's lines join with `<br>` in Markdown and stay inside a quoted CSV field. Without `--columns`, `ls` and both formats show the usual columns.
- The `table` module writes an inventory's hosts as Markdown or CSV, one file per `[[inventories.NAME.table.outputs]]` entry: its `path`, its `columns` from those `ls --columns` takes, and optionally `format`, read from a `.md` or `.csv` suffix otherwise, and `mode`, `"0644"` by default. A file holds what `ls --format` prints for the same columns, a Markdown one opening with a comment naming its inventory, and goes through export and the guard like the ssh and Ansible files. `--exclude table` keeps a host out of its inventory's tables. It never imports.

## 0.8.0 (2026-10-05)

- `ari key` has a STATE column saying what ssh would find at each key's path: `ok`, or every problem, comma separated. A key file that's missing, unreadable, open to group or others, not a private key, or that disagrees with its `.pub` or the declared `pub`, and the validity of a `-cert.pub` beside it, read with `ssh-keygen -L`. Paths resolve as ssh resolves them; a relative path, one with `%` tokens or `${}`, or an unknown `~user` shows only that. Nothing else reads key files, so `export` still works on a machine that doesn't hold every key.
- `ari key NAME --new` generates the key at its path with `ssh-keygen -t ed25519`, commented with the key's name and your `user@host`, and stores its public half as `pub`. ssh-keygen runs on the terminal and asks for the passphrase itself. It never writes over anything: a key, `.pub` or `-cert.pub` already at the path is refused, as is a relative path, one in another user's home or one whose directory doesn't exist, all before ssh-keygen runs. Nothing is saved if ssh-keygen fails or is interrupted. With `--path` it declares the key in the same command.
- `ari key NAME --pub` fills `pub` from the key file: the `.pub` line when it holds this key, otherwise the public half the private key file holds.
- In the TUI, `k` opens a keys screen with the columns `ari key` shows. Enter goes back to the host list showing only the hosts that use the key, `n` generates its file through ssh-keygen as `--new` does, handing over the terminal for the passphrase, and `d` removes the key once you confirm, or says why it can't while the defaults or a host list it.

## 0.7.1 (2026-10-05)

- The inventory format is version 3. Keys are declared once per inventory under `keys`, by name with their file's `path`, and hosts and defaults list the names in the order ssh offers them. Export writes an IdentityFile for each, then IdentitiesOnly yes once; Ansible gets the first key's file. A version 2 file is upgraded when it's read, each path declared under its file's stem and numbered on a clash, and saved as version 3 on its next write; every generated file stays the same.
- ssh options CertificateFile, LocalForward, RemoteForward, DynamicForward and SendEnv can hold a list, written one line per value. Any other keyword keeps one value, since ssh reads only its first line.
- ssh import keeps every IdentityFile, as the host's keys in order, and every line of CertificateFile, LocalForward, RemoteForward, DynamicForward and SendEnv, so a block repeating them no longer warns or leaves its file unadopted. A later block naming every token of a host adds its keys and those lines after the host's own, as ssh does; one naming only some of them is compared, and a different list is a conflict. Re-import compares a list whole.
- Import refuses a change from a Host line that leaves out a name the host answers to, or writes one in another case, in the same file or a later import. ssh applies such a block only to the names it has, so filling its User, ProxyJump or any other setting into the record changed what the other names connect with. It can still add aliases.
- `ari key` lists the declared keys of every inventory, with the hosts each reaches, defaults included. `ari key NAME --path PATH` declares a key or moves it, and every host using it follows; `--rm` is refused while the defaults or a host list it, naming them.
- `--key` takes a declared key by name or by its file and is repeatable, in the order ssh offers them; `edit --unkey` drops one and runs first, and `--key ''` goes back to the defaults' keys. An undeclared key is refused, with the command that declares it. Import declares each new key file it reads under the file's stem and lists the keys it declared. `add`, `edit` and `export` refuse a key listed twice.
- Every `--opt` given for one keyword in one command is its new value, so repeating `--opt LocalForward=...` sets several forwards. Repeating a keyword ssh reads once, or setting and clearing one together, is refused.
- `show` and the TUI's details list each key with its file. `ls --search` and the TUI filter match key names as well as files. The TUI form takes keys by name, in order, and a keyword on several lines of its ssh options holds them all.

## 0.6.1 (2026-10-05)

- ssh import reads a Host line with several names and no HostName as one host per name, each connecting to its own name, as ssh does. Before, the other names became aliases of the first and connected to it.
- ssh import treats a HostName or Port that a block leaves out as not set when the host is already in the inventory, from a later block in the same file or on re-import. Such a block now adds its user, key, aliases or options instead of reporting a HostName conflict. A value set differently is still a conflict.
- ansible import into an inventory that keeps its own defaults gives each host the source's `all.vars` for the fields it leaves unset, stored where they differ from the inventory's. Before, such a host followed the inventory's defaults. A host already in the inventory whose value would change is a conflict, and the warning names the hosts that were given the source's values.
- ansible import skips a host whose `ansible_host` isn't a string, like `no`, which YAML reads as false and ari stored as `False`, and leaves out a `description` that isn't a string. A quoted `ansible_port` such as `"2222"` is read as the port instead of being left out.
- ansible import gives groups defined in the hosts file a file of their own in the table it prints, so the first export with that table no longer refuses them.
- ansible import runs the check `ari group` runs on the groups it brings. A cycle or an undeclared child is refused, the inventory's groups stay as they were, and only the hosts in a group that didn't get declared are refused with it. Before, a cycle was saved and every export stopped on it.
- import treats a different non-empty note, Ansible's `description`, as a conflict, like a different HostName. Before, it was dropped without a word.
- `export` creates a missing directory 0755 when the files it holds are meant for others, like the Ansible inventory, and 0700 when they are private. It used to make every new directory 0700, which kept the 0644 Ansible files from everyone else. A directory that exists keeps its mode.
- `export` writes every file beside its target before renaming any into place. A write that fails, on a full disk say, now leaves every target and the guard as they were. Before, the files written ahead of it stayed while the guard missed them, and the next export refused them as edited by hand.
- An inventory file whose ssh options hold one keyword twice in different case no longer loads, and `export` refuses a host with an alias that is its own name in another case. Both can only come from editing the file by hand.
- In the TUI, `s` runs ssh only for a host the ssh column marks `✓`. For a host that excludes ssh, or whose inventory has the ssh module off, ari writes no ssh block, so `ssh NAME` would have gone wherever DNS sends that name; the TUI now says why and doesn't run it.

## 0.6.0 (2026-10-04)

- `ari` with no command on a terminal opens a TUI: a table of every host, or of the inventory `-i` names, under a filter that narrows it as you type and matches what `ls --search` matches. `/` moves to the filter, Enter shows the host as `show` prints it, `s` runs ssh and comes back to the list when the session ends, and `q` quits from the list, a host's details or an export report. Without a terminal, `ari` prints help as before. textual is a new dependency.
- In the TUI, `a` opens a form for a new host and `e` the same form for the selected one, filled with its own values and showing the defaults it follows. Ctrl+S saves through the same checks as `add` and `edit`, each problem under the field it's about; Escape cancels.
- In the TUI, `g` opens the selected host's groups as a checklist of the inventory's declared groups, with a reason picker for the checked ones that declare reasons. Ctrl+S saves what changed through the same checks as `edit --group` and `--ungroup`.
- In the TUI, `d` deletes the selected host once `y` answers the prompt, and `x` exports what the TUI lists. The report shows each file as `ari export` prints it, or every problem that stopped the export with nothing written. A hand-edited file still takes `ari export --force` from the shell.
- `ls` and the TUI have a column per module that writes files, headed by its name: `✓` when `export` writes the host there, `excluded` when the host lists the module in `exclude`, `·` when its inventory doesn't have the module on. In `ls` they sit between PORT and GROUPS.
- `ls` and `ari group` never cut a fixed column. When those alone are wider than the terminal or the 80 columns a pipe gets, the flexible columns shrink to their headings and the rows run past the edge whole, so `ari ls | grep` sees every name and address.
- `add` and `edit` check the name, hostname, port and every alias each on its own, so all the malformed ones are listed instead of only the first.

## 0.5.0 (2026-10-04)

- `ari init` writes a commented starter `config.toml` and prints where it went. It refuses when one exists, whatever it holds, and is the only time ari writes that file. `--help`, `--version` and `ari modules` run without a config.
- `edit --unalias ALIAS`, repeatable, drops an alias. It runs before `--alias`, so `--unalias old --alias new` swaps one for the other. Naming an alias the host doesn't have is an error.
- `ls` keeps each host on one line. GROUPS shows as many groups as fit beside the other columns, then `+N` for the rest, instead of wrapping; `show` lists them all.
- `ari group` lists the declared groups with how many hosts each holds, the ones its children bring included. `ari group NAME` declares or changes one with `--description`, `--child`, `--unchild`, `--reason KEY=TEXT` (`KEY=` removes it) and `--rm`. Nothing a host relies on can go: removing a group, a child or a reason still in use is refused, naming the hosts and how they get there. The error for an undeclared group now gives the command that declares it.

## 0.4.1 (2026-10-04)

- `show` honours `-i`: it looks only in that inventory, and an undeclared one is an error instead of being ignored.
- `ls --search` matches what `ls` shows. User, port and key match their effective values, inherited from the defaults or not, so `--search 2222` finds a host whose PORT column says 2222. Reasons and `exclude` match too.
- `export` stops before any module renders once a check has failed. Modules only ever see data that passed.
- Ansible files are written with mode 0644; ssh config and inventories stay 0600. A module sets the mode per file it returns, 0600 unless it says otherwise. `export` corrects the mode of a file whose contents are already right and says so.
- The test named for a missing plugin was parking the installed ansible module. It's renamed, and a real missing-plugin test sits beside it.

## 0.4.0 (2026-10-03)

- `add`, `edit` and `rm`. Every write is checked first: names and aliases unique across inventories, groups declared, reasons valid. A refused write lists every problem and saves nothing.
- The `ansible` module: import from an inventory directory, export a hosts file and routed group files with zone sections. The output is byte for byte stable. Export refuses a host in no zone or two, and a group routed to no file or two.
- Import runs every host through the same checks as `add`. A host that would leave the inventory unreadable, or reuse a name, is refused and reported, and the rest still import.
- A changed ssh option on re-import, like a new ProxyJump, is a conflict instead of being dropped under `unchanged`.
- The guard adopts an imported file only when the inventory holds all of it. A conflict, a refused host, or a skipped block leaves the file alone until `export --force`.
- A repeated IdentityFile, CertificateFile, LocalForward, RemoteForward, DynamicForward or SendEnv no longer claims ssh uses the first value. ssh uses all of them; ari keeps the first and says so.
- `make install`, `make uninstall`, `make man` and `make check`.

## 0.3.0 (2026-10-03)

- Formats are modules, found through the `ari.modules` entry point group. ssh is the first.
- Inventory schema version 2: per-module data and `exclude`. Version 1 files upgrade when read.
- Import no longer pins the importing login as a host's User.

## 0.2.0 (2026-10-03)

- Renamed from hosts to ari. README, licensed GPL-3.0-or-later.

## 0.1.0 (2026-10-03)

- Config, inventories, the hash guard, atomic writes, ssh import and export, `ls`, `show`, and the manual.
