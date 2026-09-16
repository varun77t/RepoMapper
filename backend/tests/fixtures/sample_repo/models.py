from config import Settings


class Base:
    def save(self):
        return True


class User(Base):
    def __init__(self, name):
        self.name = name

    def display(self):
        return self.name.upper()


def make_user(name):
    settings = Settings()
    user = User(name)
    user.save()
    return user, settings
