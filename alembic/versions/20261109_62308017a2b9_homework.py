"""домашки: homeworks, homework_tasks, homework_assignments, homework_answers

homework_tasks.task_id и homework_answers.task_id — ON DELETE RESTRICT:
задачу из домашки нельзя удалить из базы (в архив — можно).

Revision ID: 62308017a2b9
Revises: b4e8c2d17a06
Create Date: 2026-11-09 13:00:51.310514

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '62308017a2b9'
down_revision: Union[str, Sequence[str], None] = 'b4e8c2d17a06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('homeworks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('homework_assignments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('homework_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('assigned_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['homework_id'], ['homeworks.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('homework_id', 'user_id')
    )
    op.create_index(op.f('ix_homework_assignments_homework_id'), 'homework_assignments', ['homework_id'], unique=False)
    op.create_index(op.f('ix_homework_assignments_user_id'), 'homework_assignments', ['user_id'], unique=False)
    op.create_table('homework_tasks',
    sa.Column('homework_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['homework_id'], ['homeworks.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('homework_id', 'position'),
    sa.UniqueConstraint('homework_id', 'task_id')
    )
    op.create_index(op.f('ix_homework_tasks_task_id'), 'homework_tasks', ['task_id'], unique=False)
    op.create_table('homework_answers',
    sa.Column('assignment_id', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.Integer(), nullable=False),
    sa.Column('answer', sa.String(length=100), nullable=False),
    sa.Column('is_correct', sa.Boolean(), nullable=False),
    sa.Column('parts', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('answered_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['assignment_id'], ['homework_assignments.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('assignment_id', 'task_id')
    )


def downgrade() -> None:
    op.drop_table('homework_answers')
    op.drop_index(op.f('ix_homework_tasks_task_id'), table_name='homework_tasks')
    op.drop_table('homework_tasks')
    op.drop_index(op.f('ix_homework_assignments_user_id'), table_name='homework_assignments')
    op.drop_index(op.f('ix_homework_assignments_homework_id'), table_name='homework_assignments')
    op.drop_table('homework_assignments')
    op.drop_table('homeworks')
