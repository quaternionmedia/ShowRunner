"""cue_groups: layer and number, so a group is numbered like a cue

Revision ID: d5b8e2f1a0c3
Revises: c4e9a2d07f13
Create Date: 2026-10-24 00:00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5b8e2f1a0c3"
down_revision: Union[str, Sequence[str], None] = "c4e9a2d07f13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add ``cue_groups.layer`` and ``cue_groups.number`` (nullable: groups start unnumbered)."""
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("cue_groups")}
    with op.batch_alter_table("cue_groups") as batch:
        if "layer" not in have:
            batch.add_column(sa.Column("layer", sa.String(), nullable=True))
        if "number" not in have:
            batch.add_column(sa.Column("number", sa.String(), nullable=True))


def downgrade() -> None:
    """Drop ``cue_groups.number`` and ``cue_groups.layer``."""
    with op.batch_alter_table("cue_groups") as batch:
        batch.drop_column("number")
        batch.drop_column("layer")
