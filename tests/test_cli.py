import json
import re
import shutil
import stat
import subprocess

import pytest
import yaml
from rich.cells import cell_len
from rich.console import Console

from conftest import FIXTURES, PERSONAL_AND_WORK, PERSONAL_ONLY, copy_fixture, write_config, write_mixed
from ari import cli, core
from ari.cli import _fit_names, main
from ari.config import STARTER, load_config
from ari.modules import registry
from ari.modules.ssh import SshModule


def run(*argv):
    return main(list(argv))


def personal_json(home):
    return json.loads((home / "config" / "ari" / "personal.json").read_text())


def test_no_arguments_prints_help(capsys):
    assert run() == 0
    assert "usage: ari" in capsys.readouterr().out


def test_missing_config_explains_itself(home, capsys):
    assert run("ls") == 1
    err = capsys.readouterr().err
    assert "no config at" in err and "ari init" in err


@pytest.mark.parametrize("argv", [["--help"], ["--version"], ["ls", "--help"], ["init", "--help"]])
def test_help_and_version_need_no_config(home, capsys, argv):
    with pytest.raises(SystemExit) as e:
        run(*argv)
    assert e.value.code == 0
    assert capsys.readouterr().out


def test_modules_needs_no_config(home, capsys):
    assert run("modules") == 0
    captured = capsys.readouterr()
    assert "ssh" in captured.out and "ansible" in captured.out
    assert "ari init" in captured.err


def test_init_writes_a_working_starter_once(home, capsys):
    path = home / "config" / "ari" / "config.toml"
    assert run("init") == 0
    assert str(path) in capsys.readouterr().out
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    cfg = load_config()
    assert cfg.default == "personal"
    assert [m.module.name for m in cfg.get("personal").enabled()] == ["ssh"]
    starter = path.read_bytes()
    assert run("init") == 1
    assert "already exists" in capsys.readouterr().err
    assert path.read_bytes() == starter


def test_init_leaves_any_existing_config_alone(home, capsys):
    path = write_config(home, "not toml at all [")
    assert run("init") == 1
    assert path.read_text() == "not toml at all ["
    assert sorted(p.name for p in path.parent.iterdir()) == ["config.toml"]


def test_starter_examples_load_once_uncommented(home):
    write_config(home, re.sub(r"(?m)^# (?=\[|\S+ = )", "", STARTER))
    cfg = load_config()
    assert list(cfg.inventories) == ["personal", "work"]
    assert set(cfg.get("work").modules) == {"ssh", "ansible"}


def test_import_infers_no_defaults_and_each_host_keeps_its_own(personal, capsys):
    assert run("import", "ssh", str(FIXTURES / "personal.conf")) == 0
    out = capsys.readouterr().out
    assert "4 added" in out and "defaults set" not in out
    data = personal_json(personal)
    assert data["defaults"] == {}
    assert data["keys"] == {"personal-ed25519": {"path": "~/.ssh/personal-ed25519"}}
    by_name = {h["name"]: h for h in data["hosts"]}
    assert (by_name["laptop"]["user"], by_name["laptop"]["keys"]) == ("tom", ["personal-ed25519"])
    assert by_name["nas"]["user"] == "root"
    assert by_name["github.com"]["user"] == "git"


def test_export_matches_expected_then_is_stable(personal, capsys):
    run("import", "ssh", str(FIXTURES / "personal.conf"))
    assert run("export") == 0
    target = personal / "ssh" / "10-personal.conf"
    assert target.read_text() == (FIXTURES / "personal.expected.conf").read_text()
    capsys.readouterr()
    assert run("export") == 0
    assert "unchanged" in capsys.readouterr().out


def test_migration_flow_import_target_then_export(personal):
    target = copy_fixture("personal.conf", personal / "ssh" / "10-personal.conf")
    assert run("-i", "personal", "import", "ssh", str(target)) == 0
    assert run("export", "ssh") == 0
    assert target.read_text() == (FIXTURES / "personal.expected.conf").read_text()


def test_guard_refuses_unknown_and_edited_files(personal, capsys):
    target = personal / "ssh" / "10-personal.conf"
    target.write_text("# hand made\n")
    run("import", "ssh", str(FIXTURES / "personal.conf"))
    capsys.readouterr()

    assert run("export") == 1
    assert "wasn't written by ari" in capsys.readouterr().err
    assert target.read_text() == "# hand made\n"

    assert run("export", "--force") == 0
    target.write_text(target.read_text() + "# edited\n")
    assert run("export") == 1
    assert "edited since the last export" in capsys.readouterr().err


def test_inventory_flag_after_subcommand(both, capsys):
    assert run("import", "ssh", str(FIXTURES / "legacy.conf"), "-i", "work", "--exclude", "ansible") == 0
    capsys.readouterr()
    assert run("ls", "-i", "work") == 0
    listing = capsys.readouterr().out
    assert "oldbox" in listing and "firewall" in listing and "2222" in listing


def test_names_are_unique_across_inventories(both, tmp_path, capsys):
    run("import", "ssh", str(FIXTURES / "personal.conf"))
    clash = tmp_path / "clash.conf"
    clash.write_text("Host storage\n  HostName 203.0.113.9\n")
    assert run("-i", "work", "import", "ssh", str(clash)) == 1
    assert "already defined as personal/nas" in capsys.readouterr().err


def test_reimport_merges_aliases_and_reports_conflicts(personal, tmp_path, capsys):
    run("import", "ssh", str(FIXTURES / "personal.conf"))
    update = tmp_path / "update.conf"
    update.write_text("Host laptop lap\n  HostName 192.0.2.11\n  User tom\n\nHost vps\n  HostName 198.51.100.99\n  User tom\n")
    capsys.readouterr()
    assert run("import", "ssh", str(update)) == 1
    captured = capsys.readouterr()
    assert "1 merged (laptop)" in captured.out
    assert "HostName 198.51.100.99 differs" in captured.err
    laptop = next(h for h in personal_json(personal)["hosts"] if h["name"] == "laptop")
    assert laptop["aliases"] == ["192.0.2.11", "lap"]


def test_show_marks_inherited_values(personal, capsys):
    run("import", "ssh", str(FIXTURES / "personal.conf"))
    capsys.readouterr()
    assert run("show", "storage") == 0
    shown = capsys.readouterr().out
    assert "root" in shown and "(default)" in shown


def test_show_honours_the_inventory_flag(both, capsys):
    run("import", "ssh", str(FIXTURES / "personal.conf"))
    capsys.readouterr()
    assert run("show", "-i", "personal", "storage") == 0
    assert "nas" in capsys.readouterr().out
    assert run("show", "-i", "work", "storage") == 1
    assert "no host named 'storage' in work" in capsys.readouterr().err
    assert run("-i", "nope", "show", "storage") == 1
    assert "no inventory 'nope'" in capsys.readouterr().err


def test_show_reads_only_the_inventory_it_was_given(both, capsys):
    run("import", "ssh", str(FIXTURES / "personal.conf"))
    (both / "config" / "ari" / "work.json").write_text("{not json")
    capsys.readouterr()
    assert run("show", "-i", "personal", "nas") == 0
    assert run("show", "nas") == 1
    assert "work.json: not valid JSON" in capsys.readouterr().err


def search_inventory(home):
    """Hosts whose user, port and key come from the defaults, plus reasons and exclude."""
    write_config(home, PERSONAL_ONLY)
    (home / "config" / "ari" / "personal.json").write_text(json.dumps({
        "version": 2,
        "defaults": {"user": "deploy", "port": 2222, "ssh_key": "~/.ssh/lab-ed25519"},
        "groups": {"no_auto_update": {"reasons": {"secrets": "secrets and prod path"}}},
        "hosts": [
            {"name": "inherits", "hostname": "192.0.2.10"},
            {"name": "own", "hostname": "192.0.2.11", "user": "admin", "port": 22, "ssh_key": "~/.ssh/own-ed25519"},
            {"name": "held", "hostname": "192.0.2.12", "groups": ["no_auto_update"], "reasons": {"no_auto_update": "secrets"}},
            {"name": "hidden", "hostname": "192.0.2.13", "exclude": ["ansible"]},
        ],
    }))


@pytest.mark.parametrize(
    "needle, found",
    [
        ("2222", {"inherits", "held", "hidden"}),
        ("deploy", {"inherits", "held", "hidden"}),
        ("lab-ed25519", {"inherits", "held", "hidden"}),
        ("admin", {"own"}),
        ("own-ed25519", {"own"}),
        ("SECRETS", {"held"}),
        ("ansible", {"hidden"}),
    ],
)
def test_search_matches_effective_values_reasons_and_exclude(home, needle, found):
    search_inventory(home)
    rows = core.list_hosts(load_config(), search=needle)
    assert {host.name for _, host in rows} == found


def test_search_finds_the_port_ls_shows(home, capsys):
    search_inventory(home)
    assert run("ls", "--search", "2222") == 0
    rows = {cells[0]: cells for line in capsys.readouterr().out.splitlines() if (cells := line.split())}
    assert rows["inherits"][3] == "2222" and "own" not in rows


def test_fit_names_fits_what_it_can_and_counts_the_rest():
    groups = ["zone_app", "monitoring_db", "no_auto_update"]
    assert _fit_names([], 20).plain == "-"
    assert _fit_names(groups, 100).plain == "zone_app, monitoring_db, no_auto_update"
    assert _fit_names(groups, 30).plain == "zone_app, monitoring_db +1"
    assert _fit_names(groups, 12).plain == "zone_app +2"
    assert _fit_names(groups, 8).plain == "zone… +2"


@pytest.mark.parametrize("width, shown", [(80, None), (300, 6)])
def test_ls_rows_never_wrap_on_account_of_groups(home, monkeypatch, capsys, width, shown):
    groups = {f"group_{i}_with_a_long_name": {} for i in range(6)}
    hosts = [{"name": f"host-{i}", "hostname": f"192.0.2.{i}", "groups": list(groups)} for i in range(3)]
    write_config(home, PERSONAL_ONLY)
    (home / "config" / "ari" / "personal.json").write_text(json.dumps({"version": 2, "groups": groups, "hosts": hosts}))
    monkeypatch.setattr(cli, "out", Console(width=width, highlight=False))
    assert run("ls") == 0
    lines = capsys.readouterr().out.splitlines()
    assert all(cell_len(line) <= width for line in lines)
    rows = [line for line in lines if "192.0.2." in line]
    assert len(rows) == 3
    for row in rows:
        assert ("+5" in row) if shown is None else (row.count("_with_a_long_name") == shown)


def ls_columns(out: str) -> dict[str, dict[str, str]]:
    """ls rows by name, cut at the header's column starts."""
    lines = out.splitlines()
    header = next(line for line in lines if line.lstrip().startswith("NAME"))
    starts = [m.start() for m in re.finditer(r"\S+", header)]
    labels = header.split()
    rows = {}
    for line in lines[lines.index(header) + 2 :]:
        if not line.strip() or line.strip().endswith("hosts"):
            continue
        cells = [line[a:b].strip() for a, b in zip(starts, starts[1:] + [None])]
        rows[cells[0]] = dict(zip(labels, cells))
    return rows


def test_ls_has_a_column_per_exporting_module(home, monkeypatch, capsys):
    write_mixed(home)
    monkeypatch.setattr(cli, "out", Console(width=200, highlight=False))
    assert run("ls") == 0
    rows = ls_columns(capsys.readouterr().out)
    marks = {name: (row["SSH"], row["ANSIBLE"]) for name, row in rows.items()}
    assert marks == {
        "nas": ("✓", "·"),
        "scratch": ("excluded", "·"),
        "vault-01": ("✓", "✓"),
        "web-01": ("✓", "✓"),
        "fw": ("✓", "excluded"),
    }
    assert list(rows["nas"]) == ["NAME", "HOSTNAME", "USER", "PORT", "SSH", "ANSIBLE", "GROUPS", "INV"]
    assert run("ls", "-i", "personal") == 0
    assert list(ls_columns(capsys.readouterr().out)["nas"]) == ["NAME", "HOSTNAME", "USER", "PORT", "SSH", "GROUPS", "INV"]


@pytest.mark.parametrize("width", [40, 80])
def test_ls_never_cuts_a_name_or_an_address(home, monkeypatch, capsys, width):
    """When the fixed columns alone are wider than the terminal, rows run past it whole."""
    write_mixed(home)
    assert run("-i", "work", "add", "longhost", "backup-target-01.storage.example.net", "--group", "zone_app") == 0
    capsys.readouterr()
    monkeypatch.setattr(cli, "out", Console(width=width, highlight=False))
    assert run("ls") == 0
    lines = capsys.readouterr().out.splitlines()
    rows = {line.split()[0]: line.split() for line in lines[3:] if line.strip() and not line.endswith("hosts")}
    assert {name: cells[1] for name, cells in rows.items()} == {
        host.name: host.hostname for _, host in core.list_hosts(load_config())
    }
    assert lines[1].split() == ["NAME", "HOSTNAME", "USER", "PORT", "SSH", "ANSIBLE", "GROUPS", "INV"]
    assert all(cells[-1] in ("personal", "work") for cells in rows.values())


def test_a_mark_says_exactly_what_export_writes(home):
    write_mixed(home)
    assert run("export") == 0
    ssh = "".join((home / "ssh" / f).read_text() for f in ("10-personal.conf", "20-work.conf"))
    written = {
        "ssh": set(re.findall(r"^Host (\S+)", ssh, re.M)),
        "ansible": set(yaml.safe_load((home / "ssh" / "ansible" / "00-hosts.yml").read_text())["all"]["hosts"]),
    }
    cfg = load_config()
    assert core.export_modules(cfg) == ["ssh", "ansible"]
    for inventory, host in core.list_hosts(cfg):
        for module, names in written.items():
            reach = core.module_reach(cfg, inventory, host, module)
            assert (host.name in names) == (reach is core.ModuleReach.WRITTEN), (host.name, module, reach)


def test_a_parked_module_gets_no_column_and_reaches_no_host(home):
    write_config(home, PERSONAL_AND_WORK)
    (home / "config" / "ari" / "work.json").write_text(json.dumps({
        "version": 2, "hosts": [{"name": "app", "hostname": "192.0.2.5"}],
    }))
    cfg = load_config()
    assert core.export_modules(cfg) == ["ssh"]
    [(inventory, host)] = core.list_hosts(cfg, "work")
    assert core.module_reach(cfg, inventory, host, "ansible") is core.ModuleReach.OFF
    assert core.module_reach(cfg, inventory, host, "ssh") is core.ModuleReach.WRITTEN


def test_show_says_who_connects_when_no_user_is_set(personal, capsys, monkeypatch):
    monkeypatch.setattr(core.getpass, "getuser", lambda: "someone")
    assert run("add", "bare", "192.0.2.9") == 0
    capsys.readouterr()
    assert run("show", "bare") == 0
    assert re.search(r"^user\s+someone  \(whoever connects\)", capsys.readouterr().out, re.M)


def test_a_parked_module_does_not_block_export(both, capsys):
    """work parks the ansible module, which is installed."""
    assert run("export") == 0
    out = capsys.readouterr().out
    assert "10-personal.conf (personal, ssh, 0 hosts)" in out and "20-work.conf (work, ssh, 0 hosts)" in out


def test_a_missing_plugin_blocks_nothing_and_keeps_its_data(home, capsys):
    """A parked table and host data for a module that isn't installed at all."""
    assert "netbox" not in registry().modules and "netbox" not in registry().failures
    write_config(home, PERSONAL_ONLY + '\n[inventories.personal.netbox]\nurl = "https://netbox.example"\nenabled = false\n')
    inventory = home / "config" / "ari" / "personal.json"
    inventory.write_text(json.dumps({
        "version": 2,
        "hosts": [{"name": "vps", "hostname": "198.51.100.1", "modules": {"netbox": {"id": 42}}}],
    }))
    assert run("export") == 0
    assert "10-personal.conf (personal, ssh, 1 host)" in capsys.readouterr().out
    assert "Host vps" in (home / "ssh" / "10-personal.conf").read_text()
    assert run("show", "vps") == 0
    assert "(module not installed)" in capsys.readouterr().out
    assert run("edit", "vps", "--notes", "edge") == 0
    assert json.loads(inventory.read_text())["hosts"][0]["modules"] == {"netbox": {"id": 42}}
    assert run("modules") == 0
    assert "netbox" not in capsys.readouterr().out


def test_export_calls_no_module_once_validation_fails(personal, monkeypatch, capsys):
    run("import", "ssh", str(FIXTURES / "personal.conf"))
    path = personal / "config" / "ari" / "personal.json"
    data = json.loads(path.read_text())
    data["hosts"][0]["groups"] = ["typo"]
    path.write_text(json.dumps(data))

    def export(*args):
        raise AssertionError("module.export called after validation failed")

    monkeypatch.setattr(SshModule, "export", export)
    assert run("export") == 1
    assert "group 'typo' isn't declared" in capsys.readouterr().err
    assert not (personal / "ssh" / "10-personal.conf").exists()


def test_a_stopped_export_carries_each_problem_and_prints_as_before(home):
    write_mixed(home)
    path = home / "config" / "ari" / "work.json"
    data = json.loads(path.read_text())
    data["hosts"].append({"name": "stray", "hostname": "192.0.2.200"})
    path.write_text(json.dumps(data))
    with pytest.raises(core.ExportStopped) as e:
        core.export(load_config())
    assert e.value.problems == ["work/ansible: stray is in no zone"] and e.value.hint == ""
    assert str(e.value) == "export stopped, nothing written:\n  work/ansible: stray is in no zone"
    assert not list((home / "ssh").iterdir())


def test_add_lists_every_bad_field_each_with_its_field(home, capsys):
    write_mixed(home)
    assert run("-i", "work", "add", "a b", "x y", "--port", "0", "--alias", "c*") == 1
    assert capsys.readouterr().err.splitlines()[1:] == [
        "  work: name 'a b' is not a valid ssh host name",
        "  work (a b): hostname must be non-empty, without spaces",
        "  work (a b): port must be an integer 1-65535, got 0",
        "  work (a b): alias 'c*' is not a valid ssh host name",
    ]
    with pytest.raises(core.HostRefused) as e:
        core.edit_host(load_config(), "fw", core.Changes(port="x", unalias=["nope"], include=["ssh"], ungroup=["g"]))
    assert [p.field for p in e.value.problems] == ["port", "aliases", "groups", "exclude"]


def test_exclude_keeps_a_host_out_of_that_module(personal, tmp_path):
    run("import", "ssh", str(FIXTURES / "personal.conf"))
    side = tmp_path / "side.conf"
    side.write_text("Host scratch\n  HostName 192.0.2.77\n  User tom\n")
    assert run("import", "ssh", str(side), "--exclude", "ssh") == 0
    run("export")
    assert "scratch" not in (personal / "ssh" / "10-personal.conf").read_text()
    stored = next(h for h in personal_json(personal)["hosts"] if h["name"] == "scratch")
    assert stored["exclude"] == ["ssh"]


def test_disabled_module_writes_nothing(home, capsys):
    write_config(home, '[inventories.personal.ssh]\npath = "SSH/10-personal.conf"\nenabled = false\n')
    assert run("export") == 0
    assert "nothing to export" in capsys.readouterr().err
    assert not (home / "ssh" / "10-personal.conf").exists()


def test_export_only_named_modules(personal, capsys):
    assert run("export", "ssh") == 0
    assert run("export", "nope") == 1
    assert "unknown module 'nope'" in capsys.readouterr().err


def test_missing_user_inherits_a_declared_default_with_a_warning(personal, tmp_path, capsys):
    (personal / "config" / "ari" / "personal.json").write_text(json.dumps({"version": 3, "defaults": {"user": "tom"}}))
    loose = tmp_path / "loose.conf"
    loose.write_text("Host spare\n  HostName 192.0.2.88\n")
    capsys.readouterr()
    assert run("import", "ssh", str(loose)) == 0
    err = capsys.readouterr().err
    assert "no User in the source; the default user tom will apply" in err and "not adopted" in err
    stored = next(h for h in personal_json(personal)["hosts"] if h["name"] == "spare")
    assert "user" not in stored


def test_modules_lists_what_is_installed_and_where(both, capsys):
    assert run("modules") == 0
    listing = capsys.readouterr().out
    assert "ssh" in listing and "personal, work" in listing


def test_old_ssh_string_form_explains_the_fix(home, capsys):
    write_config(home, '[inventories.personal]\nssh = "SSH/10-personal.conf"\n')
    assert run("ls") == 1
    assert "[inventories.personal.ssh]" in capsys.readouterr().err


@pytest.mark.skipif(shutil.which("ssh") is None, reason="needs the ssh client")
@pytest.mark.parametrize("fixture", ["personal.conf", "legacy.conf"])
def test_ssh_resolves_every_alias_identically(personal, fixture):
    """The real proof: ssh -G gives the same answer before and after a round trip."""
    source = FIXTURES / fixture
    run("import", "ssh", str(source))
    run("export", "--force")
    exported = personal / "ssh" / "10-personal.conf"
    tokens = {t for line in source.read_text().splitlines() if line.startswith("Host ") for t in line.split()[1:]}

    def resolve(config, token):
        return subprocess.run(["ssh", "-G", "-F", str(config), token], capture_output=True, text=True, check=True).stdout

    for token in sorted(tokens):
        assert resolve(source, token) == resolve(exported, token), token
