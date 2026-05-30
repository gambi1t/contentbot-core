"""Telegram handlers for carousel pipeline — 2-phase flow.

Phase 1 (preview):  theme → Opus → script preview as text + approve/regen/cancel buttons.
Phase 2 (render):    user approves → Playwright renders PNGs → media_group.

The draft (LLM JSON output + theme + n_slides) is stored in `context.user_data
['carousel_draft']` between phases. PTB's user_data is in-memory per user;
loses state on bot restart, but that's fine for short-lived preview/approve
windows (~minutes).

Callbacks (registered in bot.py:handle_callback):
    carousel_approve  → render_carousel_from_draft (phase 2)
    carousel_regen    → generate_carousel_preview again with same theme
    carousel_cancel   → drop draft, message «отменено»
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Update,
)
from telegram.ext import ContextTypes

from . import broll as carousel_broll
from . import llm as carousel_llm
from . import renderer as carousel_renderer

logger = logging.getLogger(__name__)


# ─── Persistent draft storage (F1 fix, 26 мая 2026) ──────────────────────
# Раньше: late-import `bot` внутри `_pending_io` → когда bot.py запущен как
# `python bot.py` (под __main__), `import bot` грузил файл второй раз →
# два экземпляра `pending`, тихие расхождения. Симптом: carousel.llm logger
# не попадал в journalctl (второй module instance, свой logger handler).
#
# Решение: pending живёт в bot_state.py, импортируется один раз и в bot.py,
# и здесь. Python кэширует module в sys.modules — повторных загрузок нет.
from bot_state import pending, save_pending


def _user_id_from_update(update: Update) -> int:
    """Извлечь user_id из любого типа update (callback or message)."""
    if update.callback_query and update.callback_query.from_user:
        return update.callback_query.from_user.id
    if update.effective_user:
        return update.effective_user.id
    raise RuntimeError("cannot resolve user_id from update")


def _load_carousel_draft(user_id: int) -> dict | None:
    return (pending.get(user_id) or {}).get("carousel_draft")


def _save_carousel_draft(user_id: int, draft: dict) -> None:
    pending.setdefault(user_id, {})["carousel_draft"] = draft
    save_pending(pending)


def _drop_carousel_draft(user_id: int) -> None:
    p = pending.get(user_id)
    if p and "carousel_draft" in p:
        del p["carousel_draft"]
        save_pending(pending)


def _persist_carousel_pngs(png_paths, project_dir):
    """Копирует PNG-слайды карусели из temp-папки в `<project_dir>/carousel/`.

    Раньше PNG жили только в /tmp/ и пропадали после рестарта/cleanup OS.
    Теперь — в проекте карточки, попадают в zip-архив, переживают рестарт.
    Если project_dir = None — возвращает None (карусель не из карточки).
    """
    if not project_dir or not png_paths:
        return None
    import shutil as _sh
    from pathlib import Path as _Path
    dest_dir = _Path(project_dir) / "carousel"
    dest_dir.mkdir(parents=True, exist_ok=True)
    for src in png_paths:
        src_p = _Path(src)
        if not src_p.exists():
            continue
        dest = dest_dir / src_p.name
        try:
            _sh.copy2(str(src_p), str(dest))
        except Exception as e:
            logger.warning(f"[carousel] persist PNG failed for {src_p.name}: {e}")
    return dest_dir


# ─── A: Publish PNG via nginx + Notion (28 May 2026) ─────────────────────
# nginx config (см. /etc/nginx/sites-available/maksim-bot.panferov-ai.ru):
#   /media/ → /srv/bot-media-maksim/
# Кладём PNG в /srv/bot-media-maksim/carousel/<card_id_short>/slide_NN.png
# → отдаётся https://maksim-bot.panferov-ai.ru/media/carousel/<id>/slide_NN.png
# → Notion вставляет image-блок с этим external URL прямо в страницу карточки.

_CAROUSEL_MEDIA_ROOT_BY_BRAND = {
    "maksim": Path("/srv/bot-media-maksim"),
}
_CAROUSEL_URL_BASE_BY_BRAND = {
    "maksim": "https://maksim-bot.panferov-ai.ru/media",
}
_CAROUSEL_NOTION_HEADING_MARKER = "🎨 Карусель"


def _carousel_media_path_for_brand(brand: str | None):
    """Корень media-папки для бренда. None если для бренда не настроено."""
    if not brand:
        return None
    return _CAROUSEL_MEDIA_ROOT_BY_BRAND.get(brand)


def _carousel_media_url_base_for_brand(brand: str | None) -> str | None:
    """Базовый URL nginx-раздачи для бренда (без trailing slash)."""
    if not brand:
        return None
    return _CAROUSEL_URL_BASE_BY_BRAND.get(brand)


def _publish_carousel_pngs_to_media(
    png_paths,
    card_id_short: str,
    brand: str,
) -> list[str]:
    """Копирует PNG в nginx-media папку, возвращает list of public URLs.

    Возвращает [] если бренд не настроен или ошибка копирования.
    Имена сохраняются (slide_NN.png).
    """
    media_root = _carousel_media_path_for_brand(brand)
    url_base = _carousel_media_url_base_for_brand(brand)
    if not media_root or not url_base or not png_paths:
        return []
    import shutil as _sh
    target_dir = media_root / "carousel" / card_id_short
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning(f"[carousel/media] mkdir failed: {e}")
        return []
    urls: list[str] = []
    for src in png_paths:
        src_p = Path(src)
        if not src_p.exists():
            continue
        dest = target_dir / src_p.name
        try:
            _sh.copy2(str(src_p), str(dest))
            urls.append(f"{url_base}/carousel/{card_id_short}/{src_p.name}")
        except Exception as e:
            logger.warning(f"[carousel/media] copy failed for {src_p.name}: {e}")
    return urls


def _build_carousel_notion_blocks(image_urls: list[str]) -> list[dict]:
    """Список Notion-блоков для добавления карусели в страницу карточки.

    Структура: heading_2 (с MARKER) + N image-блоков (external URL) + divider.
    Marker нужен для поиска и удаления старых блоков при re-render.
    """
    n = len(image_urls)
    heading_content = f"{_CAROUSEL_NOTION_HEADING_MARKER} — {n} слайдов"
    blocks: list[dict] = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": heading_content}}],
            },
        },
    ]
    for url in image_urls:
        blocks.append({
            "object": "block",
            "type": "image",
            "image": {"type": "external", "external": {"url": url}},
        })
    blocks.append({"object": "block", "type": "divider", "divider": {}})
    return blocks


def _delete_old_carousel_blocks_from_notion(notion_client, page_id: str) -> int:
    """Найти и удалить старые carousel-блоки (heading + следующие image/divider).

    Поиск по marker в heading_2. Возвращает количество удалённых блоков.
    Делает один pass — если в странице несколько старых каруселей, чистит все.
    """
    deleted = 0
    try:
        resp = notion_client.blocks.children.list(block_id=page_id, page_size=100)
        children = resp.get("results", [])
    except Exception as e:
        logger.warning(f"[carousel/notion] list children failed: {e}")
        return 0
    # Линейный проход: при каждом heading_2 с marker → удаляем его +
    # следующие подряд image/divider пока не встретим другой heading или конец.
    in_carousel_section = False
    for blk in children:
        btype = blk.get("type")
        if btype == "heading_2":
            heading_text = ""
            try:
                heading_text = blk["heading_2"]["rich_text"][0]["text"]["content"]
            except (KeyError, IndexError):
                pass
            in_carousel_section = _CAROUSEL_NOTION_HEADING_MARKER in heading_text
            if in_carousel_section:
                try:
                    notion_client.blocks.delete(block_id=blk["id"])
                    deleted += 1
                except Exception as e:
                    logger.warning(f"[carousel/notion] delete heading failed: {e}")
        elif in_carousel_section and btype in ("image", "divider"):
            try:
                notion_client.blocks.delete(block_id=blk["id"])
                deleted += 1
            except Exception as e:
                logger.warning(f"[carousel/notion] delete {btype} failed: {e}")
        else:
            # Любой другой block-type закрывает carousel-секцию.
            in_carousel_section = False
    return deleted


def _existing_carousel_for_card_detect(draft, notion_url) -> bool:
    """True если в pending уже есть draft карусели для этой же карточки.

    Используется в card_to_carousel — перед перезаписью спрашиваем юзера
    «Открыть существующий / Заново». notion_url — из card.url.
    """
    if not draft or not notion_url:
        return False
    return draft.get("notion_url") == notion_url


def _build_script_preview(slides: list[dict]) -> str:
    """Format slide JSON list as a readable script preview for Telegram.

    Each slide is rendered as 1-3 lines that the user can quickly scan to
    approve the narrative arc before committing to a 15-20 sec PNG render.
    HTML-formatted for Telegram (parse_mode='HTML').
    """
    import html as html_mod

    def esc(s: str | None) -> str:
        return html_mod.escape(s or "", quote=False)

    n = len(slides)
    parts: list[str] = [f"📝 <b>Сценарий карусели</b> — {n} слайдов\n"]

    # Cover
    cover = slides[0]
    title = (cover.get("title_main") or "") + " " + (cover.get("title_accent") or "")
    parts.append(
        f"<b>[1/{n}] COVER</b>  ·  {esc(cover.get('kicker'))}\n"
        f"   <b>{esc(cover.get('hero'))} {esc(cover.get('hero_word') or '')}</b>  "
        f"<i>{esc(title.strip())}</i>\n"
        f"   <i>{esc(cover.get('subtitle'))}</i>"
    )

    # Inner slides — тип C показываем как цитату (pull_quote), A/B — title+body
    for i, sl in enumerate(slides[1:-1], start=2):
        stype = (sl.get("slide_type") or "").upper()
        if stype == "C" and sl.get("pull_quote"):
            parts.append(
                f"<b>[{i}/{n}]</b>  ·  {esc(sl.get('kicker') or 'ВЫВОД')}\n"
                f"   «<i>{esc(sl.get('pull_quote'))}</i>»"
            )
        else:
            parts.append(
                f"<b>[{i}/{n}]</b>  ·  {esc(sl.get('kicker'))}\n"
                f"   <b>{esc(sl.get('title'))}</b>\n"
                f"   <i>{esc(sl.get('body'))}</i>"
            )

    # CTA (last)
    if n >= 3:
        cta = slides[-1]
        parts.append(
            f"<b>[{n}/{n}] CTA</b>  ·  {esc(cta.get('kicker'))}\n"
            f"   <b>{esc(cta.get('title'))}</b>\n"
            f"   <i>{esc(cta.get('body'))}</i>"
        )

    return "\n\n".join(parts)


def _approval_keyboard(notion_url: str | None = None) -> InlineKeyboardMarkup:
    """Preview-approval keyboard.

    Две кнопки правки:
    - «✏️ Точечная правка» — Sonnet меняет только указанное (carousel_surg_edit)
    - «🔄 Переписать полностью» — Opus генерит карусель заново (carousel_regen)
    """
    rows = [
        [InlineKeyboardButton("✅ Делаем PNG", callback_data="carousel_approve")],
        [InlineKeyboardButton("✏️ Точечная правка", callback_data="carousel_surg_edit")],
        [InlineKeyboardButton("🔄 Переписать полностью", callback_data="carousel_regen")],
    ]
    if notion_url:
        rows.append([InlineKeyboardButton("📋 К карточке", url=notion_url)])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="carousel_cancel")])
    return InlineKeyboardMarkup(rows)


async def generate_carousel_preview(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    claude,
    theme: str,
    n_slides: int | None = None,
    chat_id: int | None = None,
    notion_url: str | None = None,
    template: str | None = None,    # "M1" / "M2" / None (let LLM pick)
    seed_card_id: str | None = None,  # card_id из карточки (для PNG-persist + status-update)
) -> None:
    """Phase 1: generate JSON via Opus, show script preview with approve buttons.

    Stores draft in `context.user_data["carousel_draft"]` for phase 2.
    """
    if chat_id is None:
        q = update.callback_query
        chat_id = q.message.chat_id if q else update.effective_chat.id

    if n_slides is None:
        n_slides = carousel_llm.infer_n_slides(theme, fallback=7)

    status = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🎨 Готовлю сценарий: {n_slides} слайдов в стиле Life Drive\n"
            f"<i>Тема:</i> {theme[:140]}\n\n"
            f"⏳ ~15-25 секунд (Opus пишет сценарий)"
        ),
        parse_mode="HTML",
    )

    try:
        slides = await asyncio.to_thread(
            carousel_llm.generate_carousel,
            claude, theme, n_slides,
            "claude-opus-4-7", 5000, template,
        )
        logger.info(
            f"[carousel] preview generated: {len(slides)} slides, "
            f"cover template = {slides[0].get('template')}, "
            f"template_hint = {template}"
        )
    except Exception as e:
        logger.error(f"[carousel] LLM failed: {e}", exc_info=True)
        try:
            await status.edit_text(
                f"❌ Генерация контента упала: {e}\n\nПопробуй ещё раз.",
            )
        except Exception:
            pass
        return

    # Store draft for phase 2 — persistent (bot.pending), переживает рестарт.
    _save_carousel_draft(_user_id_from_update(update), {
        "slides": slides,
        "theme": theme,
        "n_slides": n_slides,
        "notion_url": notion_url,
        "chat_id": chat_id,
        "template": template,
        "seed_card_id": seed_card_id,
    })

    preview_text = _build_script_preview(slides)
    preview_text += (
        "\n\n━━━━━━━━━━━━━━━━━━━━━\n"
        "👀 Согласуй сценарий: жми <b>«Делаем PNG»</b> чтобы запустить рендеринг "
        "(15-20 сек), <b>«Переписать сценарий»</b> чтобы Opus сгенерил заново, "
        "или <b>«Отмена»</b>."
    )

    try:
        await status.delete()
    except Exception:
        pass

    # Telegram limit 4096 — preview for 7-10 slides can be ~3000-3500 chars, fine
    if len(preview_text) > 4000:
        preview_text = preview_text[:3900] + "\n\n<i>… (обрезано)</i>"

    await context.bot.send_message(
        chat_id=chat_id,
        text=preview_text,
        parse_mode="HTML",
        reply_markup=_approval_keyboard(notion_url),
        disable_web_page_preview=True,
    )


async def render_carousel_from_draft(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int | None = None,
) -> None:
    """Phase 2: read draft from user_data, render PNGs, send media_group.

    Idempotent: clears draft from user_data on success or hard failure.
    """
    uid = _user_id_from_update(update)
    draft = _load_carousel_draft(uid)
    if not draft:
        await context.bot.send_message(
            chat_id=chat_id or update.effective_chat.id,
            text="⚠️ Сценарий потерян. Сделай карусель заново.",
        )
        return

    slides = draft["slides"]
    theme = draft.get("theme", "")
    notion_url = draft.get("notion_url")
    if chat_id is None:
        chat_id = draft.get("chat_id") or update.effective_chat.id

    status = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🖼 Рендерю {len(slides)} PNG через Playwright…\n"
            f"<i>~15-25 сек</i>"
        ),
        parse_mode="HTML",
    )

    # Pick B-roll background photos for inner slides (slides[1:]).
    # Graceful: if archive unavailable → [] → renderer falls back to plain.
    try:
        bg_photos = carousel_broll.pick_background_photos(
            theme, count=max(0, len(slides) - 1),
        )
        logger.info(f"[carousel] picked {len(bg_photos)} bg photos for "
                    f"{len(slides) - 1} inner slides")
    except Exception as e:
        logger.warning(f"[carousel] bg photo pick failed (non-fatal): {e}")
        bg_photos = []

    out_dir = Path(tempfile.mkdtemp(prefix=f"carousel_{chat_id}_"))
    try:
        png_paths = await asyncio.to_thread(
            carousel_renderer.render_carousel, slides, out_dir, bg_photos,
            "diagonal",   # racing-срез layout — выбран Артёмом 17 мая
        )
        logger.info(f"[carousel] rendered {len(png_paths)} PNGs in {out_dir}")
    except Exception as e:
        logger.error(f"[carousel] renderer failed: {e}", exc_info=True)
        try:
            await status.edit_text(
                f"❌ Рендеринг упал: {e}\n\n<code>{str(e)[:200]}</code>",
                parse_mode="HTML",
            )
        except Exception:
            pass
        shutil.rmtree(out_dir, ignore_errors=True)
        return

    # Send as media_group
    try:
        media_handles = []
        media = []
        for png in png_paths:
            f = open(str(png), "rb")
            media_handles.append(f)
            media.append(InputMediaPhoto(media=f))
        try:
            await context.bot.send_media_group(chat_id=chat_id, media=media)
        finally:
            for f in media_handles:
                try:
                    f.close()
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"[carousel] send_media_group failed: {e}", exc_info=True)
        try:
            await status.edit_text(f"❌ Не получилось отправить media_group: {e}")
        except Exception:
            pass
        shutil.rmtree(out_dir, ignore_errors=True)
        return

    try:
        await status.delete()
    except Exception:
        pass

    # ─── A (26 May 2026): сохранить PNG в projects/<card_id>/carousel/ ──
    # Карусель собранная из карточки — её PNG живут в /tmp/ и пропадают.
    # Копируем в persistent папку проекта чтобы попали в zip-архив, доступны
    # через «📥 Скачать материалы», переживают рестарт сервера.
    persisted_dir = None
    notion_published = False
    notion_status_updated = False
    seed_card_id = draft.get("seed_card_id") or draft.get("notion_page_id")
    if seed_card_id:
        try:
            # F1 fix: project_dir из bot_state (нет import bot — нет риска
            # повторного import bot.py под __main__).
            from bot_state import project_dir as _project_dir_fn
            _tmp_data = {"notion_page_id": seed_card_id}
            proj_dir = _project_dir_fn(_tmp_data)
            persisted_dir = _persist_carousel_pngs(png_paths, proj_dir)
            if persisted_dir:
                logger.info(f"[carousel] {len(png_paths)} PNGs persisted → {persisted_dir}")
        except Exception as e:
            logger.warning(f"[carousel] PNG persist failed (non-fatal): {e}")

        # A (28 May 2026): публикация PNG в nginx-media + image-блоки в Notion-страницу
        # карточки. Юзер открывает страницу в Notion → видит готовые PNG inline,
        # не лезет в Telegram скачивать.
        try:
            import bot as _bot
            _brand = _bot._get_active_brand_name()
            media_urls = _publish_carousel_pngs_to_media(
                png_paths, card_id_short=seed_card_id[:20], brand=_brand,
            )
            if media_urls:
                logger.info(
                    f"[carousel/notion] {len(media_urls)} PNGs published → "
                    f"{media_urls[0]}"
                )
                _notion = _bot.notion
                # Удалим старые carousel-блоки если есть (re-render идемпотентен).
                deleted = _delete_old_carousel_blocks_from_notion(_notion, seed_card_id)
                if deleted:
                    logger.info(f"[carousel/notion] removed {deleted} old carousel blocks")
                # Добавим свежие.
                _notion.blocks.children.append(
                    block_id=seed_card_id,
                    children=_build_carousel_notion_blocks(media_urls),
                )
                notion_published = True
                logger.info(f"[carousel/notion] {len(media_urls)} image blocks added to {seed_card_id}")
        except Exception as e:
            logger.warning(f"[carousel/notion] publish failed (non-fatal): {e}", exc_info=True)

        # B (28 May 2026): авто-обновление Notion-статуса карточки на
        # «Готово к публикации». Кнопка submenu «📊 Сменить статус ▼»
        # остаётся как override.
        try:
            import bot as _bot
            await asyncio.to_thread(
                _bot.update_notion_status,
                seed_card_id,
                "Готово к публикации",
                _bot._get_active_brand_name(),
            )
            notion_status_updated = True
            logger.info(f"[carousel/notion] status → «Готово к публикации» for {seed_card_id}")
        except Exception as e:
            logger.warning(f"[carousel/notion] status update failed (non-fatal): {e}")

    cover = slides[0]
    title = (cover.get("title_main") or "") + " " + (cover.get("title_accent") or "")
    persist_line = (
        f"\n💾 PNG сохранены в проект карточки (доступны через «📥 Скачать материалы»).\n"
        if persisted_dir else ""
    )
    notion_line = (
        f"📝 Слайды добавлены в Notion-страницу карточки.\n"
        if notion_published else ""
    )
    status_line = (
        f"✅ Статус карточки → <b>Готово к публикации</b>.\n"
        if notion_status_updated else ""
    )
    caption = (
        f"✅ <b>Карусель готова</b> — {len(slides)} слайдов\n\n"
        f"<i>{title.strip()}</i>\n"
        f"<i>{cover.get('subtitle', '')}</i>\n"
        f"{persist_line}{notion_line}{status_line}\n"
        f"📌 Для публикации в Instagram — скачать PNG (long-press на любом → Save) "
        f"и постить через @livedrive.tmn.\n\n"
        f"<i>(автопостинг в IG в MVP не подключён)</i>"
    )
    # Draft НЕ удаляем сразу — оставляем чтобы юзер мог «✏️ Поправить ещё»
    # (вернуться к точечной правке прямо из финального сообщения).
    # Удалится при carousel_cancel / idea_back_to_menu / новой генерации.
    action_kb_rows = [
        [InlineKeyboardButton(
            "✏️ Поправить ещё (вернуться к сценарию)",
            callback_data="carousel_back_to_preview",
        )],
        [InlineKeyboardButton(
            "🔄 Ещё карусель (новая)",
            callback_data="carousel_regen",
        )],
    ]
    # B4 (26 May 2026): кнопка смены статуса карточки — ТОЛЬКО если карусель
    # сделана из карточки (seed_card_id есть). Открывает submenu со списком
    # PIPELINE_STATUSES, юзер сам решает куда передвинуть.
    if seed_card_id:
        action_kb_rows.append([InlineKeyboardButton(
            "📊 Сменить статус карточки ▼",
            callback_data="carousel_status_menu",
        )])
    if notion_url:
        action_kb_rows.append([InlineKeyboardButton("📋 К карточке", url=notion_url)])
    action_kb_rows.append([InlineKeyboardButton(
        "✅ Готово — закрыть сценарий",
        callback_data="carousel_finalize",
    )])
    action_kb_rows.append([InlineKeyboardButton(
        "◀️ В главное меню", callback_data="idea_back_to_menu",
    )])

    await context.bot.send_message(
        chat_id=chat_id,
        text=caption,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(action_kb_rows),
    )


async def back_to_carousel_preview(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    requested_card_id: str | None = None,
) -> None:
    """Возврат к текстовому preview из финального меню после рендера PNG.

    Триггерится кнопкой «✏️ Поправить ещё». Берёт draft (он persistent в
    pending), показывает _build_script_preview + _approval_keyboard.

    F6 fix (ChatGPT review M8): если передан `requested_card_id` —
    проверяем что draft принадлежит ИМЕННО этой карточке. Без проверки
    юзер из C-диалога по карточке B мог открыть draft карточки A — и потом
    смена статуса/persist PNG ушли бы в чужую карточку.
    """
    uid = _user_id_from_update(update)
    draft = _load_carousel_draft(uid)
    if not draft:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚠️ Сценарий потерян — стартуй карусель заново через 🎨 в меню.",
        )
        return
    if requested_card_id:
        draft_card = draft.get("seed_card_id") or ""
        # Сравниваем по prefix 20 — callback_data truncate, но в draft full id.
        if not (draft_card and draft_card.startswith(requested_card_id[:20])
                or draft_card[:20] == requested_card_id[:20]):
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(
                    "⚠️ Этот черновик принадлежит другой карточке. "
                    "Открой нужную карточку и нажми «🎨 В карусель» заново."
                ),
            )
            return
    slides = draft["slides"]
    notion_url = draft.get("notion_url")
    preview_text = _build_script_preview(slides)
    preview_text += (
        "\n\n━━━━━━━━━━━━━━━━━━━━━\n"
        "✏️ Готов поправить. Жми «<b>Точечная правка</b>» и опиши изменение, "
        "потом снова «<b>Делаем PNG</b>» — карусель перерисуется."
    )
    if len(preview_text) > 4000:
        preview_text = preview_text[:3900] + "\n\n<i>… (обрезано)</i>"
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=preview_text,
        parse_mode="HTML",
        reply_markup=_approval_keyboard(notion_url),
        disable_web_page_preview=True,
    )


async def finalize_carousel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Явное закрытие draft карусели — кнопка «✅ Готово»."""
    try:
        _drop_carousel_draft(_user_id_from_update(update))
    except Exception:
        pass
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="✅ Сценарий карусели закрыт. PNG остались выше — копируй и постируй.",
    )


async def regenerate_carousel_preview(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    claude,
) -> None:
    """Re-run phase 1 with the same theme stored in draft.

    Triggered by `carousel_regen` callback. If no draft → tell user to start over.
    """
    uid = _user_id_from_update(update)
    draft = _load_carousel_draft(uid)
    if not draft:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚠️ Сценарий не найден — стартуй новую карусель через 🎨 в главном меню.",
        )
        return
    await generate_carousel_preview(
        update, context, claude,
        theme=draft["theme"],
        n_slides=draft.get("n_slides"),
        chat_id=draft.get("chat_id"),
        notion_url=draft.get("notion_url"),
        template=draft.get("template"),
        seed_card_id=draft.get("seed_card_id"),
    )


async def cancel_carousel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Drop draft and send «отменено». Triggered by `carousel_cancel`."""
    try:
        _drop_carousel_draft(_user_id_from_update(update))
    except Exception:
        pass
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="✖️ Карусель отменена.",
    )


async def apply_carousel_surgical_edit(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    claude,
    instruction: str,
) -> None:
    """Apply a localized edit to the carousel draft, re-show the preview.

    Triggered when user is in `awaiting_carousel_surg_edit` state and sends
    a text/voice instruction. Sonnet edits only the requested part; the rest
    of the draft is preserved. Re-shows preview with the approval keyboard.

    Hard cap: 10 surgical iterations per draft.
    """
    uid = _user_id_from_update(update)
    draft = _load_carousel_draft(uid)
    if not draft:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚠️ Сценарий потерян (бот мог рестартнуть). Сделай карусель заново через 🎨.",
        )
        return

    slides = draft["slides"]
    iters = int(draft.get("surg_iterations", 0) or 0)
    chat_id = draft.get("chat_id") or update.effective_chat.id
    notion_url = draft.get("notion_url")

    if iters >= 10:
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ Достигнут лимит точечных правок (10). "
                 "Жми «✅ Делаем PNG» или «🔄 Переписать полностью».",
        )
        return

    status = await context.bot.send_message(
        chat_id=chat_id,
        text=f"✏️ Применяю точечную правку #{iters + 1}… <i>~10-15 сек</i>",
        parse_mode="HTML",
    )

    try:
        new_slides = await asyncio.to_thread(
            carousel_llm.surgical_edit_carousel, claude, slides, instruction,
        )
        logger.info(f"[carousel] surgical edit #{iters + 1} applied")
    except Exception as e:
        logger.error(f"[carousel] surgical edit failed: {e}", exc_info=True)
        try:
            await status.edit_text(
                f"❌ Не смог применить правку: {e}\n\n"
                f"Попробуй другой формулировкой или жми «🔄 Переписать полностью».",
            )
        except Exception:
            pass
        return

    # ─── F2 failure path: явный _surg_error от Sonnet (26 May 2026) ────
    # Если Sonnet добавил `_surg_error` в первый слайд — это конкретная
    # причина «не нашёл/не понял». Чистим служебные поля и НЕ обновляем
    # draft. Проверяется ДО no-op detect (без этого equality бы сломалась
    # на разном set ключей).
    surg_error_msg = carousel_llm._extract_surg_error(new_slides)
    if surg_error_msg:
        logger.info(f"[carousel-surg] iter #{iters + 1}: _surg_error from Sonnet: {surg_error_msg!r}")
        try:
            await status.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"⚠️ <b>Правка не применена</b> — модель не нашла что менять.\n\n"
                f"Причина: <i>{surg_error_msg[:200]}</i>\n\n"
                "Переформулируй точнее (укажи слайд или поле):\n"
                "• <i>«заголовок слайда 3 поменяй на …»</i>\n"
                "• <i>«в кикере 2-го слайда замени X на Y»</i>"
            ),
            parse_mode="HTML",
            reply_markup=_approval_keyboard(notion_url),
            disable_web_page_preview=True,
        )
        # Counter НЕ инкрементим — failure не съедает лимит правок.
        return

    # ─── NO-OP detect (26 мая 2026) ────────────────────────────────────
    # Sonnet иногда возвращает JSON байт-в-байт (не нашёл подстроку и не
    # вернул _surg_error). Это generic no-op — даём общее сообщение.
    is_noop = carousel_llm._slides_equal_normalized(slides, new_slides)
    # P2: лог diff для будущей диагностики
    changed_slides_count = sum(
        1 for i, sl in enumerate(slides)
        if not carousel_llm._slides_equal_normalized([sl], [new_slides[i]])
    )
    logger.info(
        f"[carousel-surg] iter #{iters + 1}: no_op={is_noop}, "
        f"changed_slides={changed_slides_count}/{len(slides)}"
    )

    if is_noop:
        # Проверка: была ли это REPLACE-инструкция — если да, дать конкретный совет.
        replace_pat = carousel_llm._extract_replace_pattern(instruction)
        if replace_pat:
            x, y = replace_pat
            hint = (
                f"Похоже, я не нашёл в сценарии подстроку <code>{x[:60]}</code>. "
                f"Проверь точное написание (буквы, пробелы) и переформулируй — "
                f"можно указать слайд: «<i>слайд 2: {x[:30]} → {y[:30]}</i>»."
            )
        else:
            hint = (
                "Модель не нашла что менять по этой инструкции. Переформулируй "
                "поконкретнее: укажи слайд или поле "
                "(«<i>заголовок слайда 3 поменяй на …</i>», "
                "«<i>в кикере 2-го слайда замени X на Y</i>»)."
            )
        try:
            await status.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "⚠️ <b>Правка не применена</b> — сценарий не изменился.\n\n"
                f"{hint}\n\n"
                "Можно ещё раз нажать «✏️ Точечная правка» или вернуться к "
                "одобрению / переписать полностью."
            ),
            parse_mode="HTML",
            reply_markup=_approval_keyboard(notion_url),
            disable_web_page_preview=True,
        )
        # Counter НЕ инкрементим — это не успешная итерация.
        return

    draft["slides"] = new_slides
    draft["surg_iterations"] = iters + 1
    _save_carousel_draft(uid, draft)

    preview_text = _build_script_preview(new_slides)
    preview_text += (
        f"\n\n━━━━━━━━━━━━━━━━━━━━━\n"
        f"✏️ <b>Точечная правка #{iters + 1} применена.</b>\n"
        f"Согласуй: «Делаем PNG», ещё правка, «Переписать полностью» или «Отмена»."
    )
    if len(preview_text) > 4000:
        preview_text = preview_text[:3900] + "\n\n<i>… (обрезано)</i>"

    try:
        await status.delete()
    except Exception:
        pass

    await context.bot.send_message(
        chat_id=chat_id,
        text=preview_text,
        parse_mode="HTML",
        reply_markup=_approval_keyboard(notion_url),
        disable_web_page_preview=True,
    )


# Backward-compat: existing callsites in bot.py expect this name. New flow is
# preview → approve, so this becomes an alias for phase 1.
async def render_and_send_carousel(*args, **kwargs):
    """Backward-compat alias — runs phase 1 only (preview).

    Caller should NOT directly call render. Phase 2 fires via `carousel_approve`
    callback, which routes to render_carousel_from_draft.
    """
    return await generate_carousel_preview(*args, **kwargs)


__all__ = [
    "generate_carousel_preview",
    "render_carousel_from_draft",
    "regenerate_carousel_preview",
    "back_to_carousel_preview",
    "finalize_carousel",
    "cancel_carousel",
    "apply_carousel_surgical_edit",
    "render_and_send_carousel",  # alias
]
