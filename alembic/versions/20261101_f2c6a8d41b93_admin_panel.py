"""админка: журнал действий администраторов, блокировка учеников

Revision ID: f2c6a8d41b93
Revises: e8b14c6d2a07
Create Date: 2026-11-01 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2c6a8d41b93'
down_revision: Union[str, Sequence[str], None] = 'e8b14c6d2a07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'admin_actions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('admin_id', sa.Integer(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('action', sa.String(length=40), nullable=False),
        sa.Column('details', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['admin_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_admin_actions_admin_id'), 'admin_actions', ['admin_id'], unique=False)
    op.create_index(op.f('ix_admin_actions_user_id'), 'admin_actions', ['user_id'], unique=False)
    op.create_index(op.f('ix_admin_actions_created_at'), 'admin_actions', ['created_at'], unique=False)
    op.add_column('users', sa.Column('blocked_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'blocked_at')
    op.drop_index(op.f('ix_admin_actions_created_at'), table_name='admin_actions')
    op.drop_index(op.f('ix_admin_actions_user_id'), table_name='admin_actions')
    op.drop_index(op.f('ix_admin_actions_admin_id'), table_name='admin_actions')
    op.drop_table('admin_actions')
