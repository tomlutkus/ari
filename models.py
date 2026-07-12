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
        self.last_updated = last_updated

    def __str__(self) -> str:
        return self.name

    def to_dict(self) -> dict:
        host_dictionary = {}
        return host_dictionary

    @classmethod
    def from_dict(cls, data):
        pass
