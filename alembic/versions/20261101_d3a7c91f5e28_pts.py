"""pts: рейтинги учеников, задач и история изменений

Revision ID: d3a7c91f5e28
Revises: b5d8e2f61c94
Create Date: 2026-11-01 00:19:42.398775

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3a7c91f5e28'
down_revision: Union[str, Sequence[str], None] = 'b5d8e2f61c94'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('user_number_ratings',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('exam_number_id', sa.Integer(), nullable=False),
    sa.Column('pts', sa.Float(), nullable=False),
    sa.Column('rd', sa.Float(), nullable=False),
    sa.Column('vol', sa.Float(), nullable=False),
    sa.Column('games', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['exam_number_id'], ['exam_numbers.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'exam_number_id')
    )
    op.create_table('pts_changes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('attempt_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.Integer(), nullable=False),
    sa.Column('exam_number_id', sa.Integer(), nullable=False),
    sa.Column('score', sa.Float(), nullable=False),
    sa.Column('pts_before', sa.Float(), nullable=False),
    sa.Column('pts_after', sa.Float(), nullable=False),
    sa.Column('number_pts_before', sa.Float(), nullable=False),
    sa.Column('number_pts_after', sa.Float(), nullable=False),
    sa.Column('task_before', sa.Float(), nullable=False),
    sa.Column('task_after', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['attempt_id'], ['attempts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['exam_number_id'], ['exam_numbers.id'], ),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('attempt_id')
    )
    op.create_index(op.f('ix_pts_changes_created_at'), 'pts_changes', ['created_at'], unique=False)
    op.create_index(op.f('ix_pts_changes_user_id'), 'pts_changes', ['user_id'], unique=False)
    op.add_column('tasks', sa.Column('rating', sa.Float(), nullable=True))
    op.add_column('tasks', sa.Column('rating_rd', sa.Float(), server_default=sa.text('250'), nullable=False))
    op.add_column('tasks', sa.Column('rating_vol', sa.Float(), server_default=sa.text('0.06'), nullable=False))
    op.add_column('tasks', sa.Column('rating_games', sa.Integer(), server_default=sa.text('0'), nullable=False))
    op.add_column('users', sa.Column('pts', sa.Float(), server_default=sa.text('1000'), nullable=False))
    op.add_column('users', sa.Column('pts_rd', sa.Float(), server_default=sa.text('200'), nullable=False))
    op.add_column('users', sa.Column('pts_vol', sa.Float(), server_default=sa.text('0.06'), nullable=False))
    op.add_column('users', sa.Column('pts_games', sa.Integer(), server_default=sa.text('0'), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'pts_games')
    op.drop_column('users', 'pts_vol')
    op.drop_column('users', 'pts_rd')
    op.drop_column('users', 'pts')
    op.drop_column('tasks', 'rating_games')
    op.drop_column('tasks', 'rating_vol')
    op.drop_column('tasks', 'rating_rd')
    op.drop_column('tasks', 'rating')
    op.drop_index(op.f('ix_pts_changes_user_id'), table_name='pts_changes')
    op.drop_index(op.f('ix_pts_changes_created_at'), table_name='pts_changes')
    op.drop_table('pts_changes')
    op.drop_table('user_number_ratings')
