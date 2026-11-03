"""cues: QLab link (external_id, external_number) so renumbers sync to the same QLab cue

Revision ID: e7a1c4b9d280
Revises: d5b8e2f1a0c3
Create Date: 2026-10-25 12:00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7a1c4b9d280"
down_revision: Union[str, Sequence[str], None] = "d5b8e2f1a0c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add ``cues.external_id`` and ``cues.external_number`` (nullable: unlinked until synced)."""
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("cues")}
    with op.batch_alter_table("cues") as batch:
        if "external_id" not in have:
            batch.add_column(sa.Column("external_id", sa.String(), nullable=True))
        if "external_number" not in have:
            batch.add_column(sa.Column("external_number", sa.String(), nullable=True))


def downgrade() -> None:
    """Drop ``cues.external_number`` and ``cues.external_id``."""
    with op.batch_alter_table("cues") as batch:
        batch.drop_column("external_number")
        batch.drop_column("external_id")
