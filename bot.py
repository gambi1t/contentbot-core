import os
import re
import html as html_mod
import json
import random
import time
import tempfile
import logging
import asyncio
import subprocess
import requests
from logging.handlers import RotatingFileHandler
from pathlib import Path
from io import BytesIO
from datetime import datetime, timedelta

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    TypeHandler,
    filters,
)
import anthropic
from notion_client import Client as NotionClient
from PIL import Image, ImageDraw, ImageFont
import textwrap
from elevenlabs import ElevenLabs

load_dotenv(override=True)

# --- Logging: console + file ---
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logger = logging.getLogger("content_bot")
logger.setLevel(logging.DEBUG)

# Console — INFO and above
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))

# File — DEBUG and above, rotates at 5MB, keeps 3 files
file_handler = RotatingFileHandler(
    LOG_DIR / "bot.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(funcName)s - %(message)s"))

# Error file — only ERRORs, separate file for quick review
error_handler = RotatingFileHandler(
    LOG_DIR / "errors.log", maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8"
)
error_handler.setLevel(logging.ERROR)
error_handler.setFormatter(logging.Formatter("%(asctime)s - %(funcName)s - %(message)s\n%(exc_info)s"))

logger.addHandler(console_handler)
logger.addHandler(file_handler)
logger.addHandler(error_handler)

# Suppress noisy third-party loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

# --- Clients ---
notion = NotionClient(auth=os.getenv("NOTION_TOKEN"))

# Claude client: подписка (CLAUDE_CODE_OAUTH_TOKEN) приоритет → fallback на
# pay-per-token API (ANTHROPIC_API_KEY). Раньше всегда шёл pay-per-token,
# хотя подписка была подключена для auto_broll.py. 27 May 2026 Артём указал:
# «это неприемлемая цена, подключи всё на подписку».
# SubscriptionClient — drop-in замена `anthropic.Anthropic`, под капотом
# subprocess вызов `claude` CLI с OAuth-токеном (вызовы покрываются flat
# fee подписки Max/Pro). Auth-логика повторяет auto_broll.py:_run_claude.
_claude_oauth_token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
if _claude_oauth_token:
    from claude_subscription import SubscriptionClient
    claude = SubscriptionClient(oauth_token=_claude_oauth_token)
    logger.info("[claude] Auth: подписка через CLAUDE_CODE_OAUTH_TOKEN (CLI wrapper)")
else:
    claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    logger.info("[claude] Auth: pay-per-token API через ANTHROPIC_API_KEY (нет OAuth)")
NOTION_DB = os.getenv("NOTION_DATABASE_ID")
NOTION_GUIDES_DB = os.getenv("NOTION_GUIDES_DB_ID")
# Публичный поддомен notion.site для ссылок на гайды. Per-tenant: у каждого
# воркспейса свой subdomain. Дефолт — воркспейс Артёма (бренд default); для
# Максима/клиентов задаётся в .env, иначе ссылки на гайды будут вести в чужой
# воркспейс (кросс-тенант утечка).
NOTION_GUIDES_PUBLIC_HOST = os.getenv(
    "NOTION_GUIDES_PUBLIC_HOST", "difficult-relative-e9b.notion.site"
)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# --- AI disclosure footer (appended to all crosspost descriptions) ---
# YouTube requires synthetic media disclosure since July 2025; Instagram and
# TikTok don't enforce it yet, but adding it everywhere is safer and builds
# trust with the audience.  Change the text here — it auto-applies everywhere.
AI_DISCLOSURE = (
    "\n\n—\n"
    "В производстве использованы AI-инструменты. "
    "Сценарий и идеи — авторские."
)

# --- Instagram comment-to-DM default fallback ---
# Link sent in auto-reply DM when the project has no post-specific URL.
# Set this to your Telegram "master post" (pinned or constantly-updated
# aggregator of useful links). Change it in .env without touching code.
DEFAULT_DM_REPLY_URL = os.getenv("DEFAULT_DM_REPLY_URL", "https://t.me/artempanferov_ai")


def _build_dm_reply_text(url: str, card_title: str = "") -> str:
    """Compose a warm, natural DM auto-reply with the given link.

    Not rotated on purpose — the same person only gets one DM per post
    trigger, so repetition within a single recipient is impossible.
    The *public* comment reply is the one that needs rotation (see
    instagram_dm.COMMENT_REPLY_TEMPLATES).
    """
    opener = "Привет! Спасибо что откликнулся 🙌"
    body = (
        f"Как и обещал — всё по теме «{card_title}» тут:"
        if card_title
        else "Как и обещал — держи ссылку:"
    )
    tail = "Заходи, будет интересно ✨"
    return f"{opener}\n\n{body}\n\n{url}\n\n{tail}"


# --- Stock video APIs ---
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY")

# --- Cross-posting (direct API) ---
from crosspost import (
    youtube_upload_short, youtube_is_connected, youtube_auth_url, youtube_exchange_code,
    instagram_upload_reel, instagram_is_connected, instagram_auth_url, instagram_exchange_code,
    upload_video_to_temp_hosting,
    telegram_post_to_channel, get_available_platforms,
    tiktok_upload_video, tiktok_is_connected,
    vk_upload_clip, vk_is_connected, vk_get_auth_url,
    TELEGRAM_CHANNEL_ID,
)
from instagram_dm import save_keyword_for_post, start_webhook_server
import launch_monitor
from video_assembler import (
    assemble_auto_montage,
    AssemblyError,
    build_bookend_montage_plan,
    _find_broll,
)
from tg_post_handlers import (
    register as register_tgpost,
    handle_tgpost_text,
    is_tgpost_state,
)
import tg_post_writer
from publish_helpers import (
    needs_description,
    build_ig_caption,
    extract_script_text,
    extract_video_topic,
)
from selfie import handlers as selfie_handlers
from assemble_helpers import music_button_label
from fal_handlers import (
    register_fal_handlers,
    is_fal_state,
    consume_fal_prompt,
)
from heygen_test_handlers import (
    register_heygen_test_handlers,
    is_heygen_test_state,
    consume_heygen_test_photo,
    consume_heygen_test_audio,
    STATE_PHOTO as HEYGEN_TEST_STATE_PHOTO,
    STATE_AUDIO as HEYGEN_TEST_STATE_AUDIO,
    # V3 Image-to-Video — переиспользуем для shoes-flow с custom photo.
    # Без регистрации avatar_id (которая упирается в лимит 3 photo avatars
    # на free tier). Цена та же что у обычного Avatar 3/4.
    _heygen_v3_image_to_video as heygen_v3_image_to_video,
    _heygen_v3_check_status as heygen_v3_check_status,
)

# ── Billing (pay-per-use для клиентских роликов) ──
# Самостоятельный пакет billing/ — БД, API, inline-меню клиента/админа.
# Интеграция: init() + register() в main(), плюс 4 точки gate/charge в пайплайне.
from billing import api as billing_api, handlers as billing_handlers
from billing.config import is_admin as _billing_is_admin, SUPPORT_CONTACT as _BILLING_SUPPORT

BILLING_ENABLED = os.getenv("BILLING_ENABLED", "0").strip() == "1"


def _billing_is_bypassed(user_id: int) -> bool:
    """True = skip billing entirely for this user.

    Bypass in two cases:
      1. Global flag ``BILLING_ENABLED=0`` — feature is off across the bot
         (default for rollout — no charges, no registration checks).
      2. User is in ``ADMIN_TELEGRAM_IDS`` — admins always run unlimited,
         regardless of the global flag.
    """
    if not BILLING_ENABLED:
        return True
    return _billing_is_admin(user_id)


async def _billing_charge_if_needed(
    user_id: int, video_id: str | None, trigger: str,
) -> None:
    """Idempotent charge trigger. Called at 3 lifecycle points (crosspost,
    download_project, final_send) — whichever fires FIRST actually debits
    the balance; subsequent calls get 'already_charged' and are no-ops.

    Silently bypassed for admins / BILLING_ENABLED=0. Never raises —
    billing must not break the user's video flow.
    """
    if _billing_is_bypassed(user_id):
        return
    if not video_id:
        return
    try:
        result = await asyncio.to_thread(
            billing_api.charge_video, video_id, trigger,
        )
        logger.info(
            f"[billing] charge {trigger}: {video_id[:12]}... → "
            f"status={result.status} amount={result.amount_rub} "
            f"balance_after={result.new_balance} msg={result.message}"
        )
    except Exception as e:
        logger.error(f"[billing] charge_video failed ({trigger}): {e}", exc_info=True)


async def _billing_gate_middleware(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Global billing gate — runs before every other handler (group=-1).

    Checks who's sending this update and decides whether the pipeline is
    accessible. If the user is blocked, sends a polite rejection and
    raises ApplicationHandlerStop to cancel all downstream handlers
    (CommandHandlers, CallbackQueryHandlers, MessageHandlers).

    Previous bug: gate was only in /start. A non-admin user could type
    /notion or /script (or just dictate an idea) and bypass billing.

    Access rules:
      - BILLING_ENABLED=0 → everyone passes (rollout mode).
      - Admins (ADMIN_TELEGRAM_IDS) → always pass.
      - Registered + active clients → pass; pipeline + client menus work.
      - Everyone else → blocked with support contact hint.
    """
    if not BILLING_ENABLED:
        return  # feature off — no gating
    user = update.effective_user
    if user is None:
        return  # system update — pass
    user_id = user.id
    if _billing_is_admin(user_id):
        return  # admin always wins

    # Not admin — must be an active registered client
    try:
        client = await asyncio.to_thread(billing_api.get_client, user_id)
    except Exception as e:
        logger.error(f"[billing_gate] get_client failed: {e}", exc_info=True)
        return  # fail-open on technical error — don't lock admins out of their bot
    if client and client.is_active:
        return  # proceed to normal handlers

    # Rejected — show polite message with TWO buttons for maximum reach:
    #   1. «Написать менеджеру» — прямая ссылка на t.me/<support handle>,
    #      открывает ЛС с менеджером одним нажатием. Там клиент вручную
    #      вводит/пересылает ID. Работает везде.
    #   2. «Переслать ID» — t.me/share с пред-заполненным текстом. Открывает
    #      «выбор чата из контактов». Удобно если клиент уже подписан на
    #      менеджера, но требует выбора получателя.
    # Плюс ID в <code> — один тап на мобильном = копия в буфер.
    import urllib.parse as _urlparse
    # _BILLING_SUPPORT обычно "@postulataistudio" — обрезаем @ для URL.
    support_handle = (_BILLING_SUPPORT or "").lstrip("@")
    manager_url = (
        f"https://t.me/{support_handle}" if support_handle else None
    )
    share_text = (
        f"Здравствуйте! Хочу подключиться к боту контент-студии. "
        f"Мой Telegram ID: {user_id}"
    )
    share_url = (
        "https://t.me/share/url?"
        + _urlparse.urlencode({"url": " ", "text": share_text})
    )
    reject_text = (
        "🚫 <b>Доступ ограничен</b>\n\n"
        "Этот бот работает только по приглашению.\n\n"
        f"👤 Ваш Telegram ID: <code>{user_id}</code>\n"
        "<i>(тап на числе — оно скопируется в буфер)</i>\n\n"
        "<b>Как подключиться:</b>\n"
        f"1. Напишите менеджеру {_BILLING_SUPPORT} (кнопка ниже откроет чат)\n"
        "2. Пришлите ему свой Telegram ID\n"
        "3. Менеджер активирует аккаунт, и вы сможете работать"
    )
    kb_rows = []
    if manager_url:
        kb_rows.append([InlineKeyboardButton(
            f"💬 Написать {_BILLING_SUPPORT}",
            url=manager_url,
        )])
    kb_rows.append([InlineKeyboardButton(
        "📨 Переслать ID (выбор чата)",
        url=share_url,
    )])
    share_kb = InlineKeyboardMarkup(kb_rows)
    try:
        if update.callback_query:
            await update.callback_query.answer(
                "Доступ ограничен. Свяжитесь с менеджером.",
                show_alert=True,
            )
        elif update.message:
            await update.message.reply_text(
                reject_text, parse_mode="HTML",
                reply_markup=share_kb,
            )
    except Exception as e:
        logger.warning(f"[billing_gate] reject message failed: {e}")

    logger.info(
        f"[billing_gate] blocked user={user_id} "
        f"username=@{user.username or '-'} full_name={user.full_name!r}"
    )
    # Halt the chain — no CommandHandler or MessageHandler below runs.
    raise ApplicationHandlerStop


async def _billing_gate_or_reject(update: Update, user_id: int) -> bool:
    """Gate: allow pipeline if user is bypassed or is a registered client.

    Returns True = proceed. False = user was already told why they can't,
    caller should return without doing anything else.
    """
    if _billing_is_bypassed(user_id):
        return True
    client = billing_api.get_client(user_id)
    if client and client.is_active:
        return True
    # Unregistered or deactivated — reject politely
    msg = (
        "👋 Для работы с ботом нужна регистрация.\n\n"
        f"Напишите {_BILLING_SUPPORT} — подключим быстро, "
        "расскажем тарифы (от 150 ₽ за ролик)."
    )
    try:
        if update.message:
            await update.message.reply_text(msg)
        elif update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(msg)
    except Exception:
        pass
    return False

# --- ElevenLabs ---
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID")
elevenlabs_client = ElevenLabs(api_key=ELEVENLABS_API_KEY) if ELEVENLABS_API_KEY else None

# Voice settings (from user's ElevenLabs UI)
VOICE_SETTINGS = {
    "stability": 0.60,
    "similarity_boost": 0.75,
    "style": 0.05,
    "speed": 1.00,
}

# --- Fish Audio (alternative TTS) ---
FISH_API_KEY = os.getenv("FISH_API_KEY")
FISH_VOICE_ID = os.getenv("FISH_VOICE_ID")  # "Голос Технологий"

# --- HeyGen ---
HEYGEN_API_KEY = os.getenv("HEYGEN_API_KEY")
HEYGEN_GROUP_ID = "eb6cbe59c1044d05b2c6fc959ccf98c0"  # shared HeyGen group (Artem's account, includes Maksim's avatar)
HEYGEN_LOOKS = {
    "look1": {"id": "181b18f4c3bc49889eece6a984a845bf", "name": "Стоя (главный)"},
    "look2": {"id": "9a3fa1911a2a43fbbd428fd186a254bf", "name": "Белая футболка"},
    "look3": {"id": "820297f786b742de8943e9abe214762e", "name": "Белый свитер"},
    "look4": {"id": "66cd5a3f943646bf8ee568311a511e48", "name": "С микрофоном"},
    "look5": {"id": "c79726fbd35d4e11b65fead2575724dd", "name": "Футболка, фон мягкий"},
    "look6": {"id": "ddb6933aee3540f69e32cae3e5a2737b", "name": "Delat"},
    # look7 "Чёрный свитер" removed Apr 15 2026 — Avatar 3 rendered it poorly
    # (only lips moved, rest of face frozen).
    "look8": {"id": "ab66f76258424792940290244e2decda", "name": "Бежевый свитер"},
    # look9 "Белая рубашка" removed Apr 15 2026 — same issue as Чёрный свитер
    # under Avatar 3 (stiff face, only lip-sync).
    "look10": {"id": "f09aba54e96b4b7184c51b54d3b30260", "name": "Фиолетовый свитшот"},
}

# --- Brand profiles ---
# Each brand can override:
#   heygen_avatar_id   — string or None (None → use HEYGEN_LOOKS)
#   eleven_voice_id    — string or None (None → ELEVENLABS_VOICE_ID from .env)
#   eleven_model_id    — string (always set; default "eleven_multilingual_v2")
# Switch via /brand <name>. Active brand persists in-memory only — reset to
# "default" on bot restart. Intentional: prevents stale shoe-brand voice from
# surprising the next experiment session.
BRANDS = {
    "default": {
        "heygen_avatar_id": None,
        "eleven_voice_id": None,
        "eleven_model_id": "eleven_multilingual_v2",
        "description": "Артём Панфёров — эксперимент, клиенты",
        # No script overlay → use SCRIPT_PROMPT as-is (it's written for Artem).
        "script_prompt_override": None,
        # Auto-trim policy for videos uploaded via «📥 Готовые материалы».
        # None = keep full length (experiment uses long-form footage as-is).
        # For product brands we clamp to short clips so smart-mix works well.
        "auto_trim_video_sec": None,
        # Type of the «Status» property in the Notion content DB.
        # "status" = Notion built-in Status (kanban groups, default for Artem).
        # "select" = plain Select (used by maksim — Notion API can't create
        # `status` options programmatically, so the maksim DB was set up with
        # a regular select instead).
        "notion_status_type": "status",
        # Name of the «Rubric» property in the Notion content DB.
        # Artem's DB has it as "Рубрика " (with trailing space — historical
        # bug). Maksim's DB has it without a trailing space.
        "notion_rubric_property": "Рубрика ",
        # Default platforms (multi_select values in Notion «Площадки»)
        # used when creating a new card without explicit platforms. These
        # MUST match the Notion property options for the brand's DB.
        "platforms": [
            "Мой инста panferov.ai",
            "youtube shorts",
            "мой телеграм канал",
        ],
    },
    "shoes": {
        "heygen_avatar_id": "b9994460d02e4d149879e85b81d5ac37",
        # Brand-scoped HeyGen looks — when non-empty, the look picker screen
        # shows ONLY these (not the global HEYGEN_LOOKS which is Artem's face).
        # Add more when Artem sends extra avatar IDs for the shoe brand.
        "heygen_looks": {
            "main": {
                "id": "b9994460d02e4d149879e85b81d5ac37",
                "name": "Обувной — основной",
            },
        },
        "eleven_voice_id": "AB9XsbSA4eLG12t2myjN",
        "eleven_model_id": "eleven_v3",
        "description": "Обувной бренд (фото-аватар HeyGen, голос из библиотеки)",
        # Clamp uploaded videos to 5s — smart-mix pairs shoe videos with
        # 2.8s photo split-clips; longer videos unbalance the rhythm.
        "auto_trim_video_sec": 5,
        # Smart-mix layout config (per-brand). Used by `_plan_smart_mixed_montage`
        # via assemble_auto_montage. Tuned 4 мая 2026 for shoes mass-production:
        #   intro 2 сек full-screen avatar (hook with face)
        #   outro 3 сек full-screen avatar (CTA: "напиши обувка в комментариях")
        #   photo segments — dynamic duration computed from
        #     (avatar_dur - intro - outro) / N_photos, clamped [1.8, 3.0]
        #   This keeps rhythm comfortable regardless of N (was hardcoded 2.8s).
        "smart_mix": {
            "intro_dur": 2.0,
            "outro_dur": 3.0,
            "photo_dur_min": 1.8,
            "photo_dur_max": 3.0,
            "photo_dur_default": 2.8,
        },
        # Default assembly layout for this brand. When set, the assembly menu
        # shows a single one-tap button instead of the 4×2 layout matrix —
        # for mass-production. Per-card override via Notion property.
        "default_assembly_layout": "smart",
        "default_assembly_subs": True,
        # Cover text prompt override — default COVER_TEXT_PROMPT заточен под
        # AI-экспертный бренд (провокация, неудобная правда, шок-цифры), что
        # для женской обувной рекламы не работает — выдаёт штампы типа
        # «такую не найдёшь». Здесь — aspirational / чувственный / премиум-тон.
        "cover_prompt_override": """Ты — редактор обложек для Instagram Reels / TikTok обувного бренда «Обувка86».

Аудитория — женщины 25–45, ищут редкую нестандартную обувь под свой размер и стиль. Тон — взрослый, уверенный, чувственный, премиальный. Никакой инфоцыганщины, никакой провокации ради провокации, никаких «шок-цифр».

ЗАДАЧА:
По сценарию ролика придумать короткий текст для обложки — 3–8 слов, до 40 символов. Обложка должна вызывать желание рассмотреть пару — не интригу через шок, а притяжение через образ.

РАЗРЕШЁННЫЕ ОПОРНЫЕ ТЕМЫ (из FACTS обувного бренда):
— Итальянские комплектующие
— Пошив под размер клиента
— Коллекция весна-лето 2026
— Редкие формы и цвета, не массовый ретейл
— Индивидуальность, «не повторят»

ТОНЫ, КОТОРЫЕ РАБОТАЮТ (обувная/fashion реклама):
1. Парадокс-интрига: «Её ещё не сшили», «Пара, которой нет»
2. Кастом + география: «Италия под твой размер», «Флоренция на каждый день»
3. Контраст с массмаркетом: «Не из ТЦ», «Обувь против тиража»
4. Чувственный образ: «Та самая пара», «Кожа, ты, утро»
5. Уверенное утверждение: «Под заказ. Под тебя.»

ЗАПРЕЩЕНО:
— Штампы продавца: «такую не найдёшь», «коллекция, которой нет в магазинах», «обувь, которую не повторят»
— Пересказ сценария в лоб (если в сценарии «в ТЦ такую не найдёшь» — этот текст на обложку НЕ берём, это уже в ролике)
— Любые выдуманные цифры (количество пар, %, годы на рынке и т.п.)
— «Единственная в своём роде», «мини-серия» — без подтверждения фактом
— Сленг и англицизмы
— Эмодзи, многоточия, восклицательные знаки
— Провокация и кликбейт как для AI-роликов — тон совсем другой

ПРОЦЕСС:
1. Прочитай сценарий.
2. Мысленно придумай 15 вариантов — разных тонов (парадокс, кастом, география, чувство).
3. Для каждого проверь: звучит ли это как fashion-слоган, а не штамп продавца?
4. Выбери 5 лучших — каждый на новой строке, без нумерации и кавычек.""",
        # Brand-specific system prompt for scripts. Replaces the default
        # (which is written for Artem's personal AI-expert brand and is
        # completely wrong for a women's shoe store). Includes a FACTS block
        # — everything outside it is forbidden to invent.
        "script_prompt_override": """Ты — сценарист вирусного рекламного контента для обувного бренда «Обувка86» (obyvka86.ru).

Аудитория — женщины 25–45, которые устали от массового магазинного ассортимента и ищут что-то редкое, индивидуальное, под себя. Площадки: Instagram Reels, TikTok, YouTube Shorts, Telegram.

ФАКТЫ О БРЕНДЕ (ИСПОЛЬЗУЙ ТОЛЬКО ИХ, НЕ ВЫДУМЫВАЙ ДРУГИЕ):
— Бренд: Обувка86, магазин + производство обуви.
— Сайт: obyvka86.ru. Есть раздел с актуальной коллекцией.
— Продукт: женская обувь — лоферы, кроссовки, ботинки, сандалии, босоножки.
— Материалы: итальянские комплектующие (кожа, фурнитура).
— Производство: пошив под заказ по размеру клиента, а также партии под коллекции.
— Коллекция: весна-лето 2026 (текущая).
— Ключевое отличие: моделей нет в массовом ретейле, бренд собирает редкие формы и цвета, которые клиент не найдёт в обычном магазине.
— Доставка: СДЭК и Почта России по РФ, международная доставка.

КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО ПРИДУМЫВАТЬ:
— Количество пар, выпускаемых в год / в день / в месяц (нет данных — НЕ пиши).
— Фразы "единственная в своём роде", "уникальная модель", "мини-серия до N", "лимитированная серия" — если пользователь НЕ дал это как факт.
— Возраст бренда в годах ("10 лет на рынке" и т.п.) — не пиши.
— Количество клиентов, отзывов, процент возвратов — не пиши.
— Страна-производитель обуви (итальянские только КОМПЛЕКТУЮЩИЕ, не вся обувь).

МОЖНО И НУЖНО ГОВОРИТЬ:
— "Итальянские комплектующие", "итальянская кожа" (если пользователь подтвердит в идее).
— "Пошив под заказ по твоему размеру".
— "Редкая модель", "редкая форма", "нестандартный цвет" (без числовых утверждений о редкости).
— "Коллекция весна-лето 2026".
— "Моделей не найдёшь в обычном магазине" (это правда — не массовый ретейл).

ХУК (1–3 секунды): сразу цепляй визуальным или словесным контрастом. Запрещены вступления "многие думают", "давайте поговорим".

ТЕЛО (20–30 секунд): одна мысль. Не перечисление свойств, а эмоция + конкретика. Чередуй визуал и слова.

ФИНАЛ + CTA: стандартный лид-магнит по ключевому слову. В ЭТОМ сценарии CTA — "напиши «обувка» в комментариях — пришлю каталог сезона" (адаптируй под точный смысл идеи).

СТИЛЬ:
— Уверенный, спокойный, взрослый. Без инфоцыганщины.
— Без смайлов, без списков, без англицизмов и жаргона.
— 350–700 символов (30–45 секунд озвучки, зависит от темпа).
— Текст сразу пригоден для озвучки аватаром.

ЭТАЛОН (ПИШИ В ТАКОМ ЖЕ СТИЛЕ):
Тема: промо коллекции весна-лето 2026, CTA — каталог по ключевому слову «обувка».
«Эту обувь ты не найдёшь в ТЦ. Потому что её пока не сшили.
Это коллекция весна-лето 2026. Обувка86.
Итальянские комплектующие, пошив под твой размер.
Модели, которые не возьмёт ни один массовый магазин — редкие формы, нестандартные цвета, та самая пара, в которой ты сразу чувствуешь разницу.
Пока сети гоняются за тиражом, мы собираем пары для тех, кто не хочет в ней пересекаться.
Каждая пара — это решение: не взять «как у всех», а собрать ту, которую на тебе точно никто не повторит.
Напиши «обувка» в комментариях — пришлю каталог сезона. Выбирай свою.»

Почему этот эталон работает:
— Хук через визуальный парадокс ("пока не сшили") и сразу отсекает массовый ретейл.
— Все факты из FACTS-блока (коллекция 2026, Обувка86, итальянские комплектующие, пошив под размер).
— НИ ОДНОЙ выдуманной цифры или количества.
— Без "единственная в роде" и "мини-серия до N" — замена на логичное позиционирование.
— CTA прямой: ключевое слово → каталог.

ФОРМАТ ВЫВОДА:
СЦЕНАРИЙ:
[один готовый цельный текст]""",
        "notion_status_type": "status",
        "notion_rubric_property": "Рубрика ",
        "platforms": [
            "Мой инста panferov.ai",
            "youtube shorts",
            "мой телеграм канал",
        ],
    },
    "maksim": {
        # Avatar/voice IDs come from .env (HEYGEN_AVATAR_ID,
        # ELEVENLABS_VOICE_ID). Hard-coded None here so the brand inherits
        # env values — same pattern as "default".
        # Тренированные ВИДЕО-аватары Максима в его СОБСТВЕННОМ HeyGen-аккаунте.
        # Тип avatar (digital twin), не фото — лучше лип-синк/жесты, нет
        # «плывущих рук» фото-аватара.
        #
        # 27 мая 2026: финальный набор из 4 луков. Все предыдущие промежуточные
        # версии (a0bddf71/f3a502ab/90610f1a и более ранние фото-аватары)
        # выведены из обращения окончательно — в этом конфиге их нет.
        # Default `heygen_avatar_id` = улица-свитер (нейтральный outdoor).
        "heygen_avatar_id": "89408fde1ded426dbadee1dbe9357e01",
        # Непустой heygen_looks = пикер показывает ТОЛЬКО эти луки.
        # Порядок отрисовки = порядок ключей dict (Python 3.7+ insertion-ordered).
        "heygen_looks": {
            "studio_black": {
                "id": "b560db700e914b0d9b98889ce6a30b85",
                "name": "🎬 Студия — футболка",
            },
            "studio_hoodie": {
                "id": "81dfdd09940b41d6b92d00fa7328095a",
                "name": "🎬 Студия — худи",
            },
            "office_cap": {
                "id": "f5e69972c9b5430fbda5fe00b2e4f234",
                "name": "🏢 Офис — кепка",
            },
            "outdoor_sweater": {
                "id": "89408fde1ded426dbadee1dbe9357e01",
                "name": "🌲 Улица — свитер",
            },
        },
        # Новый голос Максима — клон ElevenLabs (21 мая 2026, более похожий).
        # eleven_model_id=eleven_v3 по умолчанию; переключатель моделей
        # ElevenLabs v2/v3 на озвучке сохранён (это про КАЧЕСТВО голоса,
        # не путать с версией HeyGen API).
        "eleven_voice_id": "US0Jbp64BHwpPn0TWmOF",
        "eleven_model_id": "eleven_v3",
        "description": "Максим Юмсунов — Life Drive (картинг + глэмпинг, Тюмень)",
        # Script + cover prompts live in external files (see
        # script_prompt_maksim.txt / cover_prompt_maksim.txt) instead of
        # inline strings — they're 250+ lines each (4 format branches with
        # FACTS, lime overlay rules, real Maksim-post examples), and editing
        # a .txt is friendlier than editing a Python f-string. Helper
        # `_brand_script_prompt()` / `_brand_cover_prompt()` reads the file
        # at call time (cached) and falls back to default *_PROMPT.
        "script_prompt_override": None,
        "cover_prompt_override": None,
        "script_prompt_file": "script_prompt_maksim.txt",
        "cover_prompt_file": "cover_prompt_maksim.txt",
        # Telegram channel — retention-капкан per maksim_marketing_strategy.md.
        # CTA in Reels points here; tg_post_writer publishes here on cross-post.
        "telegram_channel_handle": "@yumsunov_realbiz",
        "telegram_channel_display": "Юмсунов | Про реальный бизнес",
        # Wardrobe modes — cover-prompt and script-prompt reference these.
        # Choice depends on format_type (outdoor formats → cap; indoor/skit → no cap).
        "wardrobe_modes": {
            "outdoor_cap": (
                "Life Drive snapback (чёрная, красный патч), olive/sage hoodie, "
                "olive joggers, лавалье, природа в кадре (территория глэмпинга или картинга)"
            ),
            "indoor_no_cap": (
                "Без кепки, светлая футболка / кэжуал-худи, лавалье, "
                "студия (бордово-чёрный градиент-фон) или офис картинга"
            ),
            "outdoor_winter_cap": (
                "KLIM cap (серый меланж), терракотовая худи, джинсовая куртка с шерпой, "
                "зимний / весенний outdoor — талый снег, голые берёзки"
            ),
        },
        # Maksim records short-form videos for cross-post (TG/IG/YT/VK).
        # Karting/glamping b-roll is short bursts; clamp 5s.
        "auto_trim_video_sec": 5,
        "smart_mix": {
            "intro_dur": 2.0,
            "outro_dur": 3.0,
            "photo_dur_min": 1.8,
            "photo_dur_max": 3.0,
            "photo_dur_default": 2.8,
        },
        # Дефолт сборки Максима — Про-монтаж (одобренный формат хук → 50/50
        # → CTA), не смарт-микс (это было от обувного бренда). Меню сборки
        # для maksim рисуется отдельной веткой в card_assemble.
        "default_assembly_layout": "pro",
        "default_assembly_subs": True,
        # Maksim's Notion DB uses plain Select for Status (not the built-in
        # Status type) — see project_maksim_notion_content_db.md. The schema
        # was set up via API and Notion API can't create Status options.
        "notion_status_type": "select",
        # Maksim's DB property is "Рубрика" (no trailing space) — clean schema.
        "notion_rubric_property": "Рубрика",
        # Maksim's Notion DB uses clean platform names (set up via API
        # 7 мая 2026, see project_maksim_notion_content_db.md). MUST match
        # the «Площадки» multi_select options in his Content DB.
        "platforms": [
            "TG канал",
            "Instagram",
            "YouTube Shorts",
            "VK",
        ],
        # Рубрики Максима — ТОЧНО как опции select «Рубрика» в его Notion-базе
        # «Контент» (data_source 3586889c-…-8b2f). Классификатор и валидация
        # тянут этот список вместо рубрик Артёма, иначе в Notion попадали бы
        # чужие категории («Кейс студии», «ИИ тренды»).
        "notion_rubrics": [
            "Свободный формат",
            "Виральный ролик",
            "Средний (глэмпинг)",
            "Личный мысли | Инсайты",
            "TG-пост",
        ],
        "notion_rubric_rules": (
            "- Короткий цепляющий ролик (хук, тренд, провокация, виральная "
            "подача) → \"Виральный ролик\"\n"
            "- Идея про глэмпинг (домики, гости, отдых, природа, сезон, "
            "бронирования) → \"Средний (глэмпинг)\"\n"
            "- Личный опыт предпринимателя, ошибки, выводы, найм, управление "
            "командой → \"Личный мысли | Инсайты\"\n"
            "- Текстовый лонгрид / размышление для Telegram-канала → \"TG-пост\"\n"
            "- Если непонятно → \"Свободный формат\""
        ),
        "notion_rubric_context": (
            "автор — Максим Юмсунов, действующий предприниматель (Life Drive: "
            "картинг с 2009 + глэмпинг с 2023, Тюмень). Аудитория — "
            "предприниматели и те, кто планирует открыть глэмпинг, "
            "русскоязычные."
        ),
    },
}
# Active brand at startup. For Maksim's bot deployment set DEFAULT_BRAND=maksim
# in .env; Artem's deployment leaves it unset → "default".
_active_brand = os.getenv("DEFAULT_BRAND", "default")
if _active_brand not in BRANDS:
    logger.warning(
        f"DEFAULT_BRAND={_active_brand!r} not in BRANDS dict — falling back to 'default'"
    )
    _active_brand = "default"

# Per-call brand context. Set at the start of any handler that resolves a
# Notion card — overrides the global _active_brand for the duration of the
# request (including asyncio.to_thread calls, which copy contextvars).
import contextvars  # noqa: E402
_brand_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_brand_ctx", default=""
)


def _get_active_brand_name() -> str:
    """Return the active brand name (key in BRANDS dict).

    Resolution: per-call context (``_brand_ctx``, set from card's «Бренд»
    property) → global ``_active_brand`` (set via /brand) → "default".
    """
    ctx_name = (_brand_ctx.get() or "").strip().lower()
    name = ctx_name or _active_brand
    return name if name in BRANDS else "default"


def _get_active_brand() -> dict:
    """Return current brand profile dict (avatar_id, voice_id, model_id, …)."""
    return BRANDS.get(_get_active_brand_name(), BRANDS["default"])


def _resolve_brand_cfg(brand_name: str | None) -> dict:
    """Pick the brand profile for helpers.

    If `brand_name` is provided AND exists in BRANDS — use it.
    Otherwise — fall back to the active brand (ContextVar / global).
    Used by `_status_property_value`, `_rubric_property_name`,
    `_default_platforms`, `_status_filter_payload` so callers can target
    a specific brand instead of relying on global state.
    """
    if brand_name and brand_name in BRANDS:
        return BRANDS[brand_name]
    return _get_active_brand()


def _status_property_value(status_name: str, brand_name: str | None = None) -> dict:
    """Build a Notion property payload for «Status» that respects the brand's
    Notion schema.

    Artem's content DB uses the built-in `status` property type (with kanban
    groups). Maksim's DB uses a plain `select` because the Notion API can't
    programmatically create `status` options — see
    project_maksim_notion_content_db.md.

    `brand_name` is optional — when None, reads the active brand from context.
    Pass an explicit `brand_name` (e.g. inside `create_notion_card`) when the
    target brand is known and may differ from the active one.
    """
    status_type = _resolve_brand_cfg(brand_name).get("notion_status_type", "status")
    return {status_type: {"name": status_name}}


def _status_filter_payload(status_value: str, brand_name: str | None = None) -> dict:
    """Build the `equals`-filter wrapper for Notion DB queries on Status.

    Artem's DB filters as `{"status": {"equals": ...}}`, Maksim's as
    `{"select": {"equals": ...}}` because of the property-type difference.
    Used by `fetch_notion_cards` and similar query builders.
    """
    status_type = _resolve_brand_cfg(brand_name).get("notion_status_type", "status")
    return {status_type: {"equals": status_value}}


def _rubric_property_name(brand_name: str | None = None) -> str:
    """Return the «Rubric» property name for the brand.

    Artem's DB has a trailing space ("Рубрика ") due to a historical typo;
    Maksim's DB is clean ("Рубрика").

    `brand_name` is optional — when None, reads the active brand from context.
    """
    return _resolve_brand_cfg(brand_name).get("notion_rubric_property", "Рубрика ")


def _default_platforms(brand_name: str | None = None) -> list[str]:
    """Default platform multi_select values for a new Notion card.

    Reads the brand's `platforms` setting. These names MUST match the
    «Площадки» property options in the brand's Notion DB exactly — a typo
    means the option silently fails to apply.

    `brand_name` is optional — when None, reads the active brand. Pass
    explicit `brand_name` from `create_notion_card` to keep schema and
    platform names in sync with the card's Brand property.

    Use this everywhere a new card is created without explicit platforms.
    """
    return list(_resolve_brand_cfg(brand_name).get("platforms", [
        "Мой инста panferov.ai",
        "youtube shorts",
        "мой телеграм канал",
    ]))


# Cache of prompt-file contents keyed by absolute path. Reload-safe:
# `_invalidate_prompt_cache()` clears it on demand if you edit the file
# while the bot is running. Default behaviour is read-once-per-process
# (small files, change rarely, no need to hit disk per request).
_PROMPT_FILE_CACHE: dict[str, str] = {}


def _read_brand_prompt_file(filename: str | None) -> str | None:
    """Read a brand-specific prompt from a file alongside bot.py.

    Returns None if `filename` is empty/None or the file doesn't exist.
    Caches contents per-process (not per-request) — call
    `_invalidate_prompt_cache()` to force re-read after editing.
    """
    if not filename:
        return None
    path = Path(__file__).parent / filename
    key = str(path)
    cached = _PROMPT_FILE_CACHE.get(key)
    if cached is not None:
        return cached
    if not path.exists():
        logger.warning(f"Brand prompt file missing: {path}")
        return None
    text = path.read_text(encoding="utf-8")
    _PROMPT_FILE_CACHE[key] = text
    return text


def _invalidate_prompt_cache() -> None:
    """Drop cached prompt-file contents — next read hits disk again."""
    _PROMPT_FILE_CACHE.clear()


def _brand_script_prompt(default: str) -> str:
    """Active brand's script prompt — inline override → file → default.

    Resolution order:
      1. `BRANDS[active]["script_prompt_override"]` (inline string, e.g. shoes)
      2. `BRANDS[active]["script_prompt_file"]` (filename next to bot.py, e.g. maksim)
      3. `default` (the global SCRIPT_PROMPT loaded from script_prompt.txt)
    """
    brand = _get_active_brand()
    inline = brand.get("script_prompt_override")
    if inline:
        return inline
    file_text = _read_brand_prompt_file(brand.get("script_prompt_file"))
    if file_text:
        return file_text
    return default


def _brand_cover_prompt(default: str) -> str:
    """Active brand's cover prompt — inline override → file → default."""
    brand = _get_active_brand()
    inline = brand.get("cover_prompt_override")
    if inline:
        return inline
    file_text = _read_brand_prompt_file(brand.get("cover_prompt_file"))
    if file_text:
        return file_text
    return default



# Numbers that are always safe for the maksim brand — these are hard-coded
# into the FACTS block and represent verifiable facts about Maksim's
# bio/business. Add new numbers when FACTS expands.
_MAKSIM_SAFE_NUMBERS = {
    # Bio
    "40", "39",  # age (40 in 2026, 39 at time of viral Reel 1 in March 2025)
    "2009", "2023", "2024", "2025", "2026",  # years
    "16",  # years of karting (2025-2009)
    "2", "3",  # years of glamping ("2 года назад" / "3 года")
    # Glamping
    "8", "10",  # 8-10 domes, "около 10"
    # General small numbers used in narrative
    "1", "5", "7",
}

# Pattern matchers for suspicious invented-number constructs.
# Heuristic — they flag for inspection, not strict auto-reject.
# Each match is checked against the source idea + safe set for justification.
_SUSPICIOUS_NUMBER_PATTERNS = [
    re.compile(r"\b(\d+)\s*%", re.IGNORECASE),  # any percentage
    re.compile(r"\b(\d+)\s*(?:месяц|года|лет|недел|дн[ея]|раз)", re.IGNORECASE),  # time spans
    re.compile(r"\b(\d+)\s*(?:метр|километр|км|шт|штук|участ|кв)", re.IGNORECASE),  # quantities
    re.compile(r"\b(\d+)\s*\+", re.IGNORECASE),  # "30+"
]


# ════ Maksim brand UX ═══════════════════════════════════════════════════════
# Maksim's bot reuses Artem's existing process_idea pipeline (with
# step-by-step approval gates: scenario → cover → voice → avatar → assembly).
# The brand-aware helpers we built earlier (_brand_script_prompt,
# _status_property_value, _default_platforms, etc.) make sure that pipeline
# runs through Maksim's prompts and Notion schema when DEFAULT_BRAND=maksim.
# So all we need at the UX layer is a brand-specific main menu + greeting.
#
# Notion DB URL for the «Открыть Notion» button — comes from BRANDS["maksim"]
# at runtime so it survives DB rebuilds.
_MAKSIM_NOTION_DB_URL = "https://www.notion.so/3586889cd6a7804e9f0be2d58c34e872"


def _start_action_kb_maksim() -> InlineKeyboardMarkup:
    """Main menu for Maksim's bot — 6 pipelines per maksim_pipelines_final.md.

    Layout (3 rows × 2):
      Row 1: 🔍 Идеи дня (заглушка v2)   🎥 Селфи (главный pipeline)
      Row 2: 💡 Новая идея (AI-аватар)   📝 TG-пост
      Row 3: 📋 Карточки                 📋 Открыть Notion

    «Идеи дня» — Pipeline #0 (Поиск виральных идей), пока заглушка-плейсхолдер.
    «Селфи» — Pipeline #1, главный, использует Артёмов /selfie через cmd_selfie.
    «Новая идея» — Pipeline #3 (AI-аватар), идёт через Артёмов process_idea.
    «TG-пост» — Pipeline #5 (multi-thought), пока через Артёмов /tgpost.
    Pipelines #2 (Готовый ролик из архива) и #4 (Обзор события) — на v2,
    требуют наполнения B-roll архива и ffmpeg-монтажа набора клипов.
    """
    # 12 May 2026 — renamed «Идеи дня» → «Банк идей» и «Новая идея» →
    # «Моя идея». «Идеи дня» путало (намёк на ежедневную ленту, а это
    # on-demand генерация), и обе старые кнопки звучали одинаково
    # (источник идеи — бот или юзер? было непонятно). Новые названия
    # делают направление потока явным:
    #   🎰 Банк идей — бот предлагает (Pipeline #0-B, brand-aware LLM)
    #   ✍️ Моя идея — юзер вносит свою (Pipeline #3, AI-аватар)
    #   🔥 Тренды недели — будущее место (Pipeline #0-A, research)
    # callback_data сохранены (maksim_ideas_stub / cmd_new_idea) для
    # обратной совместимости со старыми pending state.
    return _build_maksim_start_kb(last_card=None)


def _build_maksim_start_kb(last_card: dict | None = None) -> InlineKeyboardMarkup:
    """Главное меню Максима + опциональная первая кнопка «🔄 Продолжить».

    `last_card={"id": notion_page_id, "title": "..."}` → добавляет верхним рядом
    «🔄 Продолжить: <title>» с callback `notion_card:<id[:20]>` — один клик к
    активной (или последней) карточке. Если None — старое меню без верха.
    """
    rows = []
    if last_card and last_card.get("id"):
        short_title = (last_card.get("title") or "карточка")[:40]
        rows.append([
            InlineKeyboardButton(
                f"🔄 Продолжить: {short_title}",
                callback_data=f"notion_card:{last_card['id'][:20]}",
            )
        ])
    rows.extend([
        [
            InlineKeyboardButton("🎰 Банк идей", callback_data="maksim_ideas_stub"),
            InlineKeyboardButton("🎥 Селфи", callback_data="cmd_selfie"),
        ],
        [
            InlineKeyboardButton("✍️ Моя идея", callback_data="cmd_new_idea"),
            InlineKeyboardButton("📝 TG-пост", callback_data="cmd_tgpost"),
        ],
        # 14 May 2026 — Pipeline #6 (Карусель для Instagram). Full-width row
        # to make it visually prominent for демо Максиму.
        [
            InlineKeyboardButton(
                "🎨 Карусель для Instagram",
                callback_data="cmd_carousel",
            ),
        ],
        [
            InlineKeyboardButton("📋 Карточки", callback_data="cmd_cards"),
            InlineKeyboardButton("📋 Открыть Notion", url=_MAKSIM_NOTION_DB_URL),
        ],
    ])
    return InlineKeyboardMarkup(rows)


# ─── «Готовые материалы» под maksim ──────────────────────────────────────
# Кнопки выбора категории для общей библиотеки + опт-аут. Вариант В
# (гибрид) из плана 25 мая: дефолт = `personal`, явный опт-аут — `__skip__`.
# Выбор хранится в `pending[user_id]["broll_lib_category"]`.

_MAKSIM_READY_LIB_DEFAULT = "personal"
_MAKSIM_READY_LIB_SKIP = "__skip__"

# Какие категории показываем кнопками (6 функциональных + skip). Полный
# список категорий лежит в MAKSIM_LIBRARY_CATEGORIES — для UI берём
# подмножество чтобы не перегружать ряды.
_MAKSIM_READY_LIB_BUTTONS = ["karting", "glamping", "sup", "nature", "team", "personal"]


def _maksim_ready_kb(selected_category: str | None = None) -> InlineKeyboardMarkup:
    """Клавиатура для «📥 Готовые материалы» под бренд maksim.

    Раскладка: 3 ряда × 2 категории, затем full-width «не в библиотеку» +
    «Готово» + «Назад». Выбранная категория помечена ✅ в подписи.
    """
    selected = selected_category or _MAKSIM_READY_LIB_DEFAULT
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for cat in _MAKSIM_READY_LIB_BUTTONS:
        emoji, ru_name = MAKSIM_CATEGORY_UI.get(cat, ("📁", cat))
        mark = "✅ " if selected == cat else ""
        pair.append(InlineKeyboardButton(
            f"{mark}{emoji} {ru_name}",
            callback_data=f"broll_ready_cat:{cat}",
        ))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    skip_mark = "✅ " if selected == _MAKSIM_READY_LIB_SKIP else ""
    rows.append([InlineKeyboardButton(
        f"{skip_mark}✋ Не класть в библиотеку",
        callback_data=f"broll_ready_cat:{_MAKSIM_READY_LIB_SKIP}",
    )])
    rows.append([InlineKeyboardButton("✅ Готово", callback_data="broll_ready_done")])
    rows.append([InlineKeyboardButton("◀️ Назад к B-roll", callback_data="broll")])
    return InlineKeyboardMarkup(rows)


def _maksim_ready_category_label(category: str | None) -> str:
    """Человеческая подпись текущей категории для шапки «Готовые материалы»."""
    cat = category or _MAKSIM_READY_LIB_DEFAULT
    if cat == _MAKSIM_READY_LIB_SKIP:
        return "✋ только в карточку (без копии в библиотеку)"
    emoji, ru_name = MAKSIM_CATEGORY_UI.get(cat, ("📁", cat))
    return f"{emoji} {ru_name}"


def _build_photo_lib_pick_kb(
    sample_paths: list[str],
    selected_set: set[str],
    full_lib_size: int,
    card_id_short: str,
) -> InlineKeyboardMarkup:
    """Клавиатура выбора фото из библиотеки.

    Сетка 3×3 цифровых toggle-кнопок (под альбом из 9 фото), потом:
      [➕ Добавить в проект (N)] — копирует выбранные в projects/<id>/photos/
      [🎲 Другая выборка]
      [◀️ Назад к B-roll]

    `selected_set` — множество абсолютных путей которые уже отмечены (накопительно
    через rerolls). Лейбл `#N` показывает ✅ если path в множестве, иначе ⬜.
    """
    rows: list[list[InlineKeyboardButton]] = []
    digit_row: list[InlineKeyboardButton] = []
    for idx, path in enumerate(sample_paths):
        mark = "✅" if path in selected_set else "⬜"
        digit_row.append(InlineKeyboardButton(
            f"{mark}{idx + 1}",
            callback_data=f"photo_lib_toggle:{idx}",
        ))
        if len(digit_row) == 3:
            rows.append(digit_row)
            digit_row = []
    if digit_row:
        rows.append(digit_row)
    n_selected = sum(1 for p in sample_paths if p in selected_set) + \
        sum(1 for p in selected_set if p not in sample_paths)
    add_label = (
        f"➕ Добавить в проект ({n_selected})"
        if n_selected else "➕ Добавить в проект"
    )
    rows.append([InlineKeyboardButton(add_label, callback_data="photo_lib_add")])
    rows.append([InlineKeyboardButton("🎲 Другая выборка", callback_data="broll_photo_reroll")])
    rows.append([InlineKeyboardButton("◀️ Назад к B-roll", callback_data="broll")])
    return InlineKeyboardMarkup(rows)


def _maksim_pick_lib_category(data: dict) -> str | None:
    """Выбранная категория для текущей сессии «Готовые материалы».

    Возвращает имя категории, либо None если выбран опт-аут (`__skip__`).
    Дефолт — `_MAKSIM_READY_LIB_DEFAULT` (personal).
    """
    cat = (data or {}).get("broll_lib_category") or _MAKSIM_READY_LIB_DEFAULT
    if cat == _MAKSIM_READY_LIB_SKIP:
        return None
    return cat


def _maksim_greeting_text(user_id: int) -> str:
    """Greeting shown above the inline keyboard for Maksim's bot.

    Mirrors Artem's pattern: «just send an idea, I'll do the rest». No format
    picker — pipeline branches inside Artem's process_idea handle structure.
    """
    return (
        "👋 Привет, Максим. Я твой контент-бот.\n\n"
        "🏷 Бренд: *maksim* — Life Drive (картинг + глэмпинг).\n\n"
        "💬 Просто пришли идею текстом или голосовым — я сделаю "
        "сценарий → обложку → озвучку → аватар → сборку. На каждом шаге "
        "ты подтверждаешь или просишь переделать.\n\n"
        "Или используй кнопки ниже."
    )


def _read_tg_post_from_notion(notion_page_id: str) -> str | None:
    """Extract the «📨 Пост в Telegram-канал» text from a Notion card.

    Selfie pipeline (`_selfie_finalize`) appends the polished TG-channel
    post to the Notion card as a heading_2 named «📨 Пост в Telegram-канал»
    followed by paragraph blocks (chunked via _notion_paragraph_blocks).
    Pending-state cache (`data["selfie_tg_post"]`) is the primary source,
    but it lives in RAM/pending.json and can be lost across bot restarts.
    Notion is durable storage — read from there as a fallback.

    Returns concatenated paragraph text (all paragraphs joined by \\n\\n),
    or None if the heading isn't found / Notion API fails. Used in the
    crosspost-to-telegram handler for Maksim brand. Added 12 May 2026
    after Артём caught a regression: published transcript instead of
    polished post because pending lost the cache.
    """
    if not notion_page_id:
        return None
    try:
        marker = "Пост в Telegram-канал"
        collected: list[str] = []
        in_section = False
        cursor: str | None = None
        # Notion paginates children — iterate until done
        while True:
            kwargs = {"block_id": notion_page_id, "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = notion.blocks.children.list(**kwargs)
            for block in resp.get("results", []):
                btype = block.get("type")
                if btype == "heading_2":
                    rt = block.get("heading_2", {}).get("rich_text", [])
                    h_text = "".join(t.get("plain_text", "") for t in rt)
                    if marker in h_text:
                        in_section = True
                        continue
                    if in_section:
                        # Hit the next H2 — section ended
                        break
                if in_section and btype == "paragraph":
                    rt = block.get("paragraph", {}).get("rich_text", [])
                    p_text = "".join(t.get("plain_text", "") for t in rt)
                    if p_text.strip():
                        collected.append(p_text)
            else:
                # Inner for didn't break — check pagination
                if resp.get("has_more"):
                    cursor = resp.get("next_cursor")
                    continue
                break
            # Inner for broke (next H2 hit) — stop entirely
            break
        if not collected:
            return None
        return "\n\n".join(collected)
    except Exception as e:
        logger.warning(f"[notion] read TG-post failed for {notion_page_id}: {e}")
        return None


def _notion_paragraph_blocks(text: str, max_len: int = 1900) -> list[dict]:
    """Split a long text into Notion paragraph blocks under the 2000-char limit.

    Notion API rejects any rich_text.content > 2000 characters with a
    validation_error. This helper produces a list of paragraph blocks where
    each chunk stays ≤ `max_len` characters (default 1900 — small safety
    margin under the hard 2000 cap).

    Splitting strategy: greedy on paragraph (\\n\\n) → sentence (`. `) →
    raw character. This keeps prose readable in Notion (chunks end on
    natural breaks when possible, rather than mid-word).

    Returns: `[{"object": "block", "type": "paragraph", "paragraph": {...}}]`

    Used by:
      - create_notion_card() for the script/transcript body (Codex P1 fix
        from review #2 — long selfie transcripts were uploaded as one rich_text
        block, hitting the 2000-char limit and crashing card creation).
      - _selfie_finalize() for the TG-post body appended via blocks.append.
    """
    if not text:
        return []
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        # Try to split on paragraph break first
        cut = remaining.rfind("\n\n", 0, max_len)
        if cut < max_len // 2:  # too close to the start — try sentence
            cut = remaining.rfind(". ", 0, max_len)
            if cut > 0:
                cut += 2  # include the ". " in this chunk
            else:
                cut = max_len  # fallback: hard cut
        else:
            cut += 2  # include the "\n\n"
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"text": {"content": chunk}}],
            },
        }
        for chunk in chunks
    ]


def _validate_no_invented_numbers(
    output_text: str,
    source_idea: str,
    safe_numbers: set[str] | None = None,
) -> list[str]:
    """Scan generated content for numbers that aren't justified by source.

    A number is "justified" if it appears in:
    - the user's source_idea text (the input prompt material)
    - the safe_numbers set (FACTS-derived constants)

    Returns a list of human-readable warnings. Empty list = clean output.

    This is a HEURISTIC validator — it WILL produce false positives on
    legitimate passing-mention numbers. Use it as a flag for human review
    or for retry logic, not as a hard reject without context.

    For maksim brand, pair with prompt-level rules in script_prompt_maksim.txt.
    """
    if safe_numbers is None:
        safe_numbers = _MAKSIM_SAFE_NUMBERS
    warnings: list[str] = []
    seen: set[str] = set()
    for pat in _SUSPICIOUS_NUMBER_PATTERNS:
        for m in pat.finditer(output_text):
            num = m.group(1)
            if num in seen:
                continue
            seen.add(num)
            if num in safe_numbers:
                continue
            # Check if the number appears in the source idea text
            if re.search(rf"\b{re.escape(num)}\b", source_idea):
                continue
            # Suspicious — surface a warning with surrounding context
            ctx_start = max(0, m.start() - 30)
            ctx_end = min(len(output_text), m.end() + 30)
            ctx = output_text[ctx_start:ctx_end].replace("\n", " ").strip()
            warnings.append(
                f"Suspicious number {num!r} (no source). Context: …{ctx}…"
            )
    return warnings


def _pick_card_apply_brand(
    all_cards: list[dict],
    card_id_prefix: str,
    user_id: int | None = None,
) -> dict | None:
    """Find a card by id prefix, apply its «Бренд» to the call context, and
    (optionally) cache it into pending[user_id] for restart-resilience.

    Use instead of ``next((c for c in all_cards if c["id"].startswith(prefix)))``
    when the handler will subsequently generate voice, avatar, or assemble —
    anything that reads ``_get_active_brand()`` under the hood.

    ``user_id`` is optional but strongly recommended — callers that know the
    user should pass it so the brand persists through a bot restart.
    """
    card = next((c for c in all_cards if c["id"].startswith(card_id_prefix)), None)
    if card:
        brand_name = (card.get("brand") or "").strip().lower()
        if brand_name in BRANDS:
            _brand_ctx.set(brand_name)
            if user_id is not None:
                _cache_card_brand_in_pending(user_id, brand_name)
        else:
            # Reset so a previous card's brand doesn't leak into this call
            _brand_ctx.set("")
    return card


def _cache_card_brand_in_pending(user_id: int, brand_name: str) -> None:
    """Persist the card's brand into pending[user_id] so it survives
    bot restarts (pending.json is saved to disk). After a restart the
    global ``_active_brand`` resets to "default", but callbacks for an
    open card must still resolve the correct brand — they read this
    cached value through :func:`_restore_brand_from_pending`.
    """
    if user_id in pending and brand_name and brand_name in BRANDS:
        pending[user_id]["card_brand"] = brand_name
        try:
            _save_pending(pending)
        except Exception:
            pass


def _restore_brand_from_pending(user_id: int) -> None:
    """If the user's pending data carries a cached card brand, set the
    context var to it. Called at the start of ``handle_callback`` so that
    every deep callback (heygen_looks, assemble, cover, etc.) sees the
    correct brand without needing to re-fetch the card from Notion.
    """
    cached = (pending.get(user_id) or {}).get("card_brand", "")
    if cached and cached in BRANDS:
        _brand_ctx.set(cached)

# --- Paths ---
ASSETS_DIR = Path(__file__).parent / "assets"
AVATARS_DIR = ASSETS_DIR / "avatars"
VOICES_DIR = ASSETS_DIR / "voices"
PROJECTS_DIR = Path(__file__).parent / "projects"
AVATAR_PATH = ASSETS_DIR / "avatar.jpg"  # fallback single avatar
PROMPT_PATH = Path(__file__).parent / "script_prompt.txt"


def _voice_dir(notion_page_id: str) -> Path:
    """Get voice directory for a Notion card."""
    d = VOICES_DIR / notion_page_id.replace("-", "")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_voice_meta(notion_page_id: str, voice_parts: list[str], voice_approved: list[bool]):
    """Save voice metadata (texts + approval status) for a Notion card."""
    d = _voice_dir(notion_page_id)
    meta = {"voice_parts": voice_parts, "voice_approved": voice_approved}
    (d / "voice_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_voice_meta(notion_page_id: str) -> dict | None:
    """Load voice metadata for a Notion card. Returns None if not found."""
    d = VOICES_DIR / notion_page_id.replace("-", "")
    meta_file = d / "voice_meta.json"
    if meta_file.exists():
        try:
            return json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _save_voice_file(notion_page_id: str, part_idx: int, source_path: str):
    """Copy a voice file to the card's voice directory."""
    import shutil
    d = _voice_dir(notion_page_id)
    dest = d / f"voice_part_{part_idx}.mp3"
    shutil.copy2(source_path, str(dest))


def _get_voice_files(notion_page_id: str, count: int) -> list[Path]:
    """Get list of voice file paths for a Notion card."""
    d = VOICES_DIR / notion_page_id.replace("-", "")
    return [d / f"voice_part_{i}.mp3" for i in range(count)]


def _avatars_dir_for_brand(brand: str | None = None) -> Path:
    """Return the avatar directory for the given brand.

    Resolution (first hit wins):
      1. ``assets/avatars/<brand>/`` — per-brand pool (e.g. shoes → women
         models for shoe-brand covers).
      2. ``assets/avatars/`` — the global default pool (Artem's AI-expert
         photos).

    This isolation keeps a shoe-brand cover from ever accidentally landing
    on an experiment card, and vice versa.
    """
    if not brand or brand == "default":
        return AVATARS_DIR
    brand_dir = AVATARS_DIR / brand
    if brand_dir.exists() and any(brand_dir.iterdir()):
        return brand_dir
    return AVATARS_DIR  # fallback if brand folder missing / empty


def _pick_random_avatar() -> str | None:
    """Pick a random avatar photo, respecting the active brand's pool."""
    pool_dir = _avatars_dir_for_brand(_get_active_brand_name())
    if pool_dir.exists():
        photos = [f for f in pool_dir.iterdir() if f.suffix.lower() in (".jpg", ".jpeg", ".png")]
        if photos:
            return str(random.choice(photos))
    if AVATAR_PATH.exists():
        return str(AVATAR_PATH)
    return None

# --- Project folder management ---
# F1 fix: канонический _project_dir теперь в bot_state.py (чтобы
# carousel/handlers.py не делал import bot для доступа). Здесь — тонкая
# обёртка с тем же именем для сохранения обратной совместимости.
from bot_state import project_dir as _project_dir  # noqa: E402, F401


def _save_to_project(data: dict, filename: str, source_path: str):
    """Copy a file to the project directory."""
    d = _project_dir(data)
    if not d:
        return
    import shutil
    shutil.copy2(source_path, str(d / filename))
    logger.info(f"Saved to project: {d.name}/{filename}")


def _cleanup_old_avatars(data: dict, keep_filename: str | None = None) -> int:
    """Delete old avatar_*.mp4 files from the project dir, keeping only keep_filename.

    Called before saving a new avatar so the "Download materials" button ships
    just the latest take, not every failed retry / every look the user tried.
    Returns number of files deleted.
    """
    d = _project_dir(data)
    if not d or not d.exists():
        return 0
    removed = 0
    for f in d.glob("avatar_*.mp4"):
        if keep_filename and f.name == keep_filename:
            continue
        try:
            f.unlink()
            removed += 1
            logger.info(f"Cleaned old avatar: {d.name}/{f.name}")
        except Exception as e:
            logger.warning(f"Failed to remove old avatar {f}: {e}")
    return removed


def _save_text_to_project(data: dict, filename: str, text: str):
    """Save text content to the project directory."""
    d = _project_dir(data)
    if not d:
        return
    (d / filename).write_text(text, encoding="utf-8")
    logger.info(f"Saved to project: {d.name}/{filename}")


async def _save_ready_photo(
    update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict,
) -> tuple[bool, str]:
    """Save an incoming Telegram photo (best resolution) into the active
    project's ``photos/`` folder. Used by the «📥 Готовые материалы» flow.

    Returns (success, human_readable_message). Message is suitable for direct
    reply — contains either a ✓ confirmation or a specific error.
    """
    proj = _project_dir(data)
    if not proj:
        return False, "❌ Сначала открой карточку — фото сохранять некуда."
    photos_dir = proj / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)

    # Largest PhotoSize = highest-resolution version Telegram stored.
    msg = update.message
    photo_obj = None
    src_ext = ".jpg"
    if msg.photo:
        photo_obj = msg.photo[-1]
    elif msg.document and (msg.document.mime_type or "").startswith("image/"):
        photo_obj = msg.document
        # Keep document extension if we can infer it
        if msg.document.file_name:
            src_ext = Path(msg.document.file_name).suffix.lower() or ".jpg"

    if not photo_obj:
        return False, "❌ Не удалось распознать фото."

    existing = sorted(p.name for p in photos_dir.iterdir() if p.is_file())
    next_n = 1
    while f"ready_{next_n:02d}{src_ext}" in existing:
        next_n += 1
    dest = photos_dir / f"ready_{next_n:02d}{src_ext}"

    try:
        tg_file = await context.bot.get_file(photo_obj.file_id)
        await tg_file.download_to_drive(str(dest))
    except Exception as e:
        logger.warning(f"[broll_ready] photo download failed: {e}")
        return False, f"❌ Ошибка загрузки фото: {e}"

    size_kb = dest.stat().st_size / 1024
    total_photos = sum(
        1 for p in photos_dir.iterdir()
        if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
    )
    # Brand-aware автокопия в общую библиотеку — вариант В (гибрид).
    # Дефолт maksim = personal, опт-аут = __skip__ (None после _maksim_pick_lib_category).
    lib_note = ""
    _brand_for_lib = _get_active_brand_name()
    if _brand_for_lib == "maksim":
        chosen_cat = _maksim_pick_lib_category(data)
        if chosen_cat:
            lib_copy = _copy_to_library(
                dest, brand="maksim", kind="photo", category=chosen_cat,
            )
            if lib_copy:
                cat_emoji = MAKSIM_CATEGORY_UI.get(chosen_cat, ("📁", chosen_cat))[0]
                lib_note = f" • {cat_emoji} в библиотеке"
    return True, f"✓ Фото {next_n} сохранено ({size_kb:.0f} КБ){lib_note}. Всего в проекте: {total_photos}"


async def _save_ready_video(
    update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict,
) -> tuple[bool, str]:
    """Save an incoming Telegram video/document-video as the next free
    ``broll_NN.mp4`` in the project folder. If the active brand has
    ``auto_trim_video_sec`` set and the video is longer, trim from the
    start via ffmpeg; stash the original in ``_raw_uploads/`` as backup.
    """
    proj = _project_dir(data)
    if not proj:
        return False, "❌ Сначала открой карточку — видео сохранять некуда."

    msg = update.message
    video_obj = msg.video or msg.document
    if not video_obj:
        return False, "❌ Не удалось распознать видео."
    # If document, ensure it's a video mime type
    if msg.document and not (
        (msg.document.mime_type or "").startswith("video/")
        or (msg.document.file_name or "").lower().endswith((".mp4", ".mov"))
    ):
        return False, "❌ Это не видеофайл."

    # Pick the next free broll_NN.mp4 index (smart-mix relies on this pattern)
    existing_indices = set()
    for p in proj.glob("broll_*.mp4"):
        m = re.match(r"broll_(\d+)", p.stem)
        if m:
            existing_indices.add(int(m.group(1)))
    n = 1
    while n in existing_indices:
        n += 1
    target_name = f"broll_{n:02d}.mp4"
    target_path = proj / target_name

    # Download to a staging path first (raw upload before any processing)
    raw_dir = proj / "_raw_uploads"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_name = f"ready_upload_{n:02d}.mp4"
    raw_path = raw_dir / raw_name

    try:
        tg_file = await context.bot.get_file(video_obj.file_id)
        await tg_file.download_to_drive(str(raw_path))
    except Exception as e:
        logger.warning(f"[broll_ready] video download failed: {e}")
        return False, f"❌ Ошибка загрузки видео: {e}"

    # Probe duration
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(raw_path)],
            capture_output=True, text=True, timeout=15,
        )
        duration = float(probe.stdout.strip())
    except Exception as e:
        logger.warning(f"[broll_ready] ffprobe failed: {e}")
        # Fall back to storing as-is without trim
        duration = 0.0

    brand_cfg = _get_active_brand()
    trim_sec = brand_cfg.get("auto_trim_video_sec")

    trim_note = ""
    if trim_sec and duration > trim_sec + 0.5:
        # Trim from start using ffmpeg (re-encode for clean cuts — avoids
        # GOP-boundary artefacts that stream-copy would introduce).
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", "0", "-i", str(raw_path),
                 "-t", str(trim_sec), "-c:v", "libx264", "-preset", "fast",
                 "-crf", "20", "-c:a", "aac", "-b:a", "128k",
                 str(target_path)],
                capture_output=True, timeout=180,
            )
            trim_note = f" (обрезано с {duration:.1f}с до {trim_sec}с, оригинал в _raw_uploads/)"
        except Exception as e:
            logger.warning(f"[broll_ready] trim failed, keeping full video: {e}")
            # Fall back to moving raw → target
            import shutil as _shutil
            _shutil.copy2(str(raw_path), str(target_path))
    else:
        # No trim: move raw to target, keep a copy in _raw_uploads too
        # (users may want the original for re-edits)
        import shutil as _shutil
        _shutil.copy2(str(raw_path), str(target_path))
        trim_note = f" ({duration:.1f}с, без обрезки)"

    if not target_path.exists():
        return False, "❌ Видео не сохранилось после обработки."

    size_mb = target_path.stat().st_size / (1024 * 1024)

    # Brand-aware автокопия в общую клип-библиотеку.
    # Берём НЕ обрезанный target (5с), а ОРИГИНАЛ из raw_path — в библиотеке
    # полнометражная версия полезнее для будущего переиспользования.
    lib_note = ""
    _brand_for_lib = _get_active_brand_name()
    if _brand_for_lib == "maksim":
        chosen_cat = _maksim_pick_lib_category(data)
        if chosen_cat:
            source_for_lib = raw_path if raw_path.exists() else target_path
            lib_copy = _copy_to_library(
                source_for_lib, brand="maksim", kind="video", category=chosen_cat,
            )
            if lib_copy:
                cat_emoji = MAKSIM_CATEGORY_UI.get(chosen_cat, ("📁", chosen_cat))[0]
                lib_note = f" • {cat_emoji} в библиотеке"

    return True, f"✓ Видео сохранено как {target_name} ({size_mb:.1f} МБ){trim_note}{lib_note}"


def _zip_project(data: dict) -> Path | None:
    """Create a ZIP archive of the project directory — recursively, with
    subfolders (photos/, etc.) included. Excludes assembler scratch dirs
    (_tmp_montage/) and raw-upload originals (_raw_uploads/) — those are
    huge and only useful server-side for re-cutting.
    """
    import zipfile
    d = _project_dir(data)
    if not d or not d.exists():
        return None

    SKIP_DIRS = {"_tmp_montage", "_raw_uploads", "__pycache__"}
    SKIP_SUFFIXES = (".bak",)

    zip_path = PROJECTS_DIR / f"{d.name}.zip"
    with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(d.rglob("*")):
            if not f.is_file():
                continue
            # Skip if any parent directory is in the skip list
            if any(part in SKIP_DIRS for part in f.relative_to(d).parts):
                continue
            if f.suffix.lower() in SKIP_SUFFIXES:
                continue
            # Preserve subfolder structure (photos/foo.jpg, broll_01.mp4, …)
            arcname = str(f.relative_to(d))
            zf.write(str(f), arcname)
    return zip_path


# --- Transliteration for ElevenLabs ---
# NOTE: For ElevenLabs Russian voices, use explicit stress marks (combining acute U+0301
# after a vowel: а́ е́ и́ о́ у́ ы́ э́ ю́ я́) and separate English abbreviations with spaces
# or hyphens. Otherwise the engine either mangles them or reads with wrong stress.
TRANSLIT_MAP = {
    "ChatGPT": "чат джи-пи-ти́",
    "Chat GPT": "чат джи-пи-ти́",
    "GPT-4o": "джи-пи-ти́ четы́ре о",
    "GPT-4": "джи-пи-ти́ четы́ре",
    "GPT-5": "джи-пи-ти́ пять",
    "GPT-3.5": "джи-пи-ти́ три пять",
    "GPT": "джи-пи-ти́",
    "Claude AI": "Клод Эй-Ай",
    "Claude 3": "Клод три",
    "Claude 4": "Клод четы́ре",
    "Claude": "Клод",
    "NotebookLM": "но́утбук Эл-Эм",
    "Notebook LM": "но́утбук Эл-Эм",
    "Google Workspace": "Гугл Во́ркспейс",
    "Google Sheets": "Гугл Шитс",
    "Google": "Гугл",
    "Gemini": "Дже́мини",
    "Gmail": "Джи-мейл",
    "Excel": "Эксе́ль",
    "VLOOKUP": "Ви-Лу́кап",
    "HeyGen": "Хе́й-Джен",
    "ElevenLabs": "Иле́вен Лэбс",
    "Eleven Labs": "Иле́вен Лэбс",
    "Midjourney": "Миджо́рни",
    "MidJourney": "Миджо́рни",
    "DALL-E": "Да́лли",
    "Stable Diffusion": "Сте́йбл Диффью́жн",
    "Syllaby": "Си́ллаби",
    "Ideogram": "Айдио́грам",
    "AppFunctions": "Эпп-Фа́нкшнс",
    "App Functions": "Эпп-Фа́нкшнс",
    "OpenAI": "Опен Эй-Ай",
    "Open AI": "Опен Эй-Ай",
    "Apple": "Э́пл",
    "iPhone": "Айфо́н",
    "iOS": "ай-о-э́с",
    "iPad": "айпа́д",
    "Mac": "мак",
    "MacBook": "Макбу́к",
    "VPN": "Ви-Пи-Эн",
    "Instagram": "Инстагра́м",
    "YouTube Shorts": "Ютьюб Шортс",
    "YouTube": "Ютьюб",
    "Shorts": "Шортс",
    "Reels": "Рилс",
    "TikTok": "ТикТо́к",
    "Telegram": "Телегра́м",
    "Notion": "Но́ушен",
    "Canva": "Ка́нва",
    "Figma": "Фи́гма",
    "Perplexity": "Перпле́ксити",
    "Anthropic": "Антро́пик",
    "API": "Эй-Пи-Ай",
    "LLM": "Эл-Эл-Эм",
    "AI": "Эй-Ай",
    "IT": "Ай-Ти́",
    "CTA": "Си-Ти-Эй",
    "CEO": "Си-И-О́",
    "B2B": "Би-ту-Би",
    "SaaS": "саас",
    "CRM": "Си-Эр-Эм",
    "Pexels": "Пексэлс",
    "Pixabay": "Пиксабай",
    "Runway": "Ранвей",
    "Sora": "Сора",
    "Suno": "Суно",
    "Udio": "Юдио",
    "Whisper": "Виспер",
    "Groq": "Грок",
    "Tesla": "Тесла",
    "Optimus": "Оптимус",
    "Elon Musk": "Илон Маск",
    "Elon": "Илон",
    "Musk": "Маск",
    "Boston Dynamics": "Бостон Дайнемикс",
    "SpaceX": "СпейсИкс",
    "Figure": "Фигур",
    "Copilot": "Копайлот",
    "GitHub": "ГитХаб",
    "LinkedIn": "ЛинкедИн",
    "Facebook": "Фейсбук",
    "Twitter": "Твиттер",
    "Threads": "Тредс",
    "WhatsApp": "ВотсАп",
    "Pinterest": "Пинтерест",
    "Snapchat": "Снэпчат",
    "Netflix": "Нетфликс",
    "Amazon": "Амазон",
    "Microsoft": "Майкрософт",
    "Samsung": "Самсунг",
    "Nvidia": "Энвидиа",
    "NVIDIA": "Энвидиа",
    "Meta": "Мета",
    "Llama": "Лама",
    "DeepSeek": "ДипСик",
}

def _number_to_russian(n: int) -> str:
    """Convert integer to Russian words."""
    if n == 0:
        return "ноль"

    ones = ["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять",
            "десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать",
            "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
    tens = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто"]
    hundreds = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот"]

    parts = []

    if n >= 1_000_000:
        m = n // 1_000_000
        if m == 1:
            parts.append("один миллион")
        elif m in (2, 3, 4):
            parts.append(f"{ones[m]} миллиона")
        else:
            parts.append(f"{_number_to_russian(m)} миллионов")
        n %= 1_000_000

    if n >= 1000:
        t = n // 1000
        # Determine thousand-suffix form by last two digits (Russian grammar)
        last_two = t % 100
        last_one = t % 10
        if 11 <= last_two <= 14:
            suffix = "тысяч"
        elif last_one == 1:
            suffix = "тысяча"
        elif last_one in (2, 3, 4):
            suffix = "тысячи"
        else:
            suffix = "тысяч"

        if t == 1:
            parts.append(f"одна {suffix}")
        elif t == 2:
            parts.append(f"две {suffix}")
        else:
            # Recursively convert t (handles hundreds and anything up to 999)
            t_text = _number_to_russian(t)
            # Fix gender for trailing "один"/"два" before тысяча (feminine)
            if t_text.endswith(" один"):
                t_text = t_text[:-5] + " одна"
            elif t_text == "один":
                t_text = "одна"
            elif t_text.endswith(" два"):
                t_text = t_text[:-4] + " две"
            elif t_text == "два":
                t_text = "две"
            parts.append(f"{t_text} {suffix}")
        n %= 1000

    if n >= 100:
        parts.append(hundreds[n // 100])
        n %= 100

    if n >= 20:
        parts.append(tens[n // 10])
        n %= 10

    if n > 0:
        parts.append(ones[n])

    return " ".join(p for p in parts if p)


async def _send_or_edit(query, context, text: str, reply_markup=None, **kw):
    """edit_message_text c фолбэком на send_message.

    Корень бага (Артём 31 мая): кнопка «Подобрать B-roll» висит ПОД видео-
    сообщением аватара. У видео нет .text (только .caption), и
    `query.edit_message_text(...)` падает в Telegram `BadRequest:
    "There is no text in the message to edit"`. Хендлер обрывается с
    Traceback, пользователь видит «кнопка не работает».

    Поведение:
      • edit_message_text успешен → возвращаем результат, ничего не отправляем
      • BadRequest содержит "no text" / "no caption" → шлём новое сообщение
      • BadRequest "message is not modified" → молча игнорим (известно безобидное)
      • иные BadRequest → пробрасываем (это реальная ошибка кода/прав)
    """
    from telegram.error import BadRequest as _BR  # локальный импорт чтобы не тащить наверх
    try:
        return await query.edit_message_text(text, reply_markup=reply_markup, **kw)
    except _BR as e:
        msg = str(e).lower()
        if "no text" in msg or "no caption" in msg:
            return await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=text,
                reply_markup=reply_markup,
                **kw,
            )
        if "not modified" in msg:
            return None
        raise


def transliterate_for_tts(text: str, model_id: str | None = None) -> str:
    """Replace English words with Russian phonetic equivalents and clean up text for TTS.

    model_id — модель ElevenLabs. У v3 НЕ инжектим SSML <break>-теги: v3 их
    не поддерживает и озвучивает как мусор («премани», Артём 31 мая). Для v3
    паузы держатся на пунктуации, переносы схлопываются в пробел. v2/turbo —
    <break> как раньше. None → v2-поведение (обратная совместимость).
    """
    import re
    result = text

    # 0pre. Safety-strip: remove any URLs and bot asset-reference lines that
    # might have leaked in from Notion (e.g. "🤖 Аватар (...): https://..."),
    # plus whole lines starting with asset marker emoji.
    result = re.sub(r'^\s*[🤖🎥🎙📎].*$', '', result, flags=re.MULTILINE)
    result = re.sub(r'https?://\S+', '', result)
    # Collapse blank lines created by the strips
    result = re.sub(r'\n{3,}', '\n\n', result)

    # 0. Convert numbers to Russian words
    def _replace_number(match):
        num_str = match.group(0).replace(" ", "").replace("\u00a0", "")
        try:
            n = int(num_str)
            if n > 999_999_999:
                return num_str  # too large, leave as-is
            return _number_to_russian(n)
        except ValueError:
            return num_str

    # 0a. Handle special number patterns BEFORE general number replacement
    # "24/7" → "двадцать четыре на семь"
    result = re.sub(r'24\s*/\s*7', 'двадцать четыре на семь', result)
    # General "N/N" patterns (e.g. "3/5") → "N из N"
    result = re.sub(r'(\d+)\s*/\s*(\d+)', lambda m: f'{_number_to_russian(int(m.group(1)))} из {_number_to_russian(int(m.group(2)))}', result)

    # Match numbers with optional spaces/non-breaking spaces between digit groups
    result = re.sub(r'\d[\d\s\u00a0]*\d|\d+', _replace_number, result)

    # 1. Replace known English words/phrases with Russian phonetics
    # Sort by length descending to match longer phrases first
    for eng, rus in sorted(TRANSLIT_MAP.items(), key=lambda x: -len(x[0])):
        pattern = re.compile(re.escape(eng), re.IGNORECASE)
        result = pattern.sub(rus, result)

    # 2. Transliterate remaining English words that weren't in the dictionary
    def _translit_word(match):
        word = match.group(0)
        # Simple phonetic transliteration for unknown English words
        table = {
            "th": "т", "sh": "ш", "ch": "ч", "ph": "ф", "ck": "к",
            "oo": "у", "ee": "и", "ea": "и", "ou": "ау", "ow": "оу",
            "ai": "эй", "ay": "эй", "ey": "эй", "oi": "ой", "oy": "ой",
            "a": "а", "b": "б", "c": "к", "d": "д", "e": "э",
            "f": "ф", "g": "г", "h": "х", "i": "и", "j": "дж",
            "k": "к", "l": "л", "m": "м", "n": "н", "o": "о",
            "p": "п", "q": "к", "r": "р", "s": "с", "t": "т",
            "u": "а", "v": "в", "w": "в", "x": "кс", "y": "й", "z": "з",
        }
        lower = word.lower()
        rus_word = ""
        idx = 0
        while idx < len(lower):
            # Try 2-char combinations first
            if idx + 1 < len(lower) and lower[idx:idx+2] in table:
                rus_word += table[lower[idx:idx+2]]
                idx += 2
            elif lower[idx] in table:
                rus_word += table[lower[idx]]
                idx += 1
            else:
                rus_word += lower[idx]
                idx += 1
        return rus_word

    # Find remaining English words (2+ letters, not already Cyrillic)
    result = re.sub(r'\b[A-Za-z]{2,}\b', _translit_word, result)

    # 3. Clean up punctuation that confuses TTS
    # Replace ellipsis with comma (ellipsis causes long pauses in ElevenLabs)
    result = result.replace("...", ",").replace("…", ",")
    # Remove quotes of all kinds
    result = result.replace('"', '').replace("'", "").replace("«", "").replace("»", "")
    result = result.replace(""", "").replace(""", "").replace("'", "").replace("'", "")
    # Remove standalone dashes at start of lines (list/dialogue markers) FIRST —
    # до inline-замены, иначе строковый дефис превратится в запятую.
    result = re.sub(r'^[ \t]*[-—–][ \t]*', '', result, flags=re.MULTILINE)
    # Inline em/en-dash → запятая, ПРИКЛЕЕННАЯ к предыдущему слову (схлопываем
    # окружающие пробелы). Плавающая « , » (пробел ПЕРЕД запятой) озвучивается
    # v3 Creative как мусорное слово («терне»/«тире»/«можно», Артём 31 мая —
    # подтверждено реальной генерацией). «зарплаты — каждый» → «зарплаты, каждый».
    result = re.sub(r'[ \t]*[—–][ \t]*', ', ', result)
    result = re.sub(r'[ \t]+-[ \t]+', ', ', result)
    # Clean up multiple commas — but preserve newlines for paragraph pauses
    result = re.sub(r',\s*,', ',', result)
    result = re.sub(r'[ \t]{2,}', ' ', result)

    # 4. Pauses between thoughts.
    if (model_id or "").startswith("eleven_v3"):
        # v3 НЕ поддерживает <break> SSML — озвучивает их как мусор. Паузы
        # держатся на пунктуации (. , ! ?), переносы схлопываем в пробел.
        result = re.sub(r'\n+', ' ', result)
    else:
        # v2/turbo поддерживают <break> — инжектим явные паузы.
        # Paragraph breaks → 0.6s, sentence endings → 0.3s.
        result = re.sub(r'\n{2,}', ' <break time="0.6s" /> ', result)
        result = re.sub(r'\n+', ' <break time="0.3s" /> ', result)
    # Collapse accidental double spaces
    result = re.sub(r' {2,}', ' ', result)

    return result.strip()


def _prepare_tts_intonation(text: str) -> str:
    """Use Claude to prepare script for ElevenLabs TTS.

    Claude handles ONLY: punctuation, paragraph breaks, literary rephrasing,
    and stress marks on ambiguous RUSSIAN words.

    Claude does NOT touch Latin (English) brand names — TRANSLIT_MAP handles
    those deterministically in the next pipeline step. Mixing concerns here
    produced inconsistent results (half-transliterated words, ALL CAPS fake
    stress marks, etc.).
    """
    try:
        response = claude.messages.create(
            model="claude-opus-4-7",
            max_tokens=2000,
            system="""Ты — режиссёр озвучки для ElevenLabs (русский клонированный мужской голос, предприниматель, говорит на камеру живо и уверенно). Твоя задача — превратить сырой сценарий в текст, который синтез речи прочитает максимально естественно и литературно.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ЧТО ТЫ ДЕЛАЕШЬ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**A. ПУНКТУАЦИЯ для живой речи**
- Восклицательные знаки: 2–4 штуки на текст, на самых энергичных/удивительных местах. Не бойся их, они делают голос живым.
- Точки: разбивай длинные мысли на короткие предложения. Если в предложении больше 12–15 слов — рубани точкой.
- Запятые: ставь щедро для естественных микропауз между мыслями.
- Вопросительные знаки: там где фраза звучит как вопрос, ставь `?`.
- УБИРАЙ: все кавычки любого вида (", «», "", ''), многоточие (...), тире в начале строк (маркеры перечисления).
- Одиночное тире (—) — оставляй только если это смысловое противопоставление; везде где тире заменяет запятую, ставь запятую.

**B. АБЗАЦЫ для пауз**
Разбей текст на 3–6 абзацев, разделённых **двойным переводом строки** (\\n\\n). Каждый абзац — одна законченная мысль. ElevenLabs делает на них естественные вдохи. Без абзацев получается сплошная простыня.

**C. УДАРЕНИЯ — ТОЛЬКО на неоднозначных РУССКИХ словах**
Используй символ U+0301 (комбинирующая акута). Он ставится **сразу после ударной гласной** и выглядит как маленькая палочка над буквой: а́ е́ и́ о́ у́ ы́ э́ ю́ я́.

Примеры правильного написания (скопируй точно так):
- за́мок (строение) vs замо́к (на двери)
- а́тлас (сборник карт) vs атла́с (ткань)
- больша́я (прилаг. ж.р.) vs бо́льшая (сравнительная)
- по́том (наречие) vs пото́м (позже)

❌ НЕЛЬЗЯ использовать КАПС или ЗАГЛАВНЫЕ БУКВЫ для обозначения ударения. Это не работает в TTS. Ударение — ТОЛЬКО символ `́` после гласной.
❌ НЕ ставь ударения на короткие/очевидные слова (дом, кот, ты, я, мама, папа) — перегруз.
❌ НЕ ставь ударения на иностранные слова и бренды — их обработает отдельный этап автоматически.

**D. ЛИТЕРАТУРНОСТЬ**
- Можешь переформулировать неуклюжие обороты ради ритма и звучания.
- Можешь менять порядок слов в предложении.
- Можешь разбивать длинные предложения на короткие.
- Можешь заменять канцеляризмы на живые разговорные аналоги («осуществлять» → «делать»).
- Сохраняй: все факты, имена, числа, бренды, структуру (вступление→основа→призыв), тон автора.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ЧТО НЕЛЬЗЯ ДЕЛАТЬ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- **НЕ ТРОГАЙ латиницу.** Оставляй английские названия ровно в том виде, в каком они в исходнике: `ChatGPT`, `NotebookLM`, `Gemini`, `Notion`, `Claude`, `VPN`, `GPT-4o`, `YouTube`, `iPhone` и т.д. Их обработает отдельный этап. Если ты перепишешь их кириллицей — ты всё сломаешь.
- Не удаляй факты, бренды, имена, числа.
- Не добавляй новые факты, примеры, имена.
- Не меняй содержание или структуру.
- Не используй эмодзи и markdown.
- Не пиши комментарии, заголовки, пояснения.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ПРИМЕР ТРАНСФОРМАЦИИ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ВХОД:
«Скачать ChatGPT на айфон. В России. Пять минут. Без VPN, без карты и без знакомых в Майами. Показываю как это сделать. Готово — ChatGPT и Claude уже стоят. Раз уж ты тут, поставь заодно ещё три приложения: NotebookLM, Gemini и Notion — они тебе точно пригодятся. И сразу выходи из американского аккаунта обратно в свой — иначе подписки и обновления начнут жить своей жизнью.»

ВЫХОД:
Скачать ChatGPT на айфон. В России. За пять минут!

Без VPN, без карты, без знакомых в Майами. Сейчас покажу как.

Готово! ChatGPT и Claude уже стоят. Раз уж ты тут, поставь заодно ещё три приложения: NotebookLM, Gemini и Notion. Они тебе точно пригодятся.

И сразу выходи из американского аккаунта обратно в свой! Иначе подписки и обновления начнут жить своей жизнью.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Верни ТОЛЬКО обработанный текст, ничего больше.""",
            messages=[{"role": "user", "content": f"Исходный сценарий:\n\n{text}"}],
        )
        result = response.content[0].text.strip()
        # Sanity check: length should stay within reasonable bounds
        if len(result) < len(text) * 0.6 or len(result) > len(text) * 1.5:
            logger.warning(
                f"TTS intonation result size off ({len(result)} vs {len(text)}), using original"
            )
            return text
        return result
    except Exception as e:
        logger.warning(f"Ошибка интонационной разметки: {e}")
        return text


def generate_montage_plan(
    script_text: str,
    broll_descriptions: list[str],
    audio_duration: float,
    photo_mode: bool = False,
) -> list[dict]:
    """Use Claude to create a pro montage plan from script + B-roll list.

    Returns list of segments:
    [{"start": 0.0, "end": 3.5, "layout": "split", "broll_index": 0}, ...]

    Layouts: "avatar_full", "broll_full", "split"

    ``photo_mode=True`` relaxes the minimum-segment rule from 4.0s to 2.5s
    because Ken Burns stills feel static after ~3s — short-form rhythm
    demands faster cuts when the B-roll is photos rather than video clips.
    """
    broll_list_text = "\n".join(
        f"  [{i}] {desc}" for i, desc in enumerate(broll_descriptions)
    )

    prompt = f"""Ты — профессиональный видеомонтажёр для коротких вертикальных видео (Reels/Shorts/TikTok).

Задача: создай монтажный план для видео длительностью {audio_duration:.1f} секунд.

СЦЕНАРИЙ:
{script_text}

ДОСТУПНЫЕ B-ROLL КЛИПЫ:
{broll_list_text}

ПРАВИЛА МОНТАЖА:
1. НАЧАЛО: первые 2-3 секунды — ОБЯЗАТЕЛЬНО "avatar_full" (хук лицом, коротко и цепляюще)
2. ТЕЛО (середина): ТОЛЬКО "split" и "broll_full", чередуя. НИКАКОГО "avatar_full" в середине!
3. "broll_full" — для ярких демонстраций, визуальных доказательств
4. "split" — основной лейаут, ИСПОЛЬЗУЙ ЧАЩЕ ВСЕГО (50/50: B-roll сверху + аватар снизу)
5. Минимальная длительность сегмента: {"2.5" if photo_mode else "4"} секунды (СТРОГО!). Исключение: хук может быть 2-3 секунды.
6. Не больше 2 переходов между лейаутами на каждые 10 секунд
7. КОНЕЦ: последние 3-4 секунды — ОБЯЗАТЕЛЬНО "avatar_full" для CTA (призыв подписаться)
8. Каждый B-roll клип использовать СТРОГО 1 раз (не повторять индексы!)
9. Длительность сегмента с B-roll НЕ ДОЛЖНА превышать длительность клипа (указана в скобках). Если клип 4s — сегмент максимум 4s.
10. НИКОГДА не ставь два одинаковых лейаута подряд
11. Общее время всех сегментов ДОЛЖНО равняться {audio_duration:.1f} секунд
12. Структура: avatar(2.5s) → split(6s) → broll(4s) → split(6s) → broll(5s) → split(4s) → avatar(CTA 3s)

Верни ТОЛЬКО JSON-массив сегментов, без комментариев:
[
  {{"start": 0.0, "end": 2.5, "layout": "avatar_full", "broll_index": null}},
  {{"start": 2.5, "end": 8.5, "layout": "split", "broll_index": 0}},
  {{"start": 8.5, "end": 13.0, "layout": "broll_full", "broll_index": 1}},
  {{"start": 13.0, "end": 19.0, "layout": "split", "broll_index": 2}},
  {{"start": 19.0, "end": 23.0, "layout": "broll_full", "broll_index": 3}},
  {{"start": 23.0, "end": 27.0, "layout": "split", "broll_index": 4}},
  {{"start": 27.0, "end": 30.0, "layout": "avatar_full", "broll_index": null}},
  ...
]"""

    try:
        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        # Extract JSON from response (handle markdown code blocks)
        if "```" in raw:
            import re as _re
            m = _re.search(r"```(?:json)?\s*\n?(.*?)```", raw, _re.DOTALL)
            if m:
                raw = m.group(1).strip()

        plan = json.loads(raw)

        # Validate and fix
        # Photo mode: Ken Burns stills need faster cuts (~2.2s of active
        # motion after fade in/out), so we lower the floor. Video mode keeps
        # the original 3.5s rule so real B-roll clips have room to breathe.
        MIN_SEG_SEC = 2.2 if photo_mode else 3.5
        validated = []
        for seg in plan:
            s = {
                "start": float(seg.get("start", 0)),
                "end": float(seg.get("end", 0)),
                "layout": seg.get("layout", "avatar_full"),
                "broll_index": seg.get("broll_index"),
            }
            if s["layout"] not in ("avatar_full", "broll_full", "split"):
                s["layout"] = "avatar_full"
            if s["broll_index"] is not None:
                s["broll_index"] = int(s["broll_index"])
            if s["end"] > s["start"]:
                validated.append(s)

        if not validated:
            raise ValueError("Empty montage plan after validation")

        # ── Post-processing: enforce quality rules ──

        # Rule A: merge segments shorter than MIN_SEG_SEC into previous
        merged = [validated[0]]
        for seg in validated[1:]:
            dur = seg["end"] - seg["start"]
            if dur < MIN_SEG_SEC and merged:
                # extend previous segment to cover this one
                merged[-1]["end"] = seg["end"]
            else:
                merged.append(seg)
        validated = merged

        # Rule B: first segment MUST be avatar_full
        if validated[0]["layout"] != "avatar_full":
            validated[0]["layout"] = "avatar_full"
            validated[0]["broll_index"] = None
            logger.info("[montage_plan] Forced first segment to avatar_full")

        # Rule C: last segment MUST be avatar_full (CTA)
        if validated[-1]["layout"] != "avatar_full":
            # If last segment is short, just change it
            last_dur = validated[-1]["end"] - validated[-1]["start"]
            if last_dur <= 6.0:
                validated[-1]["layout"] = "avatar_full"
                validated[-1]["broll_index"] = None
            else:
                # Split: keep current layout shorter, add avatar CTA at end
                cta_start = validated[-1]["end"] - 4.0
                old_end = validated[-1]["end"]
                validated[-1]["end"] = cta_start
                validated.append({"start": cta_start, "end": old_end, "layout": "avatar_full", "broll_index": None})
            logger.info("[montage_plan] Forced last segment to avatar_full for CTA")

        # Rule D: merge consecutive segments with same layout
        deduped = [validated[0]]
        for seg in validated[1:]:
            if seg["layout"] == deduped[-1]["layout"]:
                deduped[-1]["end"] = seg["end"]  # extend previous
                logger.info(f"[montage_plan] Merged consecutive {seg['layout']} segments")
            else:
                deduped.append(seg)
        validated = deduped

        # Rule E2: NO avatar_full in the middle — only first and last segments
        if len(validated) > 2:
            available_broll = set(range(len(broll_descriptions)))
            # Collect already-used indices
            for seg in validated:
                bi = seg.get("broll_index")
                if bi is not None:
                    available_broll.discard(bi)
            # Convert middle avatar_full to split with unused broll
            for seg in validated[1:-1]:
                if seg["layout"] == "avatar_full":
                    if available_broll:
                        seg["layout"] = "split"
                        seg["broll_index"] = available_broll.pop()
                        logger.info(f"[montage_plan] Converted middle avatar_full → split with broll {seg['broll_index']}")
                    else:
                        seg["layout"] = "split"
                        seg["broll_index"] = 0  # fallback to first clip
                        logger.info("[montage_plan] Converted middle avatar_full → split (no unused broll, reusing 0)")

        # Rule E: each broll_index used strictly once — reassign duplicates
        used_indices = set()
        all_indices = set(range(len(broll_descriptions)))
        for seg in validated:
            bi = seg.get("broll_index")
            if bi is not None:
                if bi in used_indices or bi >= len(broll_descriptions):
                    # Find unused broll clip
                    available = all_indices - used_indices
                    if available:
                        new_bi = available.pop()
                        logger.info(f"[montage_plan] Replaced duplicate broll {bi} → {new_bi}")
                        seg["broll_index"] = new_bi
                        used_indices.add(new_bi)
                    else:
                        # No more clips available — convert to avatar_full
                        seg["layout"] = "avatar_full"
                        seg["broll_index"] = None
                        logger.info(f"[montage_plan] No broll left, converted to avatar_full")
                else:
                    used_indices.add(bi)

        # Rule F: final pass — merge any remaining short segments (created by earlier rules)
        final_merged = [validated[0]]
        for seg in validated[1:]:
            dur = seg["end"] - seg["start"]
            if dur < MIN_SEG_SEC and final_merged:
                final_merged[-1]["end"] = seg["end"]
            else:
                final_merged.append(seg)
        validated = final_merged

        # Ensure last segment ends at audio_duration
        validated[-1]["end"] = audio_duration

        logger.info(
            f"[montage_plan] Generated {len(validated)} segments: "
            + ", ".join(f"{s['layout']}({s['end']-s['start']:.1f}s)" for s in validated)
        )
        return validated

    except Exception as e:
        logger.error(f"[montage_plan] Failed: {e}", exc_info=True)
        # Fallback: smart alternating plan (avatar→split→broll→split→avatar CTA)
        n_broll = len(broll_descriptions)
        segments = []
        bi = 0
        # Structure: avatar(4s) → split → broll → split → ... → avatar(CTA 4s)
        cta_dur = 4.0
        hook_dur = min(2.5, audio_duration * 0.10)
        body_dur = audio_duration - hook_dur - cta_dur
        cursor = 0.0

        # Hook
        segments.append({"start": 0.0, "end": hook_dur, "layout": "avatar_full", "broll_index": None})
        cursor = hook_dur

        # Body: alternate split and broll_full, ~6s each
        seg_dur = max(4.0, body_dur / max(1, min(n_broll, int(body_dur / 5))))
        while cursor < audio_duration - cta_dur - 1.0:
            end = min(cursor + seg_dur, audio_duration - cta_dur)
            if len(segments) % 2 == 1:  # odd = split
                segments.append({"start": cursor, "end": end, "layout": "split", "broll_index": bi % n_broll})
            else:  # even = broll_full
                segments.append({"start": cursor, "end": end, "layout": "broll_full", "broll_index": bi % n_broll})
            bi += 1
            cursor = end

        # CTA at end
        segments.append({"start": cursor, "end": audio_duration, "layout": "avatar_full", "broll_index": None})
        return segments


def generate_voiceover(
    script_text: str,
    output_path: str,
    style_override: float | None = None,
    engine: str | None = None,
    skip_intonation: bool = False,
    model_override: str | None = None,
) -> str:
    """Generate voiceover. Dispatches to Fish Audio or ElevenLabs.

    engine: 'fish', 'elevenlabs', or None (auto: Fish Audio if configured, else ElevenLabs).
    skip_intonation: if True, bypass ``_prepare_tts_intonation`` — use when the
        caller has already run intonation on the full script before splitting.
    model_override: if set, overrides the brand's ``eleven_model_id`` — нужно
        чтобы пикер «v2 vs v3» перед озвучкой передал выбор пользователя.
    """
    if engine is None:
        engine = "elevenlabs" if elevenlabs_client else ("fish" if FISH_API_KEY and FISH_VOICE_ID else "elevenlabs")

    if engine == "fish":
        try:
            return generate_speech_fish(script_text, output_path, skip_intonation=skip_intonation)
        except Exception as e:
            if elevenlabs_client:
                logger.warning(f"Fish Audio failed ({e}), falling back to ElevenLabs")
            else:
                raise

    # ElevenLabs path
    if not elevenlabs_client:
        raise RuntimeError("ElevenLabs не настроен. Добавь ELEVENLABS_API_KEY в .env")

    from elevenlabs import VoiceSettings

    style_val = style_override if style_override is not None else VOICE_SETTINGS["style"]

    # Step 1: Claude adds expressive punctuation (skipped if caller already did this)
    if skip_intonation:
        expressive_text = script_text
        logger.info("TTS: интонация пропущена (уже применена на целом сценарии)")
    else:
        expressive_text = _prepare_tts_intonation(script_text)
        logger.info(f"TTS после интонации (per-part): {expressive_text[:100]}...")

    # Brand overrides (per active brand, see /brand command).
    # Считаем модель ДО транслитерации — нужно знать v2/v3 для решения,
    # инжектить ли <break>-теги (v3 их не поддерживает).
    brand = _get_active_brand()
    voice_id = brand.get("eleven_voice_id") or ELEVENLABS_VOICE_ID
    # model_override побеждает бренд — нужно для пикера «v2/v3 перед озвучкой».
    model_id = model_override or brand.get("eleven_model_id") or "eleven_multilingual_v2"

    # Step 2: Transliterate English words + numbers for Russian pronunciation
    tts_text = transliterate_for_tts(expressive_text, model_id=model_id)
    logger.debug(f"TTS после транслитерации: {tts_text[:100]}...")

    # ElevenLabs v3 — другая шкала voice_settings. У v3 один эффективный
    # параметр stability: 0.0 = Creative (макс. выразительность, эмоции),
    # 0.5 = Natural, 1.0 = Robust (детерминированно). v2-настройки бота
    # (stability=0.60, style=0.05) — для v3 это «стерильно». Поэтому для
    # v3-моделей берём бренд-настройку (по умолчанию Creative для maksim).
    if model_id.startswith("eleven_v3"):
        _stability = brand.get("eleven_v3_stability", 0.0)  # Creative
        _style = 0.0  # v3 не использует style — отключаем
        _speed = brand.get("eleven_v3_speed", 1.0)
        logger.info(f"TTS v3 settings: stability={_stability} (Creative={_stability < 0.2})")
    else:
        _stability = VOICE_SETTINGS["stability"]
        _style = style_val
        _speed = VOICE_SETTINGS["speed"]

    audio_generator = elevenlabs_client.text_to_speech.convert(
        voice_id=voice_id,
        text=tts_text,
        model_id=model_id,
        voice_settings=VoiceSettings(
            stability=_stability,
            similarity_boost=VOICE_SETTINGS["similarity_boost"],
            style=_style,
            speed=_speed,
        ),
        output_format="mp3_44100_128",
    )

    # Write audio to file
    with open(output_path, "wb") as f:
        for chunk in audio_generator:
            f.write(chunk)

    logger.info(
        f"Озвучка сгенерирована (ElevenLabs brand={_active_brand}, "
        f"voice={voice_id[:10]}..., model={model_id}): {output_path}"
    )
    return output_path


async def _run_voiceover_generation(query, data: dict, model_id: str) -> None:
    """Полный прогон озвучки сценария выбранной моделью ElevenLabs.

    Вызывается двумя путями:
      • из callback'а «🎙 Озвучить» — после того, как пользователь выбрал
        модель v2/v3 в picker'е;
      • из callback'а ``voicepick:<model>`` — сразу после клика на модель.
    """
    model_tag = "v3" if model_id.startswith("eleven_v3") else "v2"
    await query.edit_message_text(
        f"🎙 Применяю интонацию ({model_tag}) на целый сценарий..."
    )
    try:
        script_text = data["script"]
        full_processed = await asyncio.to_thread(_prepare_tts_intonation, script_text)
        logger.info(
            f"TTS full_processed ({len(full_processed)} chars):\n{full_processed}"
        )
        # v3 + короткий сценарий → единый блок (цельная интонация, без стыков).
        _tp = _voice_target_parts(model_id, full_processed)
        parts = split_script_to_parts(full_processed, target_parts=_tp)
        logger.info(
            f"TTS split (model={model_id}, target_parts={_tp}): "
            + " | ".join(f"[{i}]({len(p)}) {p[:40]}…{p[-25:]}"
                         for i, p in enumerate(parts))
        )
        data["voice_parts"] = parts
        data["voice_approved"] = [False] * len(parts)
        data["state"] = "voice_editing"
        _save_pending(pending)
        await query.edit_message_text(
            f"🎙 Интонация готова, озвучиваю {len(parts)} частей ({model_tag})..."
        )

        for i, part_text in enumerate(parts):
            voice_path = str(ASSETS_DIR / f"voice_part_{i}.mp3")
            await asyncio.to_thread(
                generate_voiceover, part_text, voice_path,
                None, None, True, model_id,
            )

            notion_id = data.get("notion_page_id")
            if notion_id:
                _save_voice_file(notion_id, i, voice_path)
            _save_to_project(data, f"voice_part_{i}.mp3", voice_path)

            with open(voice_path, "rb") as audio_file:
                await query.get_bot().send_audio(
                    chat_id=query.message.chat_id,
                    audio=audio_file,
                    title=f"Часть {i+1}/{len(parts)} ({model_tag})",
                    caption=f"🎙 Часть {i+1}/{len(parts)} ({model_tag}):\n\n«{part_text}»",
                )

        if data.get("notion_page_id"):
            _save_voice_meta(
                data["notion_page_id"], parts, data.get("voice_approved", [])
            )

        await query.edit_message_text(
            _voice_panel_text(data),
            reply_markup=_voice_panel_keyboard(data),
        )
    except Exception as e:
        logger.error(f"Ошибка озвучки: {e}", exc_info=True)
        await query.edit_message_text(f"Ошибка озвучки: {e}")


def generate_speech_fish(script_text: str, output_path: str, skip_intonation: bool = False) -> str:
    """Generate voiceover using Fish Audio API with cloned voice.

    skip_intonation: if True, bypass ``_prepare_tts_intonation`` — use when the
        caller has already run intonation on the full script before splitting.
    """
    import httpx

    if not FISH_API_KEY or not FISH_VOICE_ID:
        raise RuntimeError("Fish Audio не настроен. Добавь FISH_API_KEY и FISH_VOICE_ID в .env")

    # Step 1: Claude adds expressive punctuation (skipped if caller already did this)
    if skip_intonation:
        expressive_text = script_text
        logger.info("Fish TTS: интонация пропущена (уже применена на целом сценарии)")
    else:
        expressive_text = _prepare_tts_intonation(script_text)

    # Step 2: Transliterate English words for Russian pronunciation
    tts_text = transliterate_for_tts(expressive_text)
    logger.debug(f"Fish TTS текст: {tts_text[:100]}...")

    # Fish Audio TTS API — returns streamed audio
    resp = httpx.post(
        "https://api.fish.audio/v1/tts",
        headers={
            "Authorization": f"Bearer {FISH_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "text": tts_text,
            "reference_id": FISH_VOICE_ID,
            "format": "mp3",
            "mp3_bitrate": 128,
            "latency": "normal",
        },
        timeout=120,
    )

    if resp.status_code == 402:
        raise RuntimeError("Fish Audio: недостаточно баланса. Пополни на fish.audio/pricing")
    if resp.status_code != 200:
        raise RuntimeError(f"Fish Audio error {resp.status_code}: {resp.text[:300]}")

    with open(output_path, "wb") as f:
        f.write(resp.content)

    logger.info(f"Озвучка сгенерирована (Fish Audio): {output_path}")
    return output_path


# --- Silence trimmer ---
def trim_long_silences(
    audio_path: str,
    output_path: str | None = None,
    max_silence_sec: float = 0.5,
    keep_silence_sec: float = 0.3,
    threshold_db: int = -30,
) -> str:
    """Detect silences > max_silence_sec and trim them to keep_silence_sec.

    Uses ffmpeg silencedetect to find pauses, then rebuilds audio
    with shortened gaps.  Returns path to trimmed file.
    """
    import re as _re
    import tempfile

    audio_path = str(audio_path)
    if output_path is None:
        output_path = audio_path  # overwrite in-place

    # 1. Detect silences
    detect_cmd = [
        "ffmpeg", "-i", audio_path,
        "-af", f"silencedetect=noise={threshold_db}dB:d={max_silence_sec}",
        "-f", "null", "-",
    ]
    result = subprocess.run(detect_cmd, capture_output=True, text=True, timeout=60)

    # Parse silence_start / silence_end from stderr
    silences: list[tuple[float, float]] = []
    starts: list[float] = []
    for line in result.stderr.split("\n"):
        m_start = _re.search(r"silence_start:\s*([\d.]+)", line)
        if m_start:
            starts.append(float(m_start.group(1)))
        m_end = _re.search(r"silence_end:\s*([\d.]+)", line)
        if m_end and starts:
            silences.append((starts.pop(), float(m_end.group(1))))

    if not silences:
        logger.info(f"[silence_trim] No long silences found in {Path(audio_path).name}")
        return audio_path

    logger.info(
        f"[silence_trim] Found {len(silences)} silences > {max_silence_sec}s "
        f"in {Path(audio_path).name}, trimming to {keep_silence_sec}s"
    )

    # 2. Get total duration
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", audio_path],
        capture_output=True, text=True, timeout=15,
    )
    total_dur = float(json.loads(probe.stdout)["format"]["duration"])

    # 3. Build segment list: speech chunks + short silence replacements
    #    Each silence [start, end] → keep audio up to (start + keep/2),
    #    insert keep_silence_sec of silence, resume from (end - keep/2)
    half_keep = keep_silence_sec / 2
    segments: list[tuple[float, float]] = []  # (start, end) of source audio to keep
    cursor = 0.0

    for s_start, s_end in silences:
        # Keep speech before silence + tiny tail
        seg_end = min(s_start + half_keep, s_end)
        if seg_end > cursor:
            segments.append((cursor, seg_end))
        # Skip the middle of silence; resume with tiny lead-in
        cursor = max(s_end - half_keep, s_start)

    # Keep the rest after last silence
    if cursor < total_dur:
        segments.append((cursor, total_dur))

    if not segments:
        return audio_path

    # 4. Build ffmpeg filter to extract and concat segments
    fd, tmp_out = tempfile.mkstemp(suffix=".mp3", prefix="trimmed_")
    os.close(fd)

    filter_parts = []
    for i, (s, e) in enumerate(segments):
        filter_parts.append(f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[s{i}];")

    concat_inputs = "".join(f"[s{i}]" for i in range(len(segments)))
    filter_parts.append(f"{concat_inputs}concat=n={len(segments)}:v=0:a=1[out]")

    filter_str = "\n".join(filter_parts)

    trim_cmd = [
        "ffmpeg", "-y", "-i", audio_path,
        "-filter_complex", filter_str,
        "-map", "[out]",
        "-c:a", "libmp3lame", "-b:a", "192k",
        tmp_out,
    ]
    r = subprocess.run(trim_cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        logger.error(f"[silence_trim] ffmpeg failed: {r.stderr[-400:]}")
        try:
            os.unlink(tmp_out)
        except OSError:
            pass
        return audio_path  # fallback: return untrimmed

    # 5. Replace original
    import shutil
    shutil.move(tmp_out, output_path)

    new_size = Path(output_path).stat().st_size / 1024
    logger.info(
        f"[silence_trim] Trimmed {len(silences)} silences → {output_path} ({new_size:.0f} KB)"
    )
    return output_path


# --- HeyGen video generation ---
def heygen_generate_video(audio_url: str, look_id: str = None, avatar_version: str = "v3") -> str:
    """Submit video generation to HeyGen. Returns video_id.

    avatar_version:
      'v3'  → Avatar 3 (sends use_avatar_iv_model=False explicitly — see note below)
      'v2'  → Avatar 4 (legacy flag version="4")
      'v4'  → Avatar IV (new default since 2026-04-20, sends use_avatar_iv_model=True)

    Both use /v2/video/generate — the AV4 dedicated endpoint is only for talking photos.

    ⚠️ HeyGen breaking change 2026-04-20: POST /v2/video/generate now defaults to
    Avatar IV for self-serve users (Studio API). Avatar IV is ~3x more expensive
    ($0.05/sec vs $0.0167/sec @ 1080p photo avatar). To preserve our historical
    cost economics ($1.39/ролик с HeyGen, see cost_per_video_apr17.md), we now
    explicitly send use_avatar_iv_model=False unless caller asks for v4. Cited:
    https://developers.heygen.com/changelog#avatar-iv-default-engine
    """
    import httpx
    if not HEYGEN_API_KEY:
        raise RuntimeError("HEYGEN_API_KEY не настроен")

    headers = {"X-Api-Key": HEYGEN_API_KEY, "Accept": "application/json", "Content-Type": "application/json"}
    # Brand overrides (per active brand, see /brand).
    # Resolution order: explicit look_id > brand override > default look1.
    _brand = _get_active_brand()
    _brand_avatar = _brand.get("heygen_avatar_id")
    avatar_id = look_id or _brand_avatar or HEYGEN_LOOKS["look1"]["id"]

    character = {
        "type": "avatar",
        "avatar_id": avatar_id,
        "avatar_style": "normal",
    }
    # Avatar 4 (legacy v2 flag) — kept for backward compatibility
    if avatar_version == "v2":
        character["version"] = "4"

    # Explicit Avatar IV / Avatar III opt-in (post 2026-04-20 breaking change).
    # Default 'v3' → opt OUT of Avatar IV → стандартный Avatar 3 ($1/мин).
    # 'v4' → opt IN → новый Avatar IV ($3/мин). Включается явно.
    if avatar_version == "v4":
        character["use_avatar_iv_model"] = True
    else:
        character["use_avatar_iv_model"] = False

    payload = {
        "video_inputs": [{
            "character": character,
            "voice": {"type": "audio", "audio_url": audio_url},
        }],
        "dimension": {"width": 1080, "height": 1920},
    }

    if avatar_version == "v4":
        ver_label = "Avatar IV (use_avatar_iv_model=true, ~$0.05/sec)"
    elif avatar_version == "v2":
        ver_label = "Avatar 4 legacy (version=4)"
    else:
        ver_label = "Avatar 3 (use_avatar_iv_model=false, ~$0.0167/sec)"
    logger.info(f"HeyGen generate: {ver_label}, avatar={avatar_id[:16]}..., audio={audio_url[:60]}...")

    resp = httpx.post("https://api.heygen.com/v2/video/generate", headers=headers, json=payload, timeout=30)
    data = resp.json()
    if data.get("error"):
        raise RuntimeError(f"HeyGen error: {data['error'].get('message', data['error'])}")
    return data["data"]["video_id"]


def heygen_upload_audio_asset(audio_path: str) -> str:
    """Upload a local audio file to HeyGen's asset store → return its audio_url.

    Headless helper (no Telegram) reused by the pipeline-spine paid-job step
    (``pipeline_step_services.BotStepRunner.start_paid_job`` via injection).
    Mirrors the inline upload used in the live selfvoice flow.
    """
    import httpx
    if not HEYGEN_API_KEY:
        raise RuntimeError("HEYGEN_API_KEY не настроен")
    with open(audio_path, "rb") as af:
        up = httpx.post(
            "https://upload.heygen.com/v1/asset",
            headers={"X-Api-Key": HEYGEN_API_KEY, "Content-Type": "audio/mpeg"},
            content=af.read(), timeout=120,
        ).json()
    if up.get("code") != 100:
        raise RuntimeError(f"HeyGen audio upload error: {up}")
    return up["data"]["url"]


def heygen_register_photo_avatar(image_url: str, name: str = "Photo Avatar") -> str:
    """Register a user-uploaded photo as a reusable HeyGen Photo Avatar.

    Returns persistent ``avatar_id`` (string), suitable for reuse across many
    videos via :func:`heygen_generate_video` ``look_id`` parameter.

    HeyGen v3 endpoint — POST /v3/avatars with type:"photo" + file URL.
    On free tier there's a hard cap of ~3 photo avatars per workspace; if
    HeyGen returns a quota error, caller should fallback to per-video
    Image-to-Video (POST /v3/videos type:"image", as in /heygen_test).

    Cited: reference_heygen_api_v3.md (Photo Avatar reusable section).
    """
    import httpx
    if not HEYGEN_API_KEY:
        raise RuntimeError("HEYGEN_API_KEY не настроен")

    headers = {
        "x-api-key": HEYGEN_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "type": "photo",
        "name": (name or "Photo Avatar")[:50],
        "file": {"type": "url", "url": image_url},
    }
    logger.info(f"HeyGen register photo avatar: name={payload['name']!r} url={image_url[:80]}")

    resp = httpx.post(
        "https://api.heygen.com/v3/avatars",
        headers=headers,
        json=payload,
        timeout=60,
    )

    # Logging-first: видим что HeyGen вернул, ДО попытки json().
    # Был случай (4 мая): пустой body → JSONDecodeError "Expecting value: line 1 column 1".
    body_preview = (resp.text or "")[:400]
    logger.info(
        f"HeyGen v3/avatars response: status={resp.status_code} "
        f"content-type={resp.headers.get('content-type', 'n/a')!r} "
        f"body_len={len(resp.text or '')} body_preview={body_preview!r}"
    )

    # Пустое тело — отдельная ошибка (HeyGen иногда так делает на rate limit /
    # временные сбои бэкенда; UI Studio в этот момент тоже выдаёт «попробуйте позже»)
    if not resp.text or not resp.text.strip():
        raise RuntimeError(
            f"HeyGen v3/avatars вернул пустой ответ (HTTP {resp.status_code}). "
            f"Возможно временный сбой API или превышен лимит регистраций. "
            f"Альтернатива — использовать /heygen_test (Image-to-Video, без "
            f"регистрации avatar_id, поштучно за ролик)."
        )

    # Не-JSON (HTML, редирект, plain text)
    try:
        data = resp.json()
    except Exception:
        raise RuntimeError(
            f"HeyGen v3/avatars вернул не-JSON (HTTP {resp.status_code}, "
            f"content-type={resp.headers.get('content-type')!r}). "
            f"Body: {body_preview!r}. Альтернатива — /heygen_test."
        )

    if resp.status_code >= 400:
        err = data.get("error") or data.get("message") or data
        raise RuntimeError(f"HeyGen v3/avatars {resp.status_code}: {err}")

    inner = data.get("data") or data
    avatar_item = inner.get("avatar_item") or inner
    avatar_id = avatar_item.get("id")
    if not avatar_id:
        raise RuntimeError(f"HeyGen v3/avatars: нет avatar_id в ответе. Body: {data}")
    logger.info(f"HeyGen photo avatar registered: avatar_id={avatar_id}")
    return avatar_id


def heygen_check_status(video_id: str) -> dict:
    """Check HeyGen video generation status. Returns dict with status, video_url, duration."""
    import httpx
    headers = {"X-Api-Key": HEYGEN_API_KEY, "Accept": "application/json"}
    resp = httpx.get(f"https://api.heygen.com/v1/video_status.get?video_id={video_id}", headers=headers, timeout=15)
    data = resp.json().get("data", {})
    return {
        "status": data.get("status"),
        "video_url": data.get("video_url"),
        "duration": data.get("duration"),
        "error": data.get("error"),
    }


def heygen_get_quota() -> int:
    """Get remaining HeyGen API credits."""
    import httpx
    headers = {"X-Api-Key": HEYGEN_API_KEY, "Accept": "application/json"}
    resp = httpx.get("https://api.heygen.com/v2/user/remaining_quota", headers=headers, timeout=10)
    return resp.json().get("data", {}).get("remaining_quota", 0)


def split_script_to_parts(script_text: str, target_parts: int | None = None) -> list[str]:
    """Split script into roughly equal parts, always on sentence boundaries.

    Strategy:
    1. First break text into paragraphs (\\n\\n is Claude's intonation pause marker).
    2. Inside each paragraph, break into sentences at .!?»" followed by whitespace.
    3. Merge back any fragment that does NOT end with a proper terminator —
       prevents mid-sentence splits when Claude inserts a \\n\\n or period in
       the middle of a clause.
    4. Distribute atomic units into target_parts by char length.

    If target_parts is None, auto-scale: ~450 chars per part (≈30s of speech).
    """
    import re

    text = script_text.strip()
    if not text:
        return []

    TERMINATORS = '.!?»"…'
    terminator_tail = re.compile(rf'[{re.escape(TERMINATORS)}]\s*$')
    sentence_splitter = re.compile(rf'(?<=[{re.escape(TERMINATORS)}])\s+')

    # 1. Break into paragraphs first — Claude-inserted \n\n are "breath" pauses
    #    between complete thoughts.
    paragraphs = [p.strip() for p in re.split(r'\n{2,}', text) if p.strip()]

    # 1a. If a paragraph doesn't end with a proper terminator, Claude put a
    #     pause in the middle of a clause. Glue it to the next paragraph so the
    #     sentence survives intact.
    glued: list[str] = []
    buf_para = ""
    for p in paragraphs:
        buf_para = f"{buf_para} {p}".strip() if buf_para else p
        if terminator_tail.search(buf_para):
            glued.append(buf_para)
            buf_para = ""
    if buf_para:
        glued.append(buf_para)
    paragraphs = glued

    # 2. Break each paragraph into atomic sentence units, merging any fragment
    #    that doesn't end with a proper terminator.
    units: list[str] = []
    for para in paragraphs:
        # Normalize single newlines inside a paragraph to spaces
        para = re.sub(r'\s+', ' ', para).strip()
        raw = sentence_splitter.split(para)
        raw = [s.strip() for s in raw if s.strip()]

        buf = ""
        for s in raw:
            buf = f"{buf} {s}".strip() if buf else s
            if terminator_tail.search(buf):
                units.append(buf)
                buf = ""
        if buf:
            # Trailing fragment without terminator — keep as its own unit
            units.append(buf)

    if not units:
        return [text]

    if target_parts is None:
        total = sum(len(u) for u in units)
        # ~450 chars per part ≈ 30 seconds at normal Russian pace
        target_parts = max(2, min(6, round(total / 450)))

    if len(units) <= target_parts:
        return units

    # 3. Distribute atomic units into target_parts roughly equally by length.
    total_len = sum(len(u) for u in units)
    target_len = total_len / target_parts

    parts: list[str] = []
    current_part: list[str] = []
    current_len = 0

    for u in units:
        current_part.append(u)
        current_len += len(u)
        if current_len >= target_len and len(parts) < target_parts - 1:
            parts.append(" ".join(current_part))
            current_part = []
            current_len = 0

    if current_part:
        parts.append(" ".join(current_part))

    # Final safety: every part except possibly the last must end with a
    # proper sentence terminator. If one doesn't, merge it into the next.
    cleaned: list[str] = []
    i = 0
    while i < len(parts):
        p = parts[i]
        if i < len(parts) - 1 and not terminator_tail.search(p):
            logger.warning(
                f"split_script_to_parts: часть {i} без терминатора, сливаю со следующей: {p[-40:]!r}"
            )
            parts[i + 1] = p + " " + parts[i + 1]
        else:
            cleaned.append(p)
        i += 1

    return cleaned or parts


def _voice_target_parts(model_id: str | None, text: str, cap: int = 1000) -> int | None:
    """Сколько частей для озвучки в зависимости от модели ElevenLabs.

    Для v3 короткий сценарий озвучиваем ОДНИМ блоком (`target_parts=1`):
    разбивка на части меняет интонацию на стыке (Артём 31 мая). v3 держит
    качество и длину на цельном тексте.

    Длинный сценарий (>cap символов) у v3 всё равно делим — защита от
    обрезки/просадки ElevenLabs на длинном одиночном запросе.

    v2 и прочие — None (авто-разбивка ~450 симв/часть, старое поведение).
    """
    if (model_id or "").startswith("eleven_v3") and len((text or "").strip()) <= cap:
        return 1
    return None


# --- Load prompts ---
SCRIPT_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")
COVER_PROMPT_PATH = Path(__file__).parent / "cover_prompt.txt"
COVER_TEXT_PROMPT = COVER_PROMPT_PATH.read_text(encoding="utf-8")
COVER_MODEL = "claude-opus-4-7"  # Opus for quality cover texts

# --- Rubric classification (brand-aware) ---
# Default (Artem) rubric taxonomy. A brand overrides these via
# notion_rubrics / notion_rubric_rules / notion_rubric_context in BRANDS,
# so Maksim's cards get HIS Notion «Рубрика» options, not Artem's.
RUBRICS = [
    "Свободный формат",
    "Кейс студии | Мои решения",
    "Личный мысли | Инсайты",
    "ИИ тренды (аватар+скринкаст)",
    "Экспертный (Аватар + Скринкаст)",
]

_DEFAULT_RUBRIC_RULES = (
    "- Если идея про кейс студии, клиентов, проекты → \"Кейс студии | Мои решения\"\n"
    "- Если идея про личный опыт, ошибки, выводы → \"Личный мысли | Инсайты\"\n"
    "- Если идея про новости ИИ, тренды, инструменты → \"ИИ тренды (аватар+скринкаст)\"\n"
    "- Если идея с демонстрацией экрана, обучение → \"Экспертный (Аватар + Скринкаст)\"\n"
    "- Если непонятно → \"Свободный формат\""
)
_DEFAULT_RUBRIC_CONTEXT = (
    "автор — предприниматель, сооснователь AI-студии. "
    "Аудитория — предприниматели 30+, русскоязычные."
)


def _brand_rubrics(brand_name: str | None = None) -> list[str]:
    """Valid rubric names for the active (or given) brand.

    Falls back to the default Artem RUBRICS when a brand defines none.
    """
    return list(_resolve_brand_cfg(brand_name).get("notion_rubrics") or RUBRICS)


def _build_structure_prompt(brand_name: str | None = None) -> str:
    """Brand-aware idea→Notion-card structuring prompt.

    Rubric list, classification rules and audience context all come from
    the brand config so Maksim gets his own «Рубрика» options, not Artem's.
    """
    cfg = _resolve_brand_cfg(brand_name)
    rubrics = cfg.get("notion_rubrics") or RUBRICS
    rules = cfg.get("notion_rubric_rules") or _DEFAULT_RUBRIC_RULES
    context = cfg.get("notion_rubric_context") or _DEFAULT_RUBRIC_CONTEXT
    rubric_line = " | ".join(rubrics)
    return (
        "Пользователь прислал сырую идею контента. Структурируй её и верни "
        "ТОЛЬКО чистый JSON без markdown:\n\n"
        "{\n"
        '  "title": "Короткое название идеи (до 50 символов)",\n'
        f'  "rubric": "СТРОГО одна из: {rubric_line}",\n'
        '  "format": ["Short video"]\n'
        "}\n\n"
        'Формат: ВСЕГДА ставь ["Short video"] — мы делаем только короткие '
        "вертикальные ролики.\n\n"
        "Правила выбора рубрики:\n"
        f"{rules}\n\n"
        f"Контекст: {context}"
    )

# Formats — match the «Формат» multi_select options in Maksim's Notion DB.
FORMATS = ["Short video", "Long post", "Reel"]

# --- Storage for pending data (persisted to file) ---
# F1 fix (26 May 2026): pending и save_pending вынесены в bot_state.py чтобы
# carousel/handlers.py не делал `import bot` (вызывало второй import bot.py
# когда сервис запущен как `python bot.py` под __main__).
# Имя `_save_pending` сохранено для обратной совместимости с ~50 call sites.
from bot_state import pending, save_pending as _save_pending, PENDING_FILE  # noqa: E402, F401


# --- Publication calendar tracking ---
CALENDAR_FILE = Path(__file__).parent / "pub_calendar.json"

# Platform short codes for calendar display.
# Maps Maksim's Notion «Опубликовано на» / «Площадки» values → short code
# used in pub_calendar.json. MUST match the multi_select options in his
# Content DB (data_source 3586889c-…-8b2f).
PLATFORM_CODES = {
    "TG канал": "TG",
    "Instagram": "IG",
    "YouTube Shorts": "YT",
    "VK": "VK",
    "TikTok": "TT",
}

# Canonical platform list for UI (calendar header, /pub picker).
# Order = visual order, top-to-bottom. Maksim's platforms only.
# To add/remove a platform here: update this list + PLATFORM_CODES above.
PLATFORM_DISPLAY: list[tuple[str, str]] = [
    ("TG",  "Telegram"),
    ("IG",  "Instagram"),
    ("YT",  "YouTube"),
    ("VK",  "VK"),
    ("TT",  "TikTok"),
]
PLATFORM_ORDER: list[str] = [code for code, _ in PLATFORM_DISPLAY]


def _load_calendar() -> dict:
    """Load publication calendar. Format: {"2026-03-31": {"IG": true, "TG": true}}"""
    if CALENDAR_FILE.exists():
        try:
            return json.loads(CALENDAR_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_calendar(data: dict):
    CALENDAR_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _record_publication(platforms: list[str]):
    """Record today's publication for given platforms."""
    today = datetime.now().strftime("%Y-%m-%d")
    cal = _load_calendar()
    day_data = cal.get(today, {})

    for platform in platforms:
        code = PLATFORM_CODES.get(platform)
        if code:
            day_data[code] = day_data.get(code, 0) + 1

    cal[today] = day_data
    _save_calendar(cal)
    logger.info(f"Публикация записана: {today} → {day_data}")


def _calc_streak(cal: dict) -> int:
    """Count consecutive days (back from today) with at least one publication.

    A day counts if any platform in pub_calendar.json has count > 0 for that
    date. First empty day (including today if empty) breaks the streak.
    Returns 0 if today is empty.
    """
    today = datetime.now().date()
    streak = 0
    for i in range(0, 365):  # hard cap — no one cares about streaks >1 year
        d = today - timedelta(days=i)
        key = d.strftime("%Y-%m-%d")
        day_data = cal.get(key, {})
        total = sum(v for v in day_data.values() if isinstance(v, int))
        if total > 0:
            streak += 1
        else:
            break
    return streak


_RU_WEEKDAYS = {0: "пн", 1: "вт", 2: "ср", 3: "чт", 4: "пт", 5: "сб", 6: "вс"}
_RU_MONTHS_SHORT = {
    1: "янв", 2: "фев", 3: "мар", 4: "апр", 5: "мая", 6: "июн",
    7: "июл", 8: "авг", 9: "сен", 10: "окт", 11: "ноя", 12: "дек",
}
_RU_MONTHS_FULL = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель", 5: "Май", 6: "Июнь",
    7: "Июль", 8: "Август", 9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
}


def _format_calendar(days: int = 7) -> str:
    """Render publication calendar as a three-block text:

    1. Header — month + streak + month total.
    2. Platforms — for each code in PLATFORM_ORDER: count for current month.
    3. Last N days — one line per day with a bar + count + platform list.

    Wrapped in <pre> by the caller so Telegram renders it monospace; bars
    keep visual alignment across rows.
    """
    cal = _load_calendar()
    today = datetime.now()
    today_date = today.date()

    # --- Block 1: header
    streak = _calc_streak(cal)
    month_prefix = today.strftime("%Y-%m")  # "2026-04"
    month_total = 0
    for key, day in cal.items():
        if key.startswith(month_prefix):
            month_total += sum(v for v in day.values() if isinstance(v, int))
    month_name = _RU_MONTHS_FULL.get(today.month, "")
    header_lines = [
        f"🗓 {month_name} {today.year}          🔥 стрик: {streak}",
        f"   Всего за месяц: {month_total}",
    ]

    # --- Block 2: platforms for this month
    platform_totals: dict[str, int] = {code: 0 for code in PLATFORM_ORDER}
    for key, day in cal.items():
        if not key.startswith(month_prefix):
            continue
        for code, count in day.items():
            if code in platform_totals and isinstance(count, int):
                platform_totals[code] += count

    # Right-align the count so numbers form a column.
    max_code_len = max(len(c) for c in PLATFORM_ORDER)
    platform_lines = [f"📊 Платформы ({_RU_MONTHS_SHORT.get(today.month, '')})"]
    for code, label in PLATFORM_DISPLAY:
        pad = " " * (max_code_len - len(code))
        count = platform_totals[code]
        # Dim label for zero-count platforms (still show the row for discipline).
        platform_lines.append(f"   {code}{pad}  {count:>3}   {label}")

    # --- Block 3: last N days
    days_lines = [f"📋 Последние {days} дней"]
    MAX_BAR = 8  # visual cap — beyond this just show the number

    for i in range(0, days):
        d = today_date - timedelta(days=i)
        key = d.strftime("%Y-%m-%d")
        day_data = cal.get(key, {})
        day_total = sum(v for v in day_data.values() if isinstance(v, int))

        date_label = f"{d.day:>2} {_RU_MONTHS_SHORT.get(d.month, '')} ({_RU_WEEKDAYS[d.weekday()]})"

        if day_total == 0:
            # Empty day — dash bar of the same width, so "пусто" lines up
            # visually with the count column on filled rows.
            empty_bar = "─" * MAX_BAR
            days_lines.append(f"   {date_label}   {empty_bar}      пусто")
            continue

        # Bar: one block per publication, capped.
        bar_len = min(day_total, MAX_BAR)
        bar = "█" * bar_len + " " * (MAX_BAR - bar_len)

        # Platform list: order by PLATFORM_ORDER, show code with ×N if >1.
        parts: list[str] = []
        for code in PLATFORM_ORDER:
            count = day_data.get(code, 0)
            if not count:
                continue
            if count == 1:
                parts.append(code)
            else:
                parts.append(f"{code}×{count}")
        platforms_str = ", ".join(parts) if parts else "?"

        days_lines.append(
            f"   {date_label}   {bar} {day_total:>2}   {platforms_str}"
        )

    return (
        "\n".join(header_lines)
        + "\n\n"
        + "\n".join(platform_lines)
        + "\n\n"
        + "\n".join(days_lines)
    )


# --- Social media tracking ---
STATS_FILE = Path(__file__).parent / "stats_history.json"

SOCIAL_CHANNELS = {
    "instagram": {"name": "Instagram", "url": "https://www.instagram.com/panferov.ai", "auto": False},
    "telegram": {"name": "Telegram", "url": "https://t.me/artempanferov_ai", "auto": True, "chat_id": "@artempanferov_ai"},
    "youtube": {"name": "YouTube", "url": "https://www.youtube.com/channel/UC2-KuNKH7GXpwAnfUmz2neQ", "auto": False},
    "tiktok": {"name": "TikTok", "url": "https://www.tiktok.com/@panferov.ai", "auto": False},
    "vk": {"name": "ВКонтакте", "url": "https://vk.ru/pantem", "auto": False},
    "max": {"name": "Max", "url": "https://max.ru/join/SVyHigPXr1xtQnrBsRM5su-nnrXiXgfzi2V-y_4bbnI", "auto": False},
}

# Order for input and display
SOCIAL_ORDER = ["instagram", "telegram", "youtube", "tiktok", "vk", "max"]


def _load_stats() -> list[dict]:
    if STATS_FILE.exists():
        try:
            return json.loads(STATS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_stats(data: list):
    STATS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_latest_stats() -> dict | None:
    history = _load_stats()
    return history[-1] if history else None


NOTION_STATS_DB = os.getenv("NOTION_STATS_DB")


def _save_stats_to_notion(snapshot: dict, week_num: int = None):
    """Save a weekly stats snapshot to Notion database."""
    if not NOTION_STATS_DB:
        logger.warning("NOTION_STATS_DB not set, skipping Notion stats save")
        return None
    try:
        date_str = snapshot.get("date", datetime.now().strftime("%Y-%m-%d"))
        if week_num is None:
            try:
                start = datetime.strptime(EXPERIMENT_START, "%Y-%m-%d")
                current = datetime.strptime(date_str, "%Y-%m-%d")
                week_num = max(1, ((current - start).days // 7) + 1)
            except Exception:
                week_num = 1

        # Extract subscriber counts
        ig = snapshot.get("instagram", {}).get("subscribers", 0)
        tg = snapshot.get("telegram", {}).get("subscribers", 0)
        yt = snapshot.get("youtube", {}).get("subscribers", 0)
        tt = snapshot.get("tiktok", {}).get("subscribers", 0)
        vk = snapshot.get("vk", {}).get("subscribers", 0)
        mx = snapshot.get("max", {}).get("subscribers", 0)

        page = notion.pages.create(
            parent={"database_id": NOTION_STATS_DB},
            properties={
                "Неделя": {"title": [{"text": {"content": f"Неделя {week_num}"}}]},
                "Дата": {"date": {"start": date_str}},
                "Instagram": {"number": ig},
                "Telegram": {"number": tg},
                "YouTube": {"number": yt},
                "TikTok": {"number": tt},
                "VK": {"number": vk},
                "Max": {"number": mx},
            },
        )
        logger.info(f"Stats saved to Notion: week {week_num}, total={ig+tg+yt+tt+vk+mx}")
        return page["url"]
    except Exception as e:
        logger.error(f"Failed to save stats to Notion: {e}")
        return None


async def _fetch_telegram_subscribers(bot) -> int | None:
    """Auto-fetch Telegram channel subscriber count.

    Reads channel from TELEGRAM_CHANNEL_ID env var (set per-bot).
    Returns None if env var not set — disables stats card gracefully.
    """
    channel = os.getenv("TELEGRAM_CHANNEL_ID")
    if not channel:
        return None
    try:
        count = await bot.get_chat_member_count(channel)
        return count
    except Exception as e:
        logger.warning(f"Не удалось получить подписчиков Telegram: {e}")
        return None


def _fetch_instagram_followers() -> int | None:
    """Auto-fetch Instagram follower count via Graph API."""
    try:
        from instagram_dm import _get_instagram_token
        creds = _get_instagram_token()
        if not creds:
            return None
        access_token, ig_user_id = creds
        resp = requests.get(
            f"https://graph.facebook.com/v21.0/{ig_user_id}",
            params={"fields": "followers_count", "access_token": access_token},
            timeout=10,
        )
        if resp.status_code == 200:
            count = resp.json().get("followers_count")
            logger.info(f"Instagram подписчики (авто): {count}")
            return count
    except Exception as e:
        logger.warning(f"Не удалось получить подписчиков Instagram: {e}")
    return None


def _fetch_youtube_subscribers() -> int | None:
    """Auto-fetch YouTube subscriber count via Data API."""
    try:
        from crosspost import _get_youtube_access_token
        access_token = _get_youtube_access_token()
        if not access_token:
            return None
        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={"part": "statistics", "mine": "true"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            if items:
                count = int(items[0]["statistics"].get("subscriberCount", 0))
                logger.info(f"YouTube подписчики (авто): {count}")
                return count
    except Exception as e:
        logger.warning(f"Не удалось получить подписчиков YouTube: {e}")
    return None


STATS_GOAL = int(os.getenv("STATS_GOAL", "0"))  # 0 disables the stats experiment widget
EXPERIMENT_START = os.getenv("EXPERIMENT_START", "2026-05-06")  # Week 1 base date


def generate_dashboard_image(history: list[dict]) -> str:
    """Generate a visual dashboard image showing subscriber growth. Returns path."""
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1080, 1350
    img = Image.new("RGB", (W, H), (18, 18, 24))
    draw = ImageDraw.Draw(img)

    # Load fonts
    font_dir = ASSETS_DIR / "fonts"
    try:
        font_title = ImageFont.truetype(str(font_dir / "Montserrat-Bold.ttf"), 48)
        font_large = ImageFont.truetype(str(font_dir / "Montserrat-Bold.ttf"), 64)
        font_medium = ImageFont.truetype(str(font_dir / "Montserrat-Bold.ttf"), 36)
        font_small = ImageFont.truetype(str(font_dir / "Montserrat-Medium.ttf"), 28)
        font_number = ImageFont.truetype(str(font_dir / "Montserrat-Bold.ttf"), 52)
    except Exception:
        font_title = font_large = font_medium = font_small = font_number = ImageFont.load_default()

    latest = history[-1] if history else {}
    date_str = latest.get("date", datetime.now().strftime("%Y-%m-%d"))

    # Calculate week number from experiment start date
    try:
        start = datetime.strptime(EXPERIMENT_START, "%Y-%m-%d")
        current = datetime.strptime(date_str, "%Y-%m-%d")
        week_num = max(1, ((current - start).days // 7) + 1)
    except Exception:
        week_num = len(history)

    # Platform colors
    platform_colors = {
        "instagram": (225, 48, 108),
        "youtube": (255, 0, 0),
        "tiktok": (0, 242, 234),
        "telegram": (36, 161, 222),
        "vk": (0, 119, 255),
        "max": (255, 140, 0),
    }
    platform_icons = {
        "instagram": "IG",
        "youtube": "YT",
        "tiktok": "TT",
        "telegram": "TG",
        "vk": "VK",
        "max": "MX",
    }

    # --- Header ---
    draw.text((40, 40), "ЭКСПЕРИМЕНТ", fill=(255, 255, 255), font=font_title)
    draw.text((40, 100), f"НЕДЕЛЯ {week_num}", fill=(120, 120, 140), font=font_medium)
    draw.text((W - 40, 100), date_str, fill=(120, 120, 140), font=font_small, anchor="ra")

    # --- Total subscribers ---
    total_subs = sum(latest.get(k, {}).get("subscribers", 0) for k in SOCIAL_ORDER if k in platform_colors)
    y = 180
    draw.text((40, y), "ВСЕГО ПОДПИСЧИКОВ", fill=(120, 120, 140), font=font_small)
    y += 45
    draw.text((40, y), f"{total_subs:,}".replace(",", " "), fill=(255, 255, 255), font=font_large)

    # Goal progress bar
    y += 85
    bar_x, bar_w, bar_h = 40, W - 80, 24
    progress = min(total_subs / STATS_GOAL, 1.0) if STATS_GOAL > 0 else 0
    # Background
    draw.rounded_rectangle([bar_x, y, bar_x + bar_w, y + bar_h], radius=12, fill=(40, 40, 50))
    # Fill
    if progress > 0:
        fill_w = max(int(bar_w * progress), bar_h)
        draw.rounded_rectangle([bar_x, y, bar_x + fill_w, y + bar_h], radius=12, fill=(76, 175, 80))
    # Label
    pct = int(progress * 100)
    draw.text((bar_x + bar_w + 10, y - 5), f"{pct}%", fill=(76, 175, 80), font=font_small)
    y += 35
    draw.text((40, y), f"Цель: {STATS_GOAL:,}".replace(",", " "), fill=(80, 80, 100), font=font_small)

    # Delta from previous
    if len(history) >= 2:
        prev = history[-2]
        prev_total = sum(prev.get(k, {}).get("subscribers", 0) for k in SOCIAL_ORDER if k in platform_colors)
        delta = total_subs - prev_total
        sign = "+" if delta >= 0 else ""
        delta_color = (76, 175, 80) if delta >= 0 else (244, 67, 54)
        draw.text((W - 40, y), f"{sign}{delta} за неделю", fill=delta_color, font=font_small, anchor="ra")

    # --- Platform cards ---
    y += 70
    card_h = 100
    card_gap = 12

    platforms_to_show = ["instagram", "youtube", "tiktok", "telegram", "vk", "max"]
    for key in platforms_to_show:
        if y + card_h > H - 100:
            break
        info = SOCIAL_CHANNELS.get(key, {})
        color = platform_colors.get(key, (100, 100, 100))
        icon = platform_icons.get(key, "?")
        subs = latest.get(key, {}).get("subscribers", 0)

        # Card background
        draw.rounded_rectangle([40, y, W - 40, y + card_h], radius=16, fill=(28, 28, 36))

        # Color accent bar
        draw.rounded_rectangle([40, y, 48, y + card_h], radius=4, fill=color)

        # Platform icon
        draw.text((70, y + 20), icon, fill=color, font=font_medium)

        # Platform name
        draw.text((140, y + 22), info.get("name", key), fill=(200, 200, 210), font=font_medium)

        # Subscriber count (right side)
        draw.text((W - 80, y + 25), f"{subs:,}".replace(",", " "), fill=(255, 255, 255), font=font_number, anchor="ra")

        # Delta
        if len(history) >= 2:
            prev_subs = history[-2].get(key, {}).get("subscribers", 0)
            d = subs - prev_subs
            if d != 0:
                sign = "+" if d > 0 else ""
                d_color = (76, 175, 80) if d > 0 else (244, 67, 54)
                draw.text((140, y + 60), f"{sign}{d}", fill=d_color, font=font_small)

        y += card_h + card_gap

    # --- Footer ---
    y = H - 60
    watermark = os.getenv("COVER_WATERMARK", "")
    if watermark:
        draw.text((W // 2, y), watermark, fill=(60, 60, 80), font=font_small, anchor="ma")

    # Save
    output_path = str(ASSETS_DIR / "dashboard.jpg")
    img.save(output_path, "JPEG", quality=90)
    return output_path


def _format_stats_report(snapshot: dict) -> str:
    """Format a single snapshot as readable text."""
    date = snapshot.get("date", "?")
    lines = [f"📊 Статистика на {date}\n"]

    for key in SOCIAL_ORDER:
        info = SOCIAL_CHANNELS[key]
        data = snapshot.get(key, {})
        subs = data.get("subscribers", 0)
        if subs:
            lines.append(f"  {info['name']}: {subs} подп.")

    total_subs = sum(snapshot.get(k, {}).get("subscribers", 0) for k in SOCIAL_ORDER)
    lines.append(f"\n📈 Всего: {total_subs} подписчиков")

    return "\n".join(lines)


def _format_comparison(prev: dict, curr: dict) -> str:
    """Format comparison between two snapshots."""
    prev_date = prev.get("date", "?")
    curr_date = curr.get("date", "?")
    days = 0
    try:
        d1 = datetime.strptime(prev_date, "%Y-%m-%d")
        d2 = datetime.strptime(curr_date, "%Y-%m-%d")
        days = (d2 - d1).days
    except Exception:
        pass

    period = f"{days} дн." if days > 0 else ""
    lines = [f"📊 Отчёт: {prev_date} → {curr_date} ({period})\n"]

    total_new_subs = 0

    for key in SOCIAL_ORDER:
        info = SOCIAL_CHANNELS[key]
        p = prev.get(key, {})
        c = curr.get(key, {})

        p_subs = p.get("subscribers", 0)
        c_subs = c.get("subscribers", 0)

        if p_subs == 0 and c_subs == 0:
            continue

        diff_subs = c_subs - p_subs
        sign_s = f"+{diff_subs}" if diff_subs >= 0 else str(diff_subs)

        lines.append(f"  {info['name']}: {p_subs} → {c_subs} ({sign_s})")
        total_new_subs += diff_subs

    sign_ts = f"+{total_new_subs}" if total_new_subs >= 0 else str(total_new_subs)
    lines.append(f"\n📈 Итого: {sign_ts} подписчиков за период")

    return "\n".join(lines)


# --- Cover generation ---
def generate_cover(cover_text: str, output_path: str, avatar_override: str = None) -> str:
    """Generate Instagram Reels cover matching nanoBanana style:
    - Photo background with gradient darkening at bottom
    - White semi-transparent frosted pill with rounded corners
    - Montserrat Bold font, large, modern
    """
    WIDTH, HEIGHT = 1440, 2560  # High resolution for sharp output

    # Load avatar photo (override or random)
    avatar_path = avatar_override or _pick_random_avatar()
    if avatar_path:
        avatar = Image.open(avatar_path).convert("RGBA")
        av_w, av_h = avatar.size
        scale = max(WIDTH / av_w, HEIGHT / av_h)
        new_w, new_h = int(av_w * scale), int(av_h * scale)
        avatar = avatar.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - WIDTH) // 2
        top = (new_h - HEIGHT) // 2
        avatar = avatar.crop((left, top, left + WIDTH, top + HEIGHT))
        img = avatar
    else:
        img = Image.new("RGBA", (WIDTH, HEIGHT), (40, 40, 40, 255))

    # --- Gradient darkening on bottom half (fast method) ---
    gradient = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    grad_draw = ImageDraw.Draw(gradient)
    start_y = int(HEIGHT * 0.45)
    for y in range(start_y, HEIGHT):
        alpha = int(100 * ((y - start_y) / (HEIGHT - start_y)))
        grad_draw.line([(0, y), (WIDTH, y)], fill=(0, 0, 0, alpha))
    img = Image.alpha_composite(img, gradient)

    # --- Load Montserrat SemiBold font ---
    font_paths = [
        str(ASSETS_DIR / "fonts" / "Montserrat-SemiBold.ttf"),
        str(ASSETS_DIR / "fonts" / "Montserrat-Bold.ttf"),
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    font_file = next((fp for fp in font_paths if os.path.exists(fp)), None)

    # --- Adaptive font size based on text length ---
    char_count = len(cover_text)
    word_count = len(cover_text.split())

    if char_count <= 15 and word_count <= 3:
        # Short text: "AI без инфошума", "Промпты мертвы"
        font_size = 175
        wrap_width = 14
    elif char_count <= 25 and word_count <= 5:
        # Medium text: "Apple платит $1 млрд за ИИ"
        font_size = 140
        wrap_width = 16
    else:
        # Long text: "Как мы делали видео для X5 Group"
        font_size = 110
        wrap_width = 20

    font = ImageFont.truetype(font_file, font_size) if font_file else ImageFont.load_default()

    # --- Wrap and measure text ---
    max_text_width = WIDTH - 200  # margins each side
    max_pill_height = int(HEIGHT * 0.3)  # pill shouldn't be taller than 30% of image
    lines = textwrap.wrap(cover_text, width=wrap_width)
    text_block = "\n".join(lines)

    temp_draw = ImageDraw.Draw(img)
    bbox = temp_draw.multiline_textbbox((0, 0), text_block, font=font, align="center")
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    # Shrink font if text is too wide or too tall
    while (text_w > max_text_width or text_h > max_pill_height) and font_size > 70:
        font_size -= 5
        font = ImageFont.truetype(font_file, font_size) if font_file else ImageFont.load_default()
        lines = textwrap.wrap(cover_text, width=wrap_width)
        text_block = "\n".join(lines)
        bbox = temp_draw.multiline_textbbox((0, 0), text_block, font=font, align="center")
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

    logger.debug(f"Обложка: '{cover_text}' → {word_count} слов, {char_count} символов, шрифт {font_size}px")

    # --- Adaptive color pill ---
    pad_x, pad_y = 75, 55
    pill_w = text_w + pad_x * 2
    pill_h = text_h + pad_y * 2
    pill_x = (WIDTH - pill_w) // 2
    pill_y = int(HEIGHT * 0.72) - pill_h // 2  # Lower — chest/stomach level

    # Sample average color from the area behind the pill
    sample_region = img.crop((pill_x, pill_y, pill_x + pill_w, pill_y + pill_h)).convert("RGB")
    pixels = list(sample_region.getdata())
    avg_r = sum(p[0] for p in pixels) // len(pixels)
    avg_g = sum(p[1] for p in pixels) // len(pixels)
    avg_b = sum(p[2] for p in pixels) // len(pixels)

    # Lighten the average color for the pill (blend toward white)
    blend = 0.6  # 0 = pure average color, 1 = pure white
    pill_r = int(avg_r + (255 - avg_r) * blend)
    pill_g = int(avg_g + (255 - avg_g) * blend)
    pill_b = int(avg_b + (255 - avg_b) * blend)

    pill_overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    pill_draw = ImageDraw.Draw(pill_overlay)
    pill_draw.rounded_rectangle(
        [pill_x, pill_y, pill_x + pill_w, pill_y + pill_h],
        radius=42,
        fill=(pill_r, pill_g, pill_b, 210),
    )
    img = Image.alpha_composite(img, pill_overlay)

    # --- Draw text centered — compensate for font descender ---
    draw = ImageDraw.Draw(img)
    text_x = pill_x + (pill_w - text_w) // 2
    # Shift text up to visually center (compensate for font descender/ascender)
    text_y = pill_y + (pill_h - text_h) // 2 - int(font_size * 0.15)
    draw.multiline_text(
        (text_x, text_y), text_block, fill=(25, 25, 25), font=font, align="center"
    )

    img = img.convert("RGB")
    img.save(output_path, "JPEG", quality=97, subsampling=0)
    return output_path


# Пути media/covers — в /srv/ (стандартное место для раздаваемых данных:
# nginx www-data туда имеет доступ; /home/<user>/ закрыта правами).
# Папки принадлежат maksim-bot (бот пишет), chmod 755 (nginx читает).
# Раздаются nginx по поддомену maksim-bot.panferov-ai.ru (vhost + SSL).
COVERS_DIR = Path("/srv/bot-covers-maksim")
COVERS_BASE_URL = "https://maksim-bot.panferov-ai.ru/covers"
MEDIA_DIR = Path("/srv/bot-media-maksim")
MEDIA_BASE_URL = "https://maksim-bot.panferov-ai.ru/media"


def save_cover_permanent(source_path: str, card_title: str = "") -> str:
    """Compress cover image and save to permanent storage. Returns public URL."""
    import hashlib

    COVERS_DIR.mkdir(parents=True, exist_ok=True)

    # Generate unique filename
    ts = str(time.time()).encode()
    name_hash = hashlib.md5(ts + card_title.encode()).hexdigest()[:12]
    filename = f"cover_{name_hash}.jpg"

    # Compress: resize to 800px wide, JPEG quality 75
    img = Image.open(source_path)
    w, h = img.size
    if w > 800:
        ratio = 800 / w
        img = img.resize((800, int(h * ratio)), Image.LANCZOS)
    img = img.convert("RGB")
    dest = COVERS_DIR / filename
    img.save(str(dest), "JPEG", quality=75)
    dest.chmod(0o644)

    size_kb = dest.stat().st_size / 1024
    logger.info(f"Обложка сохранена: {filename} ({size_kb:.0f} KB)")
    return f"{COVERS_BASE_URL}/{filename}"


def save_media_permanent(source_path: str, prefix: str = "file") -> str:
    """Save a media file (audio/video) to permanent storage. Returns public URL."""
    import hashlib
    import shutil

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(source_path).suffix or ".mp3"
    ts = str(time.time()).encode()
    name_hash = hashlib.md5(ts + source_path.encode()).hexdigest()[:12]
    filename = f"{prefix}_{name_hash}{ext}"
    dest = MEDIA_DIR / filename
    shutil.copy2(source_path, str(dest))
    dest.chmod(0o644)
    logger.info(f"Медиа сохранено: {filename}")
    return f"{MEDIA_BASE_URL}/{filename}"


# --- Notion helpers ---
def create_notion_card(card_data: dict, script_text: str, cover_url: str = None,
                       source_urls: list = None, youtube_urls: list = None) -> tuple[str, str]:
    """Create a Notion page with card properties and script in the body.
    Returns (page_url, page_id)."""

    # CTA priority: explicit `card_data["cta"]` > last non-empty line of
    # script_text > "". Fixed 9 May 2026 — previously `current_cta` was
    # ALWAYS overwritten by the last line of script_text, which meant the
    # selfie pipeline (where script_text == full transcript) saved the
    # transcript's last sentence into the Notion "Призыв" property instead
    # of a real CTA. Selfie pipeline now passes a brand-appropriate short
    # CTA via card_data["cta"] (e.g. "Подписывайся на @yumsunov_realbiz").
    # Notion rich_text hard limit is 2000 chars — clamp to 1900 just in case.
    explicit_cta = (card_data.get("cta") or "").strip()
    if explicit_cta:
        current_cta = explicit_cta
    else:
        non_empty = [ln for ln in script_text.strip().split("\n") if ln.strip()]
        current_cta = non_empty[-1] if non_empty else ""
    current_cta = current_cta[:1900]

    # Build page body blocks
    children = []

    # Add cover image if available
    if cover_url:
        children.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"text": {"content": "Обложка"}}]
            },
        })
        children.append({
            "object": "block",
            "type": "image",
            "image": {
                "type": "external",
                "external": {"url": cover_url}
            },
        })

    children.append({
        "object": "block",
        "type": "heading_2",
        "heading_2": {
            "rich_text": [{"text": {"content": "Сценарий"}}]
        },
    })
    # Codex P1 fix (8 May 2026 review #2): script_text used to be a single
    # paragraph rich_text. Notion's hard limit is 2000 chars per text.content
    # block — selfie transcripts > 2000 chars (typical for 60-sec videos)
    # would crash card creation. Now we chunk via shared helper that splits
    # on paragraph/sentence boundaries.
    children.extend(_notion_paragraph_blocks(script_text))

    # Add source links block
    all_links = []
    if source_urls:
        all_links.extend([f"📎 {u}" for u in source_urls])
    if youtube_urls:
        all_links.extend([f"🎬 {u}" for u in youtube_urls])
    if all_links:
        children.extend([
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"text": {"content": "Источники"}}]
                },
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"text": {"content": "\n".join(all_links)}}]
                },
            },
        ])

    # Brand: prefer explicit override in card_data, else fall back to the
    # currently-active brand (context from a prior card OR global /brand).
    # Explicit takes priority so launch-monitor approvals and explicit
    # {"brand": "..."} kwargs win over session-level state.
    brand_name = (card_data.get("brand") or "").strip().lower() or _get_active_brand_name()
    if brand_name not in BRANDS:
        brand_name = "default"

    logger.info(
        f"Создаю карточку в Notion: {card_data.get('title', '?')} "
        f"[brand={brand_name}]"
    )
    # Pass `brand_name` to schema helpers explicitly. Without this, helpers
    # would read the active brand from context — which can lag behind
    # `brand_name` when create_notion_card is called with an explicit
    # {"brand": "..."} override (e.g. launch-monitor approval flow).
    # Result of mismatch: card with Brand=maksim but Notion schema written
    # in default's shape — `status` instead of `select`, "Рубрика " with
    # trailing space instead of clean "Рубрика", Artem's platforms instead
    # of Maksim's. Fix per Codex review 7 May 2026.
    # «Дата» = день создания карточки (фиксирует когда идея/материал
    # попали в work-queue). 9 May 2026 fix — раньше всегда оставалось пустым,
    # неудобно сортировать board. Перезаписать вручную в Notion можно всегда.
    today_iso = datetime.now().strftime("%Y-%m-%d")
    create_kwargs = dict(
        parent={"database_id": NOTION_DB},
        properties={
            "Name": {"title": [{"text": {"content": card_data["title"]}}]},
            "Status": _status_property_value("Идеи | старт", brand_name),
            _rubric_property_name(brand_name): {"select": {"name": card_data.get("rubric", "Свободный формат")}},
            "Площадки": {
                "multi_select": [
                    {"name": p}
                    for p in card_data.get("platforms", _default_platforms(brand_name))
                ]
            },
            "Формат": {
                "multi_select": [{"name": f} for f in card_data.get("format", ["Short video"])]
            },
            "Призыв": {
                "rich_text": [
                    {"text": {"content": current_cta}}
                ]
            },
            "Бренд": {"select": {"name": brand_name}},
            "Дата": {"date": {"start": today_iso}},
        },
        children=children,
    )
    # Page-level cover banner (shown in the /cards board/gallery view), not just
    # the in-body «Обложка» image block above.
    if cover_url:
        create_kwargs["cover"] = {"type": "external", "external": {"url": cover_url}}
    page = notion.pages.create(**create_kwargs)
    return page["url"], page["id"]


def _build_author_callout_blocks() -> list[dict]:
    """Build "Об авторе" Notion callout blocks for guide pages.

    Reads social handles from env so the same bot.py works for any tenant
    (Artem / Maksim / future clients) without hard-coded literals.

    Required env: AUTHOR_NAME, AUTHOR_DESCRIPTION
    Optional env (any of these can be empty — link is omitted): AUTHOR_INSTAGRAM_URL,
    AUTHOR_TELEGRAM_URL, AUTHOR_YOUTUBE_URL, AUTHOR_YOUTUBE_NAME, AUTHOR_TIKTOK_URL,
    AUTHOR_VK_URL, AUTHOR_VK_NAME, AUTHOR_CTA_LINE, AUTHOR_WEBSITE_URL,
    AUTHOR_WEBSITE_NAME.
    """
    name = os.getenv("AUTHOR_NAME", "Автор")
    description = os.getenv("AUTHOR_DESCRIPTION", "")
    rich = [
        {"text": {"content": "Об авторе"}, "annotations": {"bold": True}},
        {"text": {"content": f"\n\n{name}"}},
    ]
    if description:
        rich.append({"text": {"content": f" — {description}"}})
    rich.append({"text": {"content": "\n\n"}})

    socials = [
        ("📸 Instagram", os.getenv("AUTHOR_INSTAGRAM_URL"), os.getenv("AUTHOR_INSTAGRAM_HANDLE", "Instagram")),
        ("✈️ Telegram",  os.getenv("AUTHOR_TELEGRAM_URL"),  os.getenv("AUTHOR_TELEGRAM_HANDLE", "Telegram")),
        ("▶️ YouTube",   os.getenv("AUTHOR_YOUTUBE_URL"),   os.getenv("AUTHOR_YOUTUBE_NAME", "YouTube")),
        ("🎵 TikTok",    os.getenv("AUTHOR_TIKTOK_URL"),    os.getenv("AUTHOR_TIKTOK_HANDLE", "TikTok")),
        ("📺 VK",        os.getenv("AUTHOR_VK_URL"),        os.getenv("AUTHOR_VK_NAME", "VK")),
    ]
    for label, url, display in socials:
        if not url:
            continue
        rich.append({"text": {"content": label}, "annotations": {"bold": True}})
        rich.append({"text": {"content": " — "}})
        rich.append({"text": {"content": display, "link": {"url": url}}})
        rich.append({"text": {"content": "\n"}})

    cta_line = os.getenv("AUTHOR_CTA_LINE")
    website_url = os.getenv("AUTHOR_WEBSITE_URL")
    website_name = os.getenv("AUTHOR_WEBSITE_NAME", website_url or "")
    if cta_line:
        rich.append({"text": {"content": f"\n{cta_line}\n"}})
    if website_url:
        rich.append({"text": {"content": website_name, "link": {"url": website_url}}})

    return [
        {"object": "block", "type": "divider", "divider": {}},
        {
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": rich,
                "icon": {"emoji": "👤"},
                "color": "gray_background",
            },
        },
    ]


def create_guide_page(script_text: str, title: str, feedback: str = None) -> str:
    """Generate a guide page in the public Notion database using Claude.
    Returns the public URL of the guide page."""
    if not NOTION_GUIDES_DB:
        raise ValueError("NOTION_GUIDES_DB_ID not configured")

    system_intro = "Ты — эксперт-аналитик и контент-редактор. По сценарию ролика создай глубокий, ценный гайд для подписчиков."
    if feedback:
        system_intro = "Ты — эксперт-аналитик и контент-редактор. Перепиши гайд с учётом правок автора, сохраняя глубину и конкретику."

    # Ask Claude to generate guide content as structured blocks
    response = claude.messages.create(
        model="claude-opus-4-7",
        max_tokens=4000,
        system=system_intro + """

ФОРМАТ ОТВЕТА — строго JSON-массив блоков Notion. Каждый блок — объект с полями:
- type: "callout_blue", "callout_yellow", "callout_red", "heading", "numbered", "bulleted", "paragraph", "divider"
- text: текст блока (не нужен для divider)
- icon: эмодзи для callout (не нужен для остальных)
- bold_prefix: жирный текст в начале (для bulleted списков, опционально)

СТРУКТУРА ГАЙДА (15-25 блоков):
1. callout_blue с эмодзи 🎁 — "Гайд для подписчиков. Ключевое слово в комментариях: [придумай короткое слово по теме]"
2. paragraph — короткое вступление (2-3 предложения): почему эта тема важна, контекст, масштаб проблемы. Цифры, факты, тренды.
3. 3-5 секций, каждая:
   - heading — название секции
   - paragraph или numbered — РАЗВЁРНУТОЕ объяснение (не просто тезис, а ПОЧЕМУ это работает, КАК применить, КАКОЙ результат)
   - numbered — конкретные шаги с деталями (каждый шаг = 1-2 предложения, не одно слово)
   - callout_yellow с 💡 — практический лайфхак или неочевидный инсайт
4. Секция "Ключевые выводы" или "Как применить у себя" — bulleted список с bold_prefix
5. callout_red с 🇷🇺 — (только если речь про зарубежные приложения)

КРИТИЧЕСКИ ВАЖНО — ГЛУБИНА И ЦЕННОСТЬ:
- НИКОГДА не придумывай статистику, цифры, проценты и суммы от себя. Используй ТОЛЬКО факты и числа из сценария. Если тема может быстро устаревать — не опирайся на старые данные, держись фактов сценария.
- Если в сценарии есть конкретные цифры — используй их. Если нет — строй аргументацию ЛОГИЧЕСКИ, через причинно-следственные связи, а не через выдуманные данные.
- ВСТУПЛЕНИЕ и весь гайд — СТРОГО про тему ролика, а не про смежную индустрию в целом. Раскрывай конкретику сценария, не уходи в общие рассуждения об отрасли.
- Каждый тезис из ролика РАСКРОЙ ШИРЕ: добавь контекст, причины, последствия, механизмы работы
- Объясняй ПОЧЕМУ, а не только ЧТО: не просто «делай так», а разбери механизм — почему это работает и при каких условиях
- Давай ПРАКТИЧЕСКИЕ РЕКОМЕНДАЦИИ: что читателю сделать прямо сейчас, на что обратить внимание
- Тон: дружеский, экспертный, как будто объясняешь другу за кофе — но с глубиной аналитика
- НЕ повторяй сценарий слово в слово — используй его как отправную точку, дополни своей экспертизой
- Каждая секция должна давать САМОСТОЯТЕЛЬНУЮ ценность — читатель должен узнать что-то новое

Пример ХОРОШЕГО блока (глубокий, через логику, без выдуманных цифр):
{"type": "paragraph", "text": "Систему стоит выстраивать не когда стало тяжело, а когда процесс повторился третий раз. Почему именно третий? Первый раз — разовая задача, второй можно списать на совпадение, третий означает регулярность. А регулярное без системы каждый раз отъедает внимание собственника, и чем дальше — тем дороже его переключение. Поэтому правило простое: повторилось трижды — опиши шаги и передай."}

Пример ПЛОХОГО блока (поверхностный или с выдуманными цифрами):
{"type": "paragraph", "text": "Делегирование экономит до 70% времени. Это важный навык для бизнеса. Начните делегировать уже сегодня."}

Пример ответа:
[
  {"type": "callout_blue", "icon": "🎁", "text": "Гайд для подписчиков. Ключевое слово: система"},
  {"type": "paragraph", "text": "Большинство собственников берутся за процессы слишком поздно — когда уже захлёбываются в рутине. Разберём, по какому признаку понять, что задачу пора превращать в систему, и как сделать это без месяца на регламенты."},
  {"type": "heading", "text": "Когда задачу пора систематизировать"},
  {"type": "paragraph", "text": "Ориентир — не «когда тяжело», а «когда повторилось трижды». Третий повтор означает регулярность, а регулярное без системы каждый раз крадёт внимание собственника. Чем раньше зафиксируешь шаги, тем дешевле обходится передача и тем меньше задача держится только на тебе."},
  {"type": "callout_yellow", "icon": "💡", "text": "Ключевой инсайт: систематизировать нужно не самые сложные задачи, а самые частые — именно они незаметно съедают день."},
  {"type": "divider"},
  {"type": "heading", "text": "Как применить у себя"},
  {"type": "numbered", "text": "Выпиши задачи, которые повторяешь каждую неделю. Отметь те, что делаешь по одному и тому же сценарию — это первые кандидаты на регламент или передачу."},
  {"type": "bulleted", "bold_prefix": "Частые задачи", "text": " — первые кандидаты: типовые ответы клиентам, отчёты, повторяющиеся согласования"}
]""",
        messages=[
            {"role": "user", "content": f"Сценарий ролика:\n{script_text}\n\n{'Правки автора: ' + feedback + chr(10) + chr(10) if feedback else ''}Создай гайд. НЕ добавляй блок об авторе — он добавляется автоматически."}
        ],
    )

    raw = response.content[0].text.strip()
    # Extract JSON from response (handle markdown code blocks)
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    blocks_data = json.loads(raw)

    # Convert to Notion blocks
    children = []
    for b in blocks_data:
        btype = b.get("type", "")
        text = b.get("text", "")

        if btype == "divider":
            children.append({"object": "block", "type": "divider", "divider": {}})
        elif btype.startswith("callout"):
            color_map = {"callout_blue": "blue_background", "callout_yellow": "yellow_background", "callout_red": "red_background"}
            children.append({
                "object": "block", "type": "callout",
                "callout": {
                    "rich_text": [{"text": {"content": text}}],
                    "icon": {"emoji": b.get("icon", "💡")},
                    "color": color_map.get(btype, "blue_background")
                }
            })
        elif btype == "heading":
            children.append({
                "object": "block", "type": "heading_2",
                "heading_2": {"rich_text": [{"text": {"content": text}}]}
            })
        elif btype == "numbered":
            children.append({
                "object": "block", "type": "numbered_list_item",
                "numbered_list_item": {"rich_text": [{"text": {"content": text}}]}
            })
        elif btype == "bulleted":
            rich = []
            if b.get("bold_prefix"):
                rich.append({"text": {"content": b["bold_prefix"]}, "annotations": {"bold": True}})
            rich.append({"text": {"content": text}})
            children.append({
                "object": "block", "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": rich}
            })
        elif btype == "paragraph":
            annotations = {}
            if "Автор" in text or "@" in text:
                annotations = {"italic": True}
            rich = [{"text": {"content": text}}]
            if annotations:
                rich = [{"text": {"content": text}, "annotations": annotations}]
            children.append({
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": rich}
            })

    if not children:
        raise ValueError("Claude не сгенерировал блоки для гайда")

    # Add author/contact block at the end (env-driven, see _build_author_callout_blocks)
    children.extend(_build_author_callout_blocks())

    # Extract keyword from first callout for the title
    guide_title = title

    page = notion.pages.create(
        parent={"database_id": NOTION_GUIDES_DB},
        properties={
            "Name": {"title": [{"text": {"content": guide_title}}]},
        },
        children=children,
    )

    # Build public URL
    page_id_clean = page["id"].replace("-", "")
    public_url = f"https://{NOTION_GUIDES_PUBLIC_HOST}/{page_id_clean}"
    return public_url


def create_guide_page_from_raw(raw_text: str, title: str) -> str:
    """Create a guide page from the user's raw text (no LLM rewrite).

    Splits input on blank lines into paragraphs. Lines that start with
    '# ' become headings, '- ' become bullets, '1. ' become numbered list.
    Always appends the author callout at the end.
    """
    if not NOTION_GUIDES_DB:
        raise ValueError("NOTION_GUIDES_DB_ID not configured")

    children = []
    # Split into blocks by blank lines to preserve paragraph structure
    blocks = [b.strip() for b in re.split(r"\n\s*\n", raw_text.strip()) if b.strip()]
    for block in blocks:
        lines = block.splitlines()
        first = lines[0].strip()
        if first.startswith("# "):
            children.append({
                "object": "block", "type": "heading_2",
                "heading_2": {"rich_text": [{"text": {"content": first[2:].strip()[:1800]}}]},
            })
            # Remaining lines go as a paragraph
            rest = "\n".join(lines[1:]).strip()
            if rest:
                children.append({
                    "object": "block", "type": "paragraph",
                    "paragraph": {"rich_text": [{"text": {"content": rest[:1800]}}]},
                })
        elif all(ln.strip().startswith(("- ", "• ")) for ln in lines if ln.strip()):
            for ln in lines:
                t = ln.strip().lstrip("-• ").strip()
                if t:
                    children.append({
                        "object": "block", "type": "bulleted_list_item",
                        "bulleted_list_item": {"rich_text": [{"text": {"content": t[:1800]}}]},
                    })
        elif all(re.match(r"^\d+[.)]\s", ln.strip()) for ln in lines if ln.strip()):
            for ln in lines:
                t = re.sub(r"^\d+[.)]\s*", "", ln.strip())
                if t:
                    children.append({
                        "object": "block", "type": "numbered_list_item",
                        "numbered_list_item": {"rich_text": [{"text": {"content": t[:1800]}}]},
                    })
        else:
            children.append({
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [{"text": {"content": block[:1800]}}]},
            })

    if not children:
        raise ValueError("Пустой текст гайда")

    # Author/contact block (env-driven, see _build_author_callout_blocks)
    children.extend(_build_author_callout_blocks())

    page = notion.pages.create(
        parent={"database_id": NOTION_GUIDES_DB},
        properties={
            "Name": {"title": [{"text": {"content": title}}]},
        },
        children=children,
    )
    page_id_clean = page["id"].replace("-", "")
    return f"https://{NOTION_GUIDES_PUBLIC_HOST}/{page_id_clean}"


def add_guide_link_to_card(page_id: str, guide_url: str):
    """Append guide link as a block to the content card in Notion."""
    notion.blocks.children.append(
        block_id=page_id,
        children=[
            {"object": "block", "type": "divider", "divider": {}},
            {
                "object": "block", "type": "callout",
                "callout": {
                    "rich_text": [
                        {"text": {"content": "🔗 Гайд для подписчиков: "}},
                        {"text": {"content": guide_url, "link": {"url": guide_url}}}
                    ],
                    "icon": {"emoji": "📎"},
                    "color": "green_background"
                }
            }
        ],
    )
    logger.info(f"Ссылка на гайд добавлена в карточку: {page_id}")


# --- Cross-posting helpers ---
def _extract_cta_keyword(script_text: str) -> str | None:
    """Extract CTA keyword from script. Looks for patterns like:
    'Напиши "чип" в комментариях', 'напиши слово чип', etc.
    Returns the keyword or None."""
    import re
    if not script_text:
        return None
    # Get last 2 lines (CTA is usually at the end)
    lines = script_text.strip().split("\n")
    cta_area = "\n".join(lines[-2:]).lower()

    # Pattern 1: "напиши «слово»" or 'напиши "слово"'
    match = re.search(r'напиши\s+[«""\']([\w\s]+?)[»""\']', cta_area)
    if match:
        return match.group(1).strip()

    # Pattern 2: "напиши слово KEYWORD" or "напиши KEYWORD в комментариях"
    match = re.search(r'напиши\s+(?:слово\s+)?(\w+)\s+(?:в\s+комментари|в\s+коммент)', cta_area)
    if match:
        return match.group(1).strip()

    # Pattern 3: "ключевое слово: KEYWORD" or "ключевое слово — KEYWORD"
    match = re.search(r'ключевое\s+слово[:\s—–-]+(\w+)', cta_area)
    if match:
        return match.group(1).strip()

    return None


def _resolve_notion_id_by_prefix(prefix: str) -> str | None:
    """Find full Notion page ID by a partial prefix.

    Pending state sometimes only has an 8-char prefix (from a callback
    like `notion_card:3380ef6e-5ff6-8112-9`). Notion API needs the full
    UUID, so we query the database and match client-side.
    """
    if not prefix or len(prefix) < 8:
        return None
    short = prefix[:8].lower()
    try:
        # Paginate defensively — the DB may have many cards.
        cursor = None
        for _ in range(10):  # hard cap at 1000 pages
            params = {"database_id": NOTION_DB, "page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            result = notion.databases.query(**params)
            for page in result.get("results", []):
                pid = page.get("id", "").replace("-", "").lower()
                if pid.startswith(short):
                    return page["id"]
            if not result.get("has_more"):
                break
            cursor = result.get("next_cursor")
    except Exception as e:
        logger.warning(f"[notion] resolve_id_by_prefix failed for {prefix}: {e}")
    return None


def _project_dir_by_prefix(card_id_prefix: str) -> Path | None:
    """Find project directory by card_id prefix (first 8 chars).

    Used as a fallback when data dict lost its `notion_page_id` / `card_data`
    (e.g. after bot restart, or when user clicks an old inline button).
    """
    if not card_id_prefix:
        return None
    short = card_id_prefix[:8]
    if not short:
        return None
    matches = sorted(PROJECTS_DIR.glob(f"{short}_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _find_video_for_card(data: dict) -> str | None:
    """Find video file in project directory for cross-posting.
    Priority: final_video.mp4 (user upload) > avatar_final > any .mp4"""
    proj = _project_dir(data)
    # Fallback: if data lost its notion_page_id (bot restart, stale callback),
    # resolve project dir by the prefix saved in crosspost_card_id.
    if not proj or not proj.exists():
        proj = _project_dir_by_prefix(
            data.get("crosspost_card_id") or data.get("notion_edit_card") or ""
        )
        if not proj or not proj.exists():
            return None
    # Highest priority: video with music mix (user added music)
    with_music = proj / "final_video_with_music.mp4"
    if with_music.exists():
        return str(with_music)
    # Then: user-uploaded final video
    final = proj / "final_video.mp4"
    if final.exists():
        return str(final)
    # Then auto-generated or avatar videos
    for pattern in ["avatar_final*.mp4", "final*.mp4", "avatar*.mp4", "*.mp4"]:
        found = sorted(proj.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)
        if found:
            return str(found[0])
    return None


def _extract_cta_line(script_text: str) -> str | None:
    """Extract the CTA line from a script — the last sentence(s) that
    contain a call to action.

    Heuristic: CTA is the tail of the script after the last clear break.
    We walk sentences from the end and grow the CTA window while any of
    these markers are present:
      - направляющие глаголы: "пиши", "напиши", "ставь", "подпишись", "комментируй", "сохрани", "поделись"
      - слова-маркеры: "комментарии", "подписка", "лайк", "директ"
    If nothing matches, fall back to the last sentence only.
    """
    if not script_text:
        return None
    import re as _re
    # Split on sentence endings while keeping meaningful chunks.
    parts = _re.split(r'(?<=[.!?…»"])\s+', script_text.strip())
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return None

    markers = (
        "пиши", "напиши", "ставь", "поставь", "подпишись", "подписывайся",
        "подпишитесь", "комментируй", "комментариях", "комментарий",
        "сохрани", "поделись", "лайк", "директ", "репост", "включи уведом",
    )

    def has_marker(text: str) -> bool:
        low = text.lower()
        return any(m in low for m in markers)

    # Walk from the end, collect sentences with markers.
    cta_sentences: list[str] = []
    for sent in reversed(parts):
        if has_marker(sent) or not cta_sentences:
            cta_sentences.insert(0, sent)
            if cta_sentences and not has_marker(sent) and len(cta_sentences) >= 1:
                # First non-marker sentence encountered after starting — stop.
                if len(cta_sentences) > 1:
                    cta_sentences.pop(0)
                    break
        else:
            break

    cta = " ".join(cta_sentences).strip()
    # Guard: if CTA looks way too long (more than half the script), shrink to last sentence.
    if len(cta) > len(script_text) * 0.6:
        cta = parts[-1]
    return cta or None


def _trim_cta_from_video(
    video_path: str,
    trim_seconds: float = 4.0,
    script_text: str = "",
) -> str | None:
    """Trim CTA from end of video for non-Instagram crosspost.

    Uses Method A (proportional trim by CTA length in script):
      - Extract CTA line from the script (last sentence + any trailing
        call-to-action sentences).
      - Compute CTA share of total script characters.
      - trim_duration = total_video_duration * cta_chars / total_chars
      - Apply a small content-side safety buffer (-0.2s) so we never
        cut the last word of the content mid-syllable.

    Falls back to fixed `trim_seconds` if script_text is missing or
    computed CTA share looks implausible (<1s or >50% of video).

    The trimmed file is saved as *_nocta.mp4 next to the original.
    """
    from pathlib import Path as _Path
    src = _Path(video_path)
    out = src.parent / f"{src.stem}_nocta2{src.suffix}"

    # Cache: already trimmed and fresh.
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        logger.info(f"[trim_cta] Reusing cached {out.name}")
        return str(out)

    # Probe source duration.
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(src)],
            capture_output=True, text=True, timeout=10,
        )
        duration = float(probe.stdout.strip())
    except Exception as e:
        logger.error(f"[trim_cta] Cannot probe duration: {e}")
        return None

    # Method A: compute CTA duration proportionally from script.
    cta_duration: float | None = None
    cta_line = _extract_cta_line(script_text) if script_text else None
    if cta_line:
        # Normalize by char count (strip spaces to avoid whitespace bias).
        cta_chars = len(cta_line.replace(" ", ""))
        total_chars = len(script_text.replace(" ", ""))
        if total_chars > 0:
            ratio = cta_chars / total_chars
            candidate = duration * ratio
            # Sanity: 1 sec minimum, 50% of video maximum.
            if 1.0 <= candidate <= duration * 0.5:
                cta_duration = candidate
                logger.info(
                    f"[trim_cta] Method A: cta='{cta_line[:60]}...' "
                    f"({cta_chars}/{total_chars} chars = {ratio:.1%}) "
                    f"→ {candidate:.2f}s of {duration:.1f}s"
                )
            else:
                logger.warning(
                    f"[trim_cta] Method A: computed {candidate:.2f}s out of bounds — falling back"
                )

    # Fallback to fixed blind trim if Method A not usable.
    if cta_duration is None:
        cta_duration = trim_seconds
        logger.info(f"[trim_cta] Using blind trim: {trim_seconds}s")

    # Safety buffer: cut 0.2s EARLIER than the CTA boundary on the
    # content side. Losing 0.2s of a long content sentence is invisible;
    # keeping 0.2s of CTA "ставь..." is very audible.
    safety = 0.2
    new_duration = duration - cta_duration - safety
    if new_duration < 5.0:
        logger.warning(
            f"[trim_cta] Result too short ({new_duration:.1f}s) from "
            f"{duration:.1f}s − {cta_duration:.1f}s − {safety}s safety. Skipping trim."
        )
        return None

    # Re-encode with audio fade-out so we don't get an audible click at the cut.
    fade_start = max(0.0, new_duration - 0.3)
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(src),
                "-t", f"{new_duration:.3f}",
                "-c:v", "copy",
                "-af", f"afade=t=out:st={fade_start:.3f}:d=0.3",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                str(out),
            ],
            capture_output=True, text=True, timeout=120,
        )
        if out.exists() and out.stat().st_size > 0:
            logger.info(
                f"[trim_cta] Trimmed {duration:.1f}s → {new_duration:.1f}s "
                f"(cut {duration - new_duration:.1f}s from end): {out.name}"
            )
            return str(out)
    except Exception as e:
        logger.error(f"[trim_cta] ffmpeg failed: {e}")

    return None


def _find_thumbnail_for_card(data: dict) -> str | None:
    """Find thumbnail/cover image in project directory, Notion cover, or assets fallback."""
    proj = _project_dir(data)
    # Fallback: resolve by crosspost_card_id prefix if data lost its notion_page_id.
    if not proj or not proj.exists():
        proj = _project_dir_by_prefix(
            data.get("crosspost_card_id") or data.get("notion_edit_card") or ""
        )
    # 1. Check project directory
    if proj and proj.exists():
        for pattern in ["cover*.jpg", "cover*.png", "thumbnail*.jpg", "*.jpg"]:
            found = sorted(proj.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)
            if found:
                return str(found[0])
    # 2. Try to download cover from Notion card
    notion_id = data.get("notion_page_id")
    if notion_id and proj:
        try:
            page = notion.pages.retrieve(page_id=notion_id)
            cover = page.get("cover")
            if cover:
                cover_url = cover.get("external", {}).get("url") or cover.get("file", {}).get("url")
                if cover_url:
                    import requests as _req
                    resp = _req.get(cover_url, timeout=15)
                    if resp.status_code == 200:
                        cover_path = proj / "cover.jpg"
                        cover_path.write_bytes(resp.content)
                        logger.info(f"Downloaded cover from Notion: {cover_path}")
                        return str(cover_path)
        except Exception as e:
            logger.debug(f"Could not fetch Notion cover: {e}")
    # 3. No fallback — better no cover than wrong cover from another card
    return None


def _prepend_cover_to_video(video_path: str, cover_path: str, duration: float = 1.0) -> str | None:
    """
    Prepend a still image (cover) as the first N seconds of the video.
    Used as a workaround for YouTube Shorts not supporting custom thumbnails reliably —
    YouTube auto-picks first frame as thumbnail.
    Returns path to new video, or None on failure.
    """
    video = Path(video_path)
    cover = Path(cover_path)
    if not video.exists() or not cover.exists():
        return None

    # Probe video to get dimensions, fps, and audio info
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate",
             "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, timeout=15,
        )
        parts = probe.stdout.strip().split(",")
        width, height = int(parts[0]), int(parts[1])
        fps_num, fps_den = parts[2].split("/")
        fps = round(int(fps_num) / int(fps_den))
    except Exception as e:
        logger.warning(f"ffprobe failed: {e}")
        width, height, fps = 1080, 1920, 30

    output = video.parent / f"{video.stem}_with_cover.mp4"

    # Build filter: scale cover to match video, create N-sec clip, concat with video
    # Use filter_complex to:
    # 1. Scale+pad cover image to match video dimensions
    # 2. Create video from image with matching fps
    # 3. Concatenate with original video (video + audio)
    filter_complex = (
        f"[1:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1,fps={fps},trim=duration={duration}[cover];"
        f"[0:v]fps={fps},scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[main];"
        f"[cover][main]concat=n=2:v=1:a=0[outv];"
        f"anullsrc=channel_layout=stereo:sample_rate=44100,atrim=duration={duration}[silent];"
        f"[silent][0:a]concat=n=2:v=0:a=1[outa]"
    )

    try:
        result = subprocess.run(
            ["ffmpeg", "-y",
             "-i", str(video),
             "-loop", "1", "-i", str(cover),
             "-filter_complex", filter_complex,
             "-map", "[outv]", "-map", "[outa]",
             "-c:v", "libx264", "-preset", "fast", "-crf", "23",
             "-c:a", "aac", "-b:a", "128k",
             "-pix_fmt", "yuv420p", "-shortest",
             str(output)],
            capture_output=True, text=True, timeout=180,
        )
        if result.returncode != 0:
            logger.warning(f"ffmpeg prepend cover failed: {result.stderr[-500:]}")
            # Try simpler fallback without audio concat (assume video has no audio)
            result2 = subprocess.run(
                ["ffmpeg", "-y",
                 "-i", str(video),
                 "-loop", "1", "-i", str(cover),
                 "-filter_complex",
                 f"[1:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                 f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
                 f"setsar=1,fps={fps},trim=duration={duration}[cover];"
                 f"[0:v]fps={fps},scale={width}:{height}:force_original_aspect_ratio=decrease,"
                 f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[main];"
                 f"[cover][main]concat=n=2:v=1:a=0[outv]",
                 "-map", "[outv]", "-map", "0:a?",
                 "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                 "-c:a", "aac", "-b:a", "128k",
                 "-pix_fmt", "yuv420p",
                 str(output)],
                capture_output=True, text=True, timeout=180,
            )
            if result2.returncode != 0:
                logger.error(f"ffmpeg prepend cover fallback failed: {result2.stderr[-500:]}")
                return None
        if output.exists():
            logger.info(f"Cover prepended to video: {output}")
            return str(output)
    except Exception as e:
        logger.error(f"Prepend cover error: {e}")
    return None


# --- B-roll search ---
def _search_pexels_videos(query: str, count: int = 15) -> list[dict]:
    """Search Pexels for stock videos (all orientations) with duration filtering."""
    if not PEXELS_API_KEY:
        return []
    import httpx
    try:
        resp = httpx.get(
            "https://api.pexels.com/videos/search",
            params={"query": query, "per_page": count},
            headers={"Authorization": PEXELS_API_KEY},
            timeout=15,
        )
        resp.raise_for_status()
        results = []
        for v in resp.json().get("videos", []):
            duration = v.get("duration", 0)
            # Filter: 2-30 seconds (usable for Reels segments)
            if duration < 2 or duration > 30:
                continue
            # Any HD file >= 720px wide
            files = [f for f in v.get("video_files", []) if f.get("width", 0) >= 720]
            if not files:
                continue
            # Pick smallest HD file
            hd = sorted(files, key=lambda f: f.get("width", 9999))[0]
            w, h = hd.get("width", 0), hd.get("height", 0)
            user = v.get("user") or {}
            results.append({
                "url": hd["link"],
                "preview": v.get("image", ""),
                "duration": duration,
                "width": w,
                "height": h,
                "tags": ", ".join(v.get("tags", [])) if isinstance(v.get("tags"), list) else "",
                "source": "Pexels",
                "id": f"pexels_{v.get('id', '')}",
                "author_id": f"pexels_user_{user.get('id', 0)}",
                "author_name": user.get("name", ""),
            })
        return results
    except Exception as e:
        logger.warning(f"Pexels search error: {e}")
        return []


def _search_pixabay_videos(query: str, count: int = 15) -> list[dict]:
    """Search Pixabay for stock videos with portrait and duration filtering."""
    if not PIXABAY_API_KEY:
        return []
    import httpx
    try:
        resp = httpx.get(
            "https://pixabay.com/api/videos/",
            params={"key": PIXABAY_API_KEY, "q": query, "per_page": count, "safesearch": "true"},
            timeout=15,
        )
        resp.raise_for_status()
        results = []
        for v in resp.json().get("hits", []):
            duration = v.get("duration", 0)
            if duration < 2 or duration > 30:
                continue
            # Try to find a portrait or near-portrait video
            vfiles = v.get("videos", {})
            # Prefer large, then medium
            for quality in ("large", "medium", "small"):
                vf = vfiles.get(quality, {})
                if vf.get("url") and vf.get("width", 0) >= 720:
                    w, h = vf.get("width", 0), vf.get("height", 0)
                    results.append({
                        "url": vf["url"],
                        "preview": f"https://i.vimeocdn.com/video/{v.get('picture_id', '')}_640x360.jpg",
                        "duration": duration,
                        "width": w,
                        "height": h,
                        "tags": v.get("tags", ""),
                        "source": "Pixabay",
                        "id": f"pixabay_{v.get('id', '')}",
                        "author_id": f"pixabay_user_{v.get('user_id', 0)}",
                        "author_name": v.get("user", ""),
                    })
                    break
        return results
    except Exception as e:
        logger.warning(f"Pixabay search error: {e}")
        return []


# Max stock clips per (source, author) across one B-roll selection — prevents
# 4 near-duplicate clips from one videographer (Pexels Jakub Zerdzicki incident,
# 27 May 2026: 4 clips returned, 2 were visual dupes).
STOCK_MAX_PER_AUTHOR = 2


def _collect_stock_candidates(queries: list[str]) -> list[dict]:
    """Search Pexels+Pixabay across `queries`, dedupe by clip id, throttle by author.

    Returns merged list of clip dicts. Each clip has keys: id, source,
    author_id, author_name, url, duration, width, height, tags.
    """
    all_candidates: list[dict] = []
    seen_ids: set[str] = set()
    per_author: dict[str, int] = {}
    for q in queries:
        for searcher in (_search_pixabay_videos, _search_pexels_videos):
            try:
                results = searcher(q, count=10)
            except Exception as e:
                logger.warning(f"_collect_stock_candidates: {searcher.__name__}({q!r}) failed: {e}")
                continue
            for r in results:
                if r["id"] in seen_ids:
                    continue
                author_key = r.get("author_id") or r.get("source", "")
                if per_author.get(author_key, 0) >= STOCK_MAX_PER_AUTHOR:
                    continue
                seen_ids.add(r["id"])
                per_author[author_key] = per_author.get(author_key, 0) + 1
                all_candidates.append(r)
    return all_candidates


# --- Local B-roll library ---
BROLL_LIBRARY_DIR = Path(__file__).parent / "broll-library"
PHOTO_LIBRARY_DIR = BROLL_LIBRARY_DIR / "photos"
PHOTO_LIB_EXTS = (".jpg", ".jpeg", ".png", ".webp")
VIDEO_LIB_EXTS = (".mp4", ".mov", ".webm")

# Число фото в превью-альбоме фото-библиотеки (B-roll меню).
# Telegram media_group лимит = 10, держим 9 чтобы Максим/Артём быстрее выбирали.
PHOTO_PREVIEW_COUNT = 9

# Сколько клипов отдаём в категории «Моя библиотека клипов» за один заход.
# 10 — компактно (15 наследие, было слишком много: 15 отдельных send_video
# растягивают чат и грузят TG; 10 даёт достаточную выборку, согласован с
# PHOTO_PREVIEW_COUNT=9).
CLIP_PREVIEW_COUNT = 10

# Фактическая структура клип-библиотеки Максима на сервере:
#   broll-library/clips/maksim/{karting, glamping, sup, personal, ...}/*.mp4
# Категории совпадают со списком фото-папок (TG_PHOTO_CATEGORIES["maksim"]),
# плюс sup/personal которые исторически добавлены вручную через rsync.
MAKSIM_LIBRARY_CATEGORIES = [
    "karting", "glamping", "sup", "nature", "team", "personal", "meetings", "maksim_self",
]

# Эмодзи + подпись для кнопок-фильтров категорий (вариант В «Готовые материалы»).
# Порядок = порядок отрисовки. Используется и для подсветки ⭐ в библиотеке клипов.
MAKSIM_CATEGORY_UI = {
    "karting":     ("🏎", "Картинг"),
    "glamping":    ("🏕", "Глэмпинг"),
    "sup":         ("🏄", "Сап"),
    "nature":      ("🌲", "Природа"),
    "team":        ("👥", "Команда"),
    "meetings":    ("🤝", "Встречи"),
    "maksim_self": ("🙂", "Сам"),
    "personal":    ("📦", "Общее"),
}

# Ключевые слова для подсветки ⭐ категории под сценарий.
# Базируются на лексике Максима (бизнес-контекст Life Drive), не на Артёмовских AI-словах.
MAKSIM_CLIP_KEYWORDS = {
    "karting":   ["картинг", "карт ", "карты", "гонк", "трасс", "трек", "пилот", "руль", "болид"],
    "glamping":  ["глэмпинг", "глемпинг", "домик", "баня", "хот-таб", "хоттаб", "терраса", "гост", "бронир", "отдых", "купольн"],
    "sup":       ["сап", "сапборд", "доск", "вёсл", "вес-л"],
    "nature":    ["природ", "лес", "сосн", "озер", "берёз", "берез", "тишин", "закат", "рассвет"],
    "team":      ["команд", "сотрудник", "управляющ", "директор", "найм"],
    "meetings":  ["выступл", "конференц", "встреч", "форум", "спикер"],
}


def _list_brand_photo_library(brand: str | None = None) -> list[Path]:
    """Brand-aware фото-библиотека для B-roll меню.

    - maksim  → `photos/maksim/**/*.{jpg,…}` (только фото Максима по категориям)
    - default → `photos/**` (исторически — `photos/midjourney/**` Артёма)

    Возврат отсортирован по имени для детерминированного превью.
    """
    if brand is None:
        brand = _get_active_brand_name()
    if brand == "maksim":
        base = PHOTO_LIBRARY_DIR / "maksim"
    else:
        base = PHOTO_LIBRARY_DIR
    if not base.exists():
        return []
    photos: list[Path] = []
    for p in base.rglob("*"):
        if p.is_file() and p.suffix.lower() in PHOTO_LIB_EXTS:
            photos.append(p)
    return sorted(photos)


def _list_brand_clip_library(brand: str | None = None) -> list[tuple[str, list[Path]]]:
    """Brand-aware клип-библиотека: список `(категория, [клипы])`.

    - maksim  → `clips/maksim/<category>/*.mp4`
    - default → старые Артёмовы AI-категории (`<category>/*.mp4` в корне broll-library)

    Пустые категории НЕ возвращаются — UI рисует только непустые.
    """
    if brand is None:
        brand = _get_active_brand_name()
    out: list[tuple[str, list[Path]]] = []
    if brand == "maksim":
        base = BROLL_LIBRARY_DIR / "clips" / "maksim"
        if not base.exists():
            return []
        for child in sorted(base.iterdir()):
            if not child.is_dir():
                continue
            clips = [
                p for p in child.iterdir()
                if p.is_file() and p.suffix.lower() in VIDEO_LIB_EXTS
                and p.stat().st_size > 1000
            ]
            if clips:
                out.append((child.name, sorted(clips)))
        return out
    # default: Артёмовы категории на верхнем уровне
    if not BROLL_LIBRARY_DIR.exists():
        return []
    for child in sorted(BROLL_LIBRARY_DIR.iterdir()):
        if not child.is_dir() or child.name in ("photos", "clips"):
            continue
        clips = [
            p for p in child.iterdir()
            if p.is_file() and p.suffix.lower() in VIDEO_LIB_EXTS
            and p.stat().st_size > 1000
        ]
        if clips:
            out.append((child.name, sorted(clips)))
    return out


def _resolve_library_target(brand: str, kind: str, category: str) -> Path:
    """Куда копировать загружаемый материал в общую библиотеку.

    `kind` — "photo" → `photos/<brand>/<category>/`,
             "video" → `clips/<brand>/<category>/`.
    Папка не создаётся здесь — `_copy_to_library` сделает mkdir.
    """
    if kind == "photo":
        return PHOTO_LIBRARY_DIR / brand / category
    elif kind == "video":
        return BROLL_LIBRARY_DIR / "clips" / brand / category
    raise ValueError(f"unknown kind: {kind!r}")


def _copy_to_library(
    src: Path,
    brand: str,
    kind: str,
    category: str | None,
) -> Path | None:
    """Копия `src` в общую библиотеку с уникальным именем.

    Имя — `<stem>_<timestamp_ms><ext>` чтобы повторная загрузка того же файла
    не перезаписала существующий. `category=None` → НЕ копировать (юзер выбрал
    «✋ не в библиотеку»), возвращаем None.

    Возвращает путь к копии или None.
    """
    if not category:
        return None
    try:
        target_dir = _resolve_library_target(brand, kind, category)
    except ValueError as e:
        logger.warning(f"[_copy_to_library] {e}")
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    import time as _time
    import shutil as _shutil
    ts_ms = int(_time.time() * 1000)
    dest = target_dir / f"{src.stem}_{ts_ms}{src.suffix.lower()}"
    # На случай коллизии (две загрузки в одну ms) — добавим суффикс
    n = 1
    while dest.exists():
        dest = target_dir / f"{src.stem}_{ts_ms}_{n}{src.suffix.lower()}"
        n += 1
    try:
        _shutil.copy2(str(src), str(dest))
    except Exception as e:
        logger.warning(f"[_copy_to_library] copy failed: {e}")
        return None
    return dest


def _match_brand_clip_categories(brand: str, text: str) -> list[str]:
    """Категории, под которые подходит сценарий — для подсветки ⭐ в UI.

    Для бренда maksim берём `MAKSIM_CLIP_KEYWORDS`, для остальных — старый
    `BROLL_CATEGORY_KEYWORDS` (Артёмовы AI-категории).
    """
    text_low = (text or "").lower()
    if brand == "maksim":
        mapping = MAKSIM_CLIP_KEYWORDS
    else:
        mapping = BROLL_CATEGORY_KEYWORDS
    matched: list[str] = []
    for cat, keywords in mapping.items():
        if any(kw.lower() in text_low for kw in keywords):
            matched.append(cat)
    return matched


def _extract_last_card_from_state(state: dict) -> dict | None:
    """Из pending-state одного юзера достать {id, title} последней карточки.

    Используется в `start()` ПЕРЕД pop, чтобы сохранить «куда вернуться» и
    показать кнопку «🔄 Продолжить» при следующем /start.

    Источники в порядке приоритета:
      1. Активный `notion_page_id` + `card_data.title` — сейчас в работе.
      2. Сохранённый `_last_card` slot — preserved между /start.
    """
    if not isinstance(state, dict):
        return None
    pid = state.get("notion_page_id")
    if pid:
        title = (state.get("card_data") or {}).get("title") or "карточка"
        return {"id": pid, "title": str(title)[:60]}
    lc = state.get("_last_card")
    if isinstance(lc, dict) and lc.get("id"):
        return {"id": lc["id"], "title": str(lc.get("title", "карточка"))[:60]}
    return None



def _list_photo_library() -> list[Path]:
    """Return all photos in broll-library/photos/** as a sorted list.

    Used by the B-roll menu to offer the 'photo library' path as an explicit
    choice (Ken Burns fallback) instead of a hidden behavior.
    """
    if not PHOTO_LIBRARY_DIR.exists():
        return []
    photos: list[Path] = []
    for p in PHOTO_LIBRARY_DIR.rglob("*"):
        if p.is_file() and p.suffix.lower() in PHOTO_LIB_EXTS:
            photos.append(p)
    return sorted(photos)


# --- TG-post photo library (brand-scoped) ---
# Separate from `_list_photo_library` (which targets `photos/midjourney/**`
# for Ken Burns B-roll). The TG-post library lives at
# `photos/<brand>/<category>/*.jpg` and is consumed by the Selfie pipeline's
# crosspost step to attach photos to the Telegram-channel post.
# Categories per brand:
#   maksim  → glamping, karting, team, maksim_self, nature, meetings
#   default → (none — Artem's contentbot does not use TG-post photos yet)
TG_PHOTO_CATEGORIES = {
    "maksim": ["glamping", "karting", "team", "maksim_self", "nature", "meetings"],
}


def _tg_photo_brand_dir(brand_name: str | None = None) -> Path:
    """Return the brand-scoped TG-post photo directory.

    `photos/<brand>/` — categories live as subfolders inside.
    """
    if not brand_name:
        brand_name = _get_active_brand_name()
    return PHOTO_LIBRARY_DIR / brand_name


def _list_tg_photos(
    brand_name: str | None = None,
    category: str | None = None,
) -> list[Path]:
    """List photos available for TG-post attachment.

    - If `category` is None — returns ALL photos under `photos/<brand>/**`
    - If `category` is set — returns only `photos/<brand>/<category>/*.jpg`

    Sorted by mtime descending (newest first) so freshly uploaded photos
    surface in the first browse page.
    """
    if not brand_name:
        brand_name = _get_active_brand_name()
    base = _tg_photo_brand_dir(brand_name)
    if category:
        base = base / category
    if not base.exists():
        return []
    photos: list[Path] = []
    for p in base.rglob("*"):
        if p.is_file() and p.suffix.lower() in PHOTO_LIB_EXTS:
            photos.append(p)
    photos.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return photos


def _tg_photo_categories_with_counts(brand_name: str | None = None) -> list[tuple[str, int]]:
    """Return [(category, file_count)] for the brand's TG-photo library.

    Used by the «По категории» filter screen — empty categories are still
    shown (count=0) so user knows which folders exist.
    """
    if not brand_name:
        brand_name = _get_active_brand_name()
    cats = TG_PHOTO_CATEGORIES.get(brand_name, [])
    base = _tg_photo_brand_dir(brand_name)
    out: list[tuple[str, int]] = []
    for cat in cats:
        cat_dir = base / cat
        if not cat_dir.exists():
            out.append((cat, 0))
            continue
        n = sum(
            1 for p in cat_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in PHOTO_LIB_EXTS
        )
        out.append((cat, n))
    return out

# Keyword mapping: keywords → library categories
BROLL_CATEGORY_KEYWORDS = {
    "robots": ["robot", "atlas", "boston dynamics", "humanoid", "optimus", "tesla bot", "figure ", "робот", "робототехника", "андроид"],
    "ai-tools": ["claude", "chatgpt", "gpt", "gemini", "copilot", "openai", "ai tool", "ai chat", "нейросет", "искусственн", "ии ", "ai "],
    "tech-general": ["datacenter", "server", "google", "data center", "cloud", "дата-центр", "сервер", "облак", "технолог"],
    "social-media": ["instagram", "tiktok", "youtube", "reels", "shorts", "соцсет", "инстаграм", "тикток", "ютуб", "подписчик", "контент", "блог", "social media", "followers", "subscribers"],
    "space": ["space", "spacex", "rocket", "mars", "космос", "ракет", "спейс"],
    "medical": ["surgery", "robot surgery", "medical", "davinci", "crispr", "longevity", "aging", "gene therapy", "медицин", "хирург", "операци", "здоровь", "старени", "долголети", "генн", "терапи", "болезн", "лечени"],
    "ai-video": ["video generation", "sora", "kling", "runway", "генерация видео", "видеогенерац"],
    # apps — UI screencasts of common apps (Артём записывает сам через #lib apps <name>)
    "apps": [
        "chatgpt", "chat gpt", "gemini", "claude app", "notion", "telegram", "whatsapp",
        "instagram", "tiktok", "youtube", "netflix", "spotify", "apple music",
        "app store", "google play", "настройки iphone", "apple id", "приложени",
        "открой прилож", "интерфейс", "экран прилож",
    ],
    # payments — оплата, подписки, карты, App Store покупки
    "payments": [
        "подписк", "оплат", "платёж", "платеж", "карта", "apple pay", "app store",
        "покупк", "apple id", "подарочн", "gift card", "маркетплейс",
        "wildberries", "ozon", "avito", "валют", "курс доллар", "курс рубл",
        "chatgpt plus", "chatgpt pro", "spotify premium", "youtube premium",
        "заблокирован", "vpn", "обход", "оплачивать", "перевести деньг",
    ],
}


def _search_local_broll(
    script_phrase: str,
    visual_desc: str,
    search_queries: list[str],
    brand: str | None = None,
) -> list[dict]:
    """Search local B-roll library by matching keywords to categories.

    Brand-aware: maksim reads `clips/maksim/<cat>/` using `MAKSIM_CLIP_KEYWORDS`;
    default brand keeps the historical Артёмов layout (categories at the root
    of broll-library, matched via `BROLL_CATEGORY_KEYWORDS`).

    Without this branching, scripts for Максим that mention generic tech words
    ("технолог", "ИИ", "сервер") leak into category `tech-general`/`ai-tools`
    and the assembler pulls robot footage into Максим's videos — the "50/50
    роботам" incident (handoff 27 May).
    """
    if not BROLL_LIBRARY_DIR.exists():
        return []

    if brand is None:
        try:
            brand = _get_active_brand_name()
        except Exception:
            brand = None

    combined_text = f"{script_phrase} {visual_desc} {' '.join(search_queries)}".lower()

    if brand == "maksim":
        matched_categories = _match_brand_clip_categories("maksim", combined_text)
        if not matched_categories:
            return []
        # Maksim's library: clips/maksim/<cat>/
        brand_lib = dict(_list_brand_clip_library("maksim"))  # {cat: [Path,...]}
        clips: list[dict] = []
        for cat in matched_categories:
            for clip_path in brand_lib.get(cat, []):
                clips.append({
                    "id": f"local_{clip_path.stem}",
                    "source": "local",
                    "path": str(clip_path),
                    "filename": clip_path.name,
                    "category": cat,
                    "duration": 5,
                    "width": 1280,
                    "height": 720,
                    "tags": f"{cat} {clip_path.stem.replace('_', ' ')}",
                    "url": "",
                })
        if clips:
            logger.info(f"Local B-roll [maksim]: {len(clips)} clips from {matched_categories}")
        return clips

    # Default brand (Артём): legacy keyword-mapping + top-level category dirs.
    matched_categories = []
    for category, keywords in BROLL_CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in combined_text:
                matched_categories.append(category)
                break

    if not matched_categories:
        return []

    clips = []
    for category in matched_categories:
        cat_dir = BROLL_LIBRARY_DIR / category
        if not cat_dir.exists():
            continue
        for clip_path in cat_dir.glob("*.mp4"):
            if clip_path.stat().st_size > 1000:
                clips.append({
                    "id": f"local_{clip_path.stem}",
                    "source": "local",
                    "path": str(clip_path),
                    "filename": clip_path.name,
                    "category": category,
                    "duration": 5,
                    "width": 1280,
                    "height": 720,
                    "tags": f"{category} {clip_path.stem.replace('_', ' ')}",
                    "url": "",
                })

    if not clips:
        return []
    logger.info(f"Local B-roll: {len(clips)} clips from categories {matched_categories}")
    return clips


def _rank_broll_candidates(candidates: list[dict], script_phrase: str, visual_desc: str) -> list[dict]:
    """Use Claude to rank B-roll candidates by relevance to the script context."""
    if not candidates:
        return []
    # Build candidate descriptions for Claude
    candidate_list = []
    for i, c in enumerate(candidates):
        orientation = "portrait (9:16)" if c.get("height", 0) > c.get("width", 0) else "landscape"
        candidate_list.append(
            f"{i+1}. [{c['source']}] {c.get('duration', '?')}s, {orientation}, tags: {c.get('tags', 'N/A')}"
        )
    candidates_text = "\n".join(candidate_list)

    try:
        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            system="""Ты — режиссёр монтажа вертикальных Reels/Shorts. Контент от лица мужчины-предпринимателя (тематика — из фразы сценария ниже, не домысливай).

Выбери 2 лучших видео из списка кандидатов.

КРИТЕРИИ (по приоритету):
1. НИКАКИХ женщин, женских рук, маникюра — контент от мужчины, в кадре только мужские руки/силуэты или нейтральные предметы (экраны, ноутбуки, телефоны без людей)
2. Landscape (16:9) или square (1:1) — ПРИОРИТЕТ, потому что B-roll показывается в верхней половине экрана (формат ~9:8). Portrait (9:16) — только если нет других вариантов
3. Релевантность к фразе сценария и описанию кадра
4. Длительность 3-10 сек идеально
5. Разнообразие — два разных ракурса/сцены

Ответь ТОЛЬКО JSON-массивом из 2 номеров: [3, 7]
Если ничего не подходит: []""",
            messages=[{"role": "user", "content": f"Фраза: {script_phrase}\nКадр: {visual_desc}\n\nКандидаты:\n{candidates_text}"}],
        )
        raw = response.content[0].text.strip()
        # Extract JSON from any wrapper text
        import re
        json_match = re.search(r'\[[\d,\s]*\]', raw)
        if json_match:
            raw = json_match.group()
        picks = json.loads(raw)
        return [candidates[i - 1] for i in picks if isinstance(i, int) and 1 <= i <= len(candidates)]
    except Exception as e:
        logger.warning(f"B-roll ranking error: {e}")
        # Fallback: prefer landscape/square videos with male/neutral tags, sort by duration closest to 5s
        landscape = [c for c in candidates if c.get("width", 0) >= c.get("height", 0)]
        # Filter out obvious female content
        neutral = [c for c in (landscape or candidates)
                   if not any(w in c.get("tags", "").lower() for w in ("woman", "female", "girl", "lady"))]
        pool = neutral if neutral else (landscape if landscape else candidates)
        pool.sort(key=lambda c: abs(c.get("duration", 0) - 5))
        return pool[:2]



def download_and_cut_youtube(url: str, clip_duration: int = 5, max_clips: int = 12) -> list[dict]:
    """Download a video from any URL (YouTube, Vimeo, generic <video> pages, etc.)
    via yt-dlp and cut into clips.  Returns list of clip dicts."""
    import sys
    import shutil as _shutil
    import tempfile

    yt_dir = ASSETS_DIR / "youtube_clips"
    yt_dir.mkdir(parents=True, exist_ok=True)

    # Clean old clips
    for f in yt_dir.glob("clip_*.mp4"):
        f.unlink()

    # Download video via yt-dlp
    video_path = str(yt_dir / "source.mp4")
    if Path(video_path).exists():
        Path(video_path).unlink()

    is_youtube = any(h in url for h in ("youtube.com/", "youtu.be/", "youtube.com/shorts/"))

    # For non-YouTube pages: check if the page embeds a YouTube video.
    # yt-dlp generic extractor often misses YouTube iframes, so we extract
    # the embed URL ourselves and download that instead.
    if not is_youtube:
        try:
            import re as _re_yt
            html_probe = subprocess.run(
                ["curl", "-sL", "--max-time", "10", url],
                capture_output=True, text=True, timeout=15,
            )
            yt_embeds = _re_yt.findall(
                r'youtube\.com/embed/([A-Za-z0-9_-]{11})', html_probe.stdout
            )
            if yt_embeds:
                # Use the first YouTube embed found on the page
                url = f"https://www.youtube.com/watch?v={yt_embeds[0]}"
                is_youtube = True
                logger.info(f"Found YouTube embed on page, switching to {url}")
        except Exception:
            pass  # Fall through to generic yt-dlp

    # Prefer venv yt-dlp
    venv_bin = Path(sys.executable).parent / ("yt-dlp.exe" if os.name == "nt" else "yt-dlp")
    yt_dlp_bin = str(venv_bin) if venv_bin.exists() else "yt-dlp"

    cmd = [yt_dlp_bin,
           "-f", "best[height<=720][ext=mp4]/best[height<=720]/best",
           "--max-filesize", "100M",
           "-o", video_path,
           "--no-playlist",
           ]

    # Webshare residential proxy (when WEBSHARE_API_KEY is set in .env).
    # Routes the yt-dlp request through a real ISP IP so YouTube doesn't
    # bot-detect us. No-op if the env var is empty — falls back to direct
    # connection (legacy behavior).
    if is_youtube:
        try:
            from webshare_proxy import get_random_proxy
            proxy_url = get_random_proxy()
            if proxy_url:
                cmd += ["--proxy", proxy_url]
                # Log host:port only; never log credentials
                _safe = proxy_url.split("@", 1)[-1] if "@" in proxy_url else proxy_url
                logger.info(f"[yt-dlp] using webshare proxy {_safe}")
        except Exception as e:
            logger.warning(f"[yt-dlp] webshare proxy unavailable: {e}")

    # YouTube-specific: cookies (temp-copy to protect master) + EJS solver
    cookies_tmp: Path | None = None
    if is_youtube:
        cookies_master = Path(__file__).parent / "assets" / "youtube_cookies.txt"
        if not cookies_master.exists():
            cookies_master = Path(__file__).parent / "cookies.txt"
        if cookies_master.exists() and cookies_master.stat().st_size > 500:
            fd, tmp_path = tempfile.mkstemp(suffix=".txt", prefix="yt_cookies_")
            os.close(fd)
            cookies_tmp = Path(tmp_path)
            _shutil.copyfile(cookies_master, cookies_tmp)
            cmd += ["--cookies", str(cookies_tmp)]
        cmd += ["--remote-components", "ejs:github"]

    cmd.append(url)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    finally:
        if cookies_tmp and cookies_tmp.exists():
            try:
                cookies_tmp.unlink()
            except OSError:
                pass
    logger.info(f"yt-dlp exit={result.returncode}, stderr={result.stderr[:200] if result.stderr else 'none'}")

    if not Path(video_path).exists():
        stderr = result.stderr or ""
        if "Video unavailable" in stderr:
            raise RuntimeError("Видео недоступно (удалено или заблокировано)")
        elif "Private video" in stderr:
            raise RuntimeError("Видео приватное — нет доступа")
        else:
            raise RuntimeError(f"Не удалось скачать видео: {stderr[:200]}")

    # Get video duration
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, timeout=10,
    )
    total_duration = float(probe.stdout.strip())
    logger.info(f"YouTube video downloaded: {total_duration:.0f}s")

    # Calculate clip positions (evenly spread across video)
    num_clips = min(max_clips, int(total_duration / clip_duration))
    if num_clips < 1:
        num_clips = 1
    step = total_duration / num_clips

    clips = []
    for i in range(num_clips):
        start = i * step
        clip_path = str(yt_dir / f"clip_{i}.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(start), "-i", video_path,
             "-t", str(clip_duration), "-c:v", "libx264", "-an",
             "-vf", "scale=-2:720", clip_path],
            capture_output=True, timeout=30,
        )
        if Path(clip_path).exists():
            clips.append({
                "source": "youtube",
                "url": url,
                "filename": f"clip_{i}.mp4",
                "path": clip_path,
                "timecode": f"{int(start//60)}:{int(start%60):02d}",
                "duration": clip_duration,
            })

    # Clean source file to save space
    Path(video_path).unlink(missing_ok=True)
    logger.info(f"Cut {len(clips)} clips from YouTube video")
    return clips


def _is_twitter_url(url: str) -> bool:
    """Check if URL points to a tweet (twitter.com, x.com, nitter.*)."""
    return bool(re.search(r'(twitter\.com|x\.com|nitter\.[^/]+)/[^/]+/status/\d+', url))


def _extract_tweet_id_and_user(url: str) -> tuple[str, str] | None:
    """Extract (username, tweet_id) from any twitter/x/nitter URL."""
    m = re.search(r'(?:twitter\.com|x\.com|nitter\.[^/]+)/([^/]+)/status/(\d+)', url)
    return (m.group(1), m.group(2)) if m else None


async def _fetch_tweet_via_fxtwitter(url: str) -> dict | None:
    """Fetch tweet content + video via FxTwitter API.

    Returns ``{"text": ..., "video_urls": [...], "outbound_urls": [...]}``
    or None on failure.  FxTwitter is a free public API that proxies
    Twitter content — no API key required, works as of 2026-04.
    """
    import httpx

    parsed = _extract_tweet_id_and_user(url)
    if not parsed:
        return None
    username, tweet_id = parsed

    api_url = f"https://api.fxtwitter.com/{username}/status/{tweet_id}"
    try:
        _fx_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }
        async with httpx.AsyncClient(timeout=15, headers=_fx_headers) as client:
            resp = await client.get(api_url)
            if resp.status_code != 200:
                logger.warning(f"FxTwitter returned {resp.status_code} for {url}")
                return None
            data = resp.json()
            tweet = data.get("tweet", {})
            text = tweet.get("text", "")
            # Collect video URLs
            video_urls = []
            for media in (tweet.get("media") or {}).get("all", []):
                if media.get("type") == "video" and media.get("url"):
                    video_urls.append(media["url"])
            # Collect outbound URLs (links the tweet points to)
            outbound = []
            for u in re.findall(r'https?://t\.co/\S+', text):
                outbound.append(u)
            # FxTwitter also provides expanded URLs in the tweet object
            if tweet.get("url"):
                outbound.append(tweet["url"])

            return {
                "text": text,
                "video_urls": video_urls,
                "outbound_urls": outbound,
                "author": tweet.get("author", {}).get("name", username),
            }
    except Exception as e:
        logger.warning(f"FxTwitter fetch failed for {url}: {e}")
        return None


def _jina_text_is_garbage(text: str) -> bool:
    """Check if Jina Reader output is mostly navigation/menu garbage.

    Returns True if the text is dominated by markdown links, images, and
    navigation items rather than real article content.  Typical failure
    mode: JS-rendered WordPress sites return header/footer/sidebar only.
    """
    lines = text.strip().splitlines()
    if not lines:
        return True
    link_lines = sum(1 for ln in lines if ln.strip().startswith(("[![", "[!", "* [", "*   [")))
    # If >40 % of lines are navigation links → garbage
    if link_lines / len(lines) > 0.40:
        return True
    # If most of the text is URLs / markdown links
    url_chars = sum(len(m.group()) for m in re.finditer(r'https?://\S+', text))
    if len(text) > 500 and url_chars / len(text) > 0.45:
        return True
    return False


def _extract_article_from_html(html: str) -> str:
    """Fallback article extraction from raw HTML when Jina fails.

    Tries in order:
    1. JSON-LD ``articleBody`` (if the site embeds full text there)
    2. ``og:description`` / ``<meta name="description">`` (always present
       for well-formed articles — gives at least 1-2 sentences)
    3. ``<p>`` tags inside ``<article>`` or ``.entry-content``

    Returns extracted text (may be short — the caller decides if it's enough).
    """
    import json as _json

    result_parts: list[str] = []

    # 1. JSON-LD structured data — sometimes has full articleBody
    for m in re.finditer(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html, re.DOTALL,
    ):
        try:
            ld = _json.loads(m.group(1))
            items = ld.get("@graph", [ld]) if isinstance(ld, dict) else ld
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("@type") == "Article" or "Article" in str(item.get("@type", "")):
                    body = item.get("articleBody", "")
                    if body and len(body) > 100:
                        result_parts.append(body[:6000])
                    headline = item.get("headline", "")
                    if headline:
                        result_parts.insert(0, headline)
                    desc = item.get("description", "")
                    if desc and desc not in " ".join(result_parts):
                        result_parts.append(desc)
        except (_json.JSONDecodeError, AttributeError):
            pass

    # 2. OpenGraph / meta description
    for pattern in (
        r'<meta\s+property="og:description"\s+content="([^"]+)"',
        r'<meta\s+name="description"\s+content="([^"]+)"',
    ):
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            desc = m.group(1).strip()
            if desc and desc not in " ".join(result_parts):
                result_parts.append(desc)

    # 3. <p> tags inside <article>
    article_m = re.search(r'<article[^>]*>(.*?)</article>', html, re.DOTALL)
    if article_m:
        paras = re.findall(r'<p[^>]*>(.*?)</p>', article_m.group(1), re.DOTALL)
        for p in paras[:15]:
            clean = re.sub(r'<[^>]+>', '', p).strip()
            if len(clean) > 40 and clean not in " ".join(result_parts):
                result_parts.append(clean)

    return "\n\n".join(result_parts)[:6000]


def extract_youtube_urls(text: str) -> list[str]:
    """Extract YouTube video URLs from text."""
    patterns = [
        r'https?://(?:www\.)?youtube\.com/watch\?v=[A-Za-z0-9_-]+',
        r'https?://youtu\.be/[A-Za-z0-9_-]+',
        r'https?://(?:www\.)?youtube\.com/shorts/[A-Za-z0-9_-]+',
    ]
    urls = []
    for p in patterns:
        urls.extend(re.findall(p, text))
    return list(dict.fromkeys(urls))  # deduplicate


def generate_shotlist(script_text: str) -> list[dict]:
    """Use Claude to generate a shotlist with multi-variant search queries for B-roll."""
    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system="""Ты — режиссёр коротких вертикальных видео (Reels/Shorts). По сценарию создай шотлист.

ФОРМАТ ОТВЕТА — строго JSON-массив:
{
  "timecode": "0:00-0:03",
  "text": "фраза из сценария",
  "visual": "описание кадра на русском",
  "search_queries": ["query1", "query2", "query3"],
  "type": "broll" или "talking_head"
}

ПРАВИЛА ШОТЛИСТА:
- 4-8 сегментов по 2-5 секунд
- Чередуй talking_head и broll (не больше 2 broll подряд)
- Первый и последний кадр — talking_head
- Для talking_head: search_queries = []
- B-roll для: перечислений, абстрактных понятий, инструментов, переходов
- Talking head для: эмоциональных фраз, CTA, личных заявлений

КРИТИЧЕСКИ ВАЖНО — ПРАВИЛА ПОИСКОВЫХ ЗАПРОСОВ:
Видео ищутся на стоковых сайтах (Pexels, Pixabay). Там есть ТОЛЬКО реальные съёмки:
- Люди за компьютерами/телефонами, офисы, природа, города, руки на клавиатуре
- Экраны с интерфейсами, уведомления, графики на мониторах
- Бизнес-встречи, кофейни, коворкинги, рабочие столы

Там НЕТ и НИКОГДА не будет:
- Анимаций, motion graphics, счётчиков, таймеров
- Летающих иконок, логотипов брендов, UI-мокапов
- Конкретных приложений (нельзя искать "Claude interface" или "Instagram app")
- Абстрактных визуализаций данных, нейросетей, AI-мозгов

ВАЖНО — ЭТО КОНТЕНТ МУЖЧИНЫ-БЛОГЕРА:
- Всегда "man", НИКОГДА "person", "woman", "girl", "hand"
- Если нужны руки — "male hands typing keyboard", "man holding phone"
- Если люди не нужны — предметы: "laptop screen", "smartphone notification", "coffee desk workspace"

ПРИМЕРЫ ПРАВИЛЬНОЙ ЗАМЕНЫ:
- "цель 10694 подписчика" → НЕ "animated counter" → ДА "man checking phone social media", "smartphone instagram followers screen", "growth chart laptop screen"
- "использую Claude и Gemini" → НЕ "Claude AI logo" → ДА "man typing laptop chat interface", "ai chatbot conversation laptop screen", "male hands smartphone app"
- "монтирую видео" → НЕ "video editing animation" → ДА "man editing video laptop", "creative workspace monitors", "content creator desk setup"
- "0 подписчиков" → НЕ "zero animation" → ДА "man starting new project laptop", "empty desk fresh start", "smartphone new account setup"

ГЕНЕРАЦИЯ 3 ЗАПРОСОВ для каждого B-roll:
1. Действие (4-6 слов): "man scrolling phone social media"
2. Обстановка (3-4 слова): "modern workspace laptop screen"
3. Предметы крупным планом (3-4 слова): "smartphone screen close up"

Все запросы на английском. НЕ добавляй слова "vertical" или "portrait" — B-roll показывается в верхней половине экрана, горизонтальное видео подходит лучше.
Думай: "какое РЕАЛЬНОЕ видео с мужчиной или предметами передаст смысл этой фразы?".""",
        messages=[{"role": "user", "content": f"Сценарий:\n{script_text}"}],
    )
    raw = response.content[0].text.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


async def find_broll_for_shotlist(shotlist: list[dict]) -> list[dict]:
    """Smart B-roll search: local library first, then stock sites, AI-ranked."""
    for shot in shotlist:
        if shot.get("type") != "broll":
            continue
        queries = shot.get("search_queries", [])
        if not queries:
            if shot.get("search_en"):
                queries = [shot["search_en"]]
            else:
                continue

        script_phrase = shot.get("text", "")
        visual_desc = shot.get("visual", "")

        # Step 1: Search local B-roll library
        local_clips = _search_local_broll(script_phrase, visual_desc, queries)
        if local_clips:
            # Pick 3 random clips per shot from local library
            picked = random.sample(local_clips, min(3, len(local_clips)))
            shot["videos"] = picked
            shot["broll_source"] = "local"
            logger.info(f"B-roll '{visual_desc[:40]}': using {len(picked)} LOCAL clips")
            continue

        # Step 2: Fall back to stock sites.
        all_candidates = _collect_stock_candidates(queries)
        logger.info(
            f"B-roll '{visual_desc[:40]}': {len(all_candidates)} STOCK candidates "
            f"from {len(queries)} queries"
        )

        if all_candidates:
            shot["videos"] = _rank_broll_candidates(
                all_candidates,
                script_phrase=script_phrase,
                visual_desc=visual_desc,
            )
            shot["broll_source"] = "stock"
        else:
            shot["videos"] = []
    return shotlist


# --- YouTube B-roll search by script ---
def generate_youtube_search_queries(script_text: str) -> list[dict]:
    """Use Claude to generate YouTube search queries for B-roll based on script."""
    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system="""Ты — режиссёр коротких видео (Reels/Shorts). По сценарию ты подбираешь YouTube-видео для B-roll.

ЗАДАЧА: Сгенерируй 3-5 поисковых запросов для YouTube, которые найдут подходящие видео.

ПРАВИЛА:
- Запросы на АНГЛИЙСКОМ языке
- Ищи реальные видео: обзоры, демонстрации, новости, репортажи по теме сценария
- Если в сценарии упоминаются конкретные продукты/технологии — используй их названия
- Каждый запрос 3-6 слов
- Ищи видео которые можно нарезать на 3-5 секундные клипы для вставок
- Предпочитай: экраны, интерфейсы, демо, обзоры технологий, новостные сюжеты

ФОРМАТ ОТВЕТА — строго JSON-массив:
[
  {"query": "запрос на англ", "reason": "почему подходит, на русском, коротко"}
]

Пример для сценария про Claude AI:
[
  {"query": "Claude AI demo 2025", "reason": "Демо интерфейса Claude"},
  {"query": "Anthropic Claude review", "reason": "Обзоры с реальными экранами"},
  {"query": "AI chatbot comparison test", "reason": "Сравнения чатботов — подходит для вставок"}
]""",
        messages=[{"role": "user", "content": f"Сценарий:\n{script_text}"}],
    )
    raw = response.content[0].text.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


def search_youtube_videos(query: str, max_results: int = 5) -> list[dict]:
    """Search YouTube Data API for videos. Returns list of video info dicts."""
    from crosspost import _get_youtube_access_token
    import requests as req

    access_token = _get_youtube_access_token()
    if not access_token:
        logger.error("YouTube not authorized for search")
        return []

    # Search with date filter — only videos from last 2 years
    from datetime import timezone
    two_years_ago = (datetime.now(timezone.utc) - timedelta(days=730)).strftime("%Y-%m-%dT%H:%M:%SZ")

    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": max_results,
        "order": "relevance",
        "publishedAfter": two_years_ago,
        "videoDuration": "medium",  # 4-20 min — good for B-roll extraction
        "relevanceLanguage": "en",
    }
    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        resp = req.get(
            "https://www.googleapis.com/youtube/v3/search",
            params=params,
            headers=headers,
            timeout=15,
        )
        if resp.status_code != 200:
            logger.error(f"YouTube search failed: {resp.status_code} {resp.text[:200]}")
            return []

        items = resp.json().get("items", [])
        results = []
        for item in items:
            snippet = item.get("snippet", {})
            video_id = item.get("id", {}).get("videoId", "")
            if not video_id:
                continue
            results.append({
                "video_id": video_id,
                "title": snippet.get("title", ""),
                "channel": snippet.get("channelTitle", ""),
                "published": snippet.get("publishedAt", "")[:10],
                "thumbnail": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
                "url": f"https://www.youtube.com/watch?v={video_id}",
            })
        return results
    except Exception as e:
        logger.error(f"YouTube search error: {e}")
        return []


# --- Notion: fetch cards ---
def fetch_notion_page_script(page_id: str) -> str:
    """Fetch script text from a Notion page's body blocks.

    Skips asset-reference paragraphs (avatar URLs, b-roll links, etc.) that the
    bot appends to the same page — those start with emoji markers (🤖/🎥/🎙/📎)
    or contain http(s) URLs. Only real script paragraphs are returned.
    """
    import re as _re
    blocks = notion.blocks.children.list(block_id=page_id)
    text_parts = []
    found_script = False
    for block in blocks.get("results", []):
        btype = block.get("type", "")
        # Look for text after "Сценарий" heading
        if btype == "heading_2":
            rich = block["heading_2"].get("rich_text", [])
            heading_text = "".join(r.get("plain_text", "") for r in rich)
            if "Сценарий" in heading_text:
                found_script = True
                continue
            elif found_script:
                break  # Next heading = end of script
        if found_script and btype == "paragraph":
            rich = block["paragraph"].get("rich_text", [])
            para_text = "".join(r.get("plain_text", "") for r in rich)
            stripped = para_text.strip()
            if not stripped:
                continue
            # Skip bot-appended asset references
            if stripped[:2] in ("🤖", "🎥", "🎙", "📎") or stripped.startswith(("🤖", "🎥", "🎙", "📎")):
                continue
            if _re.search(r'https?://', stripped):
                continue
            text_parts.append(para_text)
    return "\n".join(text_parts).strip()


def update_notion_page_script(page_id: str, new_script: str) -> None:
    """Replace the script paragraph blocks under the 'Сценарий' heading on a Notion page.

    - Finds the 'Сценарий' heading_2 block.
    - Deletes all paragraph blocks that follow until the next heading.
    - Inserts new paragraph(s) right after the heading. Long scripts are split
      into <=1900-char chunks because Notion rich_text has a 2000-char limit.
    - If no 'Сценарий' heading exists, appends one at the end of the page.
    """
    blocks = notion.blocks.children.list(block_id=page_id)
    script_heading_id = None
    to_delete: list[str] = []
    found_script = False
    for block in blocks.get("results", []):
        btype = block.get("type", "")
        if btype == "heading_2":
            rich = block["heading_2"].get("rich_text", [])
            heading_text = "".join(r.get("plain_text", "") for r in rich)
            if "Сценарий" in heading_text:
                found_script = True
                script_heading_id = block["id"]
                continue
            elif found_script:
                break  # next heading = end of script section
        if found_script and btype == "paragraph":
            to_delete.append(block["id"])

    for bid in to_delete:
        try:
            notion.blocks.delete(block_id=bid)
        except Exception as e:
            logger.warning(f"Failed to delete old script block {bid}: {e}")

    # Split long text into <=1900-char chunks on paragraph boundaries
    chunks: list[str] = []
    remaining = new_script.strip()
    MAX = 1900
    while remaining:
        if len(remaining) <= MAX:
            chunks.append(remaining)
            break
        # Try to split on last double newline, then newline, then space
        cut = remaining.rfind("\n\n", 0, MAX)
        if cut < MAX // 2:
            cut = remaining.rfind("\n", 0, MAX)
        if cut < MAX // 2:
            cut = remaining.rfind(" ", 0, MAX)
        if cut < MAX // 2:
            cut = MAX
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()

    new_blocks = [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"text": {"content": chunk}}]},
        }
        for chunk in chunks
    ]

    if script_heading_id:
        notion.blocks.children.append(
            block_id=page_id,
            children=new_blocks,
            after=script_heading_id,
        )
    else:
        # No heading — append heading + paragraphs at end of page
        notion.blocks.children.append(
            block_id=page_id,
            children=[
                {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {"rich_text": [{"text": {"content": "Сценарий"}}]},
                },
                *new_blocks,
            ],
        )


def fetch_notion_page_sources(page_id: str) -> dict:
    """Fetch source URLs and YouTube URLs from Notion page 'Источники' block."""
    blocks = notion.blocks.children.list(block_id=page_id)
    source_urls = []
    youtube_urls = []
    found_sources = False
    for block in blocks.get("results", []):
        btype = block.get("type", "")
        if btype == "heading_2":
            rich = block["heading_2"].get("rich_text", [])
            heading_text = "".join(r.get("plain_text", "") for r in rich)
            if "Источники" in heading_text:
                found_sources = True
                continue
            elif found_sources:
                break
        if found_sources and btype == "paragraph":
            rich = block["paragraph"].get("rich_text", [])
            text = "".join(r.get("plain_text", "") for r in rich)
            for line in text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                # Remove emoji prefix
                url_part = re.sub(r'^[📎🎬]\s*', '', line).strip()
                if not url_part.startswith("http"):
                    continue
                yt = extract_youtube_urls(url_part)
                if yt:
                    youtube_urls.extend(yt)
                else:
                    source_urls.append(url_part)
    return {"source_urls": source_urls, "youtube_urls": youtube_urls}


def fetch_notion_cards(status_filter: str = None, limit: int = 200) -> list[dict]:
    """Fetch cards from Notion database with full pagination.

    Status property type depends on the active brand's Notion schema —
    `status` for Artem's DB, `select` for Maksim's. We build the filter
    via `_status_filter_payload` which reads `notion_status_type` from
    the active brand. Without this dispatch, Maksim queries silently
    return zero results because Notion treats {"status": ...} as a no-op
    for select-typed properties.
    """
    filter_obj = None
    if status_filter:
        filter_obj = {
            "property": "Status",
            **_status_filter_payload(status_filter),
        }

    cards = []
    has_more = True
    start_cursor = None

    while has_more:
        query_params = {
            "database_id": NOTION_DB,
            "page_size": 100,
            "sorts": [{"timestamp": "created_time", "direction": "descending"}],
        }
        if filter_obj:
            query_params["filter"] = filter_obj
        if start_cursor:
            query_params["start_cursor"] = start_cursor

        result = notion.databases.query(**query_params)

        for page in result.get("results", []):
            props = page["properties"]
            title = ""
            if props.get("Name", {}).get("title"):
                title = props["Name"]["title"][0]["text"]["content"]

            # Status property type depends on the brand's Notion schema —
            # `status` for Artem's DB, `select` for Maksim's DB. Read both.
            status = ""
            status_prop = props.get("Status", {})
            if status_prop.get("status"):
                status = status_prop["status"]["name"]
            elif status_prop.get("select"):
                status = status_prop["select"]["name"]

            # Rubric property name varies by brand (with/without trailing
            # space). Try both forms so the function works across brands.
            rubric = ""
            rubric_prop = props.get(_rubric_property_name()) or props.get("Рубрика ") or props.get("Рубрика")
            if rubric_prop and rubric_prop.get("select"):
                rubric = rubric_prop["select"]["name"]

            # Brand profile (select: default / shoes / …). Used by
            # _pick_card_apply_brand() to auto-switch HeyGen avatar +
            # ElevenLabs voice on a per-card basis.
            brand = ""
            brand_prop = props.get("Бренд") or props.get("Brand")
            if brand_prop and brand_prop.get("select"):
                brand = brand_prop["select"]["name"]

            cards.append({
                "id": page["id"],
                "title": title,
                "status": status,
                "rubric": rubric,
                "brand": brand,
                "url": page["url"],
            })

        has_more = result.get("has_more", False)
        start_cursor = result.get("next_cursor")

        if len(cards) >= limit:
            break

    return cards


def update_notion_status(page_id: str, new_status: str, brand_name: str | None = None):
    """Update the status of a Notion page.

    `brand_name` — optional explicit brand. When None, falls back to the
    active brand from context (which is normally set by `_pick_card_apply_brand`
    before this is called, so existing call sites keep working). Pass an
    explicit value when you have a card_id/page_id but no preceding card-pick
    in the same async chain.
    """
    notion.pages.update(
        page_id=page_id,
        properties={
            "Status": _status_property_value(new_status, brand_name)
        },
    )
    logger.info(f"Notion статус обновлён: {page_id} → {new_status}")


def add_notion_note(page_id: str, note_text: str):
    """Append a note as a new block to a Notion page."""
    notion.blocks.children.append(
        block_id=page_id,
        children=[
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"text": {"content": f"📌 {note_text}"}}]
                },
            }
        ],
    )
    logger.info(f"Заметка добавлена в Notion: {page_id}")


# --- Status options (match Notion board exactly) ---
STATUSES = [
    "Идеи | старт",
    "Сценарий | озвучка",
    "Подбор скринкаст",
    "Аватар | генерации",
    "Монтаж",
    "Готово к публикации",
    "Опубликовано",
]


# --- Bot handlers ---
def _main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Persistent reply keyboard. Shown via /keyboard command (below).

    History: previously shown automatically after /start, but Telegram Web
    collapses reply keyboards into a hard-to-see icon — Artem missed them.
    Now the default /start UI is inline buttons (visible everywhere);
    reply keyboard is an opt-in extra for mobile clients via /keyboard.
    """
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🎬 Новая идея"), KeyboardButton("🎥 Селфи")],
            [KeyboardButton("📋 Карточки"), KeyboardButton("📝 TG-пост")],
            [KeyboardButton("🏷 Бренд"), KeyboardButton("❓ Помощь")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def _start_action_kb() -> InlineKeyboardMarkup:
    """Inline keyboard with the main actions — shown right in the /start
    greeting. Taps trigger ``cmd_*`` callbacks that route to the matching
    commands (selfie, cards, tgpost, brand, help). Works reliably in both
    Telegram Web and mobile.
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💡 Новая идея", callback_data="cmd_new_idea"),
            InlineKeyboardButton("🎥 Селфи", callback_data="cmd_selfie"),
        ],
        [
            InlineKeyboardButton("🖼 Фото", callback_data="cmd_image"),
            InlineKeyboardButton("🎬 Видео", callback_data="cmd_video"),
        ],
        [
            InlineKeyboardButton("📋 Карточки", callback_data="cmd_cards"),
            InlineKeyboardButton("📝 TG-пост", callback_data="cmd_tgpost"),
        ],
        [
            InlineKeyboardButton("🏷 Сменить бренд", callback_data="cmd_brand"),
            InlineKeyboardButton("❓ Помощь", callback_data="cmd_help"),
        ],
    ])


def _brand_picker_kb(selected: str) -> InlineKeyboardMarkup:
    """Inline keyboard for switching brand. Marks the active one with a dot."""
    rows = []
    for name, cfg in BRANDS.items():
        mark = "● " if name == selected else ""
        label = f"{mark}{name} — {cfg.get('description', '')[:40]}"
        rows.append([InlineKeyboardButton(label, callback_data=f"brand_set:{name}")])
    return InlineKeyboardMarkup(rows)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Preserve last-card pointer ПЕРЕД pop, чтобы /start мог показать кнопку
    # «🔄 Продолжить» одним кликом к последней карточке. См.
    # _extract_last_card_from_state + _build_maksim_start_kb.
    _last_card_preserved: dict | None = None
    if user_id in pending:
        _last_card_preserved = _extract_last_card_from_state(pending[user_id])
        pending.pop(user_id, None)
        if _last_card_preserved:
            pending[user_id] = {"_last_card": _last_card_preserved}
        _save_pending(pending)

    # Billing gate on /start:
    # - admin / BILLING_ENABLED=0 → full studio UI (brand picker etc.)
    # - registered active client → show client billing menu (balance + «Новый ролик»)
    # - unregistered → polite rejection with support contact
    if BILLING_ENABLED and not _billing_is_admin(user_id):
        client = billing_api.get_client(user_id)
        if client and client.is_active:
            # Route into the client-side billing menu (balance, new video, ...)
            await billing_handlers.show_client_menu(update, context)
            return
        if not client:
            await update.message.reply_text(
                "👋 Привет! Этот бот работает в платном режиме.\n\n"
                f"Чтобы подключиться — напишите {_BILLING_SUPPORT}.\n"
                "Тарифы начинаются от 150 ₽ за ролик."
            )
            return
        # client exists but deactivated
        await update.message.reply_text(
            "⏸ Ваш аккаунт временно деактивирован.\n\n"
            f"Свяжитесь с {_BILLING_SUPPORT} для восстановления."
        )
        return

    active = _get_active_brand_name()
    active_desc = BRANDS.get(active, BRANDS["default"]).get("description", "")

    # Maksim brand has its own greeting and main menu — 4 format buttons,
    # Notion shortcut, no Artem-specific commands. Branch here so Maksim
    # never sees /selfie, /image, /tgpost (those work for Artem only).
    if active == "maksim":
        await update.message.reply_text(
            _maksim_greeting_text(user_id),
            parse_mode="Markdown",
            reply_markup=_build_maksim_start_kb(last_card=_last_card_preserved),
        )
        return

    greeting = (
        "👋 Привет! Я — твой контент-бот.\n\n"
        f"🏷 Активный бренд: *{active}* — _{active_desc}_\n\n"
        "💬 Можешь просто отправить идею текстом или голосовым — я сделаю "
        "сценарий → Notion → обложку → озвучку → аватар → сборку.\n\n"
        "Или выбери действие кнопкой ниже:"
    )

    # Greeting with INLINE buttons (tappable, visible in Telegram Web too).
    # Reply-keyboard was previously here but invisible on Web — removed.
    await update.message.reply_text(
        greeting,
        parse_mode="Markdown",
        reply_markup=_start_action_kb(),
    )
    # Inline brand picker — one tap to switch brand
    await update.message.reply_text(
        "🏷 Или переключи бренд для новых карточек:",
        reply_markup=_brand_picker_kb(active),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /help — бренд-зависимый. Для maksim список команд другой (нет /update,
    # /report, /brand, /launches, /pub — они не в его пайплайне).
    if _get_active_brand_name() == "maksim":
        help_text = (
            "📋 *Что умеет бот*\n\n"
            "*Основной путь:*\n"
            "Пиши идею текстом или голосом → бот ведёт через сценарий → "
            "обложку → озвучку → аватар → монтаж → публикацию.\n\n"
            "*Команды:*\n"
            "• `/selfie` — живое видео с телефона: субтитры + Notion + TG-пост\n"
            "• `/script` — вставить готовый сценарий (без переписывания)\n"
            "• `/notion` — закинуть идею в Notion без сценария\n"
            "• `/tgpost` — пост в твой канал @yumsunov\\_realbiz\n"
            "• `/cards` — карточки в работе\n"
            "• `/ideas` — бэклог идей\n"
            "• `/cards_all` — все карточки включая опубликованные\n"
            "• `/calendar` — сетка публикаций\n"
            "• `/stats` — статистика по подписчикам\n\n"
            "*Дополнительно:*\n"
            "• `/image` — одно фото по описанию (fal.ai Nano Banana)\n"
            "• `/video` — короткий клип 5/10 сек (fal.ai Kling)\n"
            "• `/heygen_test` — оживить фото быстрым тестом\n"
            "• `/yt_auth`, `/ig_auth`, `/vk_auth` — авторизовать соцсети для "
            "кросспоста\n\n"
            "_Главное меню — команда /start._"
        )
        kb = None
    else:
        help_text = (
            "📋 *Что умеет бот*\n\n"
            "*Создание ролика (основной путь):*\n"
            "Просто отправь идею текстом или голосом — сделаю сценарий → "
            "карточку в Notion → обложку → озвучку → аватар → сборку.\n\n"
            "*Ещё команды:*\n"
            "• `/script` — вставить готовый сценарий (без переписывания)\n"
            "• `/notion` — закинуть идею в Notion без сценария\n"
            "• `/selfie` — живое видео с телефона: субтитры + Notion\n"
            "• `/image` — одно фото по описанию (Nano Banana Pro)\n"
            "• `/video` — одно короткое видео 5/10 сек (Kling 3.0 Pro)\n"
            "• `/heygen_test` — тест аватара: фото + аудио → видео\n"
            "• `/tgpost` — пост для канала эксперимента\n\n"
            "*Карточки:*\n"
            "• `/cards` — карточки в работе (сейчас делаются)\n"
            "• `/ideas` — бэклог идей\n"
            "• `/cards_all` — все включая опубликованные\n\n"
            "*Статистика:*\n"
            "• `/stats` — последний замер подписчиков\n"
            "• `/calendar` — сетка публикаций\n\n"
            "*Настройки:*\n"
            "• `/yt_auth`, `/vk_auth` — авторизовать соцсети для кросспоста\n\n"
            "_Нажми любую кнопку ниже — сразу перейду туда._"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("💡 Новая идея", callback_data="cmd_new_idea"),
                InlineKeyboardButton("📋 Карточки", callback_data="cmd_cards"),
            ],
            [
                InlineKeyboardButton("🎥 Селфи", callback_data="cmd_selfie"),
                InlineKeyboardButton("📝 TG-пост", callback_data="cmd_tgpost"),
            ],
            [
                InlineKeyboardButton("🖼 Фото", callback_data="cmd_image"),
                InlineKeyboardButton("🎬 Видео", callback_data="cmd_video"),
            ],
        ])
    await update.message.reply_text(
        help_text, parse_mode="Markdown", reply_markup=kb,
    )


async def notion_quick_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick Notion card creation — skip script/cover, just create a card."""
    user_id = update.effective_user.id
    logger.info(f"[user:{user_id}] /notion")
    # Clear any previous state
    if user_id in pending:
        pending.pop(user_id, None)
    pending[user_id] = {"state": "notion_quick"}
    _save_pending(pending)
    await update.message.reply_text(
        "📋 Быстрая карточка в Notion\n\n"
        "Напиши или надиктуй идею — создам карточку сразу, без сценария и обложки.\n"
        "Статус: Идеи | старт"
    )


async def voice_quick_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick voiceover — skip script/cover/Notion, just voice the text."""
    user_id = update.effective_user.id
    logger.info(f"[user:{user_id}] /voice")
    if user_id in pending:
        pending.pop(user_id, None)
    pending[user_id] = {"state": "voice_quick"}
    _save_pending(pending)
    await update.message.reply_text(
        "🎙 Быстрая озвучка\n\n"
        "Отправь готовый текст (текстом или голосовым) — разобью на части и озвучу через ElevenLabs.\n"
        "Без сценария, без Notion, сразу озвучка."
    )


_ASR_POLISH_SYSTEM = (
    "Ты редактор расшифровок русской речи. Тебе дают сырой Whisper-транскрипт. "
    "Твоя задача — найти и исправить только явные ASR-ошибки распознавания, "
    "не меняя смысл, тон, порядок слов и стиль речи говорящего.\n\n"
    "ТИПИЧНЫЕ ОШИБКИ КОТОРЫЕ ИСПРАВЛЯТЬ:\n"
    "— Слипшиеся слоги в начале слова: «с основы лес» → «сосновый лес», "
    "«с строительство» → «строительство», «в место» → «вместо» (по контексту)\n"
    "— Ложные предлоги добавленные к существительным: «с строительство», "
    "«в время» вместо «время»\n"
    "— Неправильно распознанные термины бизнес-ниши («глэмпинг», «картинг», "
    "«домики», «трасса», «гости»), если в контексте явно эти слова\n"
    "— Лишняя пунктуация в середине слова: «глэмп — пинг» → «глэмпинг»\n\n"
    "ЧЕГО НЕ ДЕЛАТЬ:\n"
    "— Не править разговорный стиль (междометия «ну», «вот», «значит» — оставить)\n"
    "— Не сокращать, не упрощать, не «улучшать» предложения — это речь автора\n"
    "— Не менять порядок слов\n"
    "— Не добавлять знаки препинания где их не было\n"
    "— Не исправлять «ошибки» которые могут быть осознанным стилем\n\n"
    "Верни ТОЛЬКО исправленный текст без комментариев, без преамбулы."
)


_TGPOST_SURG_EDITOR_SYSTEM = (
    "Ты редактор TG-постов в стиле Максима Юмсунова. Тебе дают:\n"
    "1. Текущий текст поста\n"
    "2. Инструкцию пользователя что в нём поменять (на русском, как живая речь)\n\n"
    "Твоя задача — внести правку точно по инструкции, СОХРАНИВ всё остальное.\n\n"
    "ПРАВИЛА:\n"
    "1. НЕ переписывай весь пост заново. Меняй только то, что просит инструкция.\n"
    "2. Сохраняй стиль и тон Максима: личный, прямой, без воды, без инфоцыган.\n"
    "3. Если инструкция «убери первый абзац» — убери ровно первый абзац, остальное оставь как есть.\n"
    "4. Если инструкция «поменяй хук» — поменяй только заголовок и/или первое предложение/первый абзац.\n"
    "5. Если инструкция «сделай короче» — сократи равномерно, не выкидывай ключевую мысль.\n"
    "6. Если инструкция «добавь юмор в финал» — добавь иронию ТОЛЬКО в финальные 1-2 абзаца.\n"
    "7. Markdown **жирный** сохраняй в формате `**...**` (для совместимости с конвертацией в HTML).\n"
    "8. Пустая строка между абзацами обязательна.\n\n"
    "ЗАПРЕЩЕНО:\n"
    "- Объяснять что ты изменил («Я убрал...»). Только новый текст поста.\n"
    "- Использовать AI-жаргон, инфоцыганские формулы, штампы.\n"
    "- Преувеличивать или придумывать цифры (16 лет, 40 лет, 3 года — есть; другие цифры выдумывать НЕЛЬЗЯ).\n"
    "- Делать пост менее личным, более «сухим официальным».\n\n"
    "Верни ТОЛЬКО обновлённый текст поста. Без комментариев, без префикса, "
    "без markdown-обёрток ```."
)


def _apply_tgpost_surg_edit(current_text: str, instruction: str) -> str:
    """Surgical edit of a TG-post — Sonnet receives current text + a
    free-form instruction, returns the edited post WITHOUT regenerating
    from scratch.

    Used in the idea→tg-post flow as a complement to Артёмов
    REGENERATE-style `tgpost:voice_edit` (`_generate_and_show`). The
    REGENERATE flow rebuilds the post from scratch via Opus each time
    using `extra_notes` as a hint — good when the user's instruction is
    deep («переделай для другой аудитории») but unsuitable when the
    user wants only a localized change («поменяй хук»). That deep
    perspective shift was caught on 13 May 2026: Артём asked «поменяй
    хук» and got a completely different post. This surgical helper
    closes that gap.

    Model: claude-sonnet-4-6 — same as tg_post_writer's polish pass.

    Raises ValueError on Claude failure / empty output.
    """
    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=3000,
            system=_TGPOST_SURG_EDITOR_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"ТЕКУЩИЙ ТЕКСТ ПОСТА:\n\n{current_text}\n\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"ИНСТРУКЦИЯ ПОЛЬЗОВАТЕЛЯ:\n«{instruction}»\n\n"
                        f"Верни обновлённый текст поста."
                    ),
                },
            ],
        )
        out = (resp.content[0].text if resp.content else "").strip()
    except Exception as e:
        raise ValueError(f"Sonnet edit call failed: {e}") from e
    if not out:
        raise ValueError("Sonnet returned empty edit")
    out = re.sub(r"^```(?:\w+)?\s*\n?", "", out)
    out = re.sub(r"\n?\s*```$", "", out)
    return out.strip()


def _polish_asr_transcript(transcript: str) -> str:
    """Best-effort ASR cleanup pass through Sonnet.

    Whisper «small» model (used by `subtitle_burner.transcribe_words`)
    frequently mis-hears Russian compound words and adds spurious prepositions
    («сосновый» → «с основы», «строительство» → «с строительство»). Those
    errors propagate downstream into hook-generation and TG-post — both built
    from this transcript. A narrow Sonnet pass fixes them while keeping the
    speaker's voice intact.

    Best-effort: any exception → return raw transcript unchanged. This must
    NEVER crash the Selfie pipeline.

    Used in: bot.py Selfie processing block, after `transcribe_words()`.
    """
    if not transcript or not transcript.strip():
        return transcript
    try:
        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            system=_ASR_POLISH_SYSTEM,
            messages=[
                {"role": "user", "content": f"Транскрипт:\n\n{transcript}"},
            ],
        )
        polished = response.content[0].text.strip()
        # Strip code-fence wrap if any
        polished = re.sub(r"^```|```$", "", polished, flags=re.MULTILINE).strip()
        # Sanity: way too short → keep raw (Sonnet hallucinated)
        if len(polished) < len(transcript) * 0.6:
            return transcript
        return polished
    except Exception as e:
        logger.warning(f"_polish_asr_transcript failed: {e}")
        return transcript


_HOOK_GEN_SYSTEM = (
    "Ты редактор хуков для коротких видео (Reels/Shorts/TikTok). "
    "Хук — это первая фраза/название, которая останавливает скролл. "
    "Аудитория — предприниматели 30+, не программисты. "
    "Стиль: уверенный, жёсткий по смыслу, без воды.\n\n"

    "САМОДОСТАТОЧНОСТЬ — ГЛАВНОЕ ПРАВИЛО (Fix 9 May 2026):\n"
    "Каждый хук должен быть понятен незнакомому зрителю БЕЗ контекста "
    "видео — он его ещё не смотрел, он только сейчас увидел этот хук в "
    "ленте. Если хук содержит слова «другое», «это», «такое», «одна "
    "вещь», «такая штука» БЕЗ раскрытия — это плохой хук.\n\n"
    "ПРАВИЛЬНО (раскрыто что именно):\n"
    "  • «Я выбрал лес вместо речки — и вот почему»\n"
    "  • «Большинство сливают глэмпинг ещё до первого гвоздя»\n"
    "  • «Там, где все строят глэмпинг, я бы не стал»\n"
    "НЕПРАВИЛЬНО (без раскрытия):\n"
    "  • «Я мог построить у речки. Но выбрал другое»  ← что за «другое»?\n"
    "  • «Это та самая ошибка, которую делают все»  ← что за ошибка?\n"
    "  • «Я понял одну вещь про глэмпинг»  ← что за вещь?\n"
    "  • «Эту штуку нельзя пропустить»  ← что за штука?\n\n"

    "Запрещено: вступления, подводки, 'Люди тратят', 'Сегодня многие', "
    "'Честно', 'Я думаю', 'Мне кажется', 'Давайте поговорим', 'В этом видео', "
    "'Многие недооценивают'. Запрещены грубые слова ('жрёт', 'жрать' и т.п.). "
    "Запрещены инфоцыганские формулы: 'секрет', 'формула', 'лайфхак', 'вайб'. "
    "Каждый хук 4-10 слов, цепляющий с первого слова."
)


def _generate_hook_options(
    source_text: str,
    exclude_hooks: list[str] | None = None,
    n: int = 5,
) -> list[str]:
    """Generate provocative hook options from a source (script or transcript).

    Reuses the system-prompt logic from `new_hook` callback so the hook
    style stays identical across pipelines (Artem's script flow + Maksim's
    selfie flow). Brand-specific tone comes from the active brand profile
    (we just append a brand context line to the user message — no need
    to fork the system prompt because the rules are universal).

    Returns up to `n` cleaned hook strings (no quotes, no numbering).
    Empty list if Claude failed.
    """
    exclude_hooks = exclude_hooks or []
    exclude_block = ""
    if exclude_hooks:
        exclude_block = (
            "\n\nУже предлагались (НЕ повторяй и не перефразируй их):\n"
            + "\n".join(f"- {h}" for h in exclude_hooks)
        )

    # Brand context — adds maksim-specific FACTS so hooks reference Maksim's
    # real business themes (картинг / глэмпинг / Тюмень / опыт), not generic
    # entrepreneur tropes. For default brand — empty, Artem's universal style.
    #
    # MAKSIM HOOK STYLE (refined 8 May 2026): pure open-loop. Hook MUST
    # promise a payoff but NOT deliver it — make viewer want to keep
    # watching/reading. Examples from his real viral Reels:
    #   "2 года назад я открыл глэмпинг. Это неприятно говорить, но…"  ← Reel 1, 1.5K likes
    #   "Эту ошибку совершают 80% новичков в сфере глэмпинга"          ← Reel 4
    #   "Глэмпинг — это не сказка"                                    ← Reel 1 caption hook
    # Anti-pattern (what Claude tends to do without explicit guidance):
    #   "Выбор участка решает судьбу глэмпинга"  ← это финал, не крюк.
    #   "Место решает 70% — домики только 30%"   ← это вывод, не зацепка.
    # Mechanic: every hook either (a) starts with personal anchor + ellipsis,
    # (b) names a specific sin/percentage that demands explanation, or
    # (c) ends with multitochie/«но...»/question implying revelation ahead.
    brand = _get_active_brand_name()
    brand_block = ""
    if brand == "maksim":
        brand_block = (
            "\n\nКонтекст автора: Максим Юмсунов, 40 лет, Тюмень. "
            "Картинг с 2009 (16 лет), глэмпинг с 2023 (3 года, 8-10 домов A-frame). "
            "Канал «Юмсунов | Про реальный бизнес». "
            "Хуки опираются на конкретные темы: глэмпинг-индустрия, картинг-операционка, "
            "найм, второй бизнес, опыт владельца, выбор партнёра, ошибки, провалы первого года.\n\n"
            "СТИЛЬ ХУКА — ОБЯЗАТЕЛЬНО OPEN-LOOP (обещай ответ, но не давай его в самом хуке). "
            "Зритель должен ХОТЕТЬ узнать продолжение.\n\n"
            "ЭТАЛОНЫ (это его реальные виральные хуки, НЕ копируй дословно — улавливай механику):\n"
            "  • «2 года назад я открыл глэмпинг. Это неприятно говорить, но…»\n"
            "  • «Эту ошибку совершают 80% новичков в сфере глэмпинга»\n"
            "  • «Глэмпинг — это не сказка»\n"
            "  • «Не открывай второй бизнес из любви к идее»\n"
            "  • «Я строил его 2 года. И всё это зря»\n\n"
            "АНТИ-ПАТТЕРНЫ (НЕ выдавай такие):\n"
            "  • «Выбор участка решает судьбу глэмпинга» — это вывод, а не крюк\n"
            "  • «Место решает 70% успеха» — это финальный тезис, нечего дальше смотреть\n"
            "  • «Шесть домиков и одна ошибка» — звучит как расчёт, не зацепка\n"
            "  • «Год искал землю — нашёл за день» — раскрывает ответ в самом хуке\n\n"
            "МЕХАНИКИ (используй одну из трёх):\n"
            "  1. Личный якорь + многоточие/«но»: «X лет назад я Y. И понял что Z…»\n"
            "  2. Конкретный греx + цифра: «80% делают эту ошибку», «99% не понимают»\n"
            "  3. Парадокс/слом: «Глэмпинг — это не сказка», «Не открывай из любви»\n\n"
            "Длина: 5-12 слов. Ни одно слово не должно быть водой."
        )

    user_msg = (
        f"Вот исходный текст (расшифровка живого видео или сценарий):\n\n{source_text[:2000]}\n\n"
        f"Придумай {n} альтернативных хуков-названий — мощных, цепляющих, "
        f"с первого слова останавливающих скролл. Каждый хук — на новой строке, "
        f"без нумерации, без кавычек, только текст."
        f"{brand_block}{exclude_block}"
    )

    try:
        response = claude.messages.create(
            model="claude-opus-4-7",
            max_tokens=500,
            system=_HOOK_GEN_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        hooks_text = response.content[0].text.strip()
        hooks = [
            line.strip().strip('"').strip("«»").strip("-").strip()
            for line in hooks_text.split("\n")
            if line.strip()
        ]
        # Drop too-short / numbered prefix artefacts
        hooks = [
            re.sub(r"^\d+[\.\)]\s*", "", h).strip()
            for h in hooks
            if len(h) > 5
        ]
        return hooks[:n]
    except Exception as e:
        logger.error(f"_generate_hook_options failed: {e}", exc_info=True)
        return []


def _selfie_hook_keyboard(hooks: list[str]) -> InlineKeyboardMarkup:
    """Inline keyboard for picking one of the proposed hooks for selfie title."""
    buttons = [
        [InlineKeyboardButton(
            (h[:60] + "…") if len(h) > 60 else h,
            callback_data=f"selfie_hook_pick:{i}",
        )]
        for i, h in enumerate(hooks)
    ]
    buttons.append([
        InlineKeyboardButton("🔄 Ещё варианты", callback_data="selfie_more_hooks"),
    ])
    buttons.append([
        InlineKeyboardButton("✏️ Свой текстом", callback_data="selfie_own_title"),
        InlineKeyboardButton("❌ Отмена", callback_data="cancel"),
    ])
    return InlineKeyboardMarkup(buttons)


async def _maksim_selfie_title_picker(
    message_or_query, context, user_id: int, transcript_text: str, first_sentence: str,
) -> None:
    """Title-picker injected into the selfie module: 5 Claude-Opus hooks.

    Called from selfie.handlers._finalize_with_cover when the new selfie module
    is ready to show the title-pick step. Replaces the module's built-in
    "Утвердить простое название" UI with a proper 5-hook picker — same UI
    Артём's bot used to show before the module port (8 June 2026).

    Existing callbacks selfie_hook_pick:N / selfie_more_hooks / selfie_own_title
    already live in handle_callback, so we only need to seed pending and render
    the keyboard.
    """
    # Generate 5 hooks (best-effort). On Claude failure fall back to first
    # sentence so the user always has at least one choice.
    hooks = await asyncio.to_thread(_generate_hook_options, transcript_text)
    if not hooks:
        fallback = first_sentence or (transcript_text.split(".")[0].strip()[:80] or "Живое видео")
        hooks = [fallback]

    # Seed pending the way the legacy selfie flow expected.
    pending[user_id]["selfie_hook_options"] = hooks
    pending[user_id]["selfie_shown_hooks"] = list(hooks)
    _save_pending(pending)

    wants_text = pending.get(user_id, {}).get("selfie_cover_wants_text")
    role_line = (
        "🎣 Варианты текста (нажми на один — ляжет на обложку И станет заголовком):"
        if wants_text
        else "🎣 Варианты названия (нажми на один — станет заголовком карточки):"
    )
    hooks_block = "\n".join(f"  • {h}" for h in hooks)
    text = (
        f"📝 Расшифровка:\n{transcript_text[:500]}"
        f"{'…' if len(transcript_text) > 500 else ''}\n\n"
        f"———\n"
        f"{role_line}\n\n"
        f"{hooks_block}\n\n"
        f"Или жми «🔄 Ещё варианты» / «✏️ Свой текстом»."
    )
    kb = _selfie_hook_keyboard(hooks)

    if hasattr(message_or_query, "edit_message_text"):
        await message_or_query.edit_message_text(text, reply_markup=kb)
    else:
        await message_or_query.reply_text(text, reply_markup=kb)


async def _apply_selfie_cover_text(context, user_id: int, chat_id: int, title: str) -> None:
    """Если юзер выбрал «с текстом» — наложить выбранный хук на селфи-обложку
    через generate_cover и показать результат. Источник текста — тот же хук,
    что стал заголовком (merged 10 июня: один текст вместо двух генераций).
    Мутирует data["selfie_cover"] на обложку-с-текстом. Best-effort.
    """
    data = pending.get(user_id, {})
    if not data.get("selfie_cover_wants_text"):
        return
    base = data.get("selfie_cover_base")
    if not base or not Path(base).exists():
        logger.info("[selfie] cover-text wanted but no base photo — skip overlay")
        return
    try:
        tmp_dir = Path(data.get("selfie_tmp_dir") or str(ASSETS_DIR))
        out_path = str(tmp_dir / "cover_with_text.jpg")
        await asyncio.to_thread(generate_cover, title, out_path, base)  # base = селфи-фото
        data["selfie_cover"] = out_path
        data["selfie_cover_text"] = title
        _save_pending(pending)
        try:
            with open(out_path, "rb") as ph:
                await context.bot.send_photo(
                    chat_id=chat_id, photo=ph,
                    caption=f"🖼 Обложка с текстом: «{title}»",
                )
        except Exception as e:
            logger.warning(f"[selfie] cover preview send failed: {e}")
    except Exception as e:
        logger.warning(f"[selfie] cover-text overlay failed: {e}")


async def _maksim_selfie_cover_text_step(
    message_or_query, context, user_id: int, cover_path: str, transcript: str,
) -> None:
    """Шаг «текст на обложку?» после выбора фото-обложки (инъекция в selfie).

    [✏️ С текстом] → 5 вариантов Opus → generate_cover поверх селфи-фото.
    [➡️ Без текста] → как есть, дальше к title-picker.
    Хранит базовое фото в selfie_cover_base для последующего наложения.
    """
    data = pending.get(user_id, {})
    data["state"] = "selfie_covertext"
    data["selfie_cover_base"] = cover_path  # фото без текста
    pending[user_id] = data
    _save_pending(pending)

    chat_id = (
        message_or_query.message.chat_id
        if hasattr(message_or_query, "message") and message_or_query.message
        else message_or_query.chat_id
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "🖼 Наложить текст на обложку?\n\n"
            "Сделаю обложку-превью с хук-заголовком поверх фото "
            "(как ютуб-thumbnail). Или оставить чистое фото."
        ),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ С текстом", callback_data="selfie_ct:on")],
            [InlineKeyboardButton("➡️ Без текста (чистое фото)", callback_data="selfie_ct:off")],
        ]),
    )


async def selfie_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Live video pipeline — user films on phone, bot adds subtitles."""
    user_id = update.effective_user.id
    logger.info(f"[user:{user_id}] /selfie")
    if user_id in pending:
        pending.pop(user_id, None)
    pending[user_id] = {"state": "selfie_waiting_video"}
    _save_pending(pending)
    await update.message.reply_text(
        "🎥 Живое видео\n\n"
        "Отправь видео, снятое на телефон.\n"
        "Я расшифрую речь, наложу субтитры в стиле CapCut, "
        "сгенерирую обложку и создам карточку в Notion.\n\n"
        "Просто отправь видеофайл."
    )


async def script_ready_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Accept ready script as-is, skip Claude generation."""
    user_id = update.effective_user.id
    logger.info(f"[user:{user_id}] /script")
    if user_id in pending:
        pending.pop(user_id, None)
    pending[user_id] = {"state": "script_ready"}
    _save_pending(pending)
    await update.message.reply_text(
        "📝 Готовый сценарий\n\n"
        "Отправь текст сценария — я возьму его как есть, без переписывания.\n"
        "Сразу покажу кнопки: утвердить → обложка → Notion."
    )


PIPELINE_STATUSES = ["Сценарий | озвучка", "Подбор скринкаст", "Аватар | генерации", "Монтаж", "Готово к публикации"]


async def cards_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show pipeline cards (in work, not ideas/published)."""
    logger.info(f"[user:{update.effective_user.id}] /cards")
    msg = await update.message.reply_text("Загружаю конвейер...")

    try:
        all_cards = await asyncio.to_thread(fetch_notion_cards, limit=200)
        pipeline_cards = [c for c in all_cards if c["status"] in PIPELINE_STATUSES]

        if not pipeline_cards:
            empty_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("💡 Новая идея", callback_data="cmd_new_idea")],
                [InlineKeyboardButton("📋 Посмотреть идеи", callback_data="cmd_ideas")],
            ])
            await msg.edit_text(
                "Конвейер пуст. Все карточки в «Идеях» или опубликованы.",
                reply_markup=empty_kb,
            )
            return

        by_status = {}
        for card in pipeline_cards:
            by_status.setdefault(card["status"], []).append(card)

        status_icons = {"Сценарий | озвучка": "🎙", "Подбор скринкаст": "🎥", "Аватар | генерации": "🤖", "Монтаж": "✂️", "Готово к публикации": "✅"}

        # Text list grouped by status
        text_parts = [f"🔄 Конвейер ({len(pipeline_cards)}):\n"]
        all_buttons = []
        for status in PIPELINE_STATUSES:
            cards_in_status = by_status.get(status, [])
            if not cards_in_status:
                continue
            icon = status_icons.get(status, "📝")
            text_parts.append(f"\n{icon} {status}:")
            for card in cards_in_status:
                text_parts.append(f"  • {card['title']}")
                short_title = card["title"][:30]
                all_buttons.append([InlineKeyboardButton(
                    f"{icon} {short_title}",
                    callback_data=f"notion_card:{card['id'][:30]}"
                )])

        full_text = "\n".join(text_parts)

        # If text fits in one message (< 4096), send everything together
        if len(full_text) <= 4000:
            keyboard = InlineKeyboardMarkup(all_buttons) if all_buttons else None
            await msg.edit_text(full_text, reply_markup=keyboard)
        else:
            # Text too long: send text without buttons, then buttons in a separate last message
            await msg.edit_text(full_text[:4000])
            keyboard = InlineKeyboardMarkup(all_buttons) if all_buttons else None
            await update.message.reply_text("👇 Выбери карточку:", reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Ошибка /cards: {e}", exc_info=True)
        await msg.edit_text(f"Ошибка загрузки: {e}")


async def ideas_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show idea backlog cards."""
    logger.info(f"[user:{update.effective_user.id}] /ideas")
    msg = await update.message.reply_text("Загружаю идеи...")

    try:
        all_cards = await asyncio.to_thread(fetch_notion_cards, limit=30)
        idea_cards = [c for c in all_cards if c["status"] == "Идеи | старт"]

        if not idea_cards:
            empty_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("💡 Новая идея", callback_data="cmd_new_idea")],
            ])
            await msg.edit_text(
                "Нет идей в бэклоге. Отправь новую идею!",
                reply_markup=empty_kb,
            )
            return

        text_parts = [f"💡 Бэклог идей ({len(idea_cards)}):\n"]
        for i, card in enumerate(idea_cards, 1):
            rubric_tag = f" [{card['rubric']}]" if card['rubric'] else ""
            text_parts.append(f"{i}. {card['title']}{rubric_tag}")

        buttons = []
        for card in idea_cards[:10]:
            short_title = card["title"][:30]
            buttons.append([InlineKeyboardButton(
                f"💡 {short_title}",
                callback_data=f"notion_card:{card['id'][:30]}"
            )])

        keyboard = InlineKeyboardMarkup(buttons) if buttons else None
        await msg.edit_text("\n".join(text_parts), reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Ошибка /ideas: {e}", exc_info=True)
        await msg.edit_text(f"Ошибка загрузки: {e}")


async def cards_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all cards grouped by status. Text in messages, all buttons at the bottom."""
    logger.info(f"[user:{update.effective_user.id}] /cards_all")
    msg = await update.message.reply_text("Загружаю все карточки...")

    try:
        all_cards = await asyncio.to_thread(fetch_notion_cards, limit=200)
        if not all_cards:
            await msg.edit_text("Нет карточек.")
            return

        by_status = {}
        for card in all_cards:
            status = card["status"] or "Без статуса"
            by_status.setdefault(status, []).append(card)

        status_icons = {
            "Идеи | старт": "💡",
            "Сценарий | озвучка": "🎙",
            "Подбор скринкаст": "🎥",
            "Аватар | генерации": "🤖",
            "Монтаж": "✂️",
            "Готово к публикации": "✅",
            "Опубликовано": "📢",
        }

        # Build text per status, send as separate messages (no buttons)
        all_buttons = []
        first_message = True
        for status in STATUSES:
            cards_in_status = by_status.get(status, [])
            if not cards_in_status:
                continue

            icon = status_icons.get(status, "📝")
            text_parts = [f"{icon} {status} ({len(cards_in_status)}):\n"]
            for card in cards_in_status:
                text_parts.append(f"  • {card['title']}")
                short_title = card["title"][:30]
                all_buttons.append([InlineKeyboardButton(
                    f"{icon} {short_title}",
                    callback_data=f"notion_card:{card['id'][:30]}"
                )])

            text = "\n".join(text_parts)
            if first_message:
                await msg.edit_text(text)
                first_message = False
            else:
                await update.message.reply_text(text)

        # All buttons in one last message at the bottom
        if all_buttons:
            keyboard = InlineKeyboardMarkup(all_buttons[:30])  # Telegram limit ~100 buttons
            await update.message.reply_text("👇 Выбери карточку:", reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Ошибка /cards_all: {e}", exc_info=True)
        await msg.edit_text(f"Ошибка: {e}")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show latest stats snapshot."""
    logger.info(f"[user:{update.effective_user.id}] /stats")
    latest = _get_latest_stats()
    if not latest:
        await update.message.reply_text(
            "Пока нет данных. Используй /update чтобы внести первый замер."
        )
        return
    await update.message.reply_text(_format_stats_report(latest))


async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start collecting stats from user."""
    user_id = update.effective_user.id
    logger.info(f"[user:{user_id}] /update")

    msg = await update.message.reply_text("📊 Собираю данные автоматически...")

    pending[user_id] = pending.get(user_id) or {}
    pending[user_id]["state"] = "stats_input"
    pending[user_id]["stats_draft"] = {}
    pending[user_id]["stats_step"] = 0

    auto_fetched = []

    # Auto-fetch Telegram
    tg_subs = await _fetch_telegram_subscribers(context.bot)
    if tg_subs is not None:
        pending[user_id]["stats_draft"]["telegram"] = {"subscribers": tg_subs}
        auto_fetched.append(f"✅ Telegram: {tg_subs} подп.")

    # Auto-fetch Instagram
    ig_subs = await asyncio.to_thread(_fetch_instagram_followers)
    if ig_subs is not None:
        pending[user_id]["stats_draft"]["instagram"] = {"subscribers": ig_subs}
        auto_fetched.append(f"✅ Instagram: {ig_subs} подп.")

    # Auto-fetch YouTube
    yt_subs = await asyncio.to_thread(_fetch_youtube_subscribers)
    if yt_subs is not None:
        pending[user_id]["stats_draft"]["youtube"] = {"subscribers": yt_subs}
        auto_fetched.append(f"✅ YouTube: {yt_subs} подп.")

    if auto_fetched:
        await msg.edit_text("📊 Автоматически собрано:\n" + "\n".join(auto_fetched) + "\n\nДозаполняю остальное...")
    else:
        await msg.edit_text("📊 Не удалось автоматически получить данные. Заполним вручную...")

    _save_pending(pending)
    await _ask_next_stat(update, context)


async def _ask_next_stat(update_or_query, context):
    """Ask for the next social network stats."""
    if hasattr(update_or_query, 'effective_user'):
        user_id = update_or_query.effective_user.id
    else:
        user_id = update_or_query.from_user.id

    data = pending.get(user_id, {})
    draft = data.get("stats_draft", {})
    step = data.get("stats_step", 0)

    # Skip networks that are already filled (auto-fetched)
    while step < len(SOCIAL_ORDER):
        key = SOCIAL_ORDER[step]
        if key in draft and "subscribers" in draft[key]:
            step += 1
            data["stats_step"] = step
            _save_pending(pending)
        else:
            break

    if step >= len(SOCIAL_ORDER):
        # All done — save snapshot
        await _save_stats_snapshot(update_or_query, context)
        return

    key = SOCIAL_ORDER[step]
    info = SOCIAL_CHANNELS[key]

    prompt_text = (
        f"📊 {info['name']}\n"
        f"({info['url']})\n\n"
        f"Сколько подписчиков? Напиши число.\n"
        f"(0 — если не используешь, пропуск — оставить 0)"
    )

    buttons = [[InlineKeyboardButton("⏭ Пропустить (0)", callback_data="stats_skip")]]

    if hasattr(update_or_query, 'message') and update_or_query.message:
        await update_or_query.message.reply_text(prompt_text, reply_markup=InlineKeyboardMarkup(buttons))
    elif hasattr(update_or_query, 'effective_message'):
        await update_or_query.effective_message.reply_text(prompt_text, reply_markup=InlineKeyboardMarkup(buttons))


async def _save_stats_snapshot(update_or_query, context):
    """Save completed stats snapshot."""
    if hasattr(update_or_query, 'effective_user'):
        user_id = update_or_query.effective_user.id
    else:
        user_id = update_or_query.from_user.id

    data = pending.get(user_id, {})
    draft = data.get("stats_draft", {})

    snapshot = {"date": datetime.now().strftime("%Y-%m-%d")}
    snapshot.update(draft)

    history = _load_stats()
    history.append(snapshot)
    _save_stats(history)

    # Clear state
    data["state"] = None
    data.pop("stats_draft", None)
    data.pop("stats_step", None)
    _save_pending(pending)

    report = _format_stats_report(snapshot)

    # If there's a previous snapshot, also show comparison
    if len(history) >= 2:
        report += "\n\n" + "═" * 25 + "\n\n"
        report += _format_comparison(history[-2], history[-1])

    # Send text report
    if hasattr(update_or_query, 'message') and update_or_query.message:
        chat_id = update_or_query.message.chat_id
        await update_or_query.message.reply_text(f"✅ Замер сохранён!\n\n{report}")
    elif hasattr(update_or_query, 'effective_message'):
        chat_id = update_or_query.effective_message.chat_id
        await update_or_query.effective_message.reply_text(f"✅ Замер сохранён!\n\n{report}")
    else:
        chat_id = None

    # Generate and send dashboard image
    if chat_id:
        try:
            dashboard_path = await asyncio.to_thread(generate_dashboard_image, history)
            with open(dashboard_path, "rb") as f:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=f,
                    caption=f"📊 Дашборд — неделя {len(history)}",
                )
        except Exception as e:
            logger.warning(f"Не удалось сгенерировать дашборд: {e}")

    logger.info(f"Замер сохранён: {snapshot}")


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show comparison report between first and latest snapshots."""
    logger.info(f"[user:{update.effective_user.id}] /report")
    history = _load_stats()

    if len(history) < 2:
        if len(history) == 1:
            await update.message.reply_text(
                _format_stats_report(history[0]) +
                "\n\nДля сравнения нужен минимум 2 замера. Сделай /update через несколько дней."
            )
        else:
            await update.message.reply_text("Нет данных. Используй /update для первого замера.")
        return

    # Show comparison between first and latest
    report = _format_comparison(history[0], history[-1])

    # If more than 2 snapshots, also show last period
    if len(history) > 2:
        report += "\n\n" + "═" * 25 + "\n"
        report += f"\n📊 Последний период:\n"
        report += _format_comparison(history[-2], history[-1])

    await update.message.reply_text(report)


def _calendar_keyboard() -> InlineKeyboardMarkup:
    """One-button footer under /calendar — entry into manual pub flow."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Отметить публикацию", callback_data="pub_add")],
    ])


def _pub_picker_keyboard(selected: dict[str, int]) -> InlineKeyboardMarkup:
    """Multi-select checkbox UI for manual pub entry.

    Each platform row tap cycles the counter: 0 → 1 → 2 → 3 → 0.
    Bottom row: Save / Cancel.
    ``selected`` — {code: count}; absence means 0.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for code, label in PLATFORM_DISPLAY:
        count = selected.get(code, 0)
        if count == 0:
            marker = "☐"
            tail = ""
        else:
            marker = "☑"
            tail = f"  ×{count}" if count > 1 else ""
        rows.append([
            InlineKeyboardButton(
                f"{marker}  {code} — {label}{tail}",
                callback_data=f"pub_toggle:{code}",
            )
        ])
    rows.append([
        InlineKeyboardButton("✅ Сохранить", callback_data="pub_save"),
        InlineKeyboardButton("❌ Отмена",    callback_data="pub_cancel"),
    ])
    return InlineKeyboardMarkup(rows)


async def calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show publication calendar — header + platforms + last 7 days."""
    logger.info(f"[user:{update.effective_user.id}] /calendar")
    try:
        grid = _format_calendar(days=7)
        await update.message.reply_text(
            f"<pre>{grid}</pre>",
            parse_mode="HTML",
            reply_markup=_calendar_keyboard(),
        )
    except Exception as e:
        logger.error(f"Ошибка /calendar: {e}", exc_info=True)
        await update.message.reply_text(f"Ошибка: {e}")


async def pub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually record publications for today.

    Shortcut forms:
      /pub          → opens the multi-select picker (same as calendar button)
      /pub TT       → +1 TikTok today, no dialog
      /pub TT IG    → +1 TikTok and +1 Instagram today
      /pub YT YT    → +2 YouTube today (repeat code to increment)
    """
    logger.info(f"[user:{update.effective_user.id}] /pub")
    args = (update.message.text or "").split()[1:]

    if not args:
        # No args → open picker (stateful multi-select).
        user_id = update.effective_user.id
        pending[user_id] = pending.get(user_id) or {}
        pending[user_id]["pub_draft"] = {}
        _save_pending(pending)
        await update.message.reply_text(
            "📅 Отметить публикацию за сегодня.\n\n"
            "Нажми на платформы, куда публиковался (повторный тап = +1). "
            "Затем «Сохранить».",
            reply_markup=_pub_picker_keyboard({}),
        )
        return

    # Fast path — codes passed on the command line.
    known = {code.lower(): code for code in PLATFORM_ORDER}
    added: dict[str, int] = {}
    unknown: list[str] = []
    for raw in args:
        code = known.get(raw.lower())
        if code is None:
            unknown.append(raw)
            continue
        added[code] = added.get(code, 0) + 1

    if unknown:
        known_str = ", ".join(PLATFORM_ORDER)
        await update.message.reply_text(
            f"❌ Неизвестные коды: {', '.join(unknown)}\n\n"
            f"Доступные: {known_str}"
        )
        return

    today = datetime.now().strftime("%Y-%m-%d")
    cal = _load_calendar()
    day = cal.get(today, {})
    for code, n in added.items():
        day[code] = day.get(code, 0) + n
    cal[today] = day
    _save_calendar(cal)

    summary = ", ".join(
        f"{code}" + (f"×{n}" if n > 1 else "") for code, n in added.items()
    )
    logger.info(f"[/pub] {today} +{summary}")
    await update.message.reply_text(
        f"✅ Записано на {today}: {summary}",
        reply_markup=_calendar_keyboard(),
    )


async def _selfie_finalize(update_or_query, context, user_id: int, title: str):
    """Finalize selfie pipeline: create Notion card, save files, show crosspost."""
    import shutil

    data = pending.get(user_id, {})
    selfie_tmp = data.get("selfie_tmp_dir")
    subtitled_path = data.get("selfie_subtitled")
    cover_path = data.get("selfie_cover")
    transcript = data.get("selfie_transcript", "")
    source_path = data.get("selfie_source")

    # Determine chat_id and how to send messages
    if hasattr(update_or_query, "message") and update_or_query.message:
        chat_id = update_or_query.message.chat_id
        send_msg = lambda text, **kw: context.bot.send_message(chat_id=chat_id, text=text, **kw)
    else:
        # CallbackQuery
        chat_id = update_or_query.message.chat_id
        send_msg = lambda text, **kw: context.bot.send_message(chat_id=chat_id, text=text, **kw)

    status_msg = await send_msg("📋 Создаю карточку в Notion...")

    try:
        # Brand-aware short CTA for the Notion "Призыв" property.
        # For Maksim — "Подписывайся на TG-канал @yumsunov_realbiz —
        # Юмсунов | Про реальный бизнес". Falls back to empty for default
        # brand (no preset channel). Note: this is the CTA stored in
        # Notion meta — it is NOT the post body. The actual TG-post body
        # is generated below via tg_post_writer and saved as a Notion
        # body block, not as a property.
        active_brand = _get_active_brand_name()
        brand_cfg = BRANDS.get(active_brand, BRANDS["default"])
        tg_handle = brand_cfg.get("telegram_channel_handle") or ""
        tg_display = brand_cfg.get("telegram_channel_display") or ""
        if tg_handle:
            if tg_display:
                short_cta = f"Подписывайся на TG-канал {tg_handle} — {tg_display}"
            else:
                short_cta = f"Подписывайся на TG-канал {tg_handle}"
        else:
            short_cta = ""

        card_data = {
            "title": title,
            "cta": short_cta,
            "rubric": "Свободный формат",
            "platforms": _default_platforms(),
            "format": ["Short video"],
        }

        # Загрузить обложку на публичный URL → передать в Notion, иначе она
        # сохранялась только в проект-папку и НЕ показывалась в карточке Notion
        # (Артём 9 июня: «зашёл в Notion — не увидел выбранную обложку»).
        cover_url = None
        if cover_path and Path(cover_path).exists():
            try:
                cover_url = await asyncio.to_thread(
                    save_media_permanent, str(cover_path), "cover"
                )
                logger.info(f"[selfie] Cover uploaded for Notion: {cover_url}")
            except Exception as e:
                logger.warning(f"[selfie] Cover upload for Notion failed: {e}")

        notion_url, notion_page_id = await asyncio.to_thread(
            create_notion_card, card_data, transcript, cover_url,
        )

        # Move to "Готово к публикации" since video is already done
        try:
            await asyncio.to_thread(update_notion_status, notion_page_id, "Готово к публикации")
        except Exception:
            logger.warning("[selfie] Failed to update Notion status")

        # Update pending with Notion data so _project_dir works
        data["card_data"] = card_data
        data["notion_url"] = notion_url
        data["notion_page_id"] = notion_page_id
        data["script"] = transcript
        data["state"] = "done"
        pending[user_id] = data
        _save_pending(pending)

        # TG-пост для канала больше НЕ генерируется автоматически (Артём
        # 9 июня: «написался без спроса + это давало паузу 1.5 мин»). Теперь
        # это кнопка «📰 TG-пост» на финальном экране — генерится по запросу
        # через tgpost_from_script (handler ниже). data["script"] = transcript
        # (выставлен выше) служит источником.

        # Save files to project directory
        proj = _project_dir(data)
        if proj:
            if source_path and Path(source_path).exists():
                shutil.copy2(source_path, str(proj / "source.mp4"))
                logger.info(f"[selfie] Saved source to {proj.name}/source.mp4")
            if subtitled_path and Path(subtitled_path).exists():
                shutil.copy2(subtitled_path, str(proj / "final_video.mp4"))
                logger.info(f"[selfie] Saved subtitled as {proj.name}/final_video.mp4")
            if cover_path and Path(cover_path).exists():
                shutil.copy2(cover_path, str(proj / "cover.jpg"))
                logger.info(f"[selfie] Saved cover to {proj.name}/cover.jpg")
            _save_text_to_project(data, "transcript.txt", transcript)

        # Order fix (8 May 2026): previously status_msg.edit_text() was called
        # AFTER send_video, but Telegram renders messages in chronological
        # creation order, not delivery order — and status_msg was created at
        # the start of _selfie_finalize ("Создаю карточку…"). So edit_text
        # left the buttons-message ABOVE the video in the chat, which felt
        # wrong (you want: action buttons UNDER the artifact they act on).
        # Fix: delete status_msg → send video → create a NEW message with
        # buttons. The new message is freshest → appears below video. Same
        # bug exists in Artem's bot in other handlers; port this pattern.
        try:
            await status_msg.delete()
        except Exception:
            pass

        # Send subtitled video preview FIRST (so subsequent messages appear below it)
        if subtitled_path and Path(subtitled_path).exists():
            try:
                with open(subtitled_path, "rb") as vf:
                    await context.bot.send_video(
                        chat_id=chat_id,
                        video=vf,
                        caption=f"🎥 {title}\n\nВидео с субтитрами готово.",
                        supports_streaming=True,
                    )
            except Exception as e:
                logger.warning(f"[selfie] Failed to send preview: {e}")

        card_prefix = notion_page_id[:8] if notion_page_id else ""

        # Финальный экран: описание для публикации + TG-пост (оба ОПЦИОНАЛЬНЫ,
        # по кнопке) перед кросспостингом. Раньше TG-пост писался автоматом
        # без спроса, а описание вообще пропускалось (Артём 9 июня).
        buttons = [
            [InlineKeyboardButton("📝 Описание для публикации", callback_data="gen_description")],
            [InlineKeyboardButton("📰 TG-пост по сценарию", callback_data="tgpost_from_script")],
            [InlineKeyboardButton("📢 Кросс-постинг", callback_data=f"crosspost:{card_prefix}")],
            [InlineKeyboardButton("📋 Карточка в Notion", url=notion_url)],
        ]
        if NOTION_GUIDES_DB:
            buttons.append([InlineKeyboardButton("📎 Создать гайд для подписчиков", callback_data="create_guide")])
        buttons.append([InlineKeyboardButton("✅ Готово", callback_data="finish")])

        # New message with buttons — created AFTER video → renders BELOW it
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"✅ Живое видео готово!\n\n"
                f"📋 {title}\n"
                f"🔗 {notion_url}\n"
                f"📊 Статус: Готово к публикации\n\n"
                f"Дальше по желанию:\n"
                f"• «📝 Описание» — подпись для Reels/Shorts/TikTok\n"
                f"• «📰 TG-пост» — длинный пост в канал\n"
                f"• «📢 Кросс-постинг» — публикация на площадках"
            ),
            reply_markup=InlineKeyboardMarkup(buttons),
        )

        # Clean up temp directory
        if selfie_tmp and Path(selfie_tmp).exists():
            try:
                shutil.rmtree(selfie_tmp)
                logger.info(f"[selfie] Cleaned up temp dir: {selfie_tmp}")
            except Exception as e:
                logger.warning(f"[selfie] Failed to clean temp dir: {e}")

    except Exception as e:
        logger.error(f"[selfie] Finalize error: {e}", exc_info=True)
        # status_msg might already be deleted in the success path above —
        # try edit first, fallback to a fresh send_message so the user always
        # sees the error.
        try:
            await status_msg.edit_text(f"Ошибка создания карточки: {e}")
        except Exception:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"Ошибка создания карточки: {e}",
                )
            except Exception:
                pass


async def _handle_heygen_photo_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Загрузка пользовательского фото и регистрация как persistent
    HeyGen Photo Avatar через v3 API. После успешной регистрации
    avatar_id сохраняется в pending data и пользователь переводится
    на экран выбора версии аватара (Avatar 3 / Avatar 4).

    Fallback: если HeyGen вернул лимит/ошибку регистрации, оставляем
    URL фото и предлагаем альтернативу — Image-to-Video через
    /heygen_test (per-video flow без регистрации).
    """
    import shutil  # locally — same pattern as other process_* helpers

    user_id = update.effective_user.id
    photo = update.message.photo[-1] if update.message.photo else None
    document = update.message.document
    if not photo and not document:
        await update.message.reply_text("Не вижу фото. Пришли картинку как фото или файл.")
        return

    msg = await update.message.reply_text("📸 Принял фото. Сохраняю...")
    data = pending.get(user_id, {})
    notion_id = data.get("notion_page_id")

    try:
        # Скачать файл из Telegram
        if photo:
            tg_file = await context.bot.get_file(photo.file_id)
            suffix = ".jpg"
        else:
            tg_file = await context.bot.get_file(document.file_id)
            suffix = os.path.splitext(document.file_name or "image.jpg")[1] or ".jpg"

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            await tg_file.download_to_drive(tmp.name)
            tmp_path = tmp.name

        # Сохранить копию в проект (если есть карточка) + публичный URL
        if notion_id:
            project_dir = _project_dir({"notion_page_id": notion_id, "card_data": {"title": data.get("title", "")}})
            if project_dir:
                project_dir.mkdir(parents=True, exist_ok=True)
                photo_save_path = project_dir / f"heygen_avatar_source{suffix}"
                shutil.copy2(tmp_path, str(photo_save_path))
                logger.info(f"[heygen_photo_register] saved to project: {photo_save_path}")

        public_url = await asyncio.to_thread(save_media_permanent, tmp_path, "heygen_avatar_src")
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

        # ВАЖНО (4 мая 2026): мы НЕ регистрируем avatar через POST /v3/avatars
        # потому что это упирается в лимит 3 photo-avatars на free tier
        # (HeyGen возвращает пустой ответ → JSONDecodeError). Вместо этого
        # сохраняем URL фото и используем POST /v3/videos type:"image"
        # (Image-to-Video) на этапе выбора версии. Цена та же ($0.0167/sec
        # Avatar 3, $0.05/sec Avatar 4), но без лимита и без зависимости
        # от состояния workspace в HeyGen Studio.
        data["heygen_custom_photo_url"] = public_url
        data["heygen_custom_avatar_id"] = None  # сбросить если был от старой логики
        data["heygen_look_key"] = "__custom__"
        data["state"] = None
        _save_pending(pending)

        # Image-to-Video — это технология Avatar IV. Произвольное фото
        # (без регистрации persistent avatar_id) анимирует ТОЛЬКО Avatar IV
        # (`use_avatar_iv_model=true`). Кнопка «Avatar 3» здесь раньше была —
        # давала голову без жестов. Убрана 20 мая 2026, оставлен только v4.
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "✨ Сгенерировать (Avatar IV — мимика, жесты)",
                callback_data="heygen_ver:v4",
            )],
            [InlineKeyboardButton("◀️ Назад к лукам", callback_data="heygen_looks")],
        ])
        await msg.edit_text(
            f"✅ Фото сохранено для генерации.\n\n"
            f"🔗 URL: `{public_url}`\n\n"
            f"Дальше HeyGen сделает Image-to-Video (фото + аудио → говорящее "
            f"видео) сразу, без регистрации persistent аватара. Это обходит "
            f"лимит photo-аватаров на free-tier.",
            parse_mode="Markdown",
            reply_markup=kb,
        )
    except Exception as e:
        logger.error(f"[heygen_photo_register] flow crashed: {e}", exc_info=True)
        try:
            await msg.edit_text(f"❌ Ошибка обработки: {e}")
        except Exception:
            pass
        pending[user_id]["state"] = None
        _save_pending(pending)


async def _handle_cover_pool_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принять фото от пользователя и сохранить в библиотеку обложек
    активного бренда (`assets/avatars/<brand>/`).

    Имя файла — следующий свободный номер + хэш для уникальности.
    Например: `02_uploaded_a3f2b1.png`. После сохранения подтверждаем
    пользователю и оставляем его в текущем cover-flow (бот не уводит
    автоматически — пользователь сам жмёт «📷 Другое фото» и получает
    свежее фото в пуле).
    """
    user_id = update.effective_user.id
    photo = update.message.photo[-1] if update.message.photo else None
    document = update.message.document
    if not photo and not document:
        await update.message.reply_text("Не вижу фото. Пришли картинку как фото или файл.")
        return

    msg = await update.message.reply_text("📸 Принял фото. Сохраняю в библиотеку...")
    data = pending.get(user_id, {})
    target_brand = (data.get("cover_pool_target_brand") or _get_active_brand_name()).strip()
    pool_dir = _avatars_dir_for_brand(target_brand)
    pool_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Скачать файл
        if photo:
            tg_file = await context.bot.get_file(photo.file_id)
            suffix = ".jpg"
        else:
            tg_file = await context.bot.get_file(document.file_id)
            suffix = os.path.splitext(document.file_name or "image.png")[1] or ".png"

        # Подобрать следующий свободный номер NN_uploaded_*
        import hashlib
        existing_nums = []
        for f in pool_dir.iterdir():
            if f.suffix.lower() in (".jpg", ".jpeg", ".png"):
                m = re.match(r"^(\d+)_", f.name)
                if m:
                    existing_nums.append(int(m.group(1)))
        next_num = (max(existing_nums) + 1) if existing_nums else 1
        ts_hash = hashlib.md5(str(time.time()).encode()).hexdigest()[:6]
        filename = f"{next_num:02d}_uploaded_{ts_hash}{suffix.lower()}"
        target_path = pool_dir / filename

        await tg_file.download_to_drive(str(target_path))
        target_path.chmod(0o644)
        size_kb = target_path.stat().st_size / 1024

        # Очистить state — мы вернулись в обычный режим
        pending[user_id]["state"] = None
        pending[user_id].pop("cover_pool_target_brand", None)
        _save_pending(pending)

        logger.info(
            f"[cover_pool_upload] user={user_id} brand={target_brand} "
            f"saved={target_path} ({size_kb:.0f} KB)"
        )

        # Подсказка как использовать новое фото
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📷 Сменить фото на текущей обложке", callback_data="change_avatar")],
        ])
        await msg.edit_text(
            f"✅ Сохранено: `{filename}` ({size_kb:.0f} KB)\n\n"
            f"📁 Бренд: **{target_brand}**\n"
            f"📁 Папка: `assets/avatars/{target_brand if target_brand != 'default' else ''}`\n\n"
            f"Теперь это фото в пуле обложек. Жми кнопку ниже чтобы пересобрать "
            f"текущую обложку с новым фото — оно появится среди вариантов "
            f"«🎲 Другое фото» / «🔢 По номеру».",
            parse_mode="Markdown",
            reply_markup=kb,
        )
    except Exception as e:
        logger.error(f"[cover_pool_upload] failed: {e}", exc_info=True)
        try:
            await msg.edit_text(f"❌ Ошибка сохранения: {e}")
        except Exception:
            pass
        pending[user_id]["state"] = None
        _save_pending(pending)


async def process_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo uploads. Only active during the «📥 Готовые материалы»
    flow (state=broll_ready_material) — elsewhere photos are ignored so we
    don't accidentally swallow memes or unrelated screenshots.
    """
    user_id = update.effective_user.id
    _restore_brand_from_pending(user_id)

    state = pending.get(user_id, {}).get("state")

    # ─── Selfie v2 — cover upload routed to the dedicated module ─────
    if state == "selfie_cover_uploading":
        if await selfie_handlers.handle_cover_photo_message(update, context):
            return

    # ─── Selfie v2 / Pipeline 2 — B-roll photo upload ─────
    if state == "selfie_broll_uploading_photo":
        if await selfie_handlers.handle_broll_upload_photo_message(update, context):
            return

    # ─── Maksim TG-post photo attachment via reply ───────────────────
    # When the user clicks «📤 Прислать сейчас» in the TG-photo menu,
    # `tgphoto_awaiting_reply` is set on their pending. Subsequent photo
    # uploads (single or album) are appended to `selfie_tg_photos` until
    # the user explicitly clicks «Готово» (which clears the flag via the
    # tgphoto_menu re-render).
    # Telegram sends each photo of an album as a SEPARATE update (no
    # built-in batching), so we just append per-message and reply with
    # a quiet "added #N" — no spam-album-confirmation across 10 messages.
    if pending.get(user_id, {}).get("tgphoto_awaiting_reply"):
        try:
            photos_arr = update.message.photo or []
            if not photos_arr:
                return
            best = photos_arr[-1]  # highest resolution
            tg_photos = pending[user_id].get("selfie_tg_photos", []) or []
            if len(tg_photos) >= 10:
                await update.message.reply_text(
                    "⚠️ Уже 10 фото — это лимит Telegram для одного поста. "
                    "Открой меню фото и сними лишние, если нужно добавить ещё."
                )
                return
            tg_photos.append({"source": "telegram", "file_id": best.file_id})
            pending[user_id]["selfie_tg_photos"] = tg_photos
            _save_pending(pending)
            await update.message.reply_text(
                f"✅ Фото #{len(tg_photos)} добавлено."
                + (" Ещё 1 можно." if len(tg_photos) == 9 else "")
            )
        except Exception as e:
            logger.error(f"[tgphoto] reply intake error: {e}")
            await update.message.reply_text(f"❌ Ошибка приёма фото: {e}")
        return

    # /heygen_test photo step — наш модуль ждёт фото для теста.
    if state == HEYGEN_TEST_STATE_PHOTO:
        await consume_heygen_test_photo(update, context)
        return

    # Custom photo avatar registration: фото для создания нового HeyGen
    # photo avatar в shoes / любом бренде с on-demand аватаром.
    if state == "heygen_photo_register_waiting":
        await _handle_heygen_photo_register(update, context)
        return

    # Cover-pool upload: пользователь добавляет своё фото в библиотеку
    # обложек активного бренда. Сохраняется в assets/avatars/<brand>/
    # с автонумерацией.
    if state == "cover_pool_upload_waiting":
        await _handle_cover_pool_upload(update, context)
        return

    if state != "broll_ready_material":
        # Politely remind the user how to enter ready-materials mode,
        # but only once per session (avoid spam on album uploads).
        if not pending.get(user_id, {}).get("_photo_hint_shown"):
            pending.setdefault(user_id, {})["_photo_hint_shown"] = True
            _save_pending(pending)
            try:
                await update.message.reply_text(
                    "📸 Чтобы сохранить фото в проект — сначала открой карточку → "
                    "«🎬 Видеоряд (B-roll)» → «📥 Готовые материалы». "
                    "Тогда присылай фото пачкой, я всё сложу в папку проекта."
                )
            except Exception:
                pass
        return

    data = pending[user_id]
    msg = await update.message.reply_text("📥 Сохраняю фото...")
    ok, reply_text = await _save_ready_photo(update, context, data)
    try:
        await msg.edit_text(
            reply_text + "\n\n_Ещё фото/видео или «✅ Готово» из меню._",
            parse_mode="Markdown",
        )
    except Exception:
        await update.message.reply_text(reply_text)


def _render_tgpost_html(post_text: str) -> str:
    """Convert raw post text (with markdown **bold**) to HTML-safe view.

    1. HTML-escape any < > & in the source (defensive — LLM may include
       comparison operators or HTML-like chars).
    2. Convert markdown **bold** → HTML <b>bold</b>.

    Order matters: escape first (otherwise <b> would become &lt;b&gt;),
    then substitute markdown. Used in idea→tgpost flow as a local rendering
    helper — bypasses Артёмов `_safe_preview` which intentionally keeps
    `**` literals (his design for Markdown publish flow).
    """
    escaped = html_mod.escape(post_text)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)


def _idea_tgpost_keyboard(idx: int, photos_count: int = 0) -> InlineKeyboardMarkup:
    """Extended `_kb_review` for idea→tgpost flow.

    Lives only in idea-flow — Артёмов `/tgpost` flow uses vanilla
    `_kb_review`. Differences vs Артёмов keyboard:
      * «📷 Фото к посту (N)» — entry to existing tgphoto_menu (bank +
        ad-hoc reply). Returns back to THIS post screen.
      * «✏️ Точечная правка» (Sonnet) next to «🎙️ Правки целиком» (Opus).
      * «✅ Опубликовать в канал» — uses MY publish path with photos,
        not Артёмов text-only `tgpost:publish`.

    13 May 2026 — added photos + own publish per Артём:
      «Единственное, нет возможности прикрепить фотографию, над которой
      мы работали до этого».
    """
    photo_label = (
        f"📷 Фото к посту ({photos_count} выбрано)"
        if photos_count > 0 else "📷 Прикрепить фото"
    )
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "✅ Опубликовать в канал",
            callback_data=f"idea_tgpost_publish:{idx}",
        )],
        [InlineKeyboardButton(photo_label, callback_data=f"idea_tgpost_photos:{idx}")],
        [InlineKeyboardButton("🔄 Перегенерировать", callback_data="tgpost:regen")],
        [InlineKeyboardButton("🎙️ Правки целиком (Opus)", callback_data="tgpost:voice_edit")],
        [InlineKeyboardButton(
            "✏️ Точечная правка (Sonnet)",
            callback_data=f"tgpost_surg_edit_start:{idx}",
        )],
        [InlineKeyboardButton("📥 Сохранить в Notion", callback_data="tgpost:notion")],
        [InlineKeyboardButton("❌ Отмена", callback_data="tgpost:cancel")],
    ])


def _tgpost_script_keyboard(photos_count: int = 0) -> InlineKeyboardMarkup:
    """Клавиатура экрана «📰 TG-пост по сценарию» (селфи-финал).

    Раньше тут было только [Опубликовать][Перегенерировать] — без выбора
    фото. Артём 10 июня: в TG хочу СВОЮ фотографию (не ту, что в Instagram).
    Подключаем тот же фото-механизм, что в idea→tgpost (`tgphoto_*` меню:
    своё фото из чата + банк на сервере по категориям). Единый источник —
    `selfie_tg_photos` + `_render_tgphoto_menu`, ничего нового.
    """
    photo_label = (
        f"📷 Фото к посту ({photos_count} выбрано)"
        if photos_count > 0 else "📷 Прикрепить фото"
    )
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Опубликовать в TG-канал", callback_data="tgpost_from_script:publish")],
        [InlineKeyboardButton(photo_label, callback_data="tgpost_script_photos")],
        [InlineKeyboardButton("🔄 Перегенерировать", callback_data="tgpost_from_script")],
    ])


def _clear_carousel_surg_state(user_id: int) -> None:
    """Clear `awaiting_carousel_surg_edit` state for user (if set).

    Called by carousel approve/regen/cancel callbacks so a leftover
    surgical-edit state doesn't capture the user's next message.
    """
    data = pending.get(user_id)
    if data and data.get("state") == "awaiting_carousel_surg_edit":
        data["state"] = None
        pending[user_id] = data
        _save_pending(pending)


# ─── F4 (26 May 2026): carousel seed session-объект ──────────────────────
# Раньше seed-поля (carousel_seed_text, carousel_seed_card_id, ...) висели
# в pending[uid] плоско. Старая кнопка шаблона M1 от карточки A могла
# нажаться ПОСЛЕ того как юзер открыл карточку B и заменил seed — handler
# не понимал что callback устарел, и создавал карусель по B вместо A
# (ChatGPT review M1, M2).
#
# Решение: единый объект `pending[uid]["carousel_seed"]` с session_id (8 chars
# token_urlsafe) + created_at. Кнопка шаблона включает session_id в callback:
# `carousel_tpl:M2:<session_id>`. Handler парсит session, валидирует против
# seed.session_id — mismatch / TTL>30мин → stale message, не запускает Opus.

_CAROUSEL_SEED_TTL_SEC = 30 * 60  # 30 минут


def _make_carousel_seed(card_id: str, card_url: str, text: str) -> dict:
    """Создать seed-объект с session_id + timestamp.

    Каждый клик «🎨 В карусель» из карточки делает НОВЫЙ seed (новый session_id),
    даже если карточка та же — старые кнопки шаблона перестают работать.
    """
    import secrets
    import time
    return {
        "session_id": secrets.token_urlsafe(8),
        "card_id": card_id,
        "card_url": card_url,
        "text": text,
        "created_at": time.time(),
    }


def _seed_is_stale(seed: dict | None) -> bool:
    """Seed считается устаревшим если нет created_at или прошло >TTL."""
    import time
    if not seed or not isinstance(seed, dict):
        return True
    created = seed.get("created_at")
    if not isinstance(created, (int, float)):
        return True
    return (time.time() - created) > _CAROUSEL_SEED_TTL_SEC


async def _handle_tgpost_surg_edit_instruction(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    instruction: str,
) -> None:
    """Apply user-supplied surgical edit instruction to the current TG-post.

    Called from `process_idea` (text intake) and `process_voice` (voice
    intake) when `pending[user_id]["state"] == "idea_post_surg_edit"`.

    Sources current text from `data["tgpost"]["last_post"]` (Артёмов
    canonical storage), not from a parallel field. After edit, both
    `tgpost.last_post` and the displayed message are updated; the user
    can keep iterating or switch back to Артёмов regenerate/publish.

    Hard cap: 10 iterations to prevent runaway.
    """
    user_id = update.effective_user.id
    data = pending.get(user_id) or {}
    tg = data.get("tgpost") or {}
    current_text = (tg.get("last_post") or "").strip()
    iters = int(data.get("tgpost_surg_iterations", 0) or 0)

    if not current_text:
        await update.message.reply_text(
            "⚠️ Не нашёл текущий пост в pending. Сгенерируй заново через «🎰 Банк идей»."
        )
        data["state"] = None
        pending[user_id] = data
        _save_pending(pending)
        return

    if iters >= 10:
        await update.message.reply_text(
            "⚠️ Достигнут лимит точечных правок (10). Нажми «Готово» или «🔄 Перегенерировать»."
        )
        return

    status_msg = await update.message.reply_text(
        f"✏️ Применяю точечную правку #{iters + 1}…"
    )

    try:
        new_text = await asyncio.to_thread(
            _apply_tgpost_surg_edit, current_text, instruction,
        )
    except ValueError as e:
        logger.warning(f"[tgpost surg edit] failed: {e}")
        try:
            await status_msg.edit_text(
                f"❌ Не смог применить правку: {e}\n"
                "Попробуй другой формулировкой или нажми «Отмена»."
            )
        except Exception:
            pass
        return

    # Update Артёмов canonical state + my iteration counter
    tg["last_post"] = new_text
    data["tgpost"] = tg
    data["tgpost_surg_iterations"] = iters + 1
    pending[user_id] = data
    _save_pending(pending)

    # Render new post — local HTML conversion (** → <b>)
    post_html = _render_tgpost_html(new_text)

    idx = data.get("tgpost_surg_idea_idx", 0)
    photos_count = len(data.get("selfie_tg_photos") or [])
    try:
        await status_msg.edit_text(
            f"✏️ <b>Точечная правка #{iters + 1} применена</b>\n\n{post_html}",
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=_idea_tgpost_keyboard(idx, photos_count),
        )
    except Exception as e:
        # Fallback: HTML parse can still fail on edge cases (e.g. very
        # long lines, weird unicode). Send plain text with same keyboard.
        logger.debug(f"[tgpost surg edit] HTML render fallback: {e}")
        await update.message.reply_text(
            new_text, reply_markup=_idea_tgpost_keyboard(idx, photos_count),
        )


async def process_idea(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages and video uploads."""
    # Skip commands — let CommandHandler handle them
    if update.message.text and update.message.text.startswith("/"):
        return
    user_id = update.effective_user.id
    # Restore cached card brand into _brand_ctx so downstream generation
    # (script, cover, voice, avatar, assembly) resolves the right profile
    # even after a bot restart, when global _active_brand has been lost.
    _restore_brand_from_pending(user_id)

    idea_text = update.message.text or ""
    state = pending.get(user_id, {}).get("state")

    # ─── TG-post surgical editor: text instruction intake ───
    # State set by «✏️ Точечная правка» button (idea-flow only).
    # Separate from Артёмов state `tgpost_wait_edit` which uses
    # regenerate-with-notes — that one is handled by `handle_tgpost_text`
    # via is_tgpost_state() further down the pipeline.
    if state == "idea_post_surg_edit" and idea_text and not idea_text.startswith("/"):
        if idea_text.strip().lower() in ("отмена", "отменить", "выйти", "стоп"):
            data = pending.get(user_id) or {}
            data["state"] = None
            pending[user_id] = data
            _save_pending(pending)
            await update.message.reply_text("✖️ Точечная правка отменена.")
            return
        await _handle_tgpost_surg_edit_instruction(update, context, idea_text.strip())
        return

    # ─── Carousel theme intake (Pipeline #6) ───
    # State set by `carousel_tpl:<X>` callback after user picked template.
    # User types a free-form theme → render carousel preview via carousel.handlers.
    if state == "awaiting_carousel_theme" and idea_text and not idea_text.startswith("/"):
        if idea_text.strip().lower() in ("отмена", "отменить", "выйти", "стоп"):
            data = pending.get(user_id) or {}
            data["state"] = None
            data.pop("carousel_template", None)
            pending[user_id] = data
            _save_pending(pending)
            await update.message.reply_text("✖️ Карусель отменена.")
            return
        # Capture template choice + clear state.
        data = pending.get(user_id) or {}
        chosen_tpl = data.get("carousel_template")
        data["state"] = None
        data.pop("carousel_template", None)
        pending[user_id] = data
        _save_pending(pending)
        try:
            from carousel.handlers import generate_carousel_preview
        except Exception as e:
            logger.error(f"[carousel] import failed: {e}", exc_info=True)
            await update.message.reply_text(
                f"❌ Модуль карусели не загружен: {e}",
            )
            return
        await generate_carousel_preview(
            update, context, claude,
            theme=idea_text.strip(),
            n_slides=None,    # инфер из темы: «топ-K» → K+2, иначе 7
            chat_id=update.effective_chat.id,
            notion_url=None,
            template=chosen_tpl,
        )
        return

    # ─── Carousel surgical-edit intake (Pipeline #6) ───
    # State set by `carousel_surg_edit` callback. User types an instruction
    # → Sonnet edits only the requested part of the draft, re-shows preview.
    if state == "awaiting_carousel_surg_edit" and idea_text and not idea_text.startswith("/"):
        if idea_text.strip().lower() in ("отмена", "отменить", "выйти", "стоп"):
            data = pending.get(user_id) or {}
            data["state"] = None
            pending[user_id] = data
            _save_pending(pending)
            await update.message.reply_text(
                "↩️ Точечная правка отменена — сценарий не изменился.",
            )
            return
        # One-shot: clear state, apply edit. Next edit = click button again.
        data = pending.get(user_id) or {}
        data["state"] = None
        pending[user_id] = data
        _save_pending(pending)
        try:
            from carousel.handlers import apply_carousel_surgical_edit
        except Exception as e:
            logger.error(f"[carousel] import failed: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Модуль карусели не загружен: {e}")
            return
        await apply_carousel_surgical_edit(
            update, context, claude, idea_text.strip(),
        )
        return

    # Reply-keyboard shortcuts (persistent buttons from _main_reply_keyboard).
    # Each button's text is mapped to an existing command so Artem doesn't have
    # to remember slash syntax. We only intercept when the text matches EXACTLY
    # and there's no active state — mid-flow the text is still passed through
    # to the normal pipeline.
    _reply_button_map = {
        "🎥 Селфи": selfie_command,
        "📋 Карточки": lambda u, c: cards_command(u, c),
        "📝 TG-пост": None,  # handled below — needs tgpost_command from tg_post_handlers
        "🏷 Бренд": brand_command,
        "❓ Помощь": help_command,
    }
    if idea_text in _reply_button_map and not state:
        if idea_text == "🎬 Новая идея":
            pass  # fall through to the idea-entry hint below
        elif idea_text == "📝 TG-пост":
            # tgpost_command lives in tg_post_handlers, invoked via /tgpost
            from tg_post_handlers import tgpost_command
            await tgpost_command(update, context)
            return
        elif _reply_button_map[idea_text] is not None:
            await _reply_button_map[idea_text](update, context)
            return

    if idea_text == "🎬 Новая идея" and not state:
        active = _get_active_brand_name()
        await update.message.reply_text(
            f"💡 Надиктуй или напиши идею ролика.\n\n"
            f"🏷 Текущий бренд: *{active}* "
            f"(смени через 🏷 Бренд если нужно).",
            parse_mode="Markdown",
        )
        return

    # TG-post flow (команда /tgpost) — перехватываем текст, если пользователь
    # сейчас отвечает на вопрос генератора постов.
    if is_tgpost_state(state) and idea_text:
        consumed = await handle_tgpost_text(update, context, idea_text)
        if consumed:
            return

    # fal.ai flows (/image, /video) — перехватываем текст как промпт генерации
    if is_fal_state(state) and idea_text:
        consumed = await consume_fal_prompt(update, context, idea_text)
        if consumed:
            return

    # Video file received — handle upload states first
    video_file = update.message.video or update.message.document
    is_video = video_file and (update.message.video or (video_file.mime_type and video_file.mime_type.startswith("video/")))
    if is_video:
        logger.info(f"[user:{user_id}] Видеофайл | state={state}")
    else:
        logger.info(f"[user:{user_id}] Текст: {idea_text[:80]}... | state={state}")

    # Ready script — use text as-is, skip Claude generation
    if user_id in pending and pending[user_id].get("state") == "script_ready":
        script_text = idea_text.strip()
        msg = await update.message.reply_text("📋 Структурирую для Notion...")
        try:
            # Get card structure from Claude (title, rubric etc.) but keep script text as-is
            struct_response = claude.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=512,
                system=_build_structure_prompt(),
                messages=[{"role": "user", "content": f"Идея: {script_text[:200]}\n\nСценарий: {script_text}"}],
            )
            raw_struct = struct_response.content[0].text.strip()
            if raw_struct.startswith("```"):
                raw_struct = raw_struct.split("\n", 1)[1]
                if raw_struct.endswith("```"):
                    raw_struct = raw_struct[:-3]
                raw_struct = raw_struct.strip()

            card_data = json.loads(raw_struct)
            if card_data.get("rubric") not in _brand_rubrics():
                card_data["rubric"] = "Свободный формат"
            card_data["platforms"] = _default_platforms()
            card_data["format"] = [f for f in card_data.get("format", []) if f in FORMATS] or ["Short video"]
            lines = script_text.strip().split("\n")
            card_data["cta"] = lines[-1] if lines else ""

            pending[user_id] = {
                "card_data": card_data,
                "script": script_text,
                "idea": script_text[:200],
            }
            _save_pending(pending)

            char_count = len(script_text)
            preview = (
                f"📝 СЦЕНАРИЙ (готовый):\n\n"
                f"{script_text}\n\n"
                f"———\n"
                f"📊 {char_count} символов\n"
            )
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Утвердить → обложка", callback_data="approve"),
                    InlineKeyboardButton("🔄 Переписать", callback_data="rewrite"),
                ],
                [
                    InlineKeyboardButton("✏️ Внести правки", callback_data="edit_mode"),
                    InlineKeyboardButton("✏️ Другой хук", callback_data="new_hook"),
                ],
                [InlineKeyboardButton("💾 Отложить как идею", callback_data="save_to_notion")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
            ])
            await msg.edit_text(preview, reply_markup=keyboard)
        except Exception as e:
            logger.error(f"Ошибка /script: {e}", exc_info=True)
            await msg.edit_text(f"Ошибка: {e}")
        return

    # Quick Notion card creation
    if user_id in pending and pending[user_id].get("state") == "notion_quick":
        pending[user_id]["state"] = None
        _save_pending(pending)
        msg = await update.message.reply_text("📋 Создаю карточку в Notion...")
        try:
            # Use first line as title, rest as description
            lines = idea_text.strip().split("\n", 1)
            title = lines[0].strip()[:80]
            description = lines[1].strip() if len(lines) > 1 else idea_text.strip()

            card_data = {"title": title, "cta": ""}
            notion_url, notion_page_id = await asyncio.to_thread(create_notion_card, card_data, description)

            pending.pop(user_id, None)
            _save_pending(pending)

            await msg.edit_text(
                f"✅ Карточка создана!\n\n"
                f"📋 {title}\n"
                f"📋 Notion: {notion_url}\n"
                f"📊 Статус: Идеи | старт"
            )
        except Exception as e:
            logger.error(f"Ошибка создания карточки: {e}", exc_info=True)
            await msg.edit_text(f"Ошибка: {e}")
        return

    # Quick voiceover
    if user_id in pending and pending[user_id].get("state") == "voice_quick":
        pending[user_id]["state"] = "voice_editing"
        pending[user_id]["script"] = idea_text.strip()
        _save_pending(pending)
        msg = await update.message.reply_text("🎙 Применяю интонацию и разбиваю на части...")
        try:
            # Run intonation on the FULL script once — Claude sees full context
            # (paragraph flow, emphasis placement, stress distribution) and then
            # we split the processed text into voice parts.
            full_processed = _prepare_tts_intonation(idea_text.strip())
            logger.info(
                f"TTS full_processed ({len(full_processed)} chars):\n{full_processed}"
            )
            _tp = _voice_target_parts(_get_active_brand().get("eleven_model_id"), full_processed)
            parts = split_script_to_parts(full_processed, target_parts=_tp)
            logger.info(
                f"TTS split (target_parts={_tp}): "
                + " | ".join(f"[{i}]({len(p)}) {p[:40]}…{p[-25:]}" for i, p in enumerate(parts))
            )
            pending[user_id]["voice_parts"] = parts
            pending[user_id]["voice_approved"] = [False] * len(parts)
            _save_pending(pending)
            await msg.edit_text(f"🎙 Интонация применена, озвучиваю {len(parts)} частей...")

            for i, part_text in enumerate(parts):
                voice_path = str(ASSETS_DIR / f"voice_part_{i}.mp3")
                generate_voiceover(part_text, voice_path, skip_intonation=True)

                # Save voice file to card directory and project folder
                notion_id = pending[user_id].get("notion_page_id")
                if notion_id:
                    _save_voice_file(notion_id, i, voice_path)
                _save_to_project(pending[user_id], f"voice_part_{i}.mp3", voice_path)

                with open(voice_path, "rb") as audio_file:
                    await update.message.reply_audio(
                        audio=audio_file,
                        title=f"Часть {i+1}/{len(parts)}",
                        caption=f"🎙 Часть {i+1}/{len(parts)}:\n\n«{part_text}»",
                    )

            # Save voice metadata to card directory
            notion_id = pending[user_id].get("notion_page_id")
            if notion_id:
                _save_voice_meta(notion_id, parts, pending[user_id].get("voice_approved", []))

            await msg.edit_text(
                _voice_panel_text(pending[user_id]),
                reply_markup=_voice_panel_keyboard(pending[user_id]),
            )
        except Exception as e:
            logger.error(f"Ошибка озвучки: {e}", exc_info=True)
            await msg.edit_text(f"Ошибка: {e}")
        return

    # If user is entering stats
    if user_id in pending and pending[user_id].get("state") == "stats_input":
        data = pending[user_id]
        step = data.get("stats_step", 0)
        draft = data.get("stats_draft", {})

        if step < len(SOCIAL_ORDER):
            key = SOCIAL_ORDER[step]

            try:
                subs = int(idea_text.strip().split()[0])
                draft[key] = {"subscribers": subs}

                data["stats_step"] = step + 1
                _save_pending(pending)
                await _ask_next_stat(update, context)
            except (ValueError, IndexError):
                await update.message.reply_text("Напиши одно число — количество подписчиков.")
        return

    # If user is adding a note to a Notion card
    if user_id in pending and pending[user_id].get("state") == "notion_note":
        card_id = pending[user_id].get("notion_edit_card")
        card_title = pending[user_id].get("notion_edit_title", "")
        if card_id:
            try:
                add_notion_note(card_id, idea_text)
                pending[user_id]["state"] = None
                _save_pending(pending)
                await update.message.reply_text(
                    f"✅ Заметка добавлена!\n\n"
                    f"📋 {card_title}\n"
                    f"📝 «{idea_text}»"
                )
            except Exception as e:
                logger.error(f"Ошибка добавления заметки: {e}", exc_info=True)
                await update.message.reply_text(f"Ошибка: {e}")
        return

    # If user is in hook selection state — typed custom hook
    if user_id in pending and pending[user_id].get("state") == "hook_selection":
        custom_hook = idea_text.strip()
        script_body = pending[user_id].get("script_body", "")
        new_script = custom_hook + "\n" + script_body
        char_count = len(new_script)
        pending[user_id]["script"] = new_script
        pending[user_id]["state"] = None
        pending[user_id].pop("hook_options", None)
        pending[user_id].pop("script_body", None)
        pending[user_id].pop("shown_hooks", None)
        _save_pending(pending)

        preview = (
            f"📝 СЦЕНАРИЙ (твой хук):\n\n"
            f"{new_script}\n\n"
            f"———\n"
            f"📊 {char_count} символов\n"
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Утвердить → обложка", callback_data="approve"),
                    InlineKeyboardButton("🔄 Переписать", callback_data="rewrite"),
                ],
                [
                    InlineKeyboardButton("✏️ Внести правки", callback_data="edit_mode"),
                    InlineKeyboardButton("✏️ Другой хук", callback_data="new_hook"),
                ],
                [InlineKeyboardButton("💾 Отложить как идею", callback_data="save_to_notion")],
                [
                    InlineKeyboardButton("❌ Отмена", callback_data="cancel"),
                ],
            ]
        )
        await update.message.reply_text(preview, reply_markup=keyboard)
        return

    # If user is editing a specific voice part text (via ✏️ button)
    if user_id in pending and pending[user_id].get("state") == "voice_text_edit":
        idx = pending[user_id].get("voice_edit_part", 0)
        parts = pending[user_id].get("voice_parts", [])
        if 0 <= idx < len(parts):
            new_text = idea_text.strip()
            parts[idx] = new_text
            pending[user_id]["voice_parts"] = parts
            pending[user_id]["state"] = "voice_editing"
            pending[user_id].pop("voice_edit_part", None)
            # Reset approval for this part
            approved = pending[user_id].get("voice_approved", [])
            if idx < len(approved):
                approved[idx] = False
            _save_pending(pending)

            status_msg = await update.message.reply_text(f"🎙 Озвучиваю часть {idx+1} с новым текстом...")
            try:
                voice_path = str(ASSETS_DIR / f"voice_part_{idx}.mp3")
                generate_voiceover(new_text, voice_path)

                # Save updated voice file to card directory and project folder
                notion_id = pending[user_id].get("notion_page_id")
                if notion_id:
                    _save_voice_file(notion_id, idx, voice_path)
                    _save_voice_meta(notion_id, parts, pending[user_id].get("voice_approved", []))
                _save_to_project(pending[user_id], f"voice_part_{idx}.mp3", voice_path)

                with open(voice_path, "rb") as audio_file:
                    await update.message.reply_audio(
                        audio=audio_file,
                        title=f"Часть {idx+1}/{len(parts)} (исправлена)",
                        caption=f"🎙 Часть {idx+1} (новый текст):\n\n«{new_text}»",
                    )

                await status_msg.edit_text(
                    _voice_panel_text(pending[user_id]),
                    reply_markup=_voice_panel_keyboard(pending[user_id]),
                )
            except Exception as e:
                logger.error(f"Ошибка: {e}", exc_info=True)
                await status_msg.edit_text(f"Ошибка: {e}")
        return

    # --- Guide: user pastes a ready Notion URL ---
    if user_id in pending and pending[user_id].get("state") == "guide_waiting_url":
        data = pending[user_id]
        url = (idea_text or "").strip()
        # Basic validation
        m = re.search(r"https?://[^\s]+", url)
        if not m:
            await update.message.reply_text(
                "Это не похоже на ссылку. Пришли URL вида https://...notion.site/... или нажми «Отмена»."
            )
            return
        guide_url = m.group(0).rstrip(".,;)")
        notion_page_id = data.get("notion_page_id")
        msg = await update.message.reply_text("🔗 Сохраняю ссылку на гайд...")
        try:
            if notion_page_id:
                await asyncio.to_thread(add_guide_link_to_card, notion_page_id, guide_url)
            data["guide_url"] = guide_url
            data["guide_created"] = True
            data["state"] = None
            _save_pending(pending)
            buttons = [
                [InlineKeyboardButton("👁 Открыть гайд", url=guide_url)],
                [InlineKeyboardButton("◀️ Меню гайда", callback_data="create_guide")],
                [InlineKeyboardButton("✅ Готово", callback_data="finish")],
            ]
            await msg.edit_text(
                f"✅ Ссылка сохранена и добавлена в карточку Notion.\n\n📎 {guide_url}",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception as e:
            logger.error(f"Ошибка сохранения guide URL: {e}", exc_info=True)
            data["state"] = None
            _save_pending(pending)
            await msg.edit_text(f"Ошибка: {e}")
        return

    # --- Guide: user pastes raw text, we build a Notion page from it ---
    if user_id in pending and pending[user_id].get("state") == "guide_waiting_text":
        data = pending[user_id]
        raw_text = (idea_text or "").strip()
        if len(raw_text) < 20:
            await update.message.reply_text(
                "Текст слишком короткий. Пришли осмысленный текст гайда или нажми «Отмена»."
            )
            return
        title = data.get("card_data", {}).get("title", "Гайд")
        notion_page_id = data.get("notion_page_id")
        msg = await update.message.reply_text("📝 Создаю страницу гайда в Notion...")
        try:
            guide_url = await asyncio.to_thread(create_guide_page_from_raw, raw_text, title)
            if notion_page_id:
                await asyncio.to_thread(add_guide_link_to_card, notion_page_id, guide_url)
            data["guide_url"] = guide_url
            data["guide_created"] = True
            data["state"] = None
            _save_pending(pending)
            buttons = [
                [InlineKeyboardButton("👁 Открыть гайд", url=guide_url)],
                [InlineKeyboardButton("◀️ Меню гайда", callback_data="create_guide")],
                [InlineKeyboardButton("✅ Готово", callback_data="finish")],
            ]
            await msg.edit_text(
                f"✅ Гайд создан из твоего текста.\n\n📎 {guide_url}",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception as e:
            logger.error(f"Ошибка создания гайда из текста: {e}", exc_info=True)
            data["state"] = None
            _save_pending(pending)
            await msg.edit_text(f"Ошибка: {e}")
        return

    # If user is in voice editing state — can fix part text or adjust style
    # Only handle if message looks like a voice command (starts with "часть"/"part")
    # --- Guide feedback: user sends text/voice to edit the guide ---
    if user_id in pending and pending[user_id].get("state") == "guide_feedback":
        data = pending[user_id]
        feedback = idea_text.strip()
        script_text = data.get("script", "")
        title = data.get("card_data", {}).get("title", "Гайд")
        notion_page_id = data.get("notion_page_id")

        msg = await update.message.reply_text("📎 Переписываю гайд с учётом правок...")
        try:
            guide_url = await asyncio.to_thread(create_guide_page, script_text, title, feedback)

            if notion_page_id:
                await asyncio.to_thread(add_guide_link_to_card, notion_page_id, guide_url)

            data["guide_url"] = guide_url
            data["state"] = None
            _save_pending(pending)

            buttons = [
                [InlineKeyboardButton("🔄 Переписать ещё", callback_data="guide_rewrite")],
                [InlineKeyboardButton("✅ Готово", callback_data="finish")],
            ]
            await msg.edit_text(
                f"✅ Гайд переписан!\n\n📎 {guide_url}\n\nДоволен результатом?",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception as e:
            logger.error(f"Ошибка переписки гайда: {e}", exc_info=True)
            data["state"] = None
            _save_pending(pending)
            await msg.edit_text(f"Ошибка: {e}")
        return

    # Handle AI-assisted script edit on a saved Notion card
    if user_id in pending and pending[user_id].get("state") == "script_instruct_wait":
        instruction = (update.message.text or "").strip()
        if not instruction:
            await update.message.reply_text("Напиши инструкцию текстом или пришли голосовое.")
            return
        msg = await update.message.reply_text("✍️ Применяю правку...")
        await _apply_script_instruction(user_id, instruction, msg)
        return

    # Handle script edit
    if user_id in pending and pending[user_id].get("state") == "edit_script":
        new_script = (update.message.text or "").strip()
        if not new_script:
            await update.message.reply_text("Пришли текст сценария сообщением.")
            return
        card_id = pending[user_id].get("script_edit_card")
        title = pending[user_id].get("script_edit_title", "")
        pending[user_id]["state"] = None
        _save_pending(pending)
        msg = await update.message.reply_text("📝 Сохраняю сценарий в Notion...")
        try:
            await asyncio.to_thread(update_notion_page_script, card_id, new_script)
            await msg.edit_text(
                f"✅ Сценарий «{title}» обновлён ({len(new_script)} символов).",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📜 Посмотреть", callback_data=f"card_script:{card_id[:20]}")],
                    [InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{card_id[:20]}")],
                ]),
            )
        except Exception as e:
            logger.error(f"edit_script save error: {e}", exc_info=True)
            await msg.edit_text(f"Ошибка сохранения: {e}")
        return

    # «Готовые материалы» — accept photo/video from client, save to project.
    # Photos handled by a separate MessageHandler (filters.PHOTO). Here we
    # only catch video/document uploads, since process_idea already receives
    # those. Text input during this state is not consumed here — the reply
    # keyboard / regular flow still works for user commands.
    if user_id in pending and pending[user_id].get("state") == "broll_ready_material":
        video_file = update.message.video or update.message.document
        if video_file and (
            update.message.video
            or (video_file.mime_type and video_file.mime_type.startswith("video/"))
        ):
            msg = await update.message.reply_text("📥 Сохраняю видео...")
            ok, reply_text = await _save_ready_video(update, context, pending[user_id])
            await msg.edit_text(
                reply_text + "\n\n_Ещё фото/видео или «✅ Готово» из меню выше._",
                parse_mode="Markdown",
            )
            return

    # Selfie pipeline v2 — text-edit state goes through the selfie module.
    if user_id in pending and pending[user_id].get("state") == "selfie_text_editing":
        if await selfie_handlers.handle_text_edit_message(update, context):
            return

    # Pipeline 2 — B-roll video upload (selfie + B-roll mix).
    if user_id in pending and pending[user_id].get("state") == "selfie_broll_uploading_video":
        if await selfie_handlers.handle_broll_upload_video_message(update, context):
            return

    # Selfie pipeline v2 — video intake routed to the selfie module.
    # Замещает старый inline-блок (~130 строк) на полнофункциональный модуль
    # с шагами правки субтитров → музыка → выбор обложки.
    if user_id in pending and pending[user_id].get("state") == "selfie_waiting_video":
        await selfie_handlers.process_video(update, context)
        return

    # ── Legacy selfie (unused after v2 migration) ──────────────────────────
    # Keep dead branch only as a guard sentinel — never executed because the
    # state is consumed above. Removing in a follow-up cleanup PR.
    if False:  # pragma: no cover
        video_file = update.message.video or update.message.document
        if video_file and (update.message.video or (video_file.mime_type and video_file.mime_type.startswith("video/"))):
            msg = await update.message.reply_text("📥 Загружаю видео...")
            try:
                tg_file = await context.bot.get_file(video_file.file_id)

                # Save to temp first, then to project dir after Notion card is created
                selfie_tmp = Path(tempfile.mkdtemp(prefix="selfie_"))
                source_path = selfie_tmp / "source.mp4"
                await tg_file.download_to_drive(str(source_path))
                file_size = source_path.stat().st_size / 1024 / 1024
                logger.info(f"[selfie] Source video downloaded: {file_size:.1f} MB")

                await msg.edit_text("🎙 Расшифровываю речь и накладываю субтитры...")

                # Use subtitle_burner: transcribe + generate ASS + burn subtitles
                from subtitle_burner import add_subtitles_to_video, transcribe_words
                font_dir = ASSETS_DIR / "fonts"
                font_dir_path = font_dir if font_dir.exists() else None

                # 1. Transcribe to get text for title generation
                audio_tmp = selfie_tmp / "_tmp_audio.wav"
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(source_path),
                     "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                     str(audio_tmp)],
                    capture_output=True, text=True, timeout=120,
                )
                words = await asyncio.to_thread(transcribe_words, str(audio_tmp), language="ru")
                transcript_text = " ".join(w["word"] for w in words) if words else ""
                logger.info(f"[selfie] Transcribed: {len(words)} words, {len(transcript_text)} chars")

                # Fix #1 (9 May): Whisper «small» model frequently mis-hears
                # russian compound words («сосновый» → «с основы», «строительство»
                # → «с строительство»). Post-process the transcript through
                # Sonnet to fix obvious ASR errors before passing to hook
                # generator and TG-post writer. Subtitles on video stay raw
                # (they need word-level timing from Whisper which we don't
                # remap here) — only the downstream text artifacts get cleaned.
                # Best-effort: any failure → keep raw transcript, never crash.
                if transcript_text and len(transcript_text) > 50:
                    transcript_text = await asyncio.to_thread(
                        _polish_asr_transcript, transcript_text
                    )
                    logger.info(f"[selfie] Transcript polished ({len(transcript_text)} chars)")

                # Clean up temp audio
                try:
                    audio_tmp.unlink()
                except OSError:
                    pass

                if not transcript_text.strip():
                    await msg.edit_text(
                        "Не удалось распознать речь в видео. "
                        "Попробуй отправить другое видео с чёткой речью."
                    )
                    pending[user_id]["state"] = "selfie_waiting_video"
                    _save_pending(pending)
                    return

                # 2. Burn subtitles onto video
                subtitled_path = selfie_tmp / "subtitled.mp4"
                await asyncio.to_thread(
                    add_subtitles_to_video,
                    str(source_path),
                    output_path=str(subtitled_path),
                    language="ru",
                    font_dir=font_dir_path,
                )
                logger.info(f"[selfie] Subtitles burned: {subtitled_path.stat().st_size / 1024 / 1024:.1f} MB")

                # 3. Generate cover from first frame
                cover_path = selfie_tmp / "cover.jpg"
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(source_path),
                     "-vframes", "1", "-q:v", "2",
                     str(cover_path)],
                    capture_output=True, text=True, timeout=30,
                )
                logger.info("[selfie] Cover extracted from first frame")

                # 4. Generate provocative hook options instead of taking first sentence.
                # First-sentence-as-title (legacy behaviour) was a placeholder that
                # produced bland titles. Now we run the same hook-generator that
                # `new_hook` callback uses on Artem's script flow — Claude Opus
                # with anti-cliche guards, returning 5 strong variants.
                await msg.edit_text("🎣 Придумываю названия...")
                hooks = await asyncio.to_thread(_generate_hook_options, transcript_text)
                if not hooks:
                    # Fallback to old behaviour if Claude failed — still better than nothing.
                    fallback = transcript_text.split(".")[0].strip()[:80] or "Живое видео"
                    hooks = [fallback]

                pending[user_id] = {
                    "state": "selfie_waiting_title",
                    "selfie_tmp_dir": str(selfie_tmp),
                    "selfie_source": str(source_path),
                    "selfie_subtitled": str(subtitled_path),
                    "selfie_cover": str(cover_path),
                    "selfie_transcript": transcript_text,
                    "selfie_hook_options": hooks,
                    # Track all hooks shown so "🔄 Ещё варианты" doesn't repeat them
                    "selfie_shown_hooks": list(hooks),
                }
                _save_pending(pending)

                hooks_block = "\n".join(f"  • {h}" for h in hooks)
                await msg.edit_text(
                    f"✅ Субтитры наложены!\n\n"
                    f"📝 Расшифровка:\n{transcript_text[:500]}"
                    f"{'…' if len(transcript_text) > 500 else ''}\n\n"
                    f"———\n"
                    f"🎣 Варианты названия (нажми на один — станет заголовком карточки):\n\n"
                    f"{hooks_block}\n\n"
                    f"Или жми «🔄 Ещё варианты» / «✏️ Свой текстом».",
                    reply_markup=_selfie_hook_keyboard(hooks),
                )
            except Exception as e:
                logger.error(f"[selfie] Processing error: {e}", exc_info=True)
                pending[user_id]["state"] = "selfie_waiting_video"
                _save_pending(pending)
                await msg.edit_text(
                    f"Ошибка обработки видео: {e}\n\n"
                    "Попробуй отправить другое видео."
                )
        else:
            await update.message.reply_text("Отправь видеофайл (MP4). Жду видео, снятое на телефон.")
        return

    # Handle selfie title input (user types custom title)
    if user_id in pending and pending[user_id].get("state") == "selfie_waiting_title":
        custom_title = (idea_text or "").strip()
        if not custom_title:
            await update.message.reply_text("Напиши название для видео или нажми «Утвердить название».")
            return
        # Свой текст — тоже на обложку, если выбрано «с текстом» (merged flow).
        await _apply_selfie_cover_text(context, user_id, update.message.chat_id, custom_title)
        await _selfie_finalize(update, context, user_id, custom_title)
        return

    # Handle final video upload
    if user_id in pending and pending[user_id].get("state") == "upload_final_video":
        video_file = update.message.video or update.message.document
        if video_file and (update.message.video or (video_file.mime_type and video_file.mime_type.startswith("video/"))):
            pending[user_id]["state"] = None
            _save_pending(pending)
            msg = await update.message.reply_text("📤 Загружаю готовый ролик...")
            try:
                tg_file = await context.bot.get_file(video_file.file_id)
                proj = _project_dir(pending[user_id])
                if not proj:
                    await msg.edit_text("Ошибка: нет привязанной карточки")
                    return
                final_path = str(proj / "final_video.mp4")
                await tg_file.download_to_drive(final_path)
                file_size = Path(final_path).stat().st_size / 1024 / 1024
                logger.info(f"Final video saved: {final_path} ({file_size:.1f} MB)")

                card_prefix = pending[user_id].get("upload_final_card_id", "")
                await msg.edit_text(
                    f"✅ Готовый ролик загружен ({file_size:.1f} МБ)\n\n"
                    "Теперь можешь опубликовать через «Кросс-постинг».",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📢 Кросс-постинг", callback_data=f"crosspost:{card_prefix}")],
                        [InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{card_prefix}")],
                    ]),
                )
            except Exception as e:
                logger.error(f"Final video upload error: {e}", exc_info=True)
                await msg.edit_text(f"Ошибка загрузки: {e}")
            return
        else:
            await update.message.reply_text("Отправь видеофайл (MP4).")
            return

    # Handle video file or YouTube URL for B-roll cutting
    if user_id in pending and pending[user_id].get("state") == "broll_youtube_input":
        # Check if user sent a video file
        video_file = update.message.video or update.message.document
        if video_file and (update.message.video or (video_file.mime_type and video_file.mime_type.startswith("video/"))):
            pending[user_id]["state"] = None
            _save_pending(pending)
            msg = await update.message.reply_text("🎬 Скачиваю видео и нарезаю клипы...")
            try:
                # Download video file from Telegram
                tg_file = await context.bot.get_file(video_file.file_id)
                yt_dir = ASSETS_DIR / "youtube_clips"
                yt_dir.mkdir(parents=True, exist_ok=True)
                video_path = str(yt_dir / "source.mp4")
                await tg_file.download_to_drive(video_path)
                logger.info(f"Downloaded video file: {video_file.file_name or 'video'}, size={video_file.file_size}")

                # Cut into clips using ffmpeg
                probe = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", video_path],
                    capture_output=True, text=True, timeout=10,
                )
                total_duration = float(probe.stdout.strip())
                clip_duration = 5
                max_clips = 12
                num_clips = min(max_clips, int(total_duration / clip_duration))
                if num_clips < 1:
                    num_clips = 1
                step = total_duration / num_clips

                # Clean old clips
                for f in yt_dir.glob("clip_*.mp4"):
                    f.unlink()

                clips = []
                for i in range(num_clips):
                    start = i * step
                    clip_path = str(yt_dir / f"clip_{i}.mp4")
                    subprocess.run(
                        ["ffmpeg", "-y", "-ss", str(start), "-i", video_path,
                         "-t", str(clip_duration), "-c:v", "libx264", "-an",
                         "-vf", "scale=-2:720", clip_path],
                        capture_output=True, timeout=30,
                    )
                    if Path(clip_path).exists():
                        clips.append({"path": clip_path, "timecode": f"{int(start//60)}:{int(start%60):02d}"})

                if not clips:
                    await msg.edit_text("Не удалось нарезать клипы из видео.")
                    return

                existing_clips = pending[user_id].get("broll_clips", [])
                start_idx = len(existing_clips)
                for clip in clips:
                    existing_clips.append(clip)
                pending[user_id]["broll_clips"] = existing_clips
                _save_pending(pending)

                await msg.edit_text(f"✅ Нарезано {len(clips)} клипов! Отправляю...")

                for i, clip in enumerate(clips):
                    clip_idx = start_idx + i
                    select_btn = InlineKeyboardMarkup([
                        [InlineKeyboardButton(f"✅ Выбрать #{clip_idx+1}", callback_data=f"broll_select:{clip_idx}")]
                    ])
                    try:
                        with open(clip["path"], "rb") as vf:
                            await context.bot.send_video(
                                chat_id=update.message.chat_id,
                                video=vf,
                                caption=f"#{clip_idx+1} | Видео: {clip['timecode']}",
                                reply_markup=select_btn,
                                supports_streaming=True,
                            )
                    except Exception as e:
                        logger.warning(f"Failed to send clip #{i}: {e}")

                buttons = [
                    [InlineKeyboardButton("💾 Сохранить выбранные в Notion", callback_data="broll_approve")],
                    [InlineKeyboardButton("🎬 Ещё видео", callback_data="broll_youtube")],
                    [InlineKeyboardButton("✅ Готово", callback_data="finish")],
                ]
                await context.bot.send_message(
                    chat_id=update.message.chat_id,
                    text="Выбери подходящие клипы, затем «Сохранить».",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
            except Exception as e:
                logger.error(f"Video cut error: {e}", exc_info=True)
                buttons = [
                    [InlineKeyboardButton("🎬 Попробовать ещё", callback_data="broll_youtube")],
                    [InlineKeyboardButton("🔍 Искать на стоках", callback_data="broll_stock")],
                    [InlineKeyboardButton("✅ Готово", callback_data="finish")],
                ]
                await msg.edit_text(
                    f"❌ Ошибка нарезки: {e}",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
            return

        # Accept YouTube URLs, Twitter/Nitter URLs, and any other URL.
        import re as _re
        # Detect Twitter/Nitter URLs first — use FxTwitter API (yt-dlp fails on Twitter)
        any_url_m = _re.search(r'https?://\S+', idea_text)
        raw_url = any_url_m.group(0).rstrip(".,;)") if any_url_m else ""
        is_tweet = _is_twitter_url(raw_url) if raw_url else False

        yt_urls = extract_youtube_urls(idea_text)
        video_url = yt_urls[0] if yt_urls else None
        if not video_url and not is_tweet:
            if raw_url:
                video_url = raw_url

        # --- Twitter/Nitter: download via FxTwitter direct MP4 link ---
        if is_tweet:
            pending[user_id]["state"] = None
            _save_pending(pending)
            msg = await update.message.reply_text(f"🐦 Скачиваю видео из твита...\n{raw_url}")
            try:
                tweet_data = await _fetch_tweet_via_fxtwitter(raw_url)
                if not tweet_data or not tweet_data.get("video_urls"):
                    await msg.edit_text("❌ В этом твите нет видео.")
                    return

                mp4_url = tweet_data["video_urls"][0]
                logger.info(f"Tweet video URL: {mp4_url}")

                # Download the MP4 directly (it's a CDN link)
                import httpx
                yt_dir = ASSETS_DIR / "youtube_clips"
                yt_dir.mkdir(parents=True, exist_ok=True)
                video_path = str(yt_dir / "tweet_source.mp4")
                async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
                    resp = await client.get(mp4_url)
                    if resp.status_code != 200:
                        await msg.edit_text(f"❌ Не удалось скачать видео (HTTP {resp.status_code})")
                        return
                    Path(video_path).write_bytes(resp.content)
                    logger.info(f"Downloaded tweet video: {len(resp.content)} bytes")

                await msg.edit_text("🎬 Нарезаю клипы...")

                # Cut into clips using same logic as video file upload
                probe = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", video_path],
                    capture_output=True, text=True, timeout=10,
                )
                total_duration = float(probe.stdout.strip())
                clip_duration = 5
                max_clips = 12
                num_clips = min(max_clips, int(total_duration / clip_duration))
                if num_clips < 1:
                    num_clips = 1
                step = total_duration / num_clips

                for f in yt_dir.glob("clip_*.mp4"):
                    f.unlink()

                clips = []
                for i in range(num_clips):
                    start = i * step
                    clip_path = str(yt_dir / f"clip_{i}.mp4")
                    subprocess.run(
                        ["ffmpeg", "-y", "-ss", str(start), "-i", video_path,
                         "-t", str(clip_duration), "-c:v", "libx264", "-an",
                         "-vf", "scale=-2:720", clip_path],
                        capture_output=True, timeout=30,
                    )
                    if Path(clip_path).exists():
                        clips.append({"path": clip_path, "timecode": f"{int(start//60)}:{int(start%60):02d}"})

                if not clips:
                    await msg.edit_text("❌ Не удалось нарезать клипы из видео.")
                    return

                existing_clips = pending[user_id].get("broll_clips", [])
                start_idx = len(existing_clips)
                for clip in clips:
                    existing_clips.append(clip)
                pending[user_id]["broll_clips"] = existing_clips
                _save_pending(pending)

                await msg.edit_text(f"✅ Нарезано {len(clips)} клипов из твита! Отправляю...")

                for i, clip in enumerate(clips):
                    clip_idx = start_idx + i
                    select_btn = InlineKeyboardMarkup([
                        [InlineKeyboardButton(f"✅ Выбрать #{clip_idx+1}", callback_data=f"broll_select:{clip_idx}")]
                    ])
                    try:
                        with open(clip["path"], "rb") as vf:
                            await context.bot.send_video(
                                chat_id=update.message.chat_id,
                                video=vf,
                                caption=f"#{clip_idx+1} | Твит: {clip['timecode']}",
                                reply_markup=select_btn,
                                supports_streaming=True,
                            )
                    except Exception as e:
                        logger.warning(f"Failed to send tweet clip #{i}: {e}")

                buttons = [
                    [InlineKeyboardButton("💾 Сохранить выбранные в Notion", callback_data="broll_approve")],
                    [InlineKeyboardButton("🎬 Ещё видео для нарезки", callback_data="broll_youtube")],
                    [InlineKeyboardButton("✅ Готово", callback_data="finish")],
                ]
                await context.bot.send_message(
                    chat_id=update.message.chat_id,
                    text="Выбери подходящие клипы кнопкой «Выбрать», затем «Сохранить».",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
            except Exception as e:
                logger.error(f"Tweet video download error: {e}", exc_info=True)
                buttons = [
                    [InlineKeyboardButton("🎬 Скинуть другую ссылку", callback_data="broll_youtube")],
                    [InlineKeyboardButton("✅ Готово", callback_data="finish")],
                ]
                await msg.edit_text(
                    f"❌ Ошибка скачивания видео из твита: {e}",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
            return

        if video_url:
            pending[user_id]["state"] = None
            _save_pending(pending)
            source_label = "YouTube" if yt_urls else "видео"
            msg = await update.message.reply_text(f"🎬 Скачиваю и нарезаю {source_label}...\n{video_url}\n\n⏱ Это может занять 1-2 минуты.")
            try:
                clips = await asyncio.to_thread(download_and_cut_youtube, video_url)
                if not clips:
                    await msg.edit_text("Не удалось нарезать клипы. Попробуй другую ссылку.")
                    return

                # Add clips to existing broll_clips
                existing_clips = pending[user_id].get("broll_clips", [])
                start_idx = len(existing_clips)
                for clip in clips:
                    existing_clips.append(clip)
                pending[user_id]["broll_clips"] = existing_clips
                _save_pending(pending)

                await msg.edit_text(f"✅ Нарезано {len(clips)} клипов! Отправляю для выбора...")

                # Send clips as videos
                for i, clip in enumerate(clips):
                    clip_idx = start_idx + i
                    select_btn = InlineKeyboardMarkup([
                        [InlineKeyboardButton(f"✅ Выбрать #{clip_idx+1}", callback_data=f"broll_select:{clip_idx}")]
                    ])
                    try:
                        with open(clip["path"], "rb") as vf:
                            await context.bot.send_video(
                                chat_id=update.message.chat_id,
                                video=vf,
                                caption=f"#{clip_idx+1} | {clip['timecode']}",
                                reply_markup=select_btn,
                                supports_streaming=True,
                            )
                    except Exception as e:
                        logger.warning(f"Failed to send clip #{i}: {e}")

                # Show save button
                buttons = [
                    [InlineKeyboardButton("💾 Сохранить выбранные в Notion", callback_data="broll_approve")],
                    [InlineKeyboardButton("🎬 Ещё видео для нарезки", callback_data="broll_youtube")],
                    [InlineKeyboardButton("✅ Готово", callback_data="finish")],
                ]
                await context.bot.send_message(
                    chat_id=update.message.chat_id,
                    text="Выбери подходящие клипы кнопкой «Выбрать», затем «Сохранить».",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
            except Exception as e:
                logger.error(f"Video cut error: {e}", exc_info=True)
                buttons = [
                    [InlineKeyboardButton("🎬 Скинуть другую ссылку", callback_data="broll_youtube")],
                    [InlineKeyboardButton("🔍 Искать на стоках", callback_data="broll_stock")],
                    [InlineKeyboardButton("✅ Готово", callback_data="finish")],
                ]
                await msg.edit_text(
                    f"❌ Не удалось скачать видео: {e}",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
        else:
            await update.message.reply_text("Не нашёл ссылку. Отправь ссылку на видео (YouTube, Vimeo, или любой сайт со встроенным видео).")
        return

    if user_id in pending and pending[user_id].get("state") == "voice_editing":
        lower = idea_text.lower().strip()
        parts = pending[user_id].get("voice_parts", [])

        # If message doesn't look like a voice command — treat as new idea
        if not re.match(r'(?:часть|part)\s*\d', lower):
            pending.pop(user_id, None)
            _save_pending(pending)
            await _generate_script(update, context, idea_text)
            return

        # Check for "без стайла" / "style 0" commands: "часть 2 без стайла"
        no_style_match = re.match(r'(?:часть|part)\s*(\d+)\s+(?:без стайла|без style|style\s*0|стайл\s*0)', lower)
        if no_style_match:
            idx = int(no_style_match.group(1)) - 1
            if 0 <= idx < len(parts):
                part_text = parts[idx]
                status_msg = await update.message.reply_text(f"🎙 Переозвучиваю часть {idx+1} без style...")
                try:
                    voice_path = str(ASSETS_DIR / f"voice_part_{idx}.mp3")
                    generate_voiceover(part_text, voice_path, style_override=0.0)

                    # Save updated voice file to card directory and project folder
                    notion_id = pending[user_id].get("notion_page_id")
                    if notion_id:
                        _save_voice_file(notion_id, idx, voice_path)
                    _save_to_project(pending[user_id], f"voice_part_{idx}.mp3", voice_path)

                    with open(voice_path, "rb") as audio_file:
                        await update.message.reply_audio(
                            audio=audio_file,
                            title=f"Часть {idx+1}/{len(parts)} (style=0)",
                            caption=f"🎙 Часть {idx+1}/{len(parts)} (без style):\n\n«{part_text}»",
                        )

                    # Полная панель озвучки (вкл. «🎤 Озвучить своим голосом»),
                    # чтобы кнопка была во ВСЕХ меню озвучки, не только в главной.
                    keyboard = _voice_panel_keyboard(pending[user_id])
                    await status_msg.edit_text(
                        f"✅ Часть {idx+1} переозвучена (style=0)!\n\n"
                        + "\n".join(f"Часть {i+1}: «{p[:50]}{'...' if len(p) > 50 else ''}»" for i, p in enumerate(parts))
                        + "\n\nКоманды:\n"
                        + "часть 2: новый текст — заменить текст\n"
                        + "часть 3 без стайла — переозвучить ровнее",
                        reply_markup=keyboard,
                    )
                except Exception as e:
                    logger.error(f"Ошибка: {e}", exc_info=True)
                    await status_msg.edit_text(f"Ошибка: {e}")
            else:
                await update.message.reply_text(f"Часть {idx+1} не найдена. Всего частей: {len(parts)}")
            return

        # Parse "часть N: new text"
        text_match = re.match(r'(?:часть|part)\s*(\d+)\s*[:：]\s*(.+)', idea_text, re.IGNORECASE | re.DOTALL)
        if text_match:
            idx = int(text_match.group(1)) - 1
            new_text = text_match.group(2).strip()
            if 0 <= idx < len(parts):
                parts[idx] = new_text
                pending[user_id]["voice_parts"] = parts
                _save_pending(pending)

                status_msg = await update.message.reply_text(f"🎙 Переозвучиваю часть {idx+1} с новым текстом...")
                try:
                    voice_path = str(ASSETS_DIR / f"voice_part_{idx}.mp3")
                    generate_voiceover(new_text, voice_path)

                    # Save updated voice file to card directory and project folder
                    notion_id = pending[user_id].get("notion_page_id")
                    if notion_id:
                        _save_voice_file(notion_id, idx, voice_path)
                        _save_voice_meta(notion_id, parts, pending[user_id].get("voice_approved", []))
                    _save_to_project(pending[user_id], f"voice_part_{idx}.mp3", voice_path)

                    with open(voice_path, "rb") as audio_file:
                        await update.message.reply_audio(
                            audio=audio_file,
                            title=f"Часть {idx+1}/{len(parts)} (исправлена)",
                            caption=f"🎙 Часть {idx+1}/{len(parts)} (новый текст):\n\n«{new_text}»",
                        )

                    # Полная панель озвучки (вкл. «🎤 Озвучить своим голосом»),
                    # чтобы кнопка была во ВСЕХ меню озвучки, не только в главной.
                    keyboard = _voice_panel_keyboard(pending[user_id])
                    await status_msg.edit_text(
                        f"✅ Часть {idx+1} переозвучена!\n\n"
                        + "\n".join(f"Часть {i+1}: «{p[:50]}{'...' if len(p) > 50 else ''}»" for i, p in enumerate(parts))
                        + "\n\nКоманды:\n"
                        + "часть 2: новый текст — заменить текст\n"
                        + "часть 3 без стайла — переозвучить ровнее",
                        reply_markup=keyboard,
                    )
                except Exception as e:
                    logger.error(f"Ошибка переозвучки: {e}", exc_info=True)
                    await status_msg.edit_text(f"Ошибка: {e}")
            else:
                await update.message.reply_text(f"Часть {idx+1} не найдена. Всего частей: {len(parts)}")
        else:
            await update.message.reply_text(
                "Команды для озвучки:\n\n"
                "часть 2: новый текст — заменить текст и переозвучить\n"
                "часть 3 без стайла — переозвучить без style (ровнее)\n\n"
                "Или нажми кнопку для переозвучки."
            )
        return

    # If user is editing description text
    if user_id in pending and pending[user_id].get("state") == "desc_editing":
        new_desc = idea_text.strip()
        pending[user_id]["description_draft"] = new_desc
        pending[user_id]["state"] = None
        _save_pending(pending)

        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Сохранить", callback_data="desc_save")],
            [InlineKeyboardButton("✏️ Ещё раз отредактировать", callback_data="desc_edit")],
        ])
        await update.message.reply_text(
            f"📝 Новый текст:\n\n{new_desc}\n\nСохранить?",
            reply_markup=buttons,
        )
        return

    # If user is in cover approval state
    if user_id in pending and pending[user_id].get("state") == "cover_approval":
        # Check if user wants to generate options or set custom text
        lower = idea_text.lower()
        if any(w in lower for w in ["вариант", "предлож", "придумай", "генерируй", "ещё", "еще", "другой", "другие", "нравится"]):
            # User wants AI-generated options
            await _generate_cover_options(update, context)
            return

        # Otherwise treat as custom cover text
        pending[user_id]["cover_text"] = idea_text
        _save_pending(pending)

        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✅ Ок, генерируй", callback_data="cover_ok")],
                [InlineKeyboardButton("🔄 Предложи варианты", callback_data="cover_options")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
            ]
        )
        await update.message.reply_text(
            f"🖼 Текст обложки:\n\n"
            f"«{idea_text}»\n\n"
            f"Жми кнопку или напиши другой вариант.",
            reply_markup=keyboard,

        )
        return

    # If user is in edit mode, treat message as edit instruction
    if user_id in pending and pending[user_id].get("state") == "editing":
        pending[user_id]["state"] = None
        _save_pending(pending)
        await _edit_script(update, context, idea_text)
        return

    # ── Avatar pick by number ──
    if user_id in pending and pending[user_id].get("state") == "avatar_by_number":
        data = pending[user_id]
        data["state"] = None
        num = idea_text.strip().lstrip("0") or "0"
        # Brand-aware avatar pool: shoes-brand cards see only shoes/ folder
        pool_dir = _avatars_dir_for_brand(_get_active_brand_name())
        avatars = []
        if pool_dir.exists():
            avatars = sorted([f.name for f in pool_dir.iterdir() if f.suffix.lower() in (".jpg", ".jpeg", ".png")])
        matched = None
        for a in avatars:
            m = re.match(r'^(\d+)', a)
            if m and m.group(1).lstrip("0") == num:
                matched = a
                break
        if not matched:
            # Try with leading zero
            for a in avatars:
                if a.startswith(idea_text.strip()):
                    matched = a
                    break
        if matched:
            data["chosen_avatar"] = str(pool_dir / matched)
            _save_pending(pending)
            buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Использовать это фото", callback_data="avatar_confirm")],
                [InlineKeyboardButton("🎲 Другое фото", callback_data="avatar_pick:random")],
                [InlineKeyboardButton("🔢 Выбрать по номеру", callback_data="avatar_pick_by_number")],
                [InlineKeyboardButton("📤 Загрузить своё фото в библиотеку", callback_data="cover_pool_upload")],
            ])
            with open(str(pool_dir / matched), "rb") as photo:
                await update.message.reply_photo(
                    photo=photo,
                    caption=f"📷 Фото для обложки: {matched}",
                    reply_markup=buttons,
                )
        else:
            _save_pending(pending)
            await update.message.reply_text(
                f"Фото с номером «{idea_text.strip()}» не найдено. Попробуй ещё раз или нажми кнопку.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎲 Случайное фото", callback_data="avatar_pick:random")],
                    [InlineKeyboardButton("🔢 Выбрать по номеру", callback_data="avatar_pick_by_number")],
                ]),
            )
        return

    # ── Instagram CTA text input states ──
    if user_id in pending and (pending[user_id].get("state") or "").startswith("ig_cta_"):
        data = pending[user_id]
        state = data["state"]

        if state in ("ig_cta_keyword_input", "ig_cta_keyword_then_master", "ig_cta_keyword_then_telegram", "ig_cta_keyword_then_direct", "ig_cta_keyword_then_tg_post"):
            data["ig_cta_keyword"] = idea_text.strip().lower()
            if state == "ig_cta_keyword_then_tg_post":
                # Got keyword, now ask for specific Telegram post URL.
                data["state"] = "ig_cta_tg_post_url"
                _save_pending(pending)
                await update.message.reply_text(
                    "Вставь ссылку на конкретный пост в Telegram (формата https://t.me/...).\n\n"
                    f"Или отправь «дефолт» чтобы использовать мастер-пост {DEFAULT_DM_REPLY_URL}"
                )
                return
            if state in ("ig_cta_keyword_then_master", "ig_cta_keyword_then_telegram"):
                # Use the global master-post URL from .env as the DM link.
                media_id = data.get("ig_media_id", "")
                keyword = data["ig_cta_keyword"]
                card_title = data.get("notion_title", "") or data.get("card_data", {}).get("title", "")
                reply_text = _build_dm_reply_text(DEFAULT_DM_REPLY_URL, card_title)
                save_keyword_for_post(media_id=media_id, keyword=keyword, reply_text=reply_text, guide_url="")
                data["state"] = None
                _save_pending(pending)
                await update.message.reply_text(
                    f"✅ CTA настроен!\n\nКлючевое слово: «{keyword}»\nСсылка (мастер-пост): {DEFAULT_DM_REPLY_URL}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{data.get('crosspost_card_id', '')}")],
                    ]),
                )
                return
            elif state == "ig_cta_keyword_then_direct":
                guide_url = data.get("guide_url", "")
                if not guide_url:
                    data["state"] = "ig_cta_guide_url"
                    _save_pending(pending)
                    await update.message.reply_text("Введи ссылку на гайд/материал (URL):")
                    return
                media_id = data.get("ig_media_id", "")
                keyword = data["ig_cta_keyword"]
                card_title = data.get("notion_title", "") or data.get("card_data", {}).get("title", "")
                reply_text = _build_dm_reply_text(guide_url, card_title)
                save_keyword_for_post(media_id=media_id, keyword=keyword, reply_text=reply_text, guide_url="")
                data["state"] = None
                _save_pending(pending)
                await update.message.reply_text(
                    f"✅ CTA настроен!\n\nКлючевое слово: «{keyword}»\nСсылка: {guide_url}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{data.get('crosspost_card_id', '')}")],
                    ]),
                )
                return
            else:
                # ig_cta_keyword_input — go back to CTA type selection
                data["state"] = None
                _save_pending(pending)
                await update.message.reply_text(
                    f"Ключевое слово: «{data['ig_cta_keyword']}»\n\nВыбери, куда вести подписчика:",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📨 Telegram-канал", callback_data="ig_cta_telegram")],
                        [InlineKeyboardButton("📎 Прямая ссылка (гайд)", callback_data="ig_cta_direct")],
                    ]),
                )
                return

        elif state == "ig_cta_tg_post_url":
            raw = idea_text.strip()
            if raw.lower() in ("дефолт", "default", "-"):
                url = DEFAULT_DM_REPLY_URL
            else:
                # Extract t.me URL if user pasted it with extra text
                m = re.search(r"https?://t\.me/\S+", raw)
                url = m.group(0) if m else raw
            media_id = data.get("ig_media_id", "")
            keyword = data.get("ig_cta_keyword", "")
            card_title = data.get("notion_title", "") or data.get("card_data", {}).get("title", "")
            reply_text = _build_dm_reply_text(url, card_title)
            save_keyword_for_post(media_id=media_id, keyword=keyword, reply_text=reply_text, guide_url="")
            data["state"] = None
            _save_pending(pending)
            await update.message.reply_text(
                f"✅ CTA настроен!\n\nКлючевое слово: «{keyword}»\nСсылка на пост TG: {url}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{data.get('crosspost_card_id', '')}")],
                ]),
            )
            return

        elif state == "ig_cta_guide_url":
            data["guide_url"] = idea_text.strip()
            media_id = data.get("ig_media_id", "")
            keyword = data.get("ig_cta_keyword", "")
            guide_url = data["guide_url"]
            card_title = data.get("notion_title", "") or data.get("card_data", {}).get("title", "")
            reply_text = _build_dm_reply_text(guide_url, card_title)
            save_keyword_for_post(media_id=media_id, keyword=keyword, reply_text=reply_text, guide_url="")
            data["state"] = None
            _save_pending(pending)
            await update.message.reply_text(
                f"✅ CTA настроен!\n\nКлючевое слово: «{keyword}»\nСсылка: {guide_url}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{data.get('crosspost_card_id', '')}")],
                ]),
            )
            return

    # Any other message = new idea (clear old pending if exists)
    current_state = pending.get(user_id, {}).get("state")
    if current_state:
        logger.warning(f"[user:{user_id}] State '{current_state}' not handled, treating as new idea: {idea_text[:60]}")
    if user_id in pending:
        pending.pop(user_id, None)
        _save_pending(pending)
    await _generate_script(update, context, idea_text)


async def _ideas_batch_stale(query) -> None:
    """Понятный экран, когда подборка идей устарела (рестарт бота / нажат /start).

    Раньше отдавали тихий toast (query.answer) — пользователь его не замечал и
    думал, что «кнопка не работает / ничего не происходит». Теперь редактируем
    сообщение в явное состояние с кнопкой регенерации.
    """
    try:
        await query.edit_message_text(
            "⚠️ Эта подборка идей устарела — бот перезапускался или ты нажимал "
            "/start, и кнопки от старой подборки больше не активны.\n\n"
            "Сгенерируй свежую:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🎰 Банк идей — сгенерировать заново",
                    callback_data="maksim_ideas_stub",
                ),
            ]]),
        )
    except Exception:
        await query.answer(
            "⚠️ Подборка устарела. Открой «🎰 Банк идей» заново.", show_alert=True,
        )


def _compress_for_telegram(src: str, dst: str, duration=None,
                           target_mb: int = 46) -> bool:
    """Сжать видео под лимит Telegram (<50 МБ), СОХРАНЯЯ разрешение.

    24 мая 2026. Раньше 413-guard просто даунскейлил до 720p + crf 28 —
    Avatar IV (1080p, ~46 Мбит/с, 167 МБ на 30с) превращался в мыло.
    Теперь: 2-pass x264 с целевым битрейтом под `target_mb`, разрешение
    оригинала сохраняем (1080p при том же размере файла выглядит резко).
    Если 2-pass не вышел / длительность неизвестна / файл всё равно велик
    — фолбэк на старый 720p, лишь бы гарантированно влез.
    Возвращает True, если dst готов и поместился в лимит.
    """
    LIMIT = 49 * 1024 * 1024
    dur = 0.0
    try:
        dur = float(duration)
    except (TypeError, ValueError):
        dur = 0.0
    if dur <= 0:
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", src],
                capture_output=True, text=True, timeout=30)
            dur = float((r.stdout or "0").strip())
        except Exception:
            dur = 0.0
    # --- основной путь: 2-pass, разрешение оригинала ---
    if dur > 0:
        audio_k = 128
        total_k = int(target_mb * 8 * 1024 / dur)
        video_k = max(1000, total_k - audio_k - 200)  # запас на контейнер
        logp = dst + ".pass"
        common = ["-c:v", "libx264", "-preset", "medium",
                  "-b:v", f"{video_k}k", "-maxrate", f"{int(video_k * 1.25)}k",
                  "-bufsize", f"{int(video_k * 2)}k",
                  "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        try:
            subprocess.run(["ffmpeg", "-y", "-i", src, "-an", "-pass", "1",
                            "-passlogfile", logp, *common, "-f", "mp4", os.devnull],
                           capture_output=True, timeout=600)
            subprocess.run(["ffmpeg", "-y", "-i", src, "-pass", "2",
                            "-passlogfile", logp, *common,
                            "-c:a", "aac", "-b:a", f"{audio_k}k", dst],
                           capture_output=True, timeout=600)
        except Exception as e:
            logger.warning(f"[tg-compress] 2-pass error: {e}")
        finally:
            for suf in ("-0.log", "-0.log.mbtree"):
                try:
                    os.remove(logp + suf)
                except Exception:
                    pass
        if os.path.exists(dst) and 0 < os.path.getsize(dst) <= LIMIT:
            logger.info(f"[tg-compress] 2-pass keep-res ok: "
                        f"{os.path.getsize(dst) // 1048576}МБ @ {video_k}k")
            return True
        logger.warning("[tg-compress] 2-pass не влез/не вышел → фолбэк 720p")
    # --- фолбэк: агрессивный 720p (гарантированно влезает) ---
    try:
        subprocess.run(["ffmpeg", "-y", "-i", src, "-vf", "scale=720:-2",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                        dst], capture_output=True, timeout=300)
    except Exception as e:
        logger.warning(f"[tg-compress] 720p fallback error: {e}")
    return os.path.exists(dst) and 0 < os.path.getsize(dst) <= LIMIT


async def _render_avatar_from_audio(context, chat_id, audio_path, look_id,
                                    look_name, avatar_version, data):
    """Сгенерировать HeyGen-аватар из ГОТОВОГО аудио-файла (mp3).

    Общий путь фичи «свой голос»: зеркалит проверенный TTS-флоу
    (upload-asset → heygen_generate_video(voice.type=audio) → poll → доставка +
    сохранение avatar_*.mp4), но источник аудио — переданный файл, а не TTS.
    Рабочий heygen_ver-флоу не трогаем — это отдельная функция.
    """
    import httpx
    ver_label = {"v4": "Avatar IV", "v2": "Avatar 4", "v3": "Avatar 3"}.get(avatar_version, "Avatar 3")
    status = await context.bot.send_message(
        chat_id,
        f"🤖 Генерирую аватар ({look_name}, {ver_label}) с твоим голосом...\n"
        f"⏱ Обычно 1-3 минуты.",
    )
    try:
        # Подрезаем длинные паузы (как в TTS-флоу)
        try:
            audio_path = await asyncio.to_thread(
                trim_long_silences, audio_path, audio_path,
                max_silence_sec=0.5, keep_silence_sec=0.3,
            )
        except Exception as e:
            logger.warning(f"[selfvoice] silence trim skip: {e}")

        with open(audio_path, "rb") as af:
            up = httpx.post(
                "https://upload.heygen.com/v1/asset",
                headers={"X-Api-Key": HEYGEN_API_KEY, "Content-Type": "audio/mpeg"},
                content=af.read(), timeout=120,
            ).json()
        if up.get("code") != 100:
            raise RuntimeError(f"upload error: {up}")
        audio_url = up["data"]["url"]

        video_id = await asyncio.to_thread(
            heygen_generate_video, audio_url, look_id, avatar_version,
        )
        logger.info(f"[selfvoice] HeyGen submitted {video_id} ({avatar_version})")

        for _ in range(60):  # ~10 мин
            await asyncio.sleep(10)
            result = await asyncio.to_thread(heygen_check_status, video_id)
            st = result["status"]
            if st == "completed":
                video_url = result["video_url"]
                duration = result.get("duration", "?")
                async with httpx.AsyncClient() as c:
                    vb = (await c.get(video_url, timeout=120)).content
                video_file = ASSETS_DIR / f"heygen_{video_id[:8]}.mp4"
                video_file.write_bytes(vb)

                new_name = f"avatar_{look_name}.mp4"
                _cleanup_old_avatars(data, keep_filename=new_name)
                _save_to_project(data, new_name, str(video_file))
                if Path(audio_path).exists():
                    _save_to_project(data, "voice_merged.mp3", audio_path)

                nid = data.get("notion_page_id")
                if nid:
                    try:
                        await asyncio.to_thread(
                            update_notion_status, nid, "Аватар | генерации")
                    except Exception as _se:
                        logger.warning(f"[selfvoice] status advance: {_se}")

                cap = (f"🤖 Аватар готов — озвучен ТВОИМ голосом! "
                       f"({look_name}, {ver_label}, {duration}с)")
                # 24 мая 2026 — кнопки «что дальше» строим ДО отправки и
                # прикрепляем ПОД видео (reply_markup в send_video). Раньше
                # selfvoice-флоу только редактировал статус НАВЕРХУ и кнопок
                # под роликом не давал — приходилось скроллить вверх, чтобы
                # утвердить. Теперь как в обычном флоу аватара: всё под видео.
                try:
                    quota = await asyncio.to_thread(heygen_get_quota)
                except Exception:
                    quota = "?"
                _next_buttons = [
                    [InlineKeyboardButton("🔄 Другой лук / версия", callback_data="heygen_looks")],
                ]
                if not data.get("broll_approved"):
                    _next_buttons.append([InlineKeyboardButton("🎬 Подобрать B-roll", callback_data="broll")])
                if NOTION_GUIDES_DB and not data.get("guide_created"):
                    _next_buttons.append([InlineKeyboardButton("📎 Создать гайд", callback_data="create_guide")])
                _next_buttons.append([InlineKeyboardButton("📥 Скачать материалы", callback_data="download_project")])
                _next_buttons.append([InlineKeyboardButton("✅ Готово", callback_data="finish")])
                _next_kb = InlineKeyboardMarkup(_next_buttons)
                _video_caption = (
                    f"{cap}\n\n💰 Остаток HeyGen: {quota} кредитов\nЧто дальше?"
                )
                # 413-guard: >48 МБ → сжать (2-pass, сохраняя 1080p); не
                # выйдет → ссылка. Avatar IV даёт ~167 МБ на 30с.
                send_file = video_file
                if video_file.stat().st_size > 48 * 1024 * 1024:
                    cmp = ASSETS_DIR / f"heygen_{video_id[:8]}_tg.mp4"
                    ok = await asyncio.to_thread(
                        _compress_for_telegram, str(video_file), str(cmp), duration)
                    if ok:
                        send_file = cmp
                try:
                    with open(send_file, "rb") as vf:
                        await context.bot.send_video(
                            chat_id=chat_id, video=vf, caption=_video_caption,
                            supports_streaming=True, reply_markup=_next_kb,
                        )
                except Exception as _se:
                    logger.warning(f"[selfvoice] send_video: {_se}")
                    link = None
                    try:
                        link = await asyncio.to_thread(
                            save_media_permanent, str(video_file), "avatar_selfvoice")
                    except Exception:
                        pass
                    tail = (f"\n⚠️ Крупный файл — ссылка:\n{link}" if link
                            else "\n⚠️ Файл крупный, забери из «📥 Скачать материалы».")
                    await context.bot.send_message(
                        chat_id, _video_caption + tail, reply_markup=_next_kb)
                # Статус НАВЕРХУ больше не несёт «что дальше» — гасим в галочку,
                # вся навигация теперь под видео.
                try:
                    await status.edit_text("✅ Аватар готов — детали и кнопки под видео 👇")
                except Exception:
                    pass
                return
            if st == "failed":
                await status.edit_text(
                    f"❌ HeyGen не сгенерил аватар: {result.get('error')}")
                return
        await status.edit_text("❌ Таймаут генерации аватара (>10 мин).")
    except Exception as e:
        logger.error(f"[selfvoice] render failed: {e}", exc_info=True)
        await status.edit_text(f"❌ Ошибка генерации аватара: {e}")


async def _consume_selfvoice_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принять голосовое Максима для фичи «свой голос» → конвертнуть → аватар."""
    user_id = update.effective_user.id
    data = pending.get(user_id, {})
    voice = update.message.voice or update.message.audio
    if not voice:
        await update.message.reply_text("Пришли голосовое сообщение (запись).")
        return
    msg = await update.message.reply_text("🎤 Принял голосовое, конвертирую…")
    tg_file = await context.bot.get_file(voice.file_id)
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        await tg_file.download_to_drive(tmp.name)
        ogg = tmp.name
    mp3 = str(ASSETS_DIR / f"selfvoice_{user_id}.mp3")
    try:
        await asyncio.to_thread(
            subprocess.run,
            ["ffmpeg", "-y", "-i", ogg, "-c:a", "libmp3lame", "-b:a", "128k", mp3],
            capture_output=True, timeout=120,
        )
    except Exception as e:
        await msg.edit_text(f"❌ Не смог конвертировать аудио: {e}")
        return
    if not Path(mp3).exists() or Path(mp3).stat().st_size == 0:
        await msg.edit_text("❌ Конвертация аудио не удалась — пришли голосовое ещё раз.")
        return
    # 24 мая 2026 — НЕ генерируем аватар сразу. Генерация аватара = реальные
    # деньги HeyGen, поэтому переспрашиваем (как просил Максим: «он должен
    # спросить — запись в порядке? — и только потом генерировать»).
    # Сохраняем путь к mp3 + длительность, ждём подтверждения кнопкой.
    dur = 0
    try:
        dur = int(getattr(voice, "duration", 0) or 0)
    except Exception:
        dur = 0
    data["selfvoice_audio_path"] = mp3
    data["state"] = None  # дальше всё через кнопки, не через голос
    pending[user_id] = data
    _save_pending(pending)

    ver_label = {"v4": "Avatar IV", "v2": "Avatar 4", "v3": "Avatar 3"}.get(
        data.get("selfvoice_version", "v4"), "Avatar 3")
    look_name = data.get("selfvoice_look_name", "Аватар")
    dur_str = f"{dur} сек" if dur else "запись"
    await msg.edit_text(
        f"🎤 Голос получил ({dur_str}).\n\n"
        f"Послушай свою запись выше — всё чисто, без обрывов?\n"
        f"Если ок — генерирую аватар «{look_name}» ({ver_label}) твоим голосом.\n"
        f"Это спишет кредиты HeyGen.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Да, генерировать аватар",
                                  callback_data="selfvoice_confirm")],
            [InlineKeyboardButton("🔁 Перезаписать голос",
                                  callback_data="selfvoice_redo")],
            [InlineKeyboardButton("❌ Отмена", callback_data="selfvoice_cancel")],
        ]),
    )


async def _consume_voiceover_ownvoice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принять голосовое как ОЗВУЧКУ ролика (своим голосом вместо TTS).

    В отличие от `_consume_selfvoice_audio` (который шлёт запись сразу в аватар),
    здесь запись становится `voice_part_0.mp3` проекта/карточки — единственной
    частью озвучки. Дальше обычная генерация аватара (Avatar 3/IV) и сборка берут
    эту запись (мёрджат voice_part по числу voice_parts). Заменяет ИИ-голос.
    """
    user_id = update.effective_user.id
    data = pending.get(user_id, {})
    voice = update.message.voice or update.message.audio
    if not voice:
        await update.message.reply_text("Пришли голосовое сообщение (запись).")
        return
    msg = await update.message.reply_text("🎤 Принял голосовое, сохраняю как озвучку…")
    tg_file = await context.bot.get_file(voice.file_id)
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        await tg_file.download_to_drive(tmp.name)
        ogg = tmp.name
    mp3 = str(ASSETS_DIR / "voice_part_0.mp3")
    try:
        await asyncio.to_thread(
            subprocess.run,
            ["ffmpeg", "-y", "-i", ogg, "-c:a", "libmp3lame", "-b:a", "128k", mp3],
            capture_output=True, timeout=120,
        )
    except Exception as e:
        await msg.edit_text(f"❌ Не смог конвертировать аудио: {e}")
        return
    if not Path(mp3).exists() or Path(mp3).stat().st_size == 0:
        await msg.edit_text("❌ Конвертация не удалась — пришли голосовое ещё раз.")
        return

    # Своя запись = ОДНА часть озвучки целиком, заменяет TTS.
    text = (data.get("script") or "Своя озвучка (запись)").strip()[:300]
    data["voice_parts"] = [text]
    data["voice_approved"] = [True]
    data["state"] = None
    notion_id = data.get("notion_page_id")
    if notion_id:
        try:
            _save_voice_file(notion_id, 0, mp3)
            _save_voice_meta(notion_id, data["voice_parts"], data["voice_approved"])
        except Exception as e:
            logger.warning(f"[voiceover_ownvoice] save to card failed: {e}")
    try:
        _save_to_project(data, "voice_part_0.mp3", mp3)
    except Exception as e:
        logger.warning(f"[voiceover_ownvoice] save to project failed: {e}")
    pending[user_id] = data
    _save_pending(pending)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 Сгенерировать аватар", callback_data="heygen_looks")],
        [InlineKeyboardButton("✅ Готово", callback_data="finish")],
    ])
    await msg.edit_text(
        "✅ Своя озвучка сохранена — она заменила ИИ-голос для этого ролика.\n"
        "Дальше генерируй аватар: он будет говорить твоим голосом.",
        reply_markup=kb,
    )


async def process_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice messages — transcribe then generate script."""
    user_id = update.effective_user.id
    # Same contract as process_idea — restore cached card brand so voice-
    # originated ideas go through the right profile.
    _restore_brand_from_pending(user_id)

    # /heygen_test audio step — нужен сам файл, не транскрипция.
    # Перехватываем ДО Groq Whisper, иначе бесполезно расшифруем чужой mp3.
    state = pending.get(user_id, {}).get("state")
    if state == HEYGEN_TEST_STATE_AUDIO:
        await consume_heygen_test_audio(update, context)
        return

    # Фича «свой голос»: Максим записал голосовое для озвучки аватара.
    # Перехватываем ДО Whisper — нам нужен сам аудиофайл, не транскрипция.
    if state == "awaiting_selfvoice":
        await _consume_selfvoice_audio(update, context)
        return

    # «Свой голос» на шаге ОЗВУЧКИ: запись становится озвучкой ролика (вместо TTS).
    if state == "awaiting_voiceover_ownvoice":
        await _consume_voiceover_ownvoice(update, context)
        return

    logger.info(f"[user:{user_id}] Голосовое сообщение")
    msg = await update.message.reply_text("Расшифровываю голосовое...")

    voice = update.message.voice or update.message.audio
    tg_file = await context.bot.get_file(voice.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        await tg_file.download_to_drive(tmp.name)
        tmp_path = tmp.name

    try:
        # Groq API — быстрый облачный Whisper на GPU (2-3 сек)
        try:
            from openai import OpenAI

            groq_client = OpenAI(
                api_key=os.getenv("GROQ_API_KEY", ""),
                base_url="https://api.groq.com/openai/v1"
            )
            with open(tmp_path, "rb") as audio:
                transcript = groq_client.audio.transcriptions.create(
                    model="whisper-large-v3", file=audio, language="ru"
                )
            idea_text = transcript.text
        except Exception:
            await msg.edit_text(
                "Для голосовых сообщений нужен Groq API ключ.\n"
                "Добавь GROQ_API_KEY в .env файл.\n\n"
                "Пока можешь отправлять идеи текстом."
            )
            return

        user_id = update.effective_user.id
        current_state = pending.get(user_id, {}).get("state")

        # ─── TG-post surgical editor: voice instruction intake ───
        # State set by «✏️ Точечная правка» (idea-flow only). Must come
        # BEFORE Артёмов `is_tgpost_state()` check below — our state name
        # `idea_post_surg_edit` would NOT match `is_tgpost_state()` (it
        # checks for `tgpost_wait_*`) anyway, so order is for clarity.
        if current_state == "idea_post_surg_edit" and idea_text:
            try:
                await msg.edit_text(
                    f"🎤 Расшифровка инструкции:\n«{idea_text}»\n\n✏️ Применяю точечную правку…"
                )
            except Exception:
                pass
            await _handle_tgpost_surg_edit_instruction(update, context, idea_text.strip())
            return

        # ─── Carousel theme intake (voice) — Pipeline #6 ───
        # See text-path equivalent in process_idea above.
        if current_state == "awaiting_carousel_theme" and idea_text:
            if idea_text.strip().lower() in ("отмена", "отменить", "выйти", "стоп"):
                data = pending.get(user_id) or {}
                data["state"] = None
                data.pop("carousel_template", None)
                pending[user_id] = data
                _save_pending(pending)
                try:
                    await msg.edit_text("✖️ Карусель отменена.")
                except Exception:
                    pass
                return
            data = pending.get(user_id) or {}
            chosen_tpl = data.get("carousel_template")
            data["state"] = None
            data.pop("carousel_template", None)
            pending[user_id] = data
            _save_pending(pending)
            try:
                await msg.edit_text(
                    f"🎤 Расшифровка темы:\n«{idea_text}»\n\n🎨 Готовлю сценарий…"
                )
            except Exception:
                pass
            try:
                from carousel.handlers import generate_carousel_preview
            except Exception as e:
                logger.error(f"[carousel] import failed: {e}", exc_info=True)
                await update.message.reply_text(
                    f"❌ Модуль карусели не загружен: {e}",
                )
                return
            await generate_carousel_preview(
                update, context, claude,
                theme=idea_text.strip(),
                n_slides=None,
                chat_id=update.effective_chat.id,
                notion_url=None,
                template=chosen_tpl,
            )
            return

        # ─── Carousel surgical-edit intake (voice) — Pipeline #6 ───
        # See text-path equivalent in process_idea above.
        if current_state == "awaiting_carousel_surg_edit" and idea_text:
            if idea_text.strip().lower() in ("отмена", "отменить", "выйти", "стоп"):
                data = pending.get(user_id) or {}
                data["state"] = None
                pending[user_id] = data
                _save_pending(pending)
                try:
                    await msg.edit_text(
                        "↩️ Точечная правка отменена — сценарий не изменился.",
                    )
                except Exception:
                    pass
                return
            data = pending.get(user_id) or {}
            data["state"] = None
            pending[user_id] = data
            _save_pending(pending)
            try:
                await msg.edit_text(
                    f"🎤 Расшифровка правки:\n«{idea_text}»\n\n✏️ Применяю…"
                )
            except Exception:
                pass
            try:
                from carousel.handlers import apply_carousel_surgical_edit
            except Exception as e:
                logger.error(f"[carousel] import failed: {e}", exc_info=True)
                await update.message.reply_text(f"❌ Модуль карусели не загружен: {e}")
                return
            await apply_carousel_surgical_edit(
                update, context, claude, idea_text.strip(),
            )
            return

        # TG-post flow — голос как ответ на вопрос генератора постов
        if is_tgpost_state(current_state) and idea_text:
            try:
                await msg.edit_text(f"🎤 Расшифровка:\n«{idea_text}»\n\n✍️ Обрабатываю...")
            except Exception:
                pass
            consumed = await handle_tgpost_text(update, context, idea_text)
            if consumed:
                return

        # fal.ai flows — голос как промпт для /image или /video
        if is_fal_state(current_state) and idea_text:
            try:
                await msg.edit_text(f"🎤 Расшифровка:\n«{idea_text}»\n\n🚀 Запускаю генерацию...")
            except Exception:
                pass
            consumed = await consume_fal_prompt(update, context, idea_text)
            if consumed:
                return

        # If user is adding a note to Notion card
        if current_state == "notion_note":
            card_id = pending[user_id].get("notion_edit_card")
            card_title = pending[user_id].get("notion_edit_title", "")
            if card_id:
                try:
                    add_notion_note(card_id, idea_text)
                    pending[user_id]["state"] = None
                    _save_pending(pending)
                    await msg.edit_text(
                        f"✅ Заметка добавлена!\n\n"
                        f"📋 {card_title}\n"
                        f"📝 «{idea_text}»"
                    )
                except Exception as e:
                    logger.error(f"Ошибка добавления заметки: {e}", exc_info=True)
                    await msg.edit_text(f"Ошибка: {e}")
            return

        # Script instruction via voice — applies AI edit to preview/Notion script
        if current_state == "script_instruct_wait":
            instruction = (idea_text or "").strip()
            if not instruction:
                await msg.edit_text("Пустое голосовое — попробуй ещё раз.")
                return
            await msg.edit_text(f"🎤 Расшифровка:\n«{instruction}»\n\n✍️ Применяю правку...")
            await _apply_script_instruction(user_id, instruction, msg)
            return

        # If user is in edit mode, treat voice as edit instruction
        if current_state == "editing":
            pending[user_id]["state"] = None
            _save_pending(pending)
            await msg.edit_text(f"Расшифровка:\n{idea_text}\n\nПравлю сценарий...")
            await _edit_script(update, context, idea_text, status_msg=msg)

        # Ready script from voice
        elif current_state == "script_ready":
            pending[user_id]["state"] = None
            _save_pending(pending)
            await msg.edit_text(f"Расшифровка:\n{idea_text}\n\n📋 Структурирую...")
            try:
                script_text = idea_text.strip()
                struct_response = claude.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=512,
                    system=_build_structure_prompt(),
                    messages=[{"role": "user", "content": f"Идея: {script_text[:200]}\n\nСценарий: {script_text}"}],
                )
                raw_struct = struct_response.content[0].text.strip()
                if raw_struct.startswith("```"):
                    raw_struct = raw_struct.split("\n", 1)[1]
                    if raw_struct.endswith("```"):
                        raw_struct = raw_struct[:-3]
                    raw_struct = raw_struct.strip()
                card_data = json.loads(raw_struct)
                if card_data.get("rubric") not in _brand_rubrics():
                    card_data["rubric"] = "Свободный формат"
                card_data["platforms"] = _default_platforms()
                card_data["format"] = [f for f in card_data.get("format", []) if f in FORMATS] or ["Short video"]
                lines_s = script_text.strip().split("\n")
                card_data["cta"] = lines_s[-1] if lines_s else ""

                pending[user_id] = {"card_data": card_data, "script": script_text, "idea": script_text[:200]}
                _save_pending(pending)

                char_count = len(script_text)
                preview = f"📝 СЦЕНАРИЙ (готовый):\n\n{script_text}\n\n———\n📊 {char_count} символов\n"
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Утвердить → обложка", callback_data="approve"),
                     InlineKeyboardButton("🔄 Переписать", callback_data="rewrite")],
                    [InlineKeyboardButton("✏️ Внести правки", callback_data="edit_mode"),
                     InlineKeyboardButton("✏️ Другой хук", callback_data="new_hook")],
                    [InlineKeyboardButton("💾 Отложить как идею", callback_data="save_to_notion")],
                    [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
                ])
                await msg.edit_text(preview, reply_markup=keyboard)
            except Exception as e:
                logger.error(f"Ошибка /script voice: {e}", exc_info=True)
                await msg.edit_text(f"Ошибка: {e}")

        # Quick Notion card from voice
        elif current_state == "notion_quick":
            pending[user_id]["state"] = None
            _save_pending(pending)
            await msg.edit_text(f"Расшифровка:\n{idea_text}\n\n📋 Создаю карточку в Notion...")
            try:
                title = idea_text.strip()[:80]
                card_data = {"title": title, "cta": ""}
                notion_url, notion_page_id = await asyncio.to_thread(create_notion_card, card_data, idea_text.strip())
                pending.pop(user_id, None)
                _save_pending(pending)
                await msg.edit_text(
                    f"✅ Карточка создана!\n\n"
                    f"📋 {title}\n"
                    f"📋 Notion: {notion_url}\n"
                    f"📊 Статус: Идеи | старт"
                )
            except Exception as e:
                logger.error(f"Ошибка: {e}", exc_info=True)
                await msg.edit_text(f"Ошибка: {e}")

        # Quick voiceover from voice
        elif current_state == "voice_quick":
            await msg.edit_text(f"Расшифровка:\n{idea_text}\n\n🎙 Применяю интонацию и озвучиваю...")
            try:
                pending[user_id]["state"] = "voice_editing"
                pending[user_id]["script"] = idea_text.strip()
                # Run intonation on full script before splitting (full context).
                full_processed = _prepare_tts_intonation(idea_text.strip())
                logger.info(
                    f"TTS full_processed ({len(full_processed)} chars):\n{full_processed}"
                )
                _tp = _voice_target_parts(_get_active_brand().get("eleven_model_id"), full_processed)
                parts = split_script_to_parts(full_processed, target_parts=_tp)
                logger.info(
                    f"TTS split (target_parts={_tp}): "
                    + " | ".join(f"[{i}]({len(p)}) {p[:40]}…{p[-25:]}" for i, p in enumerate(parts))
                )
                pending[user_id]["voice_parts"] = parts
                pending[user_id]["voice_approved"] = [False] * len(parts)
                _save_pending(pending)
                for i, part_text in enumerate(parts):
                    voice_path = str(ASSETS_DIR / f"voice_part_{i}.mp3")
                    generate_voiceover(part_text, voice_path, skip_intonation=True)
                    with open(voice_path, "rb") as audio_file:
                        await update.message.reply_audio(
                            audio=audio_file,
                            title=f"Часть {i+1}/{len(parts)}",
                            caption=f"🎙 Часть {i+1}/{len(parts)}:\n\n«{part_text}»",
                        )
                await msg.edit_text(
                    _voice_panel_text(pending[user_id]),
                    reply_markup=_voice_panel_keyboard(pending[user_id]),
                )
            except Exception as e:
                logger.error(f"Ошибка: {e}", exc_info=True)
                await msg.edit_text(f"Ошибка: {e}")

        # Selfie title via voice
        elif current_state == "selfie_waiting_title":
            custom_title = (idea_text or "").strip()
            if custom_title:
                await msg.edit_text(f"Расшифровка: «{custom_title}»\n\nСоздаю карточку...")
                await _selfie_finalize(update, context, user_id, custom_title)
            else:
                await msg.edit_text("Не удалось расшифровать. Напиши название текстом.")

        else:
            # Any other voice = new idea
            if user_id in pending:
                pending.pop(user_id, None)
                _save_pending(pending)
            await msg.edit_text(f"Расшифровка:\n{idea_text}\n\nПишу сценарий...")
            await _generate_script(update, context, idea_text, status_msg=msg)
    finally:
        os.unlink(tmp_path)


async def _apply_script_instruction(user_id: int, instruction: str, msg) -> None:
    """Shared editor — applies a natural-language instruction to the current script.

    Source priority: if a preview is stashed (iterative edits), use that; otherwise
    fall back to the script currently saved in Notion. Result goes back into the
    preview stash — the user still has to click ✅ Сохранить to commit.
    """
    data = pending.get(user_id, {}) or {}
    card_id = data.get("script_preview_card") or data.get("script_instruct_card")
    title = data.get("script_preview_title") or data.get("script_instruct_title", "")

    if not card_id:
        await msg.edit_text("Не нашёл карточку для правки — начни заново с «✏️ С правкой».")
        return

    # Clear the waiting state regardless of outcome.
    pending[user_id]["state"] = None
    _save_pending(pending)

    try:
        # Prefer the most recent preview if we have one — lets the user iterate.
        source_script = (data.get("script_preview_text") or "").strip()
        source_label = "из превью"
        if not source_script:
            source_script = await asyncio.to_thread(fetch_notion_page_script, card_id)
            source_label = "из Notion"
        if not source_script:
            await msg.edit_text("У карточки пустой сценарий — используй «✏️ Заменить целиком».")
            return

        logger.info(
            f"[script_instruct] user={user_id} card={card_id[:8]} src={source_label} "
            f"instruction={instruction[:80]!r}"
        )

        # Detect "сделай длиннее" + извлечь явный диапазон если указан.
        asks_longer = _user_asked_for_longer(instruction)
        explicit_range = _extract_target_chars(instruction)
        logger.info(
            f"[script_instruct] asks_longer={asks_longer} "
            f"explicit_range={explicit_range} "
            f"(instruction: {instruction[:80]!r})"
        )

        # Length hint в system prompt — конкретный или дефолтный.
        # БАЗА: 420-500 символов (~30 сек аудио). Расширять ТОЛЬКО если
        # автор явно попросил.
        if explicit_range:
            length_hint = (
                f"АВТОР УКАЗАЛ ДИАПАЗОН ДЛИНЫ: {explicit_range[0]}-{explicit_range[1]} "
                f"символов. Уложись в этот диапазон, не больше и не меньше."
            )
        elif asks_longer:
            length_hint = (
                "Автор просит сделать длиннее — добавь содержание (примеры, "
                "детализация уже упомянутых фактов, эмоциональный градус), "
                "но НЕ воду. Целевой диапазон 500-650 символов."
            )
        else:
            length_hint = (
                "По длине держись базы: 420-500 символов (~30 секунд аудио). "
                "Не растягивай без необходимости — короткий ритмичный сценарий "
                "работает лучше длинного."
            )

        resp = await asyncio.to_thread(
            claude.messages.create,
            model="claude-opus-4-7",
            max_tokens=2048,
            system=(
                "Ты редактор сценариев для коротких вертикальных роликов. Тебе дают "
                "готовый сценарий и правку от автора. Выполни правку полностью — "
                "если автор просит добавить аналогию, пример, сарказм или новый "
                "блок, смело добавляй и перестраивай текст вокруг этого. Сохрани "
                "общий посыл.\n\n"
                f"{length_hint}\n\n"
                "⚠️ КРИТИЧЕСКИ ВАЖНО — НЕ ВЫДУМЫВАЙ КОНКРЕТНЫЕ ДЕТАЛИ, КОТОРЫХ "
                "НЕТ В ИСХОДНОМ СЦЕНАРИИ И НЕ УПОМЯНУЛ АВТОР В ПРАВКЕ:\n"
                "— материалы (телячья кожа, замша, лак — НЕЛЬЗЯ если не было)\n"
                "— детали (ремешки, пряжки, шов, подкладка — НЕЛЬЗЯ если не было)\n"
                "— текстуры/глянец/блеск (зеркальный глянец, матовость — НЕЛЬЗЯ "
                "если не было)\n"
                "— цвета (чёрный, бежевый — только если упомянуто)\n"
                "— цифры (количество, проценты, годы — только если в исходнике)\n\n"
                "Если автор просит «эпитеты» — используй ОБЩИЕ нейтральные слова "
                "(классические, мягкие, лёгкие, изящные, лаконичные, графичные), "
                "которые описывают ВПЕЧАТЛЕНИЕ, а не конкретные физические "
                "свойства. Если в правке нет конкретного факта — лучше написать "
                "общее, чем выдумать конкретное.\n\n"
                "Верни только итоговый текст без пояснений."
            ),
            messages=[
                {"role": "user", "content": f"Вот текущий сценарий:\n\n{source_script}\n\nВнеси эту правку: {instruction}"},
            ],
        )
        new_script = resp.content[0].text.strip()
        if new_script.upper().startswith("СЦЕНАРИЙ"):
            new_script = new_script.split("\n", 1)[-1].strip()

        # Shortener — БАЗА 420-500 символов, расширяем только по просьбе.
        if explicit_range:
            lo, hi = explicit_range
            if len(new_script) > hi + 50:
                new_script = await _force_shorten(
                    new_script, max_chars=hi + 50, target_lo=lo, target_hi=hi
                )
        elif asks_longer:
            new_script = await _force_shorten(
                new_script, max_chars=700, target_lo=500, target_hi=650
            )
        elif len(new_script) > 500:
            # База: 420-500 (по дефолту _force_shorten)
            new_script = await _force_shorten(new_script)

        # Stash preview — user confirms before it hits Notion.
        pending[user_id]["script_preview_text"] = new_script
        pending[user_id]["script_preview_card"] = card_id
        pending[user_id]["script_preview_title"] = title
        _save_pending(pending)

        preview = (
            f"📝 СЦЕНАРИЙ (с правкой) «{title}»:\n\n"
            f"{new_script}\n\n"
            f"———\n"
            f"📊 {len(new_script)} символов\n"
            f"✏️ Правка: «{instruction[:120]}»"
        )
        await msg.edit_text(
            preview,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Сохранить в Notion", callback_data=f"script_save:{card_id[:20]}"),
                    InlineKeyboardButton("🔄 Ещё вариант", callback_data=f"script_rewrite:{card_id[:20]}"),
                ],
                [
                    InlineKeyboardButton("✏️ Ещё правка", callback_data=f"script_instruct:{card_id[:20]}"),
                    InlineKeyboardButton("📋 Заменить своим текстом", callback_data=f"script_replace:{card_id[:20]}"),
                ],
                [
                    InlineKeyboardButton("❌ Отмена", callback_data=f"card_script:{card_id[:20]}"),
                ],
            ]),
        )
    except Exception as e:
        logger.error(f"script_instruct apply failed: {e}", exc_info=True)
        await msg.edit_text(f"Ошибка правки: {e}")


# Keywords that signal user wants a LONGER script — used by callers to skip
# or relax the auto-shortener. Detected case-insensitively as substrings.
SCRIPT_LONGER_KEYWORDS = (
    "длинн", "длиньше", "длинее",
    "больше символ", "больше знак", "больше текст",
    "развёрн", "разверн", "развить",
    "детальн", "подробн",
    "расшир", "удлин",
    "более 4", "более 5", "более 6", "более 7",  # «более 450 символов»
)


def _user_asked_for_longer(instruction: str) -> bool:
    """True if user instruction asks to make the script longer."""
    if not instruction:
        return False
    low = instruction.lower()
    return any(kw in low for kw in SCRIPT_LONGER_KEYWORDS)


def _extract_target_chars(instruction: str) -> tuple[int, int] | None:
    """Извлечь явный диапазон длины из инструкции пользователя.

    Понимает:
      "450-500 символов" → (450, 500)
      "до 500 символов"  → (None, 500) → возвращаем (max-100, 500)
      "более 500"        → (500, 700)
      "около 500"        → (450, 550)
    Возвращает None если ничего не нашлось.
    """
    if not instruction:
        return None
    import re
    low = instruction.lower()

    # "450-500", "450 — 500", "450 до 500"
    m = re.search(r"(\d{3,4})\s*[-–—до]+\s*(\d{3,4})", low)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if 200 <= lo < hi <= 1500:
            return (lo, hi)

    # "до 500"
    m = re.search(r"до\s+(\d{3,4})\s*(символ|знак)", low)
    if m:
        hi = int(m.group(1))
        if 200 <= hi <= 1500:
            return (max(200, hi - 100), hi)

    # "более 500", "больше 500", "от 500"
    m = re.search(r"(более|больше|от)\s+(\d{3,4})\s*(символ|знак)?", low)
    if m:
        lo = int(m.group(2))
        if 200 <= lo <= 1500:
            return (lo, lo + 200)

    # "около 500", "примерно 500"
    m = re.search(r"(около|примерно)\s+(\d{3,4})", low)
    if m:
        target = int(m.group(2))
        if 200 <= target <= 1500:
            return (max(200, target - 50), target + 50)

    return None


async def _force_shorten(
    script_text: str,
    max_chars: int = 500,
    target_lo: int = 420,
    target_hi: int = 500,
) -> str:
    """If script is over ``max_chars``, ask Sonnet to shorten to
    ``target_lo``-``target_hi`` range.

    Defaults — БАЗОВАЯ длина по правилу Артёма (4 мая 2026):
    420-500 символов = ~30 секунд аудио на ElevenLabs eleven_v3.
    Расширять (`max_chars > 500`) только когда автор явно попросил
    «длиннее / больше / 600+» — см. ``_user_asked_for_longer``
    и ``_extract_target_chars``. Иначе крушим до базы.

    Callers могут пробросить выше при явной просьбе:
      _force_shorten(text, max_chars=900, target_lo=600, target_hi=750)
    """
    if len(script_text) <= max_chars:
        return script_text

    for attempt in range(2):  # Try twice if needed
        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=900,
            messages=[
                {"role": "user", "content": (
                    f"Сократи этот сценарий до {target_lo}-{target_hi} символов. "
                    f"Убери повторы, лишние примеры, воду. Сохрани первую фразу "
                    f"(хук) и последнюю (CTA). Верни ТОЛЬКО текст, без "
                    f"комментариев:\n\n{script_text}"
                )},
            ],
        )
        result = response.content[0].text.strip()
        if result.upper().startswith("СЦЕНАРИЙ"):
            result = result.split("\n", 1)[-1].strip()
        if len(result) <= max_chars:
            return result
        script_text = result  # Try again with shortened version

    return script_text


# COVER_TEXT_PROMPT loaded from cover_prompt.txt above


async def _generate_cover_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate 5 viral cover text options."""
    user_id = update.effective_user.id
    data = pending.get(user_id)
    if not data:
        return

    msg = await update.message.reply_text("Генерирую варианты...")

    try:
        # Collect previously shown options to avoid repeats
        prev_options = data.get("all_cover_options", [])
        exclude_text = ""
        if prev_options:
            exclude_text = f"\n\nУже предлагались (НЕ ПОВТОРЯЙ и не используй те же слова): {', '.join(prev_options)}"

        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            system=COVER_TEXT_PROMPT,
            messages=[
                {"role": "user", "content": f"Сценарий:\n{data['script']}\n\nПридумай 5 вирусных текстов для обложки. Найди в сценарии самый шокирующий факт или цифру — и построй обложку вокруг него. Каждый текст должен ИНТРИГОВАТЬ. Каждый на новой строке, только текст, без нумерации.{exclude_text}"},
            ],
        )
        options_text = response.content[0].text.strip()
        options = [line.strip().strip('"').strip("«»").strip("-").strip() for line in options_text.split("\n") if line.strip()]
        options = [o for o in options if 10 <= len(o) <= 50 and len(o.split()) >= 2][:5]

        # Save all shown options for dedup
        data.setdefault("all_cover_options", []).extend(options)

        if not options:
            await msg.edit_text("Не получилось сгенерировать. Напиши свой вариант.")
            return

        # Create buttons for each option
        buttons = [[InlineKeyboardButton(opt, callback_data=f"cover_pick:{i}")] for i, opt in enumerate(options)]
        buttons.append([InlineKeyboardButton("🔄 Ещё варианты", callback_data="cover_options")])
        buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])

        data["cover_options"] = options
        data["state"] = "cover_approval"
        _save_pending(pending)

        keyboard = InlineKeyboardMarkup(buttons)
        await msg.edit_text(
            "🖼 Выбери текст для обложки или напиши свой:\n\n"
            + "\n".join(f"• {opt}" for opt in options),
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.error(f"Ошибка: {e}", exc_info=True)
        await msg.edit_text(f"Ошибка: {e}")


async def _show_cover_photo_picker(bot, chat_id: int, data: dict, *, count: int = 3) -> bool:
    """Show ``count`` random pool photos as an album + [1][2][3] + reload buttons.

    Replaces the old single-photo «✅ Использовать это фото / 🎲 Другое фото»
    screen so the user picks a cover background from a small spread instead of
    re-rolling one at a time. The chosen file paths are cached in
    ``data["cover_photo_choices"]`` so the ``cover_photo:N`` callback can map a
    button index back to a path. Returns ``True`` if the picker was shown,
    ``False`` if the brand pool has nothing usable (caller falls back).
    """
    pool_dir = _avatars_dir_for_brand(_get_active_brand_name())
    avatars = []
    if pool_dir.exists():
        avatars = sorted(
            f.name for f in pool_dir.iterdir()
            if f.suffix.lower() in (".jpg", ".jpeg", ".png")
        )
    if not avatars:
        return False

    n = min(count, len(avatars))
    picks = random.sample(avatars, n)
    data["cover_photo_choices"] = [str(pool_dir / name) for name in picks]
    data["state"] = "cover_approval"
    _save_pending(pending)

    # Album (media group can't carry inline buttons → buttons go in a follow-up).
    try:
        from contextlib import ExitStack
        from telegram import InputMediaPhoto
        if n >= 2:
            with ExitStack() as stack:
                media = [
                    InputMediaPhoto(
                        media=stack.enter_context(open(p, "rb")),
                        caption=(f"Фото для обложки — варианты 1–{n}" if i == 0 else None),
                    )
                    for i, p in enumerate(data["cover_photo_choices"])
                ]
                await bot.send_media_group(chat_id=chat_id, media=media)
        else:
            with open(data["cover_photo_choices"][0], "rb") as f:
                await bot.send_photo(chat_id=chat_id, photo=f)
    except Exception as e:
        logger.error(f"[cover_picker] send album failed: {e}", exc_info=True)
        return False

    num_row = [
        InlineKeyboardButton(str(i + 1), callback_data=f"cover_photo:{i}")
        for i in range(n)
    ]
    buttons = InlineKeyboardMarkup([
        num_row,
        [InlineKeyboardButton("🔄 Другие фото", callback_data="cover_photo:reload")],
        [InlineKeyboardButton("📤 Загрузить своё фото в библиотеку", callback_data="cover_pool_upload")],
    ])
    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"📷 Выбери фото для обложки — нажми 1–{n}.\n"
            f"Не нравятся — «🔄 Другие фото» подберёт ещё."
        ),
        reply_markup=buttons,
    )
    return True


async def _send_cover_text_options(bot, chat_id: int, data: dict, status_msg=None):
    """Generate 5 viral cover-text options and show them as pick buttons.

    Shared by the photo-picker step and other entry points so the cover-text
    generation lives in one place. If ``status_msg`` is given it is edited in
    place; otherwise a fresh message is sent.
    """
    if status_msg is None:
        status_msg = await bot.send_message(
            chat_id=chat_id, text="🖼 Генерирую варианты обложки..."
        )
    try:
        _brand = _get_active_brand()
        _cover_system = _brand_cover_prompt(COVER_TEXT_PROMPT)
        prev_options = data.get("all_cover_options", [])
        exclude_text = ""
        if prev_options:
            exclude_text = (
                f"\n\nУже предлагались (НЕ ПОВТОРЯЙ и не используй те же слова): "
                f"{', '.join(prev_options)}"
            )
        response = claude.messages.create(
            model=COVER_MODEL,
            max_tokens=300,
            system=_cover_system,
            messages=[
                {"role": "user", "content": f"Сценарий:\n{data['script']}\n\nПридумай 5 вирусных текстов для обложки. Найди в сценарии самый шокирующий факт или цифру — и построй обложку вокруг него. Каждый текст должен ИНТРИГОВАТЬ. Каждый на новой строке, только текст, без нумерации.{exclude_text}"},
            ],
        )
        options_text = response.content[0].text.strip()
        options = [line.strip().strip('"').strip("«»").strip("-").strip() for line in options_text.split("\n") if line.strip()]
        options = [o for o in options if 10 <= len(o) <= 50 and len(o.split()) >= 2][:5]

        data.setdefault("all_cover_options", []).extend(options)
        if not options:
            await status_msg.edit_text("Не получилось сгенерировать. Напиши свой вариант.")
            return

        buttons = [[InlineKeyboardButton(opt, callback_data=f"cover_pick:{i}")] for i, opt in enumerate(options)]
        buttons.append([InlineKeyboardButton("🔄 Ещё варианты", callback_data="cover_options")])
        buttons.append([InlineKeyboardButton("◀️ Сменить фото", callback_data="cover_photo:reload")])
        buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])

        data["cover_options"] = options
        data["state"] = "cover_approval"
        _save_pending(pending)

        await status_msg.edit_text(
            "🖼 Выбери текст для обложки или напиши свой:\n\n"
            + "\n".join(f"• {opt}" for opt in options),
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except Exception as e:
        logger.error(f"[cover_text] Ошибка: {e}", exc_info=True)
        await status_msg.edit_text(f"Ошибка: {e}")


async def _edit_script(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    edit_instruction: str,
    status_msg=None,
):
    """Edit pending script based on user's text/voice instruction."""
    user_id = update.effective_user.id
    data = pending.get(user_id)
    if not data:
        return

    if status_msg is None:
        status_msg = await update.message.reply_text("Правлю сценарий...")

    try:
        response = claude.messages.create(
            model="claude-opus-4-7",
            max_tokens=1024,
            system="Ты редактор сценариев для коротких вертикальных роликов. Тебе дают готовый сценарий и правку от автора. Выполни правку полностью — если автор просит добавить аналогию, пример, сарказм или новый блок, смело добавляй и перестраивай текст вокруг этого. Сохрани общий посыл и длину (400-600 символов), но не бойся переписать абзацы ради качества. Верни только итоговый текст без пояснений.",
            messages=[
                {"role": "user", "content": f"Вот текущий сценарий:\n\n{data['script']}\n\nВнеси эту правку: {edit_instruction}"},
            ],
        )
        new_script = response.content[0].text.strip()
        if new_script.upper().startswith("СЦЕНАРИЙ"):
            new_script = new_script.split("\n", 1)[-1].strip()

        # Force shorten if over 500 chars
        new_script = await _force_shorten(new_script)

        data["script"] = new_script
        _save_pending(pending)
        char_count = len(new_script)

        preview = (
            f"📝 СЦЕНАРИЙ (отредактирован):\n\n"
            f"{new_script}\n\n"
            f"———\n"
            f"📊 {char_count} символов\n"
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Утвердить → обложка", callback_data="approve"),
                    InlineKeyboardButton("🔄 Переписать", callback_data="rewrite"),
                ],
                [
                    InlineKeyboardButton("✏️ Внести правки", callback_data="edit_mode"),
                    InlineKeyboardButton("✏️ Другой хук", callback_data="new_hook"),
                ],
                [InlineKeyboardButton("💾 Отложить как идею", callback_data="save_to_notion")],
                [
                    InlineKeyboardButton("❌ Отмена", callback_data="cancel"),
                ],
            ]
        )
        await status_msg.edit_text(preview, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка: {e}", exc_info=True)
        await status_msg.edit_text(f"Ошибка: {e}")


async def _generate_script(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    idea_text: str,
    status_msg=None,
):
    """Generate script using our prompt, show for approval."""
    user_id = update.effective_user.id
    logger.info(f"[user:{user_id}] Генерация сценария: {idea_text[:80]}...")

    # Show the active brand in the first status message so Artem always knows
    # which profile this session is recording for. For "default" we stay quiet
    # (it's the overwhelming majority of work — no need for noise).
    _brand_now = _get_active_brand_name()
    _brand_tag = (
        f"🏷 Бренд: *{_brand_now}* — _{BRANDS[_brand_now].get('description', '')}_\n\n"
        if _brand_now != "default" else ""
    )

    if status_msg is None:
        status_msg = await update.message.reply_text(
            f"{_brand_tag}Пишу сценарий...",
            parse_mode="Markdown" if _brand_tag else None,
        )

    try:
        # Step 0: Extract URLs and fetch article content
        urls = re.findall(r'https?://[^\s<>"]+', idea_text)
        if urls:
            await status_msg.edit_text("🌐 Читаю статью...")
            import httpx
            for url in urls[:2]:  # max 2 URLs
                try:
                    article_text = ""
                    full_resp_text = ""

                    # --- Twitter/Nitter: use FxTwitter API (fast, reliable) ---
                    if _is_twitter_url(url):
                        tweet_data = await _fetch_tweet_via_fxtwitter(url)
                        if tweet_data and tweet_data["text"]:
                            tweet_text = tweet_data["text"]
                            author = tweet_data.get("author", "")
                            article_text = f"Твит от {author}:\n{tweet_text}"
                            # If the tweet links to an article, fetch that too
                            for outbound in tweet_data.get("outbound_urls", [])[:1]:
                                try:
                                    jina_out = f"https://r.jina.ai/{outbound}"
                                    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
                                        resp_out = await client.get(jina_out, headers={"User-Agent": "Mozilla/5.0"})
                                        if resp_out.status_code == 200 and len(resp_out.text) > 300:
                                            if not _jina_text_is_garbage(resp_out.text):
                                                article_text += f"\n\n--- СТАТЬЯ ИЗ ССЫЛКИ В ТВИТЕ ---\n{resp_out.text[:6000]}"
                                                logger.info(f"Fetched linked article from tweet: {outbound}")
                                except Exception as e:
                                    logger.warning(f"Failed to fetch tweet outbound link {outbound}: {e}")

                            # Save video URLs for B-roll
                            if tweet_data.get("video_urls"):
                                pending[user_id] = pending.get(user_id) or {}
                                pending[user_id]["twitter_video_urls"] = tweet_data["video_urls"][:3]
                                logger.info(f"Found video in tweet: {tweet_data['video_urls'][:3]}")

                            logger.info(f"Fetched tweet via FxTwitter: {url} ({len(article_text)} chars)")
                        else:
                            logger.warning(f"FxTwitter returned no content for {url}")
                            # Skip Jina/HTML fallback — they don't work for Twitter
                            continue

                    # --- Regular URLs: Jina → HTML fallback ---
                    if not article_text:
                        # --- Try 1: Jina Reader ---
                        jina_url = f"https://r.jina.ai/{url}"
                        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
                            resp = await client.get(jina_url, headers={"User-Agent": "Mozilla/5.0"})
                            if resp.status_code == 200 and len(resp.text) > 200:
                                full_resp_text = resp.text
                                if not _jina_text_is_garbage(resp.text):
                                    article_text = resp.text[:8000]
                                    logger.info(f"Fetched article via Jina: {url} ({len(article_text)} chars)")
                                else:
                                    logger.warning(f"Jina returned nav-menu garbage for {url}, trying fallback")
                            else:
                                logger.warning(f"Jina returned {resp.status_code} for {url}")

                    # --- Try 2: Direct HTML fetch + meta/JSON-LD extraction ---
                    if not article_text:
                        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
                            headers = {
                                "User-Agent": (
                                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                                    "Chrome/120.0.0.0 Safari/537.36"
                                ),
                            }
                            resp2 = await client.get(url, headers=headers)
                            if resp2.status_code == 200:
                                full_resp_text = full_resp_text or resp2.text
                                extracted = _extract_article_from_html(resp2.text)
                                if len(extracted) > 100:
                                    article_text = extracted
                                    logger.info(f"Fallback HTML extraction: {url} ({len(article_text)} chars)")
                                else:
                                    logger.warning(f"Fallback extraction too short ({len(extracted)} chars) for {url}")

                    if article_text:
                        idea_text += f"\n\n--- СТАТЬЯ ПО ССЫЛКЕ ({url}) ---\n{article_text}"
                        # Save source URL
                        pending[user_id] = pending.get(user_id) or {}
                        src_list = pending[user_id].get("source_urls", [])
                        if url not in src_list:
                            src_list.append(url)
                        pending[user_id]["source_urls"] = src_list

                        # Extract YouTube links from article for B-roll
                        yt_urls = extract_youtube_urls(full_resp_text)
                        if yt_urls:
                            pending[user_id]["youtube_urls"] = yt_urls[:3]
                            logger.info(f"Found YouTube URLs in article: {yt_urls[:3]}")
                    else:
                        logger.warning(f"All extraction methods failed for {url}")

                except Exception as e:
                    logger.warning(f"Failed to fetch URL {url}: {e}")
            await status_msg.edit_text("✍️ Пишу сценарий...")

        # Step 1: Generate script (brand-aware system prompt)
        _brand = _get_active_brand()
        _script_system = _brand_script_prompt(SCRIPT_PROMPT)
        script_response = claude.messages.create(
            model="claude-opus-4-7",
            max_tokens=1024,
            system=_script_system,
            messages=[{"role": "user", "content": idea_text}],
        )
        script_text = script_response.content[0].text.strip()

        # Remove "СЦЕНАРИЙ:" prefix if present
        if script_text.upper().startswith("СЦЕНАРИЙ"):
            script_text = script_text.split("\n", 1)[-1].strip()

        # Force shorten if over 500 chars
        script_text = await _force_shorten(script_text)

        # Step 2: Structure for Notion card
        struct_response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=_build_structure_prompt(),
            messages=[{"role": "user", "content": f"Идея: {idea_text}\n\nСценарий: {script_text}"}],
        )
        raw_struct = struct_response.content[0].text.strip()

        # Parse JSON
        if raw_struct.startswith("```"):
            raw_struct = raw_struct.split("\n", 1)[1]
            if raw_struct.endswith("```"):
                raw_struct = raw_struct[:-3]
            raw_struct = raw_struct.strip()

        card_data = json.loads(raw_struct)

        # Validate
        if card_data.get("rubric") not in _brand_rubrics():
            card_data["rubric"] = "Свободный формат"
        # Default platforms: personal instagram + youtube shorts + telegram
        card_data["platforms"] = _default_platforms()
        card_data["format"] = [
            f for f in card_data.get("format", []) if f in FORMATS
        ] or ["Short video"]

        # Extract CTA from script (last line usually)
        lines = script_text.strip().split("\n")
        card_data["cta"] = lines[-1] if lines else ""
        logger.info(f"[user:{user_id}] Сценарий готов: {len(script_text)} символов, рубрика: {card_data['rubric']}")

    except json.JSONDecodeError:
        await status_msg.edit_text(
            f"Ошибка структурирования. Попробуй ещё раз.\n\nСценарий:\n{script_text[:500]}"
        )
        return
    except Exception as e:
        logger.error(f"Ошибка: {e}", exc_info=True)
        await status_msg.edit_text(f"Ошибка: {e}")
        return

    # Save pending data (preserve source_urls and youtube_urls from article reading)
    user_id = update.effective_user.id
    prev_data = pending.get(user_id, {})
    prev_yt_urls = prev_data.get("youtube_urls", [])
    prev_src_urls = prev_data.get("source_urls", [])
    pending[user_id] = {
        "card_data": card_data,
        "script": script_text,
        "idea": idea_text,
    }
    if prev_yt_urls:
        pending[user_id]["youtube_urls"] = prev_yt_urls
        logger.info(f"Preserved {len(prev_yt_urls)} YouTube URLs: {prev_yt_urls}")
    if prev_src_urls:
        pending[user_id]["source_urls"] = prev_src_urls
        logger.info(f"Preserved {len(prev_src_urls)} source URLs: {prev_src_urls}")
    _save_pending(pending)

    # Count characters
    char_count = len(script_text)

    # Format preview
    preview = (
        f"📝 СЦЕНАРИЙ:\n\n"
        f"{script_text}\n\n"
        f"———\n"
        f"📊 {char_count} символов\n"
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Утвердить → обложка", callback_data="approve"),
                InlineKeyboardButton("🔄 Переписать", callback_data="rewrite"),
            ],
            [
                InlineKeyboardButton("✏️ Внести правки", callback_data="edit_mode"),
                InlineKeyboardButton("✏️ Другой хук", callback_data="new_hook"),
            ],
            [InlineKeyboardButton("💾 Отложить как идею", callback_data="save_to_notion")],
            [
                InlineKeyboardButton("❌ Отмена", callback_data="cancel"),
            ],
        ]
    )

    await status_msg.edit_text(preview, reply_markup=keyboard)


def _voice_panel_text(data: dict) -> str:
    """Build voice control panel text."""
    parts = data.get("voice_parts", [])
    approved = data.get("voice_approved", [False] * len(parts))
    lines = []
    for i, p in enumerate(parts):
        status = "✅" if (i < len(approved) and approved[i]) else "⏳"
        lines.append(f"{status} Часть {i+1}: «{p[:60]}{'...' if len(p) > 60 else ''}»")
    return (
        "🎙 Озвучка:\n\n"
        + "\n".join(lines)
        + "\n\nТекстом: часть 1: новый текст"
    )


def _voice_panel_keyboard(data: dict) -> InlineKeyboardMarkup:
    """Build voice control panel keyboard."""
    parts = data.get("voice_parts", [])
    approved = data.get("voice_approved", [False] * len(parts))
    buttons = []
    for i in range(len(parts)):
        row = []
        if i < len(approved) and approved[i]:
            row.append(InlineKeyboardButton(f"✅ Часть {i+1}", callback_data=f"voice_ok:{i}"))
        else:
            row.append(InlineKeyboardButton(f"✅ Ок {i+1}", callback_data=f"voice_ok:{i}"))
        row.append(InlineKeyboardButton(f"🔄 {i+1}", callback_data=f"revoice:{i}"))
        row.append(InlineKeyboardButton(f"✏️ {i+1}", callback_data=f"vedit:{i}"))
        row.append(InlineKeyboardButton(f"🔧 {i+1}", callback_data=f"voice_cfg:{i}"))
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🔄 Переозвучить всё", callback_data="voiceover")])
    # Альтернатива TTS: озвучить ролик СВОИМ голосом (запись заменяет ИИ-голос).
    buttons.append([InlineKeyboardButton("🎤 Озвучить своим голосом", callback_data="voiceover_ownvoice")])
    if all(approved):
        buttons.append([InlineKeyboardButton("✅ Всё утверждено — готово", callback_data="finish")])
    return InlineKeyboardMarkup(buttons)


# Развилка ДО запуска TTS: ИИ-голос (ElevenLabs) или свой голос — чтобы при
# «свой голос» не гонять ElevenLabs впустую (Артём 11 июня).
_VOICEOVER_CHOICE_TEXT = (
    "🎙 Как озвучить ролик?\n\n"
    "🤖 ИИ-голос — синтез ElevenLabs (клон голоса Максима).\n"
    "🎤 Своим голосом — пришлёшь запись, она станет озвучкой; "
    "ElevenLabs не используется."
)


def _voiceover_choice_keyboard(back_cb: str | None = None) -> InlineKeyboardMarkup:
    """Клавиатура развилки озвучки: ИИ-голос / свой голос (+опц. назад)."""
    rows = [
        [InlineKeyboardButton("🤖 ИИ-голос (ElevenLabs)", callback_data="voiceover")],
        [InlineKeyboardButton("🎤 Своим голосом", callback_data="voiceover_ownvoice")],
    ]
    if back_cb:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back_cb)])
    return InlineKeyboardMarkup(rows)


def _voice_part_ps_default() -> dict:
    """Дефолтные настройки part_settings — учитывают модель активного бренда.

    v3 → один смысловой параметр stability (0.0 Creative … 1.0 Robust) +
    speed/similarity. style на v3 не используется (передаётся 0.0).
    v2 → исторические 4 параметра.
    """
    b = _get_active_brand()
    model = b.get("eleven_model_id") or "eleven_multilingual_v2"
    if model.startswith("eleven_v3"):
        return {
            "model": model,
            "sp": 1.0,
            "st": 0.0,
            "sb": b.get("eleven_v3_stability", 0.0),  # Creative по умолчанию
            "sm": 0.75,
        }
    return {
        "model": model,
        "sp": VOICE_SETTINGS["speed"],
        "st": VOICE_SETTINGS["style"],
        "sb": VOICE_SETTINGS["stability"],
        "sm": VOICE_SETTINGS["similarity_boost"],
    }


def _voice_settings_text(idx: int, ps: dict) -> str:
    """Текстовая панель над клавиатурой — model-aware."""
    model = ps.get("model") or "eleven_multilingual_v2"
    is_v3 = model.startswith("eleven_v3")
    head = (
        f"🔧 Настройки части {idx+1}\n"
        f"🤖 Модель: {'v3' if is_v3 else 'v2'}\n\n"
    )
    if is_v3:
        sb = ps.get("sb", 0.0)
        preset = "Creative" if sb < 0.2 else ("Natural" if sb < 0.7 else "Robust")
        return (
            head
            + f"⚖️ Stability: {sb} ({preset})\n"
            + f"🏎 Speed: {ps['sp']}\n"
            + f"🎯 Similarity: {ps['sm']}\n\n"
            + "Меняй пресет / ползунки и жми 🎙 Озвучить."
        )
    return (
        head
        + f"🏎 Speed: {ps['sp']}\n"
        + f"🎭 Style: {ps['st']}\n"
        + f"⚖️ Stability: {ps['sb']}\n"
        + f"🎯 Similarity: {ps['sm']}\n\n"
        + "Меняй ползунки и жми 🎙 Озвучить."
    )


def _voice_settings_keyboard(idx: int, ps: dict) -> InlineKeyboardMarkup:
    """Клавиатура настроек voice-part. Зависит от модели в ps['model']."""
    model = ps.get("model") or "eleven_multilingual_v2"
    is_v3 = model.startswith("eleven_v3")
    buttons: list[list[InlineKeyboardButton]] = []

    if is_v3:
        # 3-пресетная stability + speed + similarity.
        sb = ps.get("sb", 0.0)
        def _mark(target: float) -> str:
            return "● " if abs(sb - target) < 0.1 else ""
        buttons.append([
            InlineKeyboardButton(f"{_mark(0.0)}🎨 Creative", callback_data=f"vstab:{idx}:creative"),
            InlineKeyboardButton(f"{_mark(0.5)}🌊 Natural", callback_data=f"vstab:{idx}:natural"),
            InlineKeyboardButton(f"{_mark(1.0)}🪨 Robust", callback_data=f"vstab:{idx}:robust"),
        ])
        buttons.append([
            InlineKeyboardButton("➖", callback_data=f"vadj:{idx}:sp:-"),
            InlineKeyboardButton(f"🏎 Speed: {ps['sp']}", callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"vadj:{idx}:sp:+"),
        ])
        buttons.append([
            InlineKeyboardButton("➖", callback_data=f"vadj:{idx}:sm:-"),
            InlineKeyboardButton(f"🎯 Simil: {ps['sm']}", callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"vadj:{idx}:sm:+"),
        ])
        model_label = "🤖 v3 → переключить на v2"
    else:
        buttons.extend([
            [InlineKeyboardButton("➖", callback_data=f"vadj:{idx}:sp:-"),
             InlineKeyboardButton(f"🏎 Speed: {ps['sp']}", callback_data="noop"),
             InlineKeyboardButton("➕", callback_data=f"vadj:{idx}:sp:+")],
            [InlineKeyboardButton("➖", callback_data=f"vadj:{idx}:st:-"),
             InlineKeyboardButton(f"🎭 Style: {ps['st']}", callback_data="noop"),
             InlineKeyboardButton("➕", callback_data=f"vadj:{idx}:st:+")],
            [InlineKeyboardButton("➖", callback_data=f"vadj:{idx}:sb:-"),
             InlineKeyboardButton(f"⚖️ Stab: {ps['sb']}", callback_data="noop"),
             InlineKeyboardButton("➕", callback_data=f"vadj:{idx}:sb:+")],
            [InlineKeyboardButton("➖", callback_data=f"vadj:{idx}:sm:-"),
             InlineKeyboardButton(f"🎯 Simil: {ps['sm']}", callback_data="noop"),
             InlineKeyboardButton("➕", callback_data=f"vadj:{idx}:sm:+")],
        ])
        model_label = "🤖 v2 → переключить на v3"

    buttons.append([InlineKeyboardButton(model_label, callback_data=f"vmodel:{idx}")])
    buttons.append([InlineKeyboardButton("🎙 Озвучить с этими настройками", callback_data=f"vgen:{idx}")])
    buttons.append([
        InlineKeyboardButton(f"✅ Принять часть {idx+1}", callback_data=f"vsok:{idx}"),
        InlineKeyboardButton("✏️ Текст", callback_data=f"vedit:{idx}"),
    ])
    buttons.append([InlineKeyboardButton("⬅️ Назад к панели", callback_data="voice_back")])
    return InlineKeyboardMarkup(buttons)


async def _render_tgphoto_menu(query, context, data: dict) -> None:
    """Re-draw the «📷 Фото к TG-посту» root menu.

    Extracted as a helper (Codex review #3 P0): PTB v21+ marks
    ``CallbackQuery`` immutable, so we cannot mutate ``query.data`` and
    re-enter ``handle_callback``. Helpers like this one are called
    directly from sibling callback branches that need to redraw the
    menu (``tgphoto_clear``, ``tgphoto_cancel_send``).

    Side effects:
      * Clears `tgphoto_awaiting_reply` (returning to menu = exit reply mode)
      * Persists `pending` if state changed
    """
    if data.get("tgphoto_awaiting_reply"):
        data["tgphoto_awaiting_reply"] = False
        _save_pending(pending)
    tg_photos = data.get("selfie_tg_photos", []) or []
    cats = _tg_photo_categories_with_counts()
    total = sum(n for _, n in cats)
    non_empty_cats = [(c, n) for c, n in cats if n > 0]

    # ── Header text — короткий статус, без папки на сервере (тех. деталь,
    # клиенту не нужна).
    bank_line = (
        f"В банке на сервере: *{total}*"
        if total > 0
        else "Банк на сервере пока пустой — можно только прислать фото из чата"
    )
    text = (
        f"📷 *Фото к TG-посту*\n\n"
        f"Прикреплено к этому посту: *{len(tg_photos)}*\n"
        f"{bank_line}\n\n"
        f"Что делаем?"
    )
    kb_rows: list[list[InlineKeyboardButton]] = []

    # ── Button «Весь банк» — смотреть ВСЕ фото из всех категорий. Не
    # показываем когда банк пуст (бессмысленно), и не показываем когда
    # в банке всего одна категория с фото (тогда «Весь банк» = «эта
    # категория», была бы видимая дубль-кнопка → путаница).
    if total > 0 and len(non_empty_cats) >= 2:
        kb_rows.append([
            InlineKeyboardButton(
                f"🗂 Смотреть весь банк ({total})",
                callback_data="tgphoto_lib",
            )
        ])

    # ── Кнопки по категориям. Каждая открывает свой подбор. Если всего
    # одна заполненная категория — она и будет единственным способом
    # зайти в банк (без дубль-кнопки «Весь банк»).
    for cat, n in non_empty_cats:
        kb_rows.append([
            InlineKeyboardButton(
                f"📁 {cat} ({n})",
                callback_data=f"tgphoto_lib:cat:{cat}",
            )
        ])

    kb_rows.append([InlineKeyboardButton(
        "📤 Прислать своё фото в чат",
        callback_data="tgphoto_send",
    )])
    if tg_photos:
        kb_rows.append([InlineKeyboardButton(
            f"🗑 Убрать прикреплённые фото ({len(tg_photos)})",
            callback_data="tgphoto_clear",
        )])
    # Подпись кнопки возврата зависит от того, какой флоу открыл меню:
    # сценарный TG-пост / idea→tgpost → «к посту»; крос-пост → «к кросс-постингу».
    if data.get("tgphoto_return_script") or data.get("tgphoto_return_idea_idx") is not None:
        _back_label = "◀️ Назад к посту"
    else:
        _back_label = "◀️ Назад к кросс-постингу"
    kb_rows.append([InlineKeyboardButton(
        _back_label,
        callback_data="tgphoto_done",
    )])

    try:
        await query.edit_message_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb_rows),
        )
    except Exception:
        await context.bot.send_message(
            chat_id=query.message.chat_id, text=text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb_rows),
        )


def _compose_publication_descriptions(script_text: str) -> tuple[list[str], str]:
    """Сгенерировать 3 варианта описания для публикации + извлечённый CTA.

    Вынесено из handler'а `gen_description` (10 июня) чтобы переиспользовать
    в других пайплайнах (caption Instagram-карусели). Бренд-aware: автор,
    TG-канал и CTA тянутся из активного бренда. Без хэштегов (сознательно —
    алгоритм их не учитывает). Возвращает (variants, extracted_cta).
    """
    # Бренд-зависимые подстановки. Раньше промпт жёстко вшивал
    # «Артём Панфёров / @panferovai / AI-студия» — для бренда maksim
    # это давало неверное описание. Теперь автор + TG-канал + CTA
    # тянутся из активного бренда.
    _brand_d = _get_active_brand()
    if _get_active_brand_name() == "maksim":
        _author = "Максима Юмсунова"
        _author_bio = (
            "Максим Юмсунов — владелец Life Drive (картинг + "
            "глэмпинг в Тюмени). Канал про реальный бизнес из "
            "своей практики."
        )
        _tg_handle = _brand_d.get("telegram_channel_handle", "@yumsunov_realbiz")
        _tg_display = _brand_d.get(
            "telegram_channel_display", "Юмсунов | Про реальный бизнес"
        )
        # CTA для соцсетей (Instagram/TikTok): @-хэндл Telegram там НЕ
        # кликабелен — направляем в шапку профиля, иначе зритель не перейдёт
        # (Артём 6 июня: канал в Telegram, ссылка в bio).
        _cta_default = (
            f"Подпишись на мой Telegram-канал «{_tg_display}» — "
            f"ссылка в шапке профиля 👇"
        )
        _cta_ex_script = (
            f"Подпишись на канал {_tg_handle}, там разборы из практики"
        )
        _cta_ex_descr = (
            f"Подпишись на мой Telegram-канал «{_tg_display}» — "
            f"ссылка в шапке профиля 👇"
        )
    else:
        _author = "Артёма Панфёрова"
        _author_bio = (
            "Артём Панфёров, 15 лет в бизнесе, сооснователь AI-студии. "
            "Строит личный бренд с нуля с помощью ИИ."
        )
        _tg_handle = "@panferovai"
        _cta_default = (
            f"Подпишись на Телеграм {_tg_handle} — там ещё больше про AI 👇"
        )
        _cta_ex_script = (
            f"Подпишись на мой Телеграм {_tg_handle}, там ещё больше"
        )
        _cta_ex_descr = (
            f"Подписывайся на мой Телеграм {_tg_handle} — "
            f"там ещё больше разборов 👇"
        )

    desc_prompt = (
        f"Ты — SMM-специалист. Пишешь описание для публикации короткого "
        f"видео (Reels/Shorts/TikTok) {_author}.\n\n"

        "═══ ШАГ 1 — НАЙДИ CTA В СЦЕНАРИИ ═══\n"
        "Прочитай сценарий внимательно и найди, к какому действию автор призывает "
        "зрителя В КОНЦЕ ролика. Это и есть CTA.\n\n"
        "Примеры того, как CTA звучит в сценарии и как его переписать для описания:\n"
        f"• Сценарий: «{_cta_ex_script}»\n"
        f"  → CTA для описания: «{_cta_ex_descr}»\n"
        "• Сценарий: «Подписывайся на канал, чтобы не пропустить»\n"
        "  → CTA для описания: «Подпишись на канал, чтобы не пропустить новые разборы 👇»\n"
        "• Сценарий: «Напиши в комментах слово магия, расскажу как»\n"
        "  → CTA для описания: «Напиши слово «магия» в комментах — расскажу, как повторить 👇»\n"
        "• Сценарий: «Ставь лайк, если полезно»\n"
        "  → CTA для описания: «Лайк, если полезно 👇»\n\n"
        "КРИТИЧЕСКИ ВАЖНО:\n"
        "— CTA в описании должен по СМЫСЛУ соответствовать CTA в сценарии. "
        "Если в сценарии автор зовёт в Телеграм — CTA про Телеграм. "
        "Если зовёт писать коммент — CTA про коммент. НЕ придумывай свой CTA.\n"
        "— Если CTA ведёт в Telegram-канал — пиши «ссылка в шапке профиля» "
        "(описание идёт в Instagram/TikTok, где @-хэндл Telegram НЕ кликабелен — "
        "прямого перехода нет). НЕ давай голый @-хэндл Telegram как способ "
        "перехода; формулируй «подпишись на мой Telegram-канал — ссылка в шапке "
        "профиля».\n"
        "— CTA одинаковый во ВСЕХ 3 вариантах описания. Различаются только хуки.\n"
        f"— Если в сценарии ВООБЩЕ нет явного CTA — по умолчанию «{_cta_default}».\n\n"

        "═══ ШАГ 2 — НАПИШИ 3 ВАРИАНТА ОПИСАНИЯ ═══\n"
        "Структура каждого варианта:\n"
        "1) Хук — 1-2 строки. Цепляющая фраза, ДРУГОЙ угол на тему ролика "
        "(не пересказ сценария).\n"
        "2) Контекст — 1-2 строки. Одним предложением раскрой, что конкретно "
        "в ролике / чем он полезен зрителю.\n"
        "3) Пустая строка.\n"
        "4) CTA — ОДНА строка, из ШАГА 1. Один раз, в конце.\n\n"
        "ДЛИНА: 250–400 символов, 3–5 строк. Не короче 200, не длиннее 450.\n"
        "Варианты должны быть РАЗНЫМИ по углу хука (провокация / польза / "
        "инсайт / эмоция — выбери 3 разных угла).\n\n"

        "═══ ЗАПРЕЩЕНО ═══\n"
        "— Хештеги (алгоритм их не учитывает)\n"
        "— Копировать или пересказывать фразы из сценария дословно. Описание — "
        "ДРУГОЙ текст, другой угол. Считай, что ты не читал сценарий, а только "
        "знаешь тему и CTA.\n"
        "— Выдумывать факты об авторе (цифры, даты, достижения)\n"
        "— Клише: «Многие спрашивают», «Сегодня многие», «Честно говоря», «Друзья»\n"
        "— Слово «бесплатно» / «ноль бюджета» (у автора есть расходы на подписки)\n"
        "— Смайлы и эмодзи (максимум одна стрелка 👇 перед CTA)\n"
        "— Придумывать свой CTA, если в сценарии уже есть — бери из сценария\n\n"

        "═══ ОБ АВТОРЕ (используй только если уместно) ═══\n"
        f"{_author_bio}\n\n"

        "═══ ФОРМАТ ОТВЕТА ═══\n"
        "Сначала одна строка: `CTA: <перефразированный CTA из сценария>`\n"
        "Затем пустая строка и разделитель `---`\n"
        "Затем 3 варианта описания, каждый отделён строкой `---`\n"
        "Внутри одного варианта НЕ используй `---` — это разделитель вариантов.\n"
        "Каждый вариант должен заканчиваться той же CTA-строкой, что ты выдал в начале.\n"
    )

    response = claude.messages.create(
        model="claude-opus-4-7",
        max_tokens=2048,
        system=desc_prompt,
        messages=[
            {"role": "user", "content": f"Сценарий ролика:\n\n{script_text}"},
        ],
    )
    raw = response.content[0].text.strip()

    # Parse: first block is CTA line, then variants separated by ---
    blocks = [b.strip() for b in re.split(r'\n\s*-{3,}\s*\n', raw) if b.strip()]
    extracted_cta = ""
    variants: list[str] = []
    for b in blocks:
        # CTA marker block — log it and skip (first block only, usually)
        m = re.match(r'^CTA\s*:\s*(.+)$', b, flags=re.IGNORECASE | re.DOTALL)
        if m and not extracted_cta and len(b) < 200:
            extracted_cta = m.group(1).strip()
            continue
        variants.append(b)

    # Strip any "Вариант N:" prefix + any stray "CTA:" header inside
    cleaned = []
    for v in variants:
        v = re.sub(
            r'^(?:Вариант\s*\d+[:\.]?\s*|[\d]+[.\)]\s*)',
            '', v, flags=re.IGNORECASE,
        ).strip()
        if v:
            cleaned.append(v)
    variants = cleaned[:3] if cleaned else [raw]

    logger.info(
        "[description] extracted_cta=%r variants=%d lengths=%s",
        extracted_cta[:120],
        len(variants),
        [len(v) for v in variants],
    )
    return variants, extracted_cta


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button presses."""
    query = update.callback_query
    await query.answer()
    logger.info(f"[user:{query.from_user.id}] Кнопка: {query.data}")

    user_id = query.from_user.id
    data = pending.get(user_id)
    effective_action = query.data  # may be remapped by card_* handlers below

    # Selfie v2 callbacks — handled by the dedicated module. Registered first
    # so the prefixes (selfie_text:* / selfie_broll:* / selfie_music:* /
    # selfie_cover:*) hit before any generic action below.
    if query.data:
        if query.data.startswith("selfie_text:"):
            await selfie_handlers.handle_text_review_callback(update, context)
            return
        if query.data.startswith("selfie_montage:"):
            await selfie_handlers.handle_montage_callback(update, context)
            return
        if query.data.startswith("selfie_broll:"):
            await selfie_handlers.handle_broll_callback(update, context)
            return
        if query.data.startswith("selfie_music:"):
            await selfie_handlers.handle_music_callback(update, context)
            return
        if query.data.startswith("selfie_cover:"):
            await selfie_handlers.handle_cover_callback(update, context)
            return

    # ── Селфи: текст на обложку (С текстом/Без) ──────────────────────────────
    # MERGED 10 июня: убран отдельный (худший) генератор текста обложки. Теперь
    # «С текстом/Без» только ставит флаг, а ЕДИНЫЙ источник текста — хук-
    # генератор (_maksim_selfie_title_picker, 5 вариантов + Ещё/Свой). Выбранный
    # хук → ложится на фото (если С текстом) И становится заголовком карточки.
    # Один текст, одно качество (хуки были заметно лучше — Артём 10 июня).
    if query.data in ("selfie_ct:on", "selfie_ct:off"):
        await query.answer()
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        if data:
            data["selfie_cover_wants_text"] = (query.data == "selfie_ct:on")
            data["state"] = "selfie_waiting_title"
            _save_pending(pending)
        transcript = (data or {}).get("selfie_transcript", "")
        first = (data or {}).get("selfie_auto_title", "")
        await _maksim_selfie_title_picker(query.message, context, user_id, transcript, first)
        return

    # Restore brand context for deep callbacks (heygen_looks, assemble, cover).
    # Source: pending[user_id]["card_brand"] — cached when the card was
    # pre-saved in `approve` or loaded via _pick_card_apply_brand. Survives
    # bot restarts since pending.json is on disk; global _active_brand does not.
    _restore_brand_from_pending(user_id)

    # Also: if this callback is a card-level action (pattern includes a card
    # id prefix), refresh the cached brand from Notion. Cheap when `all_cards`
    # is already being loaded elsewhere in the handler — but for the "deep"
    # heygen_looks / card_assemble entry points we need it explicitly.
    # (Handled per-branch below — keep this comment as the contract reminder.)

    # --- Main action buttons from /start greeting ---
    # Inline buttons in the greeting route here and re-dispatch to the
    # real command handlers. Keeping a single source of truth per command —
    # all the flow logic lives in the existing CommandHandler functions.
    if query.data.startswith("cmd_"):
        action = query.data[4:]
        try:
            await query.answer()
        except Exception:
            pass
        # Fabricate a pseudo-update where update.message points to the
        # message that held the button — existing command handlers expect
        # `update.message`, not `update.callback_query.message`.
        class _FakeUpdate:
            def __init__(self, msg, user):
                self.message = msg
                self.effective_user = user
                self.effective_chat = msg.chat
                self.callback_query = None
        pseudo = _FakeUpdate(query.message, query.from_user)

        if action == "new_idea":
            await query.message.reply_text(
                f"💡 Надиктуй или напиши идею ролика.\n\n"
                f"🏷 Текущий бренд: *{_get_active_brand_name()}* "
                f"(смени через 🏷 Сменить бренд если нужно).",
                parse_mode="Markdown",
            )
            return
        if action == "selfie":
            await selfie_command(pseudo, context)
            return
        if action == "cards":
            await cards_command(pseudo, context)
            return
        if action == "tgpost":
            from tg_post_handlers import tgpost_command
            await tgpost_command(pseudo, context)
            return
        if action == "brand":
            await brand_command(pseudo, context)
            return
        if action == "help":
            await help_command(pseudo, context)
            return
        if action == "ideas":
            await ideas_command(pseudo, context)
            return
        if action == "calendar":
            await calendar_command(pseudo, context)
            return
        if action == "image":
            from fal_handlers import image_command
            await image_command(pseudo, context)
            return
        if action == "video":
            from fal_handlers import video_command
            await video_command(pseudo, context)
            return
        if action == "launches":
            await launches_command(pseudo, context)
            return
        if action == "carousel":
            # 14 May 2026 — entrypoint Pipeline #6 (Carousel for Instagram).
            # Step 1 of 2: ask for template choice. After user picks M1/M2,
            # `carousel_tpl:<X>` callback sets state and prompts for theme.
            # 26 May 2026: чистим залипший surg-edit state — если юзер ушёл
            # из прошлой карусели через cmd_carousel без approve/cancel,
            # state «awaiting_carousel_surg_edit» залипал и следующий текст
            # юзера интерпретировался как правка пропавшего draft.
            _clear_carousel_surg_state(query.from_user.id)
            await query.message.reply_text(
                "🎨 <b>Карусель для Instagram</b>\n\n"
                "Сначала — выбери стиль обложки:",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "M2 · Pit-Stop (universал, гайды/советы)",
                            callback_data="carousel_tpl:M2",
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "M1 · Anniversary (анонсы, события, юбилеи)",
                            callback_data="carousel_tpl:M1",
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "❌ Отмена",
                            callback_data="carousel_cancel",
                        ),
                    ],
                ]),
            )
            return
        # Unknown cmd_ value — show a warning but don't crash
        await query.message.reply_text(f"⚠️ Неизвестное действие: {action}")
        return

    # ═══════════════════════════════════════════════════════════════════
    #  Maksim Pipeline #0-B (Generator идей в стиле бренда)
    # ═══════════════════════════════════════════════════════════════════
    # Callbacks:
    #   maksim_ideas_stub   — root entrypoint (legacy name kept for compat
    #                         with _start_action_kb_maksim button binding).
    #                         Generates batch of 10 ideas, renders as a list.
    #   idea_save:<idx>     — save idea #idx as a Notion "Идеи | старт" card.
    #   idea_more           — generate next batch, exclude session history.
    #
    # State in pending[user_id]:
    #   ideas_batch        — list[dict] current visible batch (idea objects)
    #   ideas_session      — list[str]  ALL titles shown so far this session
    #                                   (used as exclude on "🎲 Ещё идей")
    #   ideas_saved_idx    — set[int]   indices already saved (UI cosmetic)
    # ═══════════════════════════════════════════════════════════════════

    if query.data == "maksim_ideas_stub" or query.data == "idea_more":
        # Gate by brand — for non-maksim brands we don't have a prompt
        active_brand = _get_active_brand_name()
        if active_brand != "maksim":
            await query.message.reply_text(
                f"🔍 «Идеи дня» пока работают только для бренда maksim. "
                f"Активный бренд: {active_brand}."
            )
            return

        try:
            import idea_generator
        except Exception as e:
            logger.error(f"[ideas] import failed: {e}")
            await query.message.reply_text(
                f"❌ Модуль idea_generator не загружен: {e}"
            )
            return

        # Status message — same chat, lets us replace with results later
        status_msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="🎰 Открываю банк идей… ~10-15 сек\n"
                 "(Пропускаю темы, которые уже есть в Notion-карточках)",
        )

        # Build exclude list: Notion history (cross-session) +
        # session-shown titles (intra-session, for "🎲 Ещё идей")
        try:
            existing_cards = await asyncio.to_thread(
                fetch_notion_cards, None, 200,
            )
            notion_titles = [
                c.get("title", "") for c in existing_cards if c.get("title")
            ]
        except Exception as e:
            logger.warning(f"[ideas] fetch_notion_cards failed: {e}")
            notion_titles = []

        session_data = pending.get(user_id) or {}
        session_titles: list[str] = list(session_data.get("ideas_session", []) or [])
        # De-dup, session first (recent), then notion (older)
        seen = set()
        exclude_titles: list[str] = []
        for t in session_titles + notion_titles:
            tn = t.strip()
            if tn and tn not in seen:
                seen.add(tn)
                exclude_titles.append(tn)

        # Generate. 13 May 2026 — reduced batch 10 → 5 per Артём:
        # «давай ограничимся 5, чтобы партия была обозримой».
        # Less cognitive load, faster generation (~15 сек вместо 25-30),
        # дешевле по токенам. Можно вернуть к 10 заменой числа здесь.
        try:
            ideas = await asyncio.to_thread(
                idea_generator.generate_ideas,
                claude,
                "maksim",
                exclude_titles,
                5,
            )
        except Exception as e:
            logger.error(f"[ideas] generation failed: {e}", exc_info=True)
            try:
                await status_msg.edit_text(
                    f"❌ Не получилось сгенерировать идеи: {e}\n\n"
                    f"Попробуй ещё раз через минуту."
                )
            except Exception:
                pass
            return

        if not ideas:
            try:
                await status_msg.edit_text(
                    "⚠️ Сгенерировалось 0 идей (все были близки к уже существующим).\n"
                    "Попробуй ещё раз — Claude иногда выдаёт повторы партиями."
                )
            except Exception:
                pass
            return

        # Save batch + session history to pending
        session_data = session_data or {}
        session_data["ideas_batch"] = ideas
        session_data["ideas_saved_idx"] = []
        new_session = list(session_data.get("ideas_session", []) or [])
        # 13 May 2026 — was: only title in session. Now: title + first
        # 80 chars of thesis. Lets _filter_duplicates catch «same angle,
        # different wording» on the next «🎲 Ещё идей» tap (e.g.
        # "Стратегия не делается днём" vs "Утром решаю стратегию").
        for i in ideas:
            t = (i.get("title") or "").strip()
            th = (i.get("central_thesis") or "").strip()[:80]
            if t:
                new_session.append(f"{t} {th}".strip())
        session_data["ideas_session"] = new_session
        pending[user_id] = session_data
        _save_pending(pending)

        # Render — text + per-idea save buttons + nav
        msg_text = idea_generator.format_ideas_message(ideas)

        buttons: list[list[InlineKeyboardButton]] = []
        for i, idea in enumerate(ideas):
            short_title = (idea.get("title", "") or "?")[:38]
            buttons.append([InlineKeyboardButton(
                # 24 May 2026 — was "✅ #N: …". Maksim читал зелёную
                # галочку как «уже сохранено» и думал, что весь батч
                # автоматом улетает в Notion. Меняем на «💾 Взять #N»:
                # явный жест «забрать ИМЕННО эту». ✅ остаётся ТОЛЬКО
                # для уже сохранённых (см. idea_save rebuild ниже).
                f"💾 Взять #{i + 1}: {short_title}",
                callback_data=f"idea_save:{i}",
            )])
        buttons.append([InlineKeyboardButton(
            "🎲 Ещё 5 идей (без повторов)", callback_data="idea_more",
        )])

        try:
            await status_msg.edit_text(
                msg_text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(buttons),
                disable_web_page_preview=True,
            )
        except Exception as e:
            # Fallback: status_msg might be too short to expand; send new
            logger.warning(f"[ideas] edit_text failed, sending new: {e}")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=msg_text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(buttons),
                disable_web_page_preview=True,
            )
        return

    if query.data.startswith("idea_save:"):
        try:
            idx = int(query.data.split(":", 1)[1])
        except (IndexError, ValueError):
            await query.answer("⚠️ Некорректный индекс")
            return

        session_data = pending.get(user_id) or {}
        batch = session_data.get("ideas_batch") or []
        if idx < 0 or idx >= len(batch):
            await _ideas_batch_stale(query)
            return

        saved_idx = set(session_data.get("ideas_saved_idx") or [])
        if idx in saved_idx:
            await query.answer("✅ Уже сохранена")
            return

        idea = batch[idx]
        title = (idea.get("title") or "").strip() or "Без названия"
        hook = (idea.get("hook_draft") or "").strip()
        thesis = (idea.get("central_thesis") or "").strip()
        why = (idea.get("why_works") or "").strip()
        cta = (idea.get("cta_hint") or "").strip()
        niche = idea.get("niche") or ""
        fmt_type = idea.get("format_type") or ""
        fmt_subtype = idea.get("format_subtype") or ""
        audience = idea.get("audience") or ""

        # Map niche/format → Notion "Рубрика"
        rubric = "Свободный формат"
        if fmt_type == "tg_post":
            rubric = "TG-пост"
        elif fmt_type == "video_script" and fmt_subtype in ("hook_tour", "hook_teaser"):
            rubric = "Виральный ролик"
        elif niche == "glamping":
            rubric = "Средний (глэмпинг)"

        # Notion "Формат" multi_select
        notion_format = ["Short video"] if fmt_type == "video_script" else ["Long post"]

        # Body — structured notes that Maksim/SMM can read & build from
        body_lines: list[str] = []
        if hook:
            body_lines.append(f"💡 HOOK: {hook}")
        if thesis:
            body_lines.append(f"📌 ТЕЗИС: {thesis}")
        if why:
            body_lines.append(f"✨ ПОЧЕМУ РАБОТАЕТ: {why}")
        meta_bits = []
        if niche:
            meta_bits.append(f"ниша: {niche}")
        if fmt_subtype:
            meta_bits.append(f"подтип: {fmt_subtype}")
        if audience:
            meta_bits.append(f"аудитория: {audience}")
        if meta_bits:
            body_lines.append(f"🏷 МЕТА: {' · '.join(meta_bits)}")
        body = "\n\n".join(body_lines) or title

        card_data = {
            "title": title,
            "cta": cta,
            "rubric": rubric,
            "platforms": _default_platforms("maksim"),
            "format": notion_format,
            "brand": "maksim",
        }

        try:
            notion_url, _notion_id = await asyncio.to_thread(
                create_notion_card, card_data, body,
            )
        except Exception as e:
            logger.error(f"[ideas] create_notion_card failed: {e}", exc_info=True)
            await query.answer(f"❌ Notion ошибка: {e}", show_alert=True)
            return

        # Mark idea as saved + update keyboard button
        saved_idx.add(idx)
        session_data["ideas_saved_idx"] = list(saved_idx)
        pending[user_id] = session_data
        _save_pending(pending)

        # Rebuild keyboard with this button toggled to "✅ Сохранено"
        try:
            new_buttons: list[list[InlineKeyboardButton]] = []
            for i, it in enumerate(batch):
                if i in saved_idx:
                    new_buttons.append([InlineKeyboardButton(
                        f"✅ #{i + 1} в Notion",
                        callback_data=f"idea_view:{i}",  # no-op visual
                    )])
                else:
                    short_title = (it.get("title", "") or "?")[:38]
                    new_buttons.append([InlineKeyboardButton(
                        f"💾 Взять #{i + 1}: {short_title}",
                        callback_data=f"idea_save:{i}",
                    )])
            new_buttons.append([InlineKeyboardButton(
                "🎲 Ещё 5 идей (без повторов)", callback_data="idea_more",
            )])
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup(new_buttons),
            )
        except Exception as e:
            logger.debug(f"[ideas] keyboard refresh after save: {e}")

        # Cache the Notion URL per-idx so the «📋 Открыть в Notion»
        # button in the "Что дальше?" menu has a target. URLs live in
        # pending and survive bot restarts.
        urls_map = session_data.get("ideas_notion_urls") or {}
        urls_map[str(idx)] = notion_url
        session_data["ideas_notion_urls"] = urls_map
        pending[user_id] = session_data
        _save_pending(pending)

        await query.answer(f"✅ Идея #{idx + 1} сохранена в Notion")

        # «Что дальше?» menu — closes the «idea → tupik → /start» gap.
        # Each pipeline-button passes the idx so the downstream handler
        # can pull the idea from pending[uid]["ideas_batch"][idx] without
        # extra state. Notion-URL kept on the cosmetic «📋 Открыть»
        # button as a URL InlineKeyboardButton (no callback round-trip).
        thesis = (idea.get("central_thesis") or "").strip()
        thesis_preview = thesis if len(thesis) <= 350 else thesis[:347] + "…"
        # parse_mode="HTML" requires escaping any < > & in LLM output —
        # otherwise an idea with «AI < ML» or «A & B» breaks the parser.
        title_esc = html_mod.escape(title)
        thesis_esc = html_mod.escape(thesis_preview)

        menu_text = (
            f"✅ <b>Идея в Notion:</b> {title_esc}\n\n"
            f"<i>Тезис:</i>\n{thesis_esc}\n\n"
            f"<b>Что делаем дальше?</b>"
        )
        next_kb = [
            [InlineKeyboardButton(
                "📝 Написать TG-пост сразу",
                callback_data=f"idea_pipeline:tgpost:{idx}",
            )],
            [InlineKeyboardButton(
                "🎥 Записать Селфи под идею",
                callback_data=f"idea_pipeline:selfie:{idx}",
            )],
            [InlineKeyboardButton(
                "🤖 AI-аватар: сценарий → видео",
                callback_data=f"idea_pipeline:avatar:{idx}",
            )],
            [InlineKeyboardButton(
                "🎨 Карусель для Instagram",
                callback_data=f"idea_pipeline:carousel:{idx}",
            )],
            [InlineKeyboardButton(
                "🎞 B-roll монтаж (в разработке)",
                callback_data=f"idea_pipeline:broll:{idx}",
            )],
            [InlineKeyboardButton(
                "📋 Открыть карточку в Notion", url=notion_url,
            )],
            [InlineKeyboardButton(
                "◀️ В главное меню", callback_data="idea_back_to_menu",
            )],
        ]
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=menu_text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(next_kb),
        )
        return

    if query.data.startswith("idea_view:"):
        # «Уже сохранена» button — re-open the «Что дальше?» menu for
        # this idx (user might want to launch a pipeline retroactively).
        try:
            idx = int(query.data.split(":", 1)[1])
        except (IndexError, ValueError):
            await query.answer("⚠️ Некорректный индекс")
            return
        session_data = pending.get(user_id) or {}
        batch = session_data.get("ideas_batch") or []
        if idx < 0 or idx >= len(batch):
            await _ideas_batch_stale(query)
            return
        idea = batch[idx]
        title = (idea.get("title") or "").strip() or "Без названия"
        thesis = (idea.get("central_thesis") or "").strip()
        thesis_preview = thesis if len(thesis) <= 350 else thesis[:347] + "…"
        urls_map = session_data.get("ideas_notion_urls") or {}
        notion_url = urls_map.get(str(idx), "")
        title_esc = html_mod.escape(title)
        thesis_esc = html_mod.escape(thesis_preview)

        menu_text = (
            f"✅ <b>Идея в Notion:</b> {title_esc}\n\n"
            f"<i>Тезис:</i>\n{thesis_esc}\n\n"
            f"<b>Что делаем дальше?</b>"
        )
        next_kb = [
            [InlineKeyboardButton("📝 Написать TG-пост сразу", callback_data=f"idea_pipeline:tgpost:{idx}")],
            [InlineKeyboardButton("🎥 Записать Селфи под идею", callback_data=f"idea_pipeline:selfie:{idx}")],
            [InlineKeyboardButton("🤖 AI-аватар: сценарий → видео", callback_data=f"idea_pipeline:avatar:{idx}")],
            [InlineKeyboardButton("🎨 Карусель для Instagram", callback_data=f"idea_pipeline:carousel:{idx}")],
            [InlineKeyboardButton("🎞 B-roll монтаж — ролик без аватара", callback_data=f"idea_pipeline:broll:{idx}")],
        ]
        if notion_url:
            next_kb.append([InlineKeyboardButton("📋 Открыть карточку в Notion", url=notion_url)])
        next_kb.append([InlineKeyboardButton("◀️ В главное меню", callback_data="idea_back_to_menu")])

        await query.answer()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=menu_text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(next_kb),
        )
        return

    # ═══════════════════════════════════════════════════════════════════
    #  Idea pipeline launchers — «Что дальше?» menu actions
    # ═══════════════════════════════════════════════════════════════════
    # Format: idea_pipeline:<pipeline>:<idx>
    #   tgpost  → autorun tg_post_writer on the idea's thesis
    #   selfie  → instruction: show thesis copy-ready, suggest /selfie
    #   avatar  → instruction for AI-avatar pipeline (cmd_new_idea seed)
    #   broll   → B-roll монтаж (Pipeline #2) — broll/handlers.py preview
    if query.data.startswith("idea_pipeline:"):
        parts = query.data.split(":")
        if len(parts) < 3:
            await query.answer("⚠️ Некорректный callback")
            return
        pipeline = parts[1]
        try:
            idx = int(parts[2])
        except ValueError:
            await query.answer("⚠️ Некорректный индекс")
            return

        session_data = pending.get(user_id) or {}
        batch = session_data.get("ideas_batch") or []
        if idx < 0 or idx >= len(batch):
            await _ideas_batch_stale(query)
            return
        idea = batch[idx]
        title = (idea.get("title") or "").strip() or "Без названия"
        thesis = (idea.get("central_thesis") or "").strip()
        hook = (idea.get("hook_draft") or "").strip()
        urls_map = session_data.get("ideas_notion_urls") or {}
        notion_url = urls_map.get(str(idx), "")

        # ── 📝 TG-post: autorun tg_post_writer on the thesis ─────────
        if pipeline == "tgpost":
            if not thesis:
                await query.answer("⚠️ У идеи пустой тезис, нечего раскрывать", show_alert=True)
                return
            # tg_post_writer импортирован на уровне модуля (стр. 168). Локальный
            # `import` здесь делал имя локальным для ВСЕЙ handle_callback →
            # UnboundLocalError в ветке tgpost_from_script выше (Артём 9 июня).
            status_msg = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"📝 Пишу TG-пост по идее «{title}»… ~30-40 сек\n"
                     "(Opus черновик + Sonnet polish финала)",
            )
            try:
                # Pass thesis as `facts` AND inject hook_draft via extra_notes
                # so Opus opens the post with the EXACT hook that idea_generator
                # crafted (with open-loop / self-containedness rules), not
                # a generic header re-derived from thesis.
                #
                # 13 May 2026 fix — Артём caught: «хук в превью банка идей лучше,
                # чем хук в готовом посте». Cause: hook_draft was extracted but
                # never passed downstream — Opus ignored it. Now: extra_notes
                # tells Opus to USE the hook (light adaptation OK, semantic
                # preservation MANDATORY).
                extra_notes_block = ""
                if hook:
                    extra_notes_block = (
                        f"ОБЯЗАТЕЛЬНО используй этот хук в заголовке поста "
                        f"(можно адаптировать формулировку, но СОХРАНИ силу "
                        f"и смысл; не заменяй на свой generic-вариант): "
                        f"«{hook}»"
                    )
                post_text = await asyncio.to_thread(
                    tg_post_writer.generate_post,
                    tg_post_writer.PostInput(
                        post_type="review_essay",
                        facts=thesis,
                        extra_notes=extra_notes_block,
                    ),
                    claude,
                    brand="maksim",
                )
            except Exception as e:
                logger.error(f"[ideas→tgpost] failed: {e}", exc_info=True)
                try:
                    await status_msg.edit_text(f"❌ Ошибка генерации поста: {e}")
                except Exception:
                    pass
                return

            # Hand off to Артёмов `tgpost_review` flow (publish / regen /
            # voice_edit / notion / cancel) BUT use a local extended
            # keyboard with my additional «✏️ Точечная правка» (Sonnet
            # surgical) and a local renderer with ** → <b> conversion
            # (Артёмов `_safe_preview` keeps ** literal by design).
            #
            # 13 May 2026 — Артём explicitly asked for surgical edit as
            # complement (Variant A) after seeing regenerate flow rewrite
            # the entire post on a «поменяй хук» request.
            session_data = pending.get(user_id) or {}
            tg_block = session_data.setdefault("tgpost", {})
            tg_block["post_type"] = "review_essay"
            tg_block["facts"] = thesis
            tg_block["last_post"] = post_text
            tg_block["extra_notes"] = ""
            session_data["state"] = "tgpost_review"
            session_data["tgpost_surg_iterations"] = 0
            session_data["tgpost_surg_idea_idx"] = idx
            pending[user_id] = session_data
            _save_pending(pending)

            post_html = _render_tgpost_html(post_text)
            photos_count = len(session_data.get("selfie_tg_photos") or [])
            try:
                await status_msg.edit_text(
                    f"📝 <b>Готовый пост:</b>\n\n{post_html}",
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=_idea_tgpost_keyboard(idx, photos_count),
                )
            except Exception as e:
                logger.debug(f"[idea→tgpost] HTML render fallback: {e}")
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=post_text,
                    reply_markup=_idea_tgpost_keyboard(idx, photos_count),
                )
            return

        # ── 🎥 Селфи: инструкция + копируемый тезис ──────────────────
        if pipeline == "selfie":
            title_esc = html_mod.escape(title)
            thesis_esc = html_mod.escape(thesis)
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=(
                    f"🎥 <b>Селфи под идею «{title_esc}»</b>\n\n"
                    f"1. Скопируй тезис ниже\n"
                    f"2. Жми /selfie или «🎥 Селфи» в главном меню\n"
                    f"3. Запиши видео 30-60 сек, раскрывая тезис своими словами\n"
                    f"4. Бот добавит субтитры, сгенерит 5 вирусных хуков на выбор и напишет TG-пост\n\n"
                    f"<b>Тезис для записи:</b>\n<code>{thesis_esc}</code>"
                ),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎥 Открыть Селфи", callback_data="cmd_selfie")],
                    [InlineKeyboardButton("◀️ В главное меню", callback_data="idea_back_to_menu")],
                ]),
            )
            await query.answer()
            return

        # ── 🤖 AI-аватар: 2 кнопки автозапуска (Pipeline #3) ───────
        # Раньше эта ветка показывала копипаст-инструкцию «Скопируй тезис →
        # жми Моя идея → вставь», что Артём прямо назвал «неправильным
        # pipeline» (31 мая 2026). Теперь предлагаем 2 варианта автозапуска:
        #   • idea_avatar_full   — полный prod: сценарий → одобрение
        #                          → (Фаза 2) озвучка v3 → HeyGen аватар
        #   • idea_avatar_script — только до сценария, дальше юзер сам
        # Оба варианта берут тезис+хук из banked idea и СРАЗУ зовут
        # `_generate_script` — без копипасты.
        if pipeline == "avatar":
            title_esc = html_mod.escape(title)
            thesis_esc = html_mod.escape(
                thesis if len(thesis) <= 400 else thesis[:397] + "…"
            )
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=(
                    f"🤖 <b>AI-аватар под идею «{title_esc}»</b>\n\n"
                    f"<b>Тезис:</b>\n{thesis_esc}\n\n"
                    f"Как запустить?"
                ),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🎬 Собрать ролик с аватаром",
                        callback_data=f"idea_avatar_full:{idx}",
                    )],
                    [InlineKeyboardButton(
                        "📝 Сначала только сценарий",
                        callback_data=f"idea_avatar_script:{idx}",
                    )],
                    [InlineKeyboardButton("◀️ В главное меню", callback_data="idea_back_to_menu")],
                ]),
            )
            await query.answer()
            return

        # ── 🎨 Карусель Instagram (Pipeline #6) ──
        # LLM (Sonnet) генерит 5-слайдов JSON в формате carousel.renderer
        # schema → Playwright рендерит 5 PNG @1080x1350 → media_group
        # в чат. Instagram OAuth НЕ подключён — фото показываются только
        # в Telegram, Артём/SMM скачивают вручную и постят.
        if pipeline == "carousel":
            try:
                from carousel.handlers import render_and_send_carousel
            except Exception as e:
                logger.error(f"[carousel] import failed: {e}")
                await query.answer(f"❌ Модуль carousel не загружен: {e}", show_alert=True)
                return
            theme = thesis or title or "контент Life Drive"
            await query.answer("🎨 Стартую генерацию карусели…")
            await render_and_send_carousel(
                update, context, claude,
                theme=theme,
                n_slides=None,    # инфер из темы: «топ-K» → K+2, иначе 7
                chat_id=query.message.chat_id,
                notion_url=notion_url or None,
            )
            return

        # ── 🎞 B-roll монтаж (Pipeline #2) — ролик без аватара ──────
        # Закадровый сценарий → озвучка голосом Максима → видеоряд из
        # тегированного архива клипов → субтитры. 2-фазный flow в
        # broll/handlers.py (по образцу карусели).
        if pipeline == "broll":
            try:
                from broll.handlers import generate_broll_preview
            except Exception as e:
                logger.error(f"[broll] import failed: {e}", exc_info=True)
                await query.answer(f"❌ Модуль broll не загружен: {e}", show_alert=True)
                return
            theme = thesis or title or "контент Life Drive"
            await query.answer("🎞 Стартую B-roll ролик…")
            await generate_broll_preview(
                update, context, claude,
                theme=theme,
                chat_id=query.message.chat_id,
                notion_url=notion_url or None,
            )
            return

        await query.answer(f"⚠️ Неизвестный pipeline: {pipeline}")
        return

    # ═══════════════════════════════════════════════════════════════════
    #  Idea → AI-avatar autorun (Pipeline #3, banked-idea entry point)
    # ═══════════════════════════════════════════════════════════════════
    # Два варианта запуска из меню «Что дальше?» → AI-аватар:
    #   idea_avatar_full:<idx>   — сценарий + флаг auto_after_approve=
    #                              "avatar_full" (Фаза 2 подхватит флаг и
    #                              после approve запустит цепочку voice→
    #                              HeyGen без участия юзера)
    #   idea_avatar_script:<idx> — только сценарий, дальше юзер сам через
    #                              стандартные кнопки «Утвердить → обложка»
    # Замена копипаст-инструкции (Артём 31 мая: «должна была быть кнопка,
    # чтобы по этому тезису сразу нажимался генерировать»).
    if query.data.startswith("idea_avatar_full:") or query.data.startswith("idea_avatar_script:"):
        is_full = query.data.startswith("idea_avatar_full:")
        try:
            idx = int(query.data.split(":", 1)[1])
        except (IndexError, ValueError):
            await query.answer("⚠️ Некорректный индекс")
            return

        session_data = pending.get(user_id) or {}
        batch = session_data.get("ideas_batch") or []
        if idx < 0 or idx >= len(batch):
            await _ideas_batch_stale(query)
            return

        idea = batch[idx]
        thesis = (idea.get("central_thesis") or "").strip()
        hook = (idea.get("hook_draft") or "").strip()
        if not thesis:
            await query.answer("⚠️ У идеи пустой тезис", show_alert=True)
            return

        # Seed text для _generate_script: ровно так же, как если бы Артём
        # сам надиктовал идею через «✍️ Моя идея» — хук (если есть) + тезис.
        # Хук ставим первым: SCRIPT_PROMPT в Opus умеет открывать сценарий
        # с заданного хука (см. tg_post_writer extra_notes pattern на 11275).
        seed_parts = []
        if hook:
            seed_parts.append(f"Хук: {hook}")
        seed_parts.append(f"Тезис: {thesis}")
        seed_text = "\n\n".join(seed_parts)

        # Pseudo-update — _generate_script зовёт update.message.reply_text
        # для статусов, ему нужен .message и .effective_user. Тот же
        # паттерн, что используется в "cmd_*" роутере выше (10752).
        class _PseudoUpdate:
            def __init__(self, msg, user):
                self.message = msg
                self.effective_user = user
                self.effective_chat = msg.chat
                self.callback_query = None
        pseudo = _PseudoUpdate(query.message, query.from_user)

        await query.answer("✍️ Пишу сценарий…")

        try:
            await _generate_script(pseudo, context, seed_text)
        except Exception as e:
            logger.error(f"[idea_avatar] _generate_script failed: {e}", exc_info=True)
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"❌ Ошибка генерации сценария: {e}",
            )
            return

        # _generate_script полностью перезаписал pending[user_id] (см.
        # 10441). Для full-варианта ставим флаг ПОСЛЕ — Фаза 2 (auto-
        # цепочка озвучка→HeyGen) проверит его в approve handler.
        if is_full:
            cur = pending.get(user_id, {}) or {}
            cur["auto_after_approve"] = "avatar_full"
            # Сохраним idx и notion_url из исходного банка идей, чтобы
            # Фаза 2 могла связать готовый ролик с карточкой банка.
            cur["from_idea_idx"] = idx
            urls_map = session_data.get("ideas_notion_urls") or {}
            if urls_map.get(str(idx)):
                cur["from_idea_notion_url"] = urls_map[str(idx)]
            pending[user_id] = cur
            _save_pending(pending)
            logger.info(
                f"[idea_avatar_full] флаг auto_after_approve='avatar_full' "
                f"выставлен для user={user_id}, idea_idx={idx}"
            )
        return

    # ═══════════════════════════════════════════════════════════════════
    #  TG-post surgical editor (idea-flow only)
    # ═══════════════════════════════════════════════════════════════════
    # Companion to Артёмов `tgpost:voice_edit` (which regenerates the
    # whole post). Sonnet-based surgical edit — keeps everything except
    # the instructed part. State `idea_post_surg_edit` runs in parallel
    # to Артёмов `tgpost_review` (preserved in data["tgpost"]) so user
    # can mix surgical and regenerate without state collision.
    if query.data.startswith("tgpost_surg_edit_start:"):
        try:
            idx = int(query.data.split(":", 1)[1])
        except (IndexError, ValueError):
            await query.answer("⚠️ Некорректный индекс")
            return
        session_data = pending.get(user_id) or {}
        tg = session_data.get("tgpost") or {}
        if not tg.get("last_post"):
            await query.answer("⚠️ Нет текста для точечной правки", show_alert=True)
            return
        session_data["state"] = "idea_post_surg_edit"
        session_data["tgpost_surg_idea_idx"] = idx
        # Don't reset iterations counter — user may chain surgical edits
        pending[user_id] = session_data
        _save_pending(pending)
        iters = session_data.get("tgpost_surg_iterations", 0)
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                f"✏️ <b>Жду точечную правку</b> (#{iters + 1})\n\n"
                f"Напиши <b>текстом</b> или пришли <b>голосовое</b>.\n\n"
                f"<i>Примеры — что меняется хирургически, остальное остаётся:</i>\n"
                f"• <code>убери первый абзац</code>\n"
                f"• <code>поменяй хук на более резкий</code>\n"
                f"• <code>сделай короче на треть</code>\n"
                f"• <code>добавь юмор в финал</code>\n"
                f"• <code>убери цифры про возраст</code>\n\n"
                f"<i>«Отмена» в чате — выход из режима.</i>"
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✖️ Отменить", callback_data="tgpost_surg_edit_cancel")],
            ]),
        )
        await query.answer()
        return

    # ─── Entry to photo menu from idea→tgpost screen ───
    # Sets return_idea_idx so tgphoto_done knows to bring user back to
    # the post screen (not to crosspost menu — that's for selfie flow).
    if query.data.startswith("idea_tgpost_photos:"):
        try:
            idx = int(query.data.split(":", 1)[1])
        except (IndexError, ValueError):
            await query.answer("⚠️ Некорректный индекс")
            return
        session_data = pending.get(user_id) or {}
        session_data["tgphoto_return_idea_idx"] = idx
        session_data.pop("tgphoto_return_script", None)  # mutually exclusive flows
        pending[user_id] = session_data
        _save_pending(pending)
        await query.answer()
        # Re-use existing _render_tgphoto_menu helper (single source of
        # truth for the photo menu rendering).
        await _render_tgphoto_menu(query, context, session_data)
        return

    # ─── Entry to photo menu from selfie script→tgpost screen ───
    # Same menu (своё фото из чата + банк), но возврат не в idea-flow и не
    # в крос-пост, а на экран «📰 TG-пост по сценарию». Маркер возврата —
    # tgphoto_return_script (обрабатывается в tgphoto_done).
    if query.data == "tgpost_script_photos":
        session_data = pending.get(user_id) or {}
        session_data["tgphoto_return_script"] = True
        session_data.pop("tgphoto_return_idea_idx", None)  # mutually exclusive flows
        pending[user_id] = session_data
        _save_pending(pending)
        await query.answer()
        await _render_tgphoto_menu(query, context, session_data)
        return

    # ─── Publish idea-tgpost to channel (with attached photos) ───
    # Differs from Артёмов `tgpost:publish` which sends text only. Here
    # we use telegram_post_to_channel(photos=...) — supports media_group
    # plus long-text follow-up message (same machinery as crosspost
    # Selfie pipeline).
    if query.data.startswith("idea_tgpost_publish:"):
        session_data = pending.get(user_id) or {}
        tg_block = session_data.get("tgpost") or {}
        post_text = (tg_block.get("last_post") or "").strip()
        if not post_text:
            await query.answer("⚠️ Нет текста поста", show_alert=True)
            return
        tg_photos = session_data.get("selfie_tg_photos") or None

        # Convert markdown **bold** → HTML <b> for Telegram delivery
        post_escaped = html_mod.escape(post_text)
        post_html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", post_escaped)

        await query.answer("Публикую…")
        try:
            from crosspost import telegram_post_to_channel
            result = await telegram_post_to_channel(
                context.bot, post_html, photos=tg_photos,
            )
        except Exception as e:
            logger.error(f"[idea_tgpost_publish] failed: {e}", exc_info=True)
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"❌ Не получилось опубликовать: {e}",
            )
            return

        if not result:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="❌ Публикация не удалась. Проверь TELEGRAM_CHANNEL_ID "
                     "и что бот — админ канала.",
            )
            return

        # Clear photos so they don't leak to the next post
        session_data["selfie_tg_photos"] = []
        session_data["state"] = None
        pending[user_id] = session_data
        _save_pending(pending)

        photos_part = f" + {len(tg_photos)} фото" if tg_photos else ""
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"✅ Опубликовано в канал{photos_part}.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ В главное меню", callback_data="idea_back_to_menu")],
            ]),
        )
        return

    if query.data == "tgpost_surg_edit_cancel":
        session_data = pending.get(user_id) or {}
        if session_data.get("state") == "idea_post_surg_edit":
            session_data["state"] = None
            pending[user_id] = session_data
            _save_pending(pending)
        await query.answer("Точечная правка отменена")
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="↩️ Текущий пост не изменился. Возвращайся к нему выше.",
        )
        return

    # ─── Carousel template-choice callback (between cmd_carousel and theme intake) ───
    # F4/F5 (26 May 2026): callback расширен до `carousel_tpl:<M1|M2>:<session_id>`.
    # session_id привязан к конкретному seed-объекту в pending[uid]["carousel_seed"].
    # Старые кнопки без session_id (formant `carousel_tpl:M2`) — legacy для
    # flow «главное меню → тема» (нет seed, ничего не валидируем).
    if query.data.startswith("carousel_tpl:"):
        parts = query.data.split(":")
        tpl = parts[1].strip().upper() if len(parts) >= 2 else ""
        callback_session_id = parts[2] if len(parts) >= 3 else None
        if tpl not in ("M1", "M2"):
            try:
                await query.answer("Неизвестный шаблон", show_alert=True)
            except Exception:
                pass
            return
        try:
            await query.answer(f"Шаблон {tpl} выбран")
        except Exception:
            pass
        # Strip the picker buttons so user can't re-pick mid-state
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        # Set template choice
        user_id_local = query.from_user.id
        data_local = pending.get(user_id_local) or {}
        data_local["carousel_template"] = tpl

        # ── card → carousel: валидация session_id ──
        # Если callback пришёл с session_id, проверяем что seed в pending
        # имеет ТОТ ЖЕ session_id (не устарел / не подменён). Mismatch =
        # stale callback (юзер кликнул старую кнопку от прошлой карточки).
        seed = data_local.get("carousel_seed")
        seed_valid = (
            callback_session_id is not None
            and seed is not None
            and not _seed_is_stale(seed)
            and seed.get("session_id") == callback_session_id
        )
        if callback_session_id and not seed_valid:
            # Старая кнопка / устаревший seed / mismatch — НЕ создаём карусель.
            pending[user_id_local] = data_local
            _save_pending(pending)
            await query.message.reply_text(
                "⚠️ Эта кнопка устарела (карусель открывалась раньше, или ты переходил "
                "к другой карточке). Открой карточку заново и нажми «🎨 В карусель»."
            )
            return

        if seed_valid:
            # Атомарно потребляем seed — pop ТОЛЬКО после успешной валидации.
            seed_text = seed.get("text")
            seed_url = seed.get("card_url") or ""
            seed_card_id = seed.get("card_id")
            data_local.pop("carousel_seed", None)
            data_local["state"] = None
            pending[user_id_local] = data_local
            _save_pending(pending)
            try:
                from carousel.handlers import generate_carousel_preview
            except Exception as e:
                logger.error(f"[carousel_tpl] import failed: {e}", exc_info=True)
                await query.message.reply_text(f"❌ Модуль карусели не загружен: {e}")
                return
            await generate_carousel_preview(
                update, context, claude,
                theme=seed_text,
                chat_id=query.message.chat_id,
                notion_url=seed_url,
                template=tpl,
                seed_card_id=seed_card_id,
            )
            return
        # Legacy / manual flow — без seed: ждём тему как раньше.
        data_local["state"] = "awaiting_carousel_theme"
        pending[user_id_local] = data_local
        _save_pending(pending)
        await query.message.reply_text(
            f"✅ Шаблон <b>{tpl}</b> выбран.\n\n"
            f"📝 Теперь — <b>опиши тему</b>. Можно текстом, можно голосом.\n\n"
            f"⚡ <b>Важно:</b> чем больше реальных деталей дашь — "
            f"тем меньше бот выдумает. Голос на 1-3 минуты с конкретными "
            f"историями работает лучше всего.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Отмена", callback_data="carousel_cancel")],
            ]),
        )
        return

    # ─── Carousel preview-approval callbacks (Pipeline #6 phase-2) ───
    if query.data == "carousel_approve":
        try:
            await query.answer("🖼 Рендерю PNG…")
        except Exception:
            pass
        try:
            from carousel.handlers import render_carousel_from_draft
        except Exception as e:
            logger.error(f"[carousel] import failed in approve: {e}", exc_info=True)
            await query.message.reply_text(f"❌ Модуль карусели не загружен: {e}")
            return
        # Clear surgical-edit state — approve ends the editing loop
        _clear_carousel_surg_state(query.from_user.id)
        # Strip approval buttons so user can't double-click
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await render_carousel_from_draft(
            update, context, chat_id=query.message.chat_id,
        )
        return

    if query.data == "carousel_regen":
        try:
            await query.answer("🔄 Переписываю сценарий…")
        except Exception:
            pass
        try:
            from carousel.handlers import regenerate_carousel_preview
        except Exception as e:
            logger.error(f"[carousel] import failed in regen: {e}", exc_info=True)
            await query.message.reply_text(f"❌ Модуль карусели не загружен: {e}")
            return
        # Clear any leftover surgical-edit state — full regen overrides it
        _clear_carousel_surg_state(query.from_user.id)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await regenerate_carousel_preview(update, context, claude)
        return

    if query.data == "carousel_surg_edit":
        # Точечная правка: ставим state, ждём инструкцию текстом/голосом.
        # Инструкция обрабатывается в process_idea/process_voice под
        # state == "awaiting_carousel_surg_edit".
        try:
            await query.answer("✏️ Опиши что поправить")
        except Exception:
            pass
        user_id_local = query.from_user.id
        data_local = pending.get(user_id_local) or {}
        data_local["state"] = "awaiting_carousel_surg_edit"
        pending[user_id_local] = data_local
        _save_pending(pending)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await query.message.reply_text(
            "✏️ <b>Точечная правка</b>\n\n"
            "Опиши одной фразой что поменять — текстом или голосом. "
            "Меняется только указанное, остальное остаётся как есть.\n\n"
            "<i>Примеры:</i>\n"
            "• «поменяй заголовок 3-го слайда»\n"
            "• «убери цифру с обложки»\n"
            "• «исправь грамматику в слайде 4»\n"
            "• «сделай CTA мягче»\n\n"
            "Или <code>отмена</code> чтобы выйти.",
            parse_mode="HTML",
        )
        return

    if query.data == "carousel_back_to_preview" or query.data.startswith("carousel_back_to_preview:"):
        # «✏️ Поправить ещё» из финального post-render меню. Возвращаем
        # текстовое preview, draft жив (persistent), юзер может «Точечная
        # правка» → новые PNG.
        # F6 (26 May 2026): поддерживаем `carousel_back_to_preview:<card_id>` —
        # ownership-check внутри handler'а.
        requested_card_id = None
        if ":" in query.data:
            requested_card_id = query.data.split(":", 1)[1]
        try:
            await query.answer("Возвращаюсь к сценарию…")
        except Exception:
            pass
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        try:
            from carousel.handlers import back_to_carousel_preview
        except Exception as e:
            logger.error(f"[carousel] import back_to_preview failed: {e}", exc_info=True)
            return
        await back_to_carousel_preview(update, context, requested_card_id=requested_card_id)
        return

    if query.data == "carousel_status_menu":
        # B4 (26 May 2026): кнопка из post-render финального меню. Показывает
        # submenu со списком PIPELINE_STATUSES (+ Идеи | старт) — юзер сам
        # решает куда передвинуть карточку.
        try:
            await query.answer()
        except Exception:
            pass
        try:
            from carousel.handlers import _load_carousel_draft
            draft = _load_carousel_draft(query.from_user.id)
        except Exception:
            draft = None
        seed_card_id = (draft or {}).get("seed_card_id") if draft else None
        if not seed_card_id:
            await query.message.reply_text("❌ Не нашёл карточку — статус сменить не могу.")
            return
        # Список статусов: «Идеи | старт» (откат) + PIPELINE_STATUSES.
        all_statuses = ["Идеи | старт"] + PIPELINE_STATUSES
        status_icons = {
            "Идеи | старт": "🐣",
            "Сценарий | озвучка": "🎙",
            "Подбор скринкаст": "🎥",
            "Аватар | генерации": "🤖",
            "Монтаж": "✂️",
            "Готово к публикации": "✅",
        }
        rows = []
        for st in all_statuses:
            icon = status_icons.get(st, "📝")
            rows.append([InlineKeyboardButton(
                f"{icon} {st}",
                callback_data=f"carousel_set_status:{st}",
            )])
        rows.append([InlineKeyboardButton("❌ Отмена", callback_data="carousel_status_cancel")])
        await query.message.reply_text(
            "📊 <b>В какой статус перевести карточку?</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if query.data == "carousel_status_cancel":
        try:
            await query.answer("Отменено")
        except Exception:
            pass
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    if query.data.startswith("carousel_set_status:"):
        new_status = query.data.split(":", 1)[1]
        try:
            await query.answer(f"Перевожу в «{new_status}»…")
        except Exception:
            pass
        try:
            from carousel.handlers import _load_carousel_draft
            draft = _load_carousel_draft(query.from_user.id)
        except Exception:
            draft = None
        seed_card_id = (draft or {}).get("seed_card_id") if draft else None
        if not seed_card_id:
            await query.message.reply_text("❌ Карточка потеряна, статус не сменил.")
            return
        try:
            await asyncio.to_thread(
                update_notion_status, seed_card_id, new_status, _get_active_brand_name(),
            )
            try:
                await query.edit_message_text(
                    f"✅ Статус карточки → <b>{new_status}</b>",
                    parse_mode="HTML",
                )
            except Exception:
                await query.message.reply_text(f"✅ Статус карточки → {new_status}")
        except Exception as e:
            logger.error(f"[carousel_set_status] update failed: {e}", exc_info=True)
            await query.message.reply_text(f"❌ Не смог сменить статус: {e}")
        return

    if query.data == "carousel_ig_publish":
        # Публикация готовой карусели в Instagram (10 июня). PNG-слайды →
        # JPEG → nginx-media → Graph API carousel. Логика в модуле карусели.
        try:
            await query.answer("📲 Публикую в Instagram…")
        except Exception:
            pass
        try:
            from carousel.handlers import publish_carousel_to_instagram
        except Exception as e:
            logger.error(f"[carousel] import publish_carousel_to_instagram failed: {e}", exc_info=True)
            await query.message.reply_text(f"❌ Модуль карусели не загружен: {e}")
            return
        # Снимаем кнопку чтобы не было двойного клика во время заливки.
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await publish_carousel_to_instagram(
            update, context, chat_id=query.message.chat_id,
        )
        return

    if query.data == "carousel_finalize":
        # «✅ Готово, закрыть» — явное удаление draft.
        try:
            await query.answer("Закрыто")
        except Exception:
            pass
        _clear_carousel_surg_state(query.from_user.id)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        try:
            from carousel.handlers import finalize_carousel
        except Exception as e:
            logger.error(f"[carousel] import finalize_carousel failed: {e}", exc_info=True)
            return
        await finalize_carousel(update, context)
        return

    if query.data == "carousel_cancel":
        try:
            await query.answer("Отменено")
        except Exception:
            pass
        _clear_carousel_surg_state(query.from_user.id)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        try:
            from carousel.handlers import cancel_carousel
        except Exception:
            pass
        else:
            await cancel_carousel(update, context)
        return

    # ── B-roll монтаж (Pipeline #2) callbacks ───────────────────────
    if query.data == "broll_approve":
        try:
            await query.answer("🎬 Собираю ролик…")
        except Exception:
            pass
        try:
            from broll.handlers import assemble_broll_from_draft
        except Exception as e:
            logger.error(f"[broll] import failed in approve: {e}", exc_info=True)
            await query.message.reply_text(f"❌ Модуль B-roll не загружен: {e}")
            return
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await assemble_broll_from_draft(
            update, context, generate_voiceover, chat_id=query.message.chat_id,
        )
        return

    if query.data == "broll_regen":
        try:
            await query.answer("🔄 Переписываю сценарий…")
        except Exception:
            pass
        try:
            from broll.handlers import regenerate_broll_preview
        except Exception as e:
            logger.error(f"[broll] import failed in regen: {e}", exc_info=True)
            await query.message.reply_text(f"❌ Модуль B-roll не загружен: {e}")
            return
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await regenerate_broll_preview(update, context, claude)
        return

    if query.data == "broll_cancel":
        try:
            await query.answer("Отменено")
        except Exception:
            pass
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        try:
            from broll.handlers import cancel_broll
        except Exception:
            pass
        else:
            await cancel_broll(update, context)
        return

    if query.data == "idea_back_to_menu":
        # Close the current «Что дальше?» menu (strip keyboard) and
        # open a fresh main-menu message. Don't `edit_message_text` on
        # the old one — that loses the saved-idea content which the user
        # may still want to reference.
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        # Re-send the main menu (mirrors /start behaviour for maksim brand)
        active_brand = _get_active_brand_name()
        if active_brand == "maksim":
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=_maksim_greeting_text(user_id),
                reply_markup=_start_action_kb_maksim(),
            )
        else:
            # Generic fallback for non-maksim brands
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="Главное меню. Используй /start если что.",
            )
        await query.answer()
        return

    # --- Brand switch (inline picker from /start or /brand) ---
    if query.data.startswith("brand_set:"):
        global _active_brand
        new_brand = query.data.split(":", 1)[1].strip().lower()
        if new_brand not in BRANDS:
            await query.edit_message_text(f"❌ Неизвестный бренд: {new_brand}")
            return
        prev = _active_brand
        _active_brand = new_brand
        cfg = BRANDS[new_brand]
        logger.info(f"[user:{user_id}] brand_set callback: {prev} → {new_brand}")
        # Re-render the picker with the new selection marked
        await query.edit_message_text(
            f"✅ Бренд переключён: `{prev}` → *{new_brand}*\n\n"
            f"_{cfg.get('description', '')}_\n\n"
            f"Новые карточки будут создаваться с Бренд=`{new_brand}`.\n"
            f"Сбрасывается на `default` при рестарте бота.",
            parse_mode="Markdown",
            reply_markup=_brand_picker_kb(new_brand),
        )
        return

    # --- Launch monitor: approve / skip a launch digest item ---
    if query.data.startswith("launch_skip:"):
        short_id = query.data.split(":", 1)[1]
        item = await _launch_find_item_by_short_id(short_id)
        if item:
            n = launch_monitor.mark_group_status(item["id"], "skipped")
            await query.answer(f"Пропустил ({n})" if n > 1 else "Пропустил")
        else:
            await query.answer("Не найдено")
            return
        # Mark this item's message as skipped (remove buttons, prepend marker)
        try:
            original = query.message.text_markdown or query.message.text or ""
            await query.edit_message_text(
                f"⏭ _пропущено_\n\n{original}",
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        except Exception:
            pass
        return

    if query.data.startswith("launch_approve:"):
        short_id = query.data.split(":", 1)[1]
        item = await _launch_find_item_by_short_id(short_id)
        if not item:
            await query.answer("Не найдено")
            return
        await query.answer("Создаю карточку + скачиваю медиа…")
        await _launch_run_approval(
            context.bot, query.message.chat_id, query.from_user.id, item, short_id, query.message
        )
        return

    if query.data.startswith("launch_retry:"):
        short_id = query.data.split(":", 1)[1]
        # Reset this record back to 'queued' (the state get_pending_digest reads)
        # so the pipeline can re-run on it.
        import sqlite3
        try:
            with sqlite3.connect(launch_monitor.DB_PATH) as conn:
                n = conn.execute(
                    "UPDATE seen SET status='queued' WHERE id LIKE ?",
                    (short_id + "%",),
                ).rowcount
                conn.commit()
        except Exception as e:
            logger.error(f"launch_retry reset failed: {e}")
            n = 0
        if n == 0:
            await query.answer("Не нашёл запись в БД")
            return
        item = await _launch_find_item_by_short_id(short_id)
        if not item:
            await query.answer("Запись исчезла — перезапусти /launches")
            return
        await query.answer("🔁 Перезапускаю пайплайн…")
        await _launch_run_approval(
            context.bot, query.message.chat_id, query.from_user.id, item, short_id, query.message
        )
        return

    # --- Card actions from /cards (fetch script from Notion) ---
    if query.data.startswith("card_continue:"):
        card_id_prefix = query.data.split(":", 1)[1]
        all_cards = await asyncio.to_thread(fetch_notion_cards, limit=30)
        card = _pick_card_apply_brand(all_cards, card_id_prefix)
        if not card:
            await query.edit_message_text("Карточка не найдена.")
            return

        full_id = card["id"]
        await query.edit_message_text("📄 Загружаю сценарий из Notion...")
        try:
            script_text = await asyncio.to_thread(fetch_notion_page_script, full_id)
            if not script_text:
                await query.edit_message_text("В карточке нет сценария. Сначала добавь сценарий через бот.")
                return

            # Restore sources from Notion page
            sources = await asyncio.to_thread(fetch_notion_page_sources, full_id)

            # Build card_data
            card_data = {
                "title": card["title"],
                "rubric": card.get("rubric", "Свободный формат"),
                "platforms": _default_platforms(),
                "format": ["Short video"],
                "cta": script_text.strip().split("\n")[-1] if script_text.strip() else "",
            }

            pending[user_id] = pending.get(user_id) or {}
            pending[user_id]["script"] = script_text
            pending[user_id]["card_data"] = card_data
            pending[user_id]["notion_page_id"] = full_id
            pending[user_id]["notion_url"] = card["url"]
            pending[user_id]["idea"] = script_text[:200]
            if sources.get("source_urls"):
                pending[user_id]["source_urls"] = sources["source_urls"]
            _save_pending(pending)

            char_count = len(script_text)
            preview = (
                f"📝 СЦЕНАРИЙ ({card['title']}):\n\n"
                f"{script_text}\n\n"
                f"———\n"
                f"📊 {char_count} символов\n"
            )
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Утвердить → обложка", callback_data="approve"),
                    InlineKeyboardButton("🔄 Переписать", callback_data="rewrite"),
                ],
                [
                    InlineKeyboardButton("✏️ Внести правки", callback_data="edit_mode"),
                    InlineKeyboardButton("✏️ Другой хук", callback_data="new_hook"),
                ],
                [InlineKeyboardButton("💾 Отложить как идею", callback_data="save_to_notion")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
            ])
            await query.edit_message_text(preview, reply_markup=keyboard)
        except Exception as e:
            logger.error(f"Ошибка card_continue: {e}", exc_info=True)
            await query.edit_message_text(f"Ошибка: {e}")
        return

    # ─── card → carousel: точка входа из меню карточки в Pipeline #6 ────
    # Артём (25 мая): «карусель — отдельно стоящий пайплайн, хотя должна
    # уметь брать сценарий существующей карточки». Этот handler читает
    # script из карточки → кладёт seed в pending → показывает выбор шаблона.
    # После клика carousel_tpl:<X> если в pending есть `carousel_seed_text` —
    # generate_carousel_preview зовётся СРАЗУ с seed как темой, без ожидания
    # state=awaiting_carousel_theme.
    # ChatGPT review M7 (27 May 2026): «🔄 Сделать заново» раньше работало через
    # мутацию query.data + fall-through на «card_to_carousel:» — НЕ работало
    # на проде (PTB CallbackQuery с __slots__: setattr query.data может пройти
    # тихо, но второй блок `if` не подхватывал новое значение в той же async-функции).
    # Артём поймал 27 мая. Правильное решение: вынести логику в helper, два
    # handler'а зовут её с force-флагом.
    _card_carousel_force = False
    _card_carousel_prefix: str | None = None
    if query.data.startswith("card_to_carousel_force:"):
        try:
            from carousel.handlers import _drop_carousel_draft
            _drop_carousel_draft(query.from_user.id)
        except Exception as e:
            logger.warning(f"[card_to_carousel_force] drop draft failed: {e}")
        _card_carousel_force = True
        _card_carousel_prefix = query.data.split(":", 1)[1]
    elif query.data.startswith("card_to_carousel:"):
        _card_carousel_prefix = query.data.split(":", 1)[1]

    if _card_carousel_prefix is not None:
        card_id_prefix = _card_carousel_prefix
        all_cards = await asyncio.to_thread(fetch_notion_cards, limit=30)
        card = _pick_card_apply_brand(all_cards, card_id_prefix)
        if not card:
            await query.edit_message_text("Карточка не найдена.")
            return
        full_id = card["id"]
        try:
            script_text = await asyncio.to_thread(fetch_notion_page_script, full_id)
        except Exception as e:
            logger.error(f"[card_to_carousel] fetch_notion_page_script failed: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Не смог загрузить сценарий из Notion: {e}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{full_id[:20]}")],
                ]),
            )
            return
        if not script_text or not script_text.strip():
            await query.edit_message_text(
                "❌ В карточке нет сценария — добавь сценарий, потом возвращайся "
                "и нажми «🎨 В карусель» снова.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{full_id[:20]}")],
                ]),
            )
            return

        # C (26 May 2026): защита от перезаписи. Если для этой же карточки уже
        # есть draft карусели — показать выбор «открыть существующий / заново»,
        # вместо тихой перезаписи прошлой работы.
        # 27 May 2026: при force=True (юзер нажал «🔄 Сделать заново») draft
        # УЖЕ сдропан выше — C-check пропускаем, иначе бесконечный диалог.
        try:
            from carousel.handlers import _load_carousel_draft, _existing_carousel_for_card_detect
            existing_draft = _load_carousel_draft(user_id)
            card_url = card.get("url", "")
            if (not _card_carousel_force) and _existing_carousel_for_card_detect(existing_draft, card_url):
                # Сценарий есть. Считаем сколько слайдов и когда.
                n_slides = len(existing_draft.get("slides", []))
                # F6: «Открыть существующий» теперь несёт card_id — handler
                # сверит ownership перед открытием.
                await query.edit_message_text(
                    f"⚠️ Для этой карточки уже есть карусель — <b>{n_slides} слайдов</b>.\n\n"
                    "Открыть существующий сценарий или сделать заново с нуля?",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("✏️ Открыть существующий", callback_data=f"carousel_back_to_preview:{full_id[:20]}")],
                        [InlineKeyboardButton("🔄 Сделать заново", callback_data=f"card_to_carousel_force:{full_id[:20]}")],
                        [InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{full_id[:20]}")],
                    ]),
                )
                return
        except Exception as e:
            logger.warning(f"[card_to_carousel] draft-detect failed (non-fatal): {e}")

        # F4 (26 May 2026): seed как session-объект {session_id, card_id, ...}.
        # Каждый клик «🎨 В карусель» создаёт НОВЫЙ session_id — старые
        # кнопки шаблона от прошлой карточки/прошлого клика становятся stale
        # и handler их отбрасывает.
        _clear_carousel_surg_state(query.from_user.id)
        pending[user_id] = pending.get(user_id) or {}
        seed = _make_carousel_seed(
            card_id=full_id,
            card_url=card.get("url", ""),
            text=script_text.strip(),
        )
        pending[user_id]["carousel_seed"] = seed
        # state НЕ ставим — carousel_tpl запустит превью сразу,
        # без awaiting_carousel_theme (см. handler ниже).
        _save_pending(pending)
        session_id = seed["session_id"]
        await query.edit_message_text(
            "🎨 <b>Карусель из карточки</b>\n\n"
            f"Сценарий <i>«{card['title'][:60]}»</i> возьму как seed для Opus.\n\n"
            "Выбери стиль обложки:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("M2 · Pit-Stop (universал, гайды/советы)", callback_data=f"carousel_tpl:M2:{session_id}")],
                [InlineKeyboardButton("M1 · Anniversary (анонсы, события, юбилеи)", callback_data=f"carousel_tpl:M1:{session_id}")],
                [InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{full_id[:20]}")],
                [InlineKeyboardButton("❌ Отмена", callback_data="carousel_cancel")],
            ]),
        )
        return

    if query.data.startswith("card_script:"):
        card_id_prefix = query.data.split(":", 1)[1]
        all_cards = await asyncio.to_thread(fetch_notion_cards, limit=30)
        card = _pick_card_apply_brand(all_cards, card_id_prefix)
        if not card:
            await query.edit_message_text("Карточка не найдена.")
            return
        full_id = card["id"]
        try:
            script_text = await asyncio.to_thread(fetch_notion_page_script, full_id)
        except Exception as e:
            logger.error(f"card_script fetch error: {e}", exc_info=True)
            script_text = ""

        pending[user_id] = pending.get(user_id) or {}
        pending[user_id]["script_edit_card"] = full_id
        _save_pending(pending)

        if script_text:
            preview = script_text if len(script_text) <= 3500 else script_text[:3500] + "\n\n[...обрезано]"
            text = (
                f"📜 Сценарий «{card['title']}»\n"
                f"({len(script_text)} символов)\n\n"
                f"{preview}"
            )
        else:
            text = (
                f"📜 Сценарий «{card['title']}»\n\n"
                "Пока пусто. Нажми «✏️ Заменить», чтобы добавить."
            )

        buttons = [
            [
                InlineKeyboardButton("🔄 Переписать", callback_data=f"script_rewrite:{full_id[:20]}"),
                InlineKeyboardButton("✏️ С правкой", callback_data=f"script_instruct:{full_id[:20]}"),
            ],
            [InlineKeyboardButton("✏️ Заменить целиком", callback_data=f"script_replace:{full_id[:20]}")],
            [InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{full_id[:20]}")],
        ]
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        except Exception:
            # Message too long or other formatting issue — send as file
            from io import BytesIO
            bio = BytesIO(script_text.encode("utf-8"))
            bio.name = "script.txt"
            await query.message.reply_document(
                document=bio,
                caption=f"📜 Сценарий «{card['title']}» ({len(script_text)} символов)",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        return

    if query.data.startswith("script_replace:"):
        card_id_prefix = query.data.split(":", 1)[1]
        all_cards = await asyncio.to_thread(fetch_notion_cards, limit=30)
        card = _pick_card_apply_brand(all_cards, card_id_prefix)
        if not card:
            await query.edit_message_text("Карточка не найдена.")
            return
        full_id = card["id"]

        pending[user_id] = pending.get(user_id) or {}
        pending[user_id]["state"] = "edit_script"
        pending[user_id]["script_edit_card"] = full_id
        pending[user_id]["script_edit_title"] = card["title"]
        _save_pending(pending)

        await query.edit_message_text(
            f"✏️ Пришли новый текст сценария для «{card['title']}» одним сообщением.\n\n"
            "Он полностью заменит текущий сценарий в Notion. Можно присылать длинный текст — "
            "разобью на параграфы автоматически.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Отмена", callback_data=f"card_script:{full_id[:20]}")],
            ]),
        )
        return

    if query.data.startswith("script_rewrite:"):
        # AI regeneration: same prompt stack as /cards, different angle.
        card_id_prefix = query.data.split(":", 1)[1]
        all_cards = await asyncio.to_thread(fetch_notion_cards, limit=30)
        card = _pick_card_apply_brand(all_cards, card_id_prefix)
        if not card:
            await query.edit_message_text("Карточка не найдена.")
            return
        full_id = card["id"]
        try:
            current_script = await asyncio.to_thread(fetch_notion_page_script, full_id)
        except Exception as e:
            logger.error(f"script_rewrite fetch: {e}", exc_info=True)
            current_script = ""
        if not current_script:
            await query.edit_message_text(
                "У карточки пока нет сценария. Используй «✏️ Заменить целиком».",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ К сценарию", callback_data=f"card_script:{full_id[:20]}")],
                ]),
            )
            return

        await query.edit_message_text(f"🔄 Переписываю сценарий «{card['title']}»...")
        try:
            _brand = _get_active_brand()
            _script_system = _brand_script_prompt(SCRIPT_PROMPT)
            resp = await asyncio.to_thread(
                claude.messages.create,
                model="claude-opus-4-7",
                max_tokens=1024,
                system=_script_system,
                messages=[
                    {"role": "user", "content": f"Тема: {card['title']}"},
                    {"role": "assistant", "content": current_script},
                    {"role": "user", "content": "Перепиши сценарий на ту же тему. Другой хук, другая структура, другой ритм. Сохрани факты и общий посыл, но найди свежий угол подачи."},
                ],
            )
            new_script = resp.content[0].text.strip()
            if new_script.upper().startswith("СЦЕНАРИЙ"):
                new_script = new_script.split("\n", 1)[-1].strip()
            new_script = await _force_shorten(new_script)

            # Stash as preview — user confirms before it hits Notion.
            pending[user_id] = pending.get(user_id) or {}
            pending[user_id]["script_preview_text"] = new_script
            pending[user_id]["script_preview_card"] = full_id
            pending[user_id]["script_preview_title"] = card["title"]
            _save_pending(pending)

            preview = (
                f"📝 НОВЫЙ СЦЕНАРИЙ «{card['title']}»:\n\n"
                f"{new_script}\n\n"
                f"———\n"
                f"📊 {len(new_script)} символов"
            )
            await query.edit_message_text(
                preview,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ Сохранить в Notion", callback_data=f"script_save:{full_id[:20]}"),
                        InlineKeyboardButton("🔄 Ещё вариант", callback_data=f"script_rewrite:{full_id[:20]}"),
                    ],
                    [
                        InlineKeyboardButton("✏️ С правкой", callback_data=f"script_instruct:{full_id[:20]}"),
                        InlineKeyboardButton("❌ Отмена", callback_data=f"card_script:{full_id[:20]}"),
                    ],
                ]),
            )
        except Exception as e:
            logger.error(f"script_rewrite failed: {e}", exc_info=True)
            await query.edit_message_text(
                f"Ошибка генерации: {e}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ К сценарию", callback_data=f"card_script:{full_id[:20]}")],
                ]),
            )
        return

    if query.data.startswith("script_instruct:"):
        # Wait for a text/voice instruction, then apply AI edit.
        # Source: preview stash if present (iterative edits), else current Notion script.
        card_id_prefix = query.data.split(":", 1)[1]
        all_cards = await asyncio.to_thread(fetch_notion_cards, limit=30)
        card = _pick_card_apply_brand(all_cards, card_id_prefix)
        if not card:
            await query.edit_message_text("Карточка не найдена.")
            return
        full_id = card["id"]
        pending[user_id] = pending.get(user_id) or {}
        pending[user_id]["state"] = "script_instruct_wait"
        pending[user_id]["script_instruct_card"] = full_id
        pending[user_id]["script_instruct_title"] = card["title"]
        # If we're iterating on a preview from rewrite/instruct, keep it so the
        # edit applies to the preview rather than the stale Notion version.
        has_preview = bool(pending[user_id].get("script_preview_text")) and \
            pending[user_id].get("script_preview_card") == full_id
        _save_pending(pending)
        source_hint = "превью выше" if has_preview else "сценарий из Notion"
        await query.edit_message_text(
            f"✏️ Что поправить в сценарии «{card['title']}»?\n"
            f"Источник: {source_hint}\n\n"
            "Пришли инструкцию — текстом или голосовым. Например:\n"
            "• «сделай длиннее, добавь пример про Теслу»\n"
            "• «сократи середину, усиль финал»\n"
            "• «замени хук на вопрос, финал — провокационный»\n\n"
            "Я применю правку и покажу результат перед сохранением.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Отмена", callback_data=f"card_script:{full_id[:20]}")],
            ]),
        )
        return

    if query.data.startswith("script_save:"):
        # Commit the previewed script to Notion.
        card_id_prefix = query.data.split(":", 1)[1]
        stashed = pending.get(user_id, {}).get("script_preview_text")
        full_id = pending.get(user_id, {}).get("script_preview_card")
        title = pending.get(user_id, {}).get("script_preview_title", "")
        if not stashed or not full_id or not full_id.startswith(card_id_prefix):
            await query.edit_message_text(
                "Ничего не нашёл для сохранения — попробуй переписать заново.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ К сценарию", callback_data=f"card_script:{card_id_prefix}")],
                ]),
            )
            return
        try:
            await asyncio.to_thread(update_notion_page_script, full_id, stashed)
            # Clear stash.
            for k in ("script_preview_text", "script_preview_card", "script_preview_title"):
                pending[user_id].pop(k, None)
            _save_pending(pending)
            await query.edit_message_text(
                f"✅ Сценарий «{title}» обновлён в Notion ({len(stashed)} символов).",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📜 Посмотреть", callback_data=f"card_script:{full_id[:20]}")],
                    [InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{full_id[:20]}")],
                ]),
            )
        except Exception as e:
            logger.error(f"script_save failed: {e}", exc_info=True)
            await query.edit_message_text(f"Ошибка сохранения: {e}")
        return

    if query.data.startswith("card_autobroll:"):
        # Автономная графика: Claude Code на сервере анализирует сценарий
        # карточки, рисует 6 B-roll-вставок в Remotion и рендерит их.
        card_id_prefix = query.data.split(":", 1)[1]
        all_cards = await asyncio.to_thread(fetch_notion_cards, limit=30)
        card = _pick_card_apply_brand(all_cards, card_id_prefix)
        if not card:
            await query.edit_message_text("Карточка не найдена.")
            return
        full_id = card["id"]
        _ab_data = {"notion_page_id": full_id, "card_data": {"title": card["title"]}}
        proj_dir = _project_dir(_ab_data)
        if not proj_dir:
            await query.edit_message_text("❌ Не удалось определить папку проекта.")
            return
        proj_dir.mkdir(parents=True, exist_ok=True)

        script_text = await asyncio.to_thread(fetch_notion_page_script, full_id)
        if not script_text or len(script_text.strip()) < 30:
            await query.edit_message_text(
                "❌ В карточке нет сценария.\n\n"
                "Сначала сгенерируй или впиши сценарий — графика строится из него."
            )
            return

        await query.edit_message_text(
            f"🎨 Графика для «{card['title']}»\n\n"
            f"Claude на сервере анализирует сценарий, рисует 6 B-roll-вставок "
            f"и рендерит их. Это ~8–12 минут — пришлю, когда будет готово."
        )

        try:
            from auto_broll import generate_auto_broll
            # W1 (27 May 2026): namespace separation. AutoBroll теперь пишет
            # в proj_dir/autobroll/auto_NN.mp4 — отдельно от broll_NN.mp4
            # (SMM-загрузки). Чистим ТОЛЬКО старые auto_*.mp4, реальные
            # клипы НЕ трогаем (раньше затирали SMM-видео).
            _autobroll_dir = proj_dir / "autobroll"
            if _autobroll_dir.exists():
                for _old in _autobroll_dir.glob("auto_*.mp4"):
                    _old.unlink()
            clips, cost_usd = await asyncio.to_thread(
                generate_auto_broll, script_text, proj_dir
            )
        except Exception as e:
            logger.error(f"card_autobroll failed: {e}", exc_info=True)
            await query.edit_message_text(
                f"⚠️ Не удалось сгенерировать графику:\n{e}\n\n"
                f"Можно повторить или подобрать B-roll вручную (🎬 B-roll)."
            )
            return

        # На подписке (CLAUDE_CODE_OAUTH_TOKEN) деньги по токенам не списываются —
        # тянется из лимита Max-плана; cost_usd тут это эквивалент по API.
        _on_sub = bool(os.getenv("CLAUDE_CODE_OAUTH_TOKEN"))
        _cost_line = (
            f"💳 Claude: ~${cost_usd:.3f} из подписки Max (не реальное списание)"
            if _on_sub else
            f"💵 Claude (метеред API): ${cost_usd:.3f}"
        )
        await query.edit_message_text(
            f"✅ Графика готова: {len(clips)} вставок для «{card['title']}».\n"
            f"{_cost_line}\n\n"
            f"Дальше — собери ролик в Про-монтаже (хук-аватар → 50/50 → CTA).",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎬 Собрать ролик", callback_data=f"card_assemble:{full_id[:20]}")],
                [InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{full_id[:20]}")],
            ]),
        )
        return

    if query.data.startswith("card_hfbroll:"):
        # Автономная графика через HyperFrames (ВТОРОЙ движок, параллельно
        # Remotion). Claude Code на сервере пишет 6 HTML-композиций и рендерит
        # их. Выход в proj_dir/hyperframes/hf_NN.mp4 — отдельный namespace.
        # См. project_maksim_dual_broll_engine.md.
        card_id_prefix = query.data.split(":", 1)[1]
        all_cards = await asyncio.to_thread(fetch_notion_cards, limit=30)
        card = _pick_card_apply_brand(all_cards, card_id_prefix)
        if not card:
            await query.edit_message_text("Карточка не найдена.")
            return
        full_id = card["id"]
        _hf_data = {"notion_page_id": full_id, "card_data": {"title": card["title"]}}
        proj_dir = _project_dir(_hf_data)
        if not proj_dir:
            await query.edit_message_text("❌ Не удалось определить папку проекта.")
            return
        proj_dir.mkdir(parents=True, exist_ok=True)

        script_text = await asyncio.to_thread(fetch_notion_page_script, full_id)
        if not script_text or len(script_text.strip()) < 30:
            await query.edit_message_text(
                "❌ В карточке нет сценария.\n\n"
                "Сначала сгенерируй или впиши сценарий — графика строится из него."
            )
            return

        await query.edit_message_text(
            f"🎨 Графика (HyperFrames) для «{card['title']}»\n\n"
            f"Claude на сервере пишет 6 HTML-вставок и рендерит их через "
            f"HyperFrames. Это ~8–12 минут — пришлю, когда будет готово."
        )

        try:
            from hyperframes_broll import (
                generate_hyperframes_broll,
                HyperFramesInterrupted,
                HyperFramesTimeout,
            )
            # Чистим только старые hf_*.mp4 в hyperframes/ (namespace отдельный
            # от autobroll/ и от SMM broll_*.mp4 — их НЕ трогаем).
            _hf_dir = proj_dir / "hyperframes"
            if _hf_dir.exists():
                for _old in _hf_dir.glob("hf_*.mp4"):
                    _old.unlink()
            clips, cost_usd = await asyncio.to_thread(
                generate_hyperframes_broll, script_text, proj_dir
            )
        except HyperFramesInterrupted as e:
            # Отдельная ветка: Claude Code прерван снаружи (systemd-restart,
            # OOM). НЕ показываем юзеру «упал» — это инфра-событие. Кнопка
            # «🔁 Повторить» = тот же callback, что и текущий.
            logger.warning(f"card_hfbroll interrupted: {e}")
            await query.edit_message_text(
                f"🔁 Графика не успела собраться — сервис перезапускался "
                f"во время рендера.\n\n"
                f"Сценарий и карточка на месте. Жми «Повторить» — начнём заново.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🔁 Повторить HyperFrames",
                        callback_data=query.data,  # тот же card_hfbroll:<id>
                    )],
                    [InlineKeyboardButton(
                        "🎨 Попробовать Remotion",
                        callback_data=f"card_autobroll:{card_id_prefix}",
                    )],
                    [InlineKeyboardButton(
                        "◀️ К карточке",
                        callback_data=f"notion_card:{card_id_prefix}",
                    )],
                ]),
            )
            return
        except HyperFramesTimeout as e:
            # Claude Code не уложился в CLAUDE_TIMEOUT (900s по умолчанию).
            # Не transient — retry той же длины тоже не уложится с большой
            # вероятностью. Рекомендуем Remotion (быстрее, ~2-3 мин на
            # генерацию + рендер) или сократить сценарий.
            logger.error(f"card_hfbroll timeout: {e}")
            await query.edit_message_text(
                f"⏱ {e}\n\n"
                f"Рекомендую попробовать Remotion — он обычно укладывается "
                f"в 3-5 минут. Сценарий и карточка на месте.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🎨 Попробовать Remotion",
                        callback_data=f"card_autobroll:{card_id_prefix}",
                    )],
                    [InlineKeyboardButton(
                        "🔁 Повторить HyperFrames",
                        callback_data=query.data,
                    )],
                    [InlineKeyboardButton(
                        "◀️ К карточке",
                        callback_data=f"notion_card:{card_id_prefix}",
                    )],
                ]),
            )
            return
        except Exception as e:
            logger.error(f"card_hfbroll failed: {e}", exc_info=True)
            # Обрезаем str(e) до 200 симв — защита от утечки длинных
            # exception-сообщений типа subprocess.TimeoutExpired, чей
            # str() содержит всю команду с промптом (~4KB). Уже починено
            # точечно через HyperFramesTimeout, но общая защита не помешает.
            short_e = str(e)
            if len(short_e) > 200:
                short_e = short_e[:200] + "…"
            await query.edit_message_text(
                f"⚠️ Не удалось сгенерировать графику (HyperFrames):\n{short_e}\n\n"
                f"Можно повторить, попробовать Remotion-движок или подобрать "
                f"B-roll вручную (🎬 B-roll).",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🔁 Повторить HyperFrames",
                        callback_data=query.data,
                    )],
                    [InlineKeyboardButton(
                        "🎨 Попробовать Remotion",
                        callback_data=f"card_autobroll:{card_id_prefix}",
                    )],
                    [InlineKeyboardButton(
                        "◀️ К карточке",
                        callback_data=f"notion_card:{card_id_prefix}",
                    )],
                ]),
            )
            return

        _on_sub = bool(os.getenv("CLAUDE_CODE_OAUTH_TOKEN"))
        _cost_line = (
            f"💳 Claude: ~${cost_usd:.3f} из подписки Max (не реальное списание)"
            if _on_sub else
            f"💵 Claude (метеред API): ${cost_usd:.3f}"
        )
        await query.edit_message_text(
            f"✅ Графика (HyperFrames) готова: {len(clips)} вставок для "
            f"«{card['title']}».\n{_cost_line}\n\n"
            f"Дальше — собери ролик в Про-монтаже (хук-аватар → 50/50 → CTA).",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎬 Собрать ролик", callback_data=f"card_assemble:{full_id[:20]}")],
                [InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{full_id[:20]}")],
            ]),
        )
        return

    if query.data.startswith("card_assemble:"):
        card_id_prefix = query.data.split(":", 1)[1]
        cid = card_id_prefix[:20]

        # Brand-aware menu: если у бренда есть default_assembly_layout
        # (например, shoes → smart + субтитры), показываем одну большую
        # кнопку для one-tap-производства, остальные форматы прячем под
        # «🔧 Другие варианты». Без `cid_brand_full` — обычное 4×2 меню.
        _brand_for_menu = _get_active_brand()
        _brand_default_layout = _brand_for_menu.get("default_assembly_layout")
        _brand_default_subs = _brand_for_menu.get("default_assembly_subs", True)
        _brand_name_for_menu = _get_active_brand_name()

        full_menu_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔲 Сплит", callback_data=f"card_asm_go:s:0:{cid}"),
                InlineKeyboardButton("🔲 + субтитры", callback_data=f"card_asm_go:s:1:{cid}"),
            ],
            [
                InlineKeyboardButton("🎥 Динамический", callback_data=f"card_asm_go:d:0:{cid}"),
                InlineKeyboardButton("🎥 + субтитры", callback_data=f"card_asm_go:d:1:{cid}"),
            ],
            [
                InlineKeyboardButton("🎬 Про-монтаж", callback_data=f"card_asm_go:p:0:{cid}"),
                InlineKeyboardButton("🎬 + субтитры", callback_data=f"card_asm_go:p:1:{cid}"),
            ],
            [
                InlineKeyboardButton("🧠 ИИ-монтаж", callback_data=f"card_asm_go:a:0:{cid}"),
                InlineKeyboardButton("🧠 + субтитры", callback_data=f"card_asm_go:a:1:{cid}"),
            ],
            [
                InlineKeyboardButton("🎯 Смарт-микс", callback_data=f"card_asm_go:m:0:{cid}"),
                InlineKeyboardButton("🎯 + субтитры", callback_data=f"card_asm_go:m:1:{cid}"),
            ],
            [InlineKeyboardButton("◀️ Назад", callback_data=f"notion_card:{cid}")],
        ])
        full_menu_text = (
            "🎬 Выбери формат сборки:\n\n"
            "🔲 **Сплит** — B-roll сверху + аватар снизу (50/50)\n"
            "🎥 **Динамический** — аватар ↔ B-roll на весь экран\n"
            "🎬 **Про-монтаж** — фикс. формат: хук-аватар → 50/50 → CTA\n"
            "🧠 **ИИ-монтаж** — Opus читает сценарий и B-roll, сам решает "
            "где полный экран, где 50/50, где сменить план. Для разнотипного "
            "B-roll (реальное видео + графика + фото).\n"
            "🎯 **Смарт-микс** — видео целиком на весь экран + фото в сплит.\n\n"
            "💡 _Про-монтаж — стабильный шаблон. ИИ-монтаж — умнее, но дороже_\n"
            "_(+1 вызов Opus) и менее предсказуем._\n\n"
            "📝 «+ субтитры» — word-by-word анимированные титры (CapCut-стиль)"
        )

        # Раскрытие развёрнутого меню по запросу — отдельный callback
        if query.data == f"card_assemble:{cid}__full":
            assemble_kb = full_menu_kb
            assemble_text = full_menu_text
        elif _brand_name_for_menu == "maksim":
            # Меню сборки под Максима (одобрено): дефолт — Про-монтаж
            # (хук-аватар → 50/50 → CTA), вторая — ИИ-монтаж (Opus решает
            # раскладку). Без следов обувного бренда.
            assemble_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "🎬 Собрать (Про-монтаж + субтитры)",
                    callback_data=f"card_asm_go:p:1:{cid}",
                )],
                [InlineKeyboardButton(
                    "🧠 ИИ-монтаж + субтитры (Opus решает раскладку)",
                    callback_data=f"card_asm_go:a:1:{cid}",
                )],
                [InlineKeyboardButton(
                    "🔧 Другие форматы",
                    callback_data=f"card_assemble:{cid}__full",
                )],
                [InlineKeyboardButton("◀️ Назад", callback_data=f"notion_card:{cid}")],
            ])
            assemble_text = (
                "🎬 Как собрать ролик?\n\n"
                "🎬 **Про-монтаж** — фикс. формат: хук-аватар на весь экран → "
                "50/50 (B-roll сверху, аватар снизу) → аватар-CTA. Стабильно, "
                "для типовых роликов.\n\n"
                "🧠 **ИИ-монтаж** — Opus читает сценарий + B-roll и сам решает "
                "раскладку посегментно (где полный экран, где 50/50). Для "
                "разнотипного материала. Чуть дороже (+1 вызов Opus).\n\n"
                "Оба варианта — с субтитрами. «🔧 Другие форматы» — сплит / "
                "смарт-микс / динамический."
            )
        elif _brand_default_layout:
            # Two-tap menu для бренда с дефолтом (shoes):
            # Главная кнопка = default layout (smart-mix + subs)
            # Альтернатива = full-screen only (для lifestyle-фото где split режет)
            _layout_short = {"smart": "m", "pro": "p", "split": "s", "dynamic": "d", "fullscreen": "f"}.get(
                _brand_default_layout, "s"
            )
            _subs_flag = "1" if _brand_default_subs else "0"
            _layout_label_one = {
                "smart": "🎯 Смарт-микс",
                "pro": "🎬 Про-монтаж",
                "split": "🔲 Сплит",
                "dynamic": "🎥 Динамический",
                "fullscreen": "🎥 Full-screen",
            }.get(_brand_default_layout, _brand_default_layout)
            _subs_label_one = " + субтитры" if _brand_default_subs else ""
            assemble_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    f"🎬 Собрать ({_layout_label_one}{_subs_label_one})",
                    callback_data=f"card_asm_go:{_layout_short}:{_subs_flag}:{cid}",
                )],
                [InlineKeyboardButton(
                    "🎥 Альтернатива — Full-screen (без сплитов)",
                    callback_data=f"card_asm_go:f:1:{cid}",
                )],
                [InlineKeyboardButton(
                    "🔧 Другие варианты сборки",
                    callback_data=f"card_assemble:{cid}__full",
                )],
                [InlineKeyboardButton("◀️ Назад", callback_data=f"notion_card:{cid}")],
            ])
            assemble_text = (
                f"🎬 Сборка для бренда **{_brand_name_for_menu}**\n\n"
                f"**Дефолт:** {_layout_label_one}{_subs_label_one}\n"
                f"  Видео целиком + фото в сплит. Лучше когда обувь крупным "
                f"планом — товар сохраняется в split-секции.\n\n"
                f"**Альтернатива:** 🎥 Full-screen без сплитов\n"
                f"  Все B-roll на полный экран, аватар только в начале и конце. "
                f"Лучше когда фото 9:16 lifestyle с моделью и обувью — "
                f"split режет важное."
            )
        else:
            assemble_kb = full_menu_kb
            assemble_text = full_menu_text
        # Try edit first; if this is a video message, send a new message
        try:
            await query.edit_message_text(
                assemble_text, parse_mode="Markdown", reply_markup=assemble_kb,
            )
        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=assemble_text, parse_mode="Markdown", reply_markup=assemble_kb,
            )
        return

    if query.data.startswith("card_asm_go:"):
        # Format: card_asm_go:LAYOUT:SUBS:CARD_ID
        parts = query.data.split(":", 3)
        layout_code = parts[1]   # 's' or 'd'
        subs_flag = parts[2]     # '0' or '1'
        card_id_prefix = parts[3]

        # 'a' (ИИ-монтаж) использует тот же сборщик _assemble_pro, но план
        # строит Opus (generate_montage_plan), а не детерминированный bookend.
        layout_map = {"d": "dynamic", "s": "split", "p": "pro", "a": "pro",
                      "m": "smart", "f": "fullscreen"}
        layout = layout_map.get(layout_code, "split")
        _ai_montage = layout_code == "a"
        with_subs = subs_flag == "1"

        all_cards = await asyncio.to_thread(fetch_notion_cards, limit=30)
        card = _pick_card_apply_brand(all_cards, card_id_prefix)
        if not card:
            await query.edit_message_text("Карточка не найдена.")
            return

        _tmp_data = {"notion_page_id": card["id"], "card_data": {"title": card["title"]}}
        proj_dir = _project_dir(_tmp_data)
        if not proj_dir or not proj_dir.exists():
            await query.edit_message_text(
                "❌ Нет папки проекта. Сначала сгенерируй аватар и выбери B-roll."
            )
            return

        # Выбор источника B-roll по namespace (фикс бага C1: план монтажа и
        # клипы для сборки ОБЯЗАНЫ браться из одного источника). Приоритет:
        # HyperFrames > Remotion > SMM-реал > mix. Это же даёт чистое сравнение
        # движков — собирается ровно та графика, что только что сгенерили.
        # `_find_broll(proj_dir, mode)` — единый источник истины и для плана
        # (ниже), и для ассемблера (broll_mode прокидывается в него).
        if _find_broll(proj_dir, "hf"):
            broll_mode = "hf"
        elif _find_broll(proj_dir, "ai"):
            broll_mode = "ai"
        elif _find_broll(proj_dir, "real"):
            broll_mode = "real"
        else:
            broll_mode = "mix"
        _broll_clips_for_plan = _find_broll(proj_dir, broll_mode)
        logger.info(
            f"[card_asm_go] broll_mode={broll_mode}, "
            f"{len(_broll_clips_for_plan)} клипов для плана/сборки"
        )

        layout_labels = {
            "split": "Сплит (50/50)",
            "dynamic": "Динамический (аватар ↔ B-roll)",
            "pro": "Про-монтаж (смешанные лейауты)",
            "smart": "Смарт-микс (видео full + фото split)",
            "fullscreen": "Full-screen (всё на полный экран, без сплитов)",
        }
        layout_label = "ИИ-монтаж (Opus решает раскладку)" if _ai_montage else layout_labels.get(layout, layout)
        subs_label = " + субтитры" if with_subs else ""

        # For pro layout, generate montage plan first
        montage_plan = None
        if layout == "pro":
            _plan_kind = "Opus анализирует сценарий + B-roll" if _ai_montage else "Claude создаёт монтажный план"
            await query.edit_message_text(
                f"{'🧠 ИИ-монтаж' if _ai_montage else '🎬 Про-монтаж'} «{card['title']}»\n\n"
                f"📋 Шаг 1/2: {_plan_kind}..."
            )
            try:
                # Get script text
                user_data = pending.get(user_id, {})
                script_text = user_data.get("script", "")
                if not script_text:
                    # Try reading from project folder
                    script_file = proj_dir / "script.txt"
                    if script_file.exists():
                        script_text = script_file.read_text(encoding="utf-8")

                # Get B-roll descriptions with durations.
                # ИЗ ТОГО ЖЕ источника, что увидит ассемблер (broll_mode) —
                # иначе план (по числу клипов) разойдётся с реальными путями.
                broll_files = _broll_clips_for_plan
                broll_descriptions = []
                for bf in broll_files:
                    try:
                        _bp = subprocess.run(
                            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                             "-of", "csv=p=0", str(bf)],
                            capture_output=True, text=True, timeout=10,
                        )
                        clip_dur = float(_bp.stdout.strip())
                        broll_descriptions.append(f"{bf.stem.replace('broll_', 'B-roll #')} ({clip_dur:.1f}s)")
                    except Exception:
                        broll_descriptions.append(bf.stem.replace("broll_", "B-roll #"))

                # Get audio duration from avatar
                avatar_files = sorted(proj_dir.glob("avatar_*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True)
                if avatar_files:
                    probe = subprocess.run(
                        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                         "-of", "csv=p=0", str(avatar_files[0])],
                        capture_output=True, text=True, timeout=10,
                    )
                    audio_duration = float(probe.stdout.strip())
                else:
                    audio_duration = 30.0

                if _ai_montage:
                    # ИИ-монтаж: Opus читает сценарий + список B-roll и сам
                    # решает раскладку посегментно (avatar_full / split /
                    # broll_full разной длины). Дороже (+1 вызов Opus), но
                    # умнее для разнотипного B-roll.
                    montage_plan = await asyncio.to_thread(
                        generate_montage_plan,
                        script_text, broll_descriptions, audio_duration,
                    )
                else:
                    # Детерминированный план — одобренный формат роликов
                    # Максима: хук-аватар на весь экран → split-вставки 50/50
                    # подряд → аватар-CTA. Без LLM-угадайки раскладок.
                    montage_plan = build_bookend_montage_plan(
                        audio_duration, len(broll_files),
                    )
                plan_summary = " → ".join(
                    f"{s['layout'].replace('_full','').replace('avatar','AV').replace('broll','BR')}"
                    f"({s['end']-s['start']:.0f}s)"
                    for s in montage_plan
                )
                await query.edit_message_text(
                    f"🎬 Про-монтаж «{card['title']}»\n\n"
                    f"📋 План: {plan_summary}\n\n"
                    f"⏳ Шаг 2/2: Собираю видео... Это займёт {'5-10' if with_subs else '3-5'} минут."
                )
            except Exception as e:
                logger.error(f"Montage plan failed: {e}", exc_info=True)
                await query.edit_message_text(
                    f"⚠️ Не удалось создать монтажный план: {e}\n\n"
                    f"Собираю в режиме сплит..."
                )
                layout = "split"
                montage_plan = None
        else:
            # Smart uses the pro-pipeline internally (segment-by-segment), so
            # render time is closer to pro than to split/dynamic.
            if layout == "smart":
                eta = "5-10" if with_subs else "3-5"
            else:
                eta = "3-7" if with_subs else "1-3"

            # Pre-flight check для smart-layout: показать сводку плана.
            # Помогает увидеть что фото поместятся в нужный ритм до того,
            # как ffmpeg отработает 5 минут.
            preflight_line = ""
            # Pre-flight для smart И fullscreen — оба используют smart_mix_cfg
            # для intro/outro/photo durations.
            if layout in ("smart", "fullscreen"):
                _brand_for_preflight = _get_active_brand_name()
                _smart_cfg = BRANDS.get(_brand_for_preflight, {}).get("smart_mix") or {}
                # Для fullscreen дефолты другие (1.5-3.5), под Артёма — расчёт
                # ниже через max/min учитывает оба случая.
                if layout == "fullscreen":
                    _smart_cfg = dict(_smart_cfg)  # копия, чтобы не мутировать
                    _smart_cfg.setdefault("intro_dur", 2.0)
                    _smart_cfg.setdefault("outro_dur", 3.0)
                    _smart_cfg["photo_dur_min"] = 1.5
                    _smart_cfg["photo_dur_max"] = 3.5
                    _smart_cfg["photo_dur_default"] = 2.5
                if _smart_cfg:
                    try:
                        # Считаем фото и видео в проекте
                        photos_dir = proj_dir / "photos"
                        n_photos = (
                            len(list(photos_dir.glob("*.jpg")) + list(photos_dir.glob("*.png")))
                            if photos_dir.exists() else 0
                        )
                        n_videos = len(list(proj_dir.glob("broll_*.mp4")))
                        # Длительность аватара
                        _av_files = sorted(proj_dir.glob("avatar_*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True)
                        _av_dur = 30.0
                        if _av_files:
                            try:
                                _ap = subprocess.run(
                                    ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                                     "-of", "csv=p=0", str(_av_files[0])],
                                    capture_output=True, text=True, timeout=10,
                                )
                                _av_dur = float(_ap.stdout.strip())
                            except Exception:
                                pass

                        intro = float(_smart_cfg.get("intro_dur", 1.5))
                        outro = float(_smart_cfg.get("outro_dur", 2.0))
                        pmin = float(_smart_cfg.get("photo_dur_min", 2.8))
                        pmax = float(_smart_cfg.get("photo_dur_max", 2.8))
                        pdef = float(_smart_cfg.get("photo_dur_default", 2.8))

                        if n_photos > 0 and pmin < pmax:
                            active = max(0.0, _av_dur - intro - outro)
                            ideal = active / n_photos if n_photos else pdef
                            actual = max(pmin, min(pmax, ideal))
                            note = "✅" if pmin <= ideal <= pmax else (
                                "⚠️ упёрся в минимум, дропну лишние с конца"
                                if ideal < pmin else
                                "⚠️ упёрся в максимум, slack уйдёт в CTA"
                            )
                            preflight_line = (
                                f"\n📊 План: {n_videos} видео + {n_photos} фото × "
                                f"{actual:.2f}с (идеал {ideal:.2f}с) — "
                                f"intro {intro:.0f}с / средняя {active:.1f}с / CTA {outro:.0f}с — {note}"
                            )
                        elif n_videos > 0:
                            preflight_line = f"\n📊 План: {n_videos} видео-клип(ов) на полный экран"
                    except Exception as e:
                        logger.warning(f"smart preflight failed: {e}")

            await query.edit_message_text(
                f"🎬 Собираю ролик «{card['title']}»...\n\n"
                f"Формат: {layout_label}{subs_label}{preflight_line}\n\n"
                f"Это займёт {eta} минуты."
            )

        try:
            # Resolve the brand for the card we're assembling — smart layout
            # picks the Ken Burns variant for photos based on this (e.g.
            # shoes → anchor to frame bottom so product stays in the split
            # slot). Falls back to the global /brand if the card has no
            # «Бренд» property set.
            _current_brand = _get_active_brand_name()
            _brand_smart_cfg = BRANDS.get(_current_brand, {}).get("smart_mix")
            final_path = await asyncio.to_thread(
                assemble_auto_montage, proj_dir,
                layout=layout, subtitles=with_subs,
                montage_plan=montage_plan,
                brand_name=_current_brand,
                smart_mix_cfg=_brand_smart_cfg,
                broll_mode=broll_mode,
            )
            size_mb = final_path.stat().st_size / 1024 / 1024

            # Auto-save as final_video.mp4 for crosspost
            import shutil
            final_video_path = proj_dir / "final_video.mp4"
            shutil.copy2(str(final_path), str(final_video_path))
            logger.info(f"[assembler] Saved as final_video.mp4 ({size_mb:.1f} MB)")

            # Себестоимость — логируем РЕАЛЬНЫЕ драйверы стоимости ролика (без
            # выдуманных ставок). Чтобы получить $: умножь на свои тарифы —
            # HeyGen $/сек (Avatar IV ~дороже III), ElevenLabs $/символ.
            # Claude-стоимость B-roll логируется отдельно в auto_broll.py.
            try:
                _av = sorted(proj_dir.glob("avatar_*.mp4"),
                             key=lambda f: f.stat().st_mtime, reverse=True)
                _av_sec = 0.0
                if _av:
                    _p = subprocess.run(
                        ["ffprobe", "-v", "quiet", "-show_entries",
                         "format=duration", "-of", "csv=p=0", str(_av[0])],
                        capture_output=True, text=True, timeout=10,
                    )
                    _av_sec = float(_p.stdout.strip() or 0)
                _vo_chars = len(script_text or "")
                _n_broll = len(list(proj_dir.glob("broll_*.mp4")))
                logger.info(
                    f"[cost] драйверы ролика «{card.get('title', '')[:40]}»: "
                    f"аватар {_av_sec:.0f}с | озвучка {_vo_chars} симв | "
                    f"B-roll {_n_broll} клип(ов) | финал {size_mb:.1f} МБ"
                )
            except Exception as _ce:
                logger.warning(f"[cost] не смог посчитать драйверы: {_ce}")

            # Auto-advance Notion status to "Готово к публикации" — карточка
            # больше не зависает на "Подбор скринкаст". Делаем безусловно после
            # успешной сборки финального видео (`final_video.mp4` создан).
            # 4 мая 2026: фикс по reportу Артёма — раньше статус застревал.
            try:
                _card_id_for_status = card.get("id")
                if _card_id_for_status:
                    await asyncio.to_thread(
                        update_notion_status, _card_id_for_status, "Готово к публикации",
                    )
                    logger.info(f"[status] {_card_id_for_status[:8]}... → Готово к публикации")
            except Exception as _e:
                logger.warning(f"[status] auto-advance to Готово к публикации failed: {_e}")

            # Save final video link to Notion card so that it can be downloaded
            # from Notion without opening the bot again. Uses media-permanent
            # (nginx-served). If copy fails — skip silently (not a blocker).
            try:
                notion_id = card.get("id")
                if notion_id:
                    final_url = await asyncio.to_thread(
                        save_media_permanent, str(final_video_path), "final_video"
                    )
                    final_caption = (
                        f"🎬 Финальный ролик ({layout_label}"
                        f"{' + субтитры' if with_subs else ''}, {size_mb:.1f} MB): "
                    )
                    await asyncio.to_thread(
                        notion.blocks.children.append,
                        block_id=notion_id,
                        children=[{
                            "object": "block",
                            "type": "paragraph",
                            "paragraph": {"rich_text": [
                                {"type": "text", "text": {"content": final_caption}},
                                {"type": "text", "text": {"content": final_url, "link": {"url": final_url}}},
                            ]},
                        }],
                    )
                    logger.info(f"[assembler] Final video link added to Notion: {final_url}")
            except Exception as e:
                logger.warning(f"[assembler] Failed to save final video link to Notion: {e}")

            # Billing — «download_final» is the earliest charge moment:
            # the bot handed the finished video to the client via chat.
            # Idempotent — subsequent crosspost or zip download calls get
            # 'already_charged'. Valid triggers: crosspost | download_final
            # | download_zip (see billing/api.py::VALID_TRIGGERS).
            await _billing_charge_if_needed(
                user_id, card.get("id"), trigger="download_final",
            )

            # Музыка прямо на финальном экране сборки — иначе пользователь
            # уйдёт в кросс-пост, не вспомнив про музыку (Артём 8 июня:
            # «звук либо пропустил, либо не наложился»). Корневая причина —
            # кнопки не было, она жила только в card_menu отдельным шагом.
            _music_btn = InlineKeyboardButton(
                music_button_label(proj_dir),
                callback_data=f"music_pick:{card['id'][:20]}",
            )
            buttons = [
                [InlineKeyboardButton("📢 Кросс-постинг", callback_data=f"crosspost:{card['id'][:20]}")],
                [_music_btn],
                [InlineKeyboardButton("🔄 Пересобрать", callback_data=f"card_assemble:{card['id'][:20]}")],
                [InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{card['id'][:20]}")],
            ]

            subs_note = " + субтитры" if with_subs else ""
            caption_text = (
                f"✅ Авто-ролик готов ({layout_label}{subs_note})\n"
                f"💾 Сохранён как готовый ролик ({size_mb:.1f} MB)\n\n"
                f"Нажми «📢 Кросс-постинг» для публикации "
                f"или «🔄 Пересобрать» для другого формата."
            )

            # 413-guard: бот Telegram отправляет максимум ~50 МБ. Tier 1 —
            # пробуем сжать (ffmpeg CRF 26); Tier 2 — если и сжатый >48 МБ,
            # шлём ссылку на nginx-media (бот её и так раздаёт). Оригинал
            # final_video.mp4 в проекте не трогаем.
            send_file = final_path
            _tg_compressed = None
            if size_mb > 48:
                _tg_compressed = proj_dir / "final_auto_tg.mp4"
                try:
                    await asyncio.to_thread(
                        subprocess.run,
                        ["ffmpeg", "-y", "-i", str(final_path),
                         "-c:v", "libx264", "-preset", "veryfast",
                         "-crf", "26", "-c:a", "aac", "-b:a", "128k",
                         "-movflags", "+faststart", str(_tg_compressed)],
                        capture_output=True, timeout=420,
                    )
                    if _tg_compressed.exists() and _tg_compressed.stat().st_size > 0:
                        _cmp_mb = _tg_compressed.stat().st_size / 1024 / 1024
                        logger.info(
                            f"[montage] сжал для Telegram: "
                            f"{size_mb:.1f} МБ → {_cmp_mb:.1f} МБ"
                        )
                        if _cmp_mb <= 48:
                            send_file = _tg_compressed
                except Exception as _ce:
                    logger.warning(f"[montage] компрессия не удалась: {_ce}")

            _send_mb = send_file.stat().st_size / 1024 / 1024
            sent_ok = False
            if _send_mb <= 48:
                try:
                    with open(send_file, "rb") as f:
                        await context.bot.send_video(
                            chat_id=query.message.chat_id,
                            video=f,
                            caption=caption_text,
                            supports_streaming=True,
                            reply_markup=InlineKeyboardMarkup(buttons),
                        )
                    sent_ok = True
                except Exception as _se:
                    logger.warning(f"[montage] send_video не прошёл: {_se}")

            if not sent_ok:
                # Tier 2 — публичная ссылка на nginx-media.
                _link = None
                try:
                    _link = await asyncio.to_thread(
                        save_media_permanent, str(final_path), "final_auto"
                    )
                except Exception as _le:
                    logger.warning(f"[montage] save_media_permanent: {_le}")
                tail = (
                    f" — открыть в браузере:\n{_link}" if _link
                    else " — забери из «📥 Скачать материалы»."
                )
                await query.message.reply_text(
                    f"{caption_text}\n\n"
                    f"⚠️ Файл крупный для Telegram (>{48} МБ){tail}",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )

            # Чистим временную сжатую копию.
            if _tg_compressed and _tg_compressed.exists():
                try:
                    _tg_compressed.unlink()
                except Exception:
                    pass
        except AssemblyError as e:
            logger.error(f"Auto-assemble failed: {e}")
            await query.edit_message_text(
                f"❌ Не удалось собрать ролик:\n\n{e}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{card['id'][:20]}")],
                ]),
            )
        except Exception as e:
            logger.error(f"Auto-assemble unexpected error: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Неожиданная ошибка: {e}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{card['id'][:20]}")],
                ]),
            )
        return

    if query.data.startswith("card_broll:") or query.data.startswith("card_voice:") or query.data.startswith("card_guide:") or query.data.startswith("card_avatar:") or query.data.startswith("card_cover:"):
        action = query.data.split(":")[0]
        card_id_prefix = query.data.split(":", 1)[1]

        all_cards = await asyncio.to_thread(fetch_notion_cards, limit=30)
        card = _pick_card_apply_brand(all_cards, card_id_prefix)
        if not card:
            await query.edit_message_text("Карточка не найдена.")
            return

        full_id = card["id"]
        await query.edit_message_text("📄 Загружаю сценарий из Notion...")
        logger.info(f"Loading script from Notion page: {full_id}")

        try:
            script_text = await asyncio.to_thread(fetch_notion_page_script, full_id)
            logger.info(f"Script loaded: {len(script_text) if script_text else 0} chars")
            if not script_text:
                await query.edit_message_text("В карточке нет сценария. Сначала добавь сценарий через бот.")
                return

            pending[user_id] = pending.get(user_id) or {}
            pending[user_id]["script"] = script_text
            pending[user_id]["notion_page_id"] = full_id
            pending[user_id]["notion_url"] = card["url"]
            pending[user_id]["card_data"] = {"title": card["title"]}

            # Restore voice data from card's voice directory if available
            voice_meta = _load_voice_meta(full_id)
            has_existing_voice = False
            if voice_meta:
                vparts = voice_meta.get("voice_parts", [])
                vapproved = voice_meta.get("voice_approved", [])
                vfiles = _get_voice_files(full_id, len(vparts))
                if vparts and any(f.exists() for f in vfiles):
                    pending[user_id]["voice_parts"] = vparts
                    pending[user_id]["voice_approved"] = vapproved
                    # Copy voice files to assets/ for compatibility
                    for i, vf in enumerate(vfiles):
                        if vf.exists():
                            import shutil
                            shutil.copy2(str(vf), str(ASSETS_DIR / f"voice_part_{i}.mp3"))
                    logger.info(f"Restored {len(vparts)} voice parts from card {full_id[:8]}")
                    has_existing_voice = True

            # If user clicked "Озвучить" and voice already exists — show the panel
            # instead of regenerating. User can then re-voice all or per-part from the panel.
            if action == "card_voice" and has_existing_voice:
                pending[user_id]["state"] = "voice_editing"
                _save_pending(pending)
                await query.edit_message_text(
                    "🎙 Озвучка уже есть. Можешь переозвучить части или настроить скорость/стиль:\n\n"
                    + _voice_panel_text(pending[user_id]).replace("🎙 Озвучка:\n\n", ""),
                    reply_markup=_voice_panel_keyboard(pending[user_id]),
                )
                return

            # Fetch source/YouTube URLs from Notion card's "Источники" block
            try:
                sources = await asyncio.to_thread(fetch_notion_page_sources, full_id)
                if sources.get("youtube_urls"):
                    pending[user_id]["youtube_urls"] = sources["youtube_urls"][:3]
                    logger.info(f"Found YouTube URLs in Notion card: {sources['youtube_urls'][:3]}")
                if sources.get("source_urls"):
                    pending[user_id]["source_urls"] = sources["source_urls"]
                    logger.info(f"Found source URLs in Notion card: {sources['source_urls']}")
            except Exception as e:
                logger.warning(f"Failed to fetch sources from Notion: {e}")

            _save_pending(pending)
            data = pending[user_id]

            # Развилка ДО TTS (только свежая озвучка; existing-voice выше уже
            # показал панель): выбрать ИИ-голос или свой голос, чтобы не гонять
            # ElevenLabs впустую (Артём 11 июня).
            if action == "card_voice":
                pending[user_id]["state"] = None
                _save_pending(pending)
                await query.edit_message_text(
                    _VOICEOVER_CHOICE_TEXT,
                    reply_markup=_voiceover_choice_keyboard(
                        back_cb=f"notion_card:{full_id[:20]}"
                    ),
                )
                return

            # Remap to standard action
            effective_action = {"card_broll": "broll", "card_voice": "voiceover", "card_guide": "create_guide", "card_avatar": "heygen_looks", "card_cover": "change_avatar"}[action]
            logger.info(f"Remapped {action} -> {effective_action}")
        except Exception as e:
            logger.error(f"Ошибка загрузки карточки: {e}", exc_info=True)
            await query.edit_message_text(f"Ошибка: {e}")
            return

    # Ignore noop buttons (value displays in sliders)
    if query.data == "noop":
        return

    # «Автосборка (нужен: …)» — кнопка-заглушка, когда не хватает аватара/B-roll.
    # Раньше она была noop без ответа → крутился мёртвый спиннер. Теперь —
    # внятный попап, что сделать, чтобы сборка ожила.
    if query.data == "asm_blocked":
        await query.answer(
            "Сначала сгенерируй аватар (или хотя бы подбери B-roll/фото) — "
            "после этого «Автосборка» заработает.",
            show_alert=True,
        )
        return

    # --- Publication tracking callbacks (manual entry flow) ---
    # Entry: "➕ Отметить публикацию" button under /calendar.
    if query.data == "pub_add":
        pending[user_id] = pending.get(user_id) or {}
        pending[user_id]["pub_draft"] = {}
        _save_pending(pending)
        await query.message.reply_text(
            "📅 Отметить публикацию за сегодня.\n\n"
            "Нажми на платформы, куда публиковался (повторный тап = +1). "
            "Затем «Сохранить».",
            reply_markup=_pub_picker_keyboard({}),
        )
        return

    # Checkbox tap — increment counter for this platform.
    if query.data.startswith("pub_toggle:"):
        code = query.data.split(":", 1)[1]
        if code not in PLATFORM_ORDER:
            return
        draft = (pending.get(user_id) or {}).get("pub_draft") or {}
        draft[code] = draft.get(code, 0) + 1
        # Cycle 4 → 0 so a user who overshoots can reset without cancelling.
        if draft[code] >= 5:
            draft[code] = 0
        pending.setdefault(user_id, {})["pub_draft"] = draft
        _save_pending(pending)
        try:
            await query.edit_message_reply_markup(
                reply_markup=_pub_picker_keyboard(draft)
            )
        except Exception:
            # "Message is not modified" — benign, user tapped same state.
            pass
        return

    # Save draft → pub_calendar.json.
    if query.data == "pub_save":
        draft = (pending.get(user_id) or {}).get("pub_draft") or {}
        # Drop zero-counts
        to_write = {c: n for c, n in draft.items() if n > 0}
        if not to_write:
            await query.answer("Нечего сохранять — выбери хотя бы одну.",
                               show_alert=True)
            return
        today = datetime.now().strftime("%Y-%m-%d")
        cal = _load_calendar()
        day_data = cal.get(today, {})
        for code, n in to_write.items():
            day_data[code] = day_data.get(code, 0) + n
        cal[today] = day_data
        _save_calendar(cal)

        summary = ", ".join(
            f"{c}" + (f"×{n}" if n > 1 else "") for c, n in to_write.items()
        )
        logger.info(f"[pub_save] {today} +{summary}")

        # Drop draft from pending.
        if user_id in pending and "pub_draft" in pending[user_id]:
            del pending[user_id]["pub_draft"]
            _save_pending(pending)

        # Replace the picker with a fresh calendar so Artem sees the result.
        grid = _format_calendar(days=7)
        await query.edit_message_text(
            f"✅ Записано на {today}: {summary}\n\n<pre>{grid}</pre>",
            parse_mode="HTML",
            reply_markup=_calendar_keyboard(),
        )
        return

    if query.data == "pub_cancel":
        if user_id in pending and "pub_draft" in pending[user_id]:
            del pending[user_id]["pub_draft"]
            _save_pending(pending)
        await query.edit_message_text("❌ Отменено.")
        return

    # --- Notion card management callbacks ---
    if query.data.startswith("notion_card:"):
        page_id = query.data.split(":", 1)[1]
        # Find full page_id from Notion (callback_data is truncated)
        try:
            all_cards = await asyncio.to_thread(fetch_notion_cards, limit=30)
            card = _pick_card_apply_brand(all_cards, page_id)
            if not card:
                await query.edit_message_text("Карточка не найдена.")
                return

            full_id = card["id"]
            # Store for follow-up actions
            pending[user_id] = pending.get(user_id) or {}
            pending[user_id]["notion_edit_card"] = full_id
            pending[user_id]["notion_edit_title"] = card["title"]
            _save_pending(pending)

            buttons = []

            # «▶️ Продолжить пайплайн» — главная навигационная кнопка карточки.
            # На каждом статусе ведёт к следующему логическому шагу:
            #   Идеи | старт       → подтянуть сценарий из Notion и показать «Утвердить»
            #   Сценарий | озвучка → озвучка (card_voice)
            #   Подбор скринкаст   → B-roll (card_broll)
            #   Аватар | генерации → аватар (card_avatar)
            #   Монтаж             → автосборка (card_assemble)
            #   Готово к публикации → кросс-постинг (crosspost)
            # Карточки без статуса / опубликованные / в архиве — кнопку не показываем.
            _pipeline_next_step = {
                "Идеи | старт":         ("▶️ Продолжить пайплайн",         f"card_continue:{full_id[:20]}"),
                "Сценарий | озвучка":   ("▶️ Продолжить: озвучка",         f"card_voice:{full_id[:20]}"),
                "Подбор скринкаст":     ("▶️ Продолжить: B-roll",          f"card_broll:{full_id[:20]}"),
                "Аватар | генерации":   ("▶️ Продолжить: аватар",          f"card_avatar:{full_id[:20]}"),
                "Монтаж":               ("▶️ Продолжить: сборка",          f"card_assemble:{full_id[:20]}"),
                "Готово к публикации":  ("▶️ Продолжить: публикация",      f"crosspost:{full_id[:20]}"),
            }
            _next_step = _pipeline_next_step.get(card["status"])
            if _next_step:
                buttons.append([InlineKeyboardButton(_next_step[0], callback_data=_next_step[1])])

            # Resolve project dir early — needed for B-roll count and other checks
            _tmp_data = {"notion_page_id": full_id, "card_data": {"title": card["title"]}}
            _tmp_proj = _project_dir(_tmp_data)

            # Action buttons — always available (any status)
            action_row = []
            if elevenlabs_client or (FISH_API_KEY and FISH_VOICE_ID):
                action_row.append(InlineKeyboardButton("🎙 Озвучить", callback_data=f"card_voice:{full_id[:20]}"))
            if PEXELS_API_KEY or PIXABAY_API_KEY:
                # Show saved B-roll count
                _broll_count = len(list(_tmp_proj.glob("broll_*.mp4"))) if _tmp_proj and _tmp_proj.exists() else 0
                broll_label = f"🎬 B-roll ({_broll_count})" if _broll_count else "🎬 B-roll"
                action_row.append(InlineKeyboardButton(broll_label, callback_data=f"card_broll:{full_id[:20]}"))
            if action_row:
                buttons.append(action_row)
            # Manage saved B-roll — show if clips exist
            if _tmp_proj and _tmp_proj.exists() and any(_tmp_proj.glob("broll_*.mp4")):
                _broll_n = len(list(_tmp_proj.glob("broll_*.mp4")))
                buttons.append([InlineKeyboardButton(f"📋 Управление B-roll ({_broll_n} клипов)", callback_data=f"broll_manage:{full_id[:20]}")])
            # Autonomous graphics — Claude на сервере рисует 6 графичных
            # B-roll-вставок прямо из сценария карточки. ДВА движка:
            # Remotion (React) и HyperFrames (HTML) — можно сравнить.
            buttons.append([InlineKeyboardButton(
                "🎨 Графика из сценария (Remotion)",
                callback_data=f"card_autobroll:{full_id[:20]}",
            )])
            buttons.append([InlineKeyboardButton(
                "🎨 Графика из сценария (HyperFrames)",
                callback_data=f"card_hfbroll:{full_id[:20]}",
            )])
            if NOTION_GUIDES_DB:
                buttons.append([InlineKeyboardButton("📎 Создать гайд", callback_data=f"card_guide:{full_id[:20]}")])
            if HEYGEN_API_KEY:
                buttons.append([InlineKeyboardButton("🤖 Сгенерировать аватар", callback_data=f"card_avatar:{full_id[:20]}")])
            buttons.append([InlineKeyboardButton("📜 Сценарий", callback_data=f"card_script:{full_id[:20]}")])
            # 26 May 2026: точка входа в Pipeline #6 (Карусель) из карточки.
            # Бот берёт сценарий карточки как seed для Opus, минует ввод темы.
            # Доступно ТОЛЬКО для бренда maksim — карусель пайплайн под него.
            if _get_active_brand_name() == "maksim":
                buttons.append([InlineKeyboardButton(
                    "🎨 В карусель",
                    callback_data=f"card_to_carousel:{full_id[:20]}",
                )])
            buttons.append([InlineKeyboardButton("📝 Описание для публикации", callback_data="gen_description")])
            buttons.append([InlineKeyboardButton("📰 TG-пост по сценарию", callback_data="tgpost_from_script")])
            buttons.append([InlineKeyboardButton("🖼 Сменить обложку", callback_data=f"card_cover:{full_id[:20]}")])

            # Check if project folder has files
            if _tmp_proj and _tmp_proj.exists() and any(_tmp_proj.iterdir()):
                buttons.append([InlineKeyboardButton("📥 Скачать материалы", callback_data="download_project")])

            # Check if final video exists
            _has_final = _tmp_proj and (_tmp_proj / "final_video.mp4").exists() if _tmp_proj else False
            final_label = "✅ Готовый ролик загружен" if _has_final else "📤 Загрузить готовый ролик"
            buttons.append([InlineKeyboardButton(final_label, callback_data=f"upload_final:{full_id[:20]}")])

            # Music mixing — available once there's any final video (uploaded or auto-assembled)
            _has_auto_final = _tmp_proj and (_tmp_proj / "final_auto.mp4").exists() if _tmp_proj else False
            if _has_final or _has_auto_final:
                _has_music_mix = _tmp_proj and (_tmp_proj / "final_video_with_music.mp4").exists() if _tmp_proj else False
                music_label = "🎵 Сменить музыку" if _has_music_mix else "🎵 Добавить музыку"
                buttons.append([InlineKeyboardButton(music_label, callback_data=f"music_pick:{full_id[:20]}")])

            # Auto-montage button — always visible, hint what's missing.
            # B-roll may come either from project video clips (broll_*.mp4) or
            # from the global photo library (broll-library/photos/**) — the
            # assembler auto-falls-back to Ken Burns photo clips when no video
            # clips are saved to the project folder.
            _has_avatar = bool(_tmp_proj and _tmp_proj.exists() and any(_tmp_proj.glob("avatar_*.mp4")))
            _has_video_broll = bool(_tmp_proj and _tmp_proj.exists() and any(_tmp_proj.glob("broll_*.mp4")))
            _photo_lib_count = len(_list_brand_photo_library())
            _has_photo_lib = _photo_lib_count > 0
            _has_broll = _has_video_broll or _has_photo_lib
            if _has_avatar and _has_broll:
                _has_auto = (_tmp_proj / "final_auto.mp4").exists()
                source_hint = "" if _has_video_broll else " 📸"
                if _has_auto:
                    auto_label = f"🎬 Пересобрать авто-ролик{source_hint}"
                else:
                    auto_label = f"🎬 Автосборка ролика{source_hint}"
                buttons.append([InlineKeyboardButton(auto_label, callback_data=f"card_assemble:{full_id[:20]}")])
            else:
                missing = []
                if not _has_avatar:
                    missing.append("аватар")
                if not _has_broll:
                    missing.append("B-roll или фото")
                buttons.append([InlineKeyboardButton(f"🎬 Автосборка (нужен: {', '.join(missing)})", callback_data="asm_blocked")])
            buttons.append([InlineKeyboardButton("📢 Кросс-постинг", callback_data=f"crosspost:{full_id[:20]}")])
            buttons.append([InlineKeyboardButton("📊 Сменить статус ▼", callback_data=f"card_statuses:{full_id[:20]}")])
            buttons.append([InlineKeyboardButton("📝 Добавить заметку", callback_data="notion_note")])
            buttons.append([InlineKeyboardButton("🔗 Открыть в Notion", url=card["url"])])
            buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="notion_back")])

            keyboard = InlineKeyboardMarkup(buttons)
            card_text = (
                f"📋 {card['title']}\n"
                f"📊 Статус: {card['status']}\n"
                f"🏷 Рубрика: {card['rubric']}\n\n"
                f"Выбери действие:"
            )
            try:
                await query.edit_message_text(card_text, reply_markup=keyboard)
            except Exception:
                # Video messages can't be edited — send as new message
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=card_text,
                    reply_markup=keyboard,
                )
        except Exception as e:
            logger.error(f"Ошибка notion_card: {e}", exc_info=True)
            try:
                await query.edit_message_text(f"Ошибка: {e}")
            except Exception:
                await context.bot.send_message(chat_id=query.message.chat_id, text=f"Ошибка: {e}")
        return

    if query.data.startswith("card_statuses:"):
        # Show full status list for this card
        card_id_prefix = query.data.split(":", 1)[1]
        all_cards = await asyncio.to_thread(fetch_notion_cards, limit=30)
        card = _pick_card_apply_brand(all_cards, card_id_prefix)
        if card:
            buttons = []
            for status in STATUSES:
                emoji = "✅" if status == card["status"] else "⬜"
                buttons.append([InlineKeyboardButton(
                    f"{emoji} {status}",
                    callback_data=f"notion_status:{status[:20]}"
                )])
            buttons.append([InlineKeyboardButton("◀️ Назад к карточке", callback_data=f"notion_card:{card_id_prefix}")])
            await query.edit_message_text(
                f"📋 {card['title']}\n📊 Текущий: {card['status']}\n\nВыбери новый статус:",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        return

    if query.data.startswith("notion_status:"):
        new_status = query.data.split(":", 1)[1]
        # Match full status name (callback was truncated)
        full_status = next((s for s in STATUSES if s.startswith(new_status)), None)
        if not full_status:
            await query.edit_message_text("Неизвестный статус.")
            return

        card_id = (pending.get(user_id) or {}).get("notion_edit_card")
        card_title = (pending.get(user_id) or {}).get("notion_edit_title", "")
        if not card_id:
            await query.edit_message_text("Карточка не найдена. Используй /cards.")
            return

        try:
            await asyncio.to_thread(update_notion_status, card_id, full_status)

            # Auto-record publication when status changes to "Опубликовано"
            pub_note = ""
            if full_status == "Опубликовано":
                try:
                    # Fetch card to get platforms
                    page = await asyncio.to_thread(notion.pages.retrieve, page_id=card_id)
                    platforms = [p["name"] for p in page["properties"].get("Площадки", {}).get("multi_select", [])]
                    if platforms:
                        _record_publication(platforms)
                        codes = [PLATFORM_CODES.get(p, p) for p in platforms if p in PLATFORM_CODES]
                        pub_note = f"\n📅 Записано в календарь: {', '.join(codes)}"
                except Exception as e:
                    logger.warning(f"Не удалось записать публикацию: {e}")

            await query.edit_message_text(
                f"✅ Статус обновлён!\n\n"
                f"📋 {card_title}\n"
                f"📊 {full_status}{pub_note}"
            )
        except Exception as e:
            logger.error(f"Ошибка смены статуса: {e}", exc_info=True)
            await query.edit_message_text(f"Ошибка: {e}")
        return

    if query.data == "notion_note":
        card_id = (pending.get(user_id) or {}).get("notion_edit_card")
        card_title = (pending.get(user_id) or {}).get("notion_edit_title", "")
        if not card_id:
            await query.edit_message_text("Карточка не найдена.")
            return

        pending[user_id]["state"] = "notion_note"
        _save_pending(pending)
        await query.edit_message_text(
            f"📝 Карточка: {card_title}\n\n"
            f"Напиши заметку текстом или голосовым — добавлю в карточку."
        )
        return

    if query.data == "notion_back":
        # Re-fetch and show cards list
        try:
            all_cards = await asyncio.to_thread(fetch_notion_cards, limit=30)
            active_cards = [c for c in all_cards if c["status"] != "Опубликовано"]

            by_status = {}
            for card in active_cards:
                status = card["status"] or "Без статуса"
                by_status.setdefault(status, []).append(card)

            text_parts = ["📋 Активные карточки:\n"]
            for status in STATUSES:
                if status in by_status and status != "Опубликовано":
                    text_parts.append(f"\n{'─' * 20}")
                    text_parts.append(f"📊 {status}:")
                    for card in by_status[status]:
                        rubric_tag = f" [{card['rubric']}]" if card['rubric'] else ""
                        text_parts.append(f"  • {card['title']}{rubric_tag}")

            buttons = []
            for card in active_cards[:10]:
                short_title = card["title"][:30]
                buttons.append([InlineKeyboardButton(
                    f"📝 {short_title}",
                    callback_data=f"notion_card:{card['id'][:30]}"
                )])

            keyboard = InlineKeyboardMarkup(buttons) if buttons else None
            await query.edit_message_text("\n".join(text_parts), reply_markup=keyboard)
        except Exception as e:
            logger.error(f"Ошибка notion_back: {e}", exc_info=True)
            await query.edit_message_text(f"Ошибка: {e}")
        return

    if query.data == "stats_skip":
        # Skip current platform in stats input (set to 0)
        if data and data.get("state") == "stats_input":
            step = data.get("stats_step", 0)
            if step < len(SOCIAL_ORDER):
                key = SOCIAL_ORDER[step]
                draft = data.get("stats_draft", {})
                draft[key] = {"subscribers": 0}
                data["stats_draft"] = draft
                data["stats_step"] = step + 1
                _save_pending(pending)
            await query.message.edit_reply_markup(reply_markup=None)
            await _ask_next_stat(query, context)
        return

    if query.data == "cancel":
        pending.pop(user_id, None)
        _save_pending(pending)
        # Photo messages не редактируются через edit_message_text — нужен
        # edit_message_caption. Если и это не сработало (text msg) — fallback
        # на удаление inline-клавиатуры + новое сообщение.
        cancelled_text = "❌ Отменено."
        try:
            await query.edit_message_text(cancelled_text)
        except Exception:
            try:
                await query.edit_message_caption(caption=cancelled_text)
            except Exception:
                try:
                    await query.edit_message_reply_markup(reply_markup=None)
                except Exception:
                    pass
                try:
                    await context.bot.send_message(chat_id=query.message.chat_id, text=cancelled_text)
                except Exception:
                    pass
        return

    # Selfie pipeline: user picks one of the proposed hook options as title
    if query.data.startswith("selfie_hook_pick:"):
        if not data or data.get("state") != "selfie_waiting_title":
            await query.edit_message_text("Данные устарели. Начни заново через /selfie.")
            return
        try:
            idx = int(query.data.split(":", 1)[1])
        except (ValueError, IndexError):
            await query.answer("Некорректный выбор")
            return
        hooks = data.get("selfie_hook_options") or []
        if idx < 0 or idx >= len(hooks):
            await query.answer("Вариант не найден")
            return
        title = hooks[idx]
        await query.edit_message_text(f"✅ Текст: «{title}»")
        # Наложить на обложку, если юзер выбрал «с текстом» (merged flow).
        await _apply_selfie_cover_text(context, user_id, query.message.chat_id, title)
        await context.bot.send_message(chat_id=query.message.chat_id, text="📋 Создаю карточку…")
        await _selfie_finalize(query, context, user_id, title)
        return

    # Selfie pipeline: regenerate 5 more hook options
    if query.data == "selfie_more_hooks":
        if not data or data.get("state") != "selfie_waiting_title":
            await query.edit_message_text("Данные устарели. Начни заново через /selfie.")
            return
        await query.edit_message_text("🔄 Перегенерирую…")
        transcript_text = data.get("selfie_transcript", "")
        shown = data.get("selfie_shown_hooks") or []
        hooks = await asyncio.to_thread(_generate_hook_options, transcript_text, shown)
        if not hooks:
            await query.edit_message_text(
                "Не удалось сгенерировать новые варианты. Попробуй "
                "✏️ Свой текстом или /selfie заново."
            )
            return
        # Update shown set so next round avoids these too
        data["selfie_hook_options"] = hooks
        data["selfie_shown_hooks"] = list({*shown, *hooks})
        _save_pending(pending)
        hooks_block = "\n".join(f"  • {h}" for h in hooks)
        await query.edit_message_text(
            f"🎣 Новые варианты:\n\n{hooks_block}\n\n"
            f"Или жми «🔄 Ещё варианты» / «✏️ Свой текстом».",
            reply_markup=_selfie_hook_keyboard(hooks),
        )
        return

    # Selfie pipeline: user wants to type own title
    if query.data == "selfie_own_title":
        if not data or data.get("state") != "selfie_waiting_title":
            await query.edit_message_text("Данные устарели. Начни заново через /selfie.")
            return
        # Same state — text input handler in process_idea picks it up
        await query.edit_message_text(
            "✏️ Напиши своё название текстом следующим сообщением.\n"
            "Это станет заголовком Notion-карточки."
        )
        return

    if data is None:
        await query.edit_message_text("Данные устарели. Отправь идею заново.")
        return

    if query.data == "edit_mode":
        data["state"] = "editing"
        _save_pending(pending)
        script_preview = data.get("script", "")
        await query.edit_message_text(
            f"📝 Текущий сценарий:\n\n"
            f"{script_preview}\n\n"
            f"———\n"
            f"✏️ Отправь правки текстом или голосовым. Что изменить?",

        )
        return

    if query.data == "rewrite":
        await query.edit_message_text("Переписываю сценарий...")
        # Re-generate with instruction to write differently
        try:
            _brand = _get_active_brand()
            _script_system = _brand_script_prompt(SCRIPT_PROMPT)
            response = claude.messages.create(
                model="claude-opus-4-7",
                max_tokens=1024,
                system=_script_system,
                messages=[
                    {"role": "user", "content": data["idea"]},
                    {"role": "assistant", "content": data["script"]},
                    {"role": "user", "content": "Перепиши сценарий. Другой хук, другой ритм, другая подача. Сохрани ту же идею."},
                ],
            )
            new_script = response.content[0].text.strip()
            if new_script.upper().startswith("СЦЕНАРИЙ"):
                new_script = new_script.split("\n", 1)[-1].strip()

            new_script = await _force_shorten(new_script)
            data["script"] = new_script
            _save_pending(pending)
            char_count = len(new_script)

            preview = (
                f"📝 СЦЕНАРИЙ (новый):\n\n"
                f"{new_script}\n\n"
                f"———\n"
                f"📊 {char_count} символов\n"
                    )

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("✅ Утвердить → обложка", callback_data="approve"),
                        InlineKeyboardButton("🔄 Ещё раз", callback_data="rewrite"),
                    ],
                    [
                        InlineKeyboardButton("✏️ Внести правки", callback_data="edit_mode"),
                        InlineKeyboardButton("✏️ Другой хук", callback_data="new_hook"),
                    ],
                    [InlineKeyboardButton("💾 Отложить как идею", callback_data="save_to_notion")],
                    [
                        InlineKeyboardButton("❌ Отмена", callback_data="cancel"),
                    ],
                ]
            )
            await query.edit_message_text(preview, reply_markup=keyboard)
        except Exception as e:
            logger.error(f"Ошибка: {e}", exc_info=True)
            await query.edit_message_text(f"Ошибка: {e}")
        return

    if query.data == "new_hook" or query.data == "more_hooks":
        await query.edit_message_text("Придумываю варианты хуков...")
        try:
            # Extract current hook (first line) and body (rest)
            script_lines = data["script"].strip().split("\n")
            current_hook = script_lines[0].strip()
            script_body = "\n".join(script_lines[1:]).strip()

            # Collect previously shown hooks to avoid repeats
            prev_hooks = data.get("shown_hooks", [])
            prev_hooks_text = ""
            if prev_hooks:
                prev_hooks_text = "\n\nУже предлагались (НЕ повторяй их):\n" + "\n".join(f"- {h}" for h in prev_hooks)

            response = claude.messages.create(
                model="claude-opus-4-7",
                max_tokens=500,
                system="Ты редактор хуков для коротких видео (Reels/Shorts/TikTok). Хук — это первая фраза сценария, которая останавливает скролл. Аудитория — предприниматели 30+, не программисты. Стиль: уверенный, жёсткий по смыслу, без воды. Запрещено: вступления, подводки, 'Люди тратят', 'Сегодня многие', 'Честно', 'Я думаю', 'Мне кажется', 'Давайте поговорим', 'В этом видео', 'Многие недооценивают'. Запрещены грубые слова ('жрёт', 'жрать' и т.п.).",
                messages=[
                    {"role": "user", "content": f"Вот сценарий:\n\n{data['script']}\n\nТекущий хук: «{current_hook}»\n\nПридумай 5 альтернативных хуков — мощных, цепляющих, с первого слова останавливающих скролл. Каждый хук — на новой строке, без нумерации, без кавычек, только текст.{prev_hooks_text}"},
                ],
            )
            hooks_text = response.content[0].text.strip()
            hooks = [line.strip().strip('"').strip("«»").strip("-").strip() for line in hooks_text.split("\n") if line.strip()]
            hooks = [h for h in hooks if len(h) > 5][:5]

            if not hooks:
                await query.edit_message_text("Не удалось сгенерировать хуки. Попробуй ещё раз.")
                return

            # Save hooks and body for later assembly
            data["hook_options"] = hooks
            data["script_body"] = script_body
            data["state"] = "hook_selection"
            # Track shown hooks to avoid repeats
            data.setdefault("shown_hooks", []).append(current_hook)
            data["shown_hooks"].extend(hooks)
            _save_pending(pending)

            buttons = [[InlineKeyboardButton(h, callback_data=f"hook_pick:{i}")] for i, h in enumerate(hooks)]
            buttons.append([InlineKeyboardButton("🔄 Ещё варианты", callback_data="more_hooks")])
            buttons.append([InlineKeyboardButton("⬅️ Оставить текущий", callback_data="keep_hook")])

            keyboard = InlineKeyboardMarkup(buttons)
            await query.edit_message_text(
                f"🎣 Текущий хук:\n«{current_hook}»\n\n"
                "Выбери новый хук или напиши свой:\n\n"
                + "\n".join(f"• {h}" for h in hooks)
                + "\n\nНажми на вариант или напиши свой текстом.",
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.error(f"Ошибка: {e}", exc_info=True)
            await query.edit_message_text(f"Ошибка: {e}")
        return

    if query.data.startswith("hook_pick:"):
        idx = int(query.data.split(":")[1])
        hooks = data.get("hook_options", [])
        if idx < len(hooks):
            chosen_hook = hooks[idx]
            script_body = data.get("script_body", "")
            new_script = chosen_hook + "\n" + script_body
            new_script = await _force_shorten(new_script)
            data["script"] = new_script
            data["state"] = None
            data.pop("hook_options", None)
            data.pop("script_body", None)
            data.pop("shown_hooks", None)
            _save_pending(pending)
            char_count = len(new_script)

            preview = (
                f"📝 СЦЕНАРИЙ (новый хук):\n\n"
                f"{new_script}\n\n"
                f"———\n"
                f"📊 {char_count} символов\n"
            )

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("✅ Утвердить → обложка", callback_data="approve"),
                        InlineKeyboardButton("🔄 Переписать", callback_data="rewrite"),
                    ],
                    [
                        InlineKeyboardButton("✏️ Внести правки", callback_data="edit_mode"),
                        InlineKeyboardButton("✏️ Другой хук", callback_data="new_hook"),
                    ],
                    [InlineKeyboardButton("💾 Отложить как идею", callback_data="save_to_notion")],
                    [
                        InlineKeyboardButton("❌ Отмена", callback_data="cancel"),
                    ],
                ]
            )
            await query.edit_message_text(preview, reply_markup=keyboard)
        return

    if query.data == "keep_hook":
        data["state"] = None
        data.pop("hook_options", None)
        data.pop("script_body", None)
        data.pop("shown_hooks", None)
        _save_pending(pending)
        script = data["script"]
        char_count = len(script)

        preview = (
            f"📝 СЦЕНАРИЙ:\n\n"
            f"{script}\n\n"
            f"———\n"
            f"📊 {char_count} символов\n"
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Утвердить → обложка", callback_data="approve"),
                    InlineKeyboardButton("🔄 Переписать", callback_data="rewrite"),
                ],
                [
                    InlineKeyboardButton("✏️ Внести правки", callback_data="edit_mode"),
                    InlineKeyboardButton("✏️ Другой хук", callback_data="new_hook"),
                ],
                [InlineKeyboardButton("💾 Отложить как идею", callback_data="save_to_notion")],
                [
                    InlineKeyboardButton("❌ Отмена", callback_data="cancel"),
                ],
            ]
        )
        await query.edit_message_text(preview, reply_markup=keyboard)
        return

    if effective_action == "voiceover":
        if not data or not data.get("script"):
            await query.edit_message_text("Нет сценария для озвучки.")
            return

        # Перед запуском озвучки — picker модели (v2 / v3). Дефолт-кнопка
        # подсвечена «●» из активного бренда. После генерации (или отмены)
        # voiceover_model чистится, чтобы следующий клик «Озвучить» снова
        # дал выбор.
        if not data.get("voiceover_model"):
            _b = _get_active_brand()
            _brand_default = _b.get("eleven_model_id") or "eleven_multilingual_v2"
            _is_v3_default = _brand_default.startswith("eleven_v3")
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "🎨 ElevenLabs v3 (Creative — голос Максима)"
                    + (" ●" if _is_v3_default else ""),
                    callback_data="voicepick:eleven_v3",
                )],
                [InlineKeyboardButton(
                    "🌊 ElevenLabs v2 (стандартно, ровно)"
                    + ("" if _is_v3_default else " ●"),
                    callback_data="voicepick:eleven_multilingual_v2",
                )],
                [InlineKeyboardButton("◀️ Отмена", callback_data="voice_back")],
            ])
            await query.edit_message_text(
                "🎙 Какой моделью озвучить сценарий?\n\n"
                "🎨 *v3 (Creative)* — выразительно, эмоции (дефолт maksim)\n"
                "🌊 *v2* — ровно, стандартно, проверенно\n\n"
                "После генерации на каждой части можно подкрутить настройки "
                "или сменить модель отдельно.",
                reply_markup=kb,
                parse_mode="Markdown",
            )
            return

        chosen_model = data["voiceover_model"]
        try:
            await _run_voiceover_generation(query, data, chosen_model)
        finally:
            data.pop("voiceover_model", None)
            _save_pending(pending)
        return

    # Picker: пользователь выбрал v2 или v3 в начале озвучки.
    if query.data.startswith("voicepick:"):
        if not data or not data.get("script"):
            await query.edit_message_text("Нет сценария для озвучки.")
            return
        chosen_model = query.data.split(":", 1)[1]
        data["voiceover_model"] = chosen_model
        _save_pending(pending)
        try:
            await _run_voiceover_generation(query, data, chosen_model)
        finally:
            data.pop("voiceover_model", None)
            _save_pending(pending)
        return

    if query.data.startswith("revoice:"):
        idx = int(query.data.split(":")[1])
        parts = data.get("voice_parts", [])
        if idx >= len(parts):
            await query.answer("Часть не найдена")
            return

        await query.edit_message_text(f"🎙 Переозвучиваю часть {idx+1}...")
        try:
            part_text = parts[idx]
            voice_path = str(ASSETS_DIR / f"voice_part_{idx}.mp3")
            await asyncio.to_thread(generate_voiceover, part_text, voice_path)

            # Save voice file to card directory and project folder
            notion_id = data.get("notion_page_id")
            if notion_id:
                _save_voice_file(notion_id, idx, voice_path)
            _save_to_project(data, f"voice_part_{idx}.mp3", voice_path)

            # Reset approval for this part
            data.get("voice_approved", [])[idx] = False
            _save_pending(pending)

            with open(voice_path, "rb") as audio_file:
                await query.get_bot().send_audio(
                    chat_id=query.message.chat_id,
                    audio=audio_file,
                    title=f"Часть {idx+1}/{len(parts)} (новая)",
                    caption=f"🎙 Часть {idx+1}/{len(parts)} (переозвучена):\n\n«{part_text}»",
                )

            await query.edit_message_text(
                _voice_panel_text(data),
                reply_markup=_voice_panel_keyboard(data),
            )
        except Exception as e:
            logger.error(f"Ошибка переозвучки: {e}", exc_info=True)
            await query.edit_message_text(f"Ошибка: {e}")
        return

    # Approve individual voice part
    if query.data.startswith("voice_ok:"):
        idx = int(query.data.split(":")[1])
        approved = data.get("voice_approved", [])
        if idx < len(approved):
            approved[idx] = True
            data["voice_approved"] = approved
            _save_pending(pending)

            # Update voice meta on card directory
            notion_id = data.get("notion_page_id")
            if notion_id:
                _save_voice_meta(notion_id, data.get("voice_parts", []), approved)

            # Check if all approved
            if all(approved):
                # Save voice links to Notion card
                if notion_id:
                    try:
                        voice_blocks = []
                        parts = data.get("voice_parts", [])
                        voice_files = _get_voice_files(notion_id, len(parts))
                        voice_urls = []
                        for i, vf in enumerate(voice_files):
                            if vf.exists():
                                url = save_media_permanent(str(vf), f"voice_{i}")
                                voice_urls.append(url)
                                voice_blocks.append({
                                    "object": "block",
                                    "type": "paragraph",
                                    "paragraph": {"rich_text": [
                                        {"type": "text", "text": {"content": f"🎙 Часть {i+1}: ", "link": None}},
                                        {"type": "text", "text": {"content": url, "link": {"url": url}}},
                                    ]},
                                })
                        if voice_blocks:
                            notion.blocks.children.append(
                                block_id=notion_id,
                                children=[{
                                    "object": "block",
                                    "type": "toggle",
                                    "toggle": {
                                        "rich_text": [{"type": "text", "text": {"content": "🎙 Озвучка (ElevenLabs)"}}],
                                        "children": voice_blocks,
                                    },
                                }],
                            )
                    except Exception as e:
                        logger.warning(f"Failed to save voice to Notion: {e}")

                voice_done_buttons = []
                if HEYGEN_API_KEY:
                    voice_done_buttons.append([InlineKeyboardButton("🤖 Сгенерировать аватар", callback_data="heygen_looks")])
                if NOTION_GUIDES_DB and not data.get("guide_created"):
                    voice_done_buttons.append([InlineKeyboardButton("📎 Создать гайд для подписчиков", callback_data="create_guide")])
                if not data.get("broll_approved"):
                    voice_done_buttons.append([InlineKeyboardButton("🎬 Подобрать B-roll", callback_data="broll")])
                voice_done_buttons.append([InlineKeyboardButton("📝 Описание для публикации", callback_data="gen_description")])
                voice_done_buttons.append([InlineKeyboardButton("📰 TG-пост по сценарию", callback_data="tgpost_from_script")])
                voice_done_buttons.append([InlineKeyboardButton("✅ Готово", callback_data="finish")])
                await query.edit_message_text(
                    "✅ Все части утверждены!\n\n"
                    + "\n".join(f"Часть {i+1}: «{p[:60]}{'...' if len(p) > 60 else ''}»" for i, p in enumerate(data.get("voice_parts", [])))
                    + "\n\nОзвучка готова к монтажу.",
                    reply_markup=InlineKeyboardMarkup(voice_done_buttons),
                )
            else:
                await query.edit_message_text(
                    _voice_panel_text(data),
                    reply_markup=_voice_panel_keyboard(data),
                )
        return

    # Show settings sliders for a specific part
    if query.data.startswith("voice_cfg:"):
        idx = int(query.data.split(":")[1])
        parts = data.get("voice_parts", [])
        if idx < len(parts):
            part_settings = data.setdefault("part_settings", {})
            ps = part_settings.get(str(idx)) or _voice_part_ps_default()
            # Бэкфилл model — старые ps без поля model берут из активного бренда.
            if "model" not in ps:
                ps["model"] = _get_active_brand().get(
                    "eleven_model_id", "eleven_multilingual_v2"
                )
            part_settings[str(idx)] = ps
            _save_pending(pending)
            await query.edit_message_text(
                _voice_settings_text(idx, ps),
                reply_markup=_voice_settings_keyboard(idx, ps),
            )
        return

    # Adjust voice setting slider (➖/➕)
    if query.data.startswith("vadj:"):
        # Format: vadj:{idx}:{param}:{direction +/-}
        _, idx_s, param, direction = query.data.split(":")
        idx = int(idx_s)

        part_settings = data.setdefault("part_settings", {})
        ps = part_settings.get(str(idx)) or _voice_part_ps_default()
        if "model" not in ps:
            ps["model"] = _get_active_brand().get(
                "eleven_model_id", "eleven_multilingual_v2"
            )

        # На v3 параметр style не имеет смысла (v3 не использует) — игнорируем.
        if ps["model"].startswith("eleven_v3") and param == "st":
            await query.answer("style не используется на eleven_v3")
            return

        steps = {"sp": 0.05, "st": 0.01, "sb": 0.05, "sm": 0.05}
        mins = {"sp": 0.7, "st": 0.0, "sb": 0.0, "sm": 0.0}
        maxs = {"sp": 1.5, "st": 1.0, "sb": 1.0, "sm": 1.0}

        delta = steps[param] if direction == "+" else -steps[param]
        new_val = round(ps[param] + delta, 2)
        new_val = max(mins[param], min(maxs[param], new_val))
        ps[param] = new_val
        part_settings[str(idx)] = ps
        _save_pending(pending)

        await query.edit_message_text(
            _voice_settings_text(idx, ps),
            reply_markup=_voice_settings_keyboard(idx, ps),
        )
        return

    # Toggle model v2 ↔ v3 for this part
    if query.data.startswith("vmodel:"):
        idx = int(query.data.split(":")[1])
        part_settings = data.setdefault("part_settings", {})
        ps = part_settings.get(str(idx)) or _voice_part_ps_default()
        current = ps.get("model") or _get_active_brand().get(
            "eleven_model_id", "eleven_multilingual_v2"
        )
        if current.startswith("eleven_v3"):
            # v3 → v2: вернуть исторические дефолты v2-ползунков.
            ps["model"] = "eleven_multilingual_v2"
            ps["sb"] = VOICE_SETTINGS["stability"]
            ps["sm"] = VOICE_SETTINGS["similarity_boost"]
            ps["st"] = VOICE_SETTINGS["style"]
            ps["sp"] = VOICE_SETTINGS["speed"]
        else:
            # v2 → v3: дефолт = Creative.
            ps["model"] = "eleven_v3"
            ps["sb"] = 0.0
            ps["sm"] = 0.75
            ps["st"] = 0.0
            ps["sp"] = 1.0
        part_settings[str(idx)] = ps
        _save_pending(pending)
        await query.edit_message_text(
            _voice_settings_text(idx, ps),
            reply_markup=_voice_settings_keyboard(idx, ps),
        )
        return

    # Set v3 stability preset (Creative / Natural / Robust)
    if query.data.startswith("vstab:"):
        _, idx_s, preset = query.data.split(":")
        idx = int(idx_s)
        preset_map = {"creative": 0.0, "natural": 0.5, "robust": 1.0}
        if preset not in preset_map:
            await query.answer("Неизвестный пресет")
            return
        part_settings = data.setdefault("part_settings", {})
        ps = part_settings.get(str(idx)) or _voice_part_ps_default()
        ps["model"] = "eleven_v3"  # пресет — только для v3
        ps["sb"] = preset_map[preset]
        part_settings[str(idx)] = ps
        _save_pending(pending)
        await query.edit_message_text(
            _voice_settings_text(idx, ps),
            reply_markup=_voice_settings_keyboard(idx, ps),
        )
        return

    # Generate with custom settings
    if query.data.startswith("vgen:"):
        idx = int(query.data.split(":")[1])
        parts = data.get("voice_parts", [])
        if idx >= len(parts):
            await query.answer("Часть не найдена")
            return

        part_settings = data.get("part_settings", {})
        ps = part_settings.get(str(idx)) or _voice_part_ps_default()
        # Бэкфилл model — старые ps без поля model берут модель активного бренда.
        if "model" not in ps:
            ps["model"] = _get_active_brand().get(
                "eleven_model_id", "eleven_multilingual_v2"
            )

        # Brand overrides (voice_id берём из бренда; model — из ps, чтобы
        # юзер мог переключить v2 ↔ v3 прямо для этой части).
        _brand = _get_active_brand()
        _voice_id = _brand.get("eleven_voice_id") or ELEVENLABS_VOICE_ID
        _model_id = ps["model"]
        _is_v3 = _model_id.startswith("eleven_v3")
        if _is_v3:
            sb = ps["sb"]
            preset = "Creative" if sb < 0.2 else ("Natural" if sb < 0.7 else "Robust")
            settings_str = f"v3 {preset} sb={sb} sp={ps['sp']}"
        else:
            settings_str = f"v2 sp={ps['sp']} st={ps['st']} sb={ps['sb']}"
        await query.edit_message_text(f"🎙 Озвучиваю часть {idx+1} ({settings_str})...")
        try:
            part_text = parts[idx]
            voice_path = str(ASSETS_DIR / f"voice_part_{idx}.mp3")

            from elevenlabs import VoiceSettings
            tts_text = transliterate_for_tts(part_text, model_id=_model_id)
            # На v3 style игнорируется API; передаём 0 чтобы не было «штрафа».
            _style = 0.0 if _is_v3 else ps["st"]
            custom_settings = VoiceSettings(
                stability=ps["sb"],
                similarity_boost=ps["sm"],
                style=_style,
                speed=ps["sp"],
            )

            audio_generator = elevenlabs_client.text_to_speech.convert(
                voice_id=_voice_id,
                text=tts_text,
                model_id=_model_id,
                voice_settings=custom_settings,
                output_format="mp3_44100_128",
            )
            with open(voice_path, "wb") as f:
                for chunk in audio_generator:
                    f.write(chunk)

            data.get("voice_approved", [])[idx] = False
            _save_pending(pending)

            with open(voice_path, "rb") as audio_file:
                await query.get_bot().send_audio(
                    chat_id=query.message.chat_id,
                    audio=audio_file,
                    title=f"Часть {idx+1}/{len(parts)} ({settings_str})",
                    caption=f"🎙 Часть {idx+1} ({settings_str}):\n\n«{part_text}»",
                )

            await query.edit_message_text(
                _voice_panel_text(data),
                reply_markup=_voice_panel_keyboard(data),
            )
        except Exception as e:
            logger.error(f"Ошибка: {e}", exc_info=True)
            await query.edit_message_text(f"Ошибка: {e}")
        return

    # Approve from inside the settings panel — marks the part as approved
    # and returns to the main voice panel. This mirrors ``voice_ok:`` but is
    # reachable without having to click "⬅️ Назад" first (shortens the path
    # when the user has just re-voiced with tweaked settings and liked it).
    if query.data.startswith("vsok:"):
        idx = int(query.data.split(":")[1])
        approved = data.get("voice_approved", [])
        parts = data.get("voice_parts", [])
        # Pad approved list if it somehow got out of sync with parts.
        if len(approved) < len(parts):
            approved = list(approved) + [False] * (len(parts) - len(approved))
        if 0 <= idx < len(approved):
            approved[idx] = True
            data["voice_approved"] = approved
            _save_pending(pending)
            notion_id = data.get("notion_page_id")
            if notion_id:
                _save_voice_meta(notion_id, parts, approved)

        # Redirect into the main voice_ok flow so we share the "all approved →
        # commit to Notion + show next-step buttons" branch. We do this by
        # re-building the same message the voice_ok handler builds.
        if all(approved) and parts:
            voice_done_buttons = []
            if HEYGEN_API_KEY:
                voice_done_buttons.append([InlineKeyboardButton("🤖 Сгенерировать аватар", callback_data="heygen_looks")])
            if NOTION_GUIDES_DB and not data.get("guide_created"):
                voice_done_buttons.append([InlineKeyboardButton("📎 Создать гайд для подписчиков", callback_data="create_guide")])
            if not data.get("broll_approved"):
                voice_done_buttons.append([InlineKeyboardButton("🎬 Подобрать B-roll", callback_data="broll")])
            voice_done_buttons.append([InlineKeyboardButton("📝 Описание для публикации", callback_data="gen_description")])
            voice_done_buttons.append([InlineKeyboardButton("📰 TG-пост по сценарию", callback_data="tgpost_from_script")])
            voice_done_buttons.append([InlineKeyboardButton("✅ Готово", callback_data="finish")])
            await query.edit_message_text(
                "✅ Все части утверждены!\n\n"
                + "\n".join(
                    f"Часть {i+1}: «{p[:60]}{'...' if len(p) > 60 else ''}»"
                    for i, p in enumerate(parts)
                )
                + "\n\nОзвучка готова к монтажу.",
                reply_markup=InlineKeyboardMarkup(voice_done_buttons),
            )
        else:
            await query.edit_message_text(
                _voice_panel_text(data),
                reply_markup=_voice_panel_keyboard(data),
            )
        return

    # Back from settings to main voice panel
    # Edit text of a voice part — show current text and ask for new
    if query.data.startswith("vedit:"):
        idx = int(query.data.split(":")[1])
        parts = data.get("voice_parts", [])
        if idx < len(parts):
            data["voice_edit_part"] = idx
            data["state"] = "voice_text_edit"
            _save_pending(pending)

            await query.edit_message_text(
                f"✏️ Редактирование части {idx+1}:\n\n"
                f"Текущий текст:\n«{parts[idx]}»\n\n"
                f"Отправь исправленный текст сообщением.\n"
                f"Скопируй текст выше, измени нужное слово и отправь.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Отмена", callback_data="voice_back")],
                ]),
            )
        return

    if query.data == "voice_back":
        data["state"] = "voice_editing"
        data.pop("voice_edit_part", None)
        _save_pending(pending)
        await query.edit_message_text(
            _voice_panel_text(data),
            reply_markup=_voice_panel_keyboard(data),
        )
        return

    # (card_* remap block moved to top of handle_callback)

    if effective_action == "broll":
        script_text = data.get("script", "")
        yt_urls = data.get("youtube_urls", [])

        # If we have YouTube URLs from the article — cut clips from them first
        if yt_urls:
            await _send_or_edit(
                query, context,
                f"🎬 Нашёл {len(yt_urls)} YouTube-ссылок в статье.\n"
                f"Скачиваю и нарезаю клипы...\n\n"
                f"🔗 {chr(10).join(yt_urls[:2])}"
            )
            try:
                all_clips = []
                for yt_url in yt_urls[:2]:  # Max 2 videos
                    clips = await asyncio.to_thread(download_and_cut_youtube, yt_url, 5, 8)
                    all_clips.extend(clips)

                if all_clips:
                    data["broll_clips"] = [
                        {"id": f"yt_{i}", "source": "youtube", "path": c["path"],
                         "filename": Path(c["path"]).name, "url": yt_urls[0]}
                        for i, c in enumerate(all_clips)
                    ]
                    data["broll_selected"] = []
                    _save_pending(pending)

                    await _send_or_edit(
                        query, context,
                        f"📊 Нарезано {len(all_clips)} клипов из YouTube — выбери подходящие"
                    )

                    for idx, clip in enumerate(all_clips):
                        try:
                            select_btn = InlineKeyboardMarkup([
                                [InlineKeyboardButton(f"✅ Выбрать #{idx+1}", callback_data=f"broll_select:{idx}")]
                            ])
                            clip_path = Path(clip["path"])
                            if clip_path.exists():
                                with open(clip_path, "rb") as f:
                                    await context.bot.send_video(
                                        chat_id=query.message.chat_id,
                                        video=f,
                                        caption=f"#{idx+1} | YouTube: {clip_path.name}",
                                        supports_streaming=True,
                                        reply_markup=select_btn,
                                    )
                        except Exception as e:
                            logger.warning(f"Failed to send YouTube clip #{idx+1}: {e}")

                    buttons = [
                        [InlineKeyboardButton("💾 Сохранить выбранные в Notion", callback_data="broll_approve")],
                        [InlineKeyboardButton("🔄 Подобрать со стоков", callback_data="broll_stock")],
                        [InlineKeyboardButton("🎬 Нарезать из другого видео", callback_data="broll_youtube")],
                    ]
                    if elevenlabs_client and not data.get("voice_parts"):
                        buttons.append([InlineKeyboardButton("🎙 Озвучить", callback_data="voiceover_choose")])
                    buttons.append([InlineKeyboardButton("✅ Готово", callback_data="finish")])

                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text="Нажми «Выбрать» под понравившимися клипами, затем «Сохранить».",
                        reply_markup=InlineKeyboardMarkup(buttons),
                    )
                    return
                else:
                    logger.warning("YouTube cutting returned 0 clips")
            except Exception as e:
                logger.warning(f"YouTube cutting failed: {e}")
                yt_error = str(e)

            # YouTube failed — show menu with options
            if not data.get("broll_clips"):
                buttons = [
                    [InlineKeyboardButton("🎬 Скинуть другую ссылку", callback_data="broll_youtube")],
                ]
                if _get_active_brand_name() != "maksim":
                    buttons.append([InlineKeyboardButton("🔍 Искать на стоках", callback_data="broll_stock")])
                buttons.append([InlineKeyboardButton("✅ Готово", callback_data="finish")])
                error_text = f"⚠️ Не удалось скачать YouTube-видео"
                if 'yt_error' in dir():
                    error_text += f": {yt_error}"
                error_text += "\n\nСкинь другую ссылку или поищу на стоках."
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=error_text,
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
                return

        # No YouTube URLs found — ask user before going to stock
        if not yt_urls or not data.get("_skip_yt_ask"):
            if not data.get("_skip_yt_ask"):
                # First time — offer to send YouTube link manually
                data["state"] = "broll_youtube_or_stock"
                _save_pending(pending)
                _brand_now = _get_active_brand_name()
                _photo_count = len(_list_brand_photo_library(_brand_now))
                _card_id_for_autobroll = data.get("notion_page_id", "")[:20]
                buttons = []
                # «🎨 Графика из сценария» — автономный Remotion-B-roll
                # через Claude Code на сервере. Раньше кнопка жила только в
                # меню карточки; пользователь её не видел в B-roll-flow и
                # вынужден был выходить «Готово → главное меню». Дублируем
                # сюда — самый частый путь для роликов Максима. Показываем,
                # если карточка известна и есть сценарий (иначе нечего
                # анализировать).
                if _card_id_for_autobroll and data.get("script"):
                    buttons.append([InlineKeyboardButton(
                        "🎨 Графика из сценария (Remotion)",
                        callback_data=f"card_autobroll:{_card_id_for_autobroll}",
                    )])
                    buttons.append([InlineKeyboardButton(
                        "🎨 Графика из сценария (HyperFrames)",
                        callback_data=f"card_hfbroll:{_card_id_for_autobroll}",
                    )])
                buttons.extend([
                    [InlineKeyboardButton("📥 Готовые материалы (фото + видео)", callback_data="broll_ready")],
                    [InlineKeyboardButton("📚 Моя библиотека клипов", callback_data="broll_local_lib")],
                ])
                if _photo_count > 0:
                    buttons.append([
                        InlineKeyboardButton(
                            f"📸 Фото-библиотека ({_photo_count})",
                            callback_data="broll_photo_lib",
                        )
                    ])
                buttons.extend([
                    [InlineKeyboardButton("📋 Список съёмки (личный B-roll)", callback_data="broll_shooting_list")],
                    [InlineKeyboardButton("🔍 Найти B-roll на YouTube", callback_data="broll_yt_search")],
                    # «🐦 Видео из твита» удалена 20 мая 2026 — дублировала
                    # следующую кнопку (одинаковый callback broll_youtube),
                    # Twitter вне стратегии Максима.
                    [InlineKeyboardButton("🎬 Скинуть видео для нарезки", callback_data="broll_youtube")],
                ])
                # Стоки (Pexels/Pixabay) не подходят «реальному бизнесу»
                # Максима — скрываем для бренда maksim. Локальная библиотека
                # + YouTube-поиск покрывают сценарии.
                if _brand_now != "maksim":
                    buttons.append([InlineKeyboardButton("🔍 Искать на стоках", callback_data="broll_stock")])
                if elevenlabs_client and not data.get("voice_parts"):
                    buttons.append([InlineKeyboardButton("🎙 Озвучить", callback_data="voiceover_choose")])
                buttons.append([InlineKeyboardButton("✅ Готово", callback_data="finish")])

                await _send_or_edit(
                    query, context,
                    "🎬 Выбери способ подбора видеоряда:",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
                return

        # Stock B-roll search
        await _send_or_edit(query, context, "🎬 Ищу подходящие видео на стоках...")
        try:
            shotlist = await asyncio.to_thread(generate_shotlist, script_text)
            shotlist = await find_broll_for_shotlist(shotlist)

            data["shotlist"] = shotlist
            _save_pending(pending)

            # Collect all B-roll clips, deduplicate, limit total to ~10
            all_broll_videos = []
            seen_clip_ids = set()
            for shot in shotlist:
                for v in shot.get("videos", []):
                    if v["id"] not in seen_clip_ids:
                        seen_clip_ids.add(v["id"])
                        all_broll_videos.append(v)

            # Store clips with indices for selection
            data["broll_clips"] = all_broll_videos
            data["broll_selected"] = []
            _save_pending(pending)

            await query.edit_message_text(
                f"📊 Найдено {len(all_broll_videos)} клипов — выбери подходящие кнопкой «Выбрать»",
            )

            # Send each clip with a "Select" button
            for idx, v in enumerate(all_broll_videos):
                try:
                    select_btn = InlineKeyboardMarkup([
                        [InlineKeyboardButton(f"✅ Выбрать #{idx+1}", callback_data=f"broll_select:{idx}")]
                    ])
                    if v.get("source") == "local":
                        clip_path = Path(v["path"])
                        if clip_path.exists():
                            with open(clip_path, "rb") as f:
                                await context.bot.send_video(
                                    chat_id=query.message.chat_id,
                                    video=f,
                                    caption=f"#{idx+1} | {v.get('category', '')}: {v['filename']}",
                                    supports_streaming=True,
                                    reply_markup=select_btn,
                                )
                    else:
                        await context.bot.send_message(
                            chat_id=query.message.chat_id,
                            text=f"#{idx+1} | [{v['source']}] {v.get('tags', '')}\n{v['url']}",
                            disable_web_page_preview=False,
                            reply_markup=select_btn,
                        )
                except Exception as e:
                    logger.warning(f"Failed to send clip #{idx+1}: {e}")

            # Final message with save button
            buttons = [
                [InlineKeyboardButton("💾 Сохранить выбранные в Notion", callback_data="broll_approve")],
                [InlineKeyboardButton("🔍 Найти B-roll на YouTube", callback_data="broll_yt_search")],
                [InlineKeyboardButton("🎬 Нарезать из видео", callback_data="broll_youtube")],
                [InlineKeyboardButton("🔄 Подобрать другие", callback_data="broll")],
            ]
            if elevenlabs_client and not data.get("voice_parts"):
                buttons.append([InlineKeyboardButton("🎙 Озвучить", callback_data="voiceover_choose")])
            buttons.append([InlineKeyboardButton("✅ Готово", callback_data="finish")])

            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="Нажми «Выбрать» под понравившимися клипами, затем «Сохранить».\n\n🎬 Или нарежь клипы из YouTube-видео.",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception as e:
            logger.error(f"Ошибка B-roll: {e}", exc_info=True)
            buttons = [
                [InlineKeyboardButton("🎬 Попробовать снова", callback_data="broll")],
                [InlineKeyboardButton("✅ Готово", callback_data="finish")],
            ]
            await query.edit_message_text(
                f"Ошибка поиска B-roll: {e}",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        return

    if query.data.startswith("broll_select:"):
        idx = int(query.data.split(":")[1])
        selected = data.get("broll_selected", [])
        clips = data.get("broll_clips", [])
        if idx < len(clips):
            if idx in selected:
                # Already selected — deselect
                selected.remove(idx)
                data["broll_selected"] = selected
                _save_pending(pending)
                btn = InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"✅ Выбрать #{idx+1}", callback_data=f"broll_select:{idx}")]
                ])
                try:
                    await query.edit_message_reply_markup(reply_markup=btn)
                except Exception:
                    pass
                await query.answer(f"❌ Клип #{idx+1} убран")
            else:
                # Select
                selected.append(idx)
                data["broll_selected"] = selected
                _save_pending(pending)
                btn = InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"✅ Выбран #{idx+1} ✓", callback_data=f"broll_select:{idx}")]
                ])
                try:
                    await query.edit_message_reply_markup(reply_markup=btn)
                except Exception:
                    pass
                await query.answer(f"✅ Клип #{idx+1} выбран ({len(selected)} всего)")

            # Live-update footer-сообщения категории (если сохранили id) —
            # счётчик «✅ Выбрано всего: N клипов» в актуальном состоянии.
            footer_msg_id = data.get("broll_lib_footer_msg_id")
            footer_chat_id = data.get("broll_lib_footer_chat_id")
            footer_category = data.get("broll_lib_footer_category")
            if footer_msg_id and footer_chat_id and footer_category:
                n_total = len(selected)
                new_text = (
                    "Нажми «Выбрать» под нужными, потом «Сохранить».\n\n"
                    f"✅ Выбрано всего: *{n_total}* клипов"
                    if n_total else
                    "Нажми «Выбрать» под нужными, потом «Сохранить»."
                )
                footer_buttons = InlineKeyboardMarkup([
                    [InlineKeyboardButton("💾 Сохранить выбранные в Notion", callback_data="broll_approve")],
                    [InlineKeyboardButton("📚 Другая категория", callback_data="broll_local_lib")],
                    [InlineKeyboardButton("🔀 Обновить выборку", callback_data=f"broll_lib_cat:{footer_category}")],
                    [InlineKeyboardButton("◀️ К меню B-roll", callback_data="broll")],
                ])
                try:
                    await context.bot.edit_message_text(
                        chat_id=footer_chat_id,
                        message_id=footer_msg_id,
                        text=new_text,
                        parse_mode="Markdown",
                        reply_markup=footer_buttons,
                    )
                except Exception as e:
                    # Footer мог быть удалён, устарел или is identical — не критично.
                    logger.debug(f"[broll_select] footer edit skipped: {e}")
        return

    # --- Cross-posting (direct API) ---
    if query.data.startswith("crosspost:"):
        card_id_prefix = query.data.split(":", 1)[1]
        # Reset previous selection when opening the menu for a different card
        # (otherwise ticks from the previous card's crosspost leak over).
        prev_card = data.get("crosspost_card_id")
        if prev_card != card_id_prefix:
            data.pop("crosspost_selected", None)
            # Codex review #3 P1 (9 May 2026): clear TG-photo state when
            # switching to a different card. Otherwise photos selected for
            # card A could silently attach to card B and be published with
            # the wrong post — checklist counts would still show but a
            # contentmismatch is hard to spot before crosspost_go.
            data.pop("selfie_tg_photos", None)
            data.pop("tgphoto_browse_state", None)
            data.pop("tgphoto_awaiting_reply", None)
        data["crosspost_card_id"] = card_id_prefix

        # Billing — charge on FIRST crosspost attempt (idempotent; already
        # debited if download_final or download_zip fired before).
        # We need the full Notion page id, not the 20-char prefix — resolve
        # through pending if possible, else fall back to prefix (the charge
        # will 404 with video_not_found but won't crash).
        _full_video_id = data.get("notion_page_id") or card_id_prefix
        await _billing_charge_if_needed(
            user_id, _full_video_id, trigger="crosspost",
        )

        # Get available platforms
        platforms = get_available_platforms()
        selected = data.get("crosspost_selected")
        # First open of the menu for this card — pre-select all connected platforms
        # so Artem doesn't have to tick every time.  He can still deselect before publish.
        if selected is None:
            selected = [p["id"] for p in platforms if p.get("connected")]
            data["crosspost_selected"] = selected
            _save_pending(pending)

        # ── Pre-publish checklist ──
        video_path = _find_video_for_card(data)
        script_text = data.get("script", "")
        if not script_text:
            proj = _project_dir(data) or _project_dir_by_prefix(card_id_prefix)
            if proj and (proj / "script.txt").exists():
                try:
                    script_text = (proj / "script.txt").read_text(encoding="utf-8").strip()
                except Exception:
                    pass
        description = data.get("description", "")
        cta_keyword = _extract_cta_keyword(script_text) if script_text else ""
        thumbnail_path = _find_thumbnail_for_card(data)

        # TG-post photos (Maksim brand only) — preview status of attached
        # photos to make it obvious in the checklist BEFORE publishing.
        tg_photos = data.get("selfie_tg_photos", []) or []
        active_brand_for_menu = _get_active_brand_name()

        checklist = []
        checklist.append(f"{'✅' if video_path else '❌'} Видео" + (f": {Path(video_path).name}" if video_path else " — не найдено"))
        checklist.append(f"{'✅' if script_text else '⚠️'} Сценарий" + ("" if script_text else " — не найден"))
        checklist.append(f"{'✅' if description else '⚠️'} Описание публикации" + ("" if description else " — не заполнено (будет из сценария)"))
        checklist.append(f"{'✅' if cta_keyword else '⚠️'} CTA-слово" + (f": «{cta_keyword}»" if cta_keyword else " — не найдено в сценарии"))
        checklist.append(f"{'✅' if thumbnail_path else '⚠️'} Обложка" + ("" if thumbnail_path else " — нет"))
        if active_brand_for_menu == "maksim":
            checklist.append(
                f"{'✅' if tg_photos else '⚪'} Фото к TG-посту"
                + (f": {len(tg_photos)} шт." if tg_photos else " — без фото")
            )
        checklist_text = "\n".join(checklist)

        buttons = []
        for p in platforms:
            # TikTok отложен до v2 для бренда maksim — скрыт из меню
            # кросс-постинга. Логика публикации в коде остаётся.
            if p["id"] == "tiktok" and _get_active_brand_name() == "maksim":
                continue
            is_selected = p["id"] in selected
            connected = "🟢" if p["connected"] else "🔴"
            mark = "✅" if is_selected else "⬜"
            label = f"{mark} {p['icon']} {p['name']} {connected}"
            buttons.append([InlineKeyboardButton(label, callback_data=f"crosspost_ch:{p['id']}")])

        buttons.append([InlineKeyboardButton("📢 Опубликовать на выбранных", callback_data="crosspost_go")])
        # Maksim's TG-post is text-heavy and benefits from a visual hook.
        # Button shows current count when something is attached.
        if active_brand_for_menu == "maksim":
            photo_btn_label = (
                f"📷 Фото к TG-посту ({len(tg_photos)} выбрано)"
                if tg_photos else "📷 Фото к TG-посту"
            )
            buttons.append([InlineKeyboardButton(photo_btn_label, callback_data="tgphoto_menu")])
        if not description:
            buttons.append([InlineKeyboardButton("✏️ Написать описание", callback_data="gen_description")])
        buttons.append([InlineKeyboardButton("◀️ Назад", callback_data=f"notion_card:{card_id_prefix}")])

        crosspost_text = (
            f"📢 Кросс-постинг\n\n"
            f"📋 Чеклист:\n{checklist_text}\n\n"
            f"🟢 = подключено, 🔴 = нужна авторизация\n"
            f"Выбери площадки:"
        )
        crosspost_kb = InlineKeyboardMarkup(buttons)
        try:
            await query.edit_message_text(crosspost_text, reply_markup=crosspost_kb)
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text=crosspost_text, reply_markup=crosspost_kb)
        return

    # ═══════════════════════════════════════════════════════════════════
    #  TG-post photo attachment (Maksim brand)
    # ═══════════════════════════════════════════════════════════════════
    # Three callback families:
    #   tgphoto_menu               — root menu (откуда брать)
    #   tgphoto_lib[:cat:<name>]   — browse server library, optional category
    #                                 filter; index in pagination via :p<N>
    #   tgphoto_toggle:<idx>       — toggle photo by absolute lib index
    #   tgphoto_reroll[:cat:<n>]   — next batch of 6
    #   tgphoto_done               — confirm selection, back to crosspost menu
    #   tgphoto_send               — wait for ad-hoc photos via reply
    #   tgphoto_clear              — drop all attached photos
    #
    # State stored in pending[user_id]:
    #   selfie_tg_photos      — list[{"source": "lib"|"telegram",
    #                                  "path"|"file_id": str}]
    #   tgphoto_browse_state  — {"category": str|None, "shown": [str], "page": int}
    #   tgphoto_awaiting_reply — bool (set when user clicks "Прислать сейчас")
    # ═══════════════════════════════════════════════════════════════════
    if query.data == "tgphoto_menu":
        await _render_tgphoto_menu(query, context, data)
        return

    if query.data.startswith("tgphoto_lib") or query.data.startswith("tgphoto_reroll"):
        # Parse optional :cat:<name> suffix (works for both lib and reroll)
        category: str | None = None
        parts = query.data.split(":")
        if len(parts) >= 3 and parts[1] == "cat":
            category = parts[2]

        photos = _list_tg_photos(category=category)
        if not photos:
            empty_dir = _tg_photo_brand_dir() / (category or "")
            await query.edit_message_text(
                f"📂 В категории `{category or 'все'}` пусто.\n\n"
                f"Закинь файлы (jpg/png) сюда:\n`{empty_dir}`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ Назад", callback_data="tgphoto_menu")],
                ]),
            )
            return

        # Maintain shown set across rerolls so we cycle through all photos.
        # Reset when category changes or pool is exhausted.
        browse = data.get("tgphoto_browse_state") or {}
        if browse.get("category") != category:
            browse = {"category": category, "shown": [], "page": 0}
        shown_set = set(browse.get("shown", []))

        BATCH = 6
        remaining = [p for p in photos if str(p) not in shown_set]
        if len(remaining) < BATCH:
            shown_set = set()
            remaining = photos
            browse["page"] = 0
        sample = remaining[:BATCH]
        shown_set.update(str(p) for p in sample)
        browse["shown"] = list(shown_set)
        browse["page"] = browse.get("page", 0) + 1
        data["tgphoto_browse_state"] = browse
        _save_pending(pending)

        # Send media group preview. Codex review #3 P0: ExitStack scope
        # covers the awaited send so file descriptors are closed right
        # after Telegram acknowledges the upload.
        try:
            from contextlib import ExitStack
            from telegram import InputMediaPhoto
            if len(sample) >= 2:
                with ExitStack() as stack:
                    media = [
                        InputMediaPhoto(
                            media=stack.enter_context(open(str(p), "rb"))
                        )
                        for p in sample
                    ]
                    await context.bot.send_media_group(
                        chat_id=query.message.chat_id, media=media,
                    )
            else:
                with open(str(sample[0]), "rb") as f:
                    await context.bot.send_photo(
                        chat_id=query.message.chat_id, photo=f,
                    )
        except Exception as e:
            logger.warning(f"[tgphoto] preview send failed: {e}")
            await query.message.reply_text(f"Ошибка превью: {e}")
            return

        # Build selection keyboard. Each button maps to absolute path of the
        # photo (encoded as index into the FULL `photos` list so toggle is
        # stable even when batches reshuffle).
        tg_photos = data.get("selfie_tg_photos", []) or []
        attached_paths = {p.get("path") for p in tg_photos if p.get("source") == "lib"}

        # Remember which absolute indices are CURRENTLY on screen — needed
        # so the toggle handler can rebuild THIS keyboard with updated
        # marks (without re-sending the media group). 12 May 2026 UX fix.
        shown_batch = [photos.index(p) for p in sample]
        data["tgphoto_shown_batch"] = shown_batch
        _save_pending(pending)

        toggle_row: list[InlineKeyboardButton] = []
        for i, abs_idx in enumerate(shown_batch, start=1):
            p = photos[abs_idx]
            mark = "✅" if str(p) in attached_paths else str(i)
            cb = (
                f"tgphoto_toggle:{abs_idx}:cat:{category}"
                if category else f"tgphoto_toggle:{abs_idx}"
            )
            toggle_row.append(InlineKeyboardButton(mark, callback_data=cb))

        # Split toggle row into rows of 3 for narrower screens
        kb_rows = [toggle_row[:3], toggle_row[3:]] if len(toggle_row) > 3 else [toggle_row]

        reroll_cb = (
            f"tgphoto_reroll:cat:{category}" if category else "tgphoto_reroll"
        )
        kb_rows.append([
            InlineKeyboardButton(f"🔄 Ещё 6 фото", callback_data=reroll_cb),
        ])
        kb_rows.append([
            InlineKeyboardButton(
                f"✅ Готово ({len(tg_photos)} прикреплено)",
                callback_data="tgphoto_done",
            ),
        ])
        kb_rows.append([
            InlineKeyboardButton("◀️ Назад", callback_data="tgphoto_menu"),
        ])

        cat_label = f" → *{category}*" if category else ""
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                f"🗂 *Банк фото*{cat_label}\n\n"
                f"Всего: *{len(photos)}*. Показано: *{len(shown_set)}*.\n"
                f"Тапни цифру — добавить/снять фото.\n"
                f"Уже прикреплено: *{len(tg_photos)}*."
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb_rows),
        )
        return

    if query.data.startswith("tgphoto_toggle:"):
        # Format: tgphoto_toggle:<idx>[:cat:<name>]
        parts = query.data.split(":")
        try:
            abs_idx = int(parts[1])
        except (IndexError, ValueError):
            await query.answer("⚠️ Некорректный индекс")
            return
        category = parts[3] if len(parts) >= 4 and parts[2] == "cat" else None

        photos = _list_tg_photos(category=category)
        if abs_idx < 0 or abs_idx >= len(photos):
            await query.answer("⚠️ Фото не найдено (банк изменился?)")
            return
        target_path = str(photos[abs_idx])

        tg_photos = data.get("selfie_tg_photos", []) or []
        # Toggle: if already attached → remove; else → add
        existing_idx = next(
            (i for i, p in enumerate(tg_photos)
             if p.get("source") == "lib" and p.get("path") == target_path),
            None,
        )
        if existing_idx is not None:
            tg_photos.pop(existing_idx)
            toast = f"➖ Снято · в посте: {len(tg_photos)}"
        else:
            if len(tg_photos) >= 10:
                await query.answer("⚠️ Telegram-лимит: максимум 10 фото в посте", show_alert=True)
                return
            tg_photos.append({"source": "lib", "path": target_path})
            toast = f"✅ Добавлено · в посте: {len(tg_photos)}"
        data["selfie_tg_photos"] = tg_photos
        _save_pending(pending)

        # ── Visual feedback: rebuild ONLY the keyboard for the current batch
        # so the tapped button flips to ✅ (or back to its digit) without
        # re-sending the 6-photo media group. 12 May 2026 UX fix.
        shown_batch = data.get("tgphoto_shown_batch") or []
        if shown_batch:
            attached_paths = {p.get("path") for p in tg_photos if p.get("source") == "lib"}
            toggle_row: list[InlineKeyboardButton] = []
            for i, idx in enumerate(shown_batch, start=1):
                if idx < 0 or idx >= len(photos):
                    continue
                p = photos[idx]
                mark = "✅" if str(p) in attached_paths else str(i)
                cb = (
                    f"tgphoto_toggle:{idx}:cat:{category}"
                    if category else f"tgphoto_toggle:{idx}"
                )
                toggle_row.append(InlineKeyboardButton(mark, callback_data=cb))
            kb_rows = (
                [toggle_row[:3], toggle_row[3:]]
                if len(toggle_row) > 3 else [toggle_row]
            )
            reroll_cb = (
                f"tgphoto_reroll:cat:{category}" if category else "tgphoto_reroll"
            )
            kb_rows.append([
                InlineKeyboardButton("🔄 Ещё 6 фото", callback_data=reroll_cb),
            ])
            kb_rows.append([
                InlineKeyboardButton(
                    f"✅ Готово ({len(tg_photos)} прикреплено)",
                    callback_data="tgphoto_done",
                ),
            ])
            kb_rows.append([
                InlineKeyboardButton("◀️ Назад", callback_data="tgphoto_menu"),
            ])
            try:
                await query.edit_message_reply_markup(
                    reply_markup=InlineKeyboardMarkup(kb_rows),
                )
            except Exception as e:
                # Common case: "Message is not modified" if state matches
                # what's already drawn — safe to ignore.
                logger.debug(f"[tgphoto] reply_markup refresh: {e}")

        await query.answer(toast)
        return

    if query.data == "tgphoto_send":
        data["tgphoto_awaiting_reply"] = True
        _save_pending(pending)
        await query.edit_message_text(
            "📤 *Жду фото*\n\n"
            "Пришли одно или несколько фото (можно альбомом).\n"
            "Они прикрепятся к этому TG-посту.\n\n"
            "Когда закончишь — нажми *Готово*.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Готово", callback_data="tgphoto_menu")],
                [InlineKeyboardButton("✖️ Отмена", callback_data="tgphoto_cancel_send")],
            ]),
        )
        return

    if query.data == "tgphoto_cancel_send":
        # Codex review #3 P0 (9 May 2026): PTB v21+ ``CallbackQuery`` is
        # immutable, mutating ``query.data`` then re-calling
        # ``handle_callback`` raises at runtime. Use the render helper
        # directly instead of re-dispatching.
        data["tgphoto_awaiting_reply"] = False
        _save_pending(pending)
        await _render_tgphoto_menu(query, context, data)
        return

    if query.data == "tgphoto_clear":
        data["selfie_tg_photos"] = []
        _save_pending(pending)
        await query.answer("🗑 Все фото сняты")
        await _render_tgphoto_menu(query, context, data)
        return

    if query.data == "tgphoto_done":
        # Done with photo selection — branch by which flow brought us
        # here. Two entries:
        #   * Selfie/crosspost flow → return to crosspost menu (legacy).
        #   * Idea→tgpost flow → return to the post screen with the
        #     refreshed photo counter on the button.
        tg_photos = data.get("selfie_tg_photos", []) or []

        # ── Селфи script→tgpost return: перерисовать экран TG-поста по
        # сценарию со свежим счётчиком фото на кнопке. ──
        if data.get("tgphoto_return_script"):
            data.pop("tgphoto_return_script", None)
            _save_pending(pending)
            post_text = data.get("tg_post_from_script") or ""
            if not post_text:
                await query.edit_message_text(
                    f"✅ Прикреплено: *{len(tg_photos)}* фото.\n\n"
                    "Текст поста потерян — сгенерируй заново кнопкой "
                    "«📰 TG-пост по сценарию».",
                    parse_mode="Markdown",
                )
                return
            await query.edit_message_text(
                f"📰 TG-пост по сценарию:\n\n{post_text}",
                reply_markup=_tgpost_script_keyboard(len(tg_photos)),
            )
            return

        idea_idx_for_return = data.get("tgphoto_return_idea_idx")
        if idea_idx_for_return is not None:
            # ── Idea-flow return: re-render the post screen ──
            data.pop("tgphoto_return_idea_idx", None)
            _save_pending(pending)
            tg_block = data.get("tgpost") or {}
            last_post = tg_block.get("last_post") or ""
            if not last_post:
                await query.edit_message_text(
                    f"✅ Прикреплено: *{len(tg_photos)}* фото.\n\n"
                    "Текст поста потерян — сгенерируй заново через «🎰 Банк идей».",
                    parse_mode="Markdown",
                )
                return
            post_html = _render_tgpost_html(last_post)
            try:
                await query.edit_message_text(
                    f"📝 <b>Готовый пост:</b>\n\n{post_html}",
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=_idea_tgpost_keyboard(
                        int(idea_idx_for_return), len(tg_photos),
                    ),
                )
            except Exception as e:
                logger.debug(f"[tgphoto_done idea-return] HTML fallback: {e}")
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=last_post,
                    reply_markup=_idea_tgpost_keyboard(
                        int(idea_idx_for_return), len(tg_photos),
                    ),
                )
            return

        # ── Legacy crosspost-flow return ──
        card_prefix = data.get("crosspost_card_id", "")
        if card_prefix:
            await query.edit_message_text(
                (
                    f"✅ Прикреплено к TG-посту: *{len(tg_photos)}* фото.\n\n"
                    f"Возвращайся в крос-пост и публикуй."
                ),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📢 К кросс-посту", callback_data=f"crosspost:{card_prefix}")],
                ]),
            )
        else:
            await query.edit_message_text(
                "↩️ Готово. Открой /notion и выбери карточку для кросс-постинга.",
            )
        return

    if query.data.startswith("crosspost_ch:"):
        platform_id = query.data.split(":", 1)[1]
        selected = data.get("crosspost_selected", [])

        if platform_id in selected:
            selected.remove(platform_id)
        else:
            selected.append(platform_id)
        data["crosspost_selected"] = selected
        _save_pending(pending)

        # Refresh buttons
        platforms = get_available_platforms()
        video_path = _find_video_for_card(data)
        video_status = f"🎥 Видео: {Path(video_path).name}" if video_path else "⚠️ Видео не найдено (только текст)"

        buttons = []
        for p in platforms:
            # TikTok отложен до v2 для бренда maksim — скрыт из меню
            # кросс-постинга. Логика публикации в коде остаётся.
            if p["id"] == "tiktok" and _get_active_brand_name() == "maksim":
                continue
            is_selected = p["id"] in selected
            connected = "🟢" if p["connected"] else "🔴"
            mark = "✅" if is_selected else "⬜"
            label = f"{mark} {p['icon']} {p['name']} {connected}"
            buttons.append([InlineKeyboardButton(label, callback_data=f"crosspost_ch:{p['id']}")])

        buttons.append([InlineKeyboardButton("📢 Опубликовать на выбранных", callback_data="crosspost_go")])
        card_prefix = data.get("crosspost_card_id", "")
        buttons.append([InlineKeyboardButton("◀️ Назад", callback_data=f"notion_card:{card_prefix}")])

        await query.edit_message_text(
            f"📢 Кросс-постинг — выбрано {len(selected)}\n\n"
            f"{video_status}\n\n"
            "Выбери площадки:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if query.data in ("crosspost_go", "crosspost_force_publish"):
        if query.data == "crosspost_force_publish":
            data["_skip_description_check"] = True

        selected = data.get("crosspost_selected", [])

        if not selected:
            await query.answer("Сначала выбери хотя бы одну площадку")
            return

        # Description-guard: blocking opt-in if description is missing.
        # Without it the caption falls back to a raw script[:500] cut, which
        # is what Артём complained about on 8 June 2026 ("не увидел описания
        # в пайплайне"). User must either generate one or explicitly accept
        # the script fallback.
        if needs_description(data) and not data.get("_skip_description_check"):
            buttons = [
                [InlineKeyboardButton("🪄 Сгенерировать описание сейчас", callback_data="gen_description")],
                [InlineKeyboardButton("➡️ Опубликовать без (обрезка сценария)", callback_data="crosspost_force_publish")],
                [InlineKeyboardButton("⬅️ Назад к выбору площадок", callback_data="crosspost")],
            ]
            await query.edit_message_text(
                "📝 У карточки нет описания для публикации.\n\n"
                "Без описания в подпись пойдёт первые 500 символов сценария — "
                "это редко читается как нормальное описание.\n\n"
                "Что делаем?",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        # Consume one-shot skip flag so the next publish gets the guard again.
        data.pop("_skip_description_check", None)

        # Get card info
        card_title = data.get("card_data", {}).get("title", "") or data.get("notion_edit_title", "")
        script_text = data.get("script", "")

        # Fallback 1: read persisted script.txt from the project folder
        # (written on save-to-Notion — survives bot restarts / lost pending).
        if not script_text:
            proj = _project_dir(data) or _project_dir_by_prefix(
                data.get("crosspost_card_id") or data.get("notion_edit_card") or ""
            )
            if proj and (proj / "script.txt").exists():
                try:
                    script_text = (proj / "script.txt").read_text(encoding="utf-8").strip()
                    logger.info(f"[crosspost] Loaded script from {proj.name}/script.txt ({len(script_text)} chars)")
                except Exception as e:
                    logger.warning(f"Failed to read script.txt: {e}")

        # Fallback 2: pull from Notion — resolve full ID from prefix if needed.
        if not script_text:
            card_id = (
                data.get("notion_edit_card")
                or data.get("notion_page_id")
                or _resolve_notion_id_by_prefix(data.get("crosspost_card_id", ""))
            )
            if card_id:
                try:
                    blocks = await asyncio.to_thread(notion.blocks.children.list, block_id=card_id)
                    for block in blocks.get("results", []):
                        if block["type"] == "paragraph" and not script_text:
                            texts = [t.get("plain_text", "") for t in block["paragraph"].get("rich_text", [])]
                            text = "".join(texts).strip()
                            if len(text) > 50:
                                script_text = text
                                break
                    # Cache script.txt into the project folder so next
                    # crosspost doesn't have to hit Notion again.
                    if script_text:
                        proj = _project_dir(data) or _project_dir_by_prefix(
                            data.get("crosspost_card_id") or ""
                        )
                        if proj and not (proj / "script.txt").exists():
                            try:
                                (proj / "script.txt").write_text(script_text, encoding="utf-8")
                                logger.info(f"[crosspost] Cached script.txt → {proj.name}")
                            except Exception as e:
                                logger.warning(f"Failed to cache script.txt: {e}")
                except Exception as e:
                    logger.warning(f"Failed to fetch script from Notion: {e}")

        video_path = _find_video_for_card(data)
        thumbnail_path = _find_thumbnail_for_card(data)

        # Trim CTA for non-Instagram platforms (last 4s = "подпишитесь" призыв)
        video_path_nocta = None
        needs_trim = any(p in selected for p in ("youtube", "tiktok", "vk", "telegram"))
        if needs_trim and video_path:
            video_path_nocta = await asyncio.to_thread(
                _trim_cta_from_video, video_path, 4.0, script_text or ""
            )

        await query.edit_message_text(f"📢 Публикую на {len(selected)} площадках...")

        results = []
        published_codes = []

        for platform_id in selected:
            try:
                if platform_id == "youtube":
                    if not youtube_is_connected():
                        results.append("❌ YouTube — не авторизован (запусти /yt_auth)")
                        continue
                    if not video_path:
                        results.append("❌ YouTube — нет видео для загрузки")
                        continue
                    # Use description if available, otherwise script
                    yt_description = data.get("description", "") or (script_text[:500] if script_text else "")
                    yt_description += AI_DISCLOSURE
                    # Use CTA-trimmed version for YouTube (no "подпишитесь")
                    yt_video_path = video_path_nocta or video_path
                    if thumbnail_path:
                        # Prepend cover to the ALREADY-TRIMMED version so we
                        # keep both: cover frame at start + CTA cut at end.
                        prepended = await asyncio.to_thread(
                            _prepend_cover_to_video, yt_video_path, thumbnail_path, 1.0
                        )
                        if prepended:
                            yt_video_path = prepended
                    result = await asyncio.to_thread(
                        youtube_upload_short,
                        video_path=yt_video_path,
                        title=card_title or "Новое видео",
                        description=yt_description,
                        thumbnail_path=thumbnail_path,
                    )
                    if result:
                        results.append(f"✅ YouTube Shorts — {result['url']}")
                        published_codes.append("YouTube Shorts")
                    else:
                        results.append("❌ YouTube — ошибка загрузки")

                elif platform_id == "instagram":
                    if not instagram_is_connected():
                        results.append("❌ Instagram — не авторизован (запусти /ig_auth)")
                        continue
                    if not video_path:
                        results.append("❌ Instagram — нет видео для загрузки")
                        continue
                    # Instagram needs a public URL — upload to temp hosting
                    await query.edit_message_text(
                        f"📢 Публикую на {len(selected)} площадках...\n"
                        "⏳ Загружаю видео для Instagram..."
                    )
                    public_url = await asyncio.to_thread(upload_video_to_temp_hosting, video_path)
                    if not public_url:
                        results.append("❌ Instagram — не удалось загрузить видео на хостинг")
                        continue
                    # Upload cover as permanent URL for Instagram
                    ig_cover_url = None
                    if thumbnail_path:
                        ig_cover_url = save_media_permanent(thumbnail_path, prefix="cover")
                    ig_caption = build_ig_caption(
                        card_title=card_title,
                        description=data.get("description", ""),
                        script_text=script_text,
                        ai_disclosure=AI_DISCLOSURE,
                    )
                    result = await asyncio.to_thread(
                        instagram_upload_reel,
                        video_url=public_url,
                        caption=ig_caption,
                        cover_url=ig_cover_url,
                    )
                    if result:
                        results.append(f"✅ Instagram Reels — опубликовано")
                        published_codes.append("Instagram")
                        # Save media_id for CTA setup
                        if result.get("id"):
                            data["ig_media_id"] = result["id"]
                            # Try auto-extract keyword from script
                            try:
                                cta_keyword = _extract_cta_keyword(script_text)
                                if cta_keyword:
                                    data["ig_cta_keyword_suggestion"] = cta_keyword
                            except Exception:
                                pass
                    else:
                        results.append("❌ Instagram — ошибка публикации")

                elif platform_id == "tiktok":
                    # [Variant A: manual upload] TikTok's bot detection blocks reliable
                    # automation — instead send a ready-to-upload package to the chat.
                    if not video_path:
                        results.append("❌ TikTok — нет видео для пакета")
                        continue
                    tt_video = video_path_nocta or video_path
                    tt_description = data.get("description", "") or card_title or "Новое видео"
                    tt_description += AI_DISCLOSURE
                    tt_hashtags = "#shorts #ai #нейросети"
                    tt_caption = f"{tt_description}\n\n{tt_hashtags}"
                    try:
                        # Send caption first (so user can copy it)
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=(
                                "📦 <b>Пакет для TikTok</b>\n\n"
                                "Скопируй текст ниже и вставь в TikTok при загрузке видео:\n\n"
                                f"<code>{tt_caption}</code>"
                            ),
                            parse_mode="HTML",
                        )
                        # Send video as file
                        with open(tt_video, "rb") as vf:
                            await context.bot.send_video(
                                chat_id=user_id,
                                video=vf,
                                caption="👆 Видео для TikTok. Открой на телефоне → сохрани → загрузи в TikTok.",
                                supports_streaming=True,
                            )
                        results.append("📦 TikTok — пакет для ручной загрузки отправлен")
                        published_codes.append("TikTok")
                    except Exception as e:
                        logger.error(f"TikTok manual package send failed: {e}")
                        results.append("❌ TikTok — не удалось отправить пакет")

                elif platform_id == "vk":
                    if not vk_is_connected():
                        results.append("❌ VK Клипы — не авторизован (запусти /vk_auth)")
                        continue
                    if not video_path:
                        results.append("❌ VK Клипы — нет видео для загрузки")
                        continue
                    # Use CTA-trimmed version for VK
                    vk_video = video_path_nocta or video_path
                    # Use description if available, otherwise card title
                    vk_description = data.get("description", "") or card_title or "Новое видео"
                    vk_description += AI_DISCLOSURE
                    result = await asyncio.to_thread(
                        vk_upload_clip,
                        video_path=vk_video,
                        description=vk_description,
                    )
                    if result:
                        results.append(f"✅ VK Клипы — опубликовано")
                        published_codes.append("VK")
                    else:
                        results.append("❌ VK Клипы — ошибка загрузки")

                elif platform_id == "telegram":
                    if not TELEGRAM_CHANNEL_ID:
                        results.append("❌ Telegram — TELEGRAM_CHANNEL_ID не задан в .env")
                        continue
                    # Crosspost-bug fix (9 May 2026):
                    # Maksim's Selfie pipeline generates a DEDICATED TG-channel post
                    # via tg_post_writer.generate_post(post_type="review_essay",
                    # brand="maksim") — длинный пост в стиле Максима, ОТЛИЧАЕТСЯ
                    # от транскрипта видео. До этого фикса crosspost публиковал
                    # `script_text` (= transcript) что давало plain-text видео-
                    # расшифровку в канале вместо нормального TG-поста.
                    # Pipeline сохраняет результат в data["selfie_tg_post"].
                    # Маркдаун **bold** конвертируется в HTML <b>...</b> чтобы
                    # пройти через parse_mode="HTML" в telegram_post_to_channel.
                    dedicated_tg_post = data.get("selfie_tg_post")
                    # 12 May 2026 fix — Notion fallback. Старые карточки
                    # (созданные до текущего pending.json) теряли кэш TG-
                    # поста при рестарте. Тогда срабатывал legacy fallback
                    # и в канал уходил сырой транскрипт. Теперь читаем
                    # дополнительно из Notion-блоков под H2 «📨 Пост в
                    # Telegram-канал» — там пост живёт постоянно.
                    if not dedicated_tg_post:
                        # pending может хранить только короткий
                        # `crosspost_card_id` (8-char префикс), без
                        # полного `notion_page_id` — это типичный кейс
                        # после рестарта бота. Резолвим полный ID из
                        # префикса через тот же helper, которым crosspost
                        # резолвит script.txt fallback.
                        notion_id_for_post = data.get("notion_page_id") or ""
                        if not notion_id_for_post:
                            prefix_for_post = (
                                data.get("crosspost_card_id")
                                or data.get("notion_edit_card")
                                or ""
                            )
                            if prefix_for_post:
                                notion_id_for_post = await asyncio.to_thread(
                                    _resolve_notion_id_by_prefix,
                                    prefix_for_post,
                                ) or ""
                        if notion_id_for_post:
                            dedicated_tg_post = await asyncio.to_thread(
                                _read_tg_post_from_notion, notion_id_for_post,
                            )
                            if dedicated_tg_post:
                                logger.info(
                                    f"[crosspost] TG-post recovered from Notion "
                                    f"({len(dedicated_tg_post)} chars)"
                                )
                                # Кэшируем обратно в pending — не дёргать
                                # Notion повторно. Заодно сохраняем
                                # полный ID, чтобы дальнейшие шаги
                                # crosspost-handler'а тоже работали.
                                data["selfie_tg_post"] = dedicated_tg_post
                                data["notion_page_id"] = notion_id_for_post
                                _save_pending(pending)
                            else:
                                logger.info(
                                    f"[crosspost] No TG-post block found "
                                    f"in Notion card {notion_id_for_post[:12]}"
                                )
                    if dedicated_tg_post:
                        post_text = re.sub(
                            r"\*\*(.+?)\*\*", r"<b>\1</b>", dedicated_tg_post
                        )
                    else:
                        # Legacy / Artem default flow: title + transcript
                        # (срабатывает только если TG-поста нет НИГДЕ —
                        # ни в pending, ни в Notion).
                        post_text = (
                            f"<b>{card_title}</b>\n\n{script_text}"
                            if script_text else card_title
                        )
                    # Pass attached TG-post photos (Maksim brand). Empty list
                    # → телеграм-пост уходит без фото (legacy-совместимо).
                    tg_photos_attach = data.get("selfie_tg_photos") or None
                    result = await telegram_post_to_channel(
                        context.bot, post_text,
                        photos=tg_photos_attach,
                    )
                    if result:
                        results.append(f"✅ Telegram канал — отправлено")
                        published_codes.append("TG канал")
                    else:
                        results.append("❌ Telegram — ошибка отправки")

            except Exception as e:
                results.append(f"❌ {platform_id} — {e}")
                logger.error(f"Crosspost {platform_id} error: {e}", exc_info=True)

        # Clear selection so the NEXT open of the crosspost menu re-selects fresh
        # (prevents stale ticks from a previous run leaking into the new one).
        data.pop("crosspost_selected", None)
        _save_pending(pending)

        # Record publications in calendar
        if published_codes:
            try:
                _record_publication(published_codes)
            except Exception:
                pass

        # Update "Опубликовано на" in Notion and check if all platforms done
        notion_id = data.get("notion_page_id")
        if notion_id and published_codes:
            try:
                # Read current "Опубликовано на" values
                page = notion.pages.retrieve(page_id=notion_id)
                props = page.get("properties", {})
                already_published = set()
                pub_prop = props.get("Опубликовано на", {})
                if pub_prop.get("type") == "multi_select":
                    already_published = {opt["name"] for opt in pub_prop.get("multi_select", [])}

                # Merge with newly published
                all_published = already_published | set(published_codes)
                update_props = {
                    "Опубликовано на": {
                        "multi_select": [{"name": p} for p in all_published]
                    }
                }
                # «Дата публикации» — момент ПЕРВОЙ публикации (на любую
                # площадку). Заполняем только если до этого было пусто
                # (already_published was empty), иначе сохраняем оригинал.
                # 9 May 2026 fix.
                pub_date_prop = props.get("Дата публикации", {})
                pub_date_existing = (pub_date_prop.get("date") or {}).get("start")
                if not already_published and not pub_date_existing:
                    update_props["Дата публикации"] = {
                        "date": {"start": datetime.now().strftime("%Y-%m-%d")}
                    }
                notion.pages.update(page_id=notion_id, properties=update_props)

                # Check if all target platforms are published
                target_platforms = set()
                plat_prop = props.get("Площадки", {})
                if plat_prop.get("type") == "multi_select":
                    target_platforms = {opt["name"] for opt in plat_prop.get("multi_select", [])}

                if target_platforms and target_platforms.issubset(all_published):
                    # All platforms done — move to "Опубликовано"
                    notion.pages.update(
                        page_id=notion_id,
                        properties={"Status": _status_property_value("Опубликовано")}
                    )
                    results.append("\n✅ Все площадки опубликованы — статус → Опубликовано")
                else:
                    remaining = target_platforms - all_published
                    if remaining:
                        results.append(f"\n⏳ Осталось опубликовать: {', '.join(remaining)}")
            except Exception as e:
                logger.warning(f"Failed to update Опубликовано на: {e}")

        card_prefix = data.get("crosspost_card_id", "")
        buttons = []
        if data.get("ig_media_id"):
            suggestion = data.get("ig_cta_keyword_suggestion", "")
            cta_hint = f" (предложение: «{suggestion}»)" if suggestion else ""
            buttons.append([InlineKeyboardButton(f"🔑 Настроить CTA для Instagram{cta_hint}", callback_data="ig_cta_setup")])
        buttons.append([InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{card_prefix}")])
        await query.edit_message_text(
            f"📢 Кросс-постинг завершён:\n\n" + "\n".join(results),
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    # ── Instagram CTA setup ──
    if query.data == "ig_cta_setup":
        media_id = data.get("ig_media_id", "")
        if not media_id:
            await query.edit_message_text("Нет media_id — Instagram не был опубликован.")
            return
        # Auto-extract keyword from CTA line of script
        keyword = _extract_cta_keyword(data.get("script", "")) or ""
        if keyword:
            data["ig_cta_keyword"] = keyword
        card_title = data.get("notion_title", "") or data.get("card_data", {}).get("title", "")
        # Auto-save with master-post so CTA works even if user doesn't
        # click anything else.  They can still change the link below.
        if keyword and media_id:
            reply_text = _build_dm_reply_text(DEFAULT_DM_REPLY_URL, card_title)
            save_keyword_for_post(media_id=media_id, keyword=keyword, reply_text=reply_text, guide_url="")
            _save_pending(pending)
            keyword_info = f"✅ Ключевое слово «{keyword}» уже активно (мастер-пост).\n\nХочешь изменить ссылку?"
        else:
            keyword_info = "Ключевое слово не найдено в сценарии.\n\n" + "Куда вести подписчика из DM?"
        await query.edit_message_text(
            f"🔑 Настройка CTA для Instagram\n\n"
            f"{keyword_info}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📨 Ссылка на конкретный пост TG", callback_data="ig_cta_tg_post")],
                [InlineKeyboardButton(f"🔖 Мастер-пост (дефолт)", callback_data="ig_cta_master")],
                [InlineKeyboardButton("📎 Ссылка на гайд из Notion", callback_data="ig_cta_direct")],
                [InlineKeyboardButton("✏️ Изменить ключевое слово", callback_data="ig_cta_change_keyword")],
                [InlineKeyboardButton("⏭ Пропустить CTA", callback_data=f"notion_card:{data.get('crosspost_card_id', '')}")],
            ]),
        )
        return

    if query.data == "ig_cta_master":
        # Use the global master-post URL from .env as the DM link.
        media_id = data.get("ig_media_id", "")
        keyword = data.get("ig_cta_keyword", "")
        if not keyword:
            data["state"] = "ig_cta_keyword_then_master"
            _save_pending(pending)
            await query.edit_message_text("Введи ключевое слово (одно слово, которое пользователь напишет в комментариях):")
            return
        card_title = data.get("notion_title", "") or data.get("card_data", {}).get("title", "")
        reply_text = _build_dm_reply_text(DEFAULT_DM_REPLY_URL, card_title)
        save_keyword_for_post(media_id=media_id, keyword=keyword, reply_text=reply_text, guide_url="")
        _save_pending(pending)
        await query.edit_message_text(
            f"✅ CTA настроен!\n\n"
            f"Ключевое слово: «{keyword}»\n"
            f"Ссылка (мастер-пост): {DEFAULT_DM_REPLY_URL}\n\n"
            f"Когда кто-то напишет «{keyword}» в комментариях — получит мастер-пост в DM.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{data.get('crosspost_card_id', '')}")],
            ]),
        )
        return

    if query.data == "ig_cta_tg_post":
        # Ask user for a specific t.me/... URL for this post.
        keyword = data.get("ig_cta_keyword", "")
        if not keyword:
            data["state"] = "ig_cta_keyword_then_tg_post"
            _save_pending(pending)
            await query.edit_message_text("Введи ключевое слово (одно слово, которое пользователь напишет в комментариях):")
            return
        data["state"] = "ig_cta_tg_post_url"
        _save_pending(pending)
        await query.edit_message_text(
            "Вставь ссылку на конкретный пост в Telegram (формата https://t.me/...).\n\n"
            f"Или отправь «дефолт» чтобы использовать мастер-пост {DEFAULT_DM_REPLY_URL}"
        )
        return

    if query.data == "ig_cta_telegram":
        # CTA -> Telegram channel link
        data["state"] = "ig_cta_tg_post_url"
        _save_pending(pending)
        await query.edit_message_text(
            "Введи ссылку на пост в Telegram-канале (t.me/...):\n\n"
            "Или напиши «дефолт» чтобы использовать стандартную ссылку."
        )
        return

    if query.data == "ig_cta_direct":
        media_id = data.get("ig_media_id", "")
        keyword = data.get("ig_cta_keyword", "")
        guide_url = data.get("guide_url", "")
        card_title = data.get("notion_title", "") or data.get("card_data", {}).get("title", "")
        if not keyword:
            data["state"] = "ig_cta_keyword_then_direct"
            _save_pending(pending)
            await query.edit_message_text("Введи ключевое слово (одно слово, которое пользователь напишет в комментариях):")
            return
        if not guide_url:
            data["state"] = "ig_cta_guide_url"
            _save_pending(pending)
            await query.edit_message_text("Введи ссылку на гайд/материал (URL):")
            return
        reply_text = f"Привет! Вот материалы по теме «{card_title}»:\n\n{guide_url}"
        # URL is already embedded in reply_text — pass guide_url="" so the
        # webhook handler doesn't append it a second time.
        save_keyword_for_post(media_id=media_id, keyword=keyword, reply_text=reply_text, guide_url="")
        _save_pending(pending)
        await query.edit_message_text(
            f"✅ CTA настроен!\n\n"
            f"Ключевое слово: «{keyword}»\n"
            f"Ссылка: {guide_url}\n\n"
            f"Когда кто-то напишет «{keyword}» в комментариях — получит ссылку в DM.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{data.get('crosspost_card_id', '')}")],
            ]),
        )
        return

    if query.data == "ig_cta_change_keyword":
        data["state"] = "ig_cta_keyword_input"
        _save_pending(pending)
        await query.edit_message_text("Введи новое ключевое слово (одно слово):")
        return

    if query.data == "broll_yt_search":
        # AI-powered YouTube B-roll search by script
        script_text = data.get("script", "")
        if not script_text:
            await query.edit_message_text("Сначала создай сценарий.")
            return

        # Pre-check YouTube OAuth token — fail fast if not authorized
        # instead of returning 0 videos silently.
        try:
            from crosspost import _get_youtube_access_token
            if not _get_youtube_access_token():
                await query.edit_message_text(
                    "⚠️ YouTube не авторизован или токен просрочен.\n\n"
                    "Запусти /yt_auth и залогинься под рабочим аккаунтом.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("◀️ Назад к карточке", callback_data=f"notion_card:{data.get('notion_page_id', '')}")],
                    ]),
                )
                return
        except Exception as e:
            logger.warning(f"YouTube token pre-check raised: {e}")
            # Fall through — token helper itself may have raised, let the
            # real search attempt produce a proper error below.

        await query.edit_message_text("🔍 Анализирую сценарий и ищу видео на YouTube...")
        try:
            # Step 1: Claude generates search queries
            queries = await asyncio.to_thread(generate_youtube_search_queries, script_text)

            # Step 2: Search YouTube for each query
            all_videos = []
            seen_ids = set()
            query_info = []
            for q_data in queries:
                q = q_data.get("query", "")
                reason = q_data.get("reason", "")
                if not q:
                    continue
                results = await asyncio.to_thread(search_youtube_videos, q, 3)
                new_count = 0
                for v in results:
                    if v["video_id"] not in seen_ids:
                        seen_ids.add(v["video_id"])
                        v["search_query"] = q
                        v["search_reason"] = reason
                        all_videos.append(v)
                        new_count += 1
                query_info.append(f"• {q} → {new_count} видео")

            if not all_videos:
                await query.edit_message_text(
                    "Не нашёл подходящих видео на YouTube.\n\n"
                    "Запросы были:\n" + "\n".join(query_info),
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔍 Искать на стоках", callback_data="broll_stock")],
                        [InlineKeyboardButton("🎬 Скинуть видео вручную", callback_data="broll_youtube")],
                        [InlineKeyboardButton("◀️ Назад к карточке", callback_data=f"notion_card:{data.get('notion_page_id', '')}")],
                    ]),
                )
                return

            # Store results
            data["yt_search_results"] = all_videos
            data["yt_search_queries"] = query_info
            _save_pending(pending)

            # Show summary
            summary = f"🔍 Найдено {len(all_videos)} видео на YouTube:\n\n"
            summary += "\n".join(query_info) + "\n\n"
            summary += "Выбери видео для нарезки на B-roll клипы:"

            await query.edit_message_text(summary)

            # Send each video as a message with select button
            for idx, v in enumerate(all_videos):
                try:
                    caption = (
                        f"#{idx+1} | {v['title'][:80]}\n"
                        f"📺 {v['channel']} | 📅 {v['published']}\n"
                        f"🔎 {v['search_reason']}\n"
                        f"🔗 {v['url']}"
                    )
                    btn = InlineKeyboardMarkup([
                        [InlineKeyboardButton(f"✂️ Нарезать #{idx+1}", callback_data=f"yt_broll_pick:{idx}")]
                    ])
                    if v.get("thumbnail"):
                        await context.bot.send_photo(
                            chat_id=query.message.chat_id,
                            photo=v["thumbnail"],
                            caption=caption,
                            reply_markup=btn,
                        )
                    else:
                        await context.bot.send_message(
                            chat_id=query.message.chat_id,
                            text=caption,
                            reply_markup=btn,
                        )
                except Exception as e:
                    logger.warning(f"Failed to send YT result #{idx+1}: {e}")

            # Final buttons
            buttons = [
                [InlineKeyboardButton("🔍 Искать другие запросы", callback_data="broll_yt_search")],
                [InlineKeyboardButton("🔍 Искать на стоках", callback_data="broll_stock")],
            ]
            if elevenlabs_client and not data.get("voice_parts"):
                buttons.append([InlineKeyboardButton("🎙 Озвучить", callback_data="voiceover_choose")])
            buttons.append([InlineKeyboardButton("✅ Готово", callback_data="finish")])

            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="Нажми «Нарезать» под видео — я скачаю и нарежу на клипы.",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception as e:
            logger.error(f"YouTube B-roll search error: {e}", exc_info=True)
            await query.edit_message_text(f"Ошибка поиска: {e}")
        return

    if query.data.startswith("yt_broll_pick:"):
        # User selected a YouTube video from search results — download and cut
        idx = int(query.data.split(":")[1])
        yt_results = data.get("yt_search_results", [])
        if idx >= len(yt_results):
            await query.answer("Видео не найдено")
            return

        video = yt_results[idx]
        yt_url = video["url"]
        data["broll_youtube_url"] = yt_url
        _save_pending(pending)

        # The "Нарезать #N" button lives on a photo message (YT thumbnail),
        # so edit_message_text would raise "no text in the message to edit".
        # Acknowledge the click and send a fresh status message instead.
        try:
            await query.answer("Скачиваю…")
        except Exception:
            pass
        status_msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                f"🎬 Скачиваю и нарезаю: {video['title'][:60]}...\n"
                f"🔗 {yt_url}\n\n"
                "⏳ Это может занять 1–2 минуты..."
            ),
        )

        # Download + cut LOCALLY via yt-dlp + ffmpeg (same process as bot).
        # Previously this block orchestrated work over SSH to 178.104.133.148
        # (decommissioned). Now we reuse download_and_cut_youtube() like the
        # broll_youtube manual-URL flow does.
        try:
            clips_raw = await asyncio.to_thread(download_and_cut_youtube, yt_url, 5, 8)

            if not clips_raw:
                try:
                    await status_msg.edit_text(
                        "Не удалось нарезать клипы из этого видео.",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔍 Выбрать другое", callback_data="broll_yt_search")],
                            [InlineKeyboardButton("📋 К списку найденных видео", callback_data="yt_results_show")],
                            [InlineKeyboardButton("◀️ Назад к карточке", callback_data=f"notion_card:{data.get('notion_page_id', '')}")],
                        ]),
                    )
                except Exception:
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text="Не удалось нарезать клипы из этого видео.",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔍 Выбрать другое", callback_data="broll_yt_search")],
                            [InlineKeyboardButton("📋 К списку найденных видео", callback_data="yt_results_show")],
                            [InlineKeyboardButton("◀️ Назад к карточке", callback_data=f"notion_card:{data.get('notion_page_id', '')}")],
                        ]),
                    )
                return

            # Normalize clips to pending[]["broll_clips"] schema expected by
            # downstream flows (broll_select, broll_approve).
            existing_clips = data.get("broll_clips", [])
            start_idx = len(existing_clips)
            for i, c in enumerate(clips_raw):
                existing_clips.append({
                    "id": f"yt_{start_idx + i}",
                    "source": "youtube",
                    "youtube_url": yt_url,
                    "youtube_title": video.get("title", ""),
                    "path": c["path"],
                    "filename": Path(c["path"]).name,
                    "tags": video.get("search_reason", ""),
                })
            data["broll_clips"] = existing_clips
            if "broll_selected" not in data:
                data["broll_selected"] = []
            _save_pending(pending)

            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"✂️ Нарезано {len(clips_raw)} клипов из «{video['title'][:50]}»\nВыбери подходящие:",
            )

            for ci in range(len(clips_raw)):
                real_idx = start_idx + ci
                clip = existing_clips[real_idx]
                try:
                    select_btn = InlineKeyboardMarkup([
                        [InlineKeyboardButton(f"✅ Выбрать #{real_idx+1}", callback_data=f"broll_select:{real_idx}")]
                    ])
                    clip_path = Path(clip["path"])
                    if clip_path.exists():
                        with open(clip_path, "rb") as f:
                            await context.bot.send_video(
                                chat_id=query.message.chat_id,
                                video=f,
                                caption=f"#{real_idx+1} | YouTube: {clip['filename']}",
                                supports_streaming=True,
                                reply_markup=select_btn,
                            )
                    else:
                        logger.warning(f"Clip path not found: {clip_path}")
                        await context.bot.send_message(
                            chat_id=query.message.chat_id,
                            text=f"#{real_idx+1} | {clip['filename']} (файл не найден)",
                            reply_markup=select_btn,
                        )
                except Exception as e:
                    logger.warning(f"Failed to send clip #{real_idx+1}: {e}")
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text=f"#{real_idx+1} | {clip['filename']}",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton(f"✅ Выбрать #{real_idx+1}", callback_data=f"broll_select:{real_idx}")]
                        ]),
                    )

            buttons = [
                [InlineKeyboardButton("💾 Сохранить выбранные в Notion", callback_data="broll_approve")],
                [InlineKeyboardButton("📋 К списку найденных видео", callback_data="yt_results_show")],
                [InlineKeyboardButton("🔍 Искать новые запросы", callback_data="broll_yt_search")],
                [InlineKeyboardButton("🔍 Искать на стоках", callback_data="broll_stock")],
            ]
            if elevenlabs_client and not data.get("voice_parts"):
                buttons.append([InlineKeyboardButton("🎙 Озвучить", callback_data="voiceover_choose")])
            buttons.append([InlineKeyboardButton("✅ Готово", callback_data="finish")])

            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="Нажми «Выбрать» под понравившимися клипами, затем «Сохранить».\n\n"
                     "💡 Можно нарезать ещё из других видео — жми «📋 К списку найденных видео» "
                     "и выбирай следующее. Клипы прибавятся к уже нарезанным, в конце выберешь из общего пула.\n\n"
                     "Либо проскролль чат вверх — кнопки «✂️ Нарезать #N» под каждым найденным роликом "
                     "остаются рабочими.",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception as e:
            logger.error(f"YouTube B-roll download error: {e}", exc_info=True)
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"Ошибка загрузки видео: {e}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔍 Выбрать другое", callback_data="broll_yt_search")],
                    [InlineKeyboardButton("🎬 Скинуть вручную", callback_data="broll_youtube")],
                    [InlineKeyboardButton("◀️ Назад к карточке", callback_data=f"notion_card:{data.get('notion_page_id', '')}")],
                ]),
            )
        return

    if query.data == "yt_results_show":
        # Re-render cached YouTube search results without running Claude/YouTube API again.
        # Used after cutting N-th video, when user wants to cut another from the same search.
        yt_results = data.get("yt_search_results", [])
        query_info = data.get("yt_search_queries", [])
        if not yt_results:
            await query.edit_message_text(
                "Список найденных видео потерян. Запусти поиск заново.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔍 Искать заново", callback_data="broll_yt_search")],
                    [InlineKeyboardButton("◀️ Назад к карточке", callback_data=f"notion_card:{data.get('notion_page_id', '')}")],
                ]),
            )
            return

        summary = f"📋 Найдено {len(yt_results)} видео на YouTube (из кеша, без нового запроса):\n\n"
        if query_info:
            summary += "\n".join(query_info) + "\n\n"
        summary += "Выбери видео для нарезки:"
        await query.edit_message_text(summary)

        for idx, v in enumerate(yt_results):
            try:
                caption = (
                    f"#{idx+1} | {v['title'][:80]}\n"
                    f"📺 {v['channel']} | 📅 {v['published']}\n"
                    f"🔎 {v['search_reason']}\n"
                    f"🔗 {v['url']}"
                )
                btn = InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"✂️ Нарезать #{idx+1}", callback_data=f"yt_broll_pick:{idx}")]
                ])
                if v.get("thumbnail"):
                    await context.bot.send_photo(
                        chat_id=query.message.chat_id,
                        photo=v["thumbnail"],
                        caption=caption,
                        reply_markup=btn,
                    )
                else:
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text=caption,
                        reply_markup=btn,
                    )
            except Exception as e:
                logger.warning(f"Failed to render cached YT result #{idx+1}: {e}")

        footer_buttons = [
            [InlineKeyboardButton("💾 Сохранить выбранные в Notion", callback_data="broll_approve")],
            [InlineKeyboardButton("🔍 Искать новые запросы", callback_data="broll_yt_search")],
            [InlineKeyboardButton("🔍 Искать на стоках", callback_data="broll_stock")],
            [InlineKeyboardButton("◀️ Назад к карточке", callback_data=f"notion_card:{data.get('notion_page_id', '')}")],
        ]
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="Жми «Нарезать» под нужным видео. Клипы прибавятся к уже нарезанным.",
            reply_markup=InlineKeyboardMarkup(footer_buttons),
        )
        return

    if query.data == "broll_photo_lib" or query.data == "broll_photo_reroll":
        # Explicit photo library path — user picks this when they want Ken Burns
        # clips built from Midjourney photos instead of video B-roll.
        #
        # `broll_photo_lib`      — first entry, shows random sample (PHOTO_PREVIEW_COUNT)
        # `broll_photo_reroll`   — user clicked «Другая выборка», sends a fresh batch
        _brand_now_pl = _get_active_brand_name()
        photos = _list_brand_photo_library(_brand_now_pl)
        if not photos:
            empty_hint = (
                "Закинь фото через «📥 Готовые материалы» — они попадут в проект "
                "и в общую библиотеку Максима."
                if _brand_now_pl == "maksim" else
                "Закинь картинки в `broll-library/photos/midjourney/`."
            )
            await query.edit_message_text(
                f"📸 Фото-библиотека пуста.\n\n{empty_hint}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ Назад", callback_data="broll")],
                ]),
                parse_mode="Markdown",
            )
            return

        # Resolve card id for the "go to assembly" button
        card_id = data.get("notion_page_id") if data else None
        if not card_id:
            await query.edit_message_text(
                "❌ Не могу определить карточку. Открой её через /notion и повтори.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ Назад", callback_data="broll")],
                ]),
            )
            return

        # Pick fresh random samples (max media group size is 10; PHOTO_PREVIEW_COUNT
        # = 9 by default — даёт быстрее выбрать без wall-of-photos). Exclude
        # already-shown so reroll gives genuinely new options.
        import random as _random
        PREVIEW_COUNT = PHOTO_PREVIEW_COUNT
        shown_set = set(data.get("photo_lib_shown", [])) if data else set()
        remaining = [p for p in photos if str(p) not in shown_set]
        if len(remaining) < PREVIEW_COUNT:
            # Exhausted — reset and start over
            shown_set = set()
            remaining = photos
        sample = _random.sample(remaining, min(PREVIEW_COUNT, len(remaining)))
        shown_set.update(str(p) for p in sample)
        if data is not None:
            data["photo_lib_shown"] = list(shown_set)
            # Сохраняем текущую девятку — toggle handler идентифицирует фото по индексу.
            data["photo_lib_sample"] = [str(p) for p in sample]
            # Накопительный список выбранных — НЕ сбрасываем на reroll
            # (новые «другие» прибавляются к ранее выбранным).
            if "photo_lib_selected" not in data:
                data["photo_lib_selected"] = []
            _save_pending(pending)

        # Estimate how many photos will actually land in the final video so
        # the preview text is honest about final count (not just "6 previews").
        est_photo_count: int | None = None
        est_avatar_sec: float | None = None
        try:
            proj_dir = _project_dir(data) if data else None
            if proj_dir and proj_dir.exists():
                avatar_files = sorted(
                    proj_dir.glob("avatar_*.mp4"),
                    key=lambda f: f.stat().st_mtime,
                    reverse=True,
                )
                if avatar_files:
                    _probe = subprocess.run(
                        ["ffprobe", "-v", "quiet", "-show_entries",
                         "format=duration", "-of", "csv=p=0", str(avatar_files[0])],
                        capture_output=True, text=True, timeout=10,
                    )
                    est_avatar_sec = float(_probe.stdout.strip())
                    # Must match video_assembler._gather_photo_broll clamp
                    est_photo_count = max(8, min(20, int(round(est_avatar_sec / 2.8))))
        except Exception as e:
            logger.warning(f"photo_lib count estimate failed: {e}")

        try:
            # Codex review #3 P0 (9 May 2026): ExitStack so file
            # descriptors close right after send_media_group acks.
            from contextlib import ExitStack
            from telegram import InputMediaPhoto
            if len(sample) >= 2:
                with ExitStack() as stack:
                    media = [
                        InputMediaPhoto(
                            media=stack.enter_context(open(str(p), "rb"))
                        )
                        for p in sample
                    ]
                    await context.bot.send_media_group(
                        chat_id=query.message.chat_id, media=media,
                    )
            else:
                with open(str(sample[0]), "rb") as f:
                    await context.bot.send_photo(
                        chat_id=query.message.chat_id, photo=f,
                    )
        except Exception as e:
            logger.warning(f"photo_lib preview send failed: {e}")

        cid = card_id[:20]
        seen_count = len(shown_set)
        viewed_hint = (
            f"\n\n👀 Просмотрено превью: *{seen_count}/{len(photos)}*"
            if seen_count > PREVIEW_COUNT else ""
        )
        # Honest final-count line — the preview shows PHOTO_PREVIEW_COUNT photos,
        # but the final video uses 8-20 depending on avatar length.
        if est_photo_count and est_avatar_sec:
            final_line = (
                f"🎬 В финальный ролик пойдёт *~{est_photo_count} фото* "
                f"по *~2.8 сек* на кадр (аватар {est_avatar_sec:.0f}с). "
                f"Порядок рандомный, эффект Ken Burns — медленный зум и панорамирование."
            )
        else:
            final_line = (
                f"🎬 В финальный ролик пойдёт *8–20 фото* по *~2.8 сек* "
                f"на кадр — зависит от длины аватара. Порядок рандомный, "
                f"эффект Ken Burns."
            )
        photo_lib_title = (
            "📸 *Фото-библиотека Максима*"
            if _brand_now_pl == "maksim" else
            "📸 *Фото-библиотека Midjourney*"
        )
        # Клавиатура выбора: 3×3 цифровые toggle + действия снизу.
        # Состояние ✅/⬜ берётся из photo_lib_selected (накопительно через rerolls).
        selected_paths = set(data.get("photo_lib_selected", []) if data else [])
        photo_kb_rows = _build_photo_lib_pick_kb(
            sample_paths=[str(p) for p in sample],
            selected_set=selected_paths,
            full_lib_size=len(photos),
            card_id_short=cid,
        )
        n_selected_total = len(selected_paths)
        select_hint = (
            f"\n\n✅ Выбрано всего: *{n_selected_total}*"
            if n_selected_total else
            "\n\n_Тап по цифре — отметить фото. «➕ Добавить в проект» — копирует выбранные._"
        )
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                f"{photo_lib_title}\n\n"
                f"Всего в библиотеке *{len(photos)}* фото. "
                f"Выше — *{len(sample)} случайных превью*."
                f"{select_hint}{viewed_hint}"
            ),
            parse_mode="Markdown",
            reply_markup=photo_kb_rows,
        )
        return

    # ── Photo library: toggle отдельной фотки ──
    if query.data.startswith("photo_lib_toggle:"):
        try:
            idx = int(query.data.split(":", 1)[1])
        except ValueError:
            await query.answer("Кривой индекс", show_alert=False)
            return
        sample = (data or {}).get("photo_lib_sample") or []
        if not 0 <= idx < len(sample):
            await query.answer("Это превью устарело — нажми «Другая выборка»", show_alert=True)
            return
        path_str = sample[idx]
        selected: list[str] = list(data.get("photo_lib_selected") or [])
        if path_str in selected:
            selected.remove(path_str)
            answer_msg = f"⬜ #{idx+1} убрано"
        else:
            selected.append(path_str)
            answer_msg = f"✅ #{idx+1} выбрано"
        data["photo_lib_selected"] = selected
        _save_pending(pending)
        # Перерисуем клавиатуру (текст не трогаем — Telegram быстрее работает).
        try:
            photos_full = _list_brand_photo_library(_get_active_brand_name())
            card_id = data.get("notion_page_id", "")[:20]
            new_kb = _build_photo_lib_pick_kb(
                sample_paths=sample,
                selected_set=set(selected),
                full_lib_size=len(photos_full),
                card_id_short=card_id,
            )
            await query.edit_message_reply_markup(reply_markup=new_kb)
        except Exception as e:
            logger.debug(f"[photo_lib_toggle] edit_markup skipped: {e}")
        await query.answer(answer_msg, show_alert=False)
        return

    # ── Photo library: добавить выбранные в проект ──
    if query.data == "photo_lib_add":
        proj = _project_dir(data) if data else None
        if not proj:
            await query.answer("Нет активной карточки", show_alert=True)
            return
        selected = list((data or {}).get("photo_lib_selected") or [])
        if not selected:
            await query.answer("Ни одна фотография не выбрана. Тапни цифру.", show_alert=True)
            return
        photos_dir = proj / "photos"
        photos_dir.mkdir(parents=True, exist_ok=True)
        existing_names = sorted(p.name for p in photos_dir.iterdir() if p.is_file())
        import shutil as _sh
        n_added = 0
        n_skipped = 0
        for src_str in selected:
            src = Path(src_str)
            if not src.exists():
                n_skipped += 1
                continue
            ext = src.suffix.lower() if src.suffix else ".jpg"
            next_n = 1
            while f"ready_{next_n:02d}{ext}" in existing_names:
                next_n += 1
            dest = photos_dir / f"ready_{next_n:02d}{ext}"
            try:
                _sh.copy2(str(src), str(dest))
                existing_names.append(dest.name)
                n_added += 1
            except Exception as e:
                logger.warning(f"[photo_lib_add] copy failed: {e}")
                n_skipped += 1
        # Чистим состояние выбора — следующий заход начинается с нуля.
        data["photo_lib_selected"] = []
        data["photo_lib_sample"] = []
        _save_pending(pending)
        cid_short = (data.get("notion_page_id") or "")[:20]
        skipped_note = f" ({n_skipped} пропущено)" if n_skipped else ""
        await query.edit_message_text(
            f"✅ Добавлено в проект: *{n_added}* фото{skipped_note}.\n\n"
            "Теперь можно двигаться дальше — озвучить, сгенерировать аватар "
            "или собрать ролик. Все добавленные фото лежат в `projects/<id>/photos/` "
            "и пойдут в Smart-mix / Pro-монтаж как B-roll.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ К меню B-roll", callback_data="broll")],
                [InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{cid_short}")],
            ]),
        )
        return

    if query.data == "broll_local_lib":
        # Show local B-roll library categories matched against script.
        # Brand-aware: maksim → clips/maksim/<cat>, default → broll-library/<cat>.
        if not BROLL_LIBRARY_DIR.exists():
            await query.edit_message_text(
                "📚 Локальная библиотека не найдена на этом сервере.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ Назад", callback_data="broll")],
                ]),
            )
            return

        _brand_now_cl = _get_active_brand_name()
        script_text = data.get("script", "") or ""
        title_text = data.get("card_data", {}).get("title", "") or ""
        combined = f"{title_text} {script_text}"

        # Brand-aware keyword match (maksim → MAKSIM_CLIP_KEYWORDS, иначе AI-словарь)
        matched = _match_brand_clip_categories(_brand_now_cl, combined)

        # Brand-aware список категорий с непустыми клипами
        brand_lib = _list_brand_clip_library(_brand_now_cl)
        all_categories = [(cat, len(clips), cat in matched) for cat, clips in brand_lib]

        if not all_categories:
            empty_hint = (
                "📚 Библиотека пуста.\n\n"
                "Загрузи клипы через «📥 Готовые материалы» — выбери категорию "
                "перед загрузкой, и они автоматом попадут в библиотеку Максима."
                if _brand_now_cl == "maksim" else
                "📚 Библиотека пуста."
            )
            await query.edit_message_text(
                empty_hint,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ Назад", callback_data="broll")],
                ]),
            )
            return

        # Sort: matched first, then by clip count
        all_categories.sort(key=lambda x: (not x[2], -x[1]))

        # Brand-aware подписи: для maksim — русские названия и тематические эмодзи
        default_cat_emoji = {
            "robots": "🤖", "ai-tools": "💬", "tech-general": "💻",
            "social-media": "📱", "space": "🚀", "medical": "🏥", "ai-video": "🎞",
            "apps": "📲", "payments": "💳",
        }
        buttons = []
        for category, n_clips, is_matched in all_categories:
            if _brand_now_cl == "maksim" and category in MAKSIM_CATEGORY_UI:
                emoji, ru_name = MAKSIM_CATEGORY_UI[category]
                label = f"{emoji} {ru_name} ({n_clips})"
            else:
                emoji = default_cat_emoji.get(category, "📁")
                label = f"{emoji} {category} ({n_clips})"
            if is_matched:
                label = "⭐ " + label
            buttons.append([InlineKeyboardButton(label, callback_data=f"broll_lib_cat:{category}")])
        buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="broll")])

        header = "📚 Моя библиотека клипов\n\n"
        if matched:
            header += f"⭐ — подходит под сценарий ({', '.join(matched)})\n\n"
        header += "Выбери категорию:"

        await query.edit_message_text(
            header,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if query.data.startswith("broll_lib_cat:"):
        category = query.data.split(":", 1)[1]
        _brand_now_lc = _get_active_brand_name()
        if _brand_now_lc == "maksim":
            cat_dir = BROLL_LIBRARY_DIR / "clips" / "maksim" / category
        else:
            cat_dir = BROLL_LIBRARY_DIR / category
        if not cat_dir.exists():
            await query.edit_message_text(
                f"Категория {category} не найдена.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ Назад", callback_data="broll_local_lib")],
                ]),
            )
            return

        # Build clip list for this category — limit CLIP_PREVIEW_COUNT (10),
        # иначе чат заваливается длинной полосой send_video.
        # Принимаем все видео-расширения VIDEO_LIB_EXTS (.mp4/.mov/.webm),
        # иначе расходится с меню «Моя библиотека клипов» которое тоже их считает —
        # симптом: кнопка категории показывает N, в категории «0 клипов из 0».
        import random as _random
        all_clips = [
            p for p in cat_dir.iterdir()
            if p.is_file()
            and p.suffix.lower() in VIDEO_LIB_EXTS
            and p.stat().st_size > 1000
        ]
        _random.shuffle(all_clips)
        show_clips = all_clips[:CLIP_PREVIEW_COUNT]

        new_clip_dicts = [
            {
                "id": f"local_{p.stem}",
                "source": "local",
                "path": str(p),
                "filename": p.name,
                "category": category,
                "duration": 5,
                "width": 1280,
                "height": 720,
                "tags": f"{category} {p.stem.replace('_', ' ')}",
                "url": "",
            }
            for p in show_clips
        ]

        # Merge into existing broll_clips (preserve previously selected indices)
        existing = data.get("broll_clips", [])
        existing_ids = {c.get("id") for c in existing}
        for c in new_clip_dicts:
            if c["id"] not in existing_ids:
                existing.append(c)
        data["broll_clips"] = existing
        if "broll_selected" not in data:
            data["broll_selected"] = []
        _save_pending(pending)

        await query.edit_message_text(
            f"📚 Категория «{category}»: показываю {len(show_clips)} клипов из {len(all_clips)}."
        )

        for c in new_clip_dicts:
            try:
                idx = next(i for i, x in enumerate(data["broll_clips"]) if x.get("id") == c["id"])
                is_selected = idx in data.get("broll_selected", [])
                label = f"✅ Выбран #{idx+1} ✓" if is_selected else f"✅ Выбрать #{idx+1}"
                select_btn = InlineKeyboardMarkup([
                    [InlineKeyboardButton(label, callback_data=f"broll_select:{idx}")]
                ])
                clip_path = Path(c["path"])
                with open(clip_path, "rb") as f:
                    await context.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=f,
                        caption=f"#{idx+1} | {category}: {c['filename']}",
                        supports_streaming=True,
                        reply_markup=select_btn,
                    )
            except Exception as e:
                logger.warning(f"Failed to send local clip {c['filename']}: {e}")

        # Final action panel — со счётчиком выбранных всего (по всему пулу,
        # а не только текущей выборке — Артём может смешивать категории).
        buttons = [
            [InlineKeyboardButton("💾 Сохранить выбранные в Notion", callback_data="broll_approve")],
            [InlineKeyboardButton("📚 Другая категория", callback_data="broll_local_lib")],
            [InlineKeyboardButton("🔀 Обновить выборку", callback_data=f"broll_lib_cat:{category}")],
            [InlineKeyboardButton("◀️ К меню B-roll", callback_data="broll")],
        ]
        n_selected_total = len(data.get("broll_selected", []) or [])
        footer_text = (
            "Нажми «Выбрать» под нужными, потом «Сохранить».\n\n"
            f"✅ Выбрано всего: *{n_selected_total}* клипов"
            if n_selected_total else
            "Нажми «Выбрать» под нужными, потом «Сохранить»."
        )
        footer_msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=footer_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        # Сохраняем id footer-сообщения для live-обновления счётчика при select
        data["broll_lib_footer_msg_id"] = footer_msg.message_id
        data["broll_lib_footer_chat_id"] = query.message.chat_id
        data["broll_lib_footer_category"] = category
        _save_pending(pending)
        return

    if query.data == "broll_shooting_list":
        # Generate personal shooting list using Opus
        script_text = data.get("script", "")
        if not script_text:
            await query.edit_message_text("Нет сценария. Сначала создай сценарий.")
            return

        await query.edit_message_text("📋 Составляю список съёмки по сценарию (Opus)...")

        try:
            # Get voice parts duration info if available
            parts_info = ""
            voice_parts = data.get("voice_parts", [])
            if voice_parts:
                parts_info = f"\n\nСценарий разбит на {len(voice_parts)} части для озвучки:\n"
                for i, p in enumerate(voice_parts):
                    parts_info += f"Часть {i+1}: «{p}»\n"

            shooting_prompt = (
                "Ты — режиссёр монтажа коротких вертикальных роликов (Reels/Shorts/TikTok).\n\n"
                "Задача: по сценарию составить КОМПАКТНЫЙ список B-roll блоков, которые автор должен снять или записать с экрана.\n\n"
                "Автор — предприниматель, снимает от первого лица, один, без команды. Это личный бренд, НЕ стоковый контент.\n\n"
                "ГЛАВНОЕ ПРАВИЛО: максимум 4-6 блоков на весь ролик. НЕ делай посекундную раскадровку — автор не продакшн-студия.\n"
                "Группируй похожие моменты в один блок. Например, если в сценарии 3 раза упоминаются соцсети — это один блок «скринкаст соцсетей».\n\n"
                "ПРАВИЛА:\n"
                "- 4-6 блоков, не больше. Каждый блок = один тип съёмки, который покроет несколько моментов сценария\n"
                "- Для каждого блока: примерный таймкод (диапазон), что снять, и где в сценарии это будет использовано\n"
                "- Типы: скринкаст (запись экрана), камера (крупный/средний/общий план), таймлапс, фото/скрин\n"
                "- Будь КОНКРЕТЕН: не 'запись экрана', а 'скринкаст: открываешь Instagram, показываешь профиль с подписчиками'\n"
                "- Автор — мужчина. В кадре только мужчина или нейтральные объекты\n"
                "- Первый кадр (хук) — самый важный, выдели его отдельно\n\n"
                "ФОРМАТ ОТВЕТА:\n"
                "🎬 ХУК (0:00-0:03)\n"
                "Кадр: конкретное описание\n"
                "Момент сценария: «цитата»\n\n"
                "📹 БЛОК 1 — название (0:03-0:12)\n"
                "Кадр: что снять\n"
                "Покрывает моменты: «цитата 1», «цитата 2»\n\n"
                "...и так далее\n\n"
                "В конце:\n"
                "📝 ЧТО ПОДГОТОВИТЬ ЗАРАНЕЕ:\n"
                "- короткий список (3-5 пунктов)\n"
            )

            response = claude.messages.create(
                model="claude-opus-4-7",
                max_tokens=2048,
                system=shooting_prompt,
                messages=[
                    {"role": "user", "content": f"Сценарий:\n\n{script_text}{parts_info}"},
                ],
            )
            shooting_list = response.content[0].text.strip()

            # Save to project
            _save_text_to_project(data, "shooting_list.txt", shooting_list)
            data["shooting_list"] = shooting_list
            _save_pending(pending)

            # Send as message (may be long)
            header = "📋 **СПИСОК СЪЁМКИ**\n\n"
            full_text = header + shooting_list

            # Telegram limit is 4096 chars
            if len(full_text) <= 4096:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=full_text,
                )
            else:
                # Split into chunks
                for i in range(0, len(full_text), 4000):
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text=full_text[i:i+4000],
                    )

            buttons = [
                [InlineKeyboardButton("🔄 Перегенерировать", callback_data="broll_shooting_list")],
                [InlineKeyboardButton("📥 Скачать материалы", callback_data="download_project")],
                [InlineKeyboardButton("✅ Готово", callback_data="finish")],
            ]
            await query.edit_message_text(
                "✅ Список съёмки готов и сохранён в проект.",
                reply_markup=InlineKeyboardMarkup(buttons),
            )

        except Exception as e:
            logger.error(f"Shooting list error: {e}", exc_info=True)
            buttons = [
                [InlineKeyboardButton("🔄 Попробовать снова", callback_data="broll_shooting_list")],
                [InlineKeyboardButton("✅ Готово", callback_data="finish")],
            ]
            await query.edit_message_text(
                f"❌ Ошибка: {e}",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        return

    if query.data == "broll_stock":
        # Force stock search (skip YouTube URLs)
        script_text = data.get("script", "")
        await query.edit_message_text("🎬 Ищу подходящие видео на стоках...")
        try:
            shotlist = await asyncio.to_thread(generate_shotlist, script_text)
            shotlist = await find_broll_for_shotlist(shotlist)
            data["shotlist"] = shotlist

            all_broll_videos = []
            seen_clip_ids = set()
            for shot in shotlist:
                for v in shot.get("videos", []):
                    if v["id"] not in seen_clip_ids:
                        seen_clip_ids.add(v["id"])
                        all_broll_videos.append(v)

            data["broll_clips"] = all_broll_videos
            data["broll_selected"] = []
            _save_pending(pending)

            await query.edit_message_text(
                f"📊 Найдено {len(all_broll_videos)} клипов со стоков — выбери подходящие",
            )

            for idx, v in enumerate(all_broll_videos):
                try:
                    select_btn = InlineKeyboardMarkup([
                        [InlineKeyboardButton(f"✅ Выбрать #{idx+1}", callback_data=f"broll_select:{idx}")]
                    ])
                    if v.get("source") == "local":
                        clip_path = Path(v["path"])
                        if clip_path.exists():
                            with open(clip_path, "rb") as f:
                                await context.bot.send_video(
                                    chat_id=query.message.chat_id, video=f,
                                    caption=f"#{idx+1} | {v.get('category', '')}: {v['filename']}",
                                    supports_streaming=True, reply_markup=select_btn,
                                )
                    else:
                        await context.bot.send_message(
                            chat_id=query.message.chat_id,
                            text=f"#{idx+1} | [{v['source']}] {v.get('tags', '')}\n{v['url']}",
                            disable_web_page_preview=False, reply_markup=select_btn,
                        )
                except Exception as e:
                    logger.warning(f"Failed to send clip #{idx+1}: {e}")

            buttons = [
                [InlineKeyboardButton("💾 Сохранить выбранные в Notion", callback_data="broll_approve")],
                [InlineKeyboardButton("🔍 Найти B-roll на YouTube", callback_data="broll_yt_search")],
                [InlineKeyboardButton("🎬 Нарезать из видео", callback_data="broll_youtube")],
                [InlineKeyboardButton("🔄 Подобрать другие", callback_data="broll_stock")],
            ]
            if elevenlabs_client and not data.get("voice_parts"):
                buttons.append([InlineKeyboardButton("🎙 Озвучить", callback_data="voiceover_choose")])
            buttons.append([InlineKeyboardButton("✅ Готово", callback_data="finish")])

            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="Нажми «Выбрать» под понравившимися клипами, затем «Сохранить».",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception as e:
            logger.error(f"Stock B-roll error: {e}", exc_info=True)
            await query.edit_message_text(f"Ошибка поиска: {e}")
        return

    if query.data == "broll_youtube":
        # Ask user to send YouTube URL or video file
        data["state"] = "broll_youtube_input"
        _save_pending(pending)
        await query.edit_message_text(
            "🎬 Отправь видео для нарезки на клипы:\n\n"
            "📎 Видеофайл — отправь прямо в чат\n"
            "🐦 Ссылку на твит — twitter.com, x.com или nitter\n"
            "🔗 Ссылку — YouTube, Vimeo, или любой сайт со встроенным видео"
        )
        return

    if query.data == "broll_ready":
        # "Ready materials" mode — client sends photos/videos directly,
        # bot saves them into the project folder untouched (videos may be
        # trimmed per brand, see BRANDS[brand]["auto_trim_video_sec"]).
        # Different from broll_youtube: no 5-sec ffmpeg chopping, no YouTube
        # download — the client's own footage is the end material.
        #
        # 25 May 2026: для бренда maksim добавлены кнопки-фильтры категорий
        # библиотеки — гибрид (вариант В): дефолт = personal, опт-аут возможен.
        # Каждый загруженный фото/видео идёт И в карточку, И в общую библиотеку
        # под выбранную категорию (если не выбран опт-аут).
        data["state"] = "broll_ready_material"
        _save_pending(pending)
        _brand_now = _get_active_brand_name()
        _brand_cfg = _get_active_brand()
        trim_sec = _brand_cfg.get("auto_trim_video_sec")
        trim_note = (
            f"🎬 Видео длиннее {trim_sec}с будут автоматически обрезаны "
            f"до {trim_sec}с (с начала). Оригинал сохранится в _raw_uploads/ "
            f"и пойдёт в библиотеку целиком.\n\n"
            if trim_sec else
            "🎬 Видео сохраняются целиком.\n\n"
        )
        if _brand_now == "maksim":
            current_cat = data.get("broll_lib_category") or _MAKSIM_READY_LIB_DEFAULT
            cat_label = _maksim_ready_category_label(current_cat)
            await query.edit_message_text(
                f"📥 *Готовые материалы* (бренд: *{_brand_now}*)\n\n"
                f"📂 Категория в библиотеке: *{cat_label}*\n"
                f"_(нажми ниже, чтобы сменить — выбор живёт до конца этого режима)_\n\n"
                f"Скинь сюда **фото и/или видео** по одному или пачкой. "
                f"Каждый материал пойдёт И в карточку, И в общую библиотеку под "
                f"выбранную категорию (✋ — только в карточку).\n\n"
                f"{trim_note}"
                f"Когда закончишь — нажми «✅ Готово».",
                parse_mode="Markdown",
                reply_markup=_maksim_ready_kb(current_cat),
            )
        else:
            await query.edit_message_text(
                f"📥 Готовые материалы (бренд: *{_brand_now}*)\n\n"
                "Скинь сюда **фото и/или видео** по одному или пачкой — сохраню в проект.\n\n"
                f"📸 Фото → в `projects/<id>/photos/`\n"
                f"{trim_note}"
                "Когда закончишь — нажми «✅ Готово».",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Готово", callback_data="broll_ready_done")],
                    [InlineKeyboardButton("◀️ Назад к B-roll", callback_data="broll")],
                ]),
            )
        return

    if query.data.startswith("broll_ready_cat:"):
        # Выбор категории библиотеки в режиме «Готовые материалы».
        # Сохраняем в pending — действует до выхода из режима (broll_ready_done).
        chosen = query.data.split(":", 1)[1]
        valid = set(_MAKSIM_READY_LIB_BUTTONS) | {_MAKSIM_READY_LIB_SKIP}
        if chosen not in valid:
            await query.answer("Неизвестная категория", show_alert=False)
            return
        data["broll_lib_category"] = chosen
        _save_pending(pending)
        cat_label = _maksim_ready_category_label(chosen)
        # Перерисовываем шапку с новой подписью + клавиатуру с галкой
        _brand_cfg = _get_active_brand()
        trim_sec = _brand_cfg.get("auto_trim_video_sec")
        trim_note = (
            f"🎬 Видео длиннее {trim_sec}с будут обрезаны до {trim_sec}с; "
            f"оригинал пойдёт в библиотеку целиком.\n\n"
            if trim_sec else
            "🎬 Видео сохраняются целиком.\n\n"
        )
        try:
            await query.edit_message_text(
                f"📥 *Готовые материалы* (бренд: *maksim*)\n\n"
                f"📂 Категория в библиотеке: *{cat_label}*\n\n"
                f"Скинь фото и/или видео. {trim_note}"
                f"Когда закончишь — «✅ Готово».",
                parse_mode="Markdown",
                reply_markup=_maksim_ready_kb(chosen),
            )
        except Exception as e:
            # Edit может упасть если сообщение совпадает — это OK
            logger.debug(f"[broll_ready_cat] edit skipped: {e}")
        await query.answer(f"✓ {cat_label}", show_alert=False)
        return

    if query.data == "broll_ready_done":
        # Exit the "ready materials" mode — back to the B-roll menu.
        if data and data.get("state") == "broll_ready_material":
            data["state"] = None
            _save_pending(pending)
        # Recount what we saved so the user gets a summary
        proj = _project_dir(data) if data else None
        ph_count = 0
        vid_count = 0
        if proj and proj.exists():
            photos_dir = proj / "photos"
            if photos_dir.exists():
                ph_count = sum(
                    1 for p in photos_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
                )
            vid_count = len(list(proj.glob("broll_*.mp4")))
        await query.edit_message_text(
            f"✅ Режим «Готовые материалы» закрыт.\n\n"
            f"📸 Фото в проекте: {ph_count}\n"
            f"🎬 Видео (broll_*.mp4): {vid_count}\n\n"
            "Теперь можно озвучивать, генерировать аватар и собирать Смарт-микс.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{(data or {}).get('notion_page_id', '')[:20]}")],
            ]),
        )
        return

    # ── Manage saved B-roll clips ──
    if query.data.startswith("broll_manage:"):
        card_id_prefix = query.data.split(":", 1)[1]
        # Find project dir
        _tmp_proj = None
        for d in PROJECTS_DIR.iterdir():
            if d.is_dir() and d.name.startswith(card_id_prefix[:8]):
                _tmp_proj = d
                break
        if not _tmp_proj:
            await query.edit_message_text("Папка проекта не найдена.")
            return

        broll_files = sorted(_tmp_proj.glob("broll_*.mp4"), key=lambda f: f.name)
        if not broll_files:
            await query.edit_message_text("Нет сохранённых B-roll клипов.")
            return

        # Send each clip as video with a remove button
        await query.edit_message_text(f"📋 Сохранённых B-roll: {len(broll_files)}\nОтправляю клипы для просмотра...")

        for i, clip in enumerate(broll_files):
            try:
                dur = 0
                try:
                    probe_cmd = ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(clip)]
                    dur = float(subprocess.run(probe_cmd, capture_output=True, text=True, timeout=5).stdout.strip())
                except Exception:
                    pass
                size_mb = clip.stat().st_size / 1024 / 1024
                caption = f"#{i+1} | {clip.name} | {dur:.1f}с | {size_mb:.1f}MB"
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"🗑 Удалить #{i+1}", callback_data=f"broll_rm:{card_id_prefix}:{clip.name}")]
                ])
                with open(clip, "rb") as f:
                    await context.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=f,
                        caption=caption,
                        reply_markup=kb,
                        read_timeout=60, write_timeout=60,
                    )
            except Exception as e:
                logger.warning(f"Не удалось отправить B-roll {clip.name}: {e}")

        # Summary with actions
        remaining = len(broll_files)
        summary_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑 Удалить все B-roll", callback_data=f"broll_rm_all:{card_id_prefix}")],
            [InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{card_id_prefix}")],
        ])
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"📋 Итого: {remaining} клипов B-roll.\n\nУдали ненужные кнопками выше, или нажми «К карточке» чтобы вернуться.",
            reply_markup=summary_kb,
        )
        return

    if query.data.startswith("broll_rm:"):
        # Remove a single B-roll clip: broll_rm:<card_prefix>:<filename>
        parts = query.data.split(":", 2)
        card_id_prefix = parts[1]
        clip_name = parts[2]

        _tmp_proj = None
        for d in PROJECTS_DIR.iterdir():
            if d.is_dir() and d.name.startswith(card_id_prefix[:8]):
                _tmp_proj = d
                break

        if _tmp_proj:
            clip_path = _tmp_proj / clip_name
            if clip_path.exists():
                clip_path.unlink()
                remaining = len(list(_tmp_proj.glob("broll_*.mp4")))
                await query.edit_message_caption(
                    caption=f"🗑 Удалён: {clip_name}\n(осталось {remaining} клипов)"
                )
            else:
                await query.answer("Файл уже удалён")
        else:
            await query.answer("Папка проекта не найдена")
        return

    if query.data.startswith("broll_rm_all:"):
        card_id_prefix = query.data.split(":", 1)[1]
        _tmp_proj = None
        for d in PROJECTS_DIR.iterdir():
            if d.is_dir() and d.name.startswith(card_id_prefix[:8]):
                _tmp_proj = d
                break
        if _tmp_proj:
            removed = 0
            for clip in _tmp_proj.glob("broll_*.mp4"):
                clip.unlink()
                removed += 1
            data["broll_approved"] = False
            data["broll_selected"] = []
            _save_pending(pending)
            await query.edit_message_text(f"🗑 Удалены все {removed} клипов B-roll.\nМожешь выбрать новые.")
        return

    if effective_action == "broll_approve":
        # Save selected B-roll clips to Notion card
        notion_page_id = data.get("notion_page_id")
        clips = data.get("broll_clips", [])
        selected = data.get("broll_selected", [])

        if not notion_page_id:
            await query.edit_message_text("Нет привязанной карточки Notion. Сначала создай карточку.")
            return

        if not selected:
            await query.answer("Сначала выбери клипы кнопкой «Выбрать»")
            return

        await query.edit_message_text(f"📋 Сохраняю {len(selected)} выбранных клипов...")
        try:
            # Save clip files to project folder and build Notion text
            broll_lines = []
            youtube_sources = set()
            saved_count = 0
            for idx in sorted(selected):
                if idx < len(clips):
                    v = clips[idx]
                    clip_name = f"broll_{idx+1}.mp4"
                    # Copy clip file to project folder
                    clip_path = v.get("path", "")
                    if clip_path and Path(clip_path).exists():
                        _save_to_project(data, clip_name, clip_path)
                        saved_count += 1
                    if v.get("source") == "youtube":
                        timecode = v.get("timecode", "")
                        broll_lines.append(f"🎬 {clip_name} — таймкод {timecode}")
                        if v.get("url"):
                            youtube_sources.add(v["url"])
                    elif v.get("source") == "local":
                        broll_lines.append(f"📁 {clip_name} — {v.get('category', '')}/{v.get('filename', '')}")
                    else:
                        broll_lines.append(f"📎 {clip_name} — {v.get('url', '')}")

            # Add YouTube source URLs once (not per clip)
            if youtube_sources:
                broll_lines.append("")
                broll_lines.append("Источник:")
                for yt_url in youtube_sources:
                    broll_lines.append(f"🔗 {yt_url}")

            broll_text = "\n".join(broll_lines)

            # Append B-roll block to Notion page
            await asyncio.to_thread(
                notion.blocks.children.append,
                block_id=notion_page_id,
                children=[
                    {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": "B-roll"}}]}},
                    {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": broll_text[:2000]}}]}},
                ],
            )

            data["broll_approved"] = True
            _save_pending(pending)

            # Save B-roll info to project folder
            _save_text_to_project(data, "broll_links.txt", broll_text)

            buttons = []
            if (elevenlabs_client or (FISH_API_KEY and FISH_VOICE_ID)) and not data.get("voice_parts"):
                buttons.append([InlineKeyboardButton("🎙 Озвучить", callback_data="voiceover_choose")])
            card_id_prefix = data.get("notion_page_id", "")[:20]
            if card_id_prefix:
                buttons.append([InlineKeyboardButton(f"📋 Управление B-roll ({saved_count} клипов)", callback_data=f"broll_manage:{card_id_prefix}")])
            buttons.append([InlineKeyboardButton("✅ Готово", callback_data="finish")])

            await query.edit_message_text(
                f"✅ {len(selected)} клипов B-roll сохранены в Notion!\n📂 {saved_count} видеофайлов в папке проекта.\n\nЧто дальше?",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception as e:
            logger.error(f"Ошибка сохранения B-roll: {e}", exc_info=True)
            await query.edit_message_text(f"Ошибка: {e}")
        return

    if effective_action == "create_guide":
        # Submenu: view / regenerate / paste URL / paste text / back
        existing_url = data.get("guide_url", "")
        buttons = []
        if existing_url:
            buttons.append([InlineKeyboardButton("👁 Открыть текущий гайд", url=existing_url)])
            buttons.append([InlineKeyboardButton("🔄 Сгенерировать заново (AI)", callback_data="guide_generate")])
            buttons.append([InlineKeyboardButton("🔗 Заменить ссылкой на Notion", callback_data="guide_set_url")])
            buttons.append([InlineKeyboardButton("📝 Вставить свой текст", callback_data="guide_set_text")])
        else:
            buttons.append([InlineKeyboardButton("🤖 Сгенерировать AI", callback_data="guide_generate")])
            buttons.append([InlineKeyboardButton("🔗 Вставить ссылку на Notion", callback_data="guide_set_url")])
            buttons.append([InlineKeyboardButton("📝 Вставить свой текст", callback_data="guide_set_text")])
        buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="guide_back")])

        header = "📎 Гайд для подписчиков\n\n"
        if existing_url:
            header += f"Текущий: {existing_url}\n\nЧто сделать?"
        else:
            header += "Гайд ещё не создан. Выбери способ:"
        await query.edit_message_text(header, reply_markup=InlineKeyboardMarkup(buttons))
        return

    if query.data == "guide_generate":
        script_text = data.get("script", "")
        notion_page_id = data.get("notion_page_id")
        title = data.get("card_data", {}).get("title", "Гайд")

        await query.edit_message_text("📎 Генерирую гайд для подписчиков...")
        try:
            guide_url = await asyncio.to_thread(create_guide_page, script_text, title)

            if notion_page_id:
                await asyncio.to_thread(add_guide_link_to_card, notion_page_id, guide_url)

            data["guide_url"] = guide_url
            data["guide_created"] = True
            _save_pending(pending)

            buttons = []
            buttons.append([InlineKeyboardButton("👁 Открыть гайд", url=guide_url)])
            buttons.append([InlineKeyboardButton("🔄 Переписать с правками", callback_data="guide_rewrite")])
            buttons.append([InlineKeyboardButton("◀️ Меню гайда", callback_data="create_guide")])
            if PEXELS_API_KEY or PIXABAY_API_KEY:
                buttons.append([InlineKeyboardButton("🎬 Видеоряд (B-roll)", callback_data="broll")])
            if elevenlabs_client:
                buttons.append([InlineKeyboardButton("🎙 Озвучить", callback_data="voiceover_choose")])
            buttons.append([InlineKeyboardButton("✅ Готово", callback_data="finish")])

            notion_url = data.get("notion_url", "")
            await query.edit_message_text(
                f"✅ Гайд сгенерирован!\n\n"
                f"📋 Notion: {notion_url}\n"
                f"📎 Гайд: {guide_url}\n\n"
                f"Ссылка добавлена в карточку Notion.",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception as e:
            logger.error(f"Ошибка создания гайда: {e}", exc_info=True)
            buttons = [
                [InlineKeyboardButton("📎 Попробовать снова", callback_data="guide_generate")],
                [InlineKeyboardButton("◀️ Меню гайда", callback_data="create_guide")],
            ]
            await query.edit_message_text(
                f"Ошибка создания гайда: {e}\n\nМожно попробовать снова.",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        return

    if query.data == "guide_set_url":
        data["state"] = "guide_waiting_url"
        _save_pending(pending)
        await query.edit_message_text(
            "🔗 Пришли ссылку на готовую страницу Notion\n\n"
            "Просто отправь URL сообщением. Я добавлю эту ссылку в карточку "
            "вместо автогенерации. Формат: https://...notion.site/... или https://www.notion.so/...",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Отмена", callback_data="create_guide")],
            ]),
        )
        return

    if query.data == "guide_set_text":
        data["state"] = "guide_waiting_text"
        _save_pending(pending)
        await query.edit_message_text(
            "📝 Пришли текст гайда сообщением\n\n"
            "Я создам страницу в Notion с этим текстом. Поддерживается простая разметка:\n"
            "• `# Заголовок` — заголовок\n"
            "• `- пункт` — маркированный список\n"
            "• `1. пункт` — нумерованный список\n"
            "• пустая строка — разделитель абзацев\n\n"
            "Блок «Об авторе» добавится автоматически в конце.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Отмена", callback_data="create_guide")],
            ]),
        )
        return

    if query.data == "guide_rewrite":
        # Ask user for feedback on the guide
        data["state"] = "guide_feedback"
        _save_pending(pending)
        await query.edit_message_text(
            "✏️ Как переписать гайд?\n\n"
            "Отправь текстом или голосовым сообщением, что изменить. Например:\n"
            "• «Добавь раздел про промпты»\n"
            "• «Сделай короче, убери лишнее»\n"
            "• «Перепиши в более дружелюбном тоне»\n\n"
            "Или нажми кнопку ниже, чтобы сгенерировать заново с нуля.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Сгенерировать заново", callback_data="create_guide")],
                [InlineKeyboardButton("◀️ Назад", callback_data="guide_back")],
            ]),
        )
        return

    if query.data == "guide_back":
        guide_url = data.get("guide_url", "")
        notion_url = data.get("notion_url", "")
        buttons = []
        buttons.append([InlineKeyboardButton("🔄 Переписать гайд", callback_data="guide_rewrite")])
        if elevenlabs_client and not data.get("voice_parts"):
            buttons.append([InlineKeyboardButton("🎙 Озвучить", callback_data="voiceover_choose")])
        if not data.get("broll_approved"):
            buttons.append([InlineKeyboardButton("🎬 Видеоряд (B-roll)", callback_data="broll")])
        buttons.append([InlineKeyboardButton("✅ Готово", callback_data="finish")])
        await query.edit_message_text(
            f"📎 Гайд: {guide_url}\n📋 Notion: {notion_url}\n\nЧто дальше?",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    # --- HeyGen avatar generation ---
    if query.data == "heygen_looks" or effective_action == "heygen_looks":
        # Check if we have voice parts — try card directory first, then assets/
        parts = data.get("voice_parts", []) if data else []
        notion_id = data.get("notion_page_id") if data else None

        # Try to restore from card voice directory if pending has no voice parts
        if not parts and notion_id:
            voice_meta = _load_voice_meta(notion_id)
            if voice_meta:
                parts = voice_meta.get("voice_parts", [])
                if parts:
                    data["voice_parts"] = parts
                    data["voice_approved"] = voice_meta.get("voice_approved", [False] * len(parts))
                    _save_pending(pending)

        # Check voice files — card directory first, then assets/
        if notion_id and parts:
            voice_files = _get_voice_files(notion_id, len(parts))
        else:
            voice_files = [ASSETS_DIR / f"voice_part_{i}.mp3" for i in range(len(parts))]
        has_voice = parts and any(f.exists() for f in voice_files)

        if not has_voice:
            buttons = [[InlineKeyboardButton("🎙 Сначала озвучить", callback_data="voiceover")]]
            if data and not data.get("script"):
                buttons = [[InlineKeyboardButton("◀️ Назад", callback_data="notion_back")]]
                await query.edit_message_text("Нет сценария. Сначала озвучь через /cards → Озвучить.", reply_markup=InlineKeyboardMarkup(buttons))
            else:
                await query.edit_message_text("⚠️ Для аватара нужна озвучка.\nСначала озвучь сценарий.", reply_markup=InlineKeyboardMarkup(buttons))
            return

        # Show avatar look selection — brand-aware:
        # if the active brand has its own heygen_looks, show ONLY those.
        # Otherwise fall back to the global HEYGEN_LOOKS (Artem's faces).
        _brand_now = _get_active_brand_name()
        _brand_looks = _get_active_brand().get("heygen_looks") or {}

        buttons = []
        # New (4 мая 2026): on-demand photo avatar — пользователь скидывает
        # фото, бот регистрирует его через HeyGen v3 как persistent avatar_id
        # и использует для этого ролика. Полезно когда нужен «новый образ
        # модели на каждый ролик» (shoes-кейс) и нет времени лезть в HeyGen
        # Studio. Fallback на Image-to-Video если упрёмся в лимит avatars.
        buttons.append([InlineKeyboardButton(
            "📸 Создать аватар из фото",
            callback_data="heygen_photo_register",
        )])
        if _brand_looks:
            for key, look in _brand_looks.items():
                buttons.append([InlineKeyboardButton(
                    f"👤 {look['name']}",
                    callback_data=f"heygen_gen:{key}",
                )])
        else:
            for key, look in HEYGEN_LOOKS.items():
                buttons.append([InlineKeyboardButton(
                    f"👤 {look['name']}",
                    callback_data=f"heygen_gen:{key}",
                )])
        # Reuse previously uploaded custom photo from this card (if any).
        # На 4 мая 2026 хранится photo_url (для Image-to-Video), а не
        # avatar_id (регистрация заменена — упирается в лимит). Старое поле
        # heygen_custom_avatar_id оставлено для backward compat.
        _custom_photo = data and data.get("heygen_custom_photo_url")
        _custom_id = data and data.get("heygen_custom_avatar_id")
        if _custom_photo:
            buttons.append([InlineKeyboardButton(
                f"📸 Использовать загруженное фото",
                callback_data="heygen_gen:__custom__",
            )])
        elif _custom_id:
            buttons.append([InlineKeyboardButton(
                f"📸 Использовать загруженный (id ...{_custom_id[-6:]})",
                callback_data="heygen_gen:__custom__",
            )])
        buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="finish_menu")])

        quota = 0
        try:
            quota = await asyncio.to_thread(heygen_get_quota)
        except Exception:
            pass

        brand_tag = f" [{_brand_now}]" if _brand_now != "default" else ""
        _looks_text = (
            f"🤖 Выбери лук аватара для генерации{brand_tag}:\n\n"
            f"💰 Баланс HeyGen: {quota} кредитов"
        )
        _looks_kb = InlineKeyboardMarkup(buttons)
        # «Другой лук / версия» приходит с ВИДЕО-сообщения (доставка аватара) —
        # edit_message_text на медиа падает, поэтому шлём новым сообщением.
        try:
            await query.edit_message_text(_looks_text, reply_markup=_looks_kb)
        except Exception:
            await query.get_bot().send_message(
                chat_id=query.message.chat_id, text=_looks_text, reply_markup=_looks_kb,
            )
        return

    # Handler: запрос фото для регистрации custom photo avatar.
    # State machine: heygen_photo_register_waiting → process_photo подхватывает
    # → регистрирует через heygen_register_photo_avatar → сохраняет в data
    # → возвращает пользователя на heygen_gen:__custom__ для выбора версии.
    if query.data == "heygen_photo_register":
        user_id = query.from_user.id
        pending.setdefault(user_id, {})["state"] = "heygen_photo_register_waiting"
        # Сохраняем notion_page_id чтобы process_photo нашёл проект
        if data and data.get("notion_page_id"):
            pending[user_id]["notion_page_id"] = data["notion_page_id"]
        _save_pending(pending)
        await query.edit_message_text(
            "📸 Скинь фото, которое станет аватаром этого ролика.\n\n"
            "**Требования:**\n"
            "• PNG / JPG\n"
            "• Лицо в кадре, анфас, по плечи или по грудь\n"
            "• Без очков, шляп, посторонних людей\n"
            "• Мягкий свет, без жёстких теней\n"
            "• Желательно 9:16 или 1:1\n\n"
            "После загрузки бот зарегистрирует аватар через HeyGen API и "
            "сразу предложит выбрать версию (Avatar 3 / Avatar 4).",
            parse_mode="Markdown",
        )
        return

    if query.data == "finish_menu":
        # Show the standard post-voiceover menu
        buttons = []
        if HEYGEN_API_KEY:
            buttons.append([InlineKeyboardButton("🤖 Сгенерировать аватар", callback_data="heygen_looks")])
        if NOTION_GUIDES_DB and not data.get("guide_created"):
            buttons.append([InlineKeyboardButton("📎 Создать гайд для подписчиков", callback_data="create_guide")])
        if not data.get("broll_approved"):
            buttons.append([InlineKeyboardButton("🎬 Подобрать B-roll", callback_data="broll")])
        buttons.append([InlineKeyboardButton("✅ Готово", callback_data="finish")])
        await query.edit_message_text("Что дальше?", reply_markup=InlineKeyboardMarkup(buttons))
        return

    # Step 2: after look selected, choose avatar version (3 or 4)
    if query.data.startswith("heygen_gen:"):
        look_key = query.data.split(":", 1)[1]
        # Look name resolution: brand-specific first, then global, then default.
        # Special key "__custom__" — берём имя из ранее зарегистрированного
        # photo avatar (data["heygen_custom_avatar_id"]).
        _brand_looks = _get_active_brand().get("heygen_looks") or {}
        if look_key == "__custom__" and data and data.get("heygen_custom_avatar_id"):
            look_name = f"📸 Custom photo (id ...{data['heygen_custom_avatar_id'][-6:]})"
        elif look_key in _brand_looks:
            look_name = _brand_looks[look_key].get("name", "Дефолтный")
        elif look_key != "default" and look_key in HEYGEN_LOOKS:
            look_name = HEYGEN_LOOKS[look_key].get("name", "Дефолтный")
        else:
            look_name = "Дефолтный"
        data["heygen_look_key"] = look_key
        _save_pending(pending)

        # Пикер версий — бренд-зависимый. Фото-аватар Максима зарегистрирован
        # как Avatar IV (supported_api_engines: ["avatar_iv"]) — на Avatar 3
        # и legacy Avatar 4 (version="4") он даёт только шевеление головы без
        # жестов. Поэтому для maksim предлагаем строго Avatar IV (v4 →
        # use_avatar_iv_model=true).
        if _get_active_brand_name() == "maksim":
            # 24 мая 2026 — Avatar 3 = рекомендованный дефолт (вывод Максима:
            # разница в поведении с IV минимальна, IV в разы дороже). «Свой
            # голос» теперь без жёсткой версии — выбор 3/IV на след. шаге.
            buttons = [
                [InlineKeyboardButton(
                    "⚡ Avatar 3 — рекомендуем (дешевле в разы)",
                    callback_data="heygen_ver:v3")],
                [InlineKeyboardButton(
                    "✨ Avatar IV — премиум (свои жесты, дороже)",
                    callback_data="heygen_ver:v4")],
                [InlineKeyboardButton(
                    "🎤 Озвучить своим голосом",
                    callback_data="heygen_selfvoice")],
                [InlineKeyboardButton("◀️ Назад к лукам", callback_data="heygen_looks")],
            ]
            ver_text = (
                f"👤 Лук: {look_name}\n\n"
                f"⚡ Avatar 3 — рекомендуем. Дешевле в разы, поведение почти как у IV.\n"
                f"   Повторяет жесты из твоей записи лука — если они спокойные, "
                f"выглядит отлично.\n\n"
                f"✨ Avatar IV — придумывает свои жесты, чуть живее, но в разы дороже. "
                f"Для особых роликов.\n\n"
                f"🎤 Своим голосом — пришлёшь голосовое, озвучу им (версию выберешь дальше)."
            )
        else:
            buttons = [
                [InlineKeyboardButton("⚡ Avatar 3 — быстрый, дешёвый", callback_data="heygen_ver:v3")],
                [InlineKeyboardButton("✨ Avatar 4 — реалистичный, жесты, мимика", callback_data="heygen_ver:v2")],
                [InlineKeyboardButton("◀️ Назад к лукам", callback_data="heygen_looks")],
            ]
            ver_text = (
                f"👤 Лук: {look_name}\n\n"
                f"Avatar 3 — базовая модель, ~0.5 кредита/мин\n"
                f"Avatar 4 — улучшенная мимика, жесты рук, ~1 кредит/10сек\n\n"
                f"Выбери версию:"
            )
        await query.edit_message_text(
            ver_text, reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    # Фича «свой голос»: тот же шаг версии, но озвучка — записью Максима,
    # а не TTS. Резолвим лук так же, как heygen_ver, и ждём голосовое.
    # «Свой голос» без версии → сперва выбор модели (Avatar 3 дешевле / IV
    # дороже), потом ждём голосовое. 24 мая 2026: раньше кнопка жёстко гнала
    # на IV (heygen_selfvoice:v4). Теперь Avatar 3 — рекомендованный дефолт.
    if query.data == "heygen_selfvoice":
        _lk = data.get("heygen_look_key", "default")
        await query.edit_message_text(
            "🎤 Озвучка своим голосом — выбери модель аватара:\n\n"
            "⚡ Avatar 3 — рекомендуем, дешевле в разы. Повторит жесты из записи лука.\n"
            "✨ Avatar IV — свои жесты, чуть живее, но в разы дороже.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚡ Avatar 3 (дешевле)",
                                      callback_data="heygen_selfvoice:v3")],
                [InlineKeyboardButton("✨ Avatar IV (дороже)",
                                      callback_data="heygen_selfvoice:v4")],
                [InlineKeyboardButton("◀️ Назад", callback_data=f"heygen_gen:{_lk}")],
            ]),
        )
        return

    if query.data.startswith("heygen_selfvoice:"):
        avatar_version = query.data.split(":", 1)[1]
        look_key = data.get("heygen_look_key", "look1")
        _brand = _get_active_brand()
        _brand_looks = _brand.get("heygen_looks") or {}
        if look_key in _brand_looks:
            look_id = _brand_looks[look_key].get("id")
            look_name = _brand_looks[look_key].get("name", "Аватар")
        elif look_key != "default" and look_key in HEYGEN_LOOKS:
            look_id = HEYGEN_LOOKS[look_key].get("id")
            look_name = HEYGEN_LOOKS[look_key].get("name", "Аватар")
        else:
            look_id = _brand.get("heygen_avatar_id")
            look_name = _brand.get("description", "Аватар")
        data["selfvoice_look_id"] = look_id
        data["selfvoice_look_name"] = look_name
        data["selfvoice_version"] = avatar_version
        data["state"] = "awaiting_selfvoice"
        pending[user_id] = data
        _save_pending(pending)
        _sv_ver = {"v4": "Avatar IV", "v2": "Avatar 4", "v3": "Avatar 3"}.get(
            avatar_version, "Avatar 3")
        await query.edit_message_text(
            f"🎤 Озвучка своим голосом — {look_name} ({_sv_ver})\n\n"
            f"Пришли ОДНО голосовое сообщение на весь ролик. Я озвучу им аватар "
            f"(без синтеза голоса), субтитры сниму с твоей записи на монтаже.\n\n"
            f"После записи я переспрошу — и только после твоего «Да» запущу "
            f"генерацию аватара (она спишет кредиты HeyGen)."
        )
        return

    # Подтверждение «свой голос → аватар» (24 мая 2026). Гейт перед тратой
    # кредитов HeyGen: запись принята, но генерим только после явного «Да».
    if query.data == "selfvoice_confirm":
        audio_path = data.get("selfvoice_audio_path")
        if not audio_path or not Path(audio_path).exists():
            await query.edit_message_text(
                "⚠️ Запись потерялась (бот перезапускался?). "
                "Нажми «🎤 Озвучить своим голосом» и пришли голосовое заново."
            )
            return
        await query.edit_message_text("🎤 Принято. Запускаю генерацию аватара под твою запись…")
        await _render_avatar_from_audio(
            context, query.message.chat_id, audio_path,
            data.get("selfvoice_look_id"),
            data.get("selfvoice_look_name", "Аватар"),
            data.get("selfvoice_version", "v4"),
            data,
        )
        return

    if query.data == "selfvoice_redo":
        data["state"] = "awaiting_selfvoice"
        pending[user_id] = data
        _save_pending(pending)
        await query.edit_message_text(
            f"🔁 Ок, перезаписываем. Пришли новое голосовое сообщение "
            f"на весь ролик — лук «{data.get('selfvoice_look_name', 'Аватар')}». "
            f"После записи снова переспрошу перед генерацией."
        )
        return

    if query.data == "selfvoice_cancel":
        data["state"] = None
        data.pop("selfvoice_audio_path", None)
        pending[user_id] = data
        _save_pending(pending)
        await query.edit_message_text(
            "❌ Отменил. Аватар не генерировал, кредиты не тронуты."
        )
        return

    # Step 3: generate with selected look + version
    if query.data.startswith("heygen_ver:"):
        avatar_version = query.data.split(":", 1)[1]  # "v3" / "v2" / "v4"
        look_key = data.get("heygen_look_key", "look1")
        # Brand-aware look resolution: brand's own looks take priority.
        # Fallback to heygen_avatar_id if the brand has no named looks but
        # declares a single avatar. Final fallback — global HEYGEN_LOOKS.
        # Special: look_key == "__custom__" + есть photo_url → Image-to-Video
        # через HeyGen v3 endpoint (без регистрации persistent avatar — не
        # упирается в лимит 3 photo-аватаров). avatar_id == None в этом
        # пути, look_id используется только для логирования.
        _brand = _get_active_brand()
        _brand_looks = _brand.get("heygen_looks") or {}
        custom_photo_url = data.get("heygen_custom_photo_url") if look_key == "__custom__" else None
        if look_key == "__custom__" and custom_photo_url:
            look_id = None  # not needed for image-to-video
            look_name = f"📸 Custom photo (Image-to-Video)"
        elif look_key == "__custom__" and data.get("heygen_custom_avatar_id"):
            # legacy путь — если когда-то регистрация прошла
            look_id = data["heygen_custom_avatar_id"]
            look_name = f"📸 Custom photo (id ...{look_id[-6:]})"
        elif look_key in _brand_looks:
            look_id = _brand_looks[look_key].get("id")
            look_name = _brand_looks[look_key].get("name", "Дефолтный")
        elif look_key != "default" and look_key in HEYGEN_LOOKS:
            look_id = HEYGEN_LOOKS[look_key].get("id")
            look_name = HEYGEN_LOOKS[look_key].get("name", "Дефолтный")
        else:
            # Last resort: brand's single avatar_id, or HEYGEN_LOOKS["look1"]
            # (via heygen_generate_video's own fallback when look_id is None).
            look_id = _brand.get("heygen_avatar_id")
            look_name = _brand.get("description", "Дефолтный")
        ver_label = {"v4": "Avatar IV", "v2": "Avatar 4", "v3": "Avatar 3"}.get(avatar_version, "Avatar 3")

        await query.edit_message_text(f"🤖 Генерирую видео аватара ({look_name}, {ver_label})...\n\n⏱ Обычно занимает 1-3 минуты.")

        try:
            # Combine all voice parts into one audio file
            parts = data.get("voice_parts", [])
            if not parts:
                await query.edit_message_text("Нет озвучки. Сначала озвучь сценарий.")
                return

            # Merge voice parts into single file using ffmpeg
            # Use card directory files if available, otherwise assets/
            notion_id = data.get("notion_page_id")
            if notion_id:
                card_files = [str(f) for f in _get_voice_files(notion_id, len(parts)) if f.exists()]
                if card_files:
                    voice_files = card_files
                else:
                    voice_files = [str(ASSETS_DIR / f"voice_part_{i}.mp3") for i in range(len(parts))]
            else:
                voice_files = [str(ASSETS_DIR / f"voice_part_{i}.mp3") for i in range(len(parts))]
            existing_files = [f for f in voice_files if Path(f).exists()]
            if not existing_files:
                await query.edit_message_text("Файлы озвучки не найдены. Переозвучь сценарий.")
                return

            merged_path = str(ASSETS_DIR / "voice_merged.mp3")
            if len(existing_files) == 1:
                import shutil
                shutil.copy2(existing_files[0], merged_path)
            else:
                # Use ffmpeg to concatenate
                concat_list = str(ASSETS_DIR / "concat_list.txt")
                with open(concat_list, "w") as f:
                    for vf in existing_files:
                        f.write(f"file '{vf}'\n")
                subprocess.run(
                    ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list, "-c", "copy", merged_path],
                    capture_output=True, timeout=60
                )

            if not Path(merged_path).exists():
                await query.edit_message_text("Ошибка склейки аудио.")
                return

            # Auto-trim long silences (>0.5s → 0.3s)
            try:
                merged_path = await asyncio.to_thread(
                    trim_long_silences, merged_path, merged_path,
                    max_silence_sec=0.5, keep_silence_sec=0.3,
                )
            except Exception as e:
                logger.warning(f"Silence trim failed (using untrimmed): {e}")

            # Upload audio to HeyGen's asset upload (upload.heygen.com, raw binary)
            import httpx
            headers_upload = {
                "X-Api-Key": HEYGEN_API_KEY,
                "Content-Type": "audio/mpeg",
            }
            with open(merged_path, "rb") as audio_file:
                upload_resp = httpx.post(
                    "https://upload.heygen.com/v1/asset",
                    headers=headers_upload,
                    content=audio_file.read(),
                    timeout=120,
                )
            upload_data = upload_resp.json()
            logger.info(f"HeyGen audio upload: {upload_data}")

            if upload_data.get("code") != 100:
                raise RuntimeError(f"Upload error: {upload_data}")

            audio_url = upload_data.get("data", {}).get("url", "")
            if not audio_url:
                raise RuntimeError(f"No audio URL in response: {upload_data}")

            # Generate video — два пути:
            # (а) Custom photo URL → HeyGen v3 Image-to-Video (без регистрации
            #     avatar_id, не упирается в лимит). Использует /v3/videos
            #     type:"image".
            # (б) Стандартный avatar_id → HeyGen v2 /v2/video/generate.
            if custom_photo_url:
                # 'v2' (legacy Avatar 4) → 'v4' для v3 endpoint, иначе как есть
                v3_version = "v4" if avatar_version == "v2" else avatar_version
                logger.info(
                    f"HeyGen Image-to-Video: photo={custom_photo_url[:60]}... "
                    f"audio={audio_url[:60]}... version={v3_version}"
                )
                video_id = await asyncio.to_thread(
                    heygen_v3_image_to_video, custom_photo_url, audio_url, v3_version,
                )
                logger.info(f"HeyGen v3 video submitted: {video_id} (image-to-video, version={v3_version})")
                _use_v3_polling = True
            else:
                video_id = await asyncio.to_thread(heygen_generate_video, audio_url, look_id, avatar_version)
                logger.info(f"HeyGen video submitted: {video_id} (version={avatar_version})")
                _use_v3_polling = False

            # Poll for completion (v3 use status endpoint /v3/videos/{id},
            # v2 — старый /v1/video_status.get).
            for attempt in range(60):  # max 10 minutes
                await asyncio.sleep(10)
                if _use_v3_polling:
                    result = await asyncio.to_thread(heygen_v3_check_status, video_id)
                else:
                    result = await asyncio.to_thread(heygen_check_status, video_id)
                status = result["status"]

                if status == "completed":
                    video_url = result["video_url"]
                    duration = result.get("duration", "?")

                    # Download video and send to Telegram
                    async with httpx.AsyncClient() as client:
                        video_resp = await client.get(video_url, timeout=120)
                        video_bytes = video_resp.content

                    video_file = ASSETS_DIR / f"heygen_{video_id[:8]}.mp4"
                    with open(video_file, "wb") as f:
                        f.write(video_bytes)

                    # Save avatar video to project folder and Notion.
                    # First wipe any older avatar_*.mp4 so "Скачать материалы"
                    # ships only the latest take, not leftover failed runs / old looks.
                    new_avatar_name = f"avatar_{look_name}.mp4"
                    _cleanup_old_avatars(data, keep_filename=new_avatar_name)
                    _save_to_project(data, new_avatar_name, str(video_file))
                    # Save merged voice to project folder
                    if Path(merged_path).exists():
                        _save_to_project(data, "voice_merged.mp3", merged_path)

                    # Auto-advance status: HeyGen аватар успешно сгенерён →
                    # двигаем карточку в "Аватар | генерации". 4 мая 2026 фикс
                    # по reportу Артёма (раньше карточка зависала на "Подбор
                    # скринкаст" даже после успешного аватара).
                    notion_id = data.get("notion_page_id")
                    if notion_id:
                        try:
                            await asyncio.to_thread(
                                update_notion_status, notion_id, "Аватар | генерации",
                            )
                            logger.info(f"[status] {notion_id[:8]}... → Аватар | генерации")
                        except Exception as _se:
                            logger.warning(f"[status] auto-advance to Аватар | генерации failed: {_se}")

                    # Add avatar link to Notion card
                    if notion_id:
                        # Записываем ссылку на аватар в Notion с двухуровневым
                        # fallback: сначала пробуем permanent-URL (копия в media/
                        # под nginx, живёт вечно). Если не получилось — пишем
                        # прямую HeyGen-URL (истекает за ~7 дней, но лучше чем
                        # пусто). Если и это упало — параграф-пометку, чтобы
                        # было видно что файл существует на сервере.
                        avatar_url = None
                        try:
                            avatar_url = save_media_permanent(
                                str(video_file), f"avatar_{look_name}"
                            )
                        except Exception as e:
                            logger.warning(
                                f"[avatar] save_media_permanent failed: {e} — "
                                f"fallback to direct HeyGen URL"
                            )
                        notion_caption = f"🤖 Аватар ({look_name}, {ver_label}, {duration}с): "
                        if avatar_url:
                            rich = [
                                {"type": "text", "text": {"content": notion_caption}},
                                {"type": "text", "text": {"content": avatar_url, "link": {"url": avatar_url}}},
                            ]
                        elif result.get("video_url"):
                            # Direct HeyGen URL — temporary, but still useful
                            hg_url = result["video_url"]
                            rich = [
                                {"type": "text", "text": {"content": notion_caption}},
                                {"type": "text", "text": {"content": hg_url, "link": {"url": hg_url}}},
                                {"type": "text", "text": {
                                    "content": " (временная ссылка HeyGen, ~7 дней)",
                                }, "annotations": {"italic": True, "color": "gray"}},
                            ]
                        else:
                            rich = [{"type": "text", "text": {
                                "content": notion_caption + f"(файл в проекте: {video_file.name})",
                            }}]
                        try:
                            notion.blocks.children.append(
                                block_id=notion_id,
                                children=[{
                                    "object": "block",
                                    "type": "paragraph",
                                    "paragraph": {"rich_text": rich},
                                }],
                            )
                        except Exception as e:
                            logger.warning(f"Failed to save avatar to Notion: {e}")

                    # Telegram Bot API не принимает файл >50 МБ. Avatar IV
                    # отдаёт видео с очень высоким битрейтом — ролик ~30с
                    # весит ~90 МБ → send_video падал с 413. Крупный файл
                    # шлём в Telegram сжатой копией; оригинал уже сохранён
                    # в проект для монтажа, его не трогаем.
                    _avatar_caption = (
                        f"🤖 Аватар готов! ({look_name}, {ver_label}, {duration}с)"
                    )
                    send_file = video_file
                    _tg_compressed = None
                    if video_file.stat().st_size > 48 * 1024 * 1024:
                        _tg_compressed = ASSETS_DIR / f"heygen_{video_id[:8]}_tg.mp4"
                        # 2-pass, сохраняя 1080p (раньше даунскейлили до 720p
                        # → Avatar IV выглядел мыльно). Фолбэк на 720p внутри.
                        ok = await asyncio.to_thread(
                            _compress_for_telegram, str(video_file),
                            str(_tg_compressed), duration)
                        if ok:
                            send_file = _tg_compressed
                            logger.info(
                                f"[avatar] сжал для Telegram: "
                                f"{video_file.stat().st_size / 1048576:.0f}МБ → "
                                f"{_tg_compressed.stat().st_size / 1048576:.0f}МБ"
                            )
                    # Кнопки «что дальше» строим ДО отправки видео и прикрепляем
                    # под видео (reply_markup в send_video). Иначе они оказываются
                    # в «генерация…»-сообщении наверху, и пользователю приходится
                    # скроллить чат вверх — а свежий результат в самом низу.
                    quota = await asyncio.to_thread(heygen_get_quota)
                    buttons = [
                        [InlineKeyboardButton("🔄 Другой лук / версия", callback_data="heygen_looks")],
                    ]
                    if not data.get("broll_approved"):
                        buttons.append([InlineKeyboardButton("🎬 Подобрать B-roll", callback_data="broll")])
                    if NOTION_GUIDES_DB and not data.get("guide_created"):
                        buttons.append([InlineKeyboardButton("📎 Создать гайд", callback_data="create_guide")])
                    buttons.append([InlineKeyboardButton("📥 Скачать материалы", callback_data="download_project")])
                    buttons.append([InlineKeyboardButton("✅ Готово", callback_data="finish")])
                    _next_step_kb = InlineKeyboardMarkup(buttons)
                    _video_caption = (
                        f"{_avatar_caption}\n\n"
                        f"💰 Остаток HeyGen: {quota} кредитов\n"
                        f"Что дальше?"
                    )

                    try:
                        with open(send_file, "rb") as f:
                            await context.bot.send_video(
                                chat_id=query.message.chat_id,
                                video=f,
                                caption=_video_caption,
                                supports_streaming=True,
                                reply_markup=_next_step_kb,
                            )
                    except Exception as _se:
                        # Не валим весь шаг — аватар уже сохранён в проект и
                        # Notion. Шлём текст со ссылкой + те же кнопки.
                        logger.warning(f"[avatar] send_video не прошёл: {_se}")
                        _link = result.get("video_url") or ""
                        await context.bot.send_message(
                            chat_id=query.message.chat_id,
                            text=(
                                f"{_video_caption}\n\n"
                                f"⚠️ Файл крупноват для Telegram — забери из "
                                f"«📥 Скачать материалы»"
                                + (f" или по ссылке:\n{_link}" if _link else ".")
                            ),
                            reply_markup=_next_step_kb,
                        )
                    if _tg_compressed and _tg_compressed.exists():
                        try:
                            _tg_compressed.unlink()
                        except Exception:
                            pass

                    # Заменяем «генерация…»-сообщение на короткий статус —
                    # большое меню уже под видео ниже, дублировать не нужно.
                    try:
                        await query.edit_message_text(
                            f"✅ Аватар готов ({look_name}, {ver_label}). "
                            f"Кнопки «что дальше» — под видео ниже."
                        )
                    except Exception:
                        pass
                    # Clean up
                    try:
                        video_file.unlink()
                    except Exception:
                        pass
                    break

                elif status == "failed":
                    error_msg = result.get("error", {})
                    if isinstance(error_msg, dict):
                        error_msg = error_msg.get("message", str(error_msg))
                    raise RuntimeError(f"HeyGen failed: {error_msg}")

                # Still processing — update message every 30s
                if attempt % 3 == 2:
                    try:
                        await query.edit_message_text(
                            f"🤖 Генерация... ({(attempt + 1) * 10}с)\n\nЛук: {look_name} ({ver_label})"
                        )
                    except Exception:
                        pass
            else:
                raise RuntimeError("HeyGen timeout — видео не готово за 10 минут")

        except Exception as e:
            logger.error(f"HeyGen error: {e}", exc_info=True)
            buttons = [
                [InlineKeyboardButton("🔄 Попробовать снова", callback_data="heygen_looks")],
                [InlineKeyboardButton("✅ Готово", callback_data="finish")],
            ]
            await query.edit_message_text(
                f"❌ Ошибка генерации: {e}",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        return

    if query.data == "gen_description":
        script_text = data.get("script", "")
        if not script_text:
            await query.edit_message_text("Нет сценария. Сначала создай сценарий.")
            return

        await query.answer()
        # Статус шлём НОВЫМ сообщением (внизу чата), а НЕ edit старого с
        # кнопкой — иначе «описание» висит над свежими аудио-частями озвучки.
        status_msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="📝 Пишу описание для публикации...",
        )

        try:
            variants, extracted_cta = _compose_publication_descriptions(script_text)

            data["description_variants"] = variants
            data["description_cta_extracted"] = extracted_cta
            _save_pending(pending)

            # Send each variant with a select button
            for i, var in enumerate(variants):
                btn = InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"✅ Выбрать вариант {i+1}", callback_data=f"desc_pick:{i}")]
                ])
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"📝 Вариант {i+1}:\n\n{var}",
                    reply_markup=btn,
                )

            # Статус (внизу, над вариантами) → указатель «варианты ниже».
            try:
                await status_msg.edit_text("✅ Описание готово — 3 варианта ниже 👇")
            except Exception:
                pass

            # Send the action menu as a NEW message so it lands AT THE BOTTOM
            # of the chat, under the variants (not above them).
            buttons = [
                [InlineKeyboardButton("✏️ Написать свой текст", callback_data="desc_custom")],
                # Кнопка «📝 Переписать под Telegram» (callback `tgpost:rewrite_tg`)
                # удалена 5 мая 2026 — handler нигде не реализован, кнопка не
                # делала ничего. Если потребуется — добавить handler в
                # tg_post_handlers.py с паттерном `^tgpost:rewrite_tg$`.
                [InlineKeyboardButton("🔄 Перегенерировать", callback_data="gen_description")],
                [InlineKeyboardButton("✅ Готово", callback_data="finish")],
            ]
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="Выбери вариант выше ☝️ или действие ниже:",
                reply_markup=InlineKeyboardMarkup(buttons),
            )

        except Exception as e:
            logger.error(f"Description generation error: {e}", exc_info=True)
            buttons = [
                [InlineKeyboardButton("🔄 Попробовать снова", callback_data="gen_description")],
                [InlineKeyboardButton("✅ Готово", callback_data="finish")],
            ]
            await status_msg.edit_text(f"❌ Ошибка: {e}", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if query.data.startswith("desc_pick:"):
        idx = int(query.data.split(":")[1])
        variants = data.get("description_variants", [])
        if idx >= len(variants):
            await query.answer("Вариант не найден")
            return

        description = variants[idx]
        data["description_draft"] = description
        _save_pending(pending)

        # Remove the button from the tapped variant message so it can't be
        # clicked again (and visually marks it as chosen), but keep the
        # variant text visible in the chat.
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

        # Send the save/edit menu as a NEW message at the bottom of the chat
        # — otherwise, if the user picked variant 1 or 2, the menu would
        # appear above later variants and force them to scroll up.
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Сохранить как есть", callback_data="desc_save")],
            [InlineKeyboardButton("✏️ Отредактировать", callback_data="desc_edit")],
        ])
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"✅ Выбран вариант {idx+1}:\n\n{description}\n\nСохранить или отредактировать?",
            reply_markup=buttons,
        )
        return

    # ── TG-пост по сценарию: переиспользует tg_post_writer.rewrite_for_telegram ──
    if query.data == "tgpost_from_script":
        script_text = extract_script_text(data)
        # Fallback: достать сценарий из папки проекта, даже когда сессия его
        # потеряла — карточка переоткрыта после рестарта, ИЛИ быстрый Telethon-
        # тест прыгнул сразу на готовую карточку (без прогона селфи). Раньше
        # читался только script.txt по notion_page_id, но finalize селфи пишет
        # transcript.txt и кладёт лишь notion_edit_card → кнопка из меню
        # переоткрытой карточки молча падала в «Нет сценария» (Артём 10 июня).
        if not script_text:
            # Кандидаты папок проекта по убыванию точности. Точная папка из
            # notion_edit_card+title — ПЕРВЕЕ префикс-глоба: префиксы Notion-id
            # коллизятся (8 hex), и _project_dir_by_prefix вернул бы самую
            # свежую папку с тем же префиксом → сценарий ЧУЖОЙ карточки.
            _cand_dirs: list[Path] = []
            _p1 = _project_dir(data)  # точная, если в сессии есть notion_page_id+title
            if _p1:
                _cand_dirs.append(_p1)
            _vc = data.get("notion_edit_card")
            _vt = data.get("notion_edit_title")
            if _vc:
                _p2 = _project_dir({"notion_page_id": _vc, "card_data": {"title": _vt or ""}})
                if _p2:
                    _cand_dirs.append(_p2)
            _p3 = _project_dir_by_prefix(_vc or data.get("crosspost_card_id") or "")
            if _p3:
                _cand_dirs.append(_p3)
            for _d in _cand_dirs:
                for _fname in ("script.txt", "transcript.txt"):
                    _fp = _d / _fname
                    if _fp.exists():
                        try:
                            script_text = _fp.read_text(encoding="utf-8").strip()
                        except Exception:
                            script_text = ""
                        if script_text:
                            break
                if script_text:
                    break
        # Последний резерв — тело страницы Notion (там лежит сценарий/расшифровка).
        if not script_text:
            _full_id = data.get("notion_edit_card") or data.get("notion_page_id")
            if _full_id:
                try:
                    script_text = (await asyncio.to_thread(
                        fetch_notion_page_script, _full_id
                    ) or "").strip()
                except Exception as e:
                    logger.warning(f"[tgpost_from_script] Notion script fetch failed: {e}")
        if not script_text:
            await query.answer("Нет сценария — сначала создай его", show_alert=True)
            return

        card_title = (
            data.get("card_data", {}).get("title", "")
            or data.get("notion_edit_title", "")
        )
        video_topic = extract_video_topic(data, card_title)
        description = data.get("description", "")

        await query.answer()
        status_msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="📰 Пишу TG-пост по сценарию ролика... ~30-40 сек",
        )

        try:
            post_text = await asyncio.to_thread(
                tg_post_writer.rewrite_for_telegram,
                script_text,
                description,
                video_topic,
                claude,
            )
            data["tg_post_from_script"] = post_text
            _save_pending(pending)

            photos_count = len(data.get("selfie_tg_photos") or [])
            # Markdown disabled here — generated post may contain unescaped
            # underscores/asterisks that would break parser. Plain text is fine.
            await status_msg.edit_text(
                f"📰 TG-пост по сценарию:\n\n{post_text}",
                reply_markup=_tgpost_script_keyboard(photos_count),
            )
        except Exception as e:
            logger.error(f"tgpost_from_script error: {e}", exc_info=True)
            await status_msg.edit_text(f"❌ Не удалось сгенерировать TG-пост: {e}")
        return

    if query.data == "tgpost_from_script:publish":
        post_text = data.get("tg_post_from_script", "")
        if not post_text:
            await query.answer("Нет поста для публикации", show_alert=True)
            return
        # Прикреплённые фото (своё из чата / из банка) — тот же ключ и тот же
        # publish-путь, что у idea→tgpost. Если фото нет — telegram_post_to_channel
        # шлёт просто текст (поведение как раньше).
        tg_photos = data.get("selfie_tg_photos") or None
        # Convert markdown **bold** → HTML <b> (как в idea_tgpost_publish).
        post_escaped = html_mod.escape(post_text)
        post_html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", post_escaped)
        await query.answer("Публикую…")
        try:
            from crosspost import telegram_post_to_channel
            result = await telegram_post_to_channel(
                context.bot, post_html, photos=tg_photos,
            )
        except Exception as e:
            logger.error(f"tgpost_from_script publish error: {e}", exc_info=True)
            await query.edit_message_text(f"❌ Ошибка публикации в TG: {e}")
            return
        if not result:
            await query.edit_message_text(
                "❌ Публикация не удалась. Проверь TELEGRAM_CHANNEL_ID "
                "и что бот — админ канала."
            )
            return
        # Снимаем фото, чтобы не утекли в следующий пост.
        data["selfie_tg_photos"] = []
        _save_pending(pending)
        photos_part = f" + {len(tg_photos)} фото" if tg_photos else ""
        await query.edit_message_text(
            f"✅ TG-пост опубликован{photos_part}."
        )
        return

    if query.data == "desc_edit":
        data["state"] = "desc_editing"
        _save_pending(pending)
        draft = data.get("description_draft", "")
        await query.edit_message_text(
            f"✏️ Текущий текст:\n\n{draft}\n\nОтправь исправленную версию целиком.",
        )
        return

    if query.data == "desc_custom":
        data["state"] = "desc_editing"
        data["description_draft"] = ""
        _save_pending(pending)
        await query.edit_message_text("✏️ Отправь свой текст описания.")
        return

    if query.data == "desc_save":
        description = data.get("description_draft", "")
        if not description:
            await query.answer("Нет текста")
            return

        _save_text_to_project(data, "description.txt", description)
        data["description"] = description
        data.pop("description_draft", None)
        data.pop("description_variants", None)
        _save_pending(pending)

        # Save to Notion
        notion_id = data.get("notion_page_id")
        if notion_id:
            try:
                notion.blocks.children.append(
                    block_id=notion_id,
                    children=[{
                        "object": "block",
                        "type": "toggle",
                        "toggle": {
                            "rich_text": [{"type": "text", "text": {"content": "📝 Описание для публикации"}}],
                            "children": [{
                                "object": "block",
                                "type": "paragraph",
                                "paragraph": {"rich_text": [{"type": "text", "text": {"content": description[:2000]}}]},
                            }],
                        },
                    }],
                )
            except Exception as e:
                logger.warning(f"Failed to save description to Notion: {e}")

        buttons = [
            # «📝 Переписать под Telegram» удалена 5 мая 2026 — нет handler'а.
            [InlineKeyboardButton("🔄 Перегенерировать", callback_data="gen_description")],
            [InlineKeyboardButton("✅ Готово", callback_data="finish")],
        ]
        await query.edit_message_text(
            f"✅ Описание сохранено:\n\n{description}",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if query.data.startswith("upload_final:"):
        card_id_prefix = query.data.split(":", 1)[1]
        data["state"] = "upload_final_video"
        data["upload_final_card_id"] = card_id_prefix
        _save_pending(pending)

        # Check if already has final video
        proj = _project_dir(data)
        has_final = proj and (proj / "final_video.mp4").exists() if proj else False

        if has_final:
            file_size = (proj / "final_video.mp4").stat().st_size / 1024 / 1024
            await query.edit_message_text(
                f"📤 Готовый ролик уже загружен ({file_size:.1f} МБ)\n\n"
                "Отправь новое видео, чтобы заменить, или нажми назад.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🗑 Удалить ролик", callback_data="delete_final")],
                    [InlineKeyboardButton("◀️ Назад", callback_data=f"notion_card:{card_id_prefix}")],
                ]),
            )
        else:
            await query.edit_message_text(
                "📤 Отправь готовый видеоролик (MP4)\n\n"
                "• Файл **до 20 MB** — просто пришли сюда в чат\n"
                "• Файл **больше 20 MB** — отправь в свои «Избранное» (Saved Messages) "
                "с подписью `#crosspost`. Сервер автоматически скачает и положит в проект.\n\n"
                "Это видео будет использоваться при кросс-постинге на все площадки.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ Назад", callback_data=f"notion_card:{card_id_prefix}")],
                ]),
            )
        return

    if query.data == "delete_final":
        proj = _project_dir(data)
        if proj:
            final_path = proj / "final_video.mp4"
            final_path.unlink(missing_ok=True)
            logger.info(f"Deleted final video: {final_path}")
        card_prefix = data.get("upload_final_card_id", "")
        await query.edit_message_text(
            "🗑 Готовый ролик удалён.\n\n"
            "Отправь новое видео или вернись к карточке.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{card_prefix}")],
            ]),
        )
        return

    # ---- Music mixing ----
    if query.data.startswith("music_pick:"):
        # Show category picker
        import music_mixer
        card_prefix = query.data.split(":", 1)[1]
        cats = music_mixer.list_categories()
        if not cats:
            await query.edit_message_text(
                "❌ Музыкальная библиотека пуста. Проверь "
                f"{os.getenv('MAKSIM_MUSIC_DIR', '/srv/bot-music-maksim')}/",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{card_prefix}")
                ]]),
            )
            return
        buttons = []
        for cat, meta in cats.items():
            label = f"{meta.get('emoji', '🎵')} {meta.get('label', cat)}"
            buttons.append([InlineKeyboardButton(label, callback_data=f"music_cat:{cat}:{card_prefix}")])
        buttons.append([InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{card_prefix}")])
        await query.edit_message_text(
            "🎵 <b>Выбери категорию музыки</b>\n\n"
            "Музыка будет наложена на финальный ролик с автоматическим приглушением под голосом.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if query.data.startswith("music_cat:"):
        # Show 3 random tracks from category
        import music_mixer
        parts = query.data.split(":", 2)
        cat = parts[1]
        card_prefix = parts[2] if len(parts) > 2 else ""
        tracks = music_mixer.list_tracks(cat)
        if not tracks:
            await query.answer("Нет треков в этой категории", show_alert=True)
            return
        import random as _rnd
        sample = _rnd.sample(tracks, min(3, len(tracks)))
        buttons = []
        for i, t in enumerate(sample, 1):
            label = f"🎵 Трек {i} ({t['duration']:.0f}с)"
            buttons.append([InlineKeyboardButton(label, callback_data=f"music_apply:{t['id']}:{card_prefix}")])
        buttons.append([InlineKeyboardButton("🔀 Другие треки", callback_data=f"music_cat:{cat}:{card_prefix}")])
        buttons.append([InlineKeyboardButton("◀️ Назад", callback_data=f"music_pick:{card_prefix}")])

        cats = music_mixer.list_categories()
        meta = cats.get(cat, {})
        await query.edit_message_text(
            f"{meta.get('emoji', '🎵')} <b>{meta.get('label', cat)}</b>\n"
            f"<i>{meta.get('desc', '')}</i>\n\n"
            "Выбери один из трёх вариантов:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if query.data.startswith("music_apply:"):
        # Apply selected track to final video
        import music_mixer
        parts = query.data.split(":", 2)
        track_id = parts[1]
        card_prefix = parts[2] if len(parts) > 2 else ""

        # Find the track file
        track_file = None
        track_cat = None
        for cat_name in music_mixer.list_categories().keys():
            for t in music_mixer.list_tracks(cat_name):
                if t["id"] == track_id:
                    track_file = t["file"]
                    track_cat = cat_name
                    break
            if track_file:
                break
        if not track_file:
            await query.answer("Трек не найден", show_alert=True)
            return

        proj = _project_dir(data)
        if not proj:
            await query.answer("Нет проекта", show_alert=True)
            return

        # Prefer uploaded final_video.mp4, fallback to final_auto.mp4
        source_video = proj / "final_video.mp4"
        if not source_video.exists():
            source_video = proj / "final_auto.mp4"
        if not source_video.exists():
            await query.edit_message_text(
                "❌ Нет финального ролика для микса.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{card_prefix}")
                ]]),
            )
            return

        output_path = proj / "final_video_with_music.mp4"

        await query.edit_message_text(
            f"🎵 Микширую музыку ({track_cat})...\nЭто занимает 30-60 секунд."
        )

        # Run in thread to not block
        success = await asyncio.to_thread(
            music_mixer.mix_music_into_video,
            str(source_video),
            track_file,
            str(output_path),
        )

        if success and output_path.exists():
            size_mb = output_path.stat().st_size / 1024 / 1024
            # Send the mixed video back to user
            try:
                with open(output_path, "rb") as vf:
                    await context.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=vf,
                        caption=f"🎵 Ролик с музыкой ({track_cat}, {size_mb:.1f}МБ)",
                        supports_streaming=True,
                    )
            except Exception as e:
                logger.error(f"music send failed: {e}")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=(
                    f"✅ Готово! Файл сохранён: <code>final_video_with_music.mp4</code>\n\n"
                    "Теперь этот вариант будет использоваться при кросс-постинге."
                ),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{card_prefix}")
                ]]),
            )
        else:
            await query.edit_message_text(
                "❌ Не удалось смикшировать. Проверь логи.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ К карточке", callback_data=f"notion_card:{card_prefix}")
                ]]),
            )
        return

    if query.data == "download_project":
        # Strategy: package everything into a single ZIP (photos/, videos,
        # avatar, final, script — all in one file). If the ZIP fits under
        # Telegram's 48MB-effective upload ceiling, send it. Otherwise fall
        # back to the old file-by-file mode so user still gets everything.
        chat_id = query.message.chat_id
        MAX_BOT_UPLOAD = 48 * 1024 * 1024  # 48MB safety margin under 50MB

        # Billing — charge on download (idempotent). The zip path counts
        # as download_zip; file-by-file fallback also charges (same trigger
        # since end result is the same: client got the materials).
        _full_video_id = (
            (data if isinstance(data, dict) else {}).get("notion_page_id")
        )
        await _billing_charge_if_needed(
            user_id, _full_video_id, trigger="download_zip",
        )

        status_msg = None
        try:
            status_msg = await context.bot.send_message(
                chat_id=chat_id, text="📥 Упаковываю в ZIP..."
            )
        except Exception as e:
            logger.warning(f"download_project: status send failed: {e}")

        # ── Try ZIP first ──
        data_local = data if isinstance(data, dict) else {}
        title = (data_local.get("card_data") or {}).get("title") or data_local.get("idea") or "project"
        safe_title = re.sub(r'[<>:"/\\|?*]', '', str(title))[:40].strip() or "project"

        zip_path = None
        try:
            zip_path = await asyncio.to_thread(_zip_project, data_local)
        except Exception as e:
            logger.warning(f"download_project: zip build failed: {e}")

        if zip_path and zip_path.exists():
            zip_size = zip_path.stat().st_size
            zip_mb = zip_size / (1024 * 1024)
            logger.info(f"download_project: zip built {zip_path.name} ({zip_mb:.1f} MB)")
            # Empty ZIP = 22 bytes (end-of-central-directory only). Anything
            # under ~500 bytes means _project_dir() got a mismatched title and
            # mkdir'd an empty folder. Don't ship that archive — try to find
            # the real folder by card id prefix instead.
            if zip_size < 500:
                logger.warning(
                    f"download_project: zip is empty ({zip_size} bytes). "
                    "Title mismatch — trying prefix fallback."
                )
                try:
                    zip_path.unlink()
                except Exception:
                    pass
                zip_path = None
                card_id = data_local.get("notion_page_id") or ""
                fallback_dir = None
                if card_id:
                    try:
                        fallback_dir = _project_dir_by_prefix(card_id[:8])
                    except Exception as e:
                        logger.warning(f"download_project: prefix lookup raised: {e}")
                if fallback_dir and fallback_dir.exists():
                    logger.info(f"download_project: fallback folder found: {fallback_dir}")
                    try:
                        # Rebuild zip from the real folder.
                        import zipfile
                        alt_zip = ASSETS_DIR / f"{safe_title}_fallback.zip"
                        with zipfile.ZipFile(alt_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                            for f in fallback_dir.rglob("*"):
                                if f.is_file():
                                    zf.write(f, f.relative_to(fallback_dir))
                        if alt_zip.stat().st_size > 500:
                            zip_path = alt_zip
                            zip_size = zip_path.stat().st_size
                            zip_mb = zip_size / (1024 * 1024)
                            logger.info(f"download_project: fallback zip built ({zip_mb:.1f} MB)")
                    except Exception as e:
                        logger.error(f"download_project: fallback zip failed: {e}")
                if not zip_path:
                    # Still nothing — tell user honestly, don't send 22-byte archive.
                    if status_msg:
                        try:
                            await status_msg.edit_text(
                                "❌ Материалы этой карточки не найдены на сервере.\n\n"
                                "Скорее всего, карточка старая или папка была перемещена. "
                                "Можно запросить материалы напрямую из Notion — там должны "
                                "быть сохранены все ссылки.",
                                reply_markup=InlineKeyboardMarkup([[
                                    InlineKeyboardButton("◀️ К карточке",
                                        callback_data=f"notion_card:{(data_local.get('notion_page_id') or '')[:8]}"),
                                ]]),
                            )
                        except Exception:
                            pass
                    return
            if zip_size <= MAX_BOT_UPLOAD:
                try:
                    with open(zip_path, "rb") as fh:
                        await context.bot.send_document(
                            chat_id=chat_id,
                            document=fh,
                            filename=f"{safe_title}.zip",
                            caption=f"📦 Все материалы одним архивом ({zip_mb:.1f} МБ)",
                        )
                    if status_msg:
                        try:
                            await status_msg.delete()
                        except Exception:
                            pass
                    # Clean up server-side zip
                    try:
                        zip_path.unlink()
                    except Exception:
                        pass
                    return
                except Exception as e:
                    logger.warning(f"download_project: zip upload failed ({e}), falling back to per-file")
            else:
                # Too big — warn and fall through to per-file mode below
                logger.info(f"download_project: zip too big ({zip_mb:.1f}MB > 48MB), per-file fallback")
                try:
                    if status_msg:
                        await status_msg.edit_text(
                            f"ZIP получился {zip_mb:.1f} МБ — больше лимита Telegram (48 МБ).\n"
                            "Отправляю материалы по одному..."
                        )
                except Exception:
                    pass
                try:
                    zip_path.unlink()
                except Exception:
                    pass

        try:
            data_local = data if isinstance(data, dict) else {}
            if not data_local:
                logger.warning(f"download_project: pending data empty for user {user_id}")

            title = (data_local.get("card_data") or {}).get("title") or data_local.get("idea") or "project"
            safe_title = re.sub(r'[<>:"/\\|?*]', '', str(title))[:40].strip() or "project"

            proj_dir = _project_dir(data_local) if data_local else None
            logger.info(
                f"download_project: user={user_id} title='{safe_title}' "
                f"proj_dir={proj_dir} has_script={bool(data_local.get('script'))}"
            )

            sent_count = 0
            skipped_big = []  # [(name, size_mb)]

            # 1. Script as separate text file
            script = data_local.get("script")
            if script:
                try:
                    script_bytes = script.encode("utf-8")
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=BytesIO(script_bytes),
                        filename=f"{safe_title}_script.txt",
                        caption="📝 Сценарий",
                    )
                    sent_count += 1
                except Exception as e:
                    logger.warning(f"download_project: script send failed: {e}")

            # 2. Cover image
            cover_path = ASSETS_DIR / "last_cover.jpg"
            if cover_path.exists():
                try:
                    with open(cover_path, "rb") as fh:
                        await context.bot.send_document(
                            chat_id=chat_id,
                            document=fh,
                            filename=f"{safe_title}_cover.jpg",
                            caption="🖼 Обложка",
                        )
                    sent_count += 1
                except Exception as e:
                    logger.warning(f"download_project: cover send failed: {e}")

            # 3. Project dir files, sorted with light files first
            if proj_dir and proj_dir.exists():
                all_files = [f for f in sorted(proj_dir.iterdir()) if f.is_file()]
                for f in all_files:
                    size = f.stat().st_size
                    size_mb = size / (1024 * 1024)
                    if size > MAX_BOT_UPLOAD:
                        skipped_big.append((f.name, round(size_mb, 1)))
                        logger.info(f"download_project: skip big file {f.name} ({size_mb:.1f}MB)")
                        continue
                    try:
                        with open(f, "rb") as fh:
                            await context.bot.send_document(
                                chat_id=chat_id,
                                document=fh,
                                filename=f.name,
                                caption=f"📎 {f.name} ({size_mb:.1f} МБ)",
                            )
                        sent_count += 1
                    except Exception as fe:
                        logger.warning(f"download_project: skip {f.name}: {fe}")

            logger.info(f"download_project: sent {sent_count} files, skipped {len(skipped_big)} big")

            # Final summary
            if sent_count > 0:
                summary = f"✅ Отправлено {sent_count} файлов."
                if skipped_big:
                    big_list = "\n".join(f"  • {n} ({mb} МБ)" for n, mb in skipped_big)
                    summary += (
                        f"\n\n⚠️ Пропущено {len(skipped_big)} файлов > 48 МБ "
                        f"(лимит Telegram Bot API):\n{big_list}"
                    )
                if status_msg:
                    try:
                        await status_msg.edit_text(summary)
                    except Exception:
                        await context.bot.send_message(chat_id=chat_id, text=summary)
                else:
                    await context.bot.send_message(chat_id=chat_id, text=summary)
            else:
                msg = "📂 Пока нет материалов. Сначала создай сценарий и обложку."
                if status_msg:
                    try:
                        await status_msg.edit_text(msg)
                    except Exception:
                        await context.bot.send_message(chat_id=chat_id, text=msg)
                else:
                    await context.bot.send_message(chat_id=chat_id, text=msg)
        except Exception as e:
            logger.error(f"Download project error: {e}", exc_info=True)
            try:
                await context.bot.send_message(chat_id=chat_id, text=f"❌ Ошибка отправки: {e}")
            except Exception:
                pass
        return

    if query.data == "voiceover_choose":
        # Развилка озвучки (для «🎙 Озвучить» кнопок вне card-меню).
        await query.answer()
        data = pending.get(user_id) or {}
        if not data.get("script"):
            await query.edit_message_text("Нет сценария для озвучки.")
            return
        await query.edit_message_text(
            _VOICEOVER_CHOICE_TEXT,
            reply_markup=_voiceover_choice_keyboard(),
        )
        return

    if query.data == "voiceover_ownvoice":
        # Озвучить ролик своим голосом вместо TTS (с шага озвучки).
        data["state"] = "awaiting_voiceover_ownvoice"
        _save_pending(pending)
        await query.answer()
        await query.message.reply_text(
            "🎤 Пришли голосовое сообщение — оно станет озвучкой ролика и заменит "
            "ИИ-голос. Запиши весь текст одним сообщением (как хочешь, чтобы аватар "
            "это сказал)."
        )
        return

    if query.data == "finish":
        # Auto-move to "Подбор скринкаст" if card exists
        notion_page_id = data.get("notion_page_id")
        if notion_page_id:
            try:
                await asyncio.to_thread(update_notion_status, notion_page_id, "Подбор скринкаст")
            except Exception:
                logger.warning("Не удалось обновить статус при завершении")

        # Build full "what's next" menu instead of dead-end
        buttons = []
        if HEYGEN_API_KEY and data.get("voice_parts"):
            buttons.append([InlineKeyboardButton("🤖 Сгенерировать аватар", callback_data="heygen_looks")])
        if elevenlabs_client and not data.get("voice_parts"):
            buttons.append([InlineKeyboardButton("🎙 Озвучить", callback_data="voiceover_choose")])
        if not data.get("broll_approved"):
            buttons.append([InlineKeyboardButton("🎬 Подобрать B-roll", callback_data="broll")])
        if NOTION_GUIDES_DB and not data.get("guide_created"):
            buttons.append([InlineKeyboardButton("📎 Создать гайд", callback_data="create_guide")])
        buttons.append([InlineKeyboardButton("📝 Описание для публикации", callback_data="gen_description")])
        buttons.append([InlineKeyboardButton("📰 TG-пост по сценарию", callback_data="tgpost_from_script")])
        buttons.append([InlineKeyboardButton("🖼 Сменить обложку", callback_data="change_avatar")])

        # Auto-montage + Crosspost — always visible, hint what's missing
        card_id_prefix = data.get("notion_page_id", "")[:20]
        _tmp_proj = _project_dir(data)
        _has_avatar = bool(_tmp_proj and _tmp_proj.exists() and any(_tmp_proj.glob("avatar_*.mp4")))
        _has_broll = bool(_tmp_proj and _tmp_proj.exists() and any(_tmp_proj.glob("broll_*.mp4")))
        if _has_avatar and _has_broll:
            buttons.append([InlineKeyboardButton("🎬 Автосборка ролика", callback_data=f"card_assemble:{card_id_prefix}")])
        else:
            missing = []
            if not _has_avatar:
                missing.append("аватар")
            if not _has_broll:
                missing.append("B-roll")
            buttons.append([InlineKeyboardButton(f"🎬 Автосборка (нужен: {', '.join(missing)})", callback_data="asm_blocked")])
        if card_id_prefix:
            buttons.append([InlineKeyboardButton("📢 Кросс-постинг", callback_data=f"crosspost:{card_id_prefix}")])

        buttons.append([InlineKeyboardButton("📥 Скачать материалы", callback_data="download_project")])
        buttons.append([InlineKeyboardButton("🏁 Завершить", callback_data="finish_final")])

        # UX: меню «что дальше» шлём НОВЫМ сообщением (внизу чата, где
        # пользователь сейчас и смотрит) + редактируем старое сообщение в
        # короткий финальный статус. Иначе после нажатия «Готово» меню
        # появляется в редактируемом сообщении НАВЕРХУ — пользователю
        # приходится скроллить чат, и он думает, что кнопка не сработала.
        try:
            await query.edit_message_text(
                "✅ Готово — карточка переведена в «Подбор скринкаст»."
            )
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                "✅ Что дальше?\n\n"
                "📁 Все материалы сохранены в проект."
            ),
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if query.data == "finish_final":
        pending.pop(user_id, None)
        _save_pending(pending)
        await query.edit_message_text("✅ Всё готово! Отправь новую идею, когда будешь готов.")
        return

    if query.data == "cover_options":
        # Generate cover text options via button
        await query.edit_message_text("Генерирую варианты...")
        try:
            prev_options = data.get("all_cover_options", [])
            exclude_text = ""
            if prev_options:
                exclude_text = f"\n\nУже предлагались (НЕ ПОВТОРЯЙ и не используй те же слова): {', '.join(prev_options)}"

            _cover_system = _brand_cover_prompt(COVER_TEXT_PROMPT)
            response = claude.messages.create(
                model=COVER_MODEL,
                max_tokens=300,
                system=_cover_system,
                messages=[
                    {"role": "user", "content": f"Сценарий:\n{data['script']}\n\nПридумай 5 вирусных текстов для обложки. Найди в сценарии самый шокирующий факт или цифру — и построй обложку вокруг него. Каждый текст должен ИНТРИГОВАТЬ. Каждый на новой строке, только текст, без нумерации.{exclude_text}"},
                ],
            )
            options_text = response.content[0].text.strip()
            options = [line.strip().strip('"').strip("«»").strip("-").strip() for line in options_text.split("\n") if line.strip()]
            options = [o for o in options if 10 <= len(o) <= 50 and len(o.split()) >= 2][:5]

            data.setdefault("all_cover_options", []).extend(options)
            data["cover_options"] = options
            data["state"] = "cover_approval"
            _save_pending(pending)

            buttons = [[InlineKeyboardButton(opt, callback_data=f"cover_pick:{i}")] for i, opt in enumerate(options)]
            buttons.append([InlineKeyboardButton("🔄 Ещё варианты", callback_data="cover_options")])
            buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
            keyboard = InlineKeyboardMarkup(buttons)

            await query.edit_message_text(
                "🖼 Выбери текст для обложки:\n\n"
                + "\n".join(f"• {opt}" for opt in options)
                + "\n\nНажми на вариант или напиши свой.",
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.error(f"Ошибка: {e}", exc_info=True)
            await query.edit_message_text(f"Ошибка: {e}")
        return

    if query.data.startswith("cover_photo:"):
        # 3-photo cover picker: [1][2][3] choose a background, reload re-rolls.
        # Guard against lost session (bot restart / /start cleared pending):
        # a stale button would otherwise crash on None data and die silently.
        if not data:
            await query.answer(
                "Подборка устарела — открой карточку заново и сгенерируй сценарий.",
                show_alert=True,
            )
            return
        arg = query.data.split(":", 1)[1]
        if arg == "reload":
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            shown = await _show_cover_photo_picker(
                query.get_bot(), query.message.chat_id, data
            )
            if not shown:
                await query.get_bot().send_message(
                    chat_id=query.message.chat_id,
                    text="В пуле нет фото для обложки. Загрузи фото кнопкой ниже.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("📤 Загрузить фото", callback_data="cover_pool_upload")
                    ]]),
                )
            return
        # Numeric pick → lock in that photo, move on to cover-text options.
        try:
            idx = int(arg)
        except ValueError:
            await query.answer("Не понял выбор фото")
            return
        choices = data.get("cover_photo_choices", [])
        if not (0 <= idx < len(choices)):
            await query.answer("Это фото больше недоступно — нажми «🔄 Другие фото»", show_alert=True)
            return
        data["chosen_avatar"] = choices[idx]
        _save_pending(pending)
        try:
            await query.message.edit_text(f"📷 Фото для обложки выбрано (вариант {idx + 1}).")
        except Exception:
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
        await _send_cover_text_options(query.get_bot(), query.message.chat_id, data)
        return

    if query.data == "avatar_confirm":
        # User confirmed the avatar, proceed to cover text options
        await query.message.edit_reply_markup(reply_markup=None)
        status_msg = await query.get_bot().send_message(
            chat_id=query.message.chat_id,
            text="🖼 Генерирую варианты обложки..."
        )
        try:
            prev_options = data.get("all_cover_options", [])
            exclude_text = ""
            if prev_options:
                exclude_text = f"\n\nУже предлагались (НЕ ПОВТОРЯЙ и не используй те же слова): {', '.join(prev_options)}"

            _cover_system = _brand_cover_prompt(COVER_TEXT_PROMPT)
            response = claude.messages.create(
                model=COVER_MODEL,
                max_tokens=300,
                system=_cover_system,
                messages=[
                    {"role": "user", "content": f"Сценарий:\n{data['script']}\n\nПридумай 5 вирусных текстов для обложки. Найди в сценарии самый шокирующий факт или цифру — и построй обложку вокруг него. Каждый текст должен ИНТРИГОВАТЬ. Каждый на новой строке, только текст, без нумерации.{exclude_text}"},
                ],
            )
            options_text = response.content[0].text.strip()
            options = [line.strip().strip('"').strip("«»").strip("-").strip() for line in options_text.split("\n") if line.strip()]
            options = [o for o in options if 10 <= len(o) <= 50 and len(o.split()) >= 2][:5]

            data.setdefault("all_cover_options", []).extend(options)
            data["cover_options"] = options
            data["state"] = "cover_approval"
            _save_pending(pending)

            buttons = [[InlineKeyboardButton(f"📝 {opt}", callback_data=f"cover_pick:{i}")] for i, opt in enumerate(options)]
            buttons.append([InlineKeyboardButton("🔄 Другие варианты", callback_data="cover_options")])
            buttons.append([InlineKeyboardButton("🔄 Сменить фото", callback_data="avatar_pick:random")])
            await status_msg.edit_text(
                "🖼 Выбери текст для обложки или напиши свой:",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception as e:
            logger.error(f"Cover options error: {e}", exc_info=True)
            await status_msg.edit_text(f"Ошибка: {e}")
        return

    if query.data == "avatar_pick_by_number":
        # Show available numbers and ask for input (brand-aware pool)
        _brand_now = _get_active_brand_name()
        pool_dir = _avatars_dir_for_brand(_brand_now)
        avatars = []
        if pool_dir.exists():
            avatars = sorted([f.name for f in pool_dir.iterdir() if f.suffix.lower() in (".jpg", ".jpeg", ".png")])
        # Extract numbers from filenames
        nums = []
        for a in avatars:
            m = re.match(r'^(\d+)', a)
            if m:
                nums.append(m.group(1))
        nums_str = ", ".join(nums) if nums else "нет пронумерованных файлов"
        _brand_hint = (
            f" [бренд: {_brand_now}]" if _brand_now != "default" else ""
        )
        data["state"] = "avatar_by_number"
        _save_pending(pending)
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.get_bot().send_message(
            chat_id=query.message.chat_id,
            text=f"🔢 Введи номер фото{_brand_hint} (доступные: {nums_str}):",
        )
        return

    if query.data.startswith("avatar_pick:"):
        choice = query.data.split(":", 1)[1]
        if choice == "random":
            # Pick a new random avatar and show preview (brand-aware)
            pool_dir = _avatars_dir_for_brand(_get_active_brand_name())
            avatars = []
            if pool_dir.exists():
                avatars = sorted([f.name for f in pool_dir.iterdir() if f.suffix.lower() in (".jpg", ".jpeg", ".png")])
            if avatars:
                chosen = random.choice(avatars)
                data["chosen_avatar"] = str(pool_dir / chosen)
                _save_pending(pending)

                buttons = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Использовать это фото", callback_data="avatar_confirm")],
                    [InlineKeyboardButton("🎲 Другое фото", callback_data="avatar_pick:random")],
                    [InlineKeyboardButton("🔢 Выбрать по номеру", callback_data="avatar_pick_by_number")],
                    [InlineKeyboardButton("📤 Загрузить своё фото в библиотеку", callback_data="cover_pool_upload")],
                ])
                # Delete old photo message and send new one
                try:
                    await query.message.delete()
                except Exception:
                    pass
                with open(str(pool_dir / chosen), "rb") as photo:
                    await query.get_bot().send_photo(
                        chat_id=query.message.chat_id,
                        photo=photo,
                        caption=f"📷 Фото для обложки: {chosen}",
                        reply_markup=buttons,
                    )
                return
        else:
            # Explicit filename — find it in brand pool first, fallback to global
            pool_dir = _avatars_dir_for_brand(_get_active_brand_name())
            brand_path = pool_dir / choice
            avatar_path = str(brand_path if brand_path.exists() else AVATARS_DIR / choice)
            data["chosen_avatar"] = avatar_path
        _save_pending(pending)

        # Now generate cover text options
        await query.edit_message_text("🖼 Генерирую варианты обложки...")
        try:
            prev_options = data.get("all_cover_options", [])
            exclude_text = ""
            if prev_options:
                exclude_text = f"\n\nУже предлагались (НЕ ПОВТОРЯЙ и не используй те же слова): {', '.join(prev_options)}"

            _cover_system = _brand_cover_prompt(COVER_TEXT_PROMPT)
            response = claude.messages.create(
                model=COVER_MODEL,
                max_tokens=300,
                system=_cover_system,
                messages=[
                    {"role": "user", "content": f"Сценарий:\n{data['script']}\n\nПридумай 5 вирусных текстов для обложки. Найди в сценарии самый шокирующий факт или цифру — и построй обложку вокруг него. Каждый текст должен ИНТРИГОВАТЬ. Каждый на новой строке, только текст, без нумерации.{exclude_text}"},
                ],
            )
            options_text = response.content[0].text.strip()
            options = [line.strip().strip('"').strip("«»").strip("-").strip() for line in options_text.split("\n") if line.strip()]
            options = [o for o in options if 10 <= len(o) <= 50 and len(o.split()) >= 2][:5]

            data.setdefault("all_cover_options", []).extend(options)

            if not options:
                await query.edit_message_text("Не получилось сгенерировать. Напиши свой вариант.")
                return

            buttons = [[InlineKeyboardButton(opt, callback_data=f"cover_pick:{i}")] for i, opt in enumerate(options)]
            buttons.append([InlineKeyboardButton("🔄 Ещё варианты", callback_data="cover_options")])
            buttons.append([InlineKeyboardButton("◀️ Сменить фото", callback_data="change_avatar")])
            buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])

            data["cover_options"] = options
            data["state"] = "cover_approval"
            _save_pending(pending)

            keyboard = InlineKeyboardMarkup(buttons)
            await query.edit_message_text(
                "🖼 Выбери текст для обложки или напиши свой:\n\n"
                + "\n".join(f"• {opt}" for opt in options),
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.error(f"Ошибка генерации обложки: {e}", exc_info=True)
            await query.edit_message_text(f"Ошибка: {e}")
        return

    # Cover-pool upload — приём фото от пользователя в библиотеку обложек.
    # Сохраняется в assets/avatars/<brand>/NN_uploaded_<hash>.png и сразу
    # становится доступно в "📷 Другое фото" / "🎲 Другое фото" / "🔢 По номеру".
    if query.data == "cover_pool_upload":
        user_id = query.from_user.id
        _brand = _get_active_brand_name()
        pool_dir = _avatars_dir_for_brand(_brand)
        pending.setdefault(user_id, {})["state"] = "cover_pool_upload_waiting"
        pending[user_id]["cover_pool_target_brand"] = _brand
        _save_pending(pending)
        await query.message.reply_text(
            f"📤 Скинь фото для библиотеки обложек бренда **{_brand}**.\n\n"
            f"Пойдёт в `{pool_dir.name}/` и сразу станет доступно во всех "
            f"будущих обложках этого бренда.\n\n"
            f"Требования:\n"
            f"• PNG / JPG\n"
            f"• Желательно 9:16 (вертикальное), 1080×1920\n"
            f"• Лицо в верхней половине кадра (под текст обложки)\n"
            f"• Без посторонних людей",
            parse_mode="Markdown",
        )
        return

    if query.data == "change_avatar" or effective_action == "change_avatar":
        # Show random avatar with confirm/next buttons (brand-aware pool)
        pool_dir = _avatars_dir_for_brand(_get_active_brand_name())
        avatars = []
        if pool_dir.exists():
            avatars = sorted([f.name for f in pool_dir.iterdir() if f.suffix.lower() in (".jpg", ".jpeg", ".png")])
        if avatars:
            chosen = random.choice(avatars)
            avatar_file = pool_dir / chosen
            logger.info(f"change_avatar: chosen={chosen}, size={avatar_file.stat().st_size}")
            data["chosen_avatar"] = str(avatar_file)
            _save_pending(pending)
            buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Использовать это фото", callback_data="avatar_confirm")],
                [InlineKeyboardButton("🎲 Другое фото", callback_data="avatar_pick:random")],
                [InlineKeyboardButton("🔢 Выбрать по номеру", callback_data="avatar_pick_by_number")],
                [InlineKeyboardButton("📤 Загрузить своё фото в библиотеку", callback_data="cover_pool_upload")],
            ])
            try:
                await query.message.delete()
                logger.info("change_avatar: message deleted, sending photo...")
            except Exception:
                logger.info("change_avatar: delete failed, sending photo...")
            with open(str(avatar_file), "rb") as photo:
                await query.get_bot().send_photo(
                    chat_id=query.message.chat_id,
                    photo=photo,
                    caption=f"📷 Фото для обложки: {chosen}",
                    reply_markup=buttons,
                )
        return

    if query.data.startswith("cover_pick:"):
        idx = int(query.data.split(":")[1])
        options = data.get("cover_options", [])
        if idx < len(options):
            data["cover_text"] = options[idx]
            _save_pending(pending)

            # Generate immediately
            await query.edit_message_text("Сохраняю в Notion + генерирую обложку...")
            try:
                card_data = data["card_data"]
                script_text = data["script"]
                cover_text = data["cover_text"]

                # Generate cover image with chosen avatar
                cover_path = str(ASSETS_DIR / "last_cover.jpg")
                chosen_avatar = data.get("chosen_avatar")
                generate_cover(cover_text, cover_path, avatar_override=chosen_avatar)

                # Also persist cover into the project folder so it lives
                # alongside the video (last_cover.jpg gets overwritten when
                # the next card generates its own cover).
                try:
                    _save_to_project(data, "cover.jpg", cover_path)
                    data["cover_path"] = cover_path
                except Exception as _e:
                    logger.warning(f"Cover save-to-project failed: {_e}")

                # Send preview with buttons below the photo
                buttons = [
                    [InlineKeyboardButton("✅ Сохранить в Notion", callback_data="cover_confirm")],
                    [InlineKeyboardButton("🔄 Другой текст обложки", callback_data="cover_redo_text")],
                    [InlineKeyboardButton("📷 Другое фото", callback_data="change_avatar")],
                    [InlineKeyboardButton("📤 Загрузить своё фото в библиотеку", callback_data="cover_pool_upload")],
                    [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
                ]
                try:
                    await query.message.delete()
                except Exception:
                    pass
                with open(cover_path, "rb") as cover_file:
                    await query.get_bot().send_photo(
                        chat_id=query.message.chat_id,
                        photo=cover_file,
                        caption=f"🖼 Обложка: «{cover_text}»\n\nНравится? Сохраняю или переделать?",
                        reply_markup=InlineKeyboardMarkup(buttons),
                    )

                data["cover_preview_sent"] = True
                _save_pending(pending)
            except Exception as e:
                logger.error(f"Ошибка: {e}", exc_info=True)
                await query.edit_message_text(f"Ошибка: {e}")
        return

    if query.data == "cover_redo_text":
        # Go back to cover text selection with current avatar
        data.pop("cover_text", None)
        _save_pending(pending)
        await query.edit_message_text("🖼 Генерирую новые варианты обложки...")
        try:
            prev_options = data.get("all_cover_options", [])
            exclude_text = ""
            if prev_options:
                exclude_text = f"\n\nУже предлагались (НЕ ПОВТОРЯЙ и не используй те же слова): {', '.join(prev_options)}"

            _cover_system = _brand_cover_prompt(COVER_TEXT_PROMPT)
            response = claude.messages.create(
                model=COVER_MODEL,
                max_tokens=300,
                system=_cover_system,
                messages=[
                    {"role": "user", "content": f"Сценарий:\n{data['script']}\n\nПридумай 5 вирусных текстов для обложки. Найди в сценарии самый шокирующий факт или цифру — и построй обложку вокруг него. Каждый текст должен ИНТРИГОВАТЬ. Каждый на новой строке, только текст, без нумерации.{exclude_text}"},
                ],
            )
            options_text = response.content[0].text.strip()
            options = [line.strip().strip('"').strip("«»").strip("-").strip() for line in options_text.split("\n") if line.strip()]
            options = [o for o in options if 10 <= len(o) <= 50 and len(o.split()) >= 2][:5]

            data.setdefault("all_cover_options", []).extend(options)

            if not options:
                await query.edit_message_text("Не получилось сгенерировать. Напиши свой вариант.")
                return

            buttons = [[InlineKeyboardButton(opt, callback_data=f"cover_pick:{i}")] for i, opt in enumerate(options)]
            buttons.append([InlineKeyboardButton("🔄 Ещё варианты", callback_data="cover_options")])
            buttons.append([InlineKeyboardButton("◀️ Сменить фото", callback_data="change_avatar")])
            buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])

            data["cover_options"] = options
            data["state"] = "cover_approval"
            _save_pending(pending)

            await query.edit_message_text(
                "🖼 Выбери текст для обложки или напиши свой:\n\n"
                + "\n".join(f"• {opt}" for opt in options),
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception as e:
            logger.error(f"Ошибка: {e}", exc_info=True)
            await query.edit_message_text(f"Ошибка: {e}")
        return

    if query.data == "cover_confirm":
        # User confirmed the cover — now save to Notion
        # The message might be a photo (with caption) or text — handle both
        try:
            await query.edit_message_caption(caption="📋 Сохраняю в Notion...")
        except Exception:
            try:
                await query.edit_message_text("📋 Сохраняю в Notion...")
            except Exception:
                pass
        try:
            card_data = data["card_data"]
            script_text = data["script"]
            cover_text = data.get("cover_text", "")

            # Save cover to permanent storage and get URL
            cover_url = None
            cover_path = str(ASSETS_DIR / "last_cover.jpg")
            try:
                cover_url = await asyncio.to_thread(
                    save_cover_permanent, cover_path, card_data.get("title", "")
                )
            except Exception as e:
                logger.warning(f"Не удалось сохранить обложку: {e}")

            existing_page_id = data.get("notion_page_id")

            if existing_page_id:
                # Update existing Notion card (loaded from /cards)
                # Find and replace existing cover image block, or add new one
                if cover_url:
                    try:
                        def _update_cover_in_notion():
                            # Get all blocks on the page
                            blocks = notion.blocks.children.list(block_id=existing_page_id)
                            cover_block_id = None
                            heading_block_id = None
                            # Find the "Обложка" heading and the image block after it
                            for i, block in enumerate(blocks["results"]):
                                if block["type"] == "heading_2":
                                    rt = block["heading_2"].get("rich_text", [])
                                    if rt and "Обложка" in rt[0].get("text", {}).get("content", ""):
                                        heading_block_id = block["id"]
                                        # Next block should be the image
                                        if i + 1 < len(blocks["results"]) and blocks["results"][i + 1]["type"] == "image":
                                            cover_block_id = blocks["results"][i + 1]["id"]
                                        break

                            # Delete old cover blocks if they exist
                            if cover_block_id:
                                notion.blocks.delete(block_id=cover_block_id)
                            if heading_block_id:
                                notion.blocks.delete(block_id=heading_block_id)

                            # Add fresh heading + image (appended at end of page)
                            notion.blocks.children.append(
                                block_id=existing_page_id,
                                children=[
                                    {
                                        "object": "block",
                                        "type": "heading_2",
                                        "heading_2": {"rich_text": [{"text": {"content": "Обложка"}}]},
                                    },
                                    {
                                        "object": "block",
                                        "type": "image",
                                        "image": {"type": "external", "external": {"url": cover_url}},
                                    },
                                ],
                            )
                            logger.info(f"Обложка обновлена в Notion: {existing_page_id}")

                        await asyncio.to_thread(_update_cover_in_notion)
                    except Exception as e:
                        logger.warning(f"Не удалось обновить обложку в Notion: {e}")

                    # Also set the page-level cover banner (board/gallery view).
                    # Pre-saved cards (incl. all «Банк идей» ones, which create
                    # the page at `approve` before any cover exists) never got a
                    # banner otherwise.
                    try:
                        await asyncio.to_thread(
                            lambda: notion.pages.update(
                                page_id=existing_page_id,
                                cover={"type": "external", "external": {"url": cover_url}},
                            )
                        )
                        logger.info(f"Баннер-обложка установлен в Notion: {existing_page_id}")
                    except Exception as e:
                        logger.warning(f"Не удалось установить баннер-обложку: {e}")

                notion_page_id = existing_page_id
                notion_url = f"https://www.notion.so/{existing_page_id.replace('-', '')}"

                # Move to "Сценарий | озвучка" if not already past that
                try:
                    await asyncio.to_thread(update_notion_status, notion_page_id, "Сценарий | озвучка")
                except Exception:
                    logger.warning("Не удалось обновить статус карточки")
            else:
                # Create new Notion card
                notion_url, notion_page_id = await asyncio.to_thread(
                    create_notion_card, card_data, script_text, cover_url,
                    source_urls=data.get("source_urls"),
                    youtube_urls=data.get("youtube_urls"),
                )

                # Auto-move to "Сценарий | озвучка" since script+cover are done
                try:
                    await asyncio.to_thread(update_notion_status, notion_page_id, "Сценарий | озвучка")
                except Exception:
                    logger.warning("Не удалось обновить статус карточки")

            data["notion_url"] = notion_url
            data["notion_page_id"] = notion_page_id
            data["state"] = "done"
            _save_pending(pending)

            # Save script and cover to project folder
            _save_text_to_project(data, "script.txt", script_text)
            if data.get("cover_path") and Path(data["cover_path"]).exists():
                _save_to_project(data, "cover.jpg", data["cover_path"])

            # Update the cover message caption with Notion info
            action_word = "обновлена" if existing_page_id else "создана"
            success_caption = (
                f"✅ Карточка {action_word}!\n\n"
                f"📋 Notion: {notion_url}\n"
                f"🖼 Обложка: «{cover_text}»\n"
                f"📊 Статус: Сценарий | озвучка"
            )
            try:
                await query.edit_message_caption(caption=success_caption)
            except Exception:
                try:
                    await query.edit_message_text(success_caption)
                except Exception:
                    pass

            # Send next step buttons as NEW message BELOW the cover
            buttons = []
            if NOTION_GUIDES_DB:
                buttons.append([InlineKeyboardButton("📎 Создать гайд для подписчиков", callback_data="create_guide")])
            if PEXELS_API_KEY or PIXABAY_API_KEY:
                buttons.append([InlineKeyboardButton("🎬 Видеоряд (B-roll)", callback_data="broll")])
            if elevenlabs_client:
                buttons.append([InlineKeyboardButton("🎙 Озвучить", callback_data="voiceover_choose")])
            buttons.append([InlineKeyboardButton("✅ Готово", callback_data="finish")])

            await query.get_bot().send_message(
                chat_id=query.message.chat_id,
                text="Что дальше?",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception as e:
            logger.error(f"Ошибка сохранения: {e}", exc_info=True)
            await query.edit_message_text(f"Ошибка: {e}")
        return

    if query.data == "save_to_notion":
        # Save script to Notion as idea (without continuing the pipeline)
        await query.edit_message_text("📋 Сохраняю в Notion...")
        try:
            script_text = data.get("script", "")
            card_data = data.get("card_data")
            if not card_data:
                # Fallback: create minimal card_data from script
                title = script_text.split("\n")[0][:80] if script_text else "Без названия"
                card_data = {
                    "title": title,
                    "cta": "",
                    "rubric": "Свободный формат",
                    "platforms": _default_platforms(),
                    "format": ["Short video"],
                }
            notion_url, notion_page_id = await asyncio.to_thread(
                create_notion_card, card_data, script_text,
                source_urls=data.get("source_urls"),
                youtube_urls=data.get("youtube_urls"),
            )
            data["notion_url"] = notion_url
            data["notion_page_id"] = notion_page_id
            data["state"] = "done"
            _save_pending(pending)

            # Save script to project folder
            _save_text_to_project(data, "script.txt", script_text)

            title = card_data.get("title", "")
            await query.get_bot().send_message(
                chat_id=query.message.chat_id,
                text=(
                    f"✅ Сохранено в Notion!\n\n"
                    f"📋 {title}\n"
                    f"🔗 {notion_url}\n"
                    f"📊 Статус: Идеи | старт\n\n"
                    f"Когда вернёшься — найдёшь карточку в Notion и продолжишь."
                ),
            )
        except Exception as e:
            logger.error(f"Ошибка сохранения в Notion: {e}", exc_info=True)
            await query.edit_message_text(f"Ошибка: {e}")
        return

    if query.data == "approve":
        # Billing gate — only for non-bypassed users. Admins and BILLING_ENABLED=0
        # go straight through as before. Clients get a balance check: if not
        # enough funds, reject here before creating anything in Notion.
        if not _billing_is_bypassed(user_id):
            try:
                ok, reason, price = await asyncio.to_thread(
                    billing_api.can_create_video, user_id, None  # mode=None → use client default
                )
            except Exception as e:
                logger.error(f"[billing] can_create_video failed: {e}", exc_info=True)
                ok, reason, price = True, "ok", 0  # fail-open on tech error (don't block)
            if not ok:
                price_str = f"{price} ₽" if price else "0 ₽"
                await query.edit_message_text(
                    f"💰 Недостаточно средств для ролика ({price_str}).\n\n"
                    f"Причина: {reason}\n\n"
                    f"Пополните баланс: /billing → «Запросить пополнение»\n"
                    f"Или свяжитесь с {_BILLING_SUPPORT}."
                )
                return

        data["state"] = "cover_approval"
        _save_pending(pending)

        # ── Pre-save to Notion ─────────────────────────────────────────────
        # Create the Notion card NOW, before moving on to the cover step.
        # Reason: previously the card was only created at `cover_confirm`,
        # so if Artem (or any user) bailed out on the cover screen, no card
        # appeared in Notion at all — even though he'd already approved the
        # script. Now:
        #   approve → card created in Notion (status "Идеи | старт")
        #   cover_confirm → card updated with cover + status "Сценарий | озвучка"
        # If `cover_confirm` never fires, the script is still safely in Notion.
        if not data.get("notion_page_id"):
            script_text = data.get("script", "")
            card_data = data.get("card_data")
            if script_text and card_data:
                try:
                    notion_url, notion_page_id = await asyncio.to_thread(
                        create_notion_card, card_data, script_text,
                        source_urls=data.get("source_urls"),
                        youtube_urls=data.get("youtube_urls"),
                    )
                    data["notion_url"] = notion_url
                    data["notion_page_id"] = notion_page_id
                    # Cache the card's brand in pending so deep callbacks
                    # (heygen_looks, assemble, cover) resolve the right brand
                    # even after a bot restart.
                    _brand_now = _get_active_brand_name()
                    data["card_brand"] = _brand_now
                    _save_pending(pending)
                    # Save script to project folder too
                    _save_text_to_project(data, "script.txt", script_text)
                    logger.info(
                        f"[approve] pre-saved card to Notion: {notion_page_id} "
                        f"(brand={_brand_now}, cached in pending)"
                    )
                    # Billing — bind this video to the client so subsequent
                    # charges know who to debit. Admins skip. Mode defaults
                    # to the client's configured default ("self" or "full").
                    if not _billing_is_bypassed(user_id):
                        try:
                            client = await asyncio.to_thread(billing_api.get_client, user_id)
                            mode = (client.mode_default if client else "self")
                            await asyncio.to_thread(
                                billing_api.register_video,
                                notion_page_id, user_id, mode, card_data.get("title", ""),
                            )
                            logger.info(
                                f"[billing] video registered: {notion_page_id[:12]}... "
                                f"client_tg={user_id} mode={mode}"
                            )
                        except Exception as e:
                            logger.error(f"[billing] register_video failed: {e}", exc_info=True)
                except Exception as e:
                    logger.error(f"[approve] Notion pre-save failed: {e}", exc_info=True)
                    # Don't block the flow — user can retry via the cover step

        # Remove buttons from script message, keep the script text visible
        script_text = data.get("script", "")
        char_count = len(script_text)
        saved_hint = ""
        if data.get("notion_url"):
            saved_hint = f"\n💾 Карточка уже в Notion: {data['notion_url']}\n"
        await query.edit_message_text(
            f"✅ СЦЕНАРИЙ УТВЕРЖДЁН:\n\n"
            f"{script_text}\n\n"
            f"———\n"
            f"📊 {char_count} символов"
            f"{saved_hint}"
        )

        # Show avatar selection (brand-aware pool)
        pool_dir = _avatars_dir_for_brand(_get_active_brand_name())
        avatars = []
        if pool_dir.exists():
            avatars = sorted([f.name for f in pool_dir.iterdir() if f.suffix.lower() in (".jpg", ".jpeg", ".png")])

        if len(avatars) > 1:
            # Show 3 random pool photos as an album + [1][2][3] + reload, so the
            # user picks a cover background from a spread instead of re-rolling
            # one photo at a time.
            shown = await _show_cover_photo_picker(
                query.get_bot(), query.message.chat_id, data
            )
            if shown:
                return
            # Fallback: picker couldn't render → keep the old single-photo screen.
            chosen = random.choice(avatars)
            data["chosen_avatar"] = str(pool_dir / chosen)
            _save_pending(pending)
            buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Использовать это фото", callback_data="avatar_confirm")],
                [InlineKeyboardButton("🎲 Другое фото", callback_data="avatar_pick:random")],
                [InlineKeyboardButton("📤 Загрузить своё фото в библиотеку", callback_data="cover_pool_upload")],
            ])
            with open(str(pool_dir / chosen), "rb") as photo:
                await query.get_bot().send_photo(
                    chat_id=query.message.chat_id,
                    photo=photo,
                    caption=f"📷 Фото для обложки: {chosen}",
                    reply_markup=buttons,
                )
            return

        # If exactly 1 avatar in the brand pool — auto-pick it so the cover
        # is rendered on the correct brand photo, not on the global default.
        # Without this, shoe-brand covers were landing on Artem's Avatar.jpg.
        if len(avatars) == 1:
            data["chosen_avatar"] = str(pool_dir / avatars[0])
            _save_pending(pending)
            logger.info(
                f"[approve] single-photo brand pool, auto-selected {avatars[0]} "
                f"(brand={_get_active_brand_name()})"
            )

        # If only 1 or no avatars — skip selection, go to cover text options
        status_msg = await query.get_bot().send_message(
            chat_id=query.message.chat_id,
            text="🖼 Генерирую варианты обложки..."
        )

        try:
            # Brand-aware cover prompt
            _brand = _get_active_brand()
            _cover_system = _brand_cover_prompt(COVER_TEXT_PROMPT)
            response = claude.messages.create(
                model=COVER_MODEL,
                max_tokens=300,
                system=_cover_system,
                messages=[
                    {"role": "user", "content": f"Сценарий:\n{data['script']}\n\nПридумай 5 вирусных текстов для обложки. Каждый должен ИНТРИГОВАТЬ, а не пересказывать факт из сценария. Каждый на новой строке, только текст, без нумерации."},
                ],
            )
            options_text = response.content[0].text.strip()
            options = [line.strip().strip('"').strip("«»").strip("-").strip() for line in options_text.split("\n") if line.strip()]
            options = [o for o in options if 10 <= len(o) <= 50 and len(o.split()) >= 2][:5]

            data.setdefault("all_cover_options", []).extend(options)

            if not options:
                await status_msg.edit_text("Не получилось сгенерировать. Напиши свой вариант.")
                return

            buttons = [[InlineKeyboardButton(opt, callback_data=f"cover_pick:{i}")] for i, opt in enumerate(options)]
            buttons.append([InlineKeyboardButton("🔄 Ещё варианты", callback_data="cover_options")])
            buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])

            data["cover_options"] = options
            data["state"] = "cover_approval"
            _save_pending(pending)

            keyboard = InlineKeyboardMarkup(buttons)
            await status_msg.edit_text(
                "🖼 Выбери текст для обложки или напиши свой:\n\n"
                + "\n".join(f"• {opt}" for opt in options),
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.error(f"Ошибка: {e}", exc_info=True)
            await query.edit_message_text(f"Ошибка: {e}")
        return

    if query.data == "cover_ok":
        # Generate cover preview (custom text from user)
        await query.edit_message_text("🖼 Генерирую обложку...")

        try:
            cover_text = data.get("cover_text", "")
            cover_path = str(ASSETS_DIR / "last_cover.jpg")
            chosen_avatar = data.get("chosen_avatar")
            generate_cover(cover_text, cover_path, avatar_override=chosen_avatar)

            buttons = [
                [InlineKeyboardButton("✅ Сохранить в Notion", callback_data="cover_confirm")],
                [InlineKeyboardButton("🔄 Другой текст обложки", callback_data="cover_redo_text")],
                [InlineKeyboardButton("📷 Другое фото", callback_data="change_avatar")],
                [InlineKeyboardButton("📤 Загрузить своё фото в библиотеку", callback_data="cover_pool_upload")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
            ]
            try:
                await query.message.delete()
            except Exception:
                pass
            with open(cover_path, "rb") as cover_file:
                await query.get_bot().send_photo(
                    chat_id=query.message.chat_id,
                    photo=cover_file,
                    caption=f"🖼 Обложка: «{cover_text}»\n\nНравится? Сохраняю или переделать?",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )

        except Exception as e:
            logger.error(f"Ошибка при сохранении: {e}", exc_info=True)
            await query.edit_message_text(f"Ошибка при сохранении: {e}")
        return


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log unhandled errors."""
    logger.error(f"Необработанная ошибка: {context.error}", exc_info=context.error)


async def post_init(application):
    """Set bot commands menu and menu button after startup."""
    from telegram import MenuButtonCommands
    await application.bot.set_my_commands([
        ("start", "Главный экран — с чего начать"),
        ("script", "📝 Записать готовый сценарий (без переписывания)"),
        ("notion", "💡 Закинуть идею в Notion (без сценария)"),
        ("cards", "📋 Карточки в работе"),
        ("ideas", "🧠 Бэклог идей"),
        ("cards_all", "📚 Все карточки включая опубликованные"),
        ("calendar", "🗓 Календарь публикаций"),
        ("stats", "📊 Последний замер подписчиков"),
        # /update и /report скрыты для Maksim (20 мая 2026) — handler-ы
        # сохранены, при нужде раскомментировать здесь и в register-блоке.
        ("selfie", "🎥 Живое видео с телефона + субтитры"),
        ("image", "🖼 Сгенерировать фото по описанию"),
        ("video", "🎬 Сгенерировать видео (5 или 10 сек)"),
        ("heygen_test", "🧪 Тест аватара: фото + аудио → видео"),
        ("tgpost", "📝 TG-пост эксперимента"),
        # /brand и /launches скрыты для Maksim (20 мая 2026) — один бренд +
        # инфра Launch Monitor будет переподключена под трендомер ниш.
        ("yt_auth", "Авторизовать YouTube"),
        ("vk_auth", "Авторизовать VK"),
        ("admin", "⚙️ Админ-панель (биллинг, клиенты)"),
        ("billing", "💰 Баланс и биллинг"),
        ("help", "❓ Все команды"),
    ])
    await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    logger.info("Меню команд установлено")

    # Start Instagram DM webhook server
    try:
        _webhook_runner = await start_webhook_server()
        application.bot_data["ig_webhook_runner"] = _webhook_runner
        logger.info("Instagram DM webhook server started")
    except Exception as e:
        logger.warning(f"Instagram DM webhook server failed to start: {e}")


async def ig_auth_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /ig_auth — show Instagram authorization link."""
    logger.info(f"[user:{update.effective_user.id}] /ig_auth")
    try:
        url = instagram_auth_url()
        await update.message.reply_text(
            f"🔗 Авторизуй Instagram через Facebook:\n\n"
            f"{url}\n\n"
            f"После авторизации скопируй code из URL (параметр ?code=...) "
            f"и отправь его командой:\n"
            f"/ig_code КОД",
        )
    except Exception as e:
        logger.error(f"ig_auth error: {e}", exc_info=True)
        await update.message.reply_text(f"Ошибка: {e}")


async def ig_code_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /ig_code CODE — exchange auth code for Instagram token."""
    if not context.args:
        await update.message.reply_text("Использование: /ig_code КОД\n\nГде КОД — значение параметра code из URL после авторизации.")
        return

    code = context.args[0].strip().rstrip("#_")
    await update.message.reply_text("⏳ Обмениваю код на токен...")

    result = await asyncio.to_thread(instagram_exchange_code, code)
    if result:
        ig_id = result.get("ig_user_id", "?")
        await update.message.reply_text(
            f"✅ Instagram авторизован!\n\n"
            f"IG Account: {ig_id}\n"
            f"Теперь кросспостинг и CTA будут работать."
        )
    else:
        await update.message.reply_text("❌ Ошибка авторизации. Проверь код и попробуй снова (/ig_auth).")


async def yt_auth_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /yt_auth — show YouTube authorization link."""
    logger.info(f"[user:{update.effective_user.id}] /yt_auth")
    try:
        url = youtube_auth_url()
        await update.message.reply_text(
            f"🔗 Авторизуй YouTube:\n\n"
            f"{url}\n\n"
            f"Нажми на ссылку → войди в Google-аккаунт → разреши доступ.\n"
            f"После авторизации токен сохранится автоматически.",
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"yt_auth error: {e}", exc_info=True)
        await update.message.reply_text(f"Ошибка: {e}")


async def vk_auth_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /vk_auth — show VK OAuth authorization link (VK ID + PKCE)."""
    logger.info(f"[user:{update.effective_user.id}] /vk_auth")
    try:
        url = vk_get_auth_url()
        await update.message.reply_text(
            f"🔗 Авторизуй VK:\n\n"
            f"{url}\n\n"
            f"Нажми на ссылку → войди в VK → разреши доступ.\n"
            f"Токен сохранится автоматически — ничего копировать не нужно.",
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"vk_auth error: {e}", exc_info=True)
        await update.message.reply_text(f"Ошибка: {e}")


# ---------------------------------------------------------------------------
# Launch monitor — integration
# ---------------------------------------------------------------------------
LAUNCH_OWNER_FILE = Path(__file__).parent / "launch_data" / "owner_chat_id.txt"


def _launch_save_owner_chat(chat_id: int) -> None:
    LAUNCH_OWNER_FILE.parent.mkdir(exist_ok=True)
    LAUNCH_OWNER_FILE.write_text(str(chat_id), encoding="utf-8")


def _launch_load_owner_chat() -> int | None:
    try:
        return int(LAUNCH_OWNER_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _launch_build_digest_markup(items: list) -> InlineKeyboardMarkup:
    """One row per launch: approve + skip buttons.  Kept for back-compat but
    unused on the main flow now — we send one message per item instead."""
    rows = []
    for it in items:
        short_product = (it.product or it.company or it.creator)[:24]
        rows.append([
            InlineKeyboardButton(f"✅ {short_product}", callback_data=f"launch_approve:{it.id[:16]}"),
            InlineKeyboardButton("⏭", callback_data=f"launch_skip:{it.id[:16]}"),
        ])
    return InlineKeyboardMarkup(rows)


def _launch_build_single_item_markup(it) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ В работу", callback_data=f"launch_approve:{it.id[:16]}"),
        InlineKeyboardButton("⏭ Пропустить", callback_data=f"launch_skip:{it.id[:16]}"),
    ]])


def _launch_format_single_item(it, index: int, total: int) -> str:
    """Structured digest item (rewritten 6 May 2026 for readability).

    Format:
        🚀 *Product* — Company
        📅 Date · 🎯 score N · 🔥 N упоминаний

        <summary, до 1500 chars>

        👥 @creator1, @creator2 +N
        🔗 [Источник](url)
        ━━━━━━━━━━ N/Total ━━━━━━━━━━
    """
    title = it.product or it.company or "Без названия"
    company = it.company or ""
    creators = getattr(it, "creators", None) or [it.creator]
    mention_count = getattr(it, "mention_count", 1)
    published_at = getattr(it, "published_at", "") or ""

    # Header: 🚀 *Product* — Company  (skip company if empty or same as title)
    if company and company.lower() != title.lower():
        header = f"🚀 *{title}* — {company}"
    else:
        header = f"🚀 *{title}*"

    # Meta line: 📅 date · 🎯 score · 🔥 mentions
    meta_parts = []
    if published_at:
        # Convert ISO date to "6 мая" Russian short form
        try:
            from datetime import datetime as _dt
            dt = _dt.fromisoformat(published_at.replace("Z", "+00:00"))
            ru_months = ["", "янв", "фев", "мар", "апр", "мая", "июн",
                         "июл", "авг", "сен", "окт", "ноя", "дек"]
            meta_parts.append(f"📅 {dt.day} {ru_months[dt.month]}")
        except Exception:
            pass
    meta_parts.append(f"🎯 score {it.score}")
    if mention_count > 1:
        meta_parts.append(f"🔥 {mention_count} упоминаний")
    meta_line = " · ".join(meta_parts)

    # Summary: trim to 1500 chars (was 2500 — too long, hurt scannability)
    summary = (it.summary_ru or "").strip()
    if len(summary) > 1500:
        summary = summary[:1500].rsplit(" ", 1)[0] + "…"

    # Authors line: 👥 @c1, @c2 +N
    shown_authors = ", ".join(f"@{c}" for c in creators[:3])
    overflow = len(creators) - 3
    if overflow > 0:
        shown_authors += f" +{overflow}"

    # Footer separator
    footer = f"━━━━━━━━ {index}/{total} ━━━━━━━━"

    return (
        f"{header}\n"
        f"{meta_line}\n"
        f"\n"
        f"{summary}\n"
        f"\n"
        f"👥 {shown_authors}\n"
        f"🔗 [Источник]({it.url})\n"
        f"{footer}"
    )


async def _launch_send_digest_messages(bot, chat_id: int, items: list):
    """Send one message per launch item (each with its own approve/skip buttons).

    Splits around Telegram's 4096 char per-message limit.  Caller is
    responsible for sending any header/intro message first.
    """
    total = len(items)
    for i, it in enumerate(items, 1):
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=_launch_format_single_item(it, i, total),
                parse_mode="Markdown",
                reply_markup=_launch_build_single_item_markup(it),
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error(f"Launch digest item {i} send failed: {e}")
            # Fall back to plain text without markdown in case of parse errors.
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"{i}/{total}. {it.product or it.company} (score {it.score}) — {it.url}",
                    reply_markup=_launch_build_single_item_markup(it),
                    disable_web_page_preview=True,
                )
            except Exception:
                pass


async def _launch_find_item_by_short_id(short_id: str) -> dict | None:
    """Callback data is limited to 64 bytes so we store only first 16 chars
    of the sha1 id.  Resolve it back to the full row here."""
    import sqlite3
    with sqlite3.connect(launch_monitor.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM seen WHERE id LIKE ? LIMIT 1", (short_id + "%",)
        ).fetchone()
    return dict(row) if row else None


def _launch_generate_draft_script(item: dict, group_item=None, linked_content: dict | None = None) -> str:
    """Ask Claude to draft a 30-60 second Russian script for a launch.

    Returns the script text.  Called synchronously from the approve flow —
    wrap in asyncio.to_thread at the call site.  Never raises: on failure
    returns a stub so the Notion card still gets created with a placeholder.
    """
    product = item.get("product") or item.get("company") or "Новый AI-продукт"
    company = item.get("company") or "AI-студия"
    summary = item.get("summary_ru") or ""
    source_text = (item.get("text") or "")[:2000]
    group_creators = getattr(group_item, "creators", None) or [item.get("creator", "")]
    mention_note = (
        f"\nО запуске уже написали: {', '.join('@' + c for c in group_creators[:5])}."
        if len(group_creators) > 1 else ""
    )

    # If we followed the tweet's outbound link and got article text, feed it
    # to Claude — this is usually where the real product details live (tweets
    # are too short for a good script).
    extended_block = ""
    if linked_content and linked_content.get("article_text"):
        extended_block = (
            f"\n- Расширенный контекст (с блога/лендинга, куда ведёт ссылка "
            f"{linked_content.get('source_page') or ''}):\n"
            f"{linked_content['article_text'][:3500]}"
        )

    # Build the "idea" payload the same way we would if Artem pasted this
    # launch into chat manually. We reuse the main SCRIPT_PROMPT (the same
    # one /cards uses) so launch drafts have identical tone/quality to
    # user-created scripts — previously this function had its own weaker
    # prompt that bypassed all the style rules in script_prompt.txt.
    idea_parts = [
        f"Тема: свежий AI-запуск — {product} от {company}.",
    ]
    if summary:
        idea_parts.append(f"Суть (кратко на русском): {summary}")
    if source_text:
        idea_parts.append(f"Исходный текст автора (англ/рус, как есть):\n{source_text}")
    if mention_note:
        idea_parts.append(mention_note.strip())
    if extended_block:
        idea_parts.append(extended_block.strip())
    idea_text = "\n\n".join(idea_parts)

    try:
        resp = claude.messages.create(
            model="claude-opus-4-7",
            max_tokens=1024,
            system=SCRIPT_PROMPT,
            messages=[{"role": "user", "content": idea_text}],
        )
        result = resp.content[0].text.strip()
        # Trim the "СЦЕНАРИЙ:" prefix if the model added it, matching /cards flow.
        if result.upper().startswith("СЦЕНАРИЙ"):
            result = result.split("\n", 1)[-1].strip()
        return result
    except Exception as e:
        logger.error(f"Launch draft script generation failed: {e}", exc_info=True)
        return f"(черновик не сгенерирован — ошибка: {e}. Напиши сценарий с нуля по фактам выше.)"


def _launch_create_notion_draft(item: dict, group_item=None, linked_content: dict | None = None) -> tuple[str, str] | None:
    """Create a Notion card in the main DB marked as a launch breakdown.
    Returns (notion_url, notion_page_id) on success, None on failure.

    If ``group_item`` is provided (a DigestItem), its list of creators/urls
    is appended to the card body so every source is preserved.
    ``linked_content`` (from launch_monitor.fetch_linked_content) enriches
    the Claude script prompt with article text from the tweet's outbound link.
    Also generates a Claude draft script and embeds it in the card.
    """
    product = item.get("product") or item.get("company") or "Новый AI-запуск"
    title = f"🚀 Разбор: {product}"
    summary = item.get("summary_ru") or ""
    source_url = item.get("url") or ""
    creator = item.get("creator") or ""
    company = item.get("company") or ""
    group_creators = getattr(group_item, "creators", None) or [creator]
    group_urls = getattr(group_item, "urls", None) or [source_url]

    draft_script = _launch_generate_draft_script(item, group_item, linked_content=linked_content)

    children = [
        {
            "object": "block",
            "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content": f"Повод: запуск {product}. Компания: {company or '—'}. Упоминаний в ленте: {len(group_urls)}."}}],
                "icon": {"emoji": "🚀"},
                "color": "blue_background",
            },
        },
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"text": {"content": "Что вышло"}}]},
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"text": {"content": summary or "—"}}]},
        },
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"text": {"content": "Источники"}}]},
        },
    ]
    # One bullet per source URL (with creator handle for quick context).
    for cr, u in zip(group_creators, group_urls):
        children.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [
                    {"text": {"content": f"@{cr}: "}},
                    {"text": {"content": u, "link": {"url": u}}},
                ]
            },
        })
    # If the tweet pointed to a blog post / landing page, add it as its own
    # bullet — this is usually the richest source of details.
    if linked_content and linked_content.get("source_page"):
        src_page = linked_content["source_page"]
        children.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [
                    {"text": {"content": "🔗 полный пост: "}},
                    {"text": {"content": src_page, "link": {"url": src_page}}},
                ]
            },
        })
    children.extend([
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"text": {"content": "Сценарий разбора (черновик)"}}]},
        },
        {
            "object": "block",
            "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content": "Черновик написан Claude автоматически по фактам из источника. Отредактируй под свой голос перед озвучкой."}}],
                "icon": {"emoji": "✏️"},
                "color": "yellow_background",
            },
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"text": {"content": draft_script}}]},
        },
    ])

    # Launch Monitor draft path — uses active brand (no explicit brand override
    # available here). Pass active brand to schema helpers explicitly for
    # symmetry with create_notion_card and to make the data flow auditable.
    _launch_brand = _get_active_brand_name()
    try:
        page = notion.pages.create(
            parent={"database_id": NOTION_DB},
            properties={
                "Name": {"title": [{"text": {"content": title}}]},
                "Status": _status_property_value("Идеи | старт", _launch_brand),
                _rubric_property_name(_launch_brand): {"select": {"name": "Свободный формат"}},
                "Площадки": {
                    "multi_select": [{"name": p} for p in _default_platforms(_launch_brand)]
                },
                "Формат": {"multi_select": [{"name": "Short video"}]},
            },
            children=children,
        )
        return page["url"], page["id"]
    except Exception as e:
        logger.error(f"Launch monitor: failed to create Notion draft: {e}", exc_info=True)
        return None


def _launch_download_media_to_project(notion_page_id: str, title: str, source_url: str) -> tuple[Path, int] | None:
    """Build a project folder in the same layout as /cards uses and drop the
    source tweet/video there as source.<ext>.  Returns (path, size_bytes) or None.
    """
    safe_title = re.sub(r'[<>:"/\\|?*]', '', title)[:60].strip()
    folder_name = f"{notion_page_id.replace('-', '')[:8]}_{safe_title}"
    project_dir = PROJECTS_DIR / folder_name
    project_dir.mkdir(parents=True, exist_ok=True)

    dest = project_dir / "source"
    path = launch_monitor.download_source_media(source_url, dest)
    if not path:
        return None
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return path, size


def _launch_slice_demo_highlights(
    video_path: Path,
    max_clips: int = 7,
    clip_duration: float = 5.0,
) -> list[Path]:
    """Slice a demo video into highlight clips showing graphs/numbers/UI.

    Pipeline:
      1. ffmpeg scene detection → list of scene-change timestamps.
      2. Extract one JPEG frame per scene.
      3. Claude Vision picks frames that contain graphs, benchmarks, numbers,
         product UI, code — skipping faces, logos, intros, blank frames.
      4. ffmpeg cuts a 5-second clip centered on each kept timestamp.
    Returns paths of the produced highlight_NN.mp4 clips.
    """
    import base64

    parent = video_path.parent

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(video_path)],
        capture_output=True, text=True, timeout=30,
    )
    try:
        duration = float((probe.stdout or "").strip())
    except ValueError:
        return []
    if duration < clip_duration + 1:
        return []

    # Scene detection — threshold 0.25 catches most meaningful cuts in demo vids.
    scenes_run = subprocess.run(
        ["ffmpeg", "-i", str(video_path),
         "-vf", "select='gt(scene,0.25)',showinfo",
         "-vsync", "vfr", "-f", "null", "-"],
        capture_output=True, text=True, timeout=180,
    )
    timestamps: list[float] = []
    seen_ts: set[int] = set()
    for m in re.finditer(r"pts_time:([\d.]+)", scenes_run.stderr or ""):
        t = float(m.group(1))
        if t < 0.5 or t > duration - clip_duration / 2:
            continue
        bucket = int(t * 2)  # dedupe near-identical cuts (0.5s granularity)
        if bucket in seen_ts:
            continue
        seen_ts.add(bucket)
        timestamps.append(t)

    # If scene detection is too sparse (slow-paced demos), fill with even samples.
    if len(timestamps) < 6:
        n_fill = 10
        extra = [duration * (i + 1) / (n_fill + 1) for i in range(n_fill)]
        for t in extra:
            bucket = int(t * 2)
            if bucket not in seen_ts:
                seen_ts.add(bucket)
                timestamps.append(t)
        timestamps.sort()

    # Cap at 30 candidates to bound Vision cost (~$0.04 per video).
    if len(timestamps) > 30:
        stride = max(1, len(timestamps) // 30)
        timestamps = timestamps[::stride][:30]

    # Extract frames
    frames_dir = parent / "highlight_frames"
    frames_dir.mkdir(exist_ok=True)
    for f in frames_dir.glob("*.jpg"):
        try:
            f.unlink()
        except OSError:
            pass

    frame_paths: list[tuple[int, float, Path]] = []
    for i, t in enumerate(timestamps):
        out = frames_dir / f"frame_{i:02d}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(video_path),
             "-frames:v", "1", "-q:v", "4", "-vf", "scale=640:-1", str(out)],
            capture_output=True, timeout=30,
        )
        if out.exists() and out.stat().st_size > 1024:
            frame_paths.append((i, t, out))

    if not frame_paths:
        return []

    # Claude Vision: batch-score all frames in one call.
    content: list[dict] = [{
        "type": "text",
        "text": (
            "Перед тобой пронумерованные кадры из демо-видео запуска AI-продукта. "
            "Твоя задача — выбрать самые информативные кадры для B-roll.\n\n"
            "ВЫБИРАЙ кадры, где видно хотя бы одно из:\n"
            "— графики, диаграммы, гистограммы\n"
            "— таблицы с бенчмарками, числами, сравнениями моделей\n"
            "— интерфейс продукта (UI), скриншоты приложения\n"
            "— код, консоль, терминал\n"
            "— анимации визуализации данных\n"
            "— ЧЕЛОВЕК + рядом цифра/график/таблица/UI на экране или overlay "
            "(презентер со статистикой, ведущий с графиком за спиной — ЭТО ПОДХОДИТ)\n\n"
            "ПРОПУСКАЙ кадры, где:\n"
            "— просто говорящая голова БЕЗ цифр/графиков/UI в кадре\n"
            "— логотипы, заставки, титры, пустые фоны, вставки с брендом\n"
            "— общие планы офиса, рукопожатия, люди без контекста\n"
            "— чёрные, белые, размытые кадры\n\n"
            "Правило: если в кадре виден ЛЮБОЙ визуальный факт (число, UI, график) — "
            "это хороший кадр, даже если в нём есть человек.\n\n"
            f"Выбери до {max_clips} самых сильных кадров. "
            "Верни СТРОГО JSON массив их номеров, например [0, 3, 7, 12]. Без пояснений."
        ),
    }]
    for i, _, p in frame_paths:
        try:
            img_b64 = base64.b64encode(p.read_bytes()).decode()
        except OSError:
            continue
        content.append({"type": "text", "text": f"Кадр #{i}:"})
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64},
        })

    kept_indices: list[int] = []
    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=256,
            messages=[{"role": "user", "content": content}],
        )
        text = resp.content[0].text.strip()
        m = re.search(r"\[[\d,\s]*\]", text)
        if m:
            kept_indices = [int(x) for x in json.loads(m.group()) if isinstance(x, int)]
        logger.info(f"Launch highlights: Claude picked {len(kept_indices)}/{len(frame_paths)} frames: {kept_indices}")
    except Exception as e:
        logger.warning(f"Launch: vision scoring failed, keeping evenly-spaced frames: {e}")
        # Fallback: pick max_clips evenly from the candidates.
        step = max(1, len(frame_paths) // max_clips)
        kept_indices = [frame_paths[i][0] for i in range(0, len(frame_paths), step)][:max_clips]

    # Cut clips around kept timestamps
    index_to_ts = {i: t for i, t, _ in frame_paths}
    ordered = [i for i in kept_indices if i in index_to_ts][:max_clips]
    clip_paths: list[Path] = []
    for rank, i in enumerate(ordered):
        t = index_to_ts[i]
        start = max(0.0, t - clip_duration / 2)
        if start + clip_duration > duration:
            start = max(0.0, duration - clip_duration)
        # Name as `broll_launch_NN.mp4` so the card UI's `broll_*.mp4` glob
        # picks these up automatically — clips become part of the card's
        # B-roll library without any separate import step.
        out = parent / f"broll_launch_{rank:02d}.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{start:.2f}", "-i", str(video_path),
             "-t", f"{clip_duration:.2f}", "-c:v", "libx264", "-preset", "fast",
             "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac",
             "-movflags", "+faststart", str(out)],
            capture_output=True, timeout=90,
        )
        if out.exists() and out.stat().st_size > 10_000:
            clip_paths.append(out)

    return clip_paths


async def _launch_run_approval(bot, chat_id: int, user_id: int, item: dict, short_id: str, item_message):
    """Shared approval pipeline used by both ✅ and 🔁 Переделать buttons.

    Steps:
      1. Send progress message
      2. Follow outbound links from the tweet (up to 2 hops) for article text + demo video
      3. Claude drafts a 30-60 second Russian script using the enriched context
      4. Create a Notion card with sources + draft
      5. yt-dlp tries every candidate URL (tweet → group urls → YouTube from article → deep pages)
      6. Update the progress message with the result
      7. Edit the original item card to show "в работе" + retry button
    """
    product_name = item.get("product") or item.get("company") or "без имени"
    progress_msg = await bot.send_message(
        chat_id=chat_id,
        text=f"⏳ Обрабатываю запуск «{product_name}»\n"
             f"• Хожу по ссылкам из твита (до 2 уровней) за полным текстом + демо-видео…\n"
             f"• Claude пишет черновик сценария…\n"
             f"• Создаю карточку в Notion…\n"
             f"• Качаю видео через yt-dlp…",
    )

    digest_items = launch_monitor.get_pending_digest(limit=100)
    group_item = next(
        (d for d in digest_items if d.id == item["id"] or item["id"] in d.urls),
        None,
    )

    # Hop out to linked blog/landing pages for rich context + embedded videos.
    linked_content = await asyncio.to_thread(
        launch_monitor.fetch_linked_content, item.get("text") or ""
    )

    result = await asyncio.to_thread(
        _launch_create_notion_draft, item, group_item, linked_content
    )
    if not result:
        await progress_msg.edit_text("❌ Не удалось создать карточку в Notion — смотри логи.")
        return None

    notion_url, notion_id = result
    launch_monitor.mark_group_status(item["id"], "drafted")

    # Build yt-dlp candidate list in priority order: clicked tweet → other
    # tweets in the group → YouTube/Vimeo extracted from blog → deep-hop pages.
    title = f"Разбор {product_name}"
    urls_to_try: list[str] = []
    if item.get("url"):
        urls_to_try.append(item["url"])
    if group_item:
        for u in group_item.urls:
            if u and u not in urls_to_try:
                urls_to_try.append(u)
    for u in linked_content.get("media_urls", []):
        if u and u not in urls_to_try:
            urls_to_try.append(u)
    for u in linked_content.get("page_urls", []):
        if u and u not in urls_to_try:
            urls_to_try.append(u)

    media_info = None
    media_source_url = None
    # Pre-declared so the continuation menu at the end of the function can
    # safely reference it even when media is missing or the slicer crashes.
    clips: list[Path] = []
    for u in urls_to_try:
        media_info = await asyncio.to_thread(
            _launch_download_media_to_project, notion_id, title, u
        )
        if media_info:
            media_source_url = u
            break

    media_public_url: str | None = None
    if media_info:
        path, size = media_info
        size_mb = size / (1024 * 1024)
        if media_source_url and media_source_url in linked_content.get("media_urls", []):
            src_note = " (из статьи по ссылке)"
        elif media_source_url and media_source_url in linked_content.get("page_urls", []):
            src_note = " (deep link)"
        elif media_source_url and "youtube" in (media_source_url or "").lower():
            src_note = " (YouTube)"
        else:
            src_note = ""
        media_line = f"📎 Демо-видео ({size_mb:.1f} MB){src_note} — отправляю отдельным сообщением"

        # Publish the file via nginx /media/ so Notion can embed it, and so we
        # have a stable public link even if the project folder moves later.
        try:
            media_public_url = await asyncio.to_thread(
                save_media_permanent, str(path), "launch_demo"
            )
        except Exception as e:
            logger.warning(f"Launch: failed to publish demo video: {e}")

        # Embed the video in the Notion card — a real player block, not just text.
        if media_public_url:
            try:
                await asyncio.to_thread(
                    notion.blocks.children.append,
                    block_id=notion_id,
                    children=[
                        {
                            "object": "block",
                            "type": "heading_2",
                            "heading_2": {"rich_text": [{"text": {"content": "Демо-видео запуска"}}]},
                        },
                        {
                            "object": "block",
                            "type": "video",
                            "video": {"type": "external", "external": {"url": media_public_url}},
                        },
                    ],
                )
            except Exception as e:
                logger.warning(f"Launch: failed to append video block to Notion: {e}")
    else:
        media_line = "📎 Медиа не нашлось ни в твите, ни в статье, ни в deep link"
    context_line = ""
    if linked_content.get("article_text"):
        context_line = f"\n📰 Claude прочитал статью: {linked_content.get('source_page') or ''}"

    await progress_msg.edit_text(
        f"✅ Карточка: {notion_url}\n"
        f"✏️ Черновик сценария уже внутри — открой через /cards и отредактируй\n"
        f"{media_line}"
        f"{context_line}",
        disable_web_page_preview=True,
    )

    # Deliver the actual video as a Telegram attachment so Artem can watch it
    # without digging into the project folder.
    if media_info:
        path, size = media_info
        try:
            if size <= 50 * 1024 * 1024:  # Telegram bot API limit for send_video
                with open(path, "rb") as f:
                    await bot.send_video(
                        chat_id=chat_id,
                        video=f,
                        caption=f"🎬 Демо-видео — {product_name}",
                        supports_streaming=True,
                    )
            else:
                with open(path, "rb") as f:
                    await bot.send_document(
                        chat_id=chat_id,
                        document=f,
                        caption=f"🎬 Демо-видео — {product_name} ({size/(1024*1024):.1f} MB)",
                    )
        except Exception as e:
            logger.warning(f"Launch: failed to send demo video to Telegram: {e}")
            if media_public_url:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"🎬 Демо-видео: {media_public_url}",
                    disable_web_page_preview=False,
                )

        # Smart highlight extraction: Claude Vision picks frames with
        # graphs/numbers/UI, ffmpeg cuts 5-second clips around them.
        try:
            slicing_msg = await bot.send_message(
                chat_id=chat_id,
                text="✂️ Нарезаю хайлайты из демо-видео (графики, цифры, интерфейс)…",
            )
            clips = await asyncio.to_thread(_launch_slice_demo_highlights, path)
            if clips:
                await slicing_msg.edit_text(
                    f"✂️ Нарезал {len(clips)} хайлайтов — отправляю и добавляю в Notion"
                )
                # Send each clip as a Telegram video
                for idx, clip in enumerate(clips, 1):
                    try:
                        with open(clip, "rb") as cf:
                            await bot.send_video(
                                chat_id=chat_id,
                                video=cf,
                                caption=f"✂️ Хайлайт {idx}/{len(clips)}",
                                supports_streaming=True,
                            )
                    except Exception as e:
                        logger.warning(f"Launch: failed to send highlight {idx}: {e}")

                # Publish clips via nginx and embed them in Notion.
                highlight_blocks: list[dict] = [{
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {"rich_text": [{"text": {"content": "Хайлайты демо (B-roll)"}}]},
                }]
                for idx, clip in enumerate(clips, 1):
                    try:
                        url = await asyncio.to_thread(
                            save_media_permanent, str(clip), f"launch_hl_{idx}"
                        )
                        highlight_blocks.append({
                            "object": "block",
                            "type": "video",
                            "video": {"type": "external", "external": {"url": url}},
                        })
                    except Exception as e:
                        logger.warning(f"Launch: failed to publish highlight {idx}: {e}")

                if len(highlight_blocks) > 1:
                    try:
                        await asyncio.to_thread(
                            notion.blocks.children.append,
                            block_id=notion_id,
                            children=highlight_blocks,
                        )
                    except Exception as e:
                        logger.warning(f"Launch: failed to append highlight blocks to Notion: {e}")
            else:
                await slicing_msg.edit_text("✂️ Хайлайтов не нашлось — Claude не увидел ни графиков, ни интерфейса в кадрах")
        except Exception as e:
            logger.warning(f"Launch: highlight pipeline failed: {e}", exc_info=True)

    # Seed pending[user_id] so every downstream handler (card_script,
    # card_voice, card_broll, card_assemble, crosspost…) knows which card
    # the user is currently working on. Without this the flow falls off a
    # cliff — user has to re-enter via /cards manually.
    pending.setdefault(user_id, {})
    pending[user_id]["notion_edit_card"] = notion_id
    pending[user_id]["notion_edit_title"] = title
    if clips:
        existing = pending[user_id].get("broll_clips", [])
        start_idx = len(existing)
        for i, clip_path in enumerate(clips):
            existing.append({
                "id": f"launch_hl_{start_idx + i}",
                "source": "launch_highlight",
                "path": str(clip_path),
                "filename": Path(clip_path).name,
                "tags": f"demo highlight {i}",
            })
        pending[user_id]["broll_clips"] = existing
    try:
        _save_pending(pending)
    except Exception as e:
        logger.warning(f"Launch: failed to persist pending after approval: {e}")

    # Continuation menu — Artem explicitly asked for this: after the card is
    # built he wants to tap «Сценарий / Озвучить / B-roll» right here,
    # not dig into /cards and re-pick the card. Kept minimal (3-4 keys) so
    # it reads well on mobile. Full action list stays behind «Вся карточка».
    short_nid = notion_id.replace("-", "")[:20]
    cont_buttons = [[InlineKeyboardButton("📜 Сценарий", callback_data=f"card_script:{short_nid}")]]
    voice_available = bool(elevenlabs_client or (FISH_API_KEY and FISH_VOICE_ID))
    if voice_available:
        cont_buttons.append([InlineKeyboardButton("🎙 Озвучить", callback_data=f"card_voice:{short_nid}")])
    if clips:
        cont_buttons.append([InlineKeyboardButton(
            f"📋 Управление B-roll ({len(clips)})",
            callback_data=f"broll_manage:{short_nid}",
        )])
    cont_buttons.append([InlineKeyboardButton("📇 Вся карточка", callback_data=f"notion_card:{short_nid}")])

    broll_line = f"🎬 {len(clips)} хайлайта уже в B-roll карточки\n" if clips else ""
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"✅ Карточка «{product_name}» готова\n"
                f"{broll_line}"
                "👇 Продолжаем конвейер:"
            ),
            reply_markup=InlineKeyboardMarkup(cont_buttons),
        )
    except Exception as e:
        logger.warning(f"Launch: failed to send continuation menu: {e}")

    # Edit the original item card: show "в работе" banner + retry button.
    try:
        original = item_message.text_markdown or item_message.text or ""
        # Strip any previous banner so retries don't compound the prefix.
        if original.startswith("✅ ") and "\n\n" in original:
            original = original.split("\n\n", 1)[1]
        await item_message.edit_text(
            f"✅ _в работе_ — [карточка]({notion_url})\n\n{original}",
            parse_mode="Markdown",
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔁 Переделать", callback_data=f"launch_retry:{short_id}"),
            ]]),
        )
    except Exception as e:
        logger.debug(f"Launch: failed to edit item card after approval: {e}")

    return notion_url, notion_id


async def brand_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set GLOBAL fallback brand profile (default / shoes / ...).

    Per-card brand (Notion property «Бренд») has higher priority — it is
    auto-applied when you open a card. This command only matters for flows
    WITHOUT a Notion card: /voice, /selfie without a linked card, or early
    testing before the card is created.

    Usage:
      /brand            → show current + available brands
      /brand shoes      → set global fallback to shoes
      /brand default    → back to Artem's defaults
    """
    global _active_brand
    user_id = update.effective_user.id
    args = (update.message.text or "").split()

    if len(args) < 2:
        current = BRANDS.get(_active_brand, BRANDS["default"])
        text = (
            f"🏷 Глобальный бренд: *{_active_brand}*\n"
            f"_{current.get('description', '')}_\n\n"
            "ℹ️ Карточный бренд (свойство «Бренд» в Notion) имеет приоритет — "
            "он применяется автоматически при работе с карточкой.\n\n"
            "Нажми на бренд ниже чтобы переключить глобальный fallback:"
        )
        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=_brand_picker_kb(_active_brand),
        )
        return

    new_brand = args[1].strip().lower()
    if new_brand not in BRANDS:
        available = ", ".join(f"`{n}`" for n in BRANDS.keys())
        await update.message.reply_text(
            f"❌ Нет такого бренда: `{new_brand}`\n\n"
            f"Доступные: {available}",
            parse_mode="Markdown",
        )
        return

    prev = _active_brand
    _active_brand = new_brand
    cfg = BRANDS[new_brand]
    logger.info(f"[user:{user_id}] /brand: {prev} → {new_brand}")

    heygen_avatar = cfg.get("heygen_avatar_id") or "(default из HEYGEN_LOOKS)"
    eleven_voice = cfg.get("eleven_voice_id") or f"(default ENV: {ELEVENLABS_VOICE_ID[:8] if ELEVENLABS_VOICE_ID else 'none'}...)"
    eleven_model = cfg.get("eleven_model_id") or "eleven_multilingual_v2"

    await update.message.reply_text(
        f"✅ Бренд переключён: `{prev}` → *{new_brand}*\n\n"
        f"_{cfg.get('description', '')}_\n\n"
        f"🤖 HeyGen avatar: `{heygen_avatar if len(heygen_avatar) < 40 else heygen_avatar[:32] + '...'}`\n"
        f"🎙 ElevenLabs voice: `{eleven_voice if len(eleven_voice) < 40 else eleven_voice[:32] + '...'}`\n"
        f"🧠 Model: `{eleven_model}`\n\n"
        f"⚠️ Сбрасывается на `default` при рестарте бота.",
        parse_mode="Markdown",
    )


async def launches_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual trigger: show the current launch queue right now."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    logger.info(f"[user:{user_id}] /launches")

    _launch_save_owner_chat(chat_id)

    msg = await update.message.reply_text("🔄 Опрашиваю AI-экспертов…")
    try:
        stats = await asyncio.to_thread(launch_monitor.poll_once, claude)
    except Exception as e:
        logger.error(f"Launch poll failed: {e}", exc_info=True)
        await msg.edit_text(f"❌ Ошибка опроса: {e}")
        return

    items = launch_monitor.get_pending_digest()
    header = (
        f"📊 Опрос: получено {stats['fetched']}, новых {stats['new']}, "
        f"в очереди {stats['queued']}\n\n"
        f"🚀 *Разборы запусков* — {len(items)} шт."
        if items else
        f"📊 Опрос: получено {stats['fetched']}, новых {stats['new']}, "
        f"в очереди {stats['queued']}\n\n"
        f"🚀 Разборы запусков\n\nНовых запусков от отслеживаемых авторов пока нет."
    )
    await msg.edit_text(header, parse_mode="Markdown", disable_web_page_preview=True)
    if items:
        await _launch_send_digest_messages(context.bot, chat_id, items)


async def launch_poll_cron(context):
    """Hourly: poll all creators and score new posts silently."""
    logger.info("Launch monitor poll cron triggered")
    try:
        stats = await asyncio.to_thread(launch_monitor.poll_once, claude)
        logger.info(f"Launch poll cron done: {stats}")
    except Exception as e:
        logger.error(f"Launch poll cron failed: {e}", exc_info=True)


async def launch_digest_cron(context):
    """Daily at 10:00 MSK: send the pending digest to Artem for review."""
    logger.info("Launch digest cron triggered")
    chat_id = _launch_load_owner_chat()
    if not chat_id:
        logger.warning("Launch digest: owner chat_id not set yet — send /launches once first")
        return
    items = launch_monitor.get_pending_digest()
    if not items:
        await context.bot.send_message(
            chat_id=chat_id,
            text="🚀 Разборы запусков\n\nЗа сутки ничего интересного — очередь пустая.",
        )
        return
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🚀 *Разборы запусков* — {len(items)} шт.",
        parse_mode="Markdown",
    )
    await _launch_send_digest_messages(context.bot, chat_id, items)


async def weekly_stats_to_notion(context):
    """Cron job: save latest stats snapshot to Notion every Sunday at 21:00 MSK."""
    logger.info("Weekly stats cron triggered")
    latest = _get_latest_stats()
    if not latest:
        logger.info("No stats data to save to Notion")
        return

    # Check if already saved this week (avoid duplicates)
    date_str = latest.get("date", "")
    try:
        snapshot_date = datetime.strptime(date_str, "%Y-%m-%d")
        now = datetime.now()
        # Only save if snapshot is from the last 7 days
        if (now - snapshot_date).days > 7:
            logger.info(f"Latest snapshot is from {date_str}, too old — skipping")
            return
    except Exception:
        pass

    url = await asyncio.to_thread(_save_stats_to_notion, latest)
    if url:
        logger.info(f"Weekly stats saved to Notion: {url}")
    else:
        logger.warning("Failed to save weekly stats to Notion")


def main():
    # read/connect/pool timeouts подняты с дефолтных ~5с: скачивание видео
    # (download_to_drive) на 13-50 МБ не успевало → telegram.error.TimedOut
    # «Ошибка обработки видео: Timed out» (Артём 8 июня). Влияет на ВСЕ
    # API-вызовы и загрузки (polling использует отдельный request).
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .read_timeout(60)
        .connect_timeout(30)
        .write_timeout(60)
        .pool_timeout(60)
        .build()
    )

    # ── Billing gate (MUST be first) ──────────────────────────────────────
    # TypeHandler in group=-1 runs before every other handler. If the user
    # isn't allowed through (unregistered + BILLING_ENABLED=1), the chain
    # is halted via ApplicationHandlerStop and no command/message/callback
    # handler below ever sees the update.
    app.add_handler(
        TypeHandler(Update, _billing_gate_middleware),
        group=-1,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("notion", notion_quick_command))
    # /voice removed 2026-04-21 — voice generation now only happens inside
    # the pipeline (on-demand via card menu). Standalone /voice isn't used.
    app.add_handler(CommandHandler("script", script_ready_command))
    app.add_handler(CommandHandler("cards", cards_command))
    app.add_handler(CommandHandler("ideas", ideas_command))
    app.add_handler(CommandHandler("cards_all", cards_all_command))
    app.add_handler(CommandHandler("stats", stats_command))
    # /update и /report скрыты для Maksim — замеры подписчиков по личному
    # TG-каналу пока не используем. Handler-функции сохранены, при нужде —
    # раскомментировать. (20 мая 2026)
    # app.add_handler(CommandHandler("update", update_command))
    # app.add_handler(CommandHandler("report", report_command))
    app.add_handler(CommandHandler("calendar", calendar_command))
    # /pub removed 2026-04-21 — unused. Publication flags are set via the
    # card's «Опубликовано на» field in Notion directly, or via the crosspost
    # auto-tracking after successful platform posts.
    app.add_handler(CommandHandler("ig_auth", ig_auth_command))
    app.add_handler(CommandHandler("ig_code", ig_code_command))
    app.add_handler(CommandHandler("yt_auth", yt_auth_command))
    app.add_handler(CommandHandler("vk_auth", vk_auth_command))
    # /launches — дайджест AI-запусков. Инфра остаётся (cron Launch Monitor,
    # источники), но команда скрыта: для Maksim будет переподключена под его
    # ниши (трендомер) как вторая кнопка после Банка идей. (20 мая 2026)
    # app.add_handler(CommandHandler("launches", launches_command))
    app.add_handler(CommandHandler("selfie", selfie_command))
    # /brand — переключение брендов. У Maksim один бренд (DEFAULT_BRAND=maksim).
    # При возврате multi-brand UI — раскомментировать. (20 мая 2026)
    # app.add_handler(CommandHandler("brand", brand_command))
    # /image and /video are added by register_fal_handlers(app, ...) below

    # TG-post generator (/tgpost + callback pattern ^tgpost:)
    # Регистрируем ДО общего CallbackQueryHandler(handle_callback) ниже —
    # паттерн должен матчиться первым.
    register_tgpost(
        app,
        pending_dict=pending,
        save_pending_fn=_save_pending,
        claude_client=claude,
        notion_client=notion,
        notion_db_id=NOTION_DB,
        channel_id=TELEGRAM_CHANNEL_ID,
        save_text_fn=_save_text_to_project,
        # 13 May 2026 — brand-aware tgpost flow. tg_post_handlers рендерит
        # разные кнопки post-type для maksim и default. Без get_brand_fn
        # флоу работает в default-режиме (Артёмов), что fail'ило сценарий
        # 02 в test_runner на @yumsunov_content_bot.
        get_brand_fn=_get_active_brand_name,
    )

    # Selfie pipeline v2 (ported from content-bot-2 on 8 June 2026).
    # State machine: waiting_video → text_review → text_editing → music_picking
    # → cover_picking → cover_uploading → waiting_title → _selfie_finalize.
    # Callbacks selfie_text:* / selfie_music:* / selfie_cover:* routed in
    # handle_callback below. Video/text/photo state guards in process_idea
    # and process_photo.
    selfie_handlers.init(
        pending=pending,
        save_pending=_save_pending,
        assets_dir=ASSETS_DIR,
        logger=logger,
        selfie_finalize=_selfie_finalize,
        # Replace the module's built-in trivial title-picker with our richer
        # Claude-Opus 5-hook picker. Existing selfie_hook_pick:N callbacks in
        # handle_callback take it from there.
        title_picker=_maksim_selfie_title_picker,
        cover_text_step=_maksim_selfie_cover_text_step,
    )

    # fal.ai on-demand generators — /image (Nano Banana Pro) and /video
    # (Kling 3.0 Pro). Registered BEFORE general CallbackQueryHandler so
    # the fal:dur:* pattern resolves first.
    register_fal_handlers(app, pending, _save_pending)

    # HeyGen Image-to-Video test command — /heygen_test (one-off photo+audio
    # → animated mp4). И фото и аудио хостятся через /media/ (nginx alias
    # на /srv/bot-media-maksim/ — см. MEDIA_DIR/MEDIA_BASE_URL выше).
    #
    # Note: для /heygen_test намеренно используем save_media_permanent
    # (а не save_cover_permanent) даже для фото-входа — потому что:
    #   1) save_cover_permanent ресайзит до 800px ширины, что ломает
    #      качество для Avatar IV который ожидает 1080p input
    #   2) save_media_permanent копирует as-is через shutil.copy2 → исходное
    #      разрешение фото сохраняется
    # /covers/ на nginx работает с 4 мая 2026 (см. reference_nginx_content_bot_locations.md),
    # но для HeyGen-input всё равно нужен оригинал, не сжатый.
    register_heygen_test_handlers(
        app,
        pending,
        _save_pending,
        save_media_permanent,  # save_media_fn (для аудио)
        save_media_permanent,  # save_image_fn (для фото — тоже через /media/)
        HEYGEN_API_KEY,
    )

    # Billing — create DB tables if missing + register handlers for
    # /billing, /admin and their callback patterns (c:*, a:*).
    # Also registered BEFORE the general CallbackQueryHandler(handle_callback).
    try:
        billing_api.init()
        billing_handlers.register(app)
        logger.info(
            f"Billing module: active={BILLING_ENABLED}, "
            f"admins={len(_billing_is_admin.__globals__.get('ADMIN_TELEGRAM_IDS', []))} configured, "
            f"support={_BILLING_SUPPORT}"
        )
    except Exception as e:
        logger.error(f"Billing module failed to register: {e}", exc_info=True)

    # ── Hidden pipeline-spine track (experimental, parallel to live flow) ──
    # New state-machine pipeline (content_pipeline): commands /spine,
    # /spine_resume + sp: callbacks. Deliberately NOT in the menu/help — it's an
    # experimental parallel track, not a user feature yet. Registered BEFORE the
    # general CallbackQueryHandler below so its sp:-callbacks aren't swallowed.
    # Fully isolated: any failure here must never break the live bot.
    try:
        from pathlib import Path as _Path
        from bot_pipeline_adapter import register_pipeline_spine
        register_pipeline_spine(
            app,
            claude_client=claude,
            script_system_fn=lambda: _brand_script_prompt(SCRIPT_PROMPT),
            cover_system_fn=lambda: _brand_cover_prompt(COVER_TEXT_PROMPT),
            db_path=str(_Path(__file__).parent / "pipeline.db"),
            cover_model=COVER_MODEL,
            # 1c: real HeyGen provider hooks (own-voice → avatar). The poller
            # delivers the finished render. Real renders cost money — only fired
            # when the user explicitly confirms the cost-gate.
            heygen_upload_fn=heygen_upload_audio_asset,
            heygen_generate_fn=heygen_generate_video,
            heygen_status_fn=heygen_check_status,
        )
    except Exception as _spine_e:
        logger.error(f"[spine] registration failed (live bot unaffected): {_spine_e}",
                     exc_info=True)

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_idea))
    # filters.Document.Category("video/") ловит ВСЕ video-mime типы (mp4,
    # quicktime/mov, x-matroska/mkv, webm, avi и т.д.) — иначе Telegram Web
    # часто шлёт .MOV как document с mime "video/quicktime" и фильтр на
    # MimeType("video/mp4") его не пропускает → бот молчит на /selfie + видео.
    # (Артём, 9 июня 2026, в продакшене на IMG_1566.MOV)
    # + по РАСШИРЕНИЮ: Telegram Web шлёт .MOV/.mp4 как документ с ненадёжным
    # mime (не video/*) → Category("video/") промахивается, бот молчит на видео
    # после /selfie (Артём, 8 июня, IMG_1566.MOV из Telegram Web).
    _video_doc_ext = (
        filters.Document.FileExtension("mov")
        | filters.Document.FileExtension("mp4")
        | filters.Document.FileExtension("m4v")
        | filters.Document.FileExtension("mkv")
        | filters.Document.FileExtension("webm")
        | filters.Document.FileExtension("avi")
        | filters.Document.FileExtension("mpeg")
        | filters.Document.FileExtension("mpg")
    )
    app.add_handler(MessageHandler(
        filters.VIDEO | filters.Document.VIDEO
        | filters.Document.Category("video/") | _video_doc_ext,
        process_idea,
    ))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, process_voice))
    # Photo handler — only active inside «📥 Готовые материалы» flow (state
    # broll_ready_material). Outside that state photos are ignored silently.
    app.add_handler(MessageHandler(filters.PHOTO, process_photo))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_error_handler(error_handler)

    # Weekly stats cron — every Sunday at 21:00 Moscow time (UTC+3 → 18:00 UTC)
    from datetime import time as dt_time
    app.job_queue.run_daily(
        weekly_stats_to_notion,
        time=dt_time(hour=18, minute=0, second=0),  # 18:00 UTC = 21:00 MSK
        days=(6,),  # Sunday = 6
        name="weekly_stats_notion",
    )
    logger.info("Weekly stats cron scheduled: Sunday 21:00 MSK")

    # Launch monitor: poll every hour, send digest daily at 10:00 MSK (07:00 UTC)
    app.job_queue.run_repeating(
        launch_poll_cron,
        interval=timedelta(hours=1),
        first=timedelta(minutes=2),
        name="launch_monitor_poll",
    )
    app.job_queue.run_daily(
        launch_digest_cron,
        time=dt_time(hour=7, minute=0, second=0),  # 07:00 UTC = 10:00 MSK
        name="launch_monitor_digest",
    )
    logger.info("Launch monitor cron scheduled: hourly poll + daily digest at 10:00 MSK")

    logger.info("Content bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
