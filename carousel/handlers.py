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

    # Store draft for phase 2
    context.user_data["carousel_draft"] = {
        "slides": slides,
        "theme": theme,
        "n_slides": n_slides,
        "notion_url": notion_url,
        "chat_id": chat_id,
        "template": template,
    }

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
    draft = context.user_data.get("carousel_draft")
    if not draft:
        await context.bot.send_message(
            chat_id=chat_id or update.effective_chat.id,
            text="⚠️ Сценарий потерян (бот мог рестартнуть). Сделай карусель заново.",
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

    cover = slides[0]
    title = (cover.get("title_main") or "") + " " + (cover.get("title_accent") or "")
    caption = (
        f"✅ <b>Карусель готова</b> — {len(slides)} слайдов\n\n"
        f"<i>{title.strip()}</i>\n"
        f"<i>{cover.get('subtitle', '')}</i>\n\n"
        f"📌 Для публикации в Instagram — скачать PNG (long-press на любом → Save) "
        f"и постить через @livedrive.tmn.\n\n"
        f"<i>(автопостинг в IG в MVP не подключён)</i>"
    )
    action_kb_rows = [
        [InlineKeyboardButton(
            "🔄 Ещё карусель",
            callback_data="carousel_regen",
        )],
    ]
    if notion_url:
        action_kb_rows.append([InlineKeyboardButton("📋 К карточке", url=notion_url)])
    action_kb_rows.append([InlineKeyboardButton(
        "◀️ В главное меню", callback_data="idea_back_to_menu",
    )])

    await context.bot.send_message(
        chat_id=chat_id,
        text=caption,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(action_kb_rows),
    )

    # Draft consumed
    context.user_data.pop("carousel_draft", None)


async def regenerate_carousel_preview(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    claude,
) -> None:
    """Re-run phase 1 with the same theme stored in draft.

    Triggered by `carousel_regen` callback. If no draft → tell user to start over.
    """
    draft = context.user_data.get("carousel_draft")
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
    )


async def cancel_carousel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Drop draft and send «отменено». Triggered by `carousel_cancel`."""
    context.user_data.pop("carousel_draft", None)
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
    draft = context.user_data.get("carousel_draft")
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

    draft["slides"] = new_slides
    draft["surg_iterations"] = iters + 1
    context.user_data["carousel_draft"] = draft

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
    "cancel_carousel",
    "apply_carousel_surgical_edit",
    "render_and_send_carousel",  # alias
]
