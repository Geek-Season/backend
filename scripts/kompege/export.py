"""Выгрузка задач kompege.ru в JSON. В нашу базу ничего не пишет.

    python -m scripts.kompege.export --number 2 --limit 10       # проба: 10 задач номера 2
    python -m scripts.kompege.export                             # весь банк

Что получится в папке --out (по умолчанию data/kompege):
    raw/<N>.json              задачи номера как их отдаёт kompege (без hide=true)
    files/<taskId>/<имя>      файлы к задачам (27_A.txt, 3.xlsx…)
    images/<хеш>.<расш>       картинки, если в тексте ссылка, а не data:
    preview/<N>.json          что импорт отправит в наш API (Markdown, ответ, источник)
    preview/<N>.md            то же, но удобно читать глазами

Запросы идут по одному, с паузой --delay секунд. Уже скачанные файлы не качаем повторно.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx2 as httpx  # пакет httpx2 из requirements.txt (форк httpx с тем же API)

from scripts.kompege.mapping import NUMBERS, SITE, image_cache_name, prepare

USER_AGENT = "GeekSeasonImport/1.0 (task import with permission of the kompege.ru author)"


class Polite:
    """HTTP-клиент, который не торопится: пауза перед каждым запросом и повтор при 429/5xx."""

    def __init__(self, delay: float):
        self.delay = delay
        self.client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True)
        self.last = 0.0

    def get(self, url: str) -> httpx.Response:
        for attempt in range(5):
            wait = self.last + self.delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.last = time.monotonic()
            try:
                response = self.client.get(url)
            except httpx.TransportError as error:
                print(f"  сеть: {error} — повтор", file=sys.stderr)
                time.sleep(self.delay * 2 ** (attempt + 1))
                continue
            if response.status_code == 429 or response.status_code >= 500:
                pause = float(response.headers.get("Retry-After") or self.delay * 2 ** (attempt + 1))
                print(f"  {response.status_code} на {url} — жду {pause:.0f} с", file=sys.stderr)
                time.sleep(pause)
                continue
            response.raise_for_status()
            return response
        raise RuntimeError(f"Не удалось скачать {url}")


def absolute(url: str) -> str:
    return url if url.startswith(("http://", "https://")) else SITE + "/" + url.lstrip("/")


def download(http: Polite, url: str, path: Path) -> bool:
    if path.exists() and path.stat().st_size > 0:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    data = http.get(absolute(url)).content
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)
    return True


def preview_md(number: int, prepared: list) -> str:
    out = [f"# Номер {number}: превью импорта ({len(prepared)} задач)\n"]
    for p in prepared:
        body = p.payload
        out.append(f"\n---\n\n## {p.external_id} · {body['source']} · {body['source_label']}\n")
        if p.skip:
            out.append(f"**НЕ ИМПОРТИРУЕТСЯ:** {p.skip}\n")
        for warning in p.warnings:
            out.append(f"> ⚠ {warning}\n")
        out.append(f"\n### Условие\n\n{body['statement']}\n")
        for part in body.get("parts") or []:
            out.append(f"\n### Вопрос {part['number']} — ответ `{part['correct_answer']}`\n\n{part['question']}\n")
        if "correct_answer" in body:
            out.append(f"\n**Ответ:** `{body['correct_answer']}`\n")
        if p.attachments:
            out.append("\n**Файлы:** " + ", ".join(a["name"] for a in p.attachments) + "\n")
        if p.images:
            out.append(f"\n**Картинок:** {len(p.images)}\n")
        if body.get("explanation"):
            out.append(f"\n### Разбор\n\n{body['explanation']}\n")
    return "".join(out)


def export_number(http: Polite | None, number: int, out: Path, limit: int | None) -> dict:
    """http=None — без сети: пересобрать превью из уже выгруженного raw/<N>.json (после правок конвертера)."""
    raw = out / "raw" / f"{number}.json"
    if http is None:
        print(f"Номер {number}: беру {raw}")
        items = json.loads(raw.read_text(encoding="utf-8"))
        total = len(items)
    else:
        print(f"Номер {number}: загружаю список…")
        items = http.get(f"{SITE}/api/v1/task/number/{number}").json()
        total = len(items)
    hidden = sum(1 for item in items if item.get("hide"))
    items = [item for item in items if not item.get("hide")]
    if limit:
        items = items[:limit]

    if http is not None:
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")

    prepared, downloaded = [], 0
    for item in items:
        p = prepare(item)
        prepared.append(p)
        if http is None:
            continue
        for attachment in p.attachments:
            downloaded += download(http, attachment["url"], out / "files" / str(item["taskId"]) / attachment["name"])
        for image in p.images:
            if image.src:
                downloaded += download(http, image.src, out / "images" / image_cache_name(image.src))

    (out / "preview").mkdir(parents=True, exist_ok=True)
    rows = [{"external_id": p.external_id, "skip": p.skip, "warnings": p.warnings, "images": len(p.images),
             "attachments": p.attachments, "payload": p.payload} for p in prepared]
    (out / "preview" / f"{number}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "preview" / f"{number}.md").write_text(preview_md(number, prepared), encoding="utf-8")

    stats = {"всего на сайте": total, "скрыто": hidden, "выгружено": len(items),
             "не пойдут в импорт": sum(1 for p in prepared if p.skip),
             "с предупреждениями": sum(1 for p in prepared if p.warnings), "скачано файлов": downloaded}
    print("  " + ", ".join(f"{k}: {v}" for k, v in stats.items()))
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Выгрузка задач kompege.ru в JSON")
    parser.add_argument("--number", type=int, action="append", help="номер ЕГЭ (можно несколько раз); 19 = 19–21")
    parser.add_argument("--limit", type=int, help="сколько задач номера взять (для пробы)")
    parser.add_argument("--out", type=Path, default=Path("data/kompege"))
    parser.add_argument("--delay", type=float, default=1.5, help="пауза между запросами, секунд")
    parser.add_argument("--offline", action="store_true", help="не ходить на сайт: пересобрать превью из raw/")
    args = parser.parse_args()

    numbers = args.number or NUMBERS
    wrong = [n for n in numbers if n not in NUMBERS]
    if wrong:
        parser.error(f"нет такого номера у kompege: {wrong} (20 и 21 лежат внутри 19)")

    http = None if args.offline else Polite(args.delay)
    if args.offline:
        numbers = [n for n in numbers if (args.out / "raw" / f"{n}.json").exists()]
    for number in numbers:
        export_number(http, number, args.out, args.limit)
    print(f"Готово: {args.out.resolve()}")


if __name__ == "__main__":
    main()
