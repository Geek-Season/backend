"""Задача kompege.ru (сырой JSON) → тело POST /tasks нашего API.

Здесь только преобразование, без сети: превью при выгрузке и импорт используют
одну и ту же функцию, поэтому в превью ровно то, что уйдёт в базу.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from app.modules.tasks.answers import MAX_ANSWER_LENGTH
from scripts.kompege.convert import Image, html_to_markdown

SITE = "https://kompege.ru"
SOURCE_PREFIX = "КЕГЭ (kompege.ru)"
# Номера, которые у kompege лежат отдельными списками. 20 и 21 — внутри задач 19.
NUMBERS = [n for n in range(1, 28) if n not in (20, 21)]

MAX_ANSWER = MAX_ANSWER_LENGTH  # тот же лимит, что у API
MAX_LABEL = 100
MAX_FILES_PER_KIND = 10
ATTACHMENT_EXT = {".txt", ".csv", ".xlsx", ".xls", ".ods", ".docx", ".doc", ".odt", ".pdf", ".zip"}

# Подпись источника у kompege («comment») → наш тип источника.
_SOURCE_RULES = [
    (re.compile(r"демо", re.I), "demo"),
    (re.compile(r"апробац", re.I), "approbation"),
    (re.compile(r"основн\w* (волн|этап|период)|резерв|пере[сc]дач|досрочн|открыт\w* вариант", re.I), "official"),
]
# Сложность kompege (0–3) → наша шкала 0–100 (старт рейтинга задачи = 700 + 10 × difficulty).
DIFFICULTY = {0: 40, 1: 60, 2: 75, 3: 90}
DEFAULT_DIFFICULTY = 50
# С чего начинается вопрос 19 после общего условия игры: начало строки или нового предложения.
_QUESTION_19 = re.compile(
    r"(?:(?<=\n)|(?<=[.!?] ))(\*\*)?"
    r"(Известно|Укажите|Найдите|Определите|При как|При скольк|Назовите|Сколько|Кто из|У кого|С какой)"
)
# Автор в начале условия: «(А. Шуруха)», часто жирным и ссылкой — [**(А. Шуруха)**](https://…).
_AUTHOR = re.compile(r"^\[?(?:\*\*)?\(([^()\n\d=$\\]{2,50})\)(?:\*\*)?(?:\]\(https?://[^)\s]*\))?[ \t]*\n?")


@dataclass
class Prepared:
    external_id: str
    number: int
    payload: dict
    images: list[Image] = field(default_factory=list)
    attachments: list[dict] = field(default_factory=list)  # [{"url": "/files/…", "name": "27_A.txt"}]
    warnings: list[str] = field(default_factory=list)
    skip: str | None = None  # причина, по которой задачу не импортируем


def external_id(item: dict) -> str:
    return f"kompege:{item['taskId']}"


def exam_code(number: int) -> str:
    return "19-21" if number == 19 else str(number)


def classify_source(comment: str) -> str:
    for pattern, source in _SOURCE_RULES:
        if pattern.search(comment):
            return source
    return "author"


def source_label(comment: str, author: str | None = None) -> str:
    """«КЕГЭ (kompege.ru) · А. Шуруха · Демоверсия 2027» — что есть из автора и подписи."""
    pieces = [SOURCE_PREFIX] + [" ".join(x.split()) for x in (author, comment) if x and x.strip()]
    return " · ".join(pieces)[:MAX_LABEL]


def split_author(markdown: str) -> tuple[str | None, str]:
    """Отделить автора «(А. Шуруха)» от начала условия: он пойдёт в source_label."""
    match = _AUTHOR.match(markdown)
    if not match:
        return None, markdown
    return match.group(1).strip(), markdown[match.end():].lstrip()


def flat_answer(key: str) -> str:
    """Ответ-таблица «20065 11921\\n11 5» → «20065 11921 11 5»: наша проверка сравнивает значения через пробел."""
    return " ".join((key or "").replace("\\n", " ").split())


def video_link(item: dict) -> str | None:
    video = (item.get("video") or "").strip()
    if not video:
        return None
    seconds = str(item.get("timecode") or "").strip()
    seconds = seconds if seconds.isdigit() and seconds != "0" else ""
    if item.get("videotype") == "yt":
        return f"https://youtu.be/{video}" + (f"?t={seconds}" if seconds else "")
    if item.get("videotype") == "rutube":
        return f"https://rutube.ru/video/{video}/" + (f"?t={seconds}" if seconds else "")
    return None


def image_cache_name(src: str) -> str:
    """Имя файла, под которым выгрузка сохраняет картинку с адресом src (не data:)."""
    import hashlib

    suffix = PurePosixPath(src.split("?")[0]).suffix.lower()[:6] or ".img"
    return hashlib.sha1(src.encode()).hexdigest()[:16] + suffix


# Где выгрузка хранит скачанное (data_dir — папка --out у export и --in у import_).
def image_path(data_dir: Path, src: str) -> Path:
    return data_dir / "images" / image_cache_name(src)


def attachment_path(data_dir: Path, task_id: int, name: str) -> Path:
    return data_dir / "files" / str(task_id) / name


def missing_files(prepared: "Prepared", task_id: int, data_dir: Path) -> list[str]:
    """Какие картинки-ссылки и файлы задачи не скачаны. Такую задачу не импортируем — проверить вручную."""
    missing = [f"картинка {image.src}" for image in prepared.images
               if image.src and not _downloaded(image_path(data_dir, image.src))]
    missing += [f"файл {a['name']}" for a in prepared.attachments
                if not _downloaded(attachment_path(data_dir, task_id, a["name"]))]
    return missing


def _downloaded(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def _split_question_19(markdown: str) -> tuple[str, str] | None:
    """Текст задачи 19 у kompege = условие игры + вопрос 19. Отделяем вопрос: у нас он хранится в части."""
    match = _QUESTION_19.search(markdown)
    if not match:
        return None
    statement, question = markdown[: match.start()].strip(), markdown[match.start():].strip()
    return (statement, question) if statement and question else None


def prepare(item: dict) -> Prepared:
    number = int(item["number"])
    prepared = Prepared(external_id=external_id(item), number=number, payload={})
    warn = prepared.warnings.append

    def convert(html: str) -> str:
        result = html_to_markdown(html, image_offset=len(prepared.images))
        prepared.images.extend(result.images)
        prepared.warnings.extend(w for w in result.warnings if w not in prepared.warnings)
        return result.markdown

    author, statement = split_author(convert(item.get("text") or ""))
    explanation = convert(item.get("solve_text") or "")
    link = video_link(item)
    if link:
        explanation = (explanation + "\n\n" if explanation else "") + f"Видеоразбор: {link}"

    payload = {
        "exam_number": exam_code(number),
        "external_id": prepared.external_id,
        "source": classify_source(item.get("comment") or ""),
        "source_label": source_label(item.get("comment") or "", author),
        "difficulty": DIFFICULTY.get(item.get("difficulty"), DEFAULT_DIFFICULTY),
    }

    if number == 19:
        sub = {str(s.get("number")): s for s in item.get("subTask") or []}
        if set(sub) != {"20", "21"}:
            prepared.skip = f"у задачи 19 нет частей 20 и 21 (есть: {', '.join(sorted(sub)) or 'ничего'})"
        split = _split_question_19(statement)
        if split:
            statement, question_19 = split
        else:
            question_19 = "Ответьте на вопрос задания 19 из условия выше."
            warn("не удалось отделить вопрос 19 от условия игры — проверить вручную")
        parts = [{"number": "19", "question": question_19, "correct_answer": flat_answer(item.get("key")),
                  "explanation": ""}]
        for code in ("20", "21"):
            if code in sub:
                parts.append({"number": code, "question": convert(sub[code].get("text") or ""),
                              "correct_answer": flat_answer(sub[code].get("key")), "explanation": ""})
        # Разбор у kompege общий на всю игру — кладём его в разбор задачи, а не части.
        payload.update(statement=statement, explanation=explanation, parts=parts)
        answers = [p["correct_answer"] for p in parts]
    else:
        payload.update(statement=statement, explanation=explanation, correct_answer=flat_answer(item.get("key")))
        answers = [payload["correct_answer"]]

    if not statement:
        prepared.skip = prepared.skip or "пустое условие"
    if any(not a for a in answers):
        prepared.skip = prepared.skip or "нет ответа"
    if any(len(a) > MAX_ANSWER for a in answers):
        prepared.skip = prepared.skip or f"ответ длиннее {MAX_ANSWER} символов"

    for file in item.get("files") or []:
        # Только имя, без папок: имя пойдёт в путь на диске при выгрузке.
        name = PurePosixPath((file.get("name") or file.get("url", "")).replace("\\", "/")).name.strip()
        if PurePosixPath(name).suffix.lower() not in ATTACHMENT_EXT:
            warn(f"файл {name}: такой тип мы не принимаем — пропущен")
            continue
        prepared.attachments.append({"url": file["url"], "name": name})
    if len(prepared.attachments) > MAX_FILES_PER_KIND:
        prepared.skip = prepared.skip or f"больше {MAX_FILES_PER_KIND} файлов"
    if len(prepared.images) > MAX_FILES_PER_KIND:
        prepared.skip = prepared.skip or f"больше {MAX_FILES_PER_KIND} картинок"

    prepared.payload = payload
    return prepared
