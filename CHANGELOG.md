# Changelog

Versions follow [semantic versioning](https://semver.org/). Before 1.0, a minor version adds features or changes the inventory format, and a patch version only fixes bugs. Each release is a `vX.Y.Z` tag on GitHub.

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
