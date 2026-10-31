"""user avatar and gender: аватарка (фото из Telegram или своя) и пол

Revision ID: b5d8e2f61c94
Revises: 7a1c5e9d2b40
Create Date: 2026-10-31 23:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5d8e2f61c94'
down_revision: Union[str, Sequence[str], None] = '7a1c5e9d2b40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('avatar_file', sa.String(length=64), nullable=True))
    op.add_column('users', sa.Column('avatar_custom', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('users', sa.Column('telegram_photo_uid', sa.String(length=64), nullable=True))
    op.add_column('users', sa.Column('gender', sa.String(length=1), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'gender')
    op.drop_column('users', 'telegram_photo_uid')
    op.drop_column('users', 'avatar_custom')
    op.drop_column('users', 'avatar_file')
