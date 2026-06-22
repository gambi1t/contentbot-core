"""Тест менеджера библиотеки B-roll (8 июня): загрузка + удаление файлов.

Pure-хелперы library_manager: category_target_dir, add_file_to_library (копия +
суффикс при коллизии), delete_library_file (unlink + .json-сайдкар). На temp-дереве.

Запуск: python tests/test_library_manager.py
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
import library_manager as lm  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []
    tmp = Path(tempfile.mkdtemp(prefix="libmgr_"))
    (tmp / "maksim").mkdir()  # бренд-папка → _brand_base вернёт её
    orig = dict(bp._LIBRARY_ROOTS)
    bp._LIBRARY_ROOTS["video"] = tmp
    bp._LIBRARY_ROOTS["image"] = tmp
    # Фикстуры под <root>/maksim → пиним тенант (Fix B per-tenant), иначе на
    # сервере (tenant=panferov) _brand_base уйдёт в root/panferov.
    _orig_tid = bp.tenant.active_tenant_id
    bp.tenant.active_tenant_id = lambda: "maksim"
    # исходник для загрузки
    src = tmp / "_src.mov"; src.write_bytes(b"x" * 100)
    try:
        print("\n[category_target_dir]")
        d = lm.category_target_dir("video", "personal")
        _assert(d == tmp / "maksim" / "personal", f"папка категории, got {d}", errors)

        print("\n[add_file_to_library — копия + суффикс при коллизии]")
        p1 = lm.add_file_to_library(str(src), "video", "personal", "clip.mov")
        _assert(p1 and p1.exists() and p1.name == "clip.mov",
                f"файл скопирован как clip.mov, got {p1}", errors)
        _assert(p1.parent == tmp / "maksim" / "personal", "в правильной категории", errors)
        p2 = lm.add_file_to_library(str(src), "video", "personal", "clip.mov")
        _assert(p2 and p2.name == "clip_1.mov", f"коллизия → clip_1.mov, got {p2}", errors)
        _assert(len(bp.scan_library("video", "personal")) == 2,
                "в категории 2 файла после двух загрузок", errors)

        print("\n[delete_library_file — unlink + .json сайдкар]")
        # создадим файл с сайдкаром и удалим по id
        f = tmp / "maksim" / "personal" / "todel.mov"; f.write_bytes(b"y")
        (Path(str(f) + ".json")).write_text("{}")
        items = bp.scan_library("video", "personal")
        target = next(it for it in items if it["path"].endswith("todel.mov"))
        name = lm.delete_library_file("video", target["id"])
        _assert(name == "todel.mov", f"удалён по id, имя={name}", errors)
        _assert(not f.exists(), "файл удалён с диска", errors)
        _assert(not Path(str(f) + ".json").exists(), ".json-сайдкар тоже удалён", errors)
        _assert(lm.delete_library_file("video", "no_such_id") is None,
                "несуществующий id → None", errors)

        print("\n[_safe_name]")
        _assert(lm._safe_name("a/b:c*?.mov") == "abc.mov", "санитизация имени", errors)
        _assert(lm._safe_name("") == "file", "пустое → file", errors)
    finally:
        bp._LIBRARY_ROOTS.clear(); bp._LIBRARY_ROOTS.update(orig)
        bp.tenant.active_tenant_id = _orig_tid

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
