# Source - https://stackoverflow.com/a/74875605
# Posted by Jérôme
# Retrieved 2026-04-22, License - CC BY-SA 4.0

"""DB migrations — plain Python API (no CLI dependency).

Import and call these directly without typer::

    from showrunner.migrations import upgrade
    upgrade()  # apply all pending migrations
"""
from pathlib import Path

from alembic.config import Config
from alembic import command

ROOT_PATH = Path(__file__).parent
ALEMBIC_CFG = Config(ROOT_PATH / "alembic.ini")


def current(verbose: bool = False) -> None:
    """Print the current migration revision."""
    command.current(ALEMBIC_CFG, verbose=verbose)


def upgrade(revision: str = "head") -> None:
    """Upgrade to *revision* (default: head)."""
    command.upgrade(ALEMBIC_CFG, revision)


def downgrade(revision: str) -> None:
    """Downgrade to *revision*."""
    command.downgrade(ALEMBIC_CFG, revision)


def history() -> None:
    """Print the full migration history."""
    command.history(ALEMBIC_CFG)
