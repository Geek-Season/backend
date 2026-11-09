"""Импорт выгруженных задач kompege.ru в наш банк — через админский API, как в админке.

    python -m scripts.kompege.import_ --api http://127.0.0.1:8000 --dry-run    # только показать, что будет
    python -m scripts.kompege.import_ --api http://127.0.0.1:8000 --number 2  # импорт номера 2

Токен администратора — в переменной окружения GEEK_TOKEN (или --token). Его можно взять
в браузере после входа админом: localStorage, ключ с JWT.

Для каждой задачи, как в админке (AdminPage.jsx):
  1. POST /tasks — условие с метками ![](pending:N) вместо картинок;
  2. POST /tasks/{id}/files — картинки и файлы к заданию;
  3. PATCH /tasks/{id} — тексты с настоящими адресами /files/….

Повторный запуск не создаёт дублей: задача ищется по external_id (kompege:<taskId>).
Если прошлый запуск оборвался посередине (в тексте остались pending: или не все файлы),
файлы задачи загружаются заново. С --update уже импортированные задачи перезаписываются
из выгрузки (после правок конвертера); ручные правки этих задач в админке пропадут.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

import httpx2 as httpx  # пакет httpx2 из requirements.txt (форк httpx с тем же API)

from scripts.kompege.mapping import NUMBERS, Prepared, attachment_path, image_path, missing_files, prepare

PENDING = re.compile(r"!\[([^\]]*)\]\(pending:(\d+)\)")
IMAGE_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
    (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
    (b"GIF87a", ".gif", "image/gif"),
    (b"GIF89a", ".gif", "image/gif"),
]


class ApiError(Exception):
    pass


def image_type(data: bytes) -> tuple[str, str] | None:
    for magic, ext, mime in IMAGE_MAGIC:
        if data.startswith(magic):
            return ext, mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return None


def texts(payload: dict) -> list[str]:
    out = [payload.get("statement") or "", payload.get("explanation") or ""]
    for part in payload.get("parts") or []:
        out += [part.get("question") or "", part.get("explanation") or ""]
    return out


def replace_pending(payload: dict, urls: dict[int, str]) -> dict:
    """Подставить адреса загруженных картинок; неудачные картинки убрать из текста."""

    def sub(text: str) -> str:
        return PENDING.sub(lambda m: f"![{m.group(1)}]({urls[int(m.group(2))]})" if int(m.group(2)) in urls else "",
                           text or "")

    body = {"statement": sub(payload["statement"]), "explanation": sub(payload.get("explanation", ""))}
    if payload.get("parts"):
        body["parts"] = [{**p, "question": sub(p["question"]), "explanation": sub(p["explanation"])}
                         for p in payload["parts"]]
    return body


class Importer:
    def __init__(self, api: str, token: str, data_dir: Path, dry_run: bool, client=None, update: bool = False):
        self.update = update
        # client — готовый HTTP-клиент с токеном (в тестах — TestClient админа).
        self.http = client or httpx.Client(
            base_url=api.rstrip("/"), headers={"Authorization": f"Bearer {token}"}, timeout=120
        )
        self.dir = data_dir
        self.dry_run = dry_run
        self.report = {"создано": [], "уже есть": [], "дозагружено": [], "обновлено": [], "пропущено": [], "ошибки": [],
                       "проверить вручную": []}

    # ---------- HTTP ----------

    def call(self, method: str, url: str, **kwargs) -> httpx.Response:
        response = self.http.request(method, url, **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = response.text[:200]
            raise ApiError(f"{method} {url} → {response.status_code}: {detail}")
        return response

    def find(self, external_id: str) -> dict | None:
        response = self.http.get(f"/tasks/by-external/{quote(external_id, safe='')}")
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise ApiError(f"GET /tasks/by-external/{external_id} → {response.status_code}: {response.text[:200]}")
        return response.json()

    # ---------- файлы ----------

    def image_bytes(self, prepared: Prepared, index: int) -> bytes:
        image = prepared.images[index]
        if image.data is not None:
            return image.data
        path = image_path(self.dir, image.src)
        if not path.exists():
            raise ApiError(f"картинка {image.src} не скачана — запустите export ещё раз")
        return path.read_bytes()

    def attachment_path(self, item_id: int, name: str) -> Path:
        path = attachment_path(self.dir, item_id, name)
        if not path.exists():
            raise ApiError(f"файл {name} не скачан — запустите export ещё раз")
        return path

    def upload_all(self, task_id: int, prepared: Prepared, item_id: int) -> dict[int, str]:
        urls = {}
        for index in range(len(prepared.images)):
            data = self.image_bytes(prepared, index)
            kind = image_type(data)
            if kind is None:
                self.note("проверить вручную", prepared, f"картинка {index + 1}: неподдерживаемый формат — убрана")
                continue
            ext, mime = kind
            files = {"file": (f"image{index + 1}{ext}", data, mime)}
            urls[index] = self.call("POST", f"/tasks/{task_id}/files", files=files, data={"kind": "image"}).json()["url"]
        for attachment in prepared.attachments:
            path = self.attachment_path(item_id, attachment["name"])
            files = {"file": (attachment["name"], path.read_bytes(), "application/octet-stream")}
            self.call("POST", f"/tasks/{task_id}/files", files=files, data={"kind": "attachment"})
        return urls

    # ---------- одна задача ----------

    def note(self, bucket: str, prepared: Prepared, message: str = "") -> None:
        self.report[bucket].append(f"{prepared.external_id}" + (f": {message}" if message else ""))

    def complete(self, task: dict, prepared: Prepared) -> bool:
        if any("pending:" in text for text in texts(task)):
            return False
        names = sorted(a["name"] for a in task.get("attachments") or [])
        return names == sorted(a["name"] for a in prepared.attachments)

    def run_one(self, item: dict) -> None:
        prepared = prepare(item)
        if prepared.skip:
            self.note("пропущено", prepared, prepared.skip)
            return
        # Проверяем до POST /tasks: иначе на сайте осталась бы задача без картинки.
        missing = missing_files(prepared, item["taskId"], self.dir)
        if missing:
            self.note("проверить вручную", prepared,
                      "не импортирована, не скачались: " + "; ".join(missing) + " (повторите export или добавьте вручную)")
            return
        for warning in prepared.warnings:
            self.note("проверить вручную", prepared, warning)

        existing = self.find(prepared.external_id)
        if existing and not self.update and self.complete(existing, prepared):
            self.note("уже есть", prepared, f"задача {existing['id']}")
            return
        bucket = "создано" if not existing else "обновлено" if self.update else "дозагружено"
        if self.dry_run:
            self.note(bucket, prepared, "(пробный запуск)")
            return

        if existing:
            # Прошлый запуск оборвался (или --update): убираем загруженное и грузим всё заново.
            task_id = existing["id"]
            for file in (existing.get("images") or []) + (existing.get("attachments") or []):
                self.call("DELETE", f"/tasks/{task_id}/files/{file['id']}")
            if self.update:
                # Тип источника, подпись и сложность тоже берём из свежей выгрузки.
                fields = {k: prepared.payload[k] for k in ("source", "source_label", "difficulty")}
                self.call("PATCH", f"/tasks/{task_id}", json=fields)
        else:
            task_id = self.call("POST", "/tasks", json=prepared.payload).json()["id"]

        urls = self.upload_all(task_id, prepared, item["taskId"])
        self.call("PATCH", f"/tasks/{task_id}", json=replace_pending(prepared.payload, urls))
        self.note(bucket, prepared, f"задача {task_id}")

    def run(self, numbers: list[int], limit: int | None) -> None:
        for number in numbers:
            raw = self.dir / "raw" / f"{number}.json"
            if not raw.exists():
                continue
            items = [i for i in json.loads(raw.read_text(encoding="utf-8")) if not i.get("hide")][:limit]
            print(f"Номер {number}: {len(items)} задач")
            for item in items:
                try:
                    self.run_one(item)
                except (ApiError, httpx.HTTPError) as error:
                    self.report["ошибки"].append(f"kompege:{item.get('taskId')}: {error}")

    def print_report(self) -> None:
        print()
        for bucket, rows in self.report.items():
            print(f"{bucket}: {len(rows)}")
            if bucket not in ("создано", "уже есть"):
                for row in rows:
                    print(f"  {row}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт задач kompege.ru через админский API")
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="адрес API (без /api на конце)")
    parser.add_argument("--token", default=os.environ.get("GEEK_TOKEN"), help="JWT админа (или GEEK_TOKEN)")
    parser.add_argument("--in", dest="data_dir", type=Path, default=Path("data/kompege"))
    parser.add_argument("--number", type=int, action="append", help="только этот номер (можно несколько раз)")
    parser.add_argument("--limit", type=int, help="не больше стольких задач каждого номера")
    parser.add_argument("--dry-run", action="store_true", help="ничего не менять, только показать")
    parser.add_argument("--update", action="store_true",
                        help="перезаписать уже импортированные задачи из выгрузки (ручные правки в админке пропадут)")
    args = parser.parse_args()
    if not args.token:
        parser.error("нужен токен админа: --token или переменная GEEK_TOKEN")

    importer = Importer(args.api, args.token, args.data_dir, args.dry_run, update=args.update)
    me = importer.http.get("/tasks/by-external/kompege:0")
    if me.status_code in (401, 403):
        sys.exit(f"API отказал ({me.status_code}): токен не админский или истёк")
    importer.run(args.number or NUMBERS, args.limit)
    importer.print_report()
    sys.exit(1 if importer.report["ошибки"] else 0)


if __name__ == "__main__":
    main()
