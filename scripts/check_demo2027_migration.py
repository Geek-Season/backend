"""Проверка upgrade → downgrade → upgrade на отдельной пустой БД *_check.

DATABASE_URL должен указывать на специально созданную БД, не на geek_test/рабочую.
Запуск: python -m scripts.check_demo2027_migration. БД остаётся для осмотра.
"""
import subprocess
import sys

from sqlalchemy import create_engine, text

from app.core.config import settings


def alembic(*args):
    subprocess.run([sys.executable, "-m", "alembic", *args], check=True)


def main():
    engine = create_engine(settings.database_url)
    if not engine.url.database.endswith("_check"):
        raise RuntimeError("Нужна отдельная пустая БД с окончанием _check")
    with engine.connect() as db:
        if db.execute(text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")).scalar_one():
            raise RuntimeError("Проверка требует пустую БД; существующие данные не очищаются")
    alembic("upgrade", "678b88f8a34a")
    # Намеренно нестандартные названия: downgrade обязан восстановить и ручные правки.
    codes = [*(str(n) for n in range(1, 19)), "19-21", *(str(n) for n in range(22, 28))]
    numbers = [(code, f"До переноса: {code}") for code in codes]
    with engine.begin() as db:
        ids = {}
        for pos, (code, title) in enumerate(numbers, 1):
            ids[code] = db.execute(text("INSERT INTO exam_numbers(code,title,position) VALUES (:c,:t,:p) RETURNING id"),
                                   dict(c=code, t=title, p=pos)).scalar_one()
        task_ids = []
        for code, archived in (("10", False), ("13", False), ("23", False), ("23", True)):
            task_ids.append(db.execute(text("""INSERT INTO tasks
                (exam_number_id,statement,correct_answer,explanation,source,difficulty,is_archived)
                VALUES (:n,:s,'7','','author',50,:a) RETURNING id"""),
                dict(n=ids[code], s=f"fixture {code} {archived}", a=archived)).scalar_one())
        uid = db.execute(text("INSERT INTO users(telegram_id,first_name) VALUES (2027001,'Проверка') RETURNING id")).scalar_one()
        hw = db.execute(text("INSERT INTO homeworks(title) VALUES ('До переноса') RETURNING id")).scalar_one()
        session = db.execute(text("""INSERT INTO exam_sessions(user_id,deadline_at,max_score)
            VALUES (:u,now()+interval '2 hours',3) RETURNING id"""), dict(u=uid)).scalar_one()
        finished_session = db.execute(text("""INSERT INTO exam_sessions
            (user_id,deadline_at,finished_at,max_score,primary_score)
            VALUES (:u,now()+interval '2 hours',now(),3,3) RETURNING id"""), dict(u=uid)).scalar_one()
        for pos, tid in enumerate(task_ids[:3], 1):
            db.execute(text("INSERT INTO homework_tasks VALUES (:h,:p,:t)"), dict(h=hw, p=pos, t=tid))
            db.execute(text("INSERT INTO exam_answers(session_id,position,task_id,points) VALUES (:s,:p,:t,1)"),
                       dict(s=session, p=pos, t=tid))
            db.execute(text("INSERT INTO exam_answers(session_id,position,task_id,points,answer,is_correct) VALUES (:s,:p,:t,1,'7',true)"),
                       dict(s=finished_session, p=pos, t=tid))
            db.execute(text("INSERT INTO attempts(user_id,task_id,answer,is_correct) VALUES (:u,:t,'7',true)"),dict(u=uid,t=tid))
            db.execute(text("""INSERT INTO user_number_ratings
                (user_id,exam_number_id,pts,rd,vol,games) VALUES (:u,:n,1500,80,0.06,1)"""),
                dict(u=uid,n=ids[("10","13","23")[pos-1]]))
        before = db.execute(text("SELECT id,code,title,position FROM exam_numbers ORDER BY id")).all()
        ratings_before = db.execute(text("SELECT * FROM user_number_ratings ORDER BY exam_number_id")).all()
    alembic("upgrade", "head")

    def verify():
        with engine.connect() as db:
            current = dict(db.execute(text("SELECT code,id FROM exam_numbers")).all())
            assert current["13"] == ids["10"] and current["23"] == ids["13"]
            assert current["legacy-23"] == ids["23"]
            assert db.execute(text("SELECT count(*) FROM exam_numbers WHERE is_active")).scalar_one() == 25
            assert [r[0] for r in db.execute(text("SELECT number_code FROM homework_tasks ORDER BY position"))] == ["10","13","23"]
            for sid in (session, finished_session):
                assert list(db.execute(text("SELECT number_code FROM exam_answers WHERE session_id=:s ORDER BY position"),dict(s=sid)).scalars()) == ["10","13","23"]
            assert db.execute(text("SELECT * FROM user_number_ratings ORDER BY exam_number_id")).all() == ratings_before
            assert db.execute(text("SELECT count(*) FROM attempts")).scalar_one() == 3
            assert all(db.execute(text("SELECT is_archived FROM tasks WHERE exam_number_id=:n"),dict(n=ids["23"])).scalars())
    verify()
    alembic("downgrade", "-1")
    with engine.connect() as db:
        assert db.execute(text("SELECT id,code,title,position FROM exam_numbers ORDER BY id")).all() == before
        assert db.execute(text("SELECT is_archived FROM tasks WHERE id=:id"),dict(id=task_ids[2])).scalar_one() is False
        assert db.execute(text("SELECT is_archived FROM tasks WHERE id=:id"),dict(id=task_ids[3])).scalar_one() is True
    alembic("upgrade", "head")
    verify()
    print("Миграция: upgrade → downgrade → upgrade, ID, история и архив проверены")


if __name__ == "__main__":
    main()
