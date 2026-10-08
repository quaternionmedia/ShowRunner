import sys

try:
    from typer import Typer
except ImportError:
    print(
        "Typer is required for the CLI. "
        "Run: `uv sync --extra cli` or `pip install showrunner[cli]`",
        file=sys.stderr,
    )
    sys.exit(1)

from showrunner.migrations import current, upgrade, downgrade, history

migration = Typer(help="Database migration commands")

migration.command()(current)
migration.command()(upgrade)
migration.command()(downgrade)
migration.command(name="list")(history)


if __name__ == "__main__":
    migration()
