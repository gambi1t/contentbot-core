"""Tests for carousel surgical-edit helpers added 26 May 2026.

Покрывает:
- carousel.llm._slides_equal_normalized — нормализованное сравнение JSON-слайдов
  (используется для детекта no-op в apply_carousel_surgical_edit).
- carousel.llm._extract_replace_pattern — парсит инструкцию «X поменяй на Y»
  в кортеж (X, Y) или None.
- bot._clear_carousel_surg_state — очищает залипший state у юзера.

Стиль: тот же что test_brand_library_routing.py — без pytest, main() → 0/1.
Запуск: python tests/test_carousel_surgical_helpers.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

from carousel import llm as carousel_llm  # noqa: E402
import bot  # noqa: E402


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    safe_msg = msg.encode("ascii", "replace").decode("ascii")
    if not cond:
        errors.append(f"FAIL {safe_msg}")
        print(f"  FAIL {safe_msg}")
    else:
        print(f"  OK {safe_msg}")


# ─── 1. _slides_equal_normalized ──────────────────────────────────────────

def test_slides_equal_identical(errors: list[str]) -> None:
    print("\n-- _slides_equal_normalized: identical slides --")
    fn = getattr(carousel_llm, "_slides_equal_normalized", None)
    _assert(callable(fn), "_slides_equal_normalized exists", errors)
    if not fn:
        return
    a = [{"title": "1 СОТРУДНИК КОТОРЫЙ СТОИЛ ДОРОГ", "kicker": "X"}]
    b = [{"title": "1 СОТРУДНИК КОТОРЫЙ СТОИЛ ДОРОГ", "kicker": "X"}]
    _assert(fn(a, b) is True, "identical → True", errors)


def test_slides_equal_whitespace_normalization(errors: list[str]) -> None:
    print("\n-- _slides_equal_normalized: whitespace normalized --")
    fn = getattr(carousel_llm, "_slides_equal_normalized", None)
    if not fn:
        return
    # Двойные пробелы в одном поле должны считаться эквивалентными одиночному.
    a = [{"title": "1 СОТРУДНИК  КОТОРЫЙ СТОИЛ ДОРОГ"}]   # 2 пробела
    b = [{"title": "1 СОТРУДНИК КОТОРЫЙ СТОИЛ ДОРОГ"}]    # 1 пробел
    _assert(fn(a, b) is True, "double-space == single-space → True", errors)


def test_slides_equal_real_change_detected(errors: list[str]) -> None:
    print("\n-- _slides_equal_normalized: real change detected --")
    fn = getattr(carousel_llm, "_slides_equal_normalized", None)
    if not fn:
        return
    a = [{"title": "КОТОРЫЙ СТОИЛ ДОРОГ"}]
    b = [{"title": "КОТОРЫЙ СТОИЛ ДОРОГО"}]    # +1 буква
    _assert(fn(a, b) is False, "ДОРОГ vs ДОРОГО → False", errors)


def test_slides_equal_count_mismatch(errors: list[str]) -> None:
    print("\n-- _slides_equal_normalized: count mismatch --")
    fn = getattr(carousel_llm, "_slides_equal_normalized", None)
    if not fn:
        return
    _assert(fn([{"a": 1}], [{"a": 1}, {"b": 2}]) is False, "len mismatch → False", errors)


# ─── 2. _extract_replace_pattern ─────────────────────────────────────────

def test_extract_replace_pattern_basic(errors: list[str]) -> None:
    print("\n-- _extract_replace_pattern: «X поменяй на Y» --")
    fn = getattr(carousel_llm, "_extract_replace_pattern", None)
    _assert(callable(fn), "_extract_replace_pattern exists", errors)
    if not fn:
        return
    result = fn("1 СОТРУДНИК  КОТОРЫЙ СТОИЛ ДОРОГ поменяй на 1 СОТРУДНИК  КОТОРЫЙ СТОИЛ ДОРОГО")
    _assert(result is not None, "pattern matched → not None", errors)
    if result:
        old, new = result
        _assert("ДОРОГ" in old, f"X contains ДОРОГ ({old!r})", errors)
        _assert("ДОРОГО" in new, f"Y contains ДОРОГО ({new!r})", errors)


def test_extract_replace_pattern_synonyms(errors: list[str]) -> None:
    print("\n-- _extract_replace_pattern: «замени X на Y» --")
    fn = getattr(carousel_llm, "_extract_replace_pattern", None)
    if not fn:
        return
    result = fn("замени ДОРОГ на ДОРОГО")
    _assert(result is not None, "«замени X на Y» matched", errors)
    if result:
        _assert(result[0].strip() == "ДОРОГ", f"X = ДОРОГ ({result[0]!r})", errors)
        _assert(result[1].strip() == "ДОРОГО", f"Y = ДОРОГО ({result[1]!r})", errors)


def test_extract_replace_pattern_no_match(errors: list[str]) -> None:
    print("\n-- _extract_replace_pattern: no match --")
    fn = getattr(carousel_llm, "_extract_replace_pattern", None)
    if not fn:
        return
    _assert(fn("поменяй заголовок 3-го слайда") is None, "non-replace → None", errors)
    _assert(fn("исправь грамматику") is None, "general edit → None", errors)


# ─── 3. _clear_carousel_surg_state ────────────────────────────────────────

def test_clear_carousel_surg_state_cleans(errors: list[str]) -> None:
    print("\n-- _clear_carousel_surg_state: clears залипший state --")
    fn = getattr(bot, "_clear_carousel_surg_state", None)
    _assert(callable(fn), "_clear_carousel_surg_state exists", errors)
    if not fn:
        return
    uid = -999111
    bot.pending[uid] = {"state": "awaiting_carousel_surg_edit", "carousel_template": "M2"}
    fn(uid)
    state_after = bot.pending.get(uid, {}).get("state")
    _assert(state_after != "awaiting_carousel_surg_edit",
            f"surg_edit state cleared (got {state_after!r})", errors)
    # carousel_template НЕ трогаем — это отдельный data, может быть полезен.
    bot.pending.pop(uid, None)


def test_clear_carousel_surg_state_safe_when_other_state(errors: list[str]) -> None:
    print("\n-- _clear_carousel_surg_state: safe when other state --")
    fn = getattr(bot, "_clear_carousel_surg_state", None)
    if not fn:
        return
    uid = -999112
    bot.pending[uid] = {"state": "awaiting_carousel_theme"}
    fn(uid)
    state_after = bot.pending.get(uid, {}).get("state")
    _assert(state_after == "awaiting_carousel_theme",
            "other state untouched", errors)
    bot.pending.pop(uid, None)


# ─── carousel.handlers helpers (PNG persist + draft detect) ──────────────

def test_persist_carousel_pngs_copies(errors: list[str]) -> None:
    print("\n-- _persist_carousel_pngs: copy slides to projects/<id>/carousel/ --")
    from carousel import handlers as carousel_handlers
    fn = getattr(carousel_handlers, "_persist_carousel_pngs", None)
    _assert(callable(fn), "_persist_carousel_pngs exists", errors)
    if not fn:
        return
    import tempfile, shutil as _sh
    src_dir = Path(tempfile.mkdtemp(prefix="src_pngs_"))
    proj_dir = Path(tempfile.mkdtemp(prefix="proj_"))
    try:
        # Подготовим 3 фейковых PNG как «отрендеренные слайды».
        pngs = []
        for i in range(3):
            p = src_dir / f"slide_{i+1:02d}.png"
            # Минимальный PNG (1x1) — переиспользуем _make_png
            data = bytes.fromhex(
                "89504e470d0a1a0a0000000d49484452"
                "00000001000000010806000000"
                "1f15c4890000000d49444154"
                "789c626001000000ffff03000006000557bfabd4"
                "0000000049454e44ae426082"
            )
            p.write_bytes(data)
            pngs.append(p)
        dest_dir = fn(pngs, proj_dir)
        _assert(dest_dir is not None, "returns dest dir", errors)
        if dest_dir:
            copied = sorted(dest_dir.glob("slide_*.png"))
            _assert(len(copied) == 3, f"3 PNGs copied (got {len(copied)})", errors)
            _assert(
                str(dest_dir).replace("\\", "/").endswith("/carousel"),
                f"dest is .../carousel ({dest_dir})", errors,
            )
            # source PNG должны остаться (мы копируем, не двигаем)
            _assert(all(p.exists() for p in pngs), "source PNGs preserved", errors)
    finally:
        _sh.rmtree(src_dir, ignore_errors=True)
        _sh.rmtree(proj_dir, ignore_errors=True)


def test_persist_carousel_pngs_skips_if_no_project(errors: list[str]) -> None:
    print("\n-- _persist_carousel_pngs: nil project_dir → None --")
    from carousel import handlers as carousel_handlers
    fn = getattr(carousel_handlers, "_persist_carousel_pngs", None)
    if not fn:
        return
    _assert(fn([], None) is None, "no project_dir → None", errors)


def test_existing_carousel_for_card_detect(errors: list[str]) -> None:
    print("\n-- _existing_carousel_for_card_detect --")
    from carousel import handlers as carousel_handlers
    fn = getattr(carousel_handlers, "_existing_carousel_for_card_detect", None)
    _assert(callable(fn), "_existing_carousel_for_card_detect exists", errors)
    if not fn:
        return
    # Совпадение notion_url → True
    draft = {"slides": [{}], "notion_url": "https://notion.so/abc-xyz"}
    _assert(
        fn(draft, "https://notion.so/abc-xyz") is True,
        "url match → True", errors,
    )
    # Другой URL → False
    _assert(
        fn(draft, "https://notion.so/other-page") is False,
        "url mismatch → False", errors,
    )
    # Пустой draft → False
    _assert(fn(None, "x") is False, "draft=None → False", errors)
    # Draft без notion_url → False
    _assert(
        fn({"slides": []}, "x") is False,
        "draft без url → False", errors,
    )


# ─── 4. _cover_has_empty_critical_fields ─────────────────────────────────

def test_cover_empty_after_top_strip(errors: list[str]) -> None:
    print("\n-- _cover_has_empty_critical_fields: TOP-poisoned --")
    fn = getattr(carousel_llm, "_cover_has_empty_critical_fields", None)
    _assert(callable(fn), "_cover_has_empty_critical_fields exists", errors)
    if not fn:
        return
    # После strip «ТОП» оба title пусты — критический случай (рендер сломан).
    cover = {"hero": "3", "title_main": "", "title_accent": "", "kicker": "GUIDE"}
    _assert(fn(cover) is True, "empty title_main+title_accent → True", errors)


def test_cover_one_field_empty_still_ok(errors: list[str]) -> None:
    print("\n-- _cover_has_empty_critical_fields: one empty → True (F5) --")
    fn = getattr(carousel_llm, "_cover_has_empty_critical_fields", None)
    if not fn:
        return
    # F5: после ChatGPT review M5 — любое пустое critical поле = poisoned.
    # Раньше срабатывал только при обоих пустых.
    cover = {"hero": "3", "title_main": "ТИПА", "title_accent": "", "kicker": "GUIDE"}
    _assert(fn(cover) is True, "title_accent empty alone → True (F5)", errors)


def test_cover_full_ok(errors: list[str]) -> None:
    print("\n-- _cover_has_empty_critical_fields: full cover --")
    fn = getattr(carousel_llm, "_cover_has_empty_critical_fields", None)
    if not fn:
        return
    cover = {"hero": "3", "title_main": "ТИПА", "title_accent": "КЛИЕНТА", "kicker": "GUIDE"}
    _assert(fn(cover) is False, "full cover → False", errors)


# ─── F1: bot_state — централизованный pending без import bot ─────────────

def test_bot_state_module_exists(errors: list[str]) -> None:
    print("\n-- bot_state module exists --")
    try:
        import bot_state
        _assert(hasattr(bot_state, "pending"), "bot_state.pending exists", errors)
        _assert(hasattr(bot_state, "save_pending"), "bot_state.save_pending exists", errors)
        # Identity: bot.pending должен БЫТЬ тем же объектом что bot_state.pending
        # (а не копией) — иначе late-import bot из carousel снова даст mismatch.
        _assert(bot.pending is bot_state.pending, "bot.pending is bot_state.pending (same obj)", errors)
    except ImportError as e:
        errors.append(f"FAIL import bot_state: {e}")


def test_bot_state_atomic_save(errors: list[str]) -> None:
    print("\n-- bot_state.save_pending: atomic (tmp + rename) --")
    try:
        import bot_state
    except ImportError:
        return
    # Атомарный save: при наличии .tmp файла он либо отсутствует после save,
    # либо переименован. Проверяем что .json существует после save.
    import json as _json
    save_fn = getattr(bot_state, "save_pending", None)
    if not save_fn:
        return
    # Сохраняем небольшой dict, проверяем что файл прочитался обратно
    test_data = {-77777: {"_test_marker": "atomic"}}
    save_fn(test_data)
    loaded = _json.loads(bot_state.PENDING_FILE.read_text(encoding="utf-8"))
    _assert(loaded.get("-77777", {}).get("_test_marker") == "atomic",
            "save+read round-trip works", errors)
    # cleanup — восстанавливаем оригинал
    save_fn(bot_state.pending)


# ─── F2: _extract_surg_error ──────────────────────────────────────────────

def test_extract_surg_error_present(errors: list[str]) -> None:
    print("\n-- _extract_surg_error: present --")
    fn = getattr(carousel_llm, "_extract_surg_error", None)
    _assert(callable(fn), "_extract_surg_error exists", errors)
    if not fn:
        return
    slides = [
        {"title": "X", "_surg_error": "не нашёл: ABC"},
        {"title": "Y"},
    ]
    err = fn(slides)
    _assert(err == "не нашёл: ABC", f"error extracted ({err!r})", errors)
    # _surg_error удалено из первого слайда
    _assert("_surg_error" not in slides[0], "_surg_error removed from slide", errors)


def test_extract_surg_error_absent(errors: list[str]) -> None:
    print("\n-- _extract_surg_error: absent → None --")
    fn = getattr(carousel_llm, "_extract_surg_error", None)
    if not fn:
        return
    slides = [{"title": "X"}, {"title": "Y"}]
    _assert(fn(slides) is None, "no _surg_error → None", errors)


# ─── F3: _slides_equal_normalized игнорит служебные ключи ────────────────

def test_slides_equal_ignores_surg_error(errors: list[str]) -> None:
    print("\n-- _slides_equal_normalized: ignores _surg_error key --")
    fn = getattr(carousel_llm, "_slides_equal_normalized", None)
    if not fn:
        return
    a = [{"title": "X", "kicker": "Y"}]
    b = [{"title": "X", "kicker": "Y", "_surg_error": "не нашёл"}]
    _assert(fn(a, b) is True,
            "equal slides + _surg_error in b → True (ignored)", errors)
    c = [{"title": "X", "kicker": "Y", "_debug": "trace"}]
    _assert(fn(a, c) is True, "_debug also ignored", errors)


# ─── F4: carousel_seed session-объект ────────────────────────────────────

def test_make_carousel_seed_has_session(errors: list[str]) -> None:
    print("\n-- _make_carousel_seed: session_id + created_at --")
    fn = getattr(bot, "_make_carousel_seed", None)
    _assert(callable(fn), "_make_carousel_seed exists", errors)
    if not fn:
        return
    seed = fn(card_id="abc-123", card_url="https://notion.so/abc", text="hello")
    _assert(isinstance(seed.get("session_id"), str) and len(seed["session_id"]) >= 8,
            f"session_id ≥8 chars ({seed.get('session_id')!r})", errors)
    _assert(seed.get("card_id") == "abc-123", "card_id round-trip", errors)
    _assert(seed.get("text") == "hello", "text round-trip", errors)
    _assert("created_at" in seed and isinstance(seed["created_at"], (int, float)),
            "created_at numeric", errors)
    # Два разных seed → разные session_id
    seed2 = fn(card_id="abc-123", card_url="x", text="y")
    _assert(seed["session_id"] != seed2["session_id"], "session_ids unique", errors)


def test_seed_is_stale_ttl(errors: list[str]) -> None:
    print("\n-- _seed_is_stale: TTL 30 min --")
    fn = getattr(bot, "_seed_is_stale", None)
    _assert(callable(fn), "_seed_is_stale exists", errors)
    if not fn:
        return
    import time as _time
    now = _time.time()
    fresh = {"session_id": "a", "created_at": now - 60}  # 60 sec ago
    old = {"session_id": "b", "created_at": now - 60 * 60}  # 1 hour ago
    _assert(fn(fresh) is False, "60s ago → not stale", errors)
    _assert(fn(old) is True, "1h ago → stale", errors)
    _assert(fn({}) is True, "no created_at → stale", errors)
    _assert(fn(None) is True, "None → stale", errors)


# ─── F5: cover any-empty critical (was both-empty) ────────────────────────

def test_cover_any_empty_critical(errors: list[str]) -> None:
    print("\n-- _cover_has_empty_critical_fields: any empty (not both) --")
    fn = getattr(carousel_llm, "_cover_has_empty_critical_fields", None)
    if not fn:
        return
    # ChatGPT M5: если ТОЛЬКО title_main пуст — это ТОЖЕ poisoned.
    one_empty = {"hero": "3", "title_main": "", "title_accent": "ТИПА", "kicker": "GUIDE"}
    _assert(fn(one_empty) is True, "title_main empty alone → True (after F5)", errors)


# ─── heygen_looks: 4 финальных аватара Максима (27 мая 2026) ─────────────

MAKSIM_FINAL_AVATAR_IDS = {
    "b560db700e914b0d9b98889ce6a30b85",  # Студия, чёрная футболка
    "81dfdd09940b41d6b92d00fa7328095a",  # Студия, худи
    "f5e69972c9b5430fbda5fe00b2e4f234",  # Офис, кепка
    "89408fde1ded426dbadee1dbe9357e01",  # Улица, свитер
}


def test_maksim_heygen_looks_exact_four(errors: list[str]) -> None:
    print("\n-- BRANDS[maksim].heygen_looks: ровно 4 финальных id --")
    looks = bot.BRANDS.get("maksim", {}).get("heygen_looks", {})
    ids = {entry.get("id") for entry in looks.values()}
    _assert(
        ids == MAKSIM_FINAL_AVATAR_IDS,
        f"ids match exact set; got={sorted(ids)} expected={sorted(MAKSIM_FINAL_AVATAR_IDS)}",
        errors,
    )
    _assert(
        len(looks) == 4,
        f"exactly 4 looks (got {len(looks)})",
        errors,
    )


def test_maksim_default_avatar_in_looks(errors: list[str]) -> None:
    print("\n-- BRANDS[maksim].heygen_avatar_id ∈ heygen_looks --")
    brand = bot.BRANDS.get("maksim", {})
    default_id = brand.get("heygen_avatar_id")
    looks_ids = {entry.get("id") for entry in (brand.get("heygen_looks") or {}).values()}
    _assert(
        default_id in looks_ids,
        f"default {default_id!r} в looks ({sorted(looks_ids)})",
        errors,
    )


def test_card_to_carousel_force_no_query_data_mutation(errors: list[str]) -> None:
    """Регресс: «🔄 Сделать заново» из C-диалога раньше работало через мутацию
    query.data в PTB CallbackQuery — это НЕ работало (callback тихо умирал).

    Артём 27 May 2026: «нажимаю Сделать заново — не работает».
    Правильное решение — флаг _card_carousel_force без мутации query.data.
    """
    print("\n-- bot.py: card_to_carousel_force без мутации query.data --")
    from pathlib import Path
    src = (Path(__file__).parent.parent / "bot.py").read_text(encoding="utf-8")
    # Старый антипаттерн запрещён
    _assert(
        'query.data = "card_to_carousel:' not in src,
        "no query.data mutation antipattern",
        errors,
    )
    # Новый флаг должен присутствовать
    _assert(
        "_card_carousel_force" in src,
        "force-flag используется",
        errors,
    )
    # C-check должен пропускаться при force
    _assert(
        "not _card_carousel_force" in src,
        "C-check пропускается при force",
        errors,
    )


def test_carousel_cta_points_to_telegram(errors: list[str]) -> None:
    """CTA-слайд (последний) должен звать в Telegram-канал @yumsunov_realbiz,
    а НЕ в Instagram @livedrive.tmn.

    Артём 27 May 2026: «на последнем слайде призыв заходить в какой-то другой
    ссылке» — у Максима основной канал контента = Telegram (@yumsunov_realbiz),
    Instagram @livedrive.tmn только место публикации карусели.
    """
    print("\n-- carousel/llm.py _SYSTEM_PROMPT: CTA → @yumsunov_realbiz --")
    src = carousel_llm._SYSTEM_PROMPT
    # В блоке CTA-слайда должно быть упоминание yumsunov_realbiz
    _assert("yumsunov_realbiz" in src, "_SYSTEM_PROMPT mentions @yumsunov_realbiz", errors)
    # CTA body example НЕ должен призывать к @livedrive.tmn
    # Конкретно body-пример CTA-слайда должен указывать на @yumsunov_realbiz
    # (не на @livedrive.tmn). В остальной части _SYSTEM_PROMPT упоминание
    # @livedrive ОК — там handle поле footer (страница публикации).
    cta_body_marker = 'body = "Подпишись'
    cta_body_pos = src.find(cta_body_marker)
    if cta_body_pos >= 0:
        # Берём строку body целиком до закрывающей кавычки
        cta_body_end = src.find('"\n', cta_body_pos + len(cta_body_marker))
        cta_body_line = src[cta_body_pos:cta_body_end] if cta_body_end > 0 else src[cta_body_pos:cta_body_pos + 250]
        _assert(
            "@yumsunov_realbiz" in cta_body_line,
            f"CTA body line points to @yumsunov_realbiz ({cta_body_line[:120]!r})",
            errors,
        )
        _assert(
            "@livedrive" not in cta_body_line,
            f"CTA body line no @livedrive ({cta_body_line[:120]!r})",
            errors,
        )


def test_renderer_no_hardcoded_top_word(errors: list[str]) -> None:
    """Регресс: рендер cover M2 не должен дописывать слово «ТОП» в HTML.

    27 May 2026: Артём поймал «3 ТОП ТИПА» на cover. Cover JSON в pending
    был чистый (без ТОП во всех полях), но renderer.py:186 hardcoded
    <div class="top">ТОП</div> в HTML M2 cover. Все strip/retry/prompt
    фиксы били не туда — данные были корректны.

    Удалено. Тест прибит чтобы регресс не вернулся.
    """
    print("\n-- renderer.py: нет hardcoded ТОП в render_m2 --")
    from pathlib import Path
    renderer_src = (Path(__file__).parent.parent / "carousel" / "renderer.py").read_text(encoding="utf-8")
    # Конкретный паттерн который был — `<div class="top">ТОП`
    bad = '<div class="top">ТОП'
    _assert(bad not in renderer_src, f"renderer hardcoded {bad!r}", errors)


def test_maksim_no_legacy_avatar_ids_in_code(errors: list[str]) -> None:
    """Снятые аватары не должны фигурировать в активных конфигурациях.

    Допустимо упоминание ТОЛЬКО в комментариях — но для простоты теста
    проверяем что они отсутствуют в самих values BRANDS["maksim"].
    """
    print("\n-- BRANDS[maksim]: нет снятых аватаров a0bddf71/f3a502ab/90610f1a --")
    LEGACY = {"a0bddf71", "f3a502ab", "90610f1a"}
    brand = bot.BRANDS.get("maksim", {})
    # Превращаем в строку весь dict — простой sanity check.
    import json
    blob = json.dumps(
        {k: v for k, v in brand.items() if isinstance(v, (str, dict, list, int, float, type(None)))},
        ensure_ascii=False,
    )
    found = [lid for lid in LEGACY if lid in blob]
    _assert(not found, f"legacy ids not in brand config (found: {found})", errors)


# ─── A: PNG publish to nginx media + Notion blocks (28 May 2026) ────────

def test_carousel_media_path_for_maksim(errors: list[str]) -> None:
    print("\n-- _carousel_media_path_for_brand(maksim) --")
    from carousel import handlers as carousel_handlers
    fn = getattr(carousel_handlers, "_carousel_media_path_for_brand", None)
    _assert(callable(fn), "_carousel_media_path_for_brand exists", errors)
    if not fn:
        return
    path = fn("maksim")
    _assert(
        path is not None and str(path).replace("\\", "/").endswith("/srv/bot-media-maksim"),
        f"maksim → /srv/bot-media-maksim (got {path})",
        errors,
    )
    # default бренд → None (карусель только для maksim)
    _assert(fn("default") is None, "default → None", errors)


def test_carousel_media_url_base_for_maksim(errors: list[str]) -> None:
    print("\n-- _carousel_media_url_base_for_brand(maksim) --")
    from carousel import handlers as carousel_handlers
    fn = getattr(carousel_handlers, "_carousel_media_url_base_for_brand", None)
    _assert(callable(fn), "_carousel_media_url_base_for_brand exists", errors)
    if not fn:
        return
    base = fn("maksim")
    _assert(
        base == "https://maksim-bot.panferov-ai.ru/media",
        f"maksim → https://…/media (got {base!r})",
        errors,
    )


def test_build_carousel_notion_blocks(errors: list[str]) -> None:
    print("\n-- _build_carousel_notion_blocks(urls, n) --")
    from carousel import handlers as carousel_handlers
    fn = getattr(carousel_handlers, "_build_carousel_notion_blocks", None)
    _assert(callable(fn), "_build_carousel_notion_blocks exists", errors)
    if not fn:
        return
    urls = [f"https://x/slide_{i:02d}.png" for i in range(1, 8)]
    blocks = fn(urls)
    # Должен быть heading_2 + 7 image-блоков + divider в конце
    _assert(len(blocks) >= 8, f"≥8 blocks (heading + 7 images) — got {len(blocks)}", errors)
    _assert(
        blocks[0].get("type") == "heading_2",
        f"first block heading_2 (got {blocks[0].get('type')})",
        errors,
    )
    image_blocks = [b for b in blocks if b.get("type") == "image"]
    _assert(len(image_blocks) == 7, f"7 image blocks (got {len(image_blocks)})", errors)
    # Каждый image — external url
    for i, b in enumerate(image_blocks):
        img = b.get("image", {})
        _assert(
            img.get("type") == "external" and img.get("external", {}).get("url") == urls[i],
            f"image #{i+1} external URL matches ({img.get('external', {}).get('url')!r})",
            errors,
        )


def test_carousel_notion_heading_marker(errors: list[str]) -> None:
    """Heading должен иметь стабильный маркер для поиска и удаления старых блоков
    при re-render. Не меняется между версиями."""
    print("\n-- _CAROUSEL_NOTION_HEADING_MARKER stable --")
    from carousel import handlers as carousel_handlers
    marker = getattr(carousel_handlers, "_CAROUSEL_NOTION_HEADING_MARKER", None)
    _assert(isinstance(marker, str) and len(marker) >= 8, f"marker is string ≥8 chars ({marker!r})", errors)
    # Маркер должен попадать в текст heading при build
    fn = getattr(carousel_handlers, "_build_carousel_notion_blocks", None)
    if fn and marker:
        blocks = fn(["https://x/slide_01.png"])
        heading_text = ""
        try:
            heading_text = blocks[0]["heading_2"]["rich_text"][0]["text"]["content"]
        except (KeyError, IndexError):
            pass
        _assert(marker in heading_text, f"marker in heading text ({heading_text!r})", errors)


# ─── runner ───────────────────────────────────────────────────────────────

def main() -> int:
    print("=" * 60)
    print("carousel surgical helpers tests")
    print("=" * 60)
    errors: list[str] = []

    test_slides_equal_identical(errors)
    test_slides_equal_whitespace_normalization(errors)
    test_slides_equal_real_change_detected(errors)
    test_slides_equal_count_mismatch(errors)
    test_extract_replace_pattern_basic(errors)
    test_extract_replace_pattern_synonyms(errors)
    test_extract_replace_pattern_no_match(errors)
    test_clear_carousel_surg_state_cleans(errors)
    test_clear_carousel_surg_state_safe_when_other_state(errors)
    test_cover_empty_after_top_strip(errors)
    test_cover_one_field_empty_still_ok(errors)
    test_cover_full_ok(errors)
    test_persist_carousel_pngs_copies(errors)
    test_persist_carousel_pngs_skips_if_no_project(errors)
    test_existing_carousel_for_card_detect(errors)
    test_bot_state_module_exists(errors)
    test_bot_state_atomic_save(errors)
    test_extract_surg_error_present(errors)
    test_extract_surg_error_absent(errors)
    test_slides_equal_ignores_surg_error(errors)
    test_make_carousel_seed_has_session(errors)
    test_seed_is_stale_ttl(errors)
    test_cover_any_empty_critical(errors)
    test_maksim_heygen_looks_exact_four(errors)
    test_maksim_default_avatar_in_looks(errors)
    test_maksim_no_legacy_avatar_ids_in_code(errors)
    test_renderer_no_hardcoded_top_word(errors)
    test_carousel_cta_points_to_telegram(errors)
    test_card_to_carousel_force_no_query_data_mutation(errors)
    test_carousel_media_path_for_maksim(errors)
    test_carousel_media_url_base_for_maksim(errors)
    test_build_carousel_notion_blocks(errors)
    test_carousel_notion_heading_marker(errors)

    print("\n" + "=" * 60)
    if errors:
        print(f"Found {len(errors)} failure(s)")
        for e in errors:
            print(f"  {e}")
        return 1
    print("OK all carousel surgical tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
