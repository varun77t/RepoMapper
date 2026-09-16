from config import get_setting


def slugify(text):
    return text.lower().replace(" ", "-")


def _private_helper(x):
    return x


def describe(text):
    return slugify(text) + str(get_setting("DEBUG"))
