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
    ("LocalForward", "8080 localhost:80", "8443 localhost:443"),
    ("RemoteForward", "9000 localhost:22", "9001 localhost:22"),
    ("DynamicForward", "1080", "1081"),
    ("SendEnv", "LANG", "LC_ALL"),
    ("CertificateFile", "~/.ssh/a-cert.pub", "~/.ssh/b-cert.pub"),
])
def test_every_line_ssh_would_use_is_kept_and_the_file_adopted(personal, capsys, keyword, first, second):
    target = personal / "ssh" / "10-personal.conf"
    target.write_text(f"Host r\n  HostName 192.0.2.5\n  {keyword} {first}\n  {keyword} {second}\n")
    assert run("import", "ssh", str(target)) == 0
    err = capsys.readouterr().err
    assert "not kept" not in err and "not adopted" not in err
    assert host(personal, "r")["modules"]["ssh"]["options"] == {keyword: [first, second]}
    assert run("export") == 0


def test_every_identity_file_is_a_key_in_order(personal, tmp_path, capsys):
    text = "Host r\n  HostName 192.0.2.5\n  IdentityFile ~/.ssh/b\n  IdentityFile ~/.ssh/a\n  IdentityFile ~/.ssh/b\n"
    assert run("import", "ssh", source(tmp_path, text)) == 0
    assert "IdentityFile ~/.ssh/b given twice; kept once" in capsys.readouterr().err
    assert host(personal, "r")["keys"] == ["b", "a"]


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


# What a block leaves out is not set: a later block or a re-import fills it, never conflicts with it


def host(home, name, inv="personal"):
    return next(h for h in json.loads(inventory(home, inv).read_text())["hosts"] if h["name"] == name)


def test_a_later_block_fills_what_the_first_left_unset(personal, tmp_path, capsys):
    text = "Host db\n  HostName 192.0.2.10\n\nHost db\n  User admin\n  IdentityFile ~/.ssh/k\n  ProxyJump j\n"
    assert run("import", "ssh", source(tmp_path, text)) == 0
    assert "conflict" not in capsys.readouterr().err
    db = host(personal, "db")
    assert (db["hostname"], db["user"], db["keys"]) == ("192.0.2.10", "admin", ["k"])
    assert db["modules"]["ssh"]["options"]["ProxyJump"] == "j"


def test_a_block_on_reimport_adds_without_hostname_or_port(personal, tmp_path, capsys):
    run("import", "ssh", source(tmp_path, "Host db\n  HostName 192.0.2.10\n  Port 2200\n"))
    capsys.readouterr()
    assert run("import", "ssh", source(tmp_path, "Host db\n  User admin\n", "b.conf")) == 0
    assert "1 merged (db)" in capsys.readouterr().out
    assert (host(personal, "db")["port"], host(personal, "db")["user"]) == (2200, "admin")


def test_a_hostname_set_differently_still_conflicts(personal, tmp_path, capsys):
    text = "Host db\n  HostName 192.0.2.10\n\nHost db\n  HostName 192.0.2.11\n  User admin\n"
    assert run("import", "ssh", source(tmp_path, text)) == 1
    assert "db: HostName 192.0.2.11 differs from 192.0.2.10; not merged" in capsys.readouterr().err
    assert "user" not in host(personal, "db")


def test_a_new_host_without_port_is_22_whatever_the_default(personal, tmp_path):
    inventory(personal).write_text(json.dumps({"version": 2, "defaults": {"port": 2222}}))
    run("import", "ssh", source(tmp_path, "Host a\n  HostName 192.0.2.1\n\nHost b\n  HostName 192.0.2.2\n  Port 2222\n"))
    assert host(personal, "a")["port"] == 22
    assert "port" not in host(personal, "b")


BLOCKS = """\
Host web web.example.com
    User deploy

Host db
    HostName 192.0.2.10

Host db db-old
    User admin
    IdentityFile ~/.ssh/db-ed25519
    ProxyJump web

Host nas storage 192.0.2.254
    HostName 192.0.2.254
    Port 2222

Host box box.example.org 198.51.100.7
    User ops
    Port 2200
    IdentityFile ~/.ssh/box
"""


@pytest.mark.skipif(shutil.which("ssh") is None, reason="needs the ssh client")
def test_blocks_ssh_reads_together_resolve_identically(personal, tmp_path):
    """Every token answers ssh -G the same from the source and from the export. One host is
    there already, so the import doesn't infer defaults from these blocks."""
    inventory(personal).write_text(json.dumps({"version": 2, "hosts": [{"name": "seed", "hostname": "192.0.2.99"}]}))
    path = source(tmp_path, BLOCKS)
    assert run("import", "ssh", path) == 0
    assert run("export") == 0
    exported = personal / "ssh" / "10-personal.conf"
    tokens = [t for line in BLOCKS.splitlines() if line.startswith("Host ") for t in line.split()[1:]]

    def resolve(config, token):
        return subprocess.run(["ssh", "-G", "-F", str(config), token], capture_output=True, text=True, check=True).stdout

    for token in tokens:
        assert resolve(path, token) == resolve(exported, token), token


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


# A later block for the same name adds what ssh accumulates; one named by an alias is compared


def test_a_later_block_adds_its_keys_and_forwards(personal, tmp_path, capsys):
    text = (
        "Host db\n  HostName 192.0.2.10\n  IdentityFile ~/.ssh/a\n  LocalForward 5432 localhost:5432\n\n"
        "Host db\n  IdentityFile ~/.ssh/b\n  localforward 8080 localhost:80\n  User admin\n"
    )
    assert run("import", "ssh", source(tmp_path, text)) == 0
    assert "conflict" not in capsys.readouterr().err
    db = host(personal, "db")
    assert (db["keys"], db["user"]) == (["a", "b"], "admin")
    assert db["modules"]["ssh"]["options"] == {
        "LocalForward": ["5432 localhost:5432", "8080 localhost:80"],
        "IdentitiesOnly": "no",
    }


@pytest.mark.parametrize("first, later, stored", [
    ("  IdentitiesOnly yes\n", "", None),  # the first block's yes wins over the later block's silence
    ("", "  IdentitiesOnly yes\n", None),  # the first to state it is the later block
    ("  IdentitiesOnly no\n", "  IdentitiesOnly yes\n", "no"),
    ("", "", "no"),
])
def test_identities_only_is_settled_across_blocks(personal, tmp_path, first, later, stored):
    text = f"Host db\n  HostName 192.0.2.10\n  IdentityFile ~/.ssh/a\n{first}\nHost db\n  IdentityFile ~/.ssh/b\n{later}"
    assert run("import", "ssh", source(tmp_path, text)) == 0
    options = host(personal, "db").get("modules", {}).get("ssh", {}).get("options", {})
    assert options.get("IdentitiesOnly") == stored


def test_a_later_block_by_alias_with_other_keys_conflicts(personal, tmp_path, capsys):
    text = "Host db dbx\n  HostName 192.0.2.10\n  IdentityFile ~/.ssh/a\n\nHost dbx\n  IdentityFile ~/.ssh/b\n"
    assert run("import", "ssh", source(tmp_path, text)) == 1
    assert "dbx: IdentityFile ~/.ssh/b differs from ~/.ssh/a; not merged" in capsys.readouterr().err
    assert host(personal, "db")["keys"] == ["a"]


def test_a_forward_list_compares_whole_on_reimport(personal, tmp_path, capsys):
    text = "Host p\n  HostName 192.0.2.6\n  LocalForward 1 localhost:1\n  LocalForward 2 localhost:2\n"
    run("import", "ssh", source(tmp_path, text))
    capsys.readouterr()
    assert run("import", "ssh", source(tmp_path, text, "same.conf")) == 0
    assert "1 unchanged (p)" in capsys.readouterr().out
    fewer = "Host p\n  HostName 192.0.2.6\n  LocalForward 1 localhost:1\n"
    assert run("import", "ssh", source(tmp_path, fewer, "b.conf")) == 1
    assert "LocalForward 1 localhost:1 differs from 1 localhost:1, 2 localhost:2; not merged" in capsys.readouterr().err


@pytest.mark.skipif(shutil.which("ssh") is None, reason="needs the ssh client")
def test_ssh_sees_the_export_as_it_saw_the_source(personal, capsys):
    """Every accumulating keyword repeated, and again in a later block naming every token of the
    host: ssh -G for each alias of the export matches ssh -G of the source."""
    target = personal / "ssh" / "10-personal.conf"
    target.write_text(
        "Host app app.lab\n  HostName 192.0.2.20\n  User deploy\n"
        "  IdentityFile ~/.ssh/app-ed25519\n  IdentityFile \"~/.ssh/old key\"\n"
        "  CertificateFile ~/.ssh/app-ed25519-cert.pub\n  CertificateFile ~/.ssh/ca/old-cert.pub\n"
        "  LocalForward 5432 localhost:5432\n  LocalForward 8080 localhost:80\n"
        "  RemoteForward 9000 localhost:22\n  RemoteForward 9001 localhost:22\n"
        "  DynamicForward 1080\n  DynamicForward 1081\n"
        "  SendEnv LANG\n  SendEnv LC_*\n  IdentitiesOnly yes\n\n"
        "Host app.lab app\n  IdentityFile ~/.ssh/third\n  LocalForward 9090 localhost:90\n  SendEnv TZ\n  ProxyJump bastion\n\n"
        "Host bastion\n  HostName 198.51.100.1\n  Port 2222\n  IdentityFile ~/.ssh/bastion\n"
    )
    aliases = ["app", "app.lab", "bastion"]

    def resolved(conf):
        return {a: subprocess.run(["ssh", "-G", "-F", str(conf), a], capture_output=True, text=True, check=True).stdout for a in aliases}

    before = resolved(target)
    assert run("import", "ssh", str(target)) == 0
    assert "not adopted" not in capsys.readouterr().err
    assert run("export") == 0
    assert "wrote" in capsys.readouterr().out
    assert resolved(target) == before


def test_a_later_block_naming_some_tokens_is_compared_not_added(personal, tmp_path, capsys):
    """Host app alone doesn't apply when app.lab is typed, so its key can't join the record."""
    text = "Host app app.lab\n  HostName 192.0.2.20\n  IdentityFile ~/.ssh/a\n\nHost app\n  IdentityFile ~/.ssh/b\n"
    assert run("import", "ssh", source(tmp_path, text)) == 1
    assert "app: IdentityFile ~/.ssh/b differs from ~/.ssh/a; not merged" in capsys.readouterr().err
    assert host(personal, "app")["keys"] == ["a"]


def test_a_later_block_in_another_case_is_compared_not_added(personal, tmp_path, capsys):
    """ssh matches Host tokens as typed: Host DB doesn't apply when db is typed."""
    text = "Host db\n  HostName 192.0.2.10\n  IdentityFile ~/.ssh/a\n\nHost DB\n  IdentityFile ~/.ssh/b\n"
    assert run("import", "ssh", source(tmp_path, text)) == 1
    assert "DB: ssh and Ansible tell it apart from db, the name ari holds, and ari can't keep both spellings" in capsys.readouterr().err


# A Host line that leaves out a name the host answers to can't change the whole record


@pytest.mark.parametrize("later, left_out", [
    ("Host app\n  ProxyJump bastion\n", "app.lab"),  # one token of two
    ("Host app.lab\n  ForwardAgent yes\n", "app"),  # by alias alone
    ("Host APP app.lab\n  ForwardAgent yes\n", "app"),  # ssh matches as typed: APP isn't app
])
def test_a_partial_host_line_is_refused_in_the_same_file(personal, capsys, later, left_out):
    target = personal / "ssh" / "10-personal.conf"
    target.write_text(f"Host app app.lab\n  HostName 192.0.2.20\n\n{later}")
    assert run("import", "ssh", str(target)) == 1
    err = capsys.readouterr().err
    assert f"its Host line leaves out {left_out}, so ssh applies it to the rest alone; not merged" in err
    assert "not adopted" in err
    assert "modules" not in host(personal, "app")


def test_a_partial_host_line_is_refused_on_a_later_import(personal, tmp_path, capsys):
    run("import", "ssh", source(tmp_path, "Host app app.lab\n  HostName 192.0.2.20\n"))
    capsys.readouterr()
    assert run("import", "ssh", source(tmp_path, "Host app.lab\n  ForwardAgent yes\n", "b.conf")) == 1
    assert "app.lab: its Host line leaves out app, so ssh applies it to the rest alone; not merged" in capsys.readouterr().err
    assert "modules" not in host(personal, "app")


def test_a_partial_host_line_that_changes_nothing_is_unchanged(personal, tmp_path, capsys):
    run("import", "ssh", source(tmp_path, "Host app app.lab\n  HostName 192.0.2.20\n  User admin\n"))
    capsys.readouterr()
    assert run("import", "ssh", source(tmp_path, "Host app\n  HostName 192.0.2.20\n  User admin\n", "b.conf")) == 0
    assert "1 unchanged (app)" in capsys.readouterr().out


def test_a_full_host_line_still_fills_what_was_unset(personal, capsys):
    target = personal / "ssh" / "10-personal.conf"
    target.write_text("Host app app.lab\n  HostName 192.0.2.20\n\nHost app.lab app\n  ForwardAgent yes\n  ProxyJump bastion\n")
    assert run("import", "ssh", str(target)) == 0
    assert "not adopted" not in capsys.readouterr().err
    assert host(personal, "app")["modules"]["ssh"]["options"] == {"ForwardAgent": "yes", "ProxyJump": "bastion"}
