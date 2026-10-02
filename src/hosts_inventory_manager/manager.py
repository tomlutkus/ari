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

    def _find_host(self, text: str) -> list:
        search = text.lower()
        found_hosts = []
        seen_hosts = set()
        for host in self.hosts:
            for value in vars(host).values():
                if (
                    isinstance(value, str)
                    and search in value.lower()
                    and host.name not in seen_hosts
                ):
                    found_hosts.append(host)
                    seen_hosts.add(host.name)
        return found_hosts

    def save_inventory(self):
        location = pathlib.Path("./inventory.json")
        data = {
            "hosts": [host.to_dict() for host in self.hosts],
            "last_updated": datetime.datetime.now().isoformat(),
        }
        with open(location, "w") as file:
            json.dump(data, file, indent=2)

    def list_hosts(self):
        if not self.hosts:
            return "No hosts in inventory."
        header = f"{'NAME':<16}{'HOSTNAME':<18}{'USER':<12}{'PORT':<6}{'TAGS'}"
        rows = [header, "-" * len(header)]
        for host in self.hosts:
            tags = ", ".join(host.tags) if host.tags else "-"
            rows.append(
                f"{host.name:<16}{host.hostname:<18}{host.user:<12}{host.port:<6}{tags}"
            )
        return "\n".join(rows)

    def add_host(self) -> bool:
        print("\n=== Add New Host ===")
        print(
            "Enter the required fields. Press ENTER to use defaults where available.\n"
        )
        is_editing = True
        while is_editing:
            name = input("Host alias (name) [required]: ").strip()
            hostname = input("IP or hostname [required]: ").strip()
            user_str = input("SSH username [default: current user]: ").strip() or None
            default_user = os.getenv("USER") or os.getenv("USERNAME") or "user"
            user = user_str if user_str else default_user
            port_str = input("SSH port [default: 22]: ").strip()
            try:
                port = int(port_str) if port_str else 22
            except ValueError:
                print("Invalid port value. Using default port 22.")
                port = 22
            ssh_key = input("Path to SSH key [optional]: ").strip() or None
            tags_str = input("Tags (comma-separated) [optional]: ").strip()
            tags = [t.strip() for t in tags_str.split(",")] if tags_str else []
            notes = input("Notes [optional]: ").strip() or ""

            print("\nYou entered the following:")
            print(f"  Name       : {name}")
            print(f"  Hostname   : {hostname}")
            print(f"  User       : {user}")
            print(f"  Port       : {port}")
            print(f"  SSH Key    : {ssh_key or 'None'}")
            print(f"  Tags       : {', '.join(tags) if tags else 'None'}")
            print(f"  Notes      : {notes or 'None'}")

            proceed = input("\nProceed with these values? (y/n): ").strip().lower()

            if proceed.lower() == "y":
                break
            else:
                input("Try again. Press ENTER.")
                continue
        try:
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
        except Exception as e:
            print(f"\n Failed to add host: {e}")
            return False

        return True

    def remove_host(self):
        print("\n=== Remove Host ===")
        search_text = input("Type a search to find the host you'd like to remove: ")
        host_list = self._find_host(search_text)
        print("\nThe following hosts match your search:\n")
        for i, host in enumerate(host_list, 1):
            print(f"{i}. {host.name}")
        selection = int(input("Type the number of the host you'd like to remove: "))
        name = host_list[selection - 1].name

        new_hosts = []
        for host in self.hosts:
            if host.name != name:
                new_hosts.append(host)
        self.hosts = new_hosts
        return True

    def export_to_ssh(self):
        pass

    def export_to_ansible(self):
        pass
