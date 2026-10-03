import pytest

from conftest import write_config
from ari.config import load_config
from ari.errors import HostsError


def test_module_tables_parse_and_enabled_defaults_to_true(home):
    write_config(home, '[inventories.personal.ssh]\npath = "SSH/10-personal.conf"\n')
    cfg = load_config()
    ssh = cfg.get("personal").modules["ssh"]
    assert ssh.enabled and ssh.settings.path == home / "ssh" / "10-personal.conf"


def test_disabled_module_is_kept_but_not_enabled(home):
    write_config(home, '[inventories.personal.ssh]\npath = "SSH/x.conf"\nenabled = false\n')
    ic = load_config().get("personal")
    assert "ssh" in ic.modules and ic.enabled() == []


def test_parked_table_for_a_module_that_isnt_installed_is_ignored(home):
    write_config(home, '[inventories.work.netbox]\nurl = "https://netbox.example"\nenabled = false\n')
    assert load_config().get("work").modules == {}


@pytest.mark.parametrize(
    "text, message",
    [
        ('[inventories.personal]\nssh = "~/.ssh/config.d/10-personal.conf"\n', 'path = "~/.ssh/config.d/10-personal.conf"'),
        ('[inventories.work.netbox]\nurl = "x"\n', "unknown module 'netbox'"),
        ('[inventories.personal.ssh]\npath = "relative.conf"\n', "absolute"),
        ("[inventories.personal.ssh]\n", "needs path"),
        ('[inventories.personal.ssh]\npath = "~/x.conf"\nport = 22\n', "unknown keys"),
        ('[inventories.personal.ssh]\npath = "~/x.conf"\nenabled = "yes"\n', "true or false"),
        ('[inventories.personal]\nfoo = 1\n', "unknown key 'foo'"),
    ],
)
def test_bad_configs_explain_themselves(home, text, message):
    write_config(home, text)
    with pytest.raises(HostsError, match=message.replace("(", r"\(").replace("'", "'")):
        load_config()
