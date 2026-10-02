"""ari: one record per SSH host, exported to ssh config and Ansible inventory."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ari")
except PackageNotFoundError:
    __version__ = "0.0.0"
