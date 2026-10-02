import datetime


class Host:
    def __init__(
        self,
        name: str,
        hostname: str,
        user: str,
        port: int,
        tags: list[str],
        ssh_key: str,
        notes: str,
        last_updated: str,
    ):
        self.name = name
        self.hostname = hostname
        self.user = user
        self.port = port
        self.tags = tags
        self.ssh_key = ssh_key
        self.notes = notes
        if not last_updated:
            self.last_updated = datetime.datetime.now().isoformat()
        else:
            self.last_updated = last_updated

    def __str__(self) -> str:
        lines = [
            f"name: {self.name}",
            f"hostname: {self.hostname}",
            f"user: {self.user}",
            f"port: {self.port}",
            f"tags: {self.tags}",
            f"ssh_key: {self.ssh_key}",
            f"notes: {self.notes}",
            f"last_updated: {self.last_updated}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        host_dictionary = {
            "name": self.name,
            "hostname": self.hostname,
            "user": self.user,
            "port": self.port,
            "tags": self.tags,
            "ssh_key": self.ssh_key,
            "notes": self.notes,
            "last_updated": self.last_updated,
        }
        return host_dictionary

    @classmethod
    def from_dict(cls, data: dict) -> Host:
        if not isinstance(data, dict):
            raise TypeError
        required_keys = [
            "name",
            "hostname",
            "user",
            "port",
            "tags",
            "ssh_key",
            "notes",
            "last_updated",
        ]
        for key in required_keys:
            if key not in data:
                raise KeyError

        return cls(
            data["name"],
            data["hostname"],
            data["user"],
            data["port"],
            data["tags"],
            data["ssh_key"],
            data["notes"],
            data["last_updated"],
        )
