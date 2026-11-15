"""Утверждённая демоверсия 2027: темы, архив графов и исторические подписи.

Revision ID: d7f2027a0016
Revises: 678b88f8a34a
"""
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "d7f2027a0016"
down_revision = "678b88f8a34a"
branch_labels = None
depends_on = None

TITLES = {
    "3": "Базы данных", "9": "Электронные таблицы",
    "10": "Поиск в текстовом редакторе", "13": "IP-адресация и маски подсети",
    "18": "Робот в таблице", "23": "Исполнитель",
    "26": "Сортировка и обработка данных", "27": "Кластеризация",
}


def upgrade():
    db = op.get_bind()
    op.add_column("exam_numbers", sa.Column("is_active", sa.Boolean(), nullable=False,
                                           server_default=sa.true()))
    for table in ("exam_answers", "homework_tasks"):
        op.add_column(table, sa.Column("number_code", sa.String(10), nullable=True))
        op.add_column(table, sa.Column("number_title", sa.String(100), nullable=True))
    # Сначала зафиксировать прежнюю нумерацию, включая незавершённые работы.
    db.execute(sa.text("""
        UPDATE exam_answers a SET number_code = CASE WHEN a.part_id IS NULL THEN n.code
            ELSE (SELECT p.number FROM task_parts p WHERE p.id = a.part_id) END, number_title = n.title
        FROM tasks t JOIN exam_numbers n ON n.id = t.exam_number_id
        WHERE a.task_id = t.id
    """))
    db.execute(sa.text("""
        UPDATE homework_tasks h SET number_code = n.code, number_title = n.title
        FROM tasks t JOIN exam_numbers n ON n.id = t.exam_number_id WHERE h.task_id = t.id
    """))
    # Состояние нужно для точного отката: ранее архивированные графы не разархивируем.
    op.create_table("demo_2027_migration_backup",
                    sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("state", JSONB(), nullable=False))
    numbers = [dict(r) for r in db.execute(sa.text(
        "SELECT id, code, title, position FROM exam_numbers ORDER BY id")).mappings()]
    by_code = {n["code"]: n for n in numbers}
    state = {"numbers": numbers, "graphs": [], "new_number_id": None}
    if numbers:
        if not all(c in by_code for c in ("10", "13", "23")):
            raise RuntimeError("Неполный справочник номеров: перенос требует ручной проверки")
        graphs, network, executor = (by_code[c] for c in ("23", "10", "13"))
        state["graphs"] = [dict(r) for r in db.execute(sa.text(
            "SELECT id, is_archived FROM tasks WHERE exam_number_id = :id"), graphs).mappings()]
        # Освобождаем уникальные code/position до назначения итоговых значений.
        for n in (graphs, network, executor):
            db.execute(sa.text("UPDATE exam_numbers SET code=:code, position=:pos WHERE id=:id"),
                       {"id": n["id"], "code": f"_d27{n['code']}", "pos": -n["id"]})
        for n, code, pos, active, title in (
            (graphs, "legacy-23", 10023, False, "Графы — архив проекта 2027"),
            (network, "13", executor["position"], True, TITLES["13"]),
            (executor, "23", graphs["position"], True, TITLES["23"]),
        ):
            db.execute(sa.text("""UPDATE exam_numbers SET code=:code, position=:pos,
                is_active=:active, title=:title WHERE id=:id"""),
                       dict(id=n["id"], code=code, pos=pos, active=active, title=title))
        db.execute(sa.text("UPDATE tasks SET is_archived=true WHERE exam_number_id=:id"), graphs)
        state["new_number_id"] = db.execute(sa.text("""
            INSERT INTO exam_numbers (code,title,position,is_active) VALUES ('10',:title,:pos,true)
            RETURNING id"""), {"title": TITLES["10"], "pos": network["position"]}).scalar_one()
        for code, title in TITLES.items():
            db.execute(sa.text("UPDATE exam_numbers SET title=:title WHERE code=:code"),
                       {"code": code, "title": title})
    db.execute(sa.text("INSERT INTO demo_2027_migration_backup VALUES (1,CAST(:state AS jsonb))"),
               {"state": json.dumps(state, ensure_ascii=False)})


def downgrade():
    db = op.get_bind()
    state = db.execute(sa.text("SELECT state FROM demo_2027_migration_backup WHERE id=1")).scalar_one()
    new_id = state["new_number_id"]
    if new_id is not None:
        # После импорта/новых ответов нужен восстановительный план, не каскадное удаление.
        for table in ("tasks", "user_number_ratings", "pts_changes"):
            count = db.execute(sa.text(f"SELECT count(*) FROM {table} WHERE exam_number_id=:id"),
                               {"id": new_id}).scalar_one()
            if count:
                raise RuntimeError("Откат остановлен: новый №10 уже используется; восстановите резервную копию")
        db.execute(sa.text("DELETE FROM exam_numbers WHERE id=:id"), {"id": new_id})
        for n in state["numbers"]:
            db.execute(sa.text("UPDATE exam_numbers SET code=:code, position=:pos WHERE id=:id"),
                       {"id": n["id"], "code": f"_r{n['id']}", "pos": -n["id"]})
        for n in state["numbers"]:
            db.execute(sa.text("UPDATE exam_numbers SET code=:code, title=:title, position=:position WHERE id=:id"), n)
        for task in state["graphs"]:
            db.execute(sa.text("UPDATE tasks SET is_archived=:is_archived WHERE id=:id"), task)
    op.drop_table("demo_2027_migration_backup")
    for table in ("exam_answers", "homework_tasks"):
        op.drop_column(table, "number_title")
        op.drop_column(table, "number_code")
    op.drop_column("exam_numbers", "is_active")
