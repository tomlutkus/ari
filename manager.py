import models
import json
import pathlib


class InventoryManager:
    def __init__(self):
        self.hosts = []
        self._load_inventory()

    def _load_inventory(self):
        pass

    def save_inventory(self):
        pass

    def list_hosts(self):
        pass

    def add_host(self):
        pass

    def remove_host(self):
        pass

    def export_to_ssh(self):
        pass

    def export_to_ansible(self):
        pass
