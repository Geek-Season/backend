"""Подготовка поиска №10: проверки без API и БД (pytest --noconftest)."""

import json
from zipfile import ZipFile

import httpx2 as httpx
import pytest

from scripts.kompege.export import Polite, export_number
from scripts.kompege.import_ import ApiError, Importer
from scripts.kompege.prepare_search import build_manifest, search_sanity


def dataset(tmp_path):
    item = {"number": 10, "taskId": 32879, "key": "93", "comment": "Демоверсия 2027",
            "text": "Сколько раз встречается слово «и» в тексте главы II?",
            "files": [{"name": "10.odt", "url": "/files/d6mfK9iIB.odt"}]}
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw/10.json").write_text(json.dumps([item]), encoding="utf-8")
    path = tmp_path / "files/32879/10.odt"
    path.parent.mkdir(parents=True)
    with ZipFile(path, "w") as archive:
        archive.writestr("content.xml", "<document/>")
    return item


@pytest.mark.parametrize("text", [
    "Определите, сколько раз в тексте произведения встречается существительное борода.",
    "В заданном тексте найдите предложение со словом плясками. Подсчитайте количество слов.",
    "Сколько раз встречается слово кошка в рассказе Зощенко?",
    "С помощью текстового редактора определите количество букв в файле.",
    "Текст произведения в файле. Откройте файл и определите, в какой главе говорится о бессмертии?",
    "Текст произведения в файле. Откройте файл и определите, сколько технологий содержал итоговый список.",
    "В файле приведён текст повести. Найдите в тексте максимальную измеримую скорость героя.",
    "Откройте файл и определите, сколько раз в тексте встречается лозунг ВОЙНА — ЭТО МИР.",
    "С помощью текстового редактора определите, сколько реплик Милона в комедии.",
    "С помощью текстового редактора определите, сколько раз встречаются личные местоимения в тексте.",
    "С помощью текстового редактора определите, сколько раз использован предлог в оде.",
])
def test_search_without_required_phrase(text):
    assert search_sanity(text)


def test_foreign_topic_rejected():
    assert not search_sanity("Определите количество путей в графе.")


def test_manifest_offline_preserves_id_and_validates_files(tmp_path, monkeypatch):
    dataset(tmp_path)
    monkeypatch.setattr(httpx.Client, "request", lambda *a, **kw: pytest.fail("Сеть запрещена"))
    manifest = build_manifest(tmp_path)
    assert manifest["count"] == manifest["ready"] == manifest["files"] == 1
    assert not manifest["failures"]
    row = manifest["tasks"][0]
    assert row["payload"]["external_id"] == "kompege:32879"
    assert row["payload"]["exam_number"] == "10"
    assert len(row["files"][0]["sha256"]) == 64


@pytest.mark.parametrize("content", [b"", b"<html>error</html>", b"broken zip"])
def test_bad_document_blocks_readiness(tmp_path, content):
    dataset(tmp_path)
    (tmp_path / "files/32879/10.odt").write_bytes(content)
    manifest = build_manifest(tmp_path)
    assert manifest["ready"] == 0 and manifest["failures"]


def test_duplicate_id_blocks_readiness(tmp_path):
    item = dataset(tmp_path)
    (tmp_path / "raw/10.json").write_text(json.dumps([item, item]), encoding="utf-8")
    assert build_manifest(tmp_path)["failures"][0]["reason"] == "повтор taskId"


@pytest.mark.parametrize("dry_run", [True, False])
def test_existing_incomplete_task_is_never_changed(tmp_path, dry_run):
    item = dataset(tmp_path)
    calls = []

    def handler(request):
        calls.append(request.method)
        assert request.method == "GET"
        return httpx.Response(200, json={"id": 1, "exam_number": "10", "statement": "pending:0"})

    importer = Importer("", "", tmp_path, dry_run, skip_existing=True,
                        client=httpx.Client(base_url="https://local.invalid", transport=httpx.MockTransport(handler)))
    importer.run_one(item)
    assert calls == ["GET"] and len(importer.report["уже есть"]) == 1


@pytest.mark.parametrize("skip_existing", [True, False])
def test_different_number_cannot_be_repaired_or_updated(tmp_path, skip_existing):
    item = dataset(tmp_path)
    importer = Importer("", "", tmp_path, False, client=object(), skip_existing=skip_existing)
    importer.find = lambda _: {"id": 1, "exam_number": "old-10"}
    with pytest.raises(ApiError, match="другой номер"):
        importer.run_one(item)


def test_skip_existing_conflicts_with_update(tmp_path):
    with pytest.raises(ValueError):
        Importer("", "", tmp_path, True, update=True, skip_existing=True)


def test_export_downloads_unsupported_original_too(tmp_path):
    item = dataset(tmp_path)
    item["files"].append({"name": "10.rtf", "url": "/files/original.rtf"})

    def handler(request):
        if request.url.path.endswith("/number/10"):
            return httpx.Response(200, json=[item])
        assert request.url.path == "/files/original.rtf"
        return httpx.Response(200, content=b"{\\rtf1 original}")

    http = Polite(0)
    http.client = httpx.Client(transport=httpx.MockTransport(handler))
    assert export_number(http, 10, tmp_path, None)["скачано файлов"] == 1
    manifest = build_manifest(tmp_path)
    assert manifest["ready"] == 1 and manifest["files"] == 2 and manifest["import_files"] == 1
