"""Таблицы базы данных для банка заданий.

Каждый класс = одна таблица, каждый атрибут = одна колонка.
Связь простая: у номера ЕГЭ много задач (прототипов), у задачи — один номер.
Задача номеров 19–21 состоит из общего условия игры и трёх вопросов — частей.

    exam_numbers 1 ──── * tasks 1 ──── * task_parts
                                1 ──── * task_files   (картинки и файлы к задаче)
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class ExamNumber(Base):
    """Номер задания ЕГЭ: одна карточка на странице «Задания»."""

    __tablename__ = "exam_numbers"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Как номер пишется на карточке и в адресе: "1", "2", ..., "19-21".
    code: Mapped[str] = mapped_column(String(10), unique=True)
    title: Mapped[str] = mapped_column(String(100))
    # Порядок карточек на странице.
    position: Mapped[int] = mapped_column(Integer, unique=True)

    tasks: Mapped[list["Task"]] = relationship(back_populates="exam_number")


class Task(Base):
    """Задача-прототип внутри номера."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    exam_number_id: Mapped[int] = mapped_column(ForeignKey("exam_numbers.id"), index=True)

    statement: Mapped[str] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(String(500))
    correct_answer: Mapped[str] = mapped_column(String(100))
    explanation: Mapped[str] = mapped_column(Text, default="")

    # Откуда задача: demo (демоверсия), official (реальный экзамен),
    # approbation (апробация), author (авторская).
    source: Mapped[str] = mapped_column(String(20), default="author")
    # Подпись к метке источника, например «Пересдача 08.07.26».
    source_label: Mapped[str | None] = mapped_column(String(100))
    difficulty: Mapped[int] = mapped_column(Integer, default=50)

    # Задачи не удаляем физически: по ним потом будут решения учеников.
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    exam_number: Mapped[ExamNumber] = relationship(back_populates="tasks")
    # Части задачи (только у 19–21). У обычной задачи список пуст.
    parts: Mapped[list["TaskPart"]] = relationship(
        back_populates="task", order_by="TaskPart.number", cascade="all, delete-orphan"
    )
    # Загруженные картинки и файлы к задаче (по порядку загрузки).
    files: Mapped[list["TaskFile"]] = relationship(
        back_populates="task", order_by="(TaskFile.position, TaskFile.id)", cascade="all, delete-orphan"
    )

    @property
    def exam_code(self) -> str:
        """Код номера («1», «19-21») — удобно отдавать в API вместо id."""
        return self.exam_number.code

    @property
    def images(self) -> list["TaskFile"]:
        return [f for f in self.files if f.kind == FILE_IMAGE]

    @property
    def attachments(self) -> list["TaskFile"]:
        return [f for f in self.files if f.kind == FILE_ATTACHMENT]

    @property
    def student_images(self) -> list["TaskFile"]:
        """Картинки, которые можно показать ученику до ответа.

        Условие, вопросы и разборы — Markdown: автор вставляет картинку прямо в текст,
        например ![](/files/3f2a….png). Картинку из РАЗБОРА нельзя показывать под
        условием до ответа — она подсказала бы решение. Ученик увидит её в самом
        разборе, после проверки.
        """
        in_explanations = " ".join([self.explanation or "", *(p.explanation or "" for p in self.parts)])
        in_statement = " ".join([self.statement or "", *(p.question or "" for p in self.parts)])
        return [
            f for f in self.images
            if f.url not in in_explanations or f.url in in_statement  # в условии тоже — значит, не секрет
        ]


class TaskPart(Base):
    """Вопрос внутри составной задачи: «19», «20», «21» — у каждого свой ответ и свой балл.

    Условие игры общее и хранится в tasks.statement, здесь — только вопрос.
    """

    __tablename__ = "task_parts"
    __table_args__ = (UniqueConstraint("task_id", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    number: Mapped[str] = mapped_column(String(10))  # номер задания ЕГЭ: "19", "20", "21"
    question: Mapped[str] = mapped_column(Text)
    correct_answer: Mapped[str] = mapped_column(String(100))
    explanation: Mapped[str] = mapped_column(Text, default="")

    task: Mapped[Task] = relationship(back_populates="parts")


FILE_IMAGE = "image"            # картинка: показывается прямо в условии
FILE_ATTACHMENT = "attachment"  # файл к заданию (xlsx, txt, docx…): ученик скачивает


class TaskFile(Base):
    """Картинка или файл, загруженный к задаче.

    Сам файл лежит на диске сервера (папка settings.upload_dir) под случайным
    именем stored_name, а в базе — его описание. Ученику отдаётся по адресу
    GET /files/{stored_name}; для файлов-заданий — с исходным именем original_name.
    """

    __tablename__ = "task_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # image | attachment
    # Случайное имя на диске: 32 hex-символа + расширение. Не угадать и не пересечься.
    stored_name: Mapped[str] = mapped_column(String(64), unique=True)
    # Как файл назывался у автора — с этим именем его скачает ученик («17.txt»).
    original_name: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)  # байт
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    task: Mapped[Task] = relationship(back_populates="files")

    @property
    def url(self) -> str:
        """Адрес файла в API (сайт добавляет к нему свой префикс /api)."""
        return f"/files/{self.stored_name}"
