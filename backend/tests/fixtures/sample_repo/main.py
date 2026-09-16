"""Entry point."""
import app
import utils


def main():
    application = app.create_app()
    print(utils.describe("hello world"))
    return application


if __name__ == "__main__":
    main()
