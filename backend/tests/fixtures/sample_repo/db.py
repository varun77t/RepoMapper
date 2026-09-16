from config import DB_URL, Settings


class Connection:
    def __init__(self, url=DB_URL):
        self.url = url

    def execute(self, sql):
        return self.parse(sql)

    def parse(self, sql):
        return sql.strip()


def connect():
    settings = Settings()
    return Connection(settings.as_dict().get("DB_URL", DB_URL))
