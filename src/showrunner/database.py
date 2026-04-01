"""Database manager for ShowRunner.

Provides engine/session lifecycle management and convenience helpers
for the SQLite backend. Plugins and application code should use
``ShowDatabase`` rather than creating engines directly.
"""

from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine, select

from .models import Show


class ShowDatabase:
    """Manages the SQLite engine and provides session access.

    Usage::

        db = ShowDatabase('show.db')
        with db.session() as s:
            s.add(Show(name='My Show'))
            s.commit()
        db.close()
    """

    def __init__(self, db_path: str | Path = 'show.db') -> None:
        self.db_path = Path(db_path)
        self.engine = create_engine(
            f'sqlite:///{self.db_path}',
            echo=False,
        )

    def create_schema(self) -> None:
        """Create all tables that don't already exist, then migrate columns."""
        SQLModel.metadata.create_all(self.engine)
        self._migrate_columns()

    def _migrate_columns(self) -> None:
        """Add any missing columns to existing tables."""
        inspector = inspect(self.engine)
        for table in SQLModel.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {col['name'] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name not in existing:
                    col_type = column.type.compile(self.engine.dialect)
                    with self.engine.begin() as conn:
                        conn.execute(
                            text(
                                f'ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}'
                            )
                        )

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
