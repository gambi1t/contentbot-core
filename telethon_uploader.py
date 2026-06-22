"""
Telethon uploader for >20MB videos via Saved Messages.

Watches the user's Saved Messages. When a video with #crosspost in caption
arrives, finds the active upload_final_video state in pending.json, downloads
the video to that project's final_video.mp4, clears the state, and replies
in Saved Messages with a confirmation.

Run as a systemd service alongside bot.py on the same server.
First-time authentication requires interactive input of the Telegram code.
"""
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path

from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeVideo, DocumentAttributeFilename

# Public Telegram Desktop credentials (leaked years ago, widely used for personal scripts)
API_ID = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"

BOT_DIR = Path(__file__).parent.resolve()
SESSION_FILE = BOT_DIR / "telethon_session"
PENDING_FILE = BOT_DIR / "pending.json"
PROJECTS_DIR = BOT_DIR / "projects"
BROLL_LIBRARY_DIR = BOT_DIR / "broll-library"
LOG_FILE = BOT_DIR / "telethon_uploader.log"

TRIGGER_TAG = "#crosspost"
# #lib <category> <name>  → save to broll-library/<category>/<name>.mp4
# e.g. "#lib apps chatgpt_main" → broll-library/apps/chatgpt_main.mp4
LIB_TAG_RE = re.compile(
    r'^#lib\s+([a-zA-Z0-9_\-]+)\s+([a-zA-Z0-9_\-]+)',
    re.IGNORECASE,
)
# #selfie → скачать БОЛЬШОЙ оригинал selfie (>20МБ) в selfie_inbox для selfie-
# пайплайна бота (path B; Bot API не качает >20МБ). Отдельно от #crosspost
# (upload_final_video), чтобы два Saved-Messages-потока не перехватывали файлы.
SELFIE_TAG = "#selfie"
SELFIE_INBOX = BOT_DIR / "selfie_inbox"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("telethon_uploader")


def _safe_title(title: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "", title)[:60].strip()


def _project_dir_from_data(data: dict) -> Path | None:
    notion_id = data.get("notion_page_id")
    if not notion_id:
        return None
    title = data.get("card_data", {}).get("title", "untitled")
    folder_name = f"{notion_id[:8]}_{_safe_title(title)}"
    d = PROJECTS_DIR / folder_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _find_active_upload() -> tuple[str, dict, Path] | None:
    """Find a user in pending.json with state=upload_final_video.
    Returns (user_id, data, project_dir) or None.
    """
    if not PENDING_FILE.exists():
        return None
    try:
        pending = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"Failed to read pending.json: {e}")
        return None
    for uid, data in pending.items():
        if data.get("state") == "upload_final_video":
            proj = _project_dir_from_data(data)
            if proj:
                return uid, data, proj
    return None


def _clear_upload_state(user_id: str):
    try:
        pending = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
        if user_id in pending:
            pending[user_id]["state"] = None
            PENDING_FILE.write_text(
                json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    except Exception as e:
        logger.error(f"Failed to clear upload state: {e}")


# ── selfie path B (большой оригинал >20МБ) ────────────────────────────────
# id владельца аккаунта (Saved Messages — его), ставится в main() после get_me().
OWNER_ID: "int | None" = None


def _find_active_selfie(pending: dict, owner_id: "int | None" = None) -> "tuple[str, dict] | None":
    """Юзер, ждущий большое selfie-видео (path B), в state 'selfie_waiting_video'.

    Saved Messages принадлежат ВЛАДЕЛЬЦУ (telethon залогинен его аккаунтом) →
    #selfie-загрузка ВСЕГДА от него. Матчим именно его uid (owner_id), а НЕ
    «первого ждущего» — иначе чужое/устаревшее selfie_waiting_video (напр.
    тест-аккаунт) перехватит файл. Без owner_id — фолбэк на первого (тесты).
    Отдельно от _find_active_upload (#crosspost), чтобы потоки не пересекались."""
    if owner_id is not None:
        d = pending.get(str(owner_id))
        if d and d.get("state") == "selfie_waiting_video":
            return str(owner_id), d
        return None
    for uid, data in pending.items():
        if data.get("state") == "selfie_waiting_video":
            return uid, data
    return None


def _selfie_target_path(user_id: str) -> Path:
    """Стабильный путь в selfie_inbox. Качаем в <path>.part → atomic rename,
    чтобы бот не прочитал полуфайл."""
    return SELFIE_INBOX / f"selfie_{user_id}.mp4"


def _atomic_write_pending(pending: dict) -> None:
    """Запись pending.json через tmp + os.replace — читатель не видит битый JSON.
    Не полный кросс-процесс-лок (бот тоже пишет), но #selfie-запись идёт пока
    юзер в Избранном, не жмёт кнопки бота → окно гонки минимально."""
    tmp = PENDING_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, PENDING_FILE)


client = TelegramClient(str(SESSION_FILE), API_ID, API_HASH)


@client.on(events.MessageEdited(from_users="me", outgoing=True))
async def handle_saved_edited(event):
    """Re-run the Saved Messages handler when a caption is edited.

    Artem often uploads the video first and adds #crosspost/#lib via Edit
    afterwards.  NewMessage alone misses that path — this mirrors the logic.
    """
    await handle_saved(event)


@client.on(events.NewMessage(from_users="me", outgoing=True))
async def handle_saved(event):
    """Handle new message in Saved Messages (messages sent by self to self)."""
    msg = event.message
    caption = (msg.message or "").strip()

    # Only videos / video documents
    is_video = bool(msg.video)
    if not is_video and msg.document:
        for attr in msg.document.attributes:
            if isinstance(attr, DocumentAttributeVideo):
                is_video = True
                break
            if isinstance(attr, DocumentAttributeFilename):
                if attr.file_name and attr.file_name.lower().endswith(
                    (".mp4", ".mov", ".mkv", ".webm")
                ):
                    is_video = True
                    break
    if not is_video:
        return

    size_mb = (msg.file.size or 0) / 1024 / 1024

    # --- #lib <category> <name>: save video into broll-library/<category>/<name>.mp4 ---
    lib_match = LIB_TAG_RE.match(caption)
    if lib_match:
        category = lib_match.group(1).lower()
        name = lib_match.group(2).lower()
        # Basic safety: no traversal, no weird chars (regex already guards)
        cat_dir = BROLL_LIBRARY_DIR / category
        cat_dir.mkdir(parents=True, exist_ok=True)
        target = cat_dir / f"{name}.mp4"
        if target.exists():
            await event.reply(
                f"⚠️ broll-library/{category}/{name}.mp4 уже существует. "
                f"Переименуй или удали старый."
            )
            return
        logger.info(f"#lib upload: category={category}, name={name}, size={size_mb:.1f} MB")
        status = await event.reply(
            f"📥 Сохраняю в библиотеку: {category}/{name}.mp4 ({size_mb:.1f} MB)..."
        )
        try:
            await msg.download_media(file=str(target))
            actual = target.stat().st_size / 1024 / 1024
            logger.info(f"Saved {actual:.1f} MB to {target}")
            await status.edit(
                f"✅ Сохранено в библиотеку\n"
                f"📁 broll-library/{category}/{name}.mp4 ({actual:.1f} MB)\n\n"
                f"Теперь бот найдёт этот клип автоматически по ключевым словам категории «{category}»."
            )
        except Exception as e:
            logger.error(f"#lib download failed: {e}", exc_info=True)
            await status.edit(f"❌ Ошибка сохранения: {e}")
        return

    # --- #selfie: скачать большой оригинал selfie для selfie-пайплайна бота ---
    if SELFIE_TAG in caption.lower():
        try:
            pending = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"#selfie: failed to read pending.json: {e}")
            await event.reply("❌ Не удалось прочитать состояние бота. Попробуй ещё раз.")
            return
        active = _find_active_selfie(pending, OWNER_ID)
        if not active:
            await event.reply(
                "⚠️ Бот сейчас не ждёт selfie-видео.\n\n"
                "Сначала в @panferovai_contentbot набери /selfie, потом пришли "
                "сюда оригинал ДОКУМЕНТОМ с подписью #selfie."
            )
            return
        uid, _data = active
        SELFIE_INBOX.mkdir(parents=True, exist_ok=True)
        target = _selfie_target_path(uid)
        part = target.with_suffix(target.suffix + ".part")
        logger.info(f"#selfie download: uid={uid}, size={size_mb:.1f} MB")
        status = await event.reply(
            f"📥 Скачиваю selfie {size_mb:.1f} MB (оригинал, без сжатия)...")

        last_percent = [0]

        def progress(current, total):
            if total:
                pct = int(current * 100 / total)
                if pct >= last_percent[0] + 10:
                    last_percent[0] = pct
                    logger.info(f"#selfie download progress: {pct}%")

        try:
            await msg.download_media(file=str(part), progress_callback=progress)
            os.replace(part, target)
        except Exception as e:
            logger.error(f"#selfie download failed: {e}", exc_info=True)
            try:
                part.unlink()
            except OSError:
                pass
            await status.edit(f"❌ Ошибка скачивания: {e}")
            return

        # Перечитать pending (мог измениться за долгую скачку) и записать путь
        # к оригиналу — бот подхватит его на «Обработать видео».
        try:
            pending = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
            if uid in pending and pending[uid].get("state") == "selfie_waiting_video":
                pending[uid]["selfie_source"] = str(target)
                pending[uid]["selfie_video_ready"] = True
                _atomic_write_pending(pending)
            else:
                logger.warning(f"#selfie: uid={uid} no longer waiting, downloaded anyway")
        except Exception as e:
            logger.error(f"#selfie: failed to update pending: {e}")

        actual = target.stat().st_size / 1024 / 1024
        logger.info(f"#selfie saved {actual:.1f} MB to {target}")
        await status.edit(
            f"✅ Видео получено ({actual:.1f} MB, оригинал)\n\n"
            f"Вернись в @panferovai_contentbot и нажми «✅ Обработать видео»."
        )
        return

    # Require trigger tag to avoid processing random videos in Saved Messages
    if TRIGGER_TAG not in caption.lower():
        logger.info("Video in Saved Messages without #crosspost/#lib/#selfie tag, ignoring")
        return

    logger.info(f"Got video with #crosspost tag, size {size_mb:.1f} MB")

    active = _find_active_upload()
    if not active:
        await event.reply(
            "⚠️ Нет активного ожидания видео в боте.\n\n"
            "Сначала в @panferovai_contentbot нажми 'Загрузить готовый ролик' "
            "на нужной карточке, потом пришли видео сюда."
        )
        return

    user_id, data, proj = active
    final_path = proj / "final_video.mp4"

    status = await event.reply(
        f"📥 Скачиваю {size_mb:.1f} MB в проект «{data.get('card_data', {}).get('title', 'untitled')}»..."
    )

    last_percent = [0]

    def progress(current, total):
        if total:
            pct = int(current * 100 / total)
            if pct >= last_percent[0] + 10:
                last_percent[0] = pct
                logger.info(f"Download progress: {pct}%")

    try:
        await msg.download_media(file=str(final_path), progress_callback=progress)
    except Exception as e:
        logger.error(f"Download failed: {e}", exc_info=True)
        await status.edit(f"❌ Ошибка скачивания: {e}")
        return

    actual_size = final_path.stat().st_size / 1024 / 1024
    _clear_upload_state(user_id)

    card_prefix = data.get("upload_final_card_id", "")[:8]
    logger.info(f"Saved {actual_size:.1f} MB to {final_path}")

    await status.edit(
        f"✅ Видео сохранено ({actual_size:.1f} MB)\n"
        f"📁 {proj.name}\n\n"
        f"Вернись в @panferovai_contentbot и нажми «📢 Кросс-постинг» на карточке."
    )


async def main():
    logger.info("Starting Telethon uploader...")
    await client.start()
    me = await client.get_me()
    global OWNER_ID
    OWNER_ID = me.id
    logger.info(f"Logged in as {me.first_name} (@{me.username}) id={me.id} (owner for #selfie)")
    logger.info(f"Listening on Saved Messages for videos with {TRIGGER_TAG} / #lib / {SELFIE_TAG} tags")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
