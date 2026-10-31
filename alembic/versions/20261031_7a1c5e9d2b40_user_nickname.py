"""user nickname: ник на сайте вместо имени из Telegram

Revision ID: 7a1c5e9d2b40
Revises: c4e81f0b7d2a
Create Date: 2026-10-31 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a1c5e9d2b40'
down_revision: Union[str, Sequence[str], None] = 'c4e81f0b7d2a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('nickname', sa.String(length=20), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'nickname')
