"""TDD: B-roll Pipeline 2 — гейт 6: публикация (мост в карточный публикатор).

Артём: довести Pipeline 2 до паритета. Гейт 6 — финал-экран публикации, как у
селфи/аватара (📝 Описание / 📰 TG-пост / 📢 Кросс-постинг). Эталонный
публикатор карточко-центричен: тянет видео/сценарий из папки проекта
projects/{nid[:8]}_{title} и из pending[uid]. B-roll туда НЕ пишет → не виден.

Дизайн A (после CTO-ревью + верификации кода): НЕ строить второй публикатор, а
сделать B-roll-финал видимым для существующего движка и переиспользовать его
callbacks (gen_description / tgpost_from_script / crosspost:) as-is.

Мост `bridge_broll_to_publication`:
  • atomic-копия финала → proj/final_video.mp4 (+ script.txt) — _find_video_for_card подхватит;
  • генерит стилизованный TG-пост (DI tg_post_fn=rewrite_for_telegram) → pending['selfie_tg_post']
    (Артём: «нормальный пост везде», а не сырой транскрипт);
  • merge-seed pending[uid] (setdefault().update — не затирая живой state) + brand/pipeline.
Биллинг-паритет (Артём: «платный»): на успешной доставке register_video + charge
(trigger=download_final), идемпотентно по notion_page_id (с кросспостом не задвоится).
Заголовок карточки — единый детерминированный источник _broll_card_data(theme)['title'].

Запуск: python tests/test_broll_publish.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
os.environ.setdefault("TELEGRAM_TOKEN", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import bot_state  # noqa: E402
import broll.handlers as bh  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def _cbs(markup_or_rows):
    rows = getattr(markup_or_rows, "inline_keyboard", markup_or_rows) or []
    return [getattr(b, "callback_data", None) for row in rows for b in row]


NID = "35b6889c-1111-2222-3333-444455556666"


def _mkfinal(content=b"FAKEMP4DATA" * 200):
    f = Path(tempfile.mkdtemp(prefix="broll_pub_final_")) / "subbed.mp4"
    f.write_bytes(content)
    return f


def _draft(final, **extra):
    d = {"script": "Сценарий про зимний картинг и ошибки новичков.",
         "theme": "Три ошибки новичков в картинге",
         "chat_id": 42, "notion_page_id": NID, "final_path": str(final)}
    d.update(extra); return d


async def _tg_post_fn(script, desc, topic):
    return f"<b>{topic}</b>\n\nДлинный стилизованный пост по сценарию."


# ── Тесты ────────────────────────────────────────────────────────────

def test_publication_title_single_source(errors):
    print("\n[_publication_title — единый источник = _broll_card_data title]")
    theme = "Три ошибки новичков в картинге"
    _assert(bh._publication_title(theme) == bh._broll_card_data(theme)["title"],
            "заголовок == _broll_card_data(theme)['title'] (один источник)", errors)
    _assert(len(bh._publication_title("x" * 200)) <= 80, "заголовок обрезан до 80 (как карточка)", errors)


def test_publish_action_buttons_reuse_existing_callbacks(errors):
    print("\n[_publish_action_buttons — реюз callbacks эталона, без новых b2pub]")
    cbs = _cbs(bh._publish_action_buttons(NID))
    _assert("gen_description" in cbs, "📝 Описание → существующий gen_description", errors)
    _assert("tgpost_from_script" in cbs, "📰 TG-пост → существующий tgpost_from_script", errors)
    _assert(f"crosspost:{NID[:20]}" in cbs, f"📢 Кросс-постинг → crosspost:{{nid[:20]}}: {cbs}", errors)
    _assert(not any(str(c).startswith("b2pub") for c in cbs),
            "НЕ изобретаем b2pub (ноль новых веток bot.py)", errors)


def test_bridge_persists_video_and_script(errors):
    print("\n[bridge — atomic-копия final_video.mp4 + script.txt в папку проекта]")
    bot_state.PROJECTS_DIR = Path(tempfile.mkdtemp(prefix="broll_pub_projects_"))
    final = _mkfinal()
    bh._bot_pending = {}; bh._bot_save_pending = lambda *a, **k: None
    draft = _draft(final)
    ok = asyncio.run(bh.bridge_broll_to_publication(draft, uid=7, tg_post_fn=_tg_post_fn))
    _assert(ok is True, "мост успешен → publish-кнопки можно показывать", errors)
    title = bh._publication_title(draft["theme"])
    proj = bot_state.project_dir({"notion_page_id": NID, "card_data": {"title": title}})
    vid = proj / "final_video.mp4"
    _assert(vid.exists() and vid.read_bytes() == final.read_bytes(),
            "final_video.mp4 в папке проекта, контент совпал", errors)
    _assert((proj / "script.txt").read_text(encoding="utf-8") == draft["script"],
            "script.txt записан", errors)
    _assert(not list(proj.glob("*.part")), "atomic: .part не остался", errors)


def test_bridge_find_video_consistency(errors):
    print("\n[bridge — _find_video_for_card(seeded data) находит наш файл (title-consistency)]")
    bot_state.PROJECTS_DIR = Path(tempfile.mkdtemp(prefix="broll_pub_projects2_"))
    final = _mkfinal()
    bh._bot_pending = {}; bh._bot_save_pending = lambda *a, **k: None
    draft = _draft(final)
    asyncio.run(bh.bridge_broll_to_publication(draft, uid=7, tg_post_fn=_tg_post_fn))
    # эмулируем резолв эталона: project_dir(seeded card_data) → final_video.mp4
    seeded = bh._bot_pending[7]
    proj = bot_state.project_dir({"notion_page_id": seeded["notion_page_id"],
                                  "card_data": seeded["card_data"]})
    _assert((proj / "final_video.mp4").exists(),
            "persist-title == seed-title → видео находится по seeded data", errors)


def test_bridge_merge_seeds_pending(errors):
    print("\n[bridge — merge-seed pending (не затирая живой state) + brand/pipeline + tg_post]")
    bot_state.PROJECTS_DIR = Path(tempfile.mkdtemp(prefix="broll_pub_projects3_"))
    final = _mkfinal()
    saved = {"n": 0}
    bh._bot_pending = {7: {"keepme": "до моста", "state": "что-то_живое"}}
    bh._bot_save_pending = lambda *a, **k: saved.__setitem__("n", saved["n"] + 1)
    draft = _draft(final)
    asyncio.run(bh.bridge_broll_to_publication(draft, uid=7, tg_post_fn=_tg_post_fn))
    st = bh._bot_pending[7]
    _assert(st.get("keepme") == "до моста", "существующий ключ pending сохранён (merge, не overwrite)", errors)
    _assert(st.get("notion_page_id") == NID, "seed notion_page_id", errors)
    _assert(st.get("card_data", {}).get("title") == bh._publication_title(draft["theme"]), "seed card_data.title", errors)
    _assert(st.get("script") == draft["script"], "seed script", errors)
    _assert(st.get("crosspost_card_id") == NID[:20], "seed crosspost_card_id = nid[:20]", errors)
    _assert(st.get("brand") == "maksim", "seed brand=maksim (защита от мультибренда)", errors)
    _assert(st.get("pipeline") == "broll", "seed pipeline=broll (маркер/диагностика)", errors)
    _assert(st.get("selfie_tg_post", "").startswith("<b>"), "seed selfie_tg_post (нормальный пост, не транскрипт)", errors)
    _assert(saved["n"] >= 1, "save_pending вызван", errors)


def test_bridge_guards(errors):
    print("\n[bridge — без notion_page_id / без final → False, не падает]")
    bot_state.PROJECTS_DIR = Path(tempfile.mkdtemp(prefix="broll_pub_projects4_"))
    bh._bot_pending = {}; bh._bot_save_pending = lambda *a, **k: None
    final = _mkfinal()
    _assert(asyncio.run(bh.bridge_broll_to_publication(_draft(final, notion_page_id=None), uid=7)) is False,
            "нет notion_page_id → False (publish-кнопки не показываем)", errors)
    d = _draft(final); d["final_path"] = ""
    _assert(asyncio.run(bh.bridge_broll_to_publication(d, uid=7)) is False, "нет final → False", errors)


def test_bridge_no_tg_fn_ok(errors):
    print("\n[bridge — без tg_post_fn мост всё равно работает (TG-пост опционален)]")
    bot_state.PROJECTS_DIR = Path(tempfile.mkdtemp(prefix="broll_pub_projects5_"))
    final = _mkfinal()
    bh._bot_pending = {}; bh._bot_save_pending = lambda *a, **k: None
    ok = asyncio.run(bh.bridge_broll_to_publication(_draft(final), uid=7, tg_post_fn=None))
    _assert(ok is True, "без tg_post_fn — мост успешен", errors)
    _assert("selfie_tg_post" not in bh._bot_pending[7], "selfie_tg_post не сидируется без fn (фолбэк эталона)", errors)


def test_charge_billing_parity(errors):
    print("\n[_charge_broll_publication — register → charge(download_final), идемпотентно]")
    calls = []
    async def _reg(uid, nid, title): calls.append(("register", uid, nid, title))
    async def _chg(uid, nid, trigger): calls.append(("charge", uid, nid, trigger))
    asyncio.run(bh._charge_broll_publication(7, NID, "Заголовок", register_fn=_reg, charge_fn=_chg))
    _assert(calls and calls[0] == ("register", 7, NID, "Заголовок"), "сначала register_video(uid,nid,title)", errors)
    _assert(("charge", 7, NID, "download_final") in calls, "затем charge trigger=download_final (паритет с финалом селфи)", errors)
    _assert(calls.index(("register", 7, NID, "Заголовок")) < calls.index(("charge", 7, NID, "download_final")),
            "порядок: register ДО charge (иначе video_not_found)", errors)


def test_charge_guards(errors):
    print("\n[_charge_broll_publication — без nid / без fn не падает, не списывает]")
    # без nid — ничего не зовём
    calls = []
    async def _chg(uid, nid, trigger): calls.append(trigger)
    asyncio.run(bh._charge_broll_publication(7, None, "t", register_fn=None, charge_fn=_chg))
    _assert(not calls, "без notion_page_id — charge не зовём", errors)
    # без fn — не падает
    try:
        asyncio.run(bh._charge_broll_publication(7, NID, "t", register_fn=None, charge_fn=None))
        _assert(True, "без DI-функций — не падает (биллинг не ломает поток)", errors)
    except Exception as e:
        _assert(False, f"упал без DI: {e}", errors)


def test_bot_wiring(errors):
    print("\n[bot.py — DI-проводка register/charge/tg_post в assemble]")
    src = (Path(__file__).parent.parent / "bot.py").read_text(encoding="utf-8")
    _assert("_broll_register_video" in src, "хелпер _broll_register_video (mirror register-паттерна)", errors)
    _assert("_broll_tg_post" in src, "хелпер _broll_tg_post (rewrite_for_telegram + claude)", errors)
    _assert("register_fn=" in src and "charge_fn=" in src, "register_fn/charge_fn переданы в assemble", errors)
    _assert("tg_post_fn=" in src, "tg_post_fn передан в assemble", errors)


def test_assemble_forwards_kwargs(errors):
    print("\n[handlers — assemble/accept_voiceover принимают новые DI-kwargs]")
    import inspect
    for fn in (bh.assemble_broll_from_draft, bh.accept_broll_voiceover):
        params = inspect.signature(fn).parameters
        for kw in ("register_fn", "charge_fn", "tg_post_fn"):
            _assert(kw in params, f"{fn.__name__} принимает {kw}", errors)


def main():
    errors = []
    bh.DRAFTS_DIR = Path(tempfile.mkdtemp(prefix="broll_pub_test_"))
    bh._bot_pending = {}; bh._bot_save_pending = lambda *a, **k: None
    test_publication_title_single_source(errors)
    test_publish_action_buttons_reuse_existing_callbacks(errors)
    test_bridge_persists_video_and_script(errors)
    test_bridge_find_video_consistency(errors)
    test_bridge_merge_seeds_pending(errors)
    test_bridge_guards(errors)
    test_bridge_no_tg_fn_ok(errors)
    test_charge_billing_parity(errors)
    test_charge_guards(errors)
    test_bot_wiring(errors)
    test_assemble_forwards_kwargs(errors)
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
