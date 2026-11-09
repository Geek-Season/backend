"""ответы до 255 символов: правильный ответ задачи и ответы учеников

Ответы-таблицы номера 25 (2 × 10–12 чисел) в одну строку занимают до ~200 символов.
Расширение varchar в Postgres не переписывает таблицу — миграция быстрая.

Откат возможен, только пока в базе нет ответов длиннее 100 символов: обрезать
правильный ответ молча нельзя — задача станет нерешаемой.

Revision ID: 678b88f8a34a
Revises: 00ff2b485d24
Create Date: 2026-11-09 16:10:21.442410

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '678b88f8a34a'
down_revision: Union[str, Sequence[str], None] = '00ff2b485d24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (таблица, колонка, может ли быть NULL)
COLUMNS = [
    ('tasks', 'correct_answer', False),
    ('task_parts', 'correct_answer', False),
    ('attempts', 'answer', False),
    ('exam_answers', 'answer', True),
    ('homework_answers', 'answer', False),
]


def upgrade() -> None:
    for table, column, nullable in COLUMNS:
        op.alter_column(table, column, existing_type=sa.VARCHAR(length=100), type_=sa.String(length=255),
                        existing_nullable=nullable)


def downgrade() -> None:
    bind = op.get_bind()
    for table, column, _ in COLUMNS:
        long = bind.execute(sa.text(f"SELECT count(*) FROM {table} WHERE length({column}) > 100")).scalar()
        if long:
            raise RuntimeError(f"В {table}.{column} {long} значений длиннее 100 символов — откат их обрезал бы")
    for table, column, nullable in COLUMNS:
        op.alter_column(table, column, existing_type=sa.String(length=255), type_=sa.VARCHAR(length=100),
                        existing_nullable=nullable)
