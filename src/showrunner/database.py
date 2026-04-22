"""Database manager for ShowRunner.

Provides engine/session lifecycle management and convenience helpers
for the SQLite backend. Plugins and application code should use
``ShowDatabase`` rather than creating engines directly.
"""

from pathlib import Path

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from alembic.runtime.migration import MigrationContext
from sqlmodel import Session, SQLModel, create_engine, select
from loguru import logger

from .models import Show

# Resolve alembic.ini relative to this file: src/showrunner/ → src/ → project root
_ALEMBIC_INI = Path(__file__).parent.parent.parent / 'alembic.ini'


class ShowDatabase:
    """Manages the SQLite engine and provides session access.

    Usage::

        db = ShowDatabase('show.db')
        with db.session() as s:
            s.add(Show(name='My Show'))
            s.commit()
        db.close()
    """

    def __init__(self, db_path: str | Path = 'show.db', echo: bool = False) -> None:
        logger.trace(f"Initializing ShowDatabase with path: {db_path}")
        self.db_path = Path(db_path)
        self.engine = create_engine(
            f'sqlite:///{self.db_path}',
            echo=echo,
        )
        logger.debug(f"Created SQLite engine for {self.db_path}")

    def create_schema(self) -> None:
        """Bring the database schema up to date.

        - Fresh / untracked databases: creates all tables via SQLModel then
          stamps the Alembic revision at ``head`` (no migrations needed since
          the tables already match the current models).
        - Tracked databases: Raises an error and alerts the user to run
          ``alembic upgrade head`` to apply any pending migrations in order.
        """
        logger.trace("Checking database schema version...")
        cfg = AlembicConfig(str(_ALEMBIC_INI))
        cfg.set_main_option('sqlalchemy.url', f'sqlite:///{self.db_path}')
        script = ScriptDirectory.from_config(cfg)
        current_head = script.get_current_head()

        with self.engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            current_rev = ctx.get_current_revision()
            print(f"Current database revision: {current_rev}")

        if current_rev is None:
            # Brand-new or pre-Alembic database: create tables and stamp head.
            logger.trace(
                "No existing schema detected. Creating new schema and stamping head revision."
            )
            SQLModel.metadata.create_all(self.engine)
            alembic_command.stamp(cfg, 'head')
            logger.info("Created new database schema and stamped head revision.")
            return

        if current_rev != current_head:
            logger.error(
                f"Database schema is at revision '{current_rev}', but the latest "
                f"is '{current_head}'. Pending migrations must be applied."
            )
            raise RuntimeError(
                f"Database schema is at revision '{current_rev}', but the latest "
                f"is '{current_head}'. Please run 'alembic upgrade head' to apply pending "
                "migrations."
            )
        # Schema is already up to date, nothing to do.
        return

    def session(self) -> Session:
        """Return a new SQLModel ``Session`` bound to the engine."""
        return Session(self.engine)

    def close(self) -> None:
        """Dispose of the engine and release connections."""
        self.engine.dispose()

    # -- Convenience helpers --------------------------------------------------

    def get_show(self, show_id: int) -> Show | None:
        """Fetch a single show by id."""
        with self.session() as s:
            return s.get(Show, show_id)

    def list_shows(self) -> list[Show]:
        """Return all shows ordered by name."""
        with self.session() as s:
            return list(s.exec(select(Show).order_by(Show.name)))
