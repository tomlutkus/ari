"""
Hosts Inventory Manager - Keep your hosts tidy!
Author: Thomas Lutkus <thomas@lutkus.net>
"""

from manager import InventoryManager


def main():
    my_inventory = InventoryManager()
    while True:
        print("\033c", end="")
        print("Hosts Inventory Manager v0.1")
        print("1. List all hosts")
        print("2. Add a new host")
        print("3. Remove a host")
        print("4. Export to SSH config")
        print("5. Export to Ansible Inventory")
        print("6. Save to JSON file")
        print("7. Quit")
        user_choice = input("\nType your choice and press <ENTER>:\n> ")
        if user_choice == "7":
            break
        elif user_choice == "1":
            print("Current inventory entries:\n")
            print(my_inventory.list_hosts())
            input("\nPress ENTER to continue...")
        elif user_choice == "2":
            my_inventory.add_host()
            input("\nPress ENTER to continue...")
        elif user_choice == "3":
            my_inventory.remove_host()
            input("\nPress ENTER to continue...")
        elif user_choice == "4":
            pass
        elif user_choice == "5":
            pass
        elif user_choice == "6":
            my_inventory.save_inventory()
            print("Saved inventory to file inventory.json")
            input("\nPress ENTER to continue...")
        else:
            input("Your choice was not valid! Press <ENTER> to try again.")
            continue


if __name__ == "__main__":
    main()
