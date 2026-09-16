import cycle_a
import db
import models


def create_app():
    conn = db.connect()
    user, _ = models.make_user("ada")
    cycle_a.alpha()
    return conn, user
