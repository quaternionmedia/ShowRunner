"""Database manager for ShowRunner.

Provides engine/session lifecycle management and convenience helpers
for the SQLite backend. Plugins and application code should use
``ShowDatabase`` rather than creating engines directly.
"""

from pathlib import Path

from sqlalchemy import inspect
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from alembic.runtime.migration import MigrationContext
from sqlmodel import Session, SQLModel, create_engine, select
from loguru import logger

from .models import Show

_ALEMBIC_INI = Path(__file__).parent / 'migrations' / 'alembic.ini'


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

    def alembic_config(self) -> AlembicConfig:
        """Alembic config pointed at this database."""
        cfg = AlembicConfig(str(_ALEMBIC_INI))
        cfg.attributes['db_url'] = f'sqlite:///{self.db_path}'
        return cfg

    def create_schema(self) -> None:
        """Bring the database schema up to date.

        - Empty database: creates all tables via SQLModel and stamps the
          Alembic revision at ``head``.
        - Otherwise (including pre-Alembic databases, which have no revision):
          raises ``RuntimeError`` unless already at ``head``; run
          ``sr migration upgrade`` to apply pending migrations.
        """
        cfg = self.alembic_config()
        head = ScriptDirectory.from_config(cfg).get_current_head()

        with self.engine.connect() as conn:
            current_rev = MigrationContext.configure(conn).get_current_revision()
            has_tables = bool(inspect(conn).get_table_names())

        if current_rev is None and not has_tables:
            SQLModel.metadata.create_all(self.engine)
            alembic_command.stamp(cfg, 'head')
            logger.info("Created new database schema and stamped head revision.")
            return

        if current_rev != head:
            msg = (
                f"Database schema is at revision '{current_rev or 'none (pre-migration)'}', "
                f"but the latest is '{head}'. Run 'sr migration upgrade' to apply "
                "pending migrations."
            )
            logger.error(msg)
            raise RuntimeError(msg)

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
