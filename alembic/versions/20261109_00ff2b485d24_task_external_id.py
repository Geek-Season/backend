"""tasks.external_id: id задачи во внешнем банке (импорт с kompege.ru)

Уникальный и необязательный: повторный импорт находит задачу по нему.

Revision ID: 00ff2b485d24
Revises: 62308017a2b9
Create Date: 2026-11-09 13:55:00.606809

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '00ff2b485d24'
down_revision: Union[str, Sequence[str], None] = '62308017a2b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('tasks', sa.Column('external_id', sa.String(length=64), nullable=True))
    op.create_unique_constraint('tasks_external_id_key', 'tasks', ['external_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('tasks_external_id_key', 'tasks', type_='unique')
    op.drop_column('tasks', 'external_id')
