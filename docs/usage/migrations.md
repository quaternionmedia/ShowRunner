# Database migrations

ShowRunner uses [Alembic](https://alembic.sqlalchemy.org/) to evolve the SQLite schema.
Migration scripts live in `src/showrunner/migrations/versions/`.

## Startup behaviour

`ShowDatabase.create_schema()` checks the database's Alembic revision:

| Database state              | Result                                                        |
| --------------------------- | ------------------------------------------------------------- |
| No revision, no tables      | Tables are created from the models and stamped at `head`      |
| Revision == `head`          | Nothing to do                                                 |
| Pre-Alembic DB or behind `head` | `RuntimeError` — run the upgrade command below, then restart  |

## Commands

```
sr migration current     # show the database's current revision
sr migration list        # show migration history
sr migration upgrade     # apply all pending migrations (default: head)
sr migration downgrade <revision>
```

The database is the same one the app uses: `[database] path` from `show.toml`
(current directory, then `~/.config/showrunner/`), falling back to `show.db`. Override with `alembic -x db_url=sqlite:///other.db ...`.

## Writing a migration

```
alembic -c src/showrunner/migrations/alembic.ini revision --autogenerate -m "describe change"
```

Review the generated script, then commit it. SQLite needs `op.batch_alter_table`
for column changes (already enabled via `render_as_batch` in `env.py`).

## Changelog

- `Cue.number` is now an optional string (e.g. `"q42"`), previously a required integer.
