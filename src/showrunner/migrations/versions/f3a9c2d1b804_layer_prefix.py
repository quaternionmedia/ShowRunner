"""layers: per-layer prefix, backfilled to the prefixes QLab sync already uses

Revision ID: f3a9c2d1b804
Revises: e7a1c4b9d280
Create Date: 2026-10-26 10:00:00

"""

import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a9c2d1b804"
down_revision: Union[str, Sequence[str], None] = "e7a1c4b9d280"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen copy of the prefixes QLab used for these layer types before this revision.
LEGACY = {"lights": "", "sound": "s", "video": "v", "audio": "a"}


def _letters(name: str | None) -> str:
    return re.sub(r"[^A-Za-z]", "", name or "")


def upgrade() -> None:
    """Add ``layers.prefix`` and fill it per show.

    A clash falls back to the letters of the name, then 'X' is appended.
    """
    bind = op.get_bind()
    have = {c["name"] for c in sa.inspect(bind).get_columns("layers")}
    if "prefix" not in have:
        with op.batch_alter_table("layers") as batch:
            batch.add_column(
                sa.Column("prefix", sa.String(), nullable=False, server_default="")
            )
    rows = bind.execute(
        sa.text(
            "SELECT id, show_id, name, type FROM layers ORDER BY show_id, sequence, id"
        )
    ).fetchall()
    used: dict[int, set[str]] = {}
    for lid, show_id, name, type_ in rows:
        taken = used.setdefault(show_id, set())
        prefix = LEGACY.get(type_, _letters(name))
        if prefix.lower() in taken:
            prefix = _letters(name) or "X"
            while prefix.lower() in taken:
                prefix += "X"
        taken.add(prefix.lower())
        bind.execute(
            sa.text("UPDATE layers SET prefix = :p WHERE id = :id"),
            {"p": prefix, "id": lid},
        )


def downgrade() -> None:
    """Drop ``layers.prefix``."""
    with op.batch_alter_table("layers") as batch:
        batch.drop_column("prefix")
