"""Cue.number int to str

Revision ID: 61dc746fdbf5
Revises: 2a50c1c7c24c
Create Date: 2026-04-20 15:44:45.854515

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '61dc746fdbf5'
down_revision: Union[str, Sequence[str], None] = '2a50c1c7c24c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
