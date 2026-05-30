"""Tests for brand-aware library routing (фото/клипы) и хелперов /start.

Покрывает функции, добавленные в bot.py для пакета правок UX
B-roll меню (25 мая 2026):

- _list_brand_photo_library(brand)       — источник фото-библиотеки по бренду
- _list_brand_clip_library(brand)        — источник клип-библиотеки по бренду
- _resolve_library_target(brand, kind, category) — путь куда копировать загрузку
- _copy_to_library(src, brand, kind, category)   — копия с уникальным именем
- _match_brand_clip_categories(brand, text)      — подсветка ⭐ под сценарий
- _extract_last_card_from_state(state)   — выделить last-card из pending-state
- _build_maksim_start_kb(last_card)      — главное меню Максима + «Продолжить»
- bot.PHOTO_PREVIEW_COUNT == 9           — превью теперь 9, не 6

Стиль: тот же что и test_photo_library.py — без pytest, main() → 0/1.
Запуск: python tests/test_brand_library_routing.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import bot  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────

def _make_png(path: Path) -> None:
    """Minimal valid 1×1 PNG."""
    data = bytes.fromhex(
        "89504e470d0a1a0a"
        "0000000d49484452"
        "00000001000000010806000000"
        "1f15c4890000000d49444154"
        "789c626001000000ffff03000006000557bfabd4"
        "0000000049454e44ae426082"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _make_mp4_stub(path: Path) -> None:
    """Just a placeholder file with .mp4 extension; we don't decode video.

    Размер должен быть > 1100 байт — фильтр клип-библиотеки отсеивает
    файлы < 1000 байт как «битые/пустые» (production guard).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 1200)


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    safe_msg = msg.encode("ascii", "replace").decode("ascii")
    if not cond:
        errors.append(f"FAIL {safe_msg}")
        print(f"  FAIL {safe_msg}")
    else:
        print(f"  OK {safe_msg}")


def with_temp_library(func):
    """Swap bot.BROLL_LIBRARY_DIR + bot.PHOTO_LIBRARY_DIR to a fresh tempdir."""
    def wrapper(errors):
        tmp = Path(tempfile.mkdtemp(prefix="brandlib_test_"))
        orig_broll = bot.BROLL_LIBRARY_DIR
        orig_photo = bot.PHOTO_LIBRARY_DIR
        bot.BROLL_LIBRARY_DIR = tmp
        bot.PHOTO_LIBRARY_DIR = tmp / "photos"
        try:
            func(tmp, errors)
        finally:
            bot.BROLL_LIBRARY_DIR = orig_broll
            bot.PHOTO_LIBRARY_DIR = orig_photo
            shutil.rmtree(tmp, ignore_errors=True)
    return wrapper


# ─── 1. PREVIEW_COUNT константа ───────────────────────────────────────────

def test_preview_count_is_nine(errors: list[str]) -> None:
    print("\n-- PREVIEW_COUNT = 9 --")
    val = getattr(bot, "PHOTO_PREVIEW_COUNT", None)
    _assert(val == 9, f"bot.PHOTO_PREVIEW_COUNT == 9 (got {val!r})", errors)


# ─── 2. _list_brand_photo_library ─────────────────────────────────────────

@with_temp_library
def test_brand_photo_library_maksim(tmp: Path, errors: list[str]) -> None:
    print("\n-- _list_brand_photo_library('maksim') --")
    # Maksim's photos in their dedicated brand dir
    _make_png(tmp / "photos" / "maksim" / "karting" / "k1.jpg")
    _make_png(tmp / "photos" / "maksim" / "glamping" / "g1.jpg")
    # Артёмовы midjourney — должны быть проигнорированы для бренда maksim
    _make_png(tmp / "photos" / "midjourney" / "m1.jpg")

    fn = getattr(bot, "_list_brand_photo_library", None)
    _assert(callable(fn), "_list_brand_photo_library exists", errors)
    if not fn:
        return
    result = fn("maksim")
    names = sorted(p.name for p in result)
    _assert(
        names == ["g1.jpg", "k1.jpg"],
        f"maksim returns only maksim photos ({names})",
        errors,
    )


@with_temp_library
def test_brand_photo_library_default(tmp: Path, errors: list[str]) -> None:
    print("\n-- _list_brand_photo_library('default') --")
    _make_png(tmp / "photos" / "midjourney" / "m1.jpg")
    _make_png(tmp / "photos" / "maksim" / "karting" / "k1.jpg")

    fn = getattr(bot, "_list_brand_photo_library", None)
    if not fn:
        return
    result = fn("default")
    names = sorted(p.name for p in result)
    # default = старое поведение Артёма (фото мирового пула, обычно midjourney)
    _assert(
        "m1.jpg" in names,
        f"default sees midjourney photos ({names})",
        errors,
    )


# ─── 3. _list_brand_clip_library ──────────────────────────────────────────

@with_temp_library
def test_brand_clip_library_maksim(tmp: Path, errors: list[str]) -> None:
    print("\n-- _list_brand_clip_library('maksim') --")
    # Реальная структура: broll-library/clips/maksim/<cat>/<file>.mp4
    _make_mp4_stub(tmp / "clips" / "maksim" / "karting" / "kart1.mp4")
    _make_mp4_stub(tmp / "clips" / "maksim" / "karting" / "kart2.mp4")
    _make_mp4_stub(tmp / "clips" / "maksim" / "glamping" / "gl1.mp4")
    _make_mp4_stub(tmp / "clips" / "maksim" / "personal" / "p1.mp4")
    # Артёмовы клипы — игнор
    _make_mp4_stub(tmp / "robots" / "r1.mp4")

    fn = getattr(bot, "_list_brand_clip_library", None)
    _assert(callable(fn), "_list_brand_clip_library exists", errors)
    if not fn:
        return
    result = fn("maksim")  # → list[(category, [paths])]
    cats = {cat: len(paths) for cat, paths in result}
    _assert(cats.get("karting") == 2, f"karting → 2 clips (got {cats})", errors)
    _assert(cats.get("glamping") == 1, f"glamping → 1 clip (got {cats})", errors)
    _assert(cats.get("personal") == 1, f"personal → 1 clip (got {cats})", errors)
    _assert("robots" not in cats, f"artem's robots NOT in maksim view ({cats})", errors)


@with_temp_library
def test_brand_clip_library_default(tmp: Path, errors: list[str]) -> None:
    print("\n-- _list_brand_clip_library('default') --")
    # Артёмовы клипы — в корне broll-library/<cat>/<file>.mp4
    _make_mp4_stub(tmp / "robots" / "r1.mp4")
    _make_mp4_stub(tmp / "ai-tools" / "a1.mp4")
    # Maksim's clips — игнор
    _make_mp4_stub(tmp / "clips" / "maksim" / "karting" / "k1.mp4")

    fn = getattr(bot, "_list_brand_clip_library", None)
    if not fn:
        return
    result = fn("default")
    cats = {cat: len(paths) for cat, paths in result}
    _assert(cats.get("robots") == 1, f"default sees robots ({cats})", errors)
    _assert(cats.get("ai-tools") == 1, f"default sees ai-tools ({cats})", errors)
    _assert("karting" not in cats, f"maksim's karting NOT in default ({cats})", errors)


# ─── 4. _resolve_library_target ───────────────────────────────────────────

@with_temp_library
def test_resolve_library_target_paths(tmp: Path, errors: list[str]) -> None:
    print("\n-- _resolve_library_target --")
    fn = getattr(bot, "_resolve_library_target", None)
    _assert(callable(fn), "_resolve_library_target exists", errors)
    if not fn:
        return

    p_photo = fn("maksim", "photo", "karting")
    _assert(
        str(p_photo).replace("\\", "/").endswith("photos/maksim/karting"),
        f"photo target = photos/maksim/karting (got {p_photo})",
        errors,
    )

    p_video = fn("maksim", "video", "glamping")
    _assert(
        str(p_video).replace("\\", "/").endswith("clips/maksim/glamping"),
        f"video target = clips/maksim/glamping (got {p_video})",
        errors,
    )


# ─── 5. _copy_to_library — копия с уникальным именем ─────────────────────

@with_temp_library
def test_copy_to_library_unique_naming(tmp: Path, errors: list[str]) -> None:
    print("\n-- _copy_to_library --")
    fn = getattr(bot, "_copy_to_library", None)
    _assert(callable(fn), "_copy_to_library exists", errors)
    if not fn:
        return

    # src: фото в проекте
    src = tmp / "src_photo.jpg"
    _make_png(src)

    # Копия в karting
    dest1 = fn(src, brand="maksim", kind="photo", category="karting")
    _assert(dest1 is not None, "first copy returns Path", errors)
    if dest1:
        _assert(dest1.exists(), f"copy exists ({dest1})", errors)
        _assert(
            "karting" in str(dest1).replace("\\", "/"),
            f"copy lands in karting ({dest1})",
            errors,
        )

    # Повторная копия того же src — уникальное имя, не overwrite
    dest2 = fn(src, brand="maksim", kind="photo", category="karting")
    _assert(dest2 is not None, "second copy returns Path", errors)
    if dest1 and dest2:
        _assert(dest1 != dest2, f"unique names ({dest1.name} vs {dest2.name})", errors)
        _assert(dest1.exists() and dest2.exists(), "both copies exist", errors)

    # category=None → нет копии
    dest3 = fn(src, brand="maksim", kind="photo", category=None)
    _assert(dest3 is None, "category=None → no copy", errors)


# ─── 6. _match_brand_clip_categories ──────────────────────────────────────

def test_match_brand_clip_categories(errors: list[str]) -> None:
    print("\n-- _match_brand_clip_categories('maksim', text) --")
    fn = getattr(bot, "_match_brand_clip_categories", None)
    _assert(callable(fn), "_match_brand_clip_categories exists", errors)
    if not fn:
        return

    m1 = fn("maksim", "Картинг — это адреналин. Гонка с друзьями на треке.")
    _assert("karting" in m1, f"karting matched ({m1})", errors)

    m2 = fn("maksim", "Глэмпинг открыли в 2022, домики и баня.")
    _assert("glamping" in m2, f"glamping matched ({m2})", errors)

    m3 = fn("maksim", "Сапы на озере, флот 140 досок")
    _assert("sup" in m3, f"sup matched ({m3})", errors)

    m4 = fn("maksim", "AI и нейросети меняют бизнес")
    _assert(m4 == [] or "karting" not in m4, f"no AI cross-contamination ({m4})", errors)


# ─── 7. _extract_last_card_from_state ────────────────────────────────────

def test_extract_last_card_from_state(errors: list[str]) -> None:
    print("\n-- _extract_last_card_from_state --")
    fn = getattr(bot, "_extract_last_card_from_state", None)
    _assert(callable(fn), "_extract_last_card_from_state exists", errors)
    if not fn:
        return

    # Активная карточка
    state_active = {
        "notion_page_id": "abc-123-def",
        "card_data": {"title": "Время — единственный ресурс"},
        "state": "broll",
    }
    lc = fn(state_active)
    _assert(lc is not None, "active card → not None", errors)
    if lc:
        _assert(lc["id"] == "abc-123-def", f"id passes through ({lc})", errors)
        _assert("Время" in lc["title"], f"title passes through ({lc['title']!r})", errors)

    # Preserved _last_card slot (после предыдущего /start)
    state_preserved = {"_last_card": {"id": "x-1", "title": "Старая тема"}}
    lc2 = fn(state_preserved)
    _assert(lc2 is not None, "preserved _last_card slot → not None", errors)
    if lc2:
        _assert(lc2["id"] == "x-1", f"preserved id ({lc2})", errors)

    # Пусто
    _assert(fn({}) is None, "empty state → None", errors)
    _assert(fn({"state": "idle"}) is None, "state without notion_page_id → None", errors)


# ─── 8. _build_maksim_start_kb — кнопка «🔄 Продолжить» ──────────────────

def test_build_maksim_start_kb_with_last_card(errors: list[str]) -> None:
    print("\n-- _build_maksim_start_kb(last_card=...) --")
    fn = getattr(bot, "_build_maksim_start_kb", None)
    _assert(callable(fn), "_build_maksim_start_kb exists", errors)
    if not fn:
        return

    kb_none = fn(None)
    rows_none = kb_none.inline_keyboard
    first_label_none = rows_none[0][0].text
    _assert(
        "Продолжить" not in first_label_none,
        f"no last_card → no «Продолжить» first ({first_label_none!r})",
        errors,
    )

    kb_with = fn({"id": "abc-123-def-456", "title": "Время — единственный ресурс"})
    rows_with = kb_with.inline_keyboard
    first_label_with = rows_with[0][0].text
    first_cb = rows_with[0][0].callback_data
    _assert(
        "Продолжить" in first_label_with,
        f"with last_card → first button is «Продолжить» ({first_label_with!r})",
        errors,
    )
    _assert(
        first_cb.startswith("notion_card:"),
        f"callback points to notion_card ({first_cb!r})",
        errors,
    )
    # длина больше чем у kb_none — добавилась строка
    _assert(
        len(rows_with) == len(rows_none) + 1,
        f"+1 row when last_card present ({len(rows_with)} vs {len(rows_none)})",
        errors,
    )


# ─── runner ───────────────────────────────────────────────────────────────

def main() -> int:
    print("=" * 60)
    print("brand library routing tests")
    print("=" * 60)
    errors: list[str] = []

    test_preview_count_is_nine(errors)
    test_brand_photo_library_maksim(errors)
    test_brand_photo_library_default(errors)
    test_brand_clip_library_maksim(errors)
    test_brand_clip_library_default(errors)
    test_resolve_library_target_paths(errors)
    test_copy_to_library_unique_naming(errors)
    test_match_brand_clip_categories(errors)
    test_extract_last_card_from_state(errors)
    test_build_maksim_start_kb_with_last_card(errors)

    print("\n" + "=" * 60)
    if errors:
        print(f"Found {len(errors)} failure(s)")
        for e in errors:
            print(f"  {e}")
        return 1
    print("OK all brand library tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
