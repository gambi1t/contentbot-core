"""library_manager.py — менеджер серверной B-roll библиотеки прямо в боте.

Артём (8 июня): нужно из Telegram (а не скриптами/вручную) загружать новые
клипы/фото в библиотеку и удалять неактуальные.

- Загрузка → broll-library/<photos|clips>/<brand>/<category>/<file>.
- Удаление → unlink файла (и .json-сайдкара) с сервера.

Переиспользует selfie.broll_picker (scan/categories/lookup + превью-генераторы).
init()-DI как у selfie-модуля (без `import bot`).
"""
from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from selfie import broll_picker as bp

_PENDING = None
_SAVE = None
_LOGGER = None

# Категории для ЗАГРУЗКИ (все известные, в т.ч. сейчас пустые — чтобы можно было
# наполнить personal/maksim_self/team/nature). Для УДАЛЕНИЯ берём непустые.
UPLOAD_CATEGORIES = [
    "glamping", "karting", "sup", "personal",
    "maksim_self", "team", "meetings", "nature",
]


def init(pending, save_pending, logger) -> None:
    global _PENDING, _SAVE, _LOGGER
    _PENDING = pending
    _SAVE = save_pending
    _LOGGER = logger


# ════════════════════════ pure helpers (под TDD) ════════════════════════

def category_target_dir(kind: str, category: str):
    """Папка библиотеки для (kind, category): <root>/<brand>/<category>."""
    base = bp._brand_base(kind)
    if not base:
        return None
    return base / category


def _safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name or "").strip()
    return name or "file"


def add_file_to_library(src_path: str, kind: str, category: str, orig_name: str):
    """Скопировать файл в категорию библиотеки. При коллизии имени — суффикс _N.
    Возвращает целевой Path или None."""
    target_dir = category_target_dir(kind, category)
    if not target_dir:
        return None
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / _safe_name(orig_name)
    if dest.exists():
        stem, suf = dest.stem, dest.suffix
        i = 1
        while dest.exists():
            dest = target_dir / f"{stem}_{i}{suf}"
            i += 1
    shutil.copy2(src_path, dest)
    return dest


def delete_library_file(kind: str, item_id: str):
    """Удалить файл библиотеки по id (+ .json-сайдкар). Возвращает имя или None."""
    path = bp.lookup_library_path(kind, item_id)
    if not path or not Path(path).exists():
        return None
    name = Path(path).name
    try:
        Path(path).unlink()
        j = Path(str(path) + ".json")
        if j.exists():
            j.unlink()
        return name
    except Exception:
        return None


# ════════════════════════ UI ════════════════════════

def is_upload_state(user_id: int) -> bool:
    st = (_PENDING.get(user_id, {}) or {}).get("state") or ""
    return st.startswith("lib_admin_upload:")


def _menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Загрузить в библиотеку", callback_data="lib_admin:up")],
        [InlineKeyboardButton("🗑 Удалить из библиотеки", callback_data="lib_admin:del")],
        [InlineKeyboardButton("✖️ Закрыть", callback_data="lib_admin:close")],
    ])


def _kind_kb(action: str) -> InlineKeyboardMarkup:
    # action: "up" | "del"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📷 Фото", callback_data=f"lib_admin:{action}_kind:image")],
        [InlineKeyboardButton("🎞 Клипы", callback_data=f"lib_admin:{action}_kind:video")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="lib_admin:menu")],
    ])


def _upload_cat_kb(kind: str) -> InlineKeyboardMarkup:
    rows = []
    for cat in UPLOAD_CATEGORIES:
        rows.append([InlineKeyboardButton(
            bp._cat_label(cat), callback_data=f"lib_admin:up_cat:{kind}:{cat}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="lib_admin:up")])
    return InlineKeyboardMarkup(rows)


def _del_cat_kb(kind: str, cats) -> InlineKeyboardMarkup:
    rows = []
    for cat, n in cats:
        rows.append([InlineKeyboardButton(
            f"{bp._cat_label(cat)} ({n})", callback_data=f"lib_admin:del_cat:{kind}:{cat}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="lib_admin:del")])
    return InlineKeyboardMarkup(rows)


async def library_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _LOGGER.info(f"[lib_admin] /library by {update.effective_user.id}")
    await update.message.reply_text(
        "📁 *Библиотека B-roll*\n\nЗагрузить новые клипы/фото или удалить неактуальные.",
        parse_mode="Markdown", reply_markup=_menu_kb(),
    )


async def _send_previews(context, chat_id, samples, kind: str) -> None:
    from telegram import InputMediaPhoto, InputMediaVideo
    prev_dir = Path(tempfile.mkdtemp(prefix="lib_prev_"))
    try:
        import asyncio

        async def _mk(i, s):
            if kind == "image":
                return await asyncio.to_thread(
                    bp.make_image_preview, s["path"], str(prev_dir / f"p_{i}.jpg"))
            return await asyncio.to_thread(
                bp.make_clip_preview, s["path"], str(prev_dir / f"p_{i}.mp4"))

        results = await asyncio.gather(
            *[_mk(i, s) for i, s in enumerate(samples)], return_exceptions=True)
        previews = [r for r in results if isinstance(r, str) and r and Path(r).exists()]
        if not previews:
            return
        media, handles = [], []
        try:
            for p in previews:
                f = open(p, "rb"); handles.append(f)
                media.append(InputMediaPhoto(f) if kind == "image" else InputMediaVideo(f))
            if len(media) >= 2:
                await context.bot.send_media_group(chat_id=chat_id, media=media)
            elif kind == "image":
                await context.bot.send_photo(chat_id=chat_id, photo=handles[0])
            else:
                await context.bot.send_video(chat_id=chat_id, video=handles[0])
        finally:
            for f in handles:
                try:
                    f.close()
                except Exception:
                    pass
    except Exception as e:
        _LOGGER.warning(f"[lib_admin] preview failed: {e}")
    finally:
        shutil.rmtree(prev_dir, ignore_errors=True)


def _delete_many(kind: str, ids: list) -> tuple[int, int]:
    """Удалить список файлов библиотеки. Возвращает (удалено, не_найдено).
    Артём 13 июня: «удалить все показанные» разом, не по одному."""
    ok = fail = 0
    for item_id in ids:
        if delete_library_file(kind, item_id):
            ok += 1
        else:
            fail += 1
    return ok, fail


def _del_browse_kb(samples, kind: str, category: str) -> InlineKeyboardMarkup:
    src = "photo" if kind == "image" else "clip"
    row = []
    for i, s in enumerate(samples, start=1):
        row.append(InlineKeyboardButton(
            f"🗑 {i}", callback_data=f"lib_admin:delpick:{src}:{category}:{s['id']}"))
    rows = [row[:3], row[3:]] if len(row) > 3 else [row]
    # «Удалить все показанные» — id берутся из persisted-набора (delall).
    rows.append([InlineKeyboardButton(
        f"🗑 Удалить все показанные ({len(samples)})",
        callback_data=f"lib_admin:delall:{src}:{category}")])
    rows.append([InlineKeyboardButton(
        "🔄 Ещё 6", callback_data=f"lib_admin:del_cat:{src_to_kind_tag(kind)}:{category}")])
    rows.append([InlineKeyboardButton("⬅️ Категории", callback_data=f"lib_admin:del_kind:{kind}")])
    return InlineKeyboardMarkup(rows)


def src_to_kind_tag(kind: str) -> str:
    return "image" if kind == "image" else "video"


async def _show_del_browse(query, context, kind: str, category: str) -> None:
    samples = bp.list_library_sample(kind, category, 6, [])
    chat_id = query.message.chat_id
    # Персист показанного набора — «удалить все» снесёт РОВНО эти файлы
    # (повторный list_library_sample мог бы вернуть другие при >6 в категории).
    if samples and _PENDING is not None:
        uid = query.from_user.id
        _PENDING[uid] = _PENDING.get(uid, {}) or {}
        _PENDING[uid]["lib_del_shown"] = {
            "kind": kind, "cat": category, "ids": [s["id"] for s in samples]}
        _SAVE(_PENDING)
    label = bp._cat_label(category)
    icon = "📷" if kind == "image" else "🎞"
    if not samples:
        await query.edit_message_text(f"⚠️ В «{label}» пусто.")
        return
    try:
        await query.edit_message_text(f"{icon} {label} — показываю что есть…")
    except Exception:
        pass
    await _send_previews(context, chat_id, samples, kind)
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"{icon} {label}: жми «🗑 N», чтобы удалить файл с сервера (с подтверждением).",
        reply_markup=_del_browse_kb(samples, kind, category),
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("lib_admin:"):
        return False
    user_id = update.effective_user.id
    p = query.data.split(":")
    action = p[1] if len(p) > 1 else ""
    await query.answer()

    if action == "menu":
        await query.edit_message_text(
            "📁 Библиотека B-roll — выбери действие:", reply_markup=_menu_kb())
        return True
    if action == "close":
        try:
            await query.edit_message_text("Закрыто.")
        except Exception:
            pass
        return True

    if action == "up":
        await query.edit_message_text("📤 Что грузим в библиотеку?", reply_markup=_kind_kb("up"))
        return True
    if action == "up_kind":
        kind = p[2] if len(p) > 2 else "image"
        await query.edit_message_text(
            "📁 В какую категорию?", reply_markup=_upload_cat_kb(kind))
        return True
    if action == "up_cat":
        kind = p[2] if len(p) > 2 else "image"
        cat = p[3] if len(p) > 3 else ""
        _PENDING[user_id] = _PENDING.get(user_id, {}) or {}
        _PENDING[user_id]["state"] = f"lib_admin_upload:{kind}:{cat}"
        _SAVE(_PENDING)
        what = "фото" if kind == "image" else "видео-клипы"
        await query.edit_message_text(
            f"📥 Пришли {what} (можно несколько) — добавлю в «{bp._cat_label(cat)}».\n"
            f"Когда закончишь — нажми «Готово».",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Готово", callback_data="lib_admin:up_done")],
            ]),
        )
        return True
    if action == "up_done":
        if _PENDING.get(user_id):
            _PENDING[user_id]["state"] = None
            _SAVE(_PENDING)
        await query.edit_message_text("✅ Загрузка завершена.", reply_markup=_menu_kb())
        return True

    if action == "del":
        await query.edit_message_text("🗑 Из чего удаляем?", reply_markup=_kind_kb("del"))
        return True
    if action == "del_kind":
        kind = p[2] if len(p) > 2 else "image"
        cats = bp.list_library_categories(kind)
        if not cats:
            await query.edit_message_text("⚠️ Библиотека пуста.")
            return True
        await query.edit_message_text(
            "🗑 Категория для удаления:", reply_markup=_del_cat_kb(kind, cats))
        return True
    if action == "del_cat":
        kind = p[2] if len(p) > 2 else "image"
        cat = p[3] if len(p) > 3 else ""
        await _show_del_browse(query, context, kind, cat)
        return True
    if action == "delpick":
        src = p[2] if len(p) > 2 else "photo"
        cat = p[3] if len(p) > 3 else ""
        item_id = p[4] if len(p) > 4 else ""
        kind = "image" if src == "photo" else "video"
        path = bp.lookup_library_path(kind, item_id)
        name = Path(path).name if path else item_id
        await query.edit_message_text(
            f"Удалить «{name}» из библиотеки безвозвратно?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑 Да, удалить", callback_data=f"lib_admin:delyes:{src}:{cat}:{item_id}")],
                [InlineKeyboardButton("⬅️ Отмена", callback_data=f"lib_admin:del_cat:{kind}:{cat}")],
            ]),
        )
        return True
    if action == "delall":
        src = p[2] if len(p) > 2 else "photo"
        cat = p[3] if len(p) > 3 else ""
        kind = "image" if src == "photo" else "video"
        shown = (_PENDING.get(user_id, {}) or {}).get("lib_del_shown") or {}
        ids = shown.get("ids", []) if shown.get("kind") == kind and shown.get("cat") == cat else []
        if not ids:
            await query.edit_message_text(
                "⚠️ Набор устарел — открой категорию заново.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ К категории", callback_data=f"lib_admin:del_cat:{kind}:{cat}")]]))
            return True
        await query.edit_message_text(
            f"Удалить ВСЕ {len(ids)} показанных файлов безвозвратно?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"🗑 Да, удалить все {len(ids)}",
                                      callback_data=f"lib_admin:delallyes:{src}:{cat}")],
                [InlineKeyboardButton("⬅️ Отмена", callback_data=f"lib_admin:del_cat:{kind}:{cat}")],
            ]))
        return True
    if action == "delallyes":
        src = p[2] if len(p) > 2 else "photo"
        cat = p[3] if len(p) > 3 else ""
        kind = "image" if src == "photo" else "video"
        shown = (_PENDING.get(user_id, {}) or {}).get("lib_del_shown") or {}
        ids = shown.get("ids", []) if shown.get("kind") == kind and shown.get("cat") == cat else []
        ok, fail = _delete_many(kind, ids)
        if _PENDING.get(user_id):
            _PENDING[user_id].pop("lib_del_shown", None)
            _SAVE(_PENDING)
        _LOGGER.info(f"[lib_admin] bulk deleted {ok} (fail {fail}) {kind}/{cat} by {user_id}")
        await query.edit_message_text(
            f"🗑 Удалено: {ok}" + (f" · не найдено: {fail}" if fail else ""),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ К категории", callback_data=f"lib_admin:del_cat:{kind}:{cat}")],
                [InlineKeyboardButton("📁 Меню", callback_data="lib_admin:menu")],
            ]))
        return True
    if action == "delyes":
        src = p[2] if len(p) > 2 else "photo"
        cat = p[3] if len(p) > 3 else ""
        item_id = p[4] if len(p) > 4 else ""
        kind = "image" if src == "photo" else "video"
        name = delete_library_file(kind, item_id)
        if name:
            _LOGGER.info(f"[lib_admin] deleted {kind}/{name} by {user_id}")
            await query.edit_message_text(
                f"🗑 Удалено: {name}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ К категории", callback_data=f"lib_admin:del_cat:{kind}:{cat}")],
                    [InlineKeyboardButton("📁 Меню", callback_data="lib_admin:menu")],
                ]),
            )
        else:
            await query.edit_message_text("⚠️ Файл уже удалён или не найден.")
        return True

    return False


async def handle_upload_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Приём файла в state lib_admin_upload:<kind>:<cat>. True если обработан."""
    user_id = update.effective_user.id
    data = _PENDING.get(user_id, {}) or {}
    st = data.get("state") or ""
    if not st.startswith("lib_admin_upload:"):
        return False
    _, _, rest = st.partition(":")
    kind, _, category = rest.partition(":")
    msg = update.message
    # файл: фото / видео / документ
    file_obj = None
    orig_name = None
    if kind == "image":
        if msg.photo:
            file_obj = msg.photo[-1]
            orig_name = f"IMG_{file_obj.file_unique_id}.jpg"
        elif msg.document and (msg.document.mime_type or "").startswith("image/"):
            file_obj = msg.document
            orig_name = msg.document.file_name or f"IMG_{msg.document.file_unique_id}.jpg"
    else:  # video
        if msg.video:
            file_obj = msg.video
            orig_name = (getattr(msg.video, "file_name", None)
                         or f"VID_{msg.video.file_unique_id}.mp4")
        elif msg.document:
            file_obj = msg.document
            orig_name = msg.document.file_name or f"VID_{msg.document.file_unique_id}.mp4"
    if not file_obj:
        await msg.reply_text(
            "Пришли " + ("фото." if kind == "image" else "видео-клип."))
        return True

    # >20МБ Bot API не скачает (висло «Загружаю…» → ошибка) → редирект на готовый
    # телетон-маршрут #lib (качает по MTProto без лимита, как и пополняет Артём).
    _sz = getattr(file_obj, "file_size", 0) or 0
    if kind == "video" and _sz > 20 * 1024 * 1024:
        await msg.reply_text(
            f"🎬 Клип ~{_sz / 1024 / 1024:.0f} МБ — больше 20 МБ, Telegram не отдаёт боту "
            f"напрямую.\n\nПришли его в «Избранное» Файлом с подписью "
            f"`#lib {category} имя_клипа` (имя латиницей/цифрами) — телетон скачает "
            f"в полном качестве в категорию «{category}».",
            parse_mode="Markdown",
        )
        return True

    status = await msg.reply_text("📥 Загружаю в библиотеку…")
    try:
        tg_file = await context.bot.get_file(
            file_obj.file_id, read_timeout=60, connect_timeout=30)
        tmp = Path(tempfile.mkdtemp(prefix="lib_up_"))
        local = tmp / _safe_name(orig_name)
        await tg_file.download_to_drive(
            str(local), read_timeout=180, write_timeout=180, connect_timeout=30)
        dest = add_file_to_library(str(local), kind, category, orig_name)
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception as e:
        _LOGGER.error(f"[lib_admin] upload failed: {e}", exc_info=True)
        await status.edit_text(f"❌ Не удалось загрузить: {e}")
        return True

    if dest:
        n = len(bp.scan_library(kind, category))
        await status.edit_text(
            f"✅ Добавлено в «{bp._cat_label(category)}»: {dest.name}\n"
            f"Теперь в категории {n} файл(ов). Пришли ещё или нажми «Готово».",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Готово", callback_data="lib_admin:up_done")],
            ]),
        )
    else:
        await status.edit_text("❌ Не удалось определить папку категории.")
    return True
