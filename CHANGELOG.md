# Changelog

Versions follow [semantic versioning](https://semver.org/). Before 1.0, a minor version adds features or changes the inventory format, and a patch version only fixes bugs. Each release is a `vX.Y.Z` tag on GitHub.

## Unreleased

- `ari init` writes a commented starter `config.toml` and prints where it went. It refuses when one exists, whatever it holds, and is the only time ari writes that file. `--help`, `--version` and `ari modules` run without a config.
- `edit --unalias ALIAS`, repeatable, drops an alias. It runs before `--alias`, so `--unalias old --alias new` swaps one for the other. Naming an alias the host doesn't have is an error.

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
