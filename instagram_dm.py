"""
instagram_dm.py — Instagram Comment-to-DM automation via Graph API.

Replaces ManyChat: when someone comments a keyword on a post,
automatically sends a private reply (DM) with the guide link.

Architecture:
- Webhook server (aiohttp) runs inside the bot process
- Keywords stored in dm_keywords.json: {media_id: {keyword, reply_text, guide_url}}
- Comment webhook → keyword match → private reply via Graph API
"""

import os
import json
import hmac
import random
import hashlib
import logging
from pathlib import Path
from datetime import datetime

from aiohttp import web
from dotenv import load_dotenv
import requests

load_dotenv(override=True)

logger = logging.getLogger("content_bot.instagram_dm")

# ──────────────────────────────────────────────
#  Config
# ──────────────────────────────────────────────
META_APP_SECRET = os.getenv("META_APP_SECRET", "")
INSTAGRAM_APP_SECRET = os.getenv("INSTAGRAM_APP_SECRET", "")
INSTAGRAM_WEBHOOK_VERIFY_TOKEN = os.getenv("INSTAGRAM_WEBHOOK_VERIFY_TOKEN", "")
INSTAGRAM_DM_PORT = int(os.getenv("INSTAGRAM_DM_PORT", "8443"))

KEYWORDS_FILE = Path(__file__).parent / "dm_keywords.json"
DM_LOG_FILE = Path(__file__).parent / "dm_log.json"

# Token file (shared with crosspost.py)
INSTAGRAM_TOKEN_FILE = Path(__file__).parent / "instagram_token.json"


# ──────────────────────────────────────────────
#  Keywords storage
# ──────────────────────────────────────────────

def _load_keywords() -> dict:
    """Load keyword mappings: {media_id: {keyword, reply_text, guide_url, created_at}}"""
    if KEYWORDS_FILE.exists():
        try:
            return json.loads(KEYWORDS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_keywords(data: dict):
    KEYWORDS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_keyword_for_post(media_id: str, keyword: str, reply_text: str, guide_url: str = ""):
    """
    Register a keyword trigger for an Instagram post.
    Called automatically after cross-posting a Reel.
    """
    keywords = _load_keywords()
    keywords[media_id] = {
        "keyword": keyword.lower().strip(),
        "reply_text": reply_text,
        "guide_url": guide_url,
        "created_at": datetime.now().isoformat(),
    }
    _save_keywords(keywords)
    logger.info(f"Keyword '{keyword}' registered for media {media_id}")


def get_all_keywords() -> dict:
    """Get all registered keywords."""
    return _load_keywords()


# ──────────────────────────────────────────────
#  Instagram API helpers
# ──────────────────────────────────────────────

def _get_instagram_token() -> tuple[str, str] | None:
    """Get (access_token, ig_user_id) from token file."""
    if not INSTAGRAM_TOKEN_FILE.exists():
        return None
    try:
        data = json.loads(INSTAGRAM_TOKEN_FILE.read_text(encoding="utf-8"))
        token = data.get("page_access_token") or data.get("access_token")
        ig_id = data.get("ig_user_id")
        if token and ig_id:
            return token, ig_id
    except Exception:
        pass
    return None


def _get_page_id() -> str | None:
    """Get Facebook Page ID saved alongside the Instagram token.

    Messaging / private replies require graph.facebook.com/{page_id}/...
    when we're authenticated via Facebook Login (not Instagram Login).
    """
    if not INSTAGRAM_TOKEN_FILE.exists():
        return None
    try:
        data = json.loads(INSTAGRAM_TOKEN_FILE.read_text(encoding="utf-8"))
        return data.get("page_id")
    except Exception:
        return None


# Pool of public comment-reply templates. Rotated randomly (never the
# same one twice in a row) so IG's anti-spam doesn't flag repetitive
# copy-pasted replies. Keep them short, warm, without the keyword
# itself — we don't want to re-trigger our own webhook on our reply.
COMMENT_REPLY_TEMPLATES = [
    "Спасибо за интерес! Всё нужное уже ждёт тебя в личных сообщениях 💌",
    "Привет! Отправил ссылку в директ — загляни, там всё подробно ✨",
    "Благодарю, что написал! Материал уже у тебя в личке, забирай 🚀",
    "Спасибо! Самое полезное — в твоих личных сообщениях. Приятного изучения",
    "Привет! Проверь директ: там и ссылка, и пара слов от меня 💫",
    "Рад, что заинтересовало! Заглядывай в личные — там всё собрано 🙌",
    "Спасибо за комментарий! Ссылка уже в директе, там же можно задать вопросы 📬",
    "Привет! Всё уже в твоих личных сообщениях, забирай и пользуйся ✌️",
    "Благодарю! Посмотри в директе — там ссылка и немного контекста ✨",
    "Спасибо! Ответил в личные сообщения, там всё по делу 💬",
    "Привет! Загляни в директ — материал уже ждёт тебя 📨",
    "Спасибо, что откликнулся! Отправил всё нужное в личку, приятного чтения",
    "Отлично! Проверь личные сообщения: там ссылка и мини-пояснение 💫",
    "Спасибо за интерес! В директе уже всё готово для тебя 🎁",
    "Привет! Отправил ссылку в директ, надеюсь пригодится 🙌",
    "Спасибо за комментарий! Ответил в личные — там всё что нужно ✨",
    "Готово! Заглядывай в директ — там ссылка и короткий комментарий от меня 💌",
    "Спасибо! Всё подробно в твоих личных сообщениях, пользуйся на здоровье",
    "Привет! Материал уже в директе, забирай и применяй 🚀",
    "Благодарю за комментарий! В личке ждёт ссылка — проверь когда будет удобно",
]

_REPLY_STATE_FILE = Path(__file__).parent / ".dm_reply_state.json"


def _pick_comment_reply() -> str:
    """Random template, never repeating the previous one."""
    try:
        last_idx = -1
        if _REPLY_STATE_FILE.exists():
            last_idx = json.loads(_REPLY_STATE_FILE.read_text()).get("last_idx", -1)
        choices = [i for i in range(len(COMMENT_REPLY_TEMPLATES)) if i != last_idx]
        idx = random.choice(choices)
        _REPLY_STATE_FILE.write_text(json.dumps({"last_idx": idx}))
        return COMMENT_REPLY_TEMPLATES[idx]
    except Exception:
        return random.choice(COMMENT_REPLY_TEMPLATES)


def _send_comment_reply(comment_id: str, message_text: str) -> bool:
    """Publicly reply to a comment via Facebook Graph API.

    Uses graph.facebook.com (not graph.instagram.com) because we're
    authenticated via Facebook Login with a Page access token.
    """
    creds = _get_instagram_token()
    if not creds:
        logger.error("Instagram not authorized — cannot reply to comment")
        return False
    access_token, _ = creds
    try:
        resp = requests.post(
            f"https://graph.facebook.com/v21.0/{comment_id}/replies",
            params={"access_token": access_token},
            data={"message": message_text},
            timeout=15,
        )
    except Exception as e:
        logger.error(f"Comment reply request failed: {e}")
        return False

    if resp.status_code == 200:
        logger.info(f"Comment reply sent for {comment_id}: '{message_text}'")
        return True
    logger.error(f"Comment reply failed: {resp.status_code} {resp.text[:300]}")
    return False


def _send_private_reply(comment_id: str, message_text: str) -> bool:
    """Send a private reply (DM) to a commenter via Facebook Graph API.

    Uses graph.facebook.com/{page_id}/messages because we're authenticated
    via Facebook Login (not Instagram Login). The Page access token is
    a Facebook token and cannot be parsed by graph.instagram.com.
    """
    creds = _get_instagram_token()
    if not creds:
        logger.error("Instagram not authorized — cannot send DM")
        return False

    access_token, _ = creds
    page_id = _get_page_id()
    if not page_id:
        logger.error("No page_id saved — re-authorize Instagram")
        return False

    resp = requests.post(
        f"https://graph.facebook.com/v21.0/{page_id}/messages",
        params={"access_token": access_token},
        json={
            "recipient": {"comment_id": comment_id},
            "message": {"text": message_text},
            "messaging_type": "RESPONSE",
        },
        headers={"Content-Type": "application/json"},
        timeout=15,
    )

    if resp.status_code == 200:
        logger.info(f"Private reply sent for comment {comment_id}")
        return True
    else:
        logger.error(f"Private reply failed: {resp.status_code} {resp.text[:300]}")
        return False


def _send_dm(recipient_id: str, message_text: str) -> bool:
    """Send a DM to a user by their Instagram-scoped ID (IGSID)."""
    creds = _get_instagram_token()
    if not creds:
        logger.error("Instagram not authorized — cannot send DM")
        return False

    access_token, _ = creds
    page_id = _get_page_id()
    if not page_id:
        logger.error("No page_id saved — re-authorize Instagram")
        return False

    resp = requests.post(
        f"https://graph.facebook.com/v21.0/{page_id}/messages",
        params={"access_token": access_token},
        json={
            "recipient": {"id": recipient_id},
            "message": {"text": message_text},
            "messaging_type": "RESPONSE",
        },
        headers={"Content-Type": "application/json"},
        timeout=15,
    )

    if resp.status_code == 200:
        logger.info(f"DM sent to {recipient_id}")
        return True
    else:
        logger.error(f"DM failed to {recipient_id}: {resp.status_code} {resp.text[:300]}")
        return False


def _log_dm(comment_id: str, username: str, keyword: str, media_id: str, success: bool):
    """Log DM sent for analytics."""
    try:
        log = []
        if DM_LOG_FILE.exists():
            log = json.loads(DM_LOG_FILE.read_text(encoding="utf-8"))
        log.append({
            "comment_id": comment_id,
            "username": username,
            "keyword": keyword,
            "media_id": media_id,
            "success": success,
            "timestamp": datetime.now().isoformat(),
        })
        # Keep last 1000 entries
        if len(log) > 1000:
            log = log[-1000:]
        DM_LOG_FILE.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"DM log error: {e}")


# ──────────────────────────────────────────────
#  Webhook signature verification
# ──────────────────────────────────────────────

def _verify_signature(payload: bytes, signature: str) -> bool:
    """Verify X-Hub-Signature-256 from Meta.

    We accept either secret because the same bot handles webhooks from
    two different Meta apps:
      - Facebook login product — signs with META_APP_SECRET
      - Instagram Login API     — signs with INSTAGRAM_APP_SECRET
    """
    secrets = [s for s in (META_APP_SECRET, INSTAGRAM_APP_SECRET) if s]
    if not secrets:
        logger.warning("No app secrets set — skipping signature verification")
        return True

    for secret in secrets:
        expected = "sha256=" + hmac.new(
            secret.encode(), payload, hashlib.sha256
        ).hexdigest()
        if hmac.compare_digest(expected, signature):
            return True
    return False


# ──────────────────────────────────────────────
#  Webhook handlers
# ──────────────────────────────────────────────

async def handle_webhook_verify(request: web.Request) -> web.Response:
    """Handle GET — Meta webhook verification challenge."""
    mode = request.query.get("hub.mode")
    token = request.query.get("hub.verify_token")
    challenge = request.query.get("hub.challenge")

    if mode == "subscribe" and token == INSTAGRAM_WEBHOOK_VERIFY_TOKEN:
        logger.info("Webhook verification successful")
        return web.Response(text=challenge, status=200)

    logger.warning(f"Webhook verification failed: mode={mode}, token={token}")
    return web.Response(text="Forbidden", status=403)


async def handle_webhook_post(request: web.Request) -> web.Response:
    """Handle POST — incoming comment notifications from Instagram."""
    body = await request.read()

    # Verify signature
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_signature(body, signature):
        logger.warning("Invalid webhook signature")
        return web.Response(text="Invalid signature", status=403)

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return web.Response(text="Bad JSON", status=400)

    if data.get("object") != "instagram":
        return web.Response(text="ok", status=200)

    # Process comment events (from "changes" field)
    keywords = _load_keywords()

    for entry in data.get("entry", []):
        # ── Comments (field changes) ──
        for change in entry.get("changes", []):
            if change.get("field") != "comments":
                continue

            value = change.get("value", {})
            comment_text = value.get("text", "").lower().strip()
            comment_id = value.get("id")
            media_info = value.get("media", {})
            media_id = media_info.get("id", "")
            from_user = value.get("from", {})
            username = from_user.get("username", "unknown")
            from_user_id = from_user.get("id", "")

            if not comment_id or not comment_text:
                continue

            # Skip comments from ourselves — otherwise our own public reply
            # could re-trigger the webhook and create an infinite loop.
            creds_check = _get_instagram_token()
            if creds_check and from_user_id == creds_check[1]:
                logger.info(f"Skipping own comment {comment_id}")
                continue

            logger.info(f"Comment from @{username} on media {media_id}: '{comment_text}'")

            # Check keyword match — try exact media match first, then global
            matched = None

            # 1. Check if this media has a registered keyword
            if media_id in keywords:
                kw_data = keywords[media_id]
                if kw_data["keyword"] in comment_text:
                    matched = kw_data

            # 2. If no media-specific match, check all keywords (global)
            if not matched:
                for mid, kw_data in keywords.items():
                    if kw_data["keyword"] in comment_text:
                        matched = kw_data
                        break

            if matched:
                logger.info(f"Keyword '{matched['keyword']}' matched! Sending DM to @{username}")
                reply_text = matched["reply_text"]
                # Append guide_url only if it's set AND not already present
                # in reply_text (older saved keywords had the URL in both
                # fields, which caused the link to be duplicated in DMs).
                guide_url = matched.get("guide_url") or ""
                if guide_url and guide_url not in reply_text:
                    reply_text += f"\n\n{guide_url}"

                success = _send_private_reply(comment_id, reply_text)
                _log_dm(comment_id, username, matched["keyword"], media_id, success)

                # Public comment reply (anti-spam: rotating templates).
                # Fires only if DM succeeded — no point acknowledging
                # publicly if the person never got the DM.
                if success:
                    _send_comment_reply(comment_id, _pick_comment_reply())

        # ── Messaging events (DMs, postbacks, referrals, seen) ──
        for event in entry.get("messaging", []):
            sender_id = event.get("sender", {}).get("id", "")
            if not sender_id:
                continue

            # Skip messages from ourselves
            creds = _get_instagram_token()
            if creds and sender_id == creds[1]:
                continue

            # --- Incoming DM message ---
            if "message" in event:
                msg = event["message"]
                text = msg.get("text", "")
                mid = msg.get("mid", "")
                logger.info(f"DM from {sender_id}: '{text}' (mid={mid})")

                # Check keyword match in DM text
                matched = None
                text_lower = text.lower().strip()
                for media_id, kw_data in keywords.items():
                    if kw_data["keyword"] in text_lower:
                        matched = kw_data
                        break

                if matched:
                    reply_text = matched["reply_text"]
                    if matched.get("guide_url"):
                        reply_text += f"\n\n{matched['guide_url']}"
                    _send_dm(sender_id, reply_text)
                    _log_dm(mid, sender_id, matched["keyword"], "", True)
                else:
                    # Default reply for unrecognized DM
                    _send_dm(sender_id, "Спасибо за сообщение! Напишите ключевое слово из поста, чтобы получить материал.")

            # --- Postback (button click) ---
            elif "postback" in event:
                postback = event["postback"]
                payload = postback.get("payload", "")
                title = postback.get("title", "")
                logger.info(f"Postback from {sender_id}: payload='{payload}', title='{title}'")

                # Match payload against keywords
                matched = None
                payload_lower = payload.lower().strip()
                for media_id, kw_data in keywords.items():
                    if kw_data["keyword"] in payload_lower:
                        matched = kw_data
                        break

                if matched:
                    reply_text = matched["reply_text"]
                    if matched.get("guide_url"):
                        reply_text += f"\n\n{matched['guide_url']}"
                    _send_dm(sender_id, reply_text)

            # --- Referral (user came from ad/link) ---
            elif "referral" in event:
                ref = event["referral"]
                source = ref.get("source", "")
                ref_type = ref.get("type", "")
                ref_data = ref.get("ref", "")
                logger.info(f"Referral from {sender_id}: source={source}, type={ref_type}, ref={ref_data}")

            # --- Message seen ---
            elif "read" in event:
                watermark = event["read"].get("watermark", "")
                logger.info(f"Messages read by {sender_id} up to {watermark}")

    return web.Response(text="ok", status=200)


# ──────────────────────────────────────────────
#  Server lifecycle
# ──────────────────────────────────────────────

async def handle_oauth_callback(request: web.Request) -> web.Response:
    """Handle OAuth callback for YouTube/Instagram authorization."""
    code = request.query.get("code")
    error = request.query.get("error")

    if error:
        logger.warning(f"OAuth callback error: {error}")
        return web.Response(
            text="❌ Авторизация отменена. Вернись в бот и попробуй снова.",
            content_type="text/html", charset="utf-8",
        )

    if not code:
        return web.Response(
            text="❌ Код авторизации не получен.",
            content_type="text/html", charset="utf-8",
        )

    # Try YouTube first, then Instagram
    from crosspost import youtube_exchange_code, instagram_exchange_code

    # Try YouTube
    try:
        yt_result = youtube_exchange_code(code)
        if yt_result:
            logger.info("YouTube authorized via OAuth callback")
            return web.Response(
                text="✅ YouTube авторизован! Вернись в бот — теперь можно публиковать.",
                content_type="text/html", charset="utf-8",
            )
    except Exception as e:
        logger.debug(f"YouTube exchange failed (trying Instagram): {e}")

    # Try Instagram/Facebook
    try:
        ig_result = instagram_exchange_code(code)
        if ig_result:
            logger.info("Instagram authorized via OAuth callback")
            return web.Response(
                text="✅ Instagram авторизован! Вернись в бот — CTA и кросспост снова работают.",
                content_type="text/html", charset="utf-8",
            )
    except Exception as e:
        logger.debug(f"Instagram exchange also failed: {e}")

    logger.error("OAuth callback: neither YouTube nor Instagram accepted the code")
    return web.Response(
        text="❌ Не удалось обменять код ни для YouTube, ни для Instagram. Попробуй ещё раз.",
        content_type="text/html", charset="utf-8",
    )


async def handle_vk_oauth_callback(request: web.Request) -> web.Response:
    """Handle VK ID OAuth callback — exchange code for token via PKCE."""
    code = request.query.get("code")
    device_id = request.query.get("device_id", "")
    error = request.query.get("error")

    if error:
        desc = request.query.get("error_description", error)
        logger.warning(f"VK OAuth callback error: {error} — {desc}")
        return web.Response(
            text=f"❌ VK авторизация отменена: {desc}\nВернись в бот и попробуй снова.",
            content_type="text/html", charset="utf-8",
        )

    if not code:
        return web.Response(
            text="❌ Код авторизации VK не получен.",
            content_type="text/html", charset="utf-8",
        )

    from crosspost import vk_exchange_code
    try:
        result = vk_exchange_code(code, device_id)
        if result:
            logger.info("VK authorized via OAuth callback")
            return web.Response(
                text="✅ VK авторизован! Вернись в бот — VK Клипы теперь доступны в кросспостинге.",
                content_type="text/html", charset="utf-8",
            )
    except Exception as e:
        logger.error(f"VK token exchange failed: {e}", exc_info=True)

    return web.Response(
        text="❌ Не удалось получить токен VK. Вернись в бот и попробуй /vk_auth ещё раз.",
        content_type="text/html", charset="utf-8",
    )


def create_webhook_app() -> web.Application:
    """Create aiohttp app for Instagram webhooks + OAuth callbacks."""
    app = web.Application()
    app.router.add_get("/webhook/instagram", handle_webhook_verify)
    app.router.add_post("/webhook/instagram", handle_webhook_post)
    app.router.add_get("/oauth/callback", handle_oauth_callback)
    app.router.add_get("/oauth/vk/callback", handle_vk_oauth_callback)
    return app


async def start_webhook_server():
    """Start the webhook server (call from bot's main loop)."""
    app = create_webhook_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", INSTAGRAM_DM_PORT)
    await site.start()
    logger.info(f"Instagram DM webhook server started on port {INSTAGRAM_DM_PORT}")

    # Самовосстановление подписки Страницы на feed (комментарии). Подписка
    # обнуляется при перевыдаче токена / смене прав — без этого Comment-to-DM
    # воронка тихо умирает («то работало, то слетало»). Проверяем при каждом
    # старте бота. Не блокирует запуск сервера при сбое.
    try:
        import asyncio as _asyncio
        from crosspost import ensure_page_subscribed
        await _asyncio.to_thread(ensure_page_subscribed)
    except Exception as e:
        logger.warning(f"[ig-subscribe] вызов при старте не удался: {e}")

    return runner
