"""TDD: «📥 Скачать материалы» = РОВНО 3 вещи (ролик + обложка + описание).

Было: download_project дампил ВСЮ папку (10-14 файлов: аватар 84-157МБ, broll,
source, voice_*, кадры) → ZIP >48МБ → per-file → сотни файлов в чат (инцидент
22 июня, 118 файлов). Требование Артёма: только ролик + обложка + описание.

Стало: _resolve_download_materials(data) реюзит резолверы кросспоста
(_find_video_for_card / _find_thumbnail_for_card) + описание (data/description.txt).
Хендлер шлёт ровно эти 3, без _zip_project/rglob.

Запуск: python tests/test_download_materials.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import bot_state  # noqa: E402
import bot  # noqa: E402


def _assert(cond: bool, msg: str, errors: list) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(f"FAIL {msg}")


JUNK = ["avatar_final.mp4", "broll_1.mp4", "voice_0.mp3", "source.mp4", "final_auto_1.mp4"]


def _mock_project(with_desc_in_data: bool):
    bot_state.PROJECTS_DIR = Path(tempfile.mkdtemp(prefix="dl_mat_"))
    data = {"notion_page_id": "abc123def456ghi", "card_data": {"title": "Тест ролик"}}
    if with_desc_in_data:
        data["description"] = "Описание для публикации из data."
    proj = bot_state.project_dir(data)
    proj.mkdir(parents=True, exist_ok=True)
    for name in JUNK + ["final_video.mp4", "cover.jpg"]:
        (proj / name).write_bytes(b"x")
    (proj / "description.txt").write_text("описание из файла", encoding="utf-8")
    return data, proj


def test_resolves_exactly_three(errors):
    print("\n-- _resolve_download_materials: ролик/обложка/описание, без мусора --")
    data, proj = _mock_project(with_desc_in_data=True)
    mats = bot._resolve_download_materials(data)
    _assert(mats["video"] == str(proj / "final_video.mp4"),
            f"ролик = final_video.mp4 (не аватар/broll/source); got {mats['video']}", errors)
    _assert(mats["cover"] == str(proj / "cover.jpg"), f"обложка = cover.jpg; got {mats['cover']}", errors)
    _assert(mats["description"] == "Описание для публикации из data.", "описание из data", errors)
    vals = [str(v) for v in mats.values() if v]
    for junk in JUNK:
        _assert(not any(junk in v for v in vals), f"в материалах НЕТ {junk}", errors)


def test_description_falls_back_to_file(errors):
    print("\n-- описание: нет в data → читается из proj/description.txt --")
    data, proj = _mock_project(with_desc_in_data=False)
    mats = bot._resolve_download_materials(data)
    _assert(mats["description"] == "описание из файла", "описание из description.txt", errors)


def test_handler_no_zip_no_rglob(errors):
    print("\n-- хендлер download_project больше не дампит (нет _zip_project/rglob) --")
    src = Path(bot.__file__).read_text(encoding="utf-8")
    idx = src.find('if query.data == "download_project":')
    _assert(idx != -1, "хендлер найден", errors)
    if idx == -1:
        return
    # тело хендлера до следующего `if query.data ==`
    nxt = src.find("if query.data ==", idx + 10)
    body = src[idx: nxt if nxt != -1 else idx + 4000]
    _assert("_zip_project" not in body, "нет _zip_project (дамп всей папки убран)", errors)
    _assert(".rglob(" not in body, "нет rglob-дампа папки", errors)
    _assert("_resolve_download_materials" in body, "хендлер зовёт _resolve_download_materials", errors)


def test_oversize_video_delivers_link_not_notion(errors):
    print("\n-- download_project: ролик отдаётся каноном _broll_deliver, без вранья про Notion --")
    # P1 (live-test 30.06): при >48МБ хендлер писал «Забери его из Notion-карточки»,
    # но файл туда не кладётся НИКОГДА (Notion file-upload в коде нет). Фикс: отдать
    # ролик каноном _broll_deliver — ≤48МБ документом, >48МБ nginx-ссылкой В ЧАТ.
    # Поведение «>48МБ → ссылка» уже зафиксировано в test_broll_delivery.py; тут —
    # проверяем ВШИВАНИЕ канона в download_project и удаление ложного сообщения.
    src = Path(bot.__file__).read_text(encoding="utf-8")
    idx = src.find('if query.data == "download_project":')
    _assert(idx != -1, "хендлер найден", errors)
    if idx == -1:
        return
    nxt = src.find("if query.data ==", idx + 10)
    body = src[idx: nxt if nxt != -1 else idx + 4000]
    _assert("_broll_deliver(" in body,
            "ролик отдаётся через канон _broll_deliver (ссылка в чат при >48МБ)", errors)
    _assert("Забери его из Notion" not in body,
            "убрано враньё «забери из Notion-карточки» (файла там нет)", errors)
    _assert("MAX_BOT_UPLOAD" not in body,
            "локальный порог MAX_BOT_UPLOAD убран (порог теперь внутри _broll_deliver)", errors)


def main() -> int:
    errors: list = []
    for fn in (test_resolves_exactly_three, test_description_falls_back_to_file,
               test_handler_no_zip_no_rglob, test_oversize_video_delivers_link_not_notion):
        fn(errors)
    print("\n" + (f"FAIL ({len(errors)})" if errors else "OK all download-materials tests passed"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
