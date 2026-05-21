"""
crosspost.py — Direct cross-posting to YouTube Shorts and Instagram Reels.
No Postiz dependency. Uses YouTube Data API v3 and Instagram Graph API.

Usage from bot:
    from crosspost import youtube_upload_short, instagram_upload_reel, youtube_auth_url, youtube_exchange_code
"""

import os
import json
import time
import logging
from pathlib import Path
from datetime import datetime, timedelta

import requests
from requests_toolbelt import MultipartEncoder
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger("content_bot.crosspost")

# ──────────────────────────────────────────────
#  Config
# ──────────────────────────────────────────────
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET")
YOUTUBE_REDIRECT_URI = "https://maksim-bot.panferov-ai.ru/oauth/callback"
YOUTUBE_TOKEN_FILE = Path(__file__).parent / "youtube_token.json"

# Meta / Instagram (auth goes through Facebook OAuth)
META_APP_ID = os.getenv("META_APP_ID", "")
META_APP_SECRET = os.getenv("META_APP_SECRET", "")
INSTAGRAM_ACCESS_TOKEN_FILE = Path(__file__).parent / "instagram_token.json"

# VK Clips
VK_APP_ID = os.getenv("VK_APP_ID", "")
VK_TOKEN_FILE = Path(__file__).parent / "vk_token.json"

SCOPES_YOUTUBE = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


# ══════════════════════════════════════════════
#  YOUTUBE SHORTS
# ══════════════════════════════════════════════

def _load_youtube_token() -> dict | None:
    """Load saved YouTube OAuth token."""
    if YOUTUBE_TOKEN_FILE.exists():
        try:
            data = json.loads(YOUTUBE_TOKEN_FILE.read_text(encoding="utf-8"))
            return data
        except Exception:
            return None
    return None


def _save_youtube_token(token_data: dict):
    """Save YouTube OAuth token to disk."""
    YOUTUBE_TOKEN_FILE.write_text(
        json.dumps(token_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("YouTube token saved")


def _refresh_youtube_token(token_data: dict) -> dict | None:
    """Refresh expired YouTube access token using refresh_token."""
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        logger.error("No refresh_token available for YouTube")
        return None

    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": YOUTUBE_CLIENT_ID,
            "client_secret": YOUTUBE_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        logger.error(f"YouTube token refresh failed: {resp.status_code} {resp.text[:200]}")
        return None

    new_data = resp.json()
    # Preserve refresh_token (Google doesn't always return it on refresh)
    new_data["refresh_token"] = refresh_token
    new_data["obtained_at"] = time.time()
    _save_youtube_token(new_data)
    logger.info("YouTube token refreshed")
    return new_data


def _get_youtube_access_token() -> str | None:
    """Get a valid YouTube access token, refreshing if needed."""
    token_data = _load_youtube_token()
    if not token_data:
        return None

    # Check if expired (with 5 min buffer)
    obtained = token_data.get("obtained_at", 0)
    expires_in = token_data.get("expires_in", 3600)
    if time.time() > obtained + expires_in - 300:
        token_data = _refresh_youtube_token(token_data)
        if not token_data:
            return None

    return token_data.get("access_token")


def youtube_is_connected() -> bool:
    """Check if YouTube OAuth token exists."""
    return _load_youtube_token() is not None


def youtube_auth_url() -> str:
    """Generate YouTube OAuth authorization URL for one-time setup."""
    params = {
        "client_id": YOUTUBE_CLIENT_ID,
        "redirect_uri": YOUTUBE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES_YOUTUBE),
        "access_type": "offline",
        "prompt": "consent",
    }
    qs = "&".join(f"{k}={requests.utils.quote(str(v))}" for k, v in params.items())
    return f"https://accounts.google.com/o/oauth2/v2/auth?{qs}"


def youtube_exchange_code(code: str) -> dict | None:
    """Exchange authorization code for access + refresh tokens."""
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": YOUTUBE_CLIENT_ID,
            "client_secret": YOUTUBE_CLIENT_SECRET,
            "redirect_uri": YOUTUBE_REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        logger.error(f"YouTube code exchange failed: {resp.status_code} {resp.text[:300]}")
        return None

    token_data = resp.json()
    token_data["obtained_at"] = time.time()
    _save_youtube_token(token_data)
    logger.info("YouTube authorized successfully")
    return token_data


def youtube_upload_short(
    video_path: str,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    thumbnail_path: str | None = None,
    privacy: str = "public",
) -> dict | None:
    """
    Upload a YouTube Short with optional custom thumbnail.

    Returns dict with video id and url on success, None on failure.
    """
    access_token = _get_youtube_access_token()
    if not access_token:
        logger.error("YouTube not authorized. Run /yt_auth first.")
        return None

    if not Path(video_path).exists():
        logger.error(f"Video file not found: {video_path}")
        return None

    # Add #Shorts to title/description to signal YouTube
    if "#Shorts" not in title and "#Shorts" not in (description or ""):
        description = (description or "") + "\n\n#Shorts"

    # --- Step 1: Upload video via resumable upload ---
    metadata = {
        "snippet": {
            "title": title[:100],
            "description": (description or "")[:5000],
            "tags": tags or [],
            "categoryId": "28",  # Science & Technology
            "defaultLanguage": "ru",
            "defaultAudioLanguage": "ru",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
            "madeForKids": False,
        },
    }

    # Initiate resumable upload
    file_size = Path(video_path).stat().st_size
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/*",
        "X-Upload-Content-Length": str(file_size),
    }

    init_resp = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos"
        "?uploadType=resumable&part=snippet,status",
        headers=headers,
        json=metadata,
        timeout=30,
    )

    if init_resp.status_code not in (200, 308):
        logger.error(f"YouTube upload init failed: {init_resp.status_code} {init_resp.text[:300]}")
        return None

    upload_url = init_resp.headers.get("Location")
    if not upload_url:
        logger.error("No upload URL returned from YouTube")
        return None

    # Upload video data
    with open(video_path, "rb") as f:
        upload_resp = requests.put(
            upload_url,
            data=f,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "video/*",
                "Content-Length": str(file_size),
            },
            timeout=600,
        )

    if upload_resp.status_code not in (200, 201):
        logger.error(f"YouTube video upload failed: {upload_resp.status_code} {upload_resp.text[:300]}")
        return None

    video_data = upload_resp.json()
    video_id = video_data.get("id")
    logger.info(f"YouTube video uploaded: {video_id}")

    # --- Step 2: Set custom thumbnail ---
    if thumbnail_path and Path(thumbnail_path).exists() and video_id:
        _youtube_set_thumbnail(access_token, video_id, thumbnail_path)

    return {
        "id": video_id,
        "url": f"https://youtube.com/shorts/{video_id}",
        "title": title,
    }


def _youtube_set_thumbnail(access_token: str, video_id: str, thumbnail_path: str):
    """Set custom thumbnail for a YouTube video."""
    try:
        with open(thumbnail_path, "rb") as f:
            resp = requests.post(
                f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
                f"?videoId={video_id}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "image/jpeg",
                },
                data=f,
                timeout=30,
            )
        if resp.status_code == 200:
            logger.info(f"Thumbnail set for video {video_id}")
        else:
            logger.warning(f"Thumbnail upload failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"Thumbnail upload error: {e}")


# ══════════════════════════════════════════════
#  INSTAGRAM REELS (via Facebook Graph API)
# ══════════════════════════════════════════════
#
# Instagram Content Publishing API requires:
# 1. Facebook OAuth (not Instagram Basic Display)
# 2. Permissions: instagram_basic, instagram_content_publish, pages_show_list, pages_read_engagement
# 3. Facebook Page connected to Instagram Professional account
# 4. We store: page access token + instagram business account ID
#

def _load_instagram_token() -> dict | None:
    """Load saved Instagram token data."""
    if INSTAGRAM_ACCESS_TOKEN_FILE.exists():
        try:
            return json.loads(INSTAGRAM_ACCESS_TOKEN_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _save_instagram_token(token_data: dict):
    """Save Instagram token to disk."""
    INSTAGRAM_ACCESS_TOKEN_FILE.write_text(
        json.dumps(token_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Instagram token saved")


def _refresh_instagram_token(token_data: dict) -> dict | None:
    """Refresh Facebook long-lived token (valid 60 days, refreshable)."""
    access_token = token_data.get("access_token")
    if not access_token:
        return None

    resp = requests.get(
        "https://graph.facebook.com/v21.0/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": META_APP_ID,
            "client_secret": META_APP_SECRET,
            "fb_exchange_token": access_token,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        logger.error(f"Instagram/FB token refresh failed: {resp.status_code} {resp.text[:200]}")
        return None

    new_data = resp.json()
    # Preserve our stored metadata
    new_data["ig_user_id"] = token_data.get("ig_user_id")
    new_data["page_id"] = token_data.get("page_id")
    new_data["page_access_token"] = token_data.get("page_access_token")
    new_data["obtained_at"] = time.time()
    _save_instagram_token(new_data)
    logger.info("Instagram/FB token refreshed")
    return new_data


def _get_instagram_access_token() -> str | None:
    """Get a valid Instagram page access token."""
    token_data = _load_instagram_token()
    if not token_data:
        return None

    # Refresh user token if older than 50 days
    obtained = token_data.get("obtained_at", 0)
    if time.time() > obtained + 50 * 86400:
        token_data = _refresh_instagram_token(token_data)
        if not token_data:
            return None

    # Page tokens derived from long-lived user tokens don't expire
    return token_data.get("page_access_token") or token_data.get("access_token")


def _get_instagram_user_id() -> str | None:
    """Get stored Instagram business account ID."""
    token_data = _load_instagram_token()
    if not token_data:
        return None
    return token_data.get("ig_user_id")


def instagram_is_connected() -> bool:
    """Check if Instagram token exists with ig_user_id."""
    data = _load_instagram_token()
    return data is not None and data.get("ig_user_id") is not None


def instagram_auth_url() -> str:
    """Generate Facebook OAuth URL for Instagram Content Publishing."""
    params = {
        "client_id": META_APP_ID,
        "redirect_uri": YOUTUBE_REDIRECT_URI,
        "scope": "instagram_basic,instagram_content_publish,instagram_manage_comments,instagram_manage_messages,pages_show_list,pages_read_engagement,pages_manage_metadata",
        "response_type": "code",
    }
    qs = "&".join(f"{k}={requests.utils.quote(str(v))}" for k, v in params.items())
    return f"https://www.facebook.com/v21.0/dialog/oauth?{qs}"


def instagram_exchange_code(code: str) -> dict | None:
    """
    Exchange Facebook auth code for tokens and discover Instagram business account.

    Flow:
    1. Code → short-lived user token
    2. Short token → long-lived user token (60 days)
    3. GET /me/accounts → find Facebook Page with connected Instagram
    4. Get page access token + Instagram business account ID
    """
    # Step 1: Short-lived user token
    resp = requests.get(
        "https://graph.facebook.com/v21.0/oauth/access_token",
        params={
            "client_id": META_APP_ID,
            "client_secret": META_APP_SECRET,
            "redirect_uri": YOUTUBE_REDIRECT_URI,
            "code": code,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        logger.error(f"FB code exchange failed: {resp.status_code} {resp.text[:300]}")
        return None

    short_token = resp.json().get("access_token")
    logger.info("Facebook short-lived token obtained")

    # Step 2: Exchange for long-lived token
    resp2 = requests.get(
        "https://graph.facebook.com/v21.0/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": META_APP_ID,
            "client_secret": META_APP_SECRET,
            "fb_exchange_token": short_token,
        },
        timeout=15,
    )
    if resp2.status_code != 200:
        logger.error(f"FB long token exchange failed: {resp2.status_code} {resp2.text[:300]}")
        return None

    long_token = resp2.json().get("access_token")
    logger.info("Facebook long-lived token obtained")

    # Step 3: Get Facebook Pages
    pages_resp = requests.get(
        "https://graph.facebook.com/v21.0/me/accounts",
        params={"access_token": long_token},
        timeout=15,
    )
    if pages_resp.status_code != 200:
        logger.error(f"FB pages fetch failed: {pages_resp.status_code} {pages_resp.text[:300]}")
        return None

    pages = pages_resp.json().get("data", [])
    if not pages:
        logger.error("No Facebook Pages found. Instagram must be connected to a Facebook Page.")
        return None

    # Step 4: Find page with Instagram business account
    ig_user_id = None
    page_id = None
    page_access_token = None

    for page in pages:
        page_id = page["id"]
        page_access_token = page["access_token"]

        ig_resp = requests.get(
            f"https://graph.facebook.com/v21.0/{page_id}",
            params={
                "fields": "instagram_business_account",
                "access_token": page_access_token,
            },
            timeout=15,
        )
        if ig_resp.status_code == 200:
            ig_account = ig_resp.json().get("instagram_business_account")
            if ig_account:
                ig_user_id = ig_account["id"]
                logger.info(f"Found Instagram business account: {ig_user_id} (page: {page.get('name')})")
                break

    if not ig_user_id:
        logger.error("No Instagram business account found on any Facebook Page")
        return None

    # Step 5: Subscribe page to webhook events (comments, messages)
    try:
        sub_resp = requests.post(
            f"https://graph.facebook.com/v21.0/{page_id}/subscribed_apps",
            data={
                "subscribed_fields": "feed,messages,messaging_postbacks,messaging_referrals",
                "access_token": page_access_token,
            },
            timeout=15,
        )
        if sub_resp.status_code == 200:
            logger.info(f"Page {page_id} subscribed to webhook events")
        else:
            logger.warning(f"Page webhook subscription failed: {sub_resp.status_code} {sub_resp.text[:200]}")
    except Exception as e:
        logger.warning(f"Page webhook subscription error: {e}")

    token_data = {
        "access_token": long_token,
        "page_id": page_id,
        "page_access_token": page_access_token,
        "ig_user_id": ig_user_id,
        "obtained_at": time.time(),
    }
    _save_instagram_token(token_data)
    logger.info(f"Instagram authorized: IG account {ig_user_id}")
    return token_data


def instagram_upload_reel(
    video_url: str,
    caption: str = "",
    cover_url: str | None = None,
    share_to_feed: bool = True,
) -> dict | None:
    """
    Upload Instagram Reel via Graph API.

    IMPORTANT: video_url must be a publicly accessible URL (not a local file).
    The video needs to be hosted somewhere Instagram can fetch it.

    Returns dict with media_id on success, None on failure.
    """
    access_token = _get_instagram_access_token()
    ig_user_id = _get_instagram_user_id()
    if not access_token or not ig_user_id:
        logger.error("Instagram not authorized. Run instagram_auth.py first.")
        return None

    # Step 1: Create media container
    container_params = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption[:2200],
        "share_to_feed": str(share_to_feed).lower(),
        "access_token": access_token,
    }
    if cover_url:
        container_params["cover_url"] = cover_url

    resp = requests.post(
        f"https://graph.facebook.com/v21.0/{ig_user_id}/media",
        data=container_params,
        timeout=30,
    )
    if resp.status_code != 200:
        logger.error(f"Instagram container creation failed: {resp.status_code} {resp.text[:300]}")
        return None

    container_id = resp.json().get("id")
    if not container_id:
        logger.error("No container ID returned from Instagram")
        return None

    logger.info(f"Instagram container created: {container_id}")

    # Step 2: Wait for video processing (poll status)
    error_count = 0
    for attempt in range(30):  # up to 5 min
        time.sleep(10)
        status_resp = requests.get(
            f"https://graph.facebook.com/v21.0/{container_id}",
            params={
                "fields": "status_code,status",
                "access_token": access_token,
            },
            timeout=15,
        )
        if status_resp.status_code != 200:
            logger.warning(f"Instagram status check HTTP {status_resp.status_code} (attempt {attempt+1})")
            continue

        resp_json = status_resp.json()
        status = resp_json.get("status_code")
        status_msg = resp_json.get("status", "")
        logger.info(f"Instagram container status: {status} | {status_msg} (attempt {attempt+1})")

        if status == "FINISHED":
            break
        elif status == "ERROR":
            error_count += 1
            logger.warning(f"Instagram container ERROR ({error_count}/3): {status_msg}")
            if error_count >= 3:
                logger.error(f"Instagram video processing failed after {error_count} consecutive errors: {status_msg}")
                return None
        else:
            error_count = 0  # reset on non-error status
    else:
        logger.error("Instagram video processing timed out")
        return None

    # Step 3: Publish
    publish_resp = requests.post(
        f"https://graph.facebook.com/v21.0/{ig_user_id}/media_publish",
        data={
            "creation_id": container_id,
            "access_token": access_token,
        },
        timeout=30,
    )
    if publish_resp.status_code != 200:
        logger.error(f"Instagram publish failed: {publish_resp.status_code} {publish_resp.text[:300]}")
        return None

    media_id = publish_resp.json().get("id")
    logger.info(f"Instagram Reel published: {media_id}")

    return {
        "id": media_id,
        "platform": "instagram",
    }


# ══════════════════════════════════════════════
#  TELEGRAM CHANNEL
# ══════════════════════════════════════════════

TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")  # e.g. "@panferov_ai" or chat_id


async def telegram_post_to_channel(
    bot,
    text: str,
    video_path: str | None = None,
    photos: list[dict] | None = None,
) -> dict | None:
    """
    Post to Telegram channel.

    - If `video_path` is provided → sends video with caption (legacy path).
    - If `photos` is provided (list of {"source": "lib"|"telegram",
      "path"|"file_id": ...}) → attaches photos:
        * 1 photo + text ≤1024 → send_photo with full caption
        * N photos + text ≤1024 → send_media_group, caption on first item
        * Long text (>1024) → send_media_group WITHOUT caption + follow-up
          send_message with the full text (two messages — standard pattern
          for big channels with long-form posts; review_essay for Maksim
          regularly exceeds 1024 chars).
    - Otherwise → plain send_message.

    Returns dict with message_id of the LAST sent message on success.
    Added 9 May 2026 to support Pipeline #1 (Selfie) photo attachments.
    """
    if not TELEGRAM_CHANNEL_ID:
        logger.error("TELEGRAM_CHANNEL_ID not set")
        return None

    try:
        # Legacy: video with caption (Artem's contentbot path)
        if video_path and Path(video_path).exists():
            with open(video_path, "rb") as f:
                msg = await bot.send_video(
                    chat_id=TELEGRAM_CHANNEL_ID,
                    video=f,
                    caption=text[:1024] if text else None,
                    parse_mode="HTML",
                )
            logger.info(f"Telegram channel post sent (video): {msg.message_id}")
            return {"id": msg.message_id, "platform": "telegram"}

        # Photo-attached path (Maksim Selfie)
        if photos:
            from contextlib import ExitStack
            from telegram import InputMediaPhoto

            # Codex review #3 P0 (9 May 2026): wrap all file opens in
            # ExitStack so handles get closed right after the awaited send
            # completes. Earlier version leaked descriptors per send_media_group
            # call — invisible at single-shot test, accumulates in long-
            # running bot process.
            short_caption_ok = bool(text) and len(text) <= 1024
            last_msg = None

            if len(photos) == 1 and short_caption_ok:
                # Single photo + short text → send_photo
                p = photos[0]
                if p.get("source") == "telegram" and p.get("file_id"):
                    last_msg = await bot.send_photo(
                        chat_id=TELEGRAM_CHANNEL_ID, photo=p["file_id"],
                        caption=text, parse_mode="HTML",
                    )
                else:
                    with open(p["path"], "rb") as f:
                        last_msg = await bot.send_photo(
                            chat_id=TELEGRAM_CHANNEL_ID, photo=f,
                            caption=text, parse_mode="HTML",
                        )
            elif short_caption_ok:
                # Multiple photos + short text → media_group with caption on first
                with ExitStack() as stack:
                    media: list[InputMediaPhoto] = []
                    for i, p in enumerate(photos[:10]):  # TG hard cap = 10
                        cap = text if i == 0 else None
                        pm = "HTML" if i == 0 else None
                        if p.get("source") == "telegram" and p.get("file_id"):
                            media.append(InputMediaPhoto(
                                media=p["file_id"], caption=cap, parse_mode=pm,
                            ))
                        else:
                            f = stack.enter_context(open(p["path"], "rb"))
                            media.append(InputMediaPhoto(
                                media=f, caption=cap, parse_mode=pm,
                            ))
                    msgs = await bot.send_media_group(
                        chat_id=TELEGRAM_CHANNEL_ID, media=media,
                    )
                last_msg = msgs[-1] if msgs else None
            else:
                # Long text → media_group without caption + follow-up text msg
                if len(photos) == 1:
                    p = photos[0]
                    if p.get("source") == "telegram" and p.get("file_id"):
                        await bot.send_photo(
                            chat_id=TELEGRAM_CHANNEL_ID, photo=p["file_id"],
                        )
                    else:
                        with open(p["path"], "rb") as f:
                            await bot.send_photo(
                                chat_id=TELEGRAM_CHANNEL_ID, photo=f,
                            )
                else:
                    with ExitStack() as stack:
                        media = []
                        for p in photos[:10]:
                            if p.get("source") == "telegram" and p.get("file_id"):
                                media.append(InputMediaPhoto(media=p["file_id"]))
                            else:
                                f = stack.enter_context(open(p["path"], "rb"))
                                media.append(InputMediaPhoto(media=f))
                        await bot.send_media_group(
                            chat_id=TELEGRAM_CHANNEL_ID, media=media,
                        )
                # Then text as separate message
                last_msg = await bot.send_message(
                    chat_id=TELEGRAM_CHANNEL_ID, text=text[:4096],
                    parse_mode="HTML",
                )

            if last_msg:
                logger.info(
                    f"Telegram channel post sent (photos={len(photos)}): "
                    f"{last_msg.message_id}"
                )
                return {"id": last_msg.message_id, "platform": "telegram"}
            return None

        # Default: plain text
        msg = await bot.send_message(
            chat_id=TELEGRAM_CHANNEL_ID,
            text=text[:4096],
            parse_mode="HTML",
        )
        logger.info(f"Telegram channel post sent: {msg.message_id}")
        return {"id": msg.message_id, "platform": "telegram"}
    except Exception as e:
        logger.error(f"Telegram channel post error: {e}")
        return None


# ══════════════════════════════════════════════
#  FILE HOSTING HELPER (for Instagram)
# ══════════════════════════════════════════════

def upload_video_to_temp_hosting(video_path: str) -> str | None:
    """
    Make video available via public URL for Instagram.
    Copies to /root/maksim-bot/media/ served by nginx at maksim-bot.panferov-ai.ru/media/
    Returns public URL or None.
    """
    if not Path(video_path).exists():
        return None

    try:
        import hashlib, time as _time, shutil
        media_dir = Path("/root/maksim-bot/media")
        media_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(video_path).suffix or ".mp4"
        ts = str(_time.time()).encode()
        name_hash = hashlib.md5(ts + video_path.encode()).hexdigest()[:12]
        filename = f"video_{name_hash}{ext}"
        dest = media_dir / filename
        shutil.copy2(video_path, str(dest))
        dest.chmod(0o644)
        url = f"https://maksim-bot.panferov-ai.ru/media/{filename}"
        logger.info(f"Video available at: {url}")
        return url
    except Exception as e:
        logger.error(f"Temp hosting upload error: {e}")
    return None


# ══════════════════════════════════════════════
#  ONE-TIME AUTH HELPER SCRIPT
# ══════════════════════════════════════════════

def run_oauth_server(service: str = "youtube") -> str:
    """
    Start a tiny HTTP server on localhost:8080 to capture OAuth callback.
    Returns the authorization code.

    This is meant to be run manually once via SSH tunnel:
        ssh -L 8080:localhost:8080 root@178.104.133.148
    Then open the auth URL in browser.
    """
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from urllib.parse import urlparse, parse_qs

    captured_code = None

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal captured_code
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            code = params.get("code", [None])[0]

            if code:
                captured_code = code
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    f"<h1>Авторизация {service} успешна!</h1>"
                    f"<p>Код получен. Можно закрыть это окно.</p>".encode()
                )
            else:
                error = params.get("error", ["unknown"])[0]
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"<h1>Ошибка: {error}</h1>".encode())

        def log_message(self, format, *args):
            pass  # Suppress default logging

    server = HTTPServer(("0.0.0.0", 8080), CallbackHandler)
    server.timeout = 300  # 5 min to complete auth

    logger.info(f"OAuth server started on :8080 for {service}")

    while captured_code is None:
        server.handle_request()

    server.server_close()
    return captured_code


# ══════════════════════════════════════════════
#  TIKTOK (via TikTokAutoUploader)
# ══════════════════════════════════════════════

TIKTOK_ACCOUNT = os.getenv("TIKTOK_ACCOUNT", "")


def tiktok_is_connected() -> bool:
    """Check if TikTok account is configured."""
    return bool(TIKTOK_ACCOUNT)


def tiktok_upload_video(
    video_path: str,
    description: str = "",
    hashtags: list[str] | None = None,
    cover_path: str | None = None,
) -> dict | None:
    """
    Upload video to TikTok via TikTokAutoUploader (browser automation).

    Must run within the tiktok-env virtual environment on the server.
    Returns dict with status on success, None on failure.
    """
    if not TIKTOK_ACCOUNT:
        logger.error("TIKTOK_ACCOUNT not set in .env")
        return None

    if not Path(video_path).exists():
        logger.error(f"Video file not found: {video_path}")
        return None

    # Build the upload script — runs in a subprocess using the SAME Python
    # interpreter as the bot (main venv at /root/maksim-bot/venv/bin/python).
    # The old code referenced a separate /root/tiktok-env venv that no longer
    # exists; tiktokautouploader is installed directly in the main venv.
    tags = hashtags or ["#shorts", "#ai"]
    tags_str = json.dumps(tags)

    upload_script = f"""
from tiktokautouploader import upload_tiktok

upload_tiktok(
    video={json.dumps(video_path)},
    description={json.dumps(description[:150])},
    accountname={json.dumps(TIKTOK_ACCOUNT)},
    hashtags={tags_str},
    headless=True,
    stealth=True,
)
print("TIKTOK_UPLOAD_OK")
"""

    import subprocess
    import sys
    script_path = "/tmp/tiktok_upload.py"
    Path(script_path).write_text(upload_script, encoding="utf-8")

    try:
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=600,  # 10 min max
            cwd="/root/maksim-bot",  # cookies are here: TK_cookies_{account}.json
            env={**os.environ, "HOME": "/root", "DISPLAY": ":99"},
        )

        if "TIKTOK_UPLOAD_OK" in result.stdout:
            logger.info(f"TikTok video uploaded: {TIKTOK_ACCOUNT}")
            return {"platform": "tiktok", "account": TIKTOK_ACCOUNT}
        else:
            logger.error(f"TikTok upload failed: {result.stdout[-500:]} {result.stderr[-500:]}")
            return None
    except subprocess.TimeoutExpired:
        logger.error("TikTok upload timed out (5 min)")
        return None
    except Exception as e:
        logger.error(f"TikTok upload error: {e}")
        return None


# ══════════════════════════════════════════════
#  VK CLIPS (via undocumented shortVideo.create)
# ══════════════════════════════════════════════

def _load_vk_token() -> dict | None:
    """Load saved VK OAuth token."""
    if VK_TOKEN_FILE.exists():
        try:
            return json.loads(VK_TOKEN_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _save_vk_token(token_data: dict):
    """Save VK OAuth token to disk."""
    VK_TOKEN_FILE.write_text(
        json.dumps(token_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("VK token saved")


def vk_is_connected() -> bool:
    """Check if VK token file exists and has access_token."""
    data = _load_vk_token()
    return data is not None and bool(data.get("access_token"))


VK_REDIRECT_URI = "https://maksim-bot.panferov-ai.ru/oauth/vk/callback"
VK_PKCE_FILE = Path(__file__).parent / "vk_pkce_verifier.txt"


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    import hashlib
    import base64
    code_verifier = base64.urlsafe_b64encode(os.urandom(40)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def vk_get_auth_url() -> str:
    """Generate VK ID OAuth authorization URL (authorization code + PKCE)."""
    code_verifier, code_challenge = _generate_pkce()
    # Save verifier for later exchange
    VK_PKCE_FILE.write_text(code_verifier, encoding="utf-8")

    params = {
        "response_type": "code",
        "client_id": VK_APP_ID,
        "redirect_uri": VK_REDIRECT_URI,
        "scope": "video wall offline",
        "code_challenge": code_challenge,
        "code_challenge_method": "s256",
        "state": "vk_auth",
    }
    qs = "&".join(f"{k}={requests.utils.quote(str(v))}" for k, v in params.items())
    return f"https://id.vk.com/authorize?{qs}"


def vk_exchange_code(code: str, device_id: str = "") -> dict | None:
    """Exchange authorization code for VK access token using PKCE."""
    if not VK_PKCE_FILE.exists():
        logger.error("VK PKCE verifier file not found")
        return None

    code_verifier = VK_PKCE_FILE.read_text(encoding="utf-8").strip()

    try:
        resp = requests.post(
            "https://id.vk.com/oauth2/auth",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "client_id": VK_APP_ID,
                "redirect_uri": VK_REDIRECT_URI,
                "device_id": device_id,
            },
            timeout=15,
        )
    except Exception as e:
        logger.error(f"VK token exchange request failed: {e}")
        return None

    if resp.status_code != 200:
        logger.error(f"VK token exchange HTTP {resp.status_code}: {resp.text[:500]}")
        return None

    data = resp.json()
    if "access_token" not in data:
        logger.error(f"VK token exchange failed: {data}")
        return None

    token_data = {
        "access_token": data["access_token"],
        "user_id": data.get("user_id"),
        "refresh_token": data.get("refresh_token"),
        "expires_in": data.get("expires_in"),
        "device_id": device_id or data.get("device_id", ""),
        "obtained_at": time.time(),
    }
    _save_vk_token(token_data)
    logger.info(f"VK authorized via PKCE: user_id={token_data.get('user_id')}")

    # Clean up verifier
    try:
        VK_PKCE_FILE.unlink()
    except Exception:
        pass

    return token_data


def _vk_refresh_token() -> str | None:
    """Refresh VK access token using stored refresh_token + device_id. Returns new access_token or None."""
    token_data = _load_vk_token()
    if not token_data or not token_data.get("refresh_token"):
        logger.error("VK refresh impossible: no refresh_token stored")
        return None

    device_id = token_data.get("device_id", "")

    try:
        resp = requests.post(
            "https://id.vk.com/oauth2/auth",
            data={
                "grant_type": "refresh_token",
                "refresh_token": token_data["refresh_token"],
                "client_id": VK_APP_ID,
                "device_id": device_id,
            },
            timeout=15,
        )
    except Exception as e:
        logger.error(f"VK refresh request failed: {e}")
        return None

    if resp.status_code != 200:
        logger.error(f"VK refresh HTTP {resp.status_code}: {resp.text[:500]}")
        return None

    data = resp.json()
    if "access_token" not in data:
        logger.error(f"VK refresh failed: {data}")
        return None

    new_token = {
        "access_token": data["access_token"],
        "user_id": data.get("user_id") or token_data.get("user_id"),
        "refresh_token": data.get("refresh_token") or token_data.get("refresh_token"),
        "expires_in": data.get("expires_in"),
        "device_id": device_id,
        "obtained_at": time.time(),
    }
    _save_vk_token(new_token)
    logger.info(f"VK token refreshed, expires_in={data.get('expires_in')}")
    return new_token["access_token"]


def _vk_get_valid_token() -> str | None:
    """Get a valid VK access token, auto-refreshing if expired."""
    token_data = _load_vk_token()
    if not token_data or not token_data.get("access_token"):
        return None

    # Check if token is likely expired (with 60s margin)
    obtained = token_data.get("obtained_at", 0)
    expires_in = token_data.get("expires_in", 0)
    if expires_in and obtained and (time.time() - obtained) > (expires_in - 60):
        logger.info("VK token expired, refreshing...")
        refreshed = _vk_refresh_token()
        if refreshed:
            return refreshed
        # If refresh failed, try the old token anyway
        logger.warning("VK refresh failed, trying old token")

    return token_data["access_token"]


def vk_upload_clip(
    video_path: str,
    description: str = "",
) -> dict | None:
    """
    Upload a VK Clip via official video.save API with is_clip=1.

    Flow:
    1. Call video.save(is_clip=1) to get upload_url
    2. Upload file to upload_url via multipart
    3. Return result dict or None
    """
    access_token = _vk_get_valid_token()
    if not access_token:
        logger.error("VK not authorized. Run /vk_auth first.")
        return None

    if not Path(video_path).exists():
        logger.error(f"Video file not found: {video_path}")
        return None

    file_size = Path(video_path).stat().st_size

    # Step 1: video.save with is_clip=1
    try:
        resp = requests.get(
            "https://api.vk.com/method/video.save",
            params={
                "access_token": access_token,
                "v": "5.199",
                "name": (description[:120] or "Clip"),
                "description": description[:2048],
                "is_clip": 1,
                "wallpost": 1,
            },
            timeout=30,
        )
    except Exception as e:
        logger.error(f"VK video.save request failed: {e}")
        return None

    if resp.status_code != 200:
        logger.error(f"VK video.save HTTP {resp.status_code}: {resp.text[:300]}")
        return None

    resp_json = resp.json()
    if "error" in resp_json:
        err = resp_json["error"]
        logger.error(f"VK video.save error {err.get('error_code')}: {err.get('error_msg', '')}")
        return None

    response_data = resp_json.get("response", {})
    upload_url = response_data.get("upload_url")
    if not upload_url:
        logger.error(f"VK video.save returned no upload_url: {resp_json}")
        return None

    video_id = response_data.get("video_id")
    owner_id = response_data.get("owner_id")
    logger.info(f"VK upload_url obtained (video_id={video_id}), uploading {file_size} bytes...")

    # Step 2: Upload file via multipart
    try:
        with open(video_path, "rb") as f:
            encoder = MultipartEncoder(
                fields={
                    "video_file": (
                        Path(video_path).name,
                        f,
                        "video/mp4",
                    ),
                }
            )
            upload_resp = requests.post(
                upload_url,
                data=encoder,
                headers={"Content-Type": encoder.content_type},
                timeout=600,
            )
    except Exception as e:
        logger.error(f"VK clip upload request failed: {e}")
        return None

    if upload_resp.status_code != 200:
        logger.error(f"VK clip upload HTTP {upload_resp.status_code}: {upload_resp.text[:300]}")
        return None

    logger.info(f"VK Clip uploaded: owner_id={owner_id}, video_id={video_id}")

    return {
        "platform": "vk",
        "video_id": video_id,
        "owner_id": owner_id,
    }


# ══════════════════════════════════════════════
#  AVAILABLE PLATFORMS LIST
# ══════════════════════════════════════════════

def get_available_platforms() -> list[dict]:
    """
    Return list of available cross-posting platforms and their connection status.
    """
    platforms = []

    platforms.append({
        "id": "youtube",
        "name": "YouTube Shorts",
        "icon": "🎬",
        "connected": youtube_is_connected(),
        "needs_video": True,
    })

    platforms.append({
        "id": "instagram",
        "name": "Instagram Reels",
        "icon": "📸",
        "connected": instagram_is_connected(),
        "needs_video": True,
    })

    platforms.append({
        "id": "tiktok",
        "name": "TikTok",
        "icon": "🎵",
        "connected": tiktok_is_connected(),
        "needs_video": True,
    })

    platforms.append({
        "id": "vk",
        "name": "VK Клипы",
        "icon": "📹",
        "connected": vk_is_connected(),
        "needs_video": True,
    })

    platforms.append({
        "id": "telegram",
        "name": "Telegram канал",
        "icon": "📢",
        "connected": bool(TELEGRAM_CHANNEL_ID),
        "needs_video": False,  # Can post text only
    })

    return platforms
