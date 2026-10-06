"""Writes touch nothing ari didn't create, one ari writes at a time, and export's guard state is
written with the files it records."""

import errno
import fcntl
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import PERSONAL_ONLY, write_config, write_mixed
from ari import storage
from ari.cli import main


def run(*argv):
    return main(list(argv))


def inventory(home, name="personal"):
    return home / "config" / "ari" / f"{name}.json"


def names(home, name="personal"):
    return [h["name"] for h in json.loads(inventory(home, name).read_text())["hosts"]]


def temps(directory: Path) -> list[Path]:
    return sorted(directory.rglob(f"*{storage.TEMP_SUFFIX}"))


# A file already beside the target is never ari's to touch


def test_a_link_at_the_old_temp_name_is_never_followed(home):
    write_config(home, PERSONAL_ONLY)
    victim = home / "victim"
    victim.write_text("precious\n")
    victim.chmod(0o644)
    (home / "ssh" / "10-personal.conf.tmp").symlink_to(victim)
    assert run("add", "a", "192.0.2.120") == 0
    assert run("export") == 0
    assert victim.read_text() == "precious\n"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644
    target = home / "ssh" / "10-personal.conf"
    assert not target.is_symlink() and "HostName 192.0.2.120" in target.read_text()


def test_a_file_at_the_old_temp_name_is_left_alone(home):
    write_config(home, PERSONAL_ONLY)
    kept = home / "ssh" / "10-personal.conf.tmp"
    kept.write_text("USER FILE KEEP ME\n")
    assert run("add", "a", "192.0.2.120") == 0
    assert run("export") == 0
    assert kept.read_text() == "USER FILE KEEP ME\n"


def test_an_inventory_save_never_follows_a_link(home):
    write_config(home, PERSONAL_ONLY)
    victim = home / "victim"
    victim.write_text("precious\n")
    (home / "config" / "ari" / "personal.json.tmp").symlink_to(victim)
    assert run("add", "a", "192.0.2.121") == 0
    assert victim.read_text() == "precious\n"
    assert not inventory(home).is_symlink() and names(home) == ["a"]


def test_a_link_at_the_temp_name_drawn_is_skipped(home, monkeypatch):
    """Temps are created new, never through a link: one planted at the very name drawn costs a
    second draw and nothing else."""
    write_config(home, PERSONAL_ONLY)
    victim = home / "victim"
    victim.write_text("precious\n")
    target = home / "ssh" / "10-personal.conf"
    planted = target.with_name(f".{target.name}.aaaaaaaa{storage.TEMP_SUFFIX}")
    planted.symlink_to(victim)
    draws = iter(["aaaaaaaa", "bbbbbbbb"])
    monkeypatch.setattr(storage, "_random", lambda: next(draws))
    storage.atomic_write(target, b"Host a\n", 0o600)
    assert victim.read_text() == "precious\n" and planted.is_symlink()
    assert target.read_bytes() == b"Host a\n"
    assert not target.with_name(f".{target.name}.bbbbbbbb{storage.TEMP_SUFFIX}").exists()


def test_temps_are_hidden_and_carry_the_mode_of_their_file(tmp_path):
    staged = storage.Staged()
    staged.add(tmp_path / "00-hosts.yml", b"all: {}\n", 0o644)
    (tmp,) = staged.temps.values()
    assert tmp.parent == tmp_path and tmp.name.startswith(".00-hosts.yml.") and tmp.name.endswith(storage.TEMP_SUFFIX)
    assert stat.S_IMODE(tmp.stat().st_mode) == 0o644
    staged.cleanup()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(shutil.which("ssh") is None, reason="needs the ssh client")
def test_ssh_never_reads_a_temp_through_its_include(tmp_path):
    confd = tmp_path / "config.d"
    storage.atomic_write(confd / "10-personal.conf", b"Host a\n    HostName 192.0.2.1\n", 0o600)
    staged = storage.Staged()
    staged.add(confd / "10-personal.conf", b"Host evil\n    HostName 203.0.113.9\n", 0o600)
    config = tmp_path / "config"
    config.write_text(f"Include {confd}/*.conf\n")
    resolved = subprocess.run(["ssh", "-G", "-F", str(config), "evil"], capture_output=True, text=True, check=True)
    assert "hostname evil" in resolved.stdout.splitlines()
    staged.cleanup()


@pytest.mark.skipif(shutil.which("ansible-inventory") is None, reason="needs ansible-inventory")
def test_ansible_never_reads_a_temp_in_the_inventory_directory(tmp_path):
    inv = tmp_path / "inventory"
    storage.atomic_write(inv / "00-hosts.yml", b"all:\n  hosts:\n    a:\n      ansible_host: 192.0.2.1\n", 0o644)
    staged = storage.Staged()
    staged.add(inv / "00-hosts.yml", b"all:\n  hosts:\n    evil:\n      ansible_host: 203.0.113.9\n", 0o644)
    listed = subprocess.run(
        ["ansible-inventory", "-i", str(inv), "--list"], capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL
    )
    assert set(json.loads(listed.stdout)["_meta"]["hostvars"]) == {"a"}
    staged.cleanup()


# One ari writes at a time

ADD = "import sys\nfrom ari.cli import main\nsys.exit(main(sys.argv[1:]))"


def test_parallel_adds_keep_every_host_they_report(home):
    """Every add that says it added a host leaves it on disk, and none crashes. On a slow disk a
    long queue can outlast the wait; such an add stops with nothing changed, and says so."""
    write_config(home, PERSONAL_ONLY)
    procs = [
        subprocess.Popen([sys.executable, "-c", ADD, "add", f"par{i}", f"192.0.2.{i}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for i in range(1, 31)
    ]
    results = []
    for i, p in enumerate(procs, 1):
        out, err = p.communicate(timeout=60)
        results.append((f"par{i}", p.returncode, out.decode(), err.decode()))
    added = [name for name, code, out, _ in results if code == 0 and out == f"added {name} to personal\n"]
    stopped = [name for name, code, _, err in results if code == 1 and "another ari is still writing" in err]
    assert sorted(added + stopped) == sorted(name for name, *_ in results)
    assert len(added) > len(stopped)
    assert sorted(names(home)) == sorted(added)
    assert temps(home) == []


def held(home):
    """Hold the lock the way another ari would."""
    path = home / "state" / "ari" / "lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def test_a_write_waits_for_another_ari_then_stops_with_nothing_saved(home, monkeypatch, capsys):
    write_config(home, PERSONAL_ONLY)
    assert run("add", "first", "192.0.2.1") == 0
    before = inventory(home).read_bytes()
    monkeypatch.setattr(storage, "LOCK_WAIT", 0.2, raising=False)
    fd = held(home)
    try:
        capsys.readouterr()
        assert run("add", "second", "192.0.2.2") == 1
        assert run("export") == 1
    finally:
        os.close(fd)
    err = capsys.readouterr().err
    assert "another ari is still writing" in err and "lock" in err and "nothing changed" in err
    assert inventory(home).read_bytes() == before
    assert not (home / "ssh" / "10-personal.conf").exists()
    assert run("add", "second", "192.0.2.2") == 0


def test_reads_never_wait_for_the_lock(home, monkeypatch, capsys):
    write_config(home, PERSONAL_ONLY)
    assert run("add", "first", "192.0.2.1") == 0
    monkeypatch.setattr(storage, "LOCK_WAIT", 0.2, raising=False)
    fd = held(home)
    try:
        capsys.readouterr()
        assert run("ls", "--format", "csv") == 0 and run("show", "first") == 0 and run("key") == 0 and run("group") == 0
    finally:
        os.close(fd)
    assert "another ari" not in capsys.readouterr().err


def test_the_lock_is_let_go_after_a_refusal(home):
    write_config(home, PERSONAL_ONLY)
    assert run("add", "bad name", "192.0.2.1") == 1
    assert run("add", "good", "192.0.2.1") == 0


def test_a_state_directory_ari_cant_use_stops_a_write_before_it_starts(home, capsys):
    write_config(home, PERSONAL_ONLY)
    (home / "state").mkdir()
    (home / "state" / "ari").write_text("not a directory\n")
    capsys.readouterr()
    assert run("add", "a", "192.0.2.1") == 1
    assert "nothing changed" in capsys.readouterr().err
    assert not inventory(home).exists()


# export's guard state is written with the files it records


def snapshot(home):
    return {p: p.read_bytes() for p in sorted((home / "ssh").rglob("*")) if p.is_file()}


def test_a_guard_that_cant_be_staged_stops_export_before_any_rename(home, monkeypatch, capsys):
    write_mixed(home)
    assert run("export") == 0
    assert run("edit", "vault-01", "--port", "2200") == 0
    before, guard = snapshot(home), (home / "state" / "ari" / "exports.json").read_bytes()
    real = storage._write_temp

    def full(target, data, mode):
        if Path(target).name.startswith("exports.json"):
            raise OSError(errno.ENOSPC, "No space left on device", str(target))
        return real(target, data, mode)

    monkeypatch.setattr(storage, "_write_temp", full)
    capsys.readouterr()
    assert run("export") == 1
    err = capsys.readouterr().err
    assert "export stopped, nothing written" in err and "exports.json: No space left on device" in err
    assert snapshot(home) == before
    assert (home / "state" / "ari" / "exports.json").read_bytes() == guard
    assert temps(home) == []
    monkeypatch.setattr(storage, "_write_temp", real)
    capsys.readouterr()
    assert run("export") == 0
    assert "edited since" not in capsys.readouterr().err


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes into a read-only directory")
def test_a_read_only_state_directory_stops_export_and_the_next_one_passes(home, capsys):
    write_config(home, PERSONAL_ONLY)
    assert run("add", "a", "192.0.2.130") == 0 and run("export") == 0
    state = home / "state" / "ari"
    assert run("edit", "a", "--hostname", "192.0.2.131") == 0
    before = snapshot(home)
    state.chmod(0o555)
    try:
        capsys.readouterr()
        assert run("export") == 1
        assert "exports.json: Permission denied" in capsys.readouterr().err
        assert snapshot(home) == before
    finally:
        state.chmod(0o755)
    assert run("edit", "a", "--hostname", "192.0.2.132") == 0
    capsys.readouterr()
    assert run("export") == 0
    assert "HostName 192.0.2.132" in (home / "ssh" / "10-personal.conf").read_text()
