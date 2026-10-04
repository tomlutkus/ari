# Changelog

Versions follow [semantic versioning](https://semver.org/). Before 1.0, a minor version adds features or changes the inventory format, and a patch version only fixes bugs. Each release is a `vX.Y.Z` tag on GitHub.

## Unreleased

- ssh import reads a Host line with several names and no HostName as one host per name, each connecting to its own name, as ssh does. Before, the other names became aliases of the first and connected to it.
- ssh import treats a HostName or Port that a block leaves out as not set when the host is already in the inventory, from a later block in the same file or on re-import. Such a block now adds its user, key, aliases or options instead of reporting a HostName conflict. A value set differently is still a conflict.

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
