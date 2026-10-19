"""layers_table: per-show layers (name, colour, type); seed defaults and existing cue layers

Revision ID: b3f1d0a7c921
Revises: 2a50c1c7c24c
Create Date: 2026-10-14 00:00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3f1d0a7c921"
down_revision: Union[str, Sequence[str], None] = "2a50c1c7c24c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen copy of the defaults: migrations must not import live app code.
DEFAULTS = [
    ("Lights", "orange", "lights"),
    ("Sound", "blue", "sound"),
    ("Video", "purple", "video"),
    ("Audio", "cyan", "audio"),
    ("Stage", "green", "stage"),
]


def upgrade() -> None:
    """Create ``layers`` and seed each show with the defaults plus any cue layer names in use."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("layers"):
        # A fresh DB may already have 'layers' from create_all; only create it when missing.
        op.create_table(
            "layers",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "show_id", sa.Integer(), sa.ForeignKey("shows.id"), nullable=False
            ),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("color", sa.String(), nullable=False),
            sa.Column("type", sa.String(), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.UniqueConstraint("show_id", "name"),
        )
    if not insp.has_table("shows"):
        return  # legacy/partial schema: nothing to seed

    layers = sa.table(
        "layers",
        sa.column("show_id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("color", sa.String),
        sa.column("type", sa.String),
        sa.column("sequence", sa.Integer),
    )
    show_ids = [r[0] for r in bind.execute(sa.text("select id from shows"))]
    for show_id in show_ids:
        existing = {
            r[0]
            for r in bind.execute(
                sa.text("select name from layers where show_id = :s"), {"s": show_id}
            )
        }
        seq = 0
        for name, color, type_ in DEFAULTS:
            if name not in existing:
                bind.execute(
                    layers.insert().values(
                        show_id=show_id,
                        name=name,
                        color=color,
                        type=type_,
                        sequence=seq,
                    )
                )
                existing.add(name)
            seq += 1
        if insp.has_table("cues") and insp.has_table("cue_lists"):
            extra = bind.execute(
                sa.text(
                    "select distinct c.layer from cues c join cue_lists l "
                    "on c.cue_list_id = l.id where l.show_id = :s and c.layer is not null"
                ),
                {"s": show_id},
            )
            for (name,) in extra.fetchall():
                if name in existing:
                    continue
                bind.execute(
                    layers.insert().values(
                        show_id=show_id,
                        name=name,
                        color="grey",
                        type="generic",
                        sequence=seq,
                    )
                )
                existing.add(name)
                seq += 1


def downgrade() -> None:
    """Drop the ``layers`` table."""
    op.drop_table("layers")
