from models import Host
import json
import pathlib


class InventoryManager:
    def __init__(self):
        self.hosts = []
        self._load_inventory()

    def _load_inventory(self):
        location = pathlib.Path("./inventory.json")
        try:
            with open(location, "r") as inventory_file:
                hosts_dictionary = json.load(inventory_file)
                hosts_list = hosts_dictionary["hosts"]
                for host_dictionary in hosts_list:
                    host = Host.from_dict(host_dictionary)
                    self.hosts.append(host)
            return True
        except FileNotFoundError:
            return False
        except json.JSONDecodeError:
            return False

    def save_inventory(self):
        pass

    def list_hosts(self):
        hosts_copy = self.hosts.copy()
        hosts_text = []
        for host in hosts_copy:
            hosts_text.append(str(host))
        
        return "\n".join(hosts_text)

    def add_host(self):
        pass

    def remove_host(self):
        pass

    def export_to_ssh(self):
        pass

    def export_to_ansible(self):
        pass


inv_manager = InventoryManager()
text = inv_manager.list_hosts()
print(text)
