"""Nothing imports this module and it is not an entry point.

Expected to be the only file-level orphan in the fixture.
"""
from config import get_setting


def unused_function():
    return get_setting("DEBUG")
