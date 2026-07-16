import datetime

from models import Host
import json
import pathlib
import os


class InventoryManager:
    def __init__(self):
        self.hosts = []
        self._load_inventory()

    def _load_inventory(self) -> bool:
        location = pathlib.Path("./inventory.json")
        try:
            with open(location, "r") as file:
                hosts_dictionary = json.load(file)
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
        location = pathlib.Path("./inventory.json")
        data = {
            "hosts": [host.to_dict() for host in self.hosts],
            "last_updated": datetime.datetime.now().isoformat(),
        }
        with open(location, "w") as file:
            json.dump(data, file, indent=2)

    def list_hosts(self):
        hosts_text = [str(host) for host in self.hosts]
        return "\n===============================================\n".join(hosts_text)

    def add_host(self) -> bool:
        default_user = os.getenv("USER") or os.getenv("USERNAME") or "user"
        print("\n=== Add New Host ===")
        print(
            "Enter the required fields. Press ENTER to use defaults where available.\n"
        )
        name = input("Host alias (name) [required]: ").strip()
        hostname = input("IP or hostname [required]: ").strip()
        user_str = input("SSH username [default: current user]: ").strip() or None
        user = user_str if user_str else default_user
        port_str = input("SSH port [default: 22]: ").strip()
        port = int(port_str) if port_str else 22
        ssh_key = input("Path to SSH key [optional]: ").strip() or None
        tags_str = input("Tags (comma-separated) [optional]: ").strip()
        tags = [t.strip() for t in tags_str.split(",")] if tags_str else []
        notes = input("Notes [optional]: ").strip() or ""

        host_dict = {
            "name": name,
            "hostname": hostname,
            "user": user,
            "port": port,
            "ssh_key": ssh_key,
            "tags": tags,
            "notes": notes,
            "last_updated": datetime.datetime.now().isoformat(),
        }
        new_host = Host.from_dict(host_dict)
        self.hosts.append(new_host)
        return True

    def remove_host(self):
        pass

    def export_to_ssh(self):
        pass

    def export_to_ansible(self):
        pass


# inv_manager = InventoryManager()
# text = inv_manager.list_hosts()
# print(text)
