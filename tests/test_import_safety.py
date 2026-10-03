"""Import goes through the same door as add: nothing it saves can make the inventory unreadable,
and the guard only adopts a file the inventory now holds in full."""

import json
import shutil
import subprocess

import pytest

from ari.cli import main
from ari.modules.ssh import ACCUMULATING


def run(*argv):
    return main(list(argv))


def inventory(home, name="personal"):
    return home / "config" / "ari" / f"{name}.json"


def names(home, name="personal"):
    return [h["name"] for h in json.loads(inventory(home, name).read_text())["hosts"]]


def source(tmp_path, text, name="src.conf"):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


# Refused hosts never reach the file


@pytest.mark.parametrize(
    "block, message",
    [
        ("Host foo=bar\n  HostName 192.0.2.2\n", "name 'foo=bar' is not a valid ssh host name"),
        ('Host sp\n  HostName "a b"\n', "hostname must be non-empty, without spaces"),
        ("Host big\n  HostName 192.0.2.3\n  Port 70000\n", "not a port number; host skipped"),
        ("Host odd\n  HostName 192.0.2.4\n  Bad_Key yes\n", "isn't an ssh_config keyword"),
    ],
)
def test_a_bad_block_is_refused_and_the_rest_still_imports(personal, tmp_path, capsys, block, message):
    path = source(tmp_path, "Host good\n  HostName 192.0.2.1\n\n" + block)
    run("import", "ssh", path)
    out = capsys.readouterr()
    assert message in out.err
    assert names(personal) == ["good"]
    assert run("ls") == 0


def test_refusal_exits_1(personal, tmp_path):
    assert run("import", "ssh", source(tmp_path, "Host foo=bar\n  HostName 192.0.2.2\n")) == 1


def test_two_blocks_sharing_an_alias_cannot_both_get_in(personal, tmp_path, capsys):
    path = source(tmp_path, "Host nas storage\n  HostName 192.0.2.10\n\nHost other STORAGE\n  HostName 192.0.2.11\n")
    assert run("import", "ssh", path) == 1
    assert "refused: personal (other): 'STORAGE' is already used by personal/nas" in capsys.readouterr().err
    assert names(personal) == ["nas"]
    assert run("export") == 0


def test_a_merged_alias_that_clashes_is_refused(personal, tmp_path, capsys):
    run("import", "ssh", source(tmp_path, "Host a\n  HostName 192.0.2.1\n\nHost b\n  HostName 192.0.2.2\n"))
    before = inventory(personal).read_bytes()
    capsys.readouterr()
    assert run("import", "ssh", source(tmp_path, "Host a b\n  HostName 192.0.2.1\n", "again.conf")) == 1
    assert "'b' is already used by personal/b" in capsys.readouterr().err
    assert inventory(personal).read_bytes() == before


def test_refused_hosts_do_not_shape_the_defaults(personal, tmp_path):
    text = "Host x=1\n  HostName 192.0.2.1\n  User ghost\n\nHost y=2\n  HostName 192.0.2.2\n  User ghost\n\nHost ok\n  HostName 192.0.2.3\n"
    run("import", "ssh", source(tmp_path, text))
    assert json.loads(inventory(personal).read_text())["defaults"] == {}


# The guard adopts only a file the inventory now holds in full


def hand_written(home):
    path = home / "ssh" / "10-personal.conf"
    path.write_text("# hand written\nHost nas\n  HostName 192.0.2.99\n")
    return path


def test_a_conflicted_import_does_not_adopt_the_file(personal, tmp_path, capsys):
    run("import", "ssh", source(tmp_path, "Host nas\n  HostName 192.0.2.10\n"))
    target = hand_written(personal)
    assert run("import", "ssh", str(target)) == 1
    assert "not adopted" in capsys.readouterr().err
    before = target.read_bytes()
    assert run("export") == 1
    assert target.read_bytes() == before
    assert run("export", "--force") == 0
    assert target.read_bytes() != before


def test_a_refused_host_leaves_the_file_unadopted(personal, capsys):
    target = personal / "ssh" / "10-personal.conf"
    target.write_text("Host good\n  HostName 192.0.2.1\n\nHost foo=bar\n  HostName 192.0.2.2\n")
    run("import", "ssh", str(target))
    assert run("export") == 1
    assert "foo=bar" in target.read_text()


def test_a_skipped_block_leaves_the_file_unadopted(personal, capsys):
    target = personal / "ssh" / "10-personal.conf"
    target.write_text("Host *\n  ServerAliveInterval 30\n\nHost good\n  HostName 192.0.2.1\n")
    assert run("import", "ssh", str(target)) == 0
    assert "not adopted" in capsys.readouterr().err
    assert run("export") == 1
    assert "Host *" in target.read_text()


def test_a_clean_import_is_adopted_as_before(personal, capsys):
    target = personal / "ssh" / "10-personal.conf"
    target.write_text("Host good\n  HostName 192.0.2.1\n  ProxyJump j1\n  ProxyJump j2\n")
    assert run("import", "ssh", str(target)) == 0
    err = capsys.readouterr().err
    assert "second ProxyJump ignored, ssh uses the first" in err and "not adopted" not in err
    assert run("export") == 0


# Repeated keywords say what ssh actually does


@pytest.mark.parametrize("keyword, first, second", [
    ("IdentityFile", "~/.ssh/a", "~/.ssh/b"),
    ("LocalForward", "8080 localhost:80", "8443 localhost:443"),
    ("SendEnv", "LANG", "LC_ALL"),
])
def test_a_repeat_ssh_would_use_is_reported_and_unadopted(personal, tmp_path, capsys, keyword, first, second):
    path = source(tmp_path, f"Host r\n  HostName 192.0.2.5\n  {keyword} {first}\n  {keyword} {second}\n")
    assert run("import", "ssh", path) == 0
    err = capsys.readouterr().err
    assert f"second {keyword} not kept; ssh uses every {keyword}, ari stores one" in err
    assert "not adopted" in err


def test_accumulating_keywords_match_ssh():
    assert ACCUMULATING == {"identityfile", "certificatefile", "localforward", "remoteforward", "dynamicforward", "sendenv"}


@pytest.mark.skipif(shutil.which("ssh") is None, reason="needs the ssh client")
def test_ssh_agrees_about_which_keywords_accumulate(tmp_path):
    values = {
        "IdentityFile": ("~/.ssh/a", "~/.ssh/b"),
        "CertificateFile": ("~/.ssh/a.pub", "~/.ssh/b.pub"),
        "LocalForward": ("8080 localhost:80", "8443 localhost:443"),
        "RemoteForward": ("9000 localhost:22", "9001 localhost:22"),
        "DynamicForward": ("1080", "1081"),
        "SendEnv": ("LANG", "LC_ALL"),
        "ProxyJump": ("j1", "j2"),
        "ServerAliveInterval": ("10", "20"),
        "SetEnv": ("A=1", "B=2"),
    }
    for keyword, (a, b) in values.items():
        conf = tmp_path / "c.conf"
        conf.write_text(f"Host t\n  HostName 192.0.2.1\n  {keyword} {a}\n  {keyword} {b}\n")
        out = subprocess.run(["ssh", "-G", "-F", str(conf), "t"], capture_output=True, text=True, check=True).stdout
        count = sum(1 for line in out.splitlines() if line.split(" ")[0] == keyword.lower())
        assert (count == 2) == (keyword.lower() in ACCUMULATING), keyword


# A changed option is a conflict, not "unchanged"


def test_a_changed_option_conflicts_and_keeps_the_record(personal, tmp_path, capsys):
    run("import", "ssh", source(tmp_path, "Host p\n  HostName 192.0.2.6\n  ProxyJump jump\n"))
    capsys.readouterr()
    assert run("import", "ssh", source(tmp_path, "Host p\n  HostName 192.0.2.6\n  proxyjump other\n", "b.conf")) == 1
    out = capsys.readouterr()
    assert "conflict: p: proxyjump other differs from jump; not merged" in out.err
    assert "unchanged" not in out.out
    assert '"ProxyJump": "jump"' in inventory(personal).read_text()


def test_a_new_option_still_merges(personal, tmp_path, capsys):
    run("import", "ssh", source(tmp_path, "Host p\n  HostName 192.0.2.6\n  ProxyJump jump\n"))
    capsys.readouterr()
    assert run("import", "ssh", source(tmp_path, "Host p\n  HostName 192.0.2.6\n  ProxyJump jump\n  ForwardAgent yes\n", "b.conf")) == 0
    assert "1 merged (p)" in capsys.readouterr().out


def test_an_option_differing_from_the_inventory_default_conflicts(personal, tmp_path, capsys):
    run("import", "ssh", source(tmp_path, "Host p\n  HostName 192.0.2.6\n"))
    data = json.loads(inventory(personal).read_text())
    data["defaults"]["modules"] = {"ssh": {"options": {"ServerAliveInterval": "30"}}}
    inventory(personal).write_text(json.dumps(data))
    capsys.readouterr()
    assert run("import", "ssh", source(tmp_path, "Host p\n  HostName 192.0.2.6\n  ServerAliveInterval 60\n", "b.conf")) == 1
    assert "ServerAliveInterval 60 differs from 30" in capsys.readouterr().err


def test_reimporting_the_generated_file_is_unchanged(personal, tmp_path, capsys):
    run("import", "ssh", source(tmp_path, "Host p q\n  HostName 192.0.2.6\n  User tom\n  IdentityFile ~/.ssh/k\n  ProxyJump jump\n"))
    run("export")
    capsys.readouterr()
    assert run("import", "ssh", str(personal / "ssh" / "10-personal.conf")) == 0
    assert "1 unchanged (p)" in capsys.readouterr().out


# Ansible: what Ansible would lose leaves the files unadopted; comments don't


def test_ansible_vars_left_out_mean_unadopted(both, tmp_path, capsys):
    (tmp_path / "00-hosts.yml").write_text("all:\n  hosts:\n    a:\n      ansible_host: 192.0.2.1\n      ansible_become: true\n")
    assert run("-i", "work", "import", "ansible", str(tmp_path)) == 0
    err = capsys.readouterr().err
    assert "ansible_become not imported" in err and "not adopted" in err


def test_ansible_comments_alone_are_still_adopted(both, tmp_path, capsys):
    (tmp_path / "00-hosts.yml").write_text("all:\n  hosts:\n    # a comment\n    a:\n      ansible_host: 192.0.2.1\n")
    assert run("-i", "work", "import", "ansible", str(tmp_path)) == 0
    err = capsys.readouterr().err
    assert "comment not imported" in err and "not adopted" not in err
