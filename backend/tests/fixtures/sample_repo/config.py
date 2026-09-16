"""Leaf module: imported by almost everything, imports nothing local.

Expected to rank #1 by PageRank -- it is the most depended-upon file.
"""

DEBUG = False
DB_URL = "sqlite://"


def get_setting(name):
    return {"DEBUG": DEBUG, "DB_URL": DB_URL}.get(name)


class Settings:
    def __init__(self):
        self.debug = get_setting("DEBUG")

    def as_dict(self):
        return {"debug": self.debug}
