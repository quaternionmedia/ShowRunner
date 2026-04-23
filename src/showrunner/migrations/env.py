from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from alembic import context

# ---------------------------------------------------------------------------
# Alembic Config object
# ---------------------------------------------------------------------------
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Import all models so SQLModel.metadata is fully populated
# ---------------------------------------------------------------------------
import showrunner.models  # noqa: F401  side-effect: registers table metadata

target_metadata = SQLModel.metadata

# ---------------------------------------------------------------------------
# Resolve database URL from show.toml if available, otherwise use alembic.ini
# ---------------------------------------------------------------------------


def _get_url() -> str:
    """Return SQLite URL, preferring show.toml over alembic.ini."""
    # Allow CLI override via -x db_url=sqlite:///foo.db
    x_args = context.get_x_argument(as_dictionary=True)
    if 'db_url' in x_args:
        return x_args['db_url']

    # Try to load show.toml from the directory Alembic was invoked from
    toml_path = Path.cwd() / 'show.toml'
    if toml_path.exists():
        import tomllib

        with open(toml_path, 'rb') as f:
            data = tomllib.load(f)
        db_path = data.get('database', {}).get('path', 'show.db')
        return f'sqlite:///{db_path}'

    return config.get_main_option('sqlalchemy.url', 'sqlite:///show.db')


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (emits SQL to stdout)."""
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
        render_as_batch=True,  # required for SQLite ALTER TABLE emulation
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    cfg = config.get_section(config.config_ini_section, {})
    cfg['sqlalchemy.url'] = _get_url()

    connectable = engine_from_config(
        cfg,
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # required for SQLite ALTER TABLE emulation
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
