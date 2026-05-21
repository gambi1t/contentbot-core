"""
tg_post_handlers — интеграция tg_post_writer в Илана (bot.py).

Добавляет:
  • команду /tgpost — пошаговый флоу генерации TG-поста
    (intro / stage / video_companion);
  • callback-паттерн `tgpost:*` для всех кнопок флоу;
  • хелпер handle_tgpost_text() — вызывается из process_idea / process_voice,
    когда pending[user_id].state начинается с 'tgpost_'.

Внешние зависимости (pending-словарь, Claude, Notion и т.д.) передаются
через register(). Это сохраняет bot.py «чистым»: там только одна строка
импорта + одна строка регистрации + две строки делегации в process_idea /
process_voice.

Источник правды по тону — memory/spec_ilon_telegram_posts.md.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from tg_post_writer import PostInput, generate_post

logger = logging.getLogger("content_bot")


# ═══ Внешние зависимости (заполняются register()) ═══════════════════════════
_ext: dict = {}


# ═══ Состояния pending ═══════════════════════════════════════════════════════
#
# Все ключи начинаются с 'tgpost_', чтобы process_idea / process_voice
# могли одной проверкой перенаправить ввод сюда.
#
#   tgpost_choose_type     — ждём tap на одну из кнопок [intro|stage|video]
#   tgpost_wait_stage_num  — для stage: ждём номер этапа
#   tgpost_wait_bridge     — для stage: ждём мостик из прошлого поста
#   tgpost_wait_facts      — ждём фактуру (текст/голос)
#   tgpost_wait_edit       — ждём голосовую/текстовую правку уже готового поста
#   tgpost_review          — готовый пост показан, ждём действия через кнопки
#
TG_STATE_PREFIX = "tgpost_"


def is_tgpost_state(state: Optional[str]) -> bool:
    """True, если state принадлежит потоку генерации TG-постов."""
    return bool(state) and state.startswith(TG_STATE_PREFIX)


# ═══ Клавиатуры ══════════════════════════════════════════════════════════════

def _kb_type_picker(brand: str = "default") -> InlineKeyboardMarkup:
    """Brand-aware post-type picker.

    Initially (Artem's flow) showed только Артёмовы форматы: stage,
    video_companion, thought. После запуска Максима выяснилось, что
    Максим попадает в это же меню через «📝 TG-пост» главного меню,
    но его форматы (review_essay / review_list / thesis) — другие.
    Test runner (scenarios/02) задокументировал расхождение 13 May 2026.

    Now: для maksim показываем Максимовы форматы, для default —
    исторические Артёмовы.
    """
    if brand == "maksim":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📰 Разбор-эссе", callback_data="tgpost:type:review_essay")],
            [InlineKeyboardButton("📋 Тезисный список", callback_data="tgpost:type:review_list")],
            [InlineKeyboardButton("💭 Короткий тезис", callback_data="tgpost:type:thesis")],
            [InlineKeyboardButton("❌ Отмена", callback_data="tgpost:cancel")],
        ])
    # default — Артёмов content-bot
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧭 Про эксперимент (Этап N)", callback_data="tgpost:type:stage")],
        [InlineKeyboardButton("🎬 Пост к ролику", callback_data="tgpost:type:video_companion")],
        [InlineKeyboardButton("💭 Мысль / наблюдение", callback_data="tgpost:type:thought")],
        [InlineKeyboardButton("❌ Отмена", callback_data="tgpost:cancel")],
    ])


def _kb_review() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Опубликовать в канал", callback_data="tgpost:publish")],
        [
            InlineKeyboardButton("🔄 Перегенерировать", callback_data="tgpost:regen"),
            InlineKeyboardButton("🎙️ Правки", callback_data="tgpost:voice_edit"),
        ],
        [InlineKeyboardButton("📥 Сохранить в Notion", callback_data="tgpost:notion")],
        [InlineKeyboardButton("❌ Отмена", callback_data="tgpost:cancel")],
    ])


# ═══ Команда /tgpost ═════════════════════════════════════════════════════════

async def tgpost_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запускает флоу генерации TG-поста.

    13 May 2026 — теперь brand-aware: maksim видит свои форматы
    (review_essay/review_list/thesis), default — Артёмовы (stage/...).
    Brand резолвится через _ext['get_brand'] callable, который bot.py
    передаёт в register().
    """
    user_id = update.effective_user.id
    pending = _ext["pending"]
    save = _ext["save_pending"]

    data = pending.setdefault(user_id, {})
    # Чистим прошлый флоу, если был
    data["state"] = "tgpost_choose_type"
    data["tgpost"] = {}
    save(pending)

    get_brand = _ext.get("get_brand")
    brand = get_brand() if callable(get_brand) else "default"

    if brand == "maksim":
        text = (
            "📝 <b>Пост для TG-канала «Юмсунов | Про реальный бизнес»</b>\n\n"
            "Выбери тип поста:\n\n"
            "📰 <b>Разбор-эссе</b> — длинный пост (800-1500 знаков) с моралью и образным финалом\n"
            "📋 <b>Тезисный список</b> — нумерованный 7-10 пунктов с эмодзи-цифрами\n"
            "💭 <b>Короткий тезис</b> — одно наблюдение, 300-500 знаков"
        )
    else:
        text = (
            "📝 <b>Пост для Telegram-канала эксперимента</b>\n\n"
            "Выбери тип поста:\n\n"
            "🧭 <b>Про эксперимент</b> — новый этап истории «694 → 10 694»\n"
            "🎬 <b>Пост к ролику</b> — длинный текст в канал к выпущенному видео\n"
            "💭 <b>Мысль / наблюдение</b> — короткий свободный пост без ролика"
        )

    await update.message.reply_text(
        text,
        reply_markup=_kb_type_picker(brand),
        parse_mode="HTML",
    )


# ═══ Callback-обработчик (pattern ^tgpost:) ══════════════════════════════════

async def handle_tgpost_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    pending = _ext["pending"]
    save = _ext["save_pending"]
    data = pending.setdefault(user_id, {})
    tg = data.setdefault("tgpost", {})

    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    # Отмена
    if action == "cancel":
        data.pop("tgpost", None)
        data["state"] = None
        save(pending)
        try:
            await query.edit_message_text("❌ Отменено.")
        except Exception:
            pass
        return

    # Выбор типа поста
    if action == "type":
        post_type = parts[2] if len(parts) > 2 else "stage"
        tg["post_type"] = post_type

        if post_type == "intro":
            data["state"] = "tgpost_wait_facts"
            save(pending)
            await query.edit_message_text(
                "📣 <b>Вводный пост</b>\n\n"
                "Надиктуй голосом или напиши текстом фактуру:\n"
                "— что делаю и ради чего\n"
                "— цель в цифрах\n"
                "— площадки, инструменты, ритм\n"
                "— любые акценты, которые важны\n\n"
                "Если оставить пустым («—») — возьму дефолтные параметры из спеки.",
                parse_mode="HTML",
            )
            return

        if post_type == "stage":
            data["state"] = "tgpost_wait_stage_num"
            save(pending)
            await query.edit_message_text(
                "🧭 <b>Пост-этап</b>\n\n"
                "Какой номер этапа? Напиши цифрой (1, 2, 3, …).",
                parse_mode="HTML",
            )
            return

        if post_type == "thought":
            # Короткий свободный пост: спрашиваем только фактуру и генерим.
            data["state"] = "tgpost_wait_facts"
            save(pending)
            await query.edit_message_text(
                "💭 <b>Мысль / наблюдение</b>\n\n"
                "Короткий свободный пост без привязки к этапу или ролику.\n\n"
                "Надиктуй голосом или напиши текстом — о чём мысль, что "
                "заметил, какой вывод. Чем конкретнее (место, ситуация, "
                "детали) — тем живее получится.\n\n"
                "Длина выходного поста будет 300–700 знаков.",
                parse_mode="HTML",
            )
            return

        # ── Maksim brand types ──────────────────────────────────────
        # Все три собирают фактуру одинаково (через wait_facts → generate),
        # разница только в системном промпте `generate_post`, который
        # выбирает развёртку по `inp.post_type`.
        if post_type == "review_essay":
            data["state"] = "tgpost_wait_facts"
            save(pending)
            await query.edit_message_text(
                "📰 <b>Разбор-эссе</b>\n\n"
                "Длинный пост (800-1500 знаков) с разбором темы по подкатегориям "
                "и образным финалом. Эталон стиля — пост «Большинство строят "
                "глэмпинг у воды. И зря».\n\n"
                "Надиктуй или напиши фактуру:\n"
                "— что разбираем (тема, тезис)\n"
                "— конкретные детали из реального опыта\n"
                "— контринтуитивные наблюдения\n"
                "— подкатегории темы если есть",
                parse_mode="HTML",
            )
            return

        if post_type == "review_list":
            data["state"] = "tgpost_wait_facts"
            save(pending)
            await query.edit_message_text(
                "📋 <b>Тезисный список</b>\n\n"
                "Нумерованный пост с 7-10 пунктами через эмодзи-цифры "
                "1️⃣ 2️⃣ 3️⃣ … По эталонной механике Максима.\n\n"
                "Надиктуй или напиши:\n"
                "— тему / общий тезис\n"
                "— факты, наблюдения, выводы (бот сам структурирует в пункты)\n"
                "— конкретика из реального опыта обязательна",
                parse_mode="HTML",
            )
            return

        if post_type == "thesis":
            data["state"] = "tgpost_wait_facts"
            save(pending)
            await query.edit_message_text(
                "💭 <b>Короткий тезис</b>\n\n"
                "Один яркий тезис в 300-500 знаков. Без нумерации, "
                "без длинной разбивки — просто наблюдение с конкретикой.\n\n"
                "Надиктуй или напиши, о чём тезис.",
                parse_mode="HTML",
            )
            return

        if post_type == "video_companion":
            # Пробуем подхватить текущий проект из pending
            script_raw = data.get("script") or data.get("voice_parts") or ""
            script = "\n".join(script_raw) if isinstance(script_raw, list) else str(script_raw)
            desc = data.get("description") or data.get("description_draft") or ""
            title = (data.get("card_data") or {}).get("title", "")

            if script.strip():
                tg["video_script"] = script
                tg["short_description"] = desc
                tg["video_topic"] = title
                data["state"] = "tgpost_wait_facts"
                save(pending)
                await query.edit_message_text(
                    f"🎬 <b>Сопровод к видео</b>\n\n"
                    f"Нашёл текущий проект: «{title or 'без названия'}».\n"
                    f"Сценарий и короткое описание возьму оттуда.\n\n"
                    f"Надиктуй или напиши одной-двумя фразами, какой акцент сделать "
                    f"(или пришли «авто» — сам решу).",
                    parse_mode="HTML",
                )
            else:
                data["state"] = "tgpost_wait_facts"
                save(pending)
                await query.edit_message_text(
                    "🎬 <b>Сопровод к видео</b>\n\n"
                    "Текущего проекта не нашёл.\n"
                    "Пришли сценарий ролика (текст озвучки) — голосом или текстом.",
                    parse_mode="HTML",
                )
            return
        return

    # Перегенерировать
    if action == "regen":
        await _generate_and_show(query.message, user_id, via_callback=True, status_query=query)
        return

    # Правки — ждём голос/текст
    if action == "voice_edit":
        data["state"] = "tgpost_wait_edit"
        save(pending)
        await query.edit_message_text(
            "🎙️ Надиктуй правку голосом или напиши текстом — пришлю новую версию с учётом."
        )
        return

    # Публикация в канал
    if action == "publish":
        await _publish_to_channel(query, context, tg)
        return

    # Сохранение в Notion
    if action == "notion":
        await _save_to_notion(query, tg)
        return

    # Кнопка «Переписать под Telegram» с экрана описания ролика
    if action == "rewrite_tg":
        await _rewrite_for_telegram_from_description(query, context, data)
        return


# ═══ Обработка текста (делегируется из process_idea / process_voice) ═════════

async def handle_tgpost_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """
    Возвращает True, если ввод поглощён флоу tgpost (вызывающий НЕ должен
    продолжать обычную обработку идеи/сценария).
    """
    user_id = update.effective_user.id
    pending = _ext["pending"]
    save = _ext["save_pending"]
    data = pending.get(user_id)
    if not data:
        return False
    state = data.get("state") or ""
    if not state.startswith(TG_STATE_PREFIX):
        return False

    tg = data.setdefault("tgpost", {})
    text = (text or "").strip()

    # Этап — номер
    if state == "tgpost_wait_stage_num":
        m = re.search(r"\d+", text)
        if not m:
            await update.message.reply_text("Нужно число — номер этапа. Попробуй ещё раз.")
            return True
        tg["stage_num"] = int(m.group())
        data["state"] = "tgpost_wait_bridge"
        save(pending)
        await update.message.reply_text(
            f"🧭 Этап {tg['stage_num']}\n\n"
            f"Какой мостик из прошлого поста? Что было обещано в конце предыдущего этапа?\n\n"
            f"Напиши коротко или пришли «—» если мостика нет."
        )
        return True

    # Этап — мостик
    if state == "tgpost_wait_bridge":
        tg["bridge"] = "" if text in {"", "—", "-", "нет", "Нет"} else text
        data["state"] = "tgpost_wait_facts"
        save(pending)
        await update.message.reply_text(
            "📋 Теперь фактура этапа:\n"
            "— что произошло\n— цифры, события, инструменты\n\n"
            "Голосом или текстом."
        )
        return True

    # Фактура (intro / stage / video_companion)
    if state == "tgpost_wait_facts":
        if tg.get("post_type") == "video_companion":
            tg["extra_notes"] = "" if text.lower() in {"", "—", "-", "авто", "auto"} else text
        else:
            tg["facts"] = "" if text in {"", "—", "-"} else text
        await _generate_and_show(update.message, user_id, via_callback=False)
        return True

    # Правка уже сгенерированного
    if state == "tgpost_wait_edit":
        prev = tg.get("extra_notes", "")
        tg["extra_notes"] = (prev + "\n\nПравка: " + text).strip() if prev else f"Правка: {text}"
        await _generate_and_show(update.message, user_id, via_callback=False)
        return True

    # Любой текст в review-состоянии — игнорируем (пусть жмут кнопки)
    if state == "tgpost_review":
        return False

    return False


# ═══ Генерация + показ превью ═══════════════════════════════════════════════

async def _generate_and_show(message, user_id: int, *, via_callback: bool,
                             status_query=None):
    pending = _ext["pending"]
    save = _ext["save_pending"]
    data = pending.get(user_id, {})
    tg = data.setdefault("tgpost", {})

    # Статусное сообщение
    if via_callback and status_query is not None:
        try:
            await status_query.edit_message_text("✍️ Пишу пост… Opus 4, ~20–40 сек.")
            status = status_query.message
        except Exception:
            status = await message.reply_text("✍️ Пишу пост… Opus 4, ~20–40 сек.")
    else:
        status = await message.reply_text("✍️ Пишу пост… Opus 4, ~20–40 сек.")

    try:
        inp = PostInput(
            post_type=tg.get("post_type", "stage"),
            stage_num=tg.get("stage_num"),
            facts=tg.get("facts", ""),
            bridge_from_previous=tg.get("bridge", ""),
            extra_notes=tg.get("extra_notes", ""),
            video_script=tg.get("video_script", ""),
            short_description=tg.get("short_description", ""),
            video_topic=tg.get("video_topic", ""),
        )
        post_text = await asyncio.to_thread(generate_post, inp, _ext["claude"])
        tg["last_post"] = post_text
        data["state"] = "tgpost_review"
        save(pending)

        shown = _safe_preview(post_text)
        await status.edit_text(
            f"📝 <b>Готовый пост:</b>\n\n{shown}",
            reply_markup=_kb_review(),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"tgpost generation failed: {e}", exc_info=True)
        try:
            await status.edit_text(f"❌ Ошибка генерации: {e}")
        except Exception:
            pass


def _safe_preview(text: str) -> str:
    """
    Превью поста. Пост хранится с markdown-жирным (**Заголовок**).
    Для превью показываем как есть — читатель увидит текст с двойными
    звёздочками, но при публикации в канал парс_mode='Markdown' отрендерит
    их в жирный.  HTML-символы экранируем.
    """
    t = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Обрежем, если слишком длинный (лимит TG с клавиатурой)
    if len(t) > 3500:
        t = t[:3500] + "\n\n… (обрезано для превью)"
    return t


# ═══ Публикация в канал ══════════════════════════════════════════════════════

async def _publish_to_channel(query, context, tg: dict):
    text = tg.get("last_post", "")
    if not text:
        await query.edit_message_text("Нет текста для публикации.")
        return

    channel_id = _ext.get("channel_id") or os.getenv("TELEGRAM_CHANNEL_ID")
    if not channel_id:
        await query.edit_message_text(
            "❌ TELEGRAM_CHANNEL_ID не задан в .env — публикация невозможна."
        )
        return

    try:
        msg = await context.bot.send_message(
            chat_id=channel_id,
            text=text,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        # Ссылка на пост (если канал публичный)
        ch = str(channel_id).lstrip("@")
        link = f"https://t.me/{ch}/{msg.message_id}" if not ch.startswith("-") else ""
        confirm = f"✅ Опубликовано в канал.\n\n{link}" if link else "✅ Опубликовано в канал."
        await query.edit_message_text(confirm, disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"tgpost publish failed: {e}", exc_info=True)
        await query.edit_message_text(
            f"❌ Не опубликовалось: {e}\n\n"
            f"Проверь TELEGRAM_CHANNEL_ID и что бот — админ канала."
        )


# ═══ Сохранение в Notion ═════════════════════════════════════════════════════

async def _save_to_notion(query, tg: dict):
    text = tg.get("last_post", "")
    if not text:
        await query.edit_message_text("Нет текста.")
        return
    notion = _ext.get("notion")
    db = _ext.get("notion_db")
    if not notion or not db:
        await query.edit_message_text("❌ Notion не сконфигурирован.")
        return

    post_type = tg.get("post_type", "stage")
    stage_num = tg.get("stage_num")

    # Заголовок — первая строка, без ** **
    first_line = text.split("\n", 1)[0].strip().strip("*").strip()
    title = first_line[:100] if first_line else f"TG-пост ({post_type})"
    if post_type == "stage" and stage_num:
        if not title.lower().startswith("этап"):
            title = f"Этап {stage_num}. {title}"

    try:
        # Разбиваем длинный текст на блоки по 2000 символов (лимит Notion)
        chunks = [text[i:i + 1900] for i in range(0, len(text), 1900)]
        children = [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": ch}}],
                },
            }
            for ch in chunks
        ]

        page = await asyncio.to_thread(
            notion.pages.create,
            parent={"database_id": db},
            properties={"Name": {"title": [{"text": {"content": title}}]}},
            children=children,
        )
        url = page.get("url", "")
        await query.edit_message_text(
            f"📥 Сохранено в Notion\n{url}\n\n{_safe_preview(text)}",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"tgpost notion save failed: {e}", exc_info=True)
        await query.edit_message_text(f"❌ Notion: {e}")


# ═══ «Переписать под Telegram» с экрана описания ролика ══════════════════════

async def _rewrite_for_telegram_from_description(query, context, data: dict):
    """
    Вызывается по кнопке с экрана готового описания ролика.
    Берёт script + description + title из текущего проекта и генерит длинный
    сопроводительный пост для Telegram-канала.
    """
    pending = _ext["pending"]
    save = _ext["save_pending"]

    description = data.get("description") or data.get("description_draft") or ""
    script_raw = data.get("script") or data.get("voice_parts") or ""
    script = "\n".join(script_raw) if isinstance(script_raw, list) else str(script_raw)
    title = (data.get("card_data") or {}).get("title", "")

    if not script.strip() and not description.strip():
        await query.answer("Нет ни сценария, ни описания — нечего переписывать.", show_alert=True)
        return

    tg = data.setdefault("tgpost", {})
    tg["post_type"] = "video_companion"
    tg["video_script"] = script
    tg["short_description"] = description
    tg["video_topic"] = title
    data["state"] = "tgpost_review"
    save(pending)

    try:
        await query.edit_message_text("✍️ Пишу Telegram-версию описания… Opus 4, ~20–40 сек.")
    except Exception:
        pass

    try:
        inp = PostInput(
            post_type="video_companion",
            video_script=script,
            short_description=description,
            video_topic=title,
        )
        post_text = await asyncio.to_thread(generate_post, inp, _ext["claude"])
        tg["last_post"] = post_text
        save(pending)

        # Сохраняем в папку проекта как description_tg.txt
        save_text_fn = _ext.get("save_text_fn")
        if save_text_fn:
            try:
                save_text_fn(data, "description_tg.txt", post_text)
            except Exception as e:
                logger.warning(f"save description_tg.txt failed: {e}")

        shown = _safe_preview(post_text)
        await query.edit_message_text(
            f"📝 <b>Telegram-версия (длинная):</b>\n\n{shown}",
            reply_markup=_kb_review(),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"rewrite_tg failed: {e}", exc_info=True)
        await query.edit_message_text(f"❌ Ошибка: {e}")


# ═══ Регистрация в Application ═══════════════════════════════════════════════

def register(
    app: Application,
    *,
    pending_dict: dict,
    save_pending_fn,
    claude_client,
    notion_client,
    notion_db_id: str,
    channel_id: Optional[str] = None,
    save_text_fn=None,
    get_brand_fn=None,
):
    """
    Вызывается из bot.main() ДО MessageHandler(filters.TEXT, process_idea),
    чтобы наш CallbackQueryHandler с pattern='^tgpost:' матчился раньше
    общего без паттерна.

    get_brand_fn — callable() -> str, возвращает активный brand name.
    Используется для brand-aware рендера _kb_type_picker / tgpost_command.
    Optional — если не передан, флоу работает в default-режиме (Артёмов).
    """
    _ext["pending"] = pending_dict
    _ext["save_pending"] = save_pending_fn
    _ext["claude"] = claude_client
    _ext["notion"] = notion_client
    _ext["notion_db"] = notion_db_id
    _ext["channel_id"] = channel_id or os.getenv("TELEGRAM_CHANNEL_ID")
    _ext["save_text_fn"] = save_text_fn
    _ext["get_brand"] = get_brand_fn

    app.add_handler(CommandHandler("tgpost", tgpost_command))
    app.add_handler(CallbackQueryHandler(handle_tgpost_callback, pattern=r"^tgpost:"))
    logger.info("tg_post_handlers: /tgpost + callback ^tgpost: зарегистрированы")
