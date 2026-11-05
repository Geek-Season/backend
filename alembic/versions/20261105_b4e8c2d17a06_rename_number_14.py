"""номер 14: «Арифметика СС» → «Арифметика системы счисления» (без сокращения)

Revision ID: b4e8c2d17a06
Revises: a9d3e5c71f20
Create Date: 2026-11-05 00:10:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4e8c2d17a06'
down_revision: Union[str, Sequence[str], None] = 'a9d3e5c71f20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD = 'Арифметика СС'
NEW = 'Арифметика системы счисления'


def _rename(old: str, new: str) -> None:
    # Меняем только если название не правили руками в админке.
    op.get_bind().execute(
        sa.text("UPDATE exam_numbers SET title = :new WHERE code = '14' AND title = :old"),
        {'new': new, 'old': old},
    )


def upgrade() -> None:
    _rename(OLD, NEW)


def downgrade() -> None:
    _rename(NEW, OLD)
