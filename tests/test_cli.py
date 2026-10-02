import json
import shutil
import subprocess

import pytest

from conftest import FIXTURES, copy_fixture
from ari.cli import main


def run(*argv):
    return main(list(argv))


def personal_json(home):
    return json.loads((home / "config" / "ari" / "personal.json").read_text())


def test_no_arguments_prints_help(capsys):
    assert run() == 0
    assert "usage: ari" in capsys.readouterr().out


def test_missing_config_explains_itself(home, capsys):
    assert run("ls") == 1
    assert "no config at" in capsys.readouterr().err


def test_import_infers_defaults_and_stores_only_differences(personal, capsys):
    assert run("import", "ssh", str(FIXTURES / "personal.conf")) == 0
    assert "4 added" in capsys.readouterr().out
    data = personal_json(personal)
    assert data["defaults"] == {"user": "tom", "ssh_key": "~/.ssh/personal-ed25519"}
    by_name = {h["name"]: h for h in data["hosts"]}
    assert set(by_name["laptop"]) == {"name", "hostname", "aliases", "last_updated"}
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
    assert run("import", "ssh", str(FIXTURES / "legacy.conf"), "-i", "work", "--no-ansible") == 0
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


def test_ansible_target_is_noted_not_built(both, capsys):
    assert run("export") == 0
    assert "Ansible export isn't built yet" in capsys.readouterr().err


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
