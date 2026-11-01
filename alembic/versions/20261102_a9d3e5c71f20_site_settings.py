"""настройки сайта: дата ЕГЭ для счётчика на главной

Revision ID: a9d3e5c71f20
Revises: f2c6a8d41b93
Create Date: 2026-11-02 00:10:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9d3e5c71f20'
down_revision: Union[str, Sequence[str], None] = 'f2c6a8d41b93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'site_settings',
        sa.Column('key', sa.String(length=40), nullable=False),
        sa.Column('value', sa.String(length=200), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )


def downgrade() -> None:
    op.drop_table('site_settings')
