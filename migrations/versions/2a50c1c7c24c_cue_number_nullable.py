"""cue_number_nullable

Revision ID: 2a50c1c7c24c
Revises:
Create Date: 2026-04-20 15:36:32.527679

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2a50c1c7c24c'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Make cues.number nullable (was NOT NULL INTEGER, now NULL TEXT)."""
    with op.batch_alter_table('cues') as batch_op:
        batch_op.alter_column(
            'number',
            existing_type=sa.Integer(),
            type_=sa.String(),
            nullable=True,
        )


def downgrade() -> None:
    """Revert cues.number to NOT NULL INTEGER."""
    with op.batch_alter_table('cues') as batch_op:
        batch_op.alter_column(
            'number',
            existing_type=sa.String(),
            type_=sa.Integer(),
            nullable=False,
        )
