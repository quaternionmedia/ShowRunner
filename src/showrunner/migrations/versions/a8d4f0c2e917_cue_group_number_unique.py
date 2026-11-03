"""cue_groups: at most one numbered group per cue list, layer and number

Revision ID: a8d4f0c2e917
Revises: f3a9c2d1b804
Create Date: 2026-10-27 10:00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8d4f0c2e917"
down_revision: Union[str, Sequence[str], None] = "f3a9c2d1b804"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX = "ux_cue_groups_layer_number"


def upgrade() -> None:
    """Add the unique index. Fails, naming no rows, if two groups already share a number."""
    if INDEX not in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("cue_groups")}:
        op.create_index(
            INDEX, "cue_groups", ["cue_list_id", "layer", "number"], unique=True,
            sqlite_where=sa.text("number IS NOT NULL AND layer IS NOT NULL"),
        )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="cue_groups")
