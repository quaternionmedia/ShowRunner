"""cue_groups: groups of cues (nestable boxes) and cues.group_id

Revision ID: c4e9a2d07f13
Revises: b3f1d0a7c921
Create Date: 2026-10-20 00:00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e9a2d07f13"
down_revision: Union[str, Sequence[str], None] = "b3f1d0a7c921"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create ``cue_groups`` and add ``cues.group_id`` (nullable: most cues are ungrouped)."""
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("cue_groups"):
        # A fresh DB may already have it from create_all; only create it when missing.
        op.create_table(
            "cue_groups",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("cue_list_id", sa.Integer(), sa.ForeignKey("cue_lists.id"), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("color", sa.String(), nullable=False),
            sa.Column("parent_id", sa.Integer(), sa.ForeignKey("cue_groups.id"), nullable=True),
            sa.Column("external_id", sa.String(), nullable=True),
        )
    if "group_id" not in {c["name"] for c in insp.get_columns("cues")}:
        with op.batch_alter_table("cues") as batch:
            batch.add_column(sa.Column("group_id", sa.Integer(), nullable=True))
            batch.create_foreign_key("fk_cues_group_id", "cue_groups", ["group_id"], ["id"])


def downgrade() -> None:
    """Drop ``cues.group_id`` and ``cue_groups``."""
    with op.batch_alter_table("cues") as batch:
        batch.drop_constraint("fk_cues_group_id", type_="foreignkey")
        batch.drop_column("group_id")
    op.drop_table("cue_groups")
