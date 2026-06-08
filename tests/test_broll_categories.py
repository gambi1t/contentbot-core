"""Тест category-aware B-roll библиотеки (Phase 1, 10 июня).

selfie.broll_picker: выбор B-roll по категориям, пустые скрыты, фото из
broll-library/photos/<brand>/<cat> (НЕ обложки). Pure-функции на temp-дереве.

Запуск: python tests/test_broll_categories.py
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

from selfie import broll_picker as bp  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg)
        print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def _make_tree(root: Path):
    # <root>/maksim/<cat>/<files>
    base = root / "maksim"
    (base / "glamping").mkdir(parents=True)
    (base / "karting").mkdir(parents=True)
    (base / "empty_cat").mkdir(parents=True)  # пустая — должна скрыться
    for i in range(3):
        (base / "glamping" / f"g{i}.jpg").write_bytes(b"x")
    (base / "karting" / "k0.jpg").write_bytes(b"x")
    (base / "empty_cat" / "notes.txt").write_text("not an image")  # не картинка


def main():
    errors = []
    tmp = Path(tempfile.mkdtemp(prefix="brollcat_"))
    _make_tree(tmp)

    # monkeypatch image root → temp
    orig = dict(bp._LIBRARY_ROOTS)
    bp._LIBRARY_ROOTS["image"] = tmp
    try:
        print("\n[list_library_categories — пустые скрыты]")
        cats = bp.list_library_categories("image")
        names = [c for c, _ in cats]
        _assert(names == ["glamping", "karting"], f"только непустые, отсортированы: {names}", errors)
        _assert(dict(cats)["glamping"] == 3, f"счётчик glamping=3: {dict(cats).get('glamping')}", errors)
        _assert("empty_cat" not in names, "empty_cat скрыт (нет картинок)", errors)

        print("\n[scan_library — фильтр по категории]")
        g = bp.scan_library("image", "glamping")
        _assert(len(g) == 3, f"glamping = 3 файла, got {len(g)}", errors)
        allf = bp.scan_library("image", None)
        _assert(len(allf) == 4, f"вся библиотека = 4 файла (3+1), got {len(allf)}", errors)

        print("\n[lookup_library_path — по id без знания категории]")
        some_id = g[0]["id"]
        path = bp.lookup_library_path("image", some_id)
        _assert(path is not None and Path(path).exists(), f"id резолвится в существующий путь: {path}", errors)
        _assert("glamping" in (path or ""), "путь из категории glamping", errors)

        print("\n[list_library_sample — exclude работает]")
        s1 = bp.list_library_sample("image", "glamping", n=2, exclude_ids=[])
        _assert(len(s1) == 2, f"сэмпл 2 из 3: {len(s1)}", errors)
        excl = [x["id"] for x in s1]
        s2 = bp.list_library_sample("image", "glamping", n=2, exclude_ids=excl)
        _assert(len(s2) == 1, f"после exclude 2 — остался 1: {len(s2)}", errors)
        _assert(not (set(excl) & {x['id'] for x in s2}), "exclude не пересекается", errors)

        print("\n[build_category_keyboard — callbacks + назад]")
        kb = bp.build_category_keyboard("image", cats)
        flat = [b for row in kb.inline_keyboard for b in row]
        cbs = [b.callback_data for b in flat]
        _assert(any(c == "selfie_broll:cat:photo:glamping" for c in cbs), f"кнопка категории glamping: {cbs}", errors)
        _assert(any(c == "selfie_broll:back" for c in cbs), "есть кнопка Назад", errors)

        print("\n[build_category_keyboard — «Готово (N)» виден когда выбрано]")
        # без выбора — нет кнопки Готово
        cbs0 = [b.callback_data for row in bp.build_category_keyboard("image", cats, 0).inline_keyboard for b in row]
        _assert("selfie_broll:done" not in cbs0, "при 0 выбранных кнопки Готово нет", errors)
        # с выбором — есть «Готово (N)» с числом в тексте
        kb3 = bp.build_category_keyboard("image", cats, 2)
        flat3 = [b for row in kb3.inline_keyboard for b in row]
        done = [b for b in flat3 if b.callback_data == "selfie_broll:done"]
        _assert(len(done) == 1, "при 2 выбранных есть кнопка Готово", errors)
        _assert(done and "2" in done[0].text, f"в тексте Готово число выбранных: {done[0].text if done else None}", errors)

        print("\n[build_library_keyboard — reroll несёт категорию]")
        kb2 = bp.build_library_keyboard(s1, kind="image", category="glamping")
        cbs2 = [b.callback_data for row in kb2.inline_keyboard for b in row]
        _assert(any(c == "selfie_broll:reroll:photo:glamping" for c in cbs2), f"reroll с категорией: {cbs2}", errors)
        _assert(any(c == "selfie_broll:catback:photo" for c in cbs2), "назад к категориям (catback)", errors)
        _assert(any(c.startswith("selfie_broll:pick:photo:") for c in cbs2), "кнопки выбора pick", errors)
    finally:
        bp._LIBRARY_ROOTS.clear()
        bp._LIBRARY_ROOTS.update(orig)

    print()
    if errors:
        print(f"❌ FAIL — {len(errors)}:")
        for e in errors:
            print(f"   - {e}")
        return 1
    print("✅ ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
