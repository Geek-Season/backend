"""Офлайн-проверка выгрузки поиска №10, без нашего API, настроек и БД.

    python -m scripts.kompege.prepare_search --in data/kompege

Сначала запустите существующий export --number 10. Manifest содержит контрольные
суммы всех оригиналов; dry-run использует настоящий Importer с запрещённой сетью.
Существование external_id на целевом сервере здесь неизвестно: будущий импорт
обязательно выполняется с --skip-existing, без --update.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

from app.modules.tasks.schemas import TaskCreate
from scripts.kompege.import_ import Importer, image_type
from scripts.kompege.mapping import attachment_path, missing_files, prepare


def search_sanity(statement: str) -> bool:
    """Несколько независимых признаков, а не одна обязательная фраза."""
    text = statement.casefold().replace("ё", "е")
    operation = re.search(r"сколько|количеств|подсчита|определ|найд", text)
    language = re.search(r"слов|предложен|существительн|букв|символ|сочетан|лозунг|имя|имени|реплик|местоимен|предлог", text)
    document = re.search(r"текст|файл|глав|роман|повест|рассказ|произведен|книг|редактор", text)
    # Есть и поиск сведений в книге: номера главы, скорости героя, размера списка.
    retrieval = re.search(r"откройте.*файл.*определите|найдите в тексте", text, re.S)
    return bool(operation and document and (language or retrieval))


class NoNetwork:
    """Любой HTTP-запрос — ошибка."""

    def get(self, *args, **kwargs):
        raise AssertionError("Сеть запрещена при офлайн-проверке")

    request = get


class OfflineImporter(Importer):
    """Наличие задачи на сервере здесь неизвестно."""

    def find(self, external_id):
        return None


def inspect_file(path: Path) -> dict:
    if not path.is_file() or not path.stat().st_size:
        raise ValueError(f"файл отсутствует или пуст: {path}")
    data = path.read_bytes()
    if data.lstrip().lower().startswith((b"<!doctype html", b"<html")):
        raise ValueError(f"вместо файла скачан HTML: {path}")
    if path.suffix.lower() in {".odt", ".docx"}:
        try:
            with ZipFile(path) as archive:
                required = "content.xml" if path.suffix.lower() == ".odt" else "word/document.xml"
                if required not in archive.namelist() or archive.testzip():
                    raise ValueError(f"повреждён документ: {path}")
        except BadZipFile as error:
            raise ValueError(f"некорректный ZIP-документ: {path}") from error
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def build_manifest(data_dir: Path) -> dict:
    raw = data_dir / "raw" / "10.json"
    items = json.loads(raw.read_text(encoding="utf-8"))
    importer = OfflineImporter("", "", data_dir, dry_run=True, client=NoNetwork(),
                               skip_existing=True)
    rows, seen = [], set()
    for item in items:
        row = {"external_id": f"kompege:{item.get('taskId')}", "files": [], "images": [], "failures": []}
        rows.append(row)
        try:
            if item.get("number") != 10 or type(item.get("taskId")) is not int or item["taskId"] <= 0:
                raise ValueError("неверный номер или taskId")
            if item["taskId"] in seen:
                raise ValueError("повтор taskId")
            seen.add(item["taskId"])
            p = prepare(item)
            row.update(hidden=bool(item.get("hide")), warnings=p.warnings,
                       payload=p.payload, sanity=search_sanity(p.payload["statement"]))
            if item.get("hide"):
                row["excluded"] = "скрытая задача"
                continue
            if p.skip or not row["sanity"]:
                raise ValueError(p.skip or "не прошла тематическую проверку поиска")
            TaskCreate.model_validate(p.payload)
            if p.external_id != row["external_id"] or p.payload["exam_number"] != "10":
                raise ValueError("изменён external_id или номер")
            names = set()
            for file in item.get("files") or []:
                name = PurePosixPath((file.get("name") or file.get("url", "")).replace("\\", "/")).name.strip()
                if not name or name in {".", ".."} or name in names:
                    raise ValueError("пустое или повторяющееся имя вложения")
                names.add(name)
                path = attachment_path(data_dir, item["taskId"], name)
                if not path.resolve().is_relative_to(data_dir.resolve()):
                    raise ValueError("путь вложения вне выгрузки")
                row["files"].append({"path": path.relative_to(data_dir).as_posix(), "url": file["url"],
                                     "import": any(a["name"] == name for a in p.attachments),
                                     **inspect_file(path)})
                if row["files"][-1]["bytes"] > 20 * 1024 * 1024:
                    raise ValueError("вложение превышает стандартный лимит API 20 МБ")
            if not p.attachments:
                raise ValueError("нет поддерживаемого файла с текстом")
            missing = missing_files(p, item["taskId"], data_dir)
            if missing:
                raise ValueError("; ".join(missing))
            for index in range(len(p.images)):
                data = importer.image_bytes(p, index)
                if image_type(data) is None or len(data) > 5 * 1024 * 1024:
                    raise ValueError("неподдерживаемая картинка или превышен стандартный лимит API 5 МБ")
                row["images"].append({"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                                      "source": p.images[index].src or "inline base64"})
            importer.run_one(item)
        except (ValueError, OSError) as error:
            row["failures"].append(str(error))
    failures = [{"external_id": r["external_id"], "reason": f} for r in rows for f in r["failures"]]
    return {"number": 10, "offline": True, "target_existing_ids": "не проверялись; использовать --skip-existing",
            "raw_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(), "count": len(rows),
            "ready": len(importer.report["создано"]), "files": sum(len(r["files"]) for r in rows),
            "images": sum(len(r["images"]) for r in rows),
            "checked_default_limits_mb": {"attachment": 20, "image": 5},
            "import_files": sum(f["import"] for r in rows for f in r["files"]),
            "failures": failures, "dry_run": importer.report, "tasks": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="data_dir", type=Path, default=Path("data/kompege"))
    args = parser.parse_args()
    manifest = build_manifest(args.data_dir)
    path = args.data_dir / "manifest-10.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("count", "ready", "files", "import_files", "failures")},
                     ensure_ascii=False, indent=2))
    print(f"Manifest: {path.resolve()}")
    raise SystemExit(1 if manifest["failures"] else 0)


if __name__ == "__main__":
    main()
