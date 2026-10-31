"""pts live: ранг, входной пробник, PTS за пробник (пустой ответ = ошибка)

Revision ID: e8b14c6d2a07
Revises: d3a7c91f5e28
Create Date: 2026-11-01 00:35:04.521003

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8b14c6d2a07'
down_revision: Union[str, Sequence[str], None] = 'd3a7c91f5e28'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('pts_changes', sa.Column('exam_session_id', sa.Integer(), nullable=True))
    op.alter_column('pts_changes', 'attempt_id',
               existing_type=sa.INTEGER(),
               nullable=True)
    op.create_index(op.f('ix_pts_changes_exam_session_id'), 'pts_changes', ['exam_session_id'], unique=False)
    op.create_unique_constraint('pts_changes_user_id_task_id_key', 'pts_changes', ['user_id', 'task_id'])
    op.create_foreign_key('pts_changes_exam_session_id_fkey', 'pts_changes', 'exam_sessions', ['exam_session_id'], ['id'], ondelete='SET NULL')
    op.add_column('users', sa.Column('rank_key', sa.String(length=20), nullable=True))
    op.add_column('users', sa.Column('calibrated_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'calibrated_at')
    op.drop_column('users', 'rank_key')
    op.drop_constraint('pts_changes_exam_session_id_fkey', 'pts_changes', type_='foreignkey')
    op.drop_constraint('pts_changes_user_id_task_id_key', 'pts_changes', type_='unique')
    op.drop_index(op.f('ix_pts_changes_exam_session_id'), table_name='pts_changes')
    op.execute("DELETE FROM pts_changes WHERE attempt_id IS NULL")  # строки из пробников; пересчёт восстановит
    op.alter_column('pts_changes', 'attempt_id',
               existing_type=sa.INTEGER(),
               nullable=False)
    op.drop_column('pts_changes', 'exam_session_id')
