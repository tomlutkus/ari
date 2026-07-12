"""
Hosts Inventory Manager - Keep your hosts tidy!
Author: Thomas Lutkus <thomas@lutkus.net>
"""


def main():
    while True:
        print("\033c", end="")
        print("Hosts Inventory Manager v0.1")
        print("1. List all hosts")
        print("2. Add a new host")
        print("3. Remove a host")
        print("4. Export to SSH config")
        print("5. Export to Ansible Inventory")
        print("6. Quit")
        user_choice = input("\nType your choice and press <ENTER>:\n> ")
        if user_choice == "6":
            break
        elif user_choice == "1":
            pass
        elif user_choice == "2":
            pass
        elif user_choice == "3":
            pass
        elif user_choice == "4":
            pass
        elif user_choice == "5":
            pass
        else:
            input("Your choice was not valid! Press <ENTER> to try again.")
            continue




# Hosts Inventory Manager
# 1. List all hosts
# 2. Add a new host
# 3. Remove a host
# 4. Export to SSH config
# 5. Export to Ansible Inventory
# 6. Quit


if __name__ == "__main__":
    main()
