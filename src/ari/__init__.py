"""ari: one record per SSH host, exported to ssh config, Ansible inventory and host tables."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ari")
except PackageNotFoundError:
    __version__ = "0.0.0"
