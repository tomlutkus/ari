"""The column registry: ls --columns picks from it, ls --format prints it as Markdown or CSV, and
the table module writes the same bytes. Values are the ones that take effect."""

import csv
import getpass
import io
import re

import pytest
from rich.cells import cell_len
from rich.console import Console

from conftest import FIXTURES, write_listed
from ari import cli, core
from ari.columns import COLUMNS
from ari.cli import main
from ari.config import load_config

EVERY = "name,hostname,aliases,user,port,keys,os,notes,groups,exclude,inv,ssh,ansible"


def run(*argv):
    return main(list(argv))


@pytest.fixture
def listed(home):
    write_listed(home)
    return home


def printed(capsys, *argv) -> str:
    assert run("ls", *argv) == 0
    return capsys.readouterr().out


def test_the_registry_names_each_column_and_heads_it_in_capitals():
    assert list(COLUMNS) == [
        "name", "hostname", "aliases", "user", "port", "keys", "os", "notes", "groups", "exclude", "inv"
    ]
    assert all(c.heading == c.name.upper() for c in COLUMNS.values())


@pytest.mark.parametrize("form, fixture", [("md", "ls.md"), ("csv", "ls.csv")])
def test_every_column_prints_as_its_fixture(listed, capsys, form, fixture):
    assert printed(capsys, "--format", form, "--columns", EVERY) == (FIXTURES / fixture).read_text()


def test_csv_reads_back_as_the_values_that_take_effect(listed, capsys):
    rows = list(csv.reader(io.StringIO(printed(capsys, "--format", "csv", "--columns", EVERY))))
    assert rows[0] == EVERY.split(",")
    by_name = {row[0]: dict(zip(rows[0], row)) for row in rows[1:]}
    assert by_name["fw"]["user"] == "deploy" and by_name["fw"]["port"] == "2222" and by_name["fw"]["keys"] == "old, lab"
    assert by_name["vault-01"]["keys"] == "lab" and by_name["vault-01"]["groups"] == "zone_app, no_auto_update"
    assert by_name["pi"]["notes"] == 'two lines\nthe second | with a pipe, "quoted"'
    assert by_name["web-01"]["aliases"] == "www, 192.0.2.10" and by_name["web-01"]["port"] == "22"
    assert (by_name["scratch"]["ssh"], by_name["fw"]["ansible"], by_name["nas"]["ansible"]) == ("excluded", "excluded", "·")
    assert [row[0] for row in rows[1:]] == [h.name for _, h in core.list_hosts(load_config())]


def test_markdown_keeps_each_host_on_one_line(listed, capsys):
    lines = printed(capsys, "--format", "md", "--columns", "name,notes").splitlines()
    assert lines[:2] == ["| NAME | NOTES |", "| --- | --- |"]
    assert len(lines) == 2 + len(core.list_hosts(load_config()))
    assert '| pi | two lines<br>the second \\| with a pipe, "quoted" |' in lines


def test_a_user_set_nowhere_is_blank_in_a_file_and_whoever_connects_on_screen(listed, capsys, monkeypatch):
    assert "| nas |  |" in printed(capsys, "--format", "md", "--columns", "name,user")
    assert "nas,\n" in printed(capsys, "--format", "csv", "--columns", "name,user")
    monkeypatch.setattr(cli, "out", Console(width=100, highlight=False))
    rows = {line.split()[0]: line.split() for line in printed(capsys, "--columns", "name,user").splitlines()[3:7]}
    assert rows["nas"] == ["nas", getpass.getuser()] and rows["pi"] == ["pi", "pi"]


def test_a_format_without_columns_takes_the_ones_ls_shows(listed, capsys):
    assert printed(capsys, "--format", "csv").splitlines()[0] == "name,hostname,user,port,ssh,ansible,groups,inv"
    assert printed(capsys, "--format", "csv", "-i", "personal").splitlines()[0] == "name,hostname,user,port,ssh,groups,inv"


@pytest.mark.parametrize(
    "argv, names",
    [
        (["-i", "personal"], ["nas", "pi", "scratch"]),
        (["--search", "rocky"], ["vault-01"]),
        (["--search", "old"], ["fw"]),
        (["--group", "zone_app"], ["vault-01", "web-01"]),
    ],
)
def test_a_format_lists_the_hosts_ls_would(listed, capsys, argv, names):
    rows = printed(capsys, "--format", "csv", "--columns", "name", *argv).splitlines()
    assert rows == ["name", *names]


def test_no_hosts_is_the_heading_alone(listed, capsys):
    assert printed(capsys, "--format", "csv", "--search", "nothing like it") == "name,hostname,user,port,ssh,ansible,groups,inv\n"
    assert printed(capsys, "--format", "md", "--columns", "name,os", "--search", "nothing like it") == "| NAME | OS |\n| --- | --- |\n"
    assert printed(capsys, "--columns", "name,os", "--search", "nothing like it") == "no hosts\n"


@pytest.mark.parametrize(
    "argv, message",
    [
        (["--columns", "name,nope"], "no column 'nope'; there are name, hostname, aliases, user, port, keys, os, notes, groups, exclude, inv, ssh, ansible"),
        (["--columns", "name,"], "no column ''"),
        (["--columns", "NAME"], "no column 'NAME'"),
        (["--columns", "name,os,name"], "column 'name' is named twice"),
        (["--columns", "name,ansible", "-i", "personal"], "no column 'ansible'; there are name, hostname, aliases, user, port, keys, os, notes, groups, exclude, inv, ssh"),
        (["--columns", "name", "--format", "html"], None),
    ],
)
def test_a_column_ls_doesnt_have_is_refused(listed, capsys, argv, message):
    if message is None:
        with pytest.raises(SystemExit):
            run("ls", *argv)
        assert "invalid choice: 'html'" in capsys.readouterr().err
        return
    assert run("ls", *argv) == 1
    out = capsys.readouterr()
    assert out.out == "" and message in out.err


def test_columns_print_in_the_order_given(listed, capsys, monkeypatch):
    monkeypatch.setattr(cli, "out", Console(width=200, highlight=False))
    lines = printed(capsys, "--columns", "inv,os,name,ansible").splitlines()
    assert lines[1].split() == ["INV", "OS", "NAME", "ANSIBLE"]
    rows = [re.split(r"\s{2,}", line.strip()) for line in lines[3:9]]
    assert rows == [
        ["personal", "TrueNAS 25.04", "nas", "·"],
        ["personal", "Raspberry Pi OS 12", "pi", "·"],
        ["personal", "-", "scratch", "·"],
        ["work", "-", "fw", "excluded"],
        ["work", "Rocky 10.1", "vault-01", "✓"],
        ["work", "-", "web-01", "✓"],
    ]


WIDE = "name,hostname,os,aliases,keys,groups,notes,exclude"


@pytest.mark.parametrize("width", [90, 110, 140, 200])
def test_flexible_columns_share_what_is_left_and_rows_never_wrap(listed, capsys, monkeypatch, width):
    """Lists and notes give way, the widest first, down to their headings; name, hostname and os
    stay whole. The fixed columns and the headings take 89 here."""
    monkeypatch.setattr(cli, "out", Console(width=width, highlight=False))
    lines = printed(capsys, "--columns", WIDE).splitlines()
    assert all(cell_len(line) <= width for line in lines)
    body = "\n".join(lines)
    for _, host in core.list_hosts(load_config()):
        assert host.name in body and host.hostname in body and (host.os or "-") in body
    assert lines[-1] == "6 hosts" and len(lines) == 3 + 6 + 2
    if width == 200:
        assert "zone_app, no_auto_update" in body and "two lines" in body and "the second" not in body
    if width == 110:
        assert "basement NAS" in body and "front door" in body and "zone_app +1" in body and "www +1" in body
    if width == 90:
        assert "zone_…" in body and "base…" in body


@pytest.mark.parametrize("width", [30, 88])
def test_rows_run_past_a_terminal_too_narrow_for_the_fixed_columns(listed, capsys, monkeypatch, width):
    monkeypatch.setattr(cli, "out", Console(width=width, highlight=False))
    lines = printed(capsys, "--columns", WIDE).splitlines()
    assert cell_len(lines[1]) == 89
    assert lines[1].split() == [c.upper() for c in WIDE.split(",")]
    rows = {cells[0]: cells for line in lines[3:9] if (cells := line.split())}
    assert rows["pi"][:5] == ["pi", "192.0.2.50", "Raspberry", "Pi", "OS"]
    assert rows["vault-01"][:4] == ["vault-01", "192.0.2.30", "Rocky", "10.1"]


def test_ls_without_columns_is_its_own_set(listed, capsys, monkeypatch):
    monkeypatch.setattr(cli, "out", Console(width=200, highlight=False))
    default = printed(capsys)
    assert default == printed(capsys, "--columns", "name,hostname,user,port,ssh,ansible,groups,inv")
    assert default.splitlines()[1].split() == ["NAME", "HOSTNAME", "USER", "PORT", "SSH", "ANSIBLE", "GROUPS", "INV"]
