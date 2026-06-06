"""Launch monitor: watches 10 English-language AI creators on X/Twitter,
detects posts about new AI product launches, scores them with Claude, and
produces a daily digest for Artem to review in Telegram.

Architecture:
    - Hourly poll of each creator's RSS feed (via rsshub.app bridge for X).
    - Each fresh post is hashed and stored in SQLite (seen_launches.db) so
      we never re-process the same tweet.
    - Claude-Sonnet scores the post: is this about a new AI product launch
      (0-10), what product, which company, short Russian summary.
    - Scored launches with score >= 6 are queued for the daily digest.
    - At 10:00 MSK, bot.py's cron reads the queue and posts a digest with
      ✅ В работу / ⏭ Пропустить buttons per launch.
    - Approved launches become "🚀 Разбор запуска" projects in Notion.
    - Nothing is ever auto-published — Artem reviews every draft manually.

This module is intentionally import-safe: no side effects at import time,
all I/O happens inside functions called by bot.py.
"""
from __future__ import annotations

import os
import re
import json
import sqlite3
import hashlib
import logging
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

import requests

import paths

logger = logging.getLogger("content_bot.launch_monitor")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# 10 English-language AI creators who do real launch breakdowns.
# Handle is the bare @name, without the @.  Fetched via Nitter RSS.
CREATORS: list[dict] = [
    {"handle": "mckaywrigley",  "name": "Mckay Wrigley",    "why": "техно-разборы с кодом"},
    {"handle": "minchoi",       "name": "Min Choi",         "why": "агрегатор релизов"},
    {"handle": "LinusEkenstam", "name": "Linus Ekenstam",   "why": "визуальные AI-модели"},
    {"handle": "RowanCheung",   "name": "Rowan Cheung",     "why": "The Rundown AI, дневные дайджесты"},
    {"handle": "mreflow",       "name": "Matt Wolfe",       "why": "еженедельные подборки новинок"},
    {"handle": "skirano",       "name": "Pietro Schirano",  "why": "креативные связки инструментов"},
    {"handle": "nickfloats",    "name": "Nick St. Pierre",  "why": "Midjourney и image AI"},
    {"handle": "bilawalsidhu",  "name": "Bilawal Sidhu",    "why": "кинематографичные AI-видео"},
    {"handle": "aaditsh",       "name": "Aadit Sheth",      "why": "ежедневные треды с примерами"},
    {"handle": "heyBarsee",     "name": "Barsee",           "why": "визуальные разборы виральных запусков"},
    {"handle": "rileybrown_ai", "name": "Riley Brown",      "why": "сплит-видео AI-инструменты, 166K"},
    {"handle": "TheAiGrid",     "name": "TheAIGRID",        "why": "AI-новости, глубокий анализ, 350K YT"},
    {"handle": "jaaborstl",     "name": "Jaa Bors",         "why": "AI-тулзы, короткие разборы, сплит-формат"},
    {"handle": "ai_for_success", "name": "AI For Success",  "why": "AI-новости и инструменты, сплит-видео"},
]

# 10 official AI product accounts — the "horse's mouth" source.
# They announce their own releases first, always with demo media.
OFFICIAL_ACCOUNTS: list[dict] = [
    {"handle": "OpenAI",         "name": "OpenAI",          "why": "официальный аккаунт OpenAI"},
    {"handle": "AnthropicAI",    "name": "Anthropic",       "why": "официальный аккаунт Anthropic"},
    {"handle": "GoogleDeepMind", "name": "Google DeepMind", "why": "официальный аккаунт Google DeepMind"},
    {"handle": "runwayml",       "name": "Runway",          "why": "официальный аккаунт Runway"},
    {"handle": "LumaLabsAI",     "name": "Luma Labs",       "why": "официальный аккаунт Luma Labs"},
    {"handle": "elevenlabsio",   "name": "ElevenLabs",      "why": "официальный аккаунт ElevenLabs"},
    {"handle": "midjourney",     "name": "Midjourney",      "why": "официальный аккаунт Midjourney"},
    {"handle": "higgsfield_ai",  "name": "Higgsfield AI",   "why": "официальный аккаунт Higgsfield"},
    {"handle": "suno_ai_",       "name": "Suno",            "why": "официальный аккаунт Suno"},
    {"handle": "krea_ai",        "name": "Krea",            "why": "официальный аккаунт Krea"},
]

# YouTube channels to watch (primarily for Shorts demos / product promos).
# Channel IDs resolved lazily on first poll from the @handle and cached
# in launch_data/youtube_channels.json.  If resolution fails, the entry
# is silently skipped — we don't want one broken channel to stall the cycle.
YOUTUBE_CREATORS: list[dict] = [
    {"yt_handle": "mreflow",       "name": "Matt Wolfe"},
    {"yt_handle": "bilawalsidhu",  "name": "Bilawal Sidhu"},
    {"yt_handle": "TheRundownAI",  "name": "The Rundown AI (Rowan Cheung)"},
    {"yt_handle": "MinChoi",       "name": "Min Choi"},
    {"yt_handle": "mckaywrigley",  "name": "Mckay Wrigley"},
    {"yt_handle": "rileybrown_ai", "name": "Riley Brown"},
    {"yt_handle": "TheAIGRID",     "name": "TheAIGRID"},
    {"yt_handle": "LinusEkenstam", "name": "Linus Ekenstam"},
    {"yt_handle": "nickfloats",    "name": "Nick St. Pierre"},
    {"yt_handle": "skirano",       "name": "Pietro Schirano"},
]

# Primary RSS source for X/Twitter: nitter.net (public instance, rock-solid
# as of 2026-04).  We fall back through a list of mirrors if the primary 404s
# or gets rate-limited — ordered by historical reliability.
NITTER_MIRRORS = [
    os.getenv("NITTER_PRIMARY", "https://nitter.net"),
    "https://nitter.privacyredirect.com",
    "https://nitter.kavin.rocks",
]

DATA_DIR = Path(__file__).parent / "launch_data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "seen_launches.db"
YT_CHANNEL_CACHE_FILE = DATA_DIR / "youtube_channels.json"

SCORE_THRESHOLD = 6  # launches below this are dropped from the digest
MAX_DIGEST_ITEMS = 15
POST_MAX_AGE_HOURS = 48  # ignore anything older than this when polling


# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen (
                id              TEXT PRIMARY KEY,          -- sha1 of source+url
                creator         TEXT NOT NULL,
                url             TEXT NOT NULL,
                title           TEXT,
                text            TEXT,
                published_at    TEXT,
                fetched_at      TEXT NOT NULL,
                score           INTEGER,
                product         TEXT,
                company         TEXT,
                summary_ru      TEXT,
                status          TEXT NOT NULL DEFAULT 'new'
                                -- new | queued | approved | skipped | drafted
            );
            CREATE INDEX IF NOT EXISTS idx_seen_status    ON seen(status);
            CREATE INDEX IF NOT EXISTS idx_seen_score     ON seen(score);
            CREATE INDEX IF NOT EXISTS idx_seen_fetched   ON seen(fetched_at);
            """
        )


# ---------------------------------------------------------------------------
# RSS fetch
# ---------------------------------------------------------------------------

@dataclass
class RawPost:
    creator: str
    url: str
    title: str
    text: str
    published_at: str  # ISO-8601

    @property
    def id(self) -> str:
        # Normalize URL so different Nitter mirrors produce the same ID.
        # nitter.net/user/status/123 and nitter.privacyredirect.com/user/status/123
        # are the same tweet — strip the domain, keep only the path.
        import re as _re
        norm_url = self.url
        nitter_match = _re.search(r'nitter\.[^/]+(/[^?#]+)', norm_url)
        if nitter_match:
            norm_url = nitter_match.group(1)  # e.g. /LumaLabsAI/status/123
        # Also strip x.com / twitter.com domain for consistency
        tw_match = _re.search(r'(?:twitter\.com|x\.com)(/[^?#]+)', norm_url)
        if tw_match:
            norm_url = tw_match.group(1)
        return hashlib.sha1(f"{self.creator}|{norm_url}".encode()).hexdigest()


def _rss_urls(handle: str) -> list[str]:
    # Nitter exposes a user's timeline at /<handle>/rss
    return [f"{base}/{handle}/rss" for base in NITTER_MIRRORS]


def _parse_rss(xml_bytes: bytes, creator: str) -> list[RawPost]:
    """Parse a minimal subset of an RSS 2.0 feed.

    We don't use feedparser to keep deps light — rsshub feeds are clean
    enough that stdlib ElementTree handles them.
    """
    posts: list[RawPost] = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        logger.warning(f"[{creator}] RSS parse error: {e}")
        return posts

    channel = root.find("channel")
    if channel is None:
        return posts

    for item in channel.findall("item"):
        link = (item.findtext("link") or "").strip()
        title = (item.findtext("title") or "").strip()
        desc = (item.findtext("description") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()

        # Strip HTML from description — good enough for scoring.
        text = re.sub(r"<[^>]+>", " ", desc)
        text = re.sub(r"\s+", " ", text).strip()

        if not link:
            continue
        posts.append(
            RawPost(
                creator=creator,
                url=link,
                title=title,
                text=text or title,
                published_at=pub,
            )
        )
    return posts


# ---------------------------------------------------------------------------
# YouTube support
# ---------------------------------------------------------------------------

def _load_yt_cache() -> dict:
    try:
        return json.loads(YT_CHANNEL_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_yt_cache(data: dict) -> None:
    try:
        YT_CHANNEL_CACHE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"YT cache save failed: {e}")


def resolve_youtube_channel_id(handle: str) -> str | None:
    """Turn a YouTube @handle into its canonical UC... channel id.

    We scrape the handle page HTML for `"channelId":"UC..."` — faster than
    invoking yt-dlp and avoids a subprocess round trip.  Results are cached
    so we only pay this cost once per handle across restarts.
    """
    cache = _load_yt_cache()
    if handle in cache and cache[handle]:
        return cache[handle]

    try:
        r = requests.get(
            f"https://www.youtube.com/@{handle}",
            timeout=12,
            headers={"User-Agent": "Mozilla/5.0 (compatible; content-bot/1.0)"},
        )
    except requests.RequestException as e:
        logger.warning(f"YT handle resolve failed for @{handle}: {e}")
        return None

    if r.status_code != 200:
        logger.warning(f"YT handle @{handle} HTTP {r.status_code}")
        cache[handle] = None
        _save_yt_cache(cache)
        return None

    m = re.search(r'"channelId":"(UC[\w-]+)"', r.text)
    if not m:
        logger.warning(f"YT handle @{handle}: channelId not found in page HTML")
        cache[handle] = None
        _save_yt_cache(cache)
        return None

    channel_id = m.group(1)
    cache[handle] = channel_id
    _save_yt_cache(cache)
    logger.info(f"YT handle @{handle} → {channel_id}")
    return channel_id


def _parse_youtube_atom(xml_bytes: bytes, creator: str) -> list[RawPost]:
    """YouTube returns an Atom feed, not RSS.  Namespaces make ET ugly so
    we strip them before parsing — easier than threading ns maps through."""
    posts: list[RawPost] = []
    try:
        text = xml_bytes.decode("utf-8", errors="replace")
        # Drop default namespace declarations for simpler XPath-style access.
        text = re.sub(r'\sxmlns="[^"]+"', "", text, count=1)
        root = ET.fromstring(text)
    except ET.ParseError as e:
        logger.warning(f"[{creator}] YT Atom parse error: {e}")
        return posts

    for entry in root.findall("entry"):
        link_el = entry.find("link")
        link = link_el.get("href") if link_el is not None else ""
        title = (entry.findtext("title") or "").strip()
        published = (entry.findtext("published") or "").strip()
        # Description lives under <media:group><media:description> — we
        # stripped the namespace prefix on media:, so the node name is
        # still media:description which ET can't find without the prefix.
        # Fall back: search any descendant text containing the description.
        desc = ""
        for el in entry.iter():
            tag = el.tag.rsplit("}", 1)[-1] if "}" in el.tag else el.tag
            if tag == "description" and el.text:
                desc = el.text.strip()
                break

        if not link:
            continue
        text_body = f"{title}. {desc}".strip()
        posts.append(
            RawPost(
                creator=creator,
                url=link,
                title=title,
                text=text_body,
                published_at=published,
            )
        )
    return posts


def fetch_youtube_channel(yt_handle: str, creator_name: str, timeout: int = 15) -> list[RawPost]:
    channel_id = resolve_youtube_channel_id(yt_handle)
    if not channel_id:
        return []
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "content-bot/1.0"})
    except requests.RequestException as e:
        logger.warning(f"[yt:{yt_handle}] fetch failed: {e}")
        return []
    if r.status_code != 200:
        logger.warning(f"[yt:{yt_handle}] HTTP {r.status_code}")
        return []
    # Tag creator as "yt:handle" so the scorer sees the source type.
    return _parse_youtube_atom(r.content, creator=f"yt:{yt_handle}")


# ---------------------------------------------------------------------------
# X / Nitter fetch
# ---------------------------------------------------------------------------

def fetch_creator(handle: str, timeout: int = 15) -> list[RawPost]:
    """Try each Nitter mirror in order until one returns a parseable feed.

    Mirrors go down all the time, so we swallow network errors and move on
    instead of raising.  Only logs a warning if *every* mirror fails.
    """
    headers = {"User-Agent": "Mozilla/5.0 (compatible; content-bot/1.0)"}
    for url in _rss_urls(handle):
        try:
            r = requests.get(url, timeout=timeout, headers=headers)
        except requests.RequestException as e:
            logger.debug(f"[{handle}] {url} network error: {e}")
            continue
        if r.status_code != 200:
            logger.debug(f"[{handle}] {url} HTTP {r.status_code}")
            continue
        posts = _parse_rss(r.content, creator=handle)
        if posts:
            return posts
        logger.debug(f"[{handle}] {url} returned 200 but 0 items")
    # All Nitter mirrors are down (captcha/502) since mid-April 2026.
    # Log at DEBUG to avoid flooding bot.log with 24 warnings per hour.
    # TODO: get xcancel.com RSS whitelisted (email rss@xcancel.com with
    # server IP) or switch to a paid Twitter API.
    logger.debug(f"[{handle}] all {len(NITTER_MIRRORS)} Nitter mirrors failed")
    return []


def _is_recent(pub_date: str) -> bool:
    """Accept posts younger than POST_MAX_AGE_HOURS.  Parse lenient."""
    if not pub_date:
        return True  # don't drop it just because the feed lacks a date
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
    ):
        try:
            dt = datetime.strptime(pub_date, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt) <= timedelta(hours=POST_MAX_AGE_HOURS)
        except ValueError:
            continue
    return True


# ---------------------------------------------------------------------------
# Claude scoring
# ---------------------------------------------------------------------------

SCORING_PROMPT = """Ты ассистент Артёма — AI-блогера, который делает короткие разборы ТОЛЬКО про действительно НОВЫЕ AI-продукты и модели для русскоязычной аудитории. Артём очень боится быть обманщиком: лучше пропустить десять хороших новостей, чем выпустить один разбор про несуществующий «запуск».

Перед тобой пост англоязычного аккаунта из X/Twitter. Твоя задача — честно оценить, стоит ли делать разбор.

Автор: {creator}

Текст поста (оригинал):
---
{text}
---{linked_block}

КРИТИЧЕСКИЕ ПРАВИЛА (читай внимательно, это самое важное):

1. НЕ ГАЛЛЮЦИНИРУЙ. Никогда не додумывай факты, которых нет в тексте или в расширенном контексте. Если видишь только название и ссылку без описания — это НЕ повод для разбора, это фрагмент треда или реклама. Верни is_launch=false, score=0.

2. ЖЁСТКО ОТКЛОНЯЙ (is_launch=false, score=0, summary_ru=""):
   - Security advisory и уязвимости: «we identified a security issue», «please update your app», «axios vulnerability», «fix», «patch» — это НЕ запуск, это предупреждение.
   - Обновления цен и тарифов: «new $100 tier», «increased usage», «price change», «subscription update» — это pricing, а не новый продукт.
   - Фрагменты треда: текст короче 120 символов, выглядит как «Название: ссылка» или «🔗 Product: URL» — это просто ссылка внутри большого треда, без контекста.
   - Ретвиты, цитаты, благодарности, follow-me призывы («if you enjoyed this thread, follow me»).
   - Обсуждения и мнения: «I think», «my take», «interesting thought», тред-рассуждения про индустрию без конкретного нового продукта.
   - Интеграции, которые расширяют уже существующий продукт новой фичей — это update, а не запуск (например, «X now connects with Y»).
   - Анонсы мероприятий, вебинаров, хакатонов, курсов.
   - Ранние тизеры без демо и без конкретики («coming soon», «stay tuned»).

3. ПРИНИМАЙ (is_launch=true, score ≥ 6) ТОЛЬКО если явно видно:
   - Название нового продукта или модели ("Introducing X", "We're releasing X", "X is now available", "X is out")
   - И есть описание: что умеет, для кого, чем отличается от предыдущего
   - Для score ≥ 8: это крупный игрок (OpenAI, Anthropic, Google/DeepMind, Meta, Runway, ElevenLabs, Midjourney, Luma, Pika, Stability, Suno, xAI) И это реально новый продукт (не обновление цен и не security).

4. ПОЛЕ confidence (0-10) — насколько ты уверен, что это именно новый запуск:
   - 10 = точно новый продукт, всё ясно
   - 5 = похоже на запуск, но контекста маловато
   - 0 = это точно не запуск (security/pricing/тред/ретвит)
   - Если confidence < 7 — ставь is_launch=false и score=0, независимо от всего остального.

5. ПОЛЕ summary_ru: пиши ТОЛЬКО по фактам из поста/контекста. Никогда не додумывай функционал, о котором не сказано прямо. Если не уверен — оставь пустым.

Верни СТРОГО JSON без единого лишнего символа, без markdown-fence:
{{
  "is_launch": true/false,
  "score": 0-10,
  "confidence": 0-10,
  "product": "",
  "company": "",
  "summary_ru": "",
  "reject_reason": ""
}}

Если is_launch=false, заполни reject_reason одним из: "security_advisory", "pricing_update", "thread_fragment", "retweet_or_opinion", "existing_product_update", "event_announcement", "insufficient_context", "other"."""


# Hard-filter: text patterns that are obviously thread fragments and can be
# rejected without calling Claude at all.
_THREAD_FRAGMENT_RE = re.compile(
    r'^\s*(?:🔗|📎|▶|👉)?\s*[^:\n]{1,60}:\s*https?://\S+\s*$',
    re.IGNORECASE,
)

# Phrases that almost always indicate NOT a launch (security, pricing, updates).
_HARD_REJECT_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r'\bsecurity (?:issue|advisory|vulnerability|update|fix|patch)\b',
        r'\baxios\b.*\b(?:vulnerability|issue|library)\b',
        r'\b(?:identified|found|discovered) a (?:security|vulnerability)\b',
        r'\bplease update your\b',
        r'\bupdate your (?:macos|windows|app|version)\b',
        r'\b(?:new|updated) (?:\$\d+|tier|pricing|plan|subscription)\b.*\b(?:month|tier|plan)\b',
    ]
]


def _is_obvious_thread_fragment(text: str) -> bool:
    """Cheap pre-filter: reject obviously-fragmented posts without burning a Claude call."""
    if not text or len(text.strip()) < 30:
        return True
    if _THREAD_FRAGMENT_RE.match(text.strip()):
        return True
    # "🔗 Name: url" pattern — single line, starts with link emoji, has URL
    first_line = text.strip().split("\n", 1)[0]
    if len(first_line) < 80 and first_line.count(":") >= 1 and re.search(r'https?://', first_line):
        words = re.sub(r'https?://\S+', '', first_line).strip()
        if len(words) < 40:  # basically just "Label: url"
            return True
    return False


def _looks_like_hard_reject(text: str) -> str | None:
    """Return the matched phrase if the text is obviously a security/pricing/update post."""
    for pat in _HARD_REJECT_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


def score_with_claude(claude_client, creator: str, text: str) -> dict:
    """Returns {is_launch, score, product, company, summary_ru, confidence, reject_reason}.

    Two-stage pipeline:
      1. Cheap hard filter — reject obvious thread fragments / security / pricing
         without calling Claude at all (saves tokens + can't hallucinate).
      2. Pre-fetch the linked blog/landing page (if the tweet has an outbound
         link) so Claude scores with full article context, not just the 280-char
         blurb.  This prevents the #1 failure mode where Claude invents a
         product launch from "🔗 Name: url".
      3. Claude scoring with strict prompt + confidence floor.

    On any failure returns a safe zero-score result.
    """
    empty = {"is_launch": False, "score": 0, "product": "", "company": "",
             "summary_ru": "", "confidence": 0, "reject_reason": ""}

    # --- Stage 1: hard filters ---
    if _is_obvious_thread_fragment(text):
        logger.info(f"[{creator}] hard-reject: thread fragment — {text[:80]!r}")
        return {**empty, "reject_reason": "thread_fragment"}

    hard_match = _looks_like_hard_reject(text)
    if hard_match:
        logger.info(f"[{creator}] hard-reject: {hard_match!r} — {text[:80]!r}")
        return {**empty, "reject_reason": "security_or_pricing"}

    # --- Stage 2: pre-fetch linked article context (if short post + has link) ---
    linked_block = ""
    try:
        if len(text) < 400 and re.search(r'https?://', text):
            ctx = fetch_linked_content(text, timeout=10, max_hops=1)
            if ctx.get("article_text"):
                linked_block = (
                    f"\n\nРасширенный контекст (с блога/лендинга, куда ведёт ссылка "
                    f"{ctx.get('source_page') or ''}):\n{ctx['article_text'][:2500]}"
                )
    except Exception as e:
        logger.debug(f"[{creator}] pre-fetch for scoring failed: {e}")

    # --- Stage 3: Claude scoring ---
    try:
        resp = claude_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": SCORING_PROMPT.format(
                    creator=creator, text=text[:3000], linked_block=linked_block
                ),
            }],
        )
        raw = resp.content[0].text.strip()
        # Claude sometimes wraps JSON in ```json fences — strip them.
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        data = json.loads(raw)
        confidence = int(data.get("confidence") or 0)
        is_launch = bool(data.get("is_launch"))
        score = int(data.get("score") or 0)
        # Confidence floor: if Claude isn't ≥7 sure, we don't queue it.
        if confidence < 7:
            is_launch = False
            score = 0
        return {
            "is_launch": is_launch,
            "score": score,
            "confidence": confidence,
            "product": (data.get("product") or "").strip(),
            "company": (data.get("company") or "").strip(),
            "summary_ru": (data.get("summary_ru") or "").strip(),
            "reject_reason": (data.get("reject_reason") or "").strip(),
        }
    except Exception as e:
        logger.warning(f"Claude scoring failed for {creator}: {e}")
        return empty


# ---------------------------------------------------------------------------
# Poll cycle — called hourly from bot.py job queue
# ---------------------------------------------------------------------------

def _iter_sources():
    """Yield (fetch_fn, handle_for_db, display_name) for every source.

    Keeps poll_once agnostic to what's an X creator vs an X official
    account vs a YouTube channel — they all speak RawPost.
    """
    for c in CREATORS:
        yield (lambda h=c["handle"]: fetch_creator(h), c["handle"], c["name"])
    for o in OFFICIAL_ACCOUNTS:
        yield (lambda h=o["handle"]: fetch_creator(h), o["handle"], o["name"])
    for y in YOUTUBE_CREATORS:
        yield (
            lambda h=y["yt_handle"], n=y["name"]: fetch_youtube_channel(h, n),
            f"yt:{y['yt_handle']}",
            y["name"],
        )


def poll_once(claude_client) -> dict:
    """Fetch every source (X creators + X official + YouTube), score new
    posts, store them.

    Returns a stats dict suitable for logging:
        {"fetched": N, "new": N, "scored": N, "queued": N}
    """
    init_db()
    stats = {"fetched": 0, "new": 0, "scored": 0, "queued": 0}

    with _db() as conn:
        for fetch_fn, db_handle, display_name in _iter_sources():
            try:
                posts = fetch_fn()
            except Exception as e:
                logger.warning(f"[{db_handle}] fetch raised: {e}")
                continue
            stats["fetched"] += len(posts)

            for p in posts:
                if not _is_recent(p.published_at):
                    continue
                cur = conn.execute("SELECT 1 FROM seen WHERE id=?", (p.id,))
                if cur.fetchone():
                    continue
                stats["new"] += 1

                score_data = score_with_claude(claude_client, display_name, p.text)
                stats["scored"] += 1

                is_launch = score_data["is_launch"] and score_data["score"] >= SCORE_THRESHOLD
                status = "queued" if is_launch else "skipped"

                # Product-level dedup: if the same product was already queued/approved
                # within the last 7 days, don't queue it again.  This catches the case
                # where multiple creators tweet about the same launch, or when Nitter
                # mirror rotation used to produce different IDs for the same tweet.
                if status == "queued" and score_data.get("product"):
                    import re as _re_dedup
                    norm_product = _re_dedup.sub(
                        r"[^a-z0-9а-яё]+", "", (score_data["product"] or "").lower()
                    )
                    if norm_product and len(norm_product) > 2:
                        dup = conn.execute(
                            """SELECT 1 FROM seen
                               WHERE status IN ('queued','approved','drafted')
                                 AND fetched_at > datetime('now','-7 days')
                                 AND lower(replace(replace(replace(product,' ',''),'-',''),'.','')) = ?
                               LIMIT 1""",
                            (norm_product,),
                        ).fetchone()
                        if dup:
                            status = "skipped"
                            logger.info(
                                f"[{db_handle}] product-dedup: '{score_data['product']}' "
                                f"already queued/approved in last 7d, skipping"
                            )

                # Title/text-similarity dedup (added 6 May 2026 to fix repeating launches).
                # Catches cases where Claude extracts different `product` strings for the
                # same news, OR when summary_ru wording differs.  Compares title+first 200
                # chars of text against queued/approved/drafted items in last 5 days using
                # difflib SequenceMatcher.  Threshold 0.78 chosen empirically — captures
                # rewordings without false-positives on truly distinct launches.
                if status == "queued":
                    import difflib as _difflib
                    needle = ((p.title or "")[:80] + " " + (p.text or "")[:200]).lower().strip()
                    if needle and len(needle) > 30:
                        recent = conn.execute(
                            """SELECT title, text FROM seen
                               WHERE status IN ('queued','approved','drafted')
                                 AND fetched_at > datetime('now','-5 days')
                               ORDER BY fetched_at DESC LIMIT 100"""
                        ).fetchall()
                        for prev in recent:
                            haystack = ((prev["title"] or "")[:80] + " " + (prev["text"] or "")[:200]).lower().strip()
                            if not haystack or len(haystack) < 30:
                                continue
                            ratio = _difflib.SequenceMatcher(None, needle, haystack).ratio()
                            if ratio >= 0.78:
                                status = "skipped"
                                logger.info(
                                    f"[{db_handle}] text-sim-dedup: ratio={ratio:.2f} "
                                    f"vs. earlier post; skipping '{(p.title or '')[:60]}'"
                                )
                                break

                if status == "queued":
                    stats["queued"] += 1

                conn.execute(
                    """INSERT INTO seen
                       (id, creator, url, title, text, published_at, fetched_at,
                        score, product, company, summary_ru, status)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        p.id, db_handle, p.url, p.title, p.text, p.published_at,
                        datetime.now(timezone.utc).isoformat(),
                        score_data["score"], score_data["product"],
                        score_data["company"], score_data["summary_ru"], status,
                    ),
                )
        conn.commit()

    logger.info(f"Launch monitor poll: {stats}")
    return stats


# ---------------------------------------------------------------------------
# Source media download (B-roll harvest)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Outbound link chasing: tweets often point to a blog post or landing page
# that contains the *real* goods — embedded YouTube demo, detailed feature
# list, quotes, benchmarks.  We follow the first external link in the tweet
# text and pull both the readable article text (for Claude's script prompt)
# and any embedded video URLs (for yt-dlp).
# ---------------------------------------------------------------------------

_OUTBOUND_URL_RE = re.compile(r'https?://[^\s<>"\'\)\]]+', re.IGNORECASE)
# Bare URLs (no protocol) — things like "goo.gle/41IC3lY", "chatgpt.com/download/",
# "bit.ly/abc".  Must have a TLD and at least one path character to avoid matching
# sentences like "We released this.today" — hence the required slash.
_BARE_URL_RE = re.compile(
    r'(?<![@\w/])((?:[a-z0-9][a-z0-9-]{0,30}\.){1,4}[a-z]{2,10}/[^\s<>"\'\)\]]+)',
    re.IGNORECASE,
)
_BLOCKED_HOSTS = ("twitter.com", "x.com", "nitter.", "t.co", "pic.twitter.com")
_YOUTUBE_ID_RE = re.compile(
    r'(?:https?:)?(?://)?(?:www\.|m\.)?(?:youtube\.com/(?:watch\?v=|embed/|v/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})'
)
_VIMEO_RE = re.compile(r'https?://(?:www\.|player\.)?vimeo\.com/(?:video/)?(\d+)')

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
}


def _extract_outbound_urls(text: str) -> list[str]:
    """Extract outbound URLs from a tweet — both protocol-prefixed and bare
    domain/path forms (goo.gle/xxx, chatgpt.com/download/, etc.).  Tweets
    often use shortener URLs without https:// so we have to handle both.
    """
    urls: list[str] = []
    seen: set[str] = set()
    text = text or ""

    def _accept(u: str) -> None:
        u = u.rstrip('.,;:!?)\'"<>»')
        host = re.sub(r'^https?://', '', u).split('/', 1)[0].lower()
        if not host:
            return
        # Match full host or subdomain, NOT substring — "t.co" must not block
        # "chatgpt.com" just because "t.co" is a substring of it.
        for b in _BLOCKED_HOSTS:
            if b.endswith("."):  # prefix marker like "nitter." -> nitter.net
                if host.startswith(b):
                    return
            elif host == b or host.endswith("." + b):
                return
        if u in seen:
            return
        seen.add(u)
        urls.append(u)

    # 1) Full URLs with protocol
    for raw in _OUTBOUND_URL_RE.findall(text):
        _accept(raw)
    # 2) Bare URLs (goo.gle/xyz etc.) — prepend https://
    # Strip already-matched full URLs first so we don't double-match them.
    stripped = _OUTBOUND_URL_RE.sub(" ", text)
    for raw in _BARE_URL_RE.findall(stripped):
        _accept("https://" + raw)
    return urls


def _extract_meta(html: str, prop: str) -> str:
    pat = re.escape(prop)
    m = re.search(
        rf'<meta[^>]+(?:property|name)=["\']{pat}["\'][^>]+content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if m:
        return m.group(1)
    m = re.search(
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{pat}["\']',
        html, re.IGNORECASE,
    )
    return m.group(1) if m else ""


def _strip_html(html: str) -> str:
    html = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<nav[^>]*>.*?</nav>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<footer[^>]*>.*?</footer>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<[^>]+>', ' ', html)
    # Decode the most common HTML entities (avoid pulling in a parser lib).
    html = (html.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&#39;", "'"))
    return re.sub(r'\s+', ' ', html).strip()


_DEEP_LINK_KEYWORDS = (
    "desktop", "feature", "learn-more", "learn_more", "learn more",
    "watch", "demo", "product", "introducing", "tour", "video",
    "подробнее", "смотреть", "возможности", "о приложении",
)


def _find_deep_link_candidates(html: str, base_url: str, limit: int = 5) -> list[str]:
    """Find promising same-host links for a second fetch hop — things like
    'Подробнее', 'Features', 'Watch demo', etc.  Used when the first-hop
    landing page doesn't have an embedded video but probably points to one.
    """
    try:
        from urllib.parse import urljoin, urlparse
    except Exception:
        return []
    base_host = urlparse(base_url).netloc.lower()
    candidates: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        html, re.IGNORECASE | re.DOTALL,
    ):
        href = m.group(1)
        anchor = re.sub(r'<[^>]+>', '', m.group(2)).strip().lower()
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        try:
            full = urljoin(base_url, href)
        except Exception:
            continue
        host = urlparse(full).netloc.lower()
        # Allow same host OR a subdomain of the same registered domain.
        if host != base_host and not host.endswith("." + base_host.split(".", 1)[-1]):
            continue
        key = full.split("#", 1)[0]
        if key in seen or key == base_url:
            continue
        haystack = (anchor + " " + full).lower()
        if not any(kw in haystack for kw in _DEEP_LINK_KEYWORDS):
            continue
        seen.add(key)
        candidates.append(key)
        if len(candidates) >= limit:
            break
    return candidates


def fetch_linked_content(tweet_text: str, timeout: int = 15, max_hops: int = 2) -> dict:
    """Follow outbound links from a tweet (up to ``max_hops`` deep) and
    extract article text + embedded video URLs.

    Used to enrich launch breakdowns: the tweet is usually a 280-char blurb,
    but the linked blog post has the full story and a demo video we can
    download as B-roll.  If the first landing page has no video, we look for
    internal links like 'features', 'demo', 'watch', 'подробнее' and follow
    the top few — that's where the demo video typically lives (e.g. the
    chatgpt.com/download page points to chatgpt.com/features/desktop which
    has the actual product tour video).

    Returns::

        {
            "article_text": str,         # first ~4000 chars, cleaned
            "media_urls":   list[str],   # YouTube / Vimeo URLs found
            "page_urls":    list[str],   # additional visited pages (yt-dlp fallback)
            "source_page":  str,         # final URL of the first hop
        }

    Best-effort: empty fields on failure.  Never raises.
    """
    result: dict = {
        "article_text": "",
        "media_urls": [],
        "page_urls": [],
        "source_page": None,
    }
    for outbound in _extract_outbound_urls(tweet_text):
        visited: set[str] = set()
        article_text_parts: list[str] = []
        media_urls_accum: list[str] = []
        page_urls_accum: list[str] = []
        first_page_url: str | None = None

        queue: list[tuple[str, int]] = [(outbound, 0)]
        while queue:
            url, depth = queue.pop(0)
            if url in visited or depth >= max_hops:
                continue
            visited.add(url)
            try:
                r = requests.get(
                    url, timeout=timeout,
                    headers=_BROWSER_HEADERS,
                    allow_redirects=True,
                )
                if r.status_code != 200 or not r.text:
                    continue
                final_url = r.url
                visited.add(final_url)
                if first_page_url is None:
                    first_page_url = final_url
                html = r.text

                # --- Extract media URLs (YouTube, Vimeo) ---
                yt_ids = list(dict.fromkeys(_YOUTUBE_ID_RE.findall(html)))
                vimeo_ids = list(dict.fromkeys(_VIMEO_RE.findall(html)))
                for vid in yt_ids[:5]:
                    mu = f"https://www.youtube.com/watch?v={vid}"
                    if mu not in media_urls_accum:
                        media_urls_accum.append(mu)
                for vid in vimeo_ids[:5]:
                    mu = f"https://vimeo.com/{vid}"
                    if mu not in media_urls_accum:
                        media_urls_accum.append(mu)

                # --- Extract article text (only on first hop — the announcement
                # page is usually richest; deeper pages are product tours) ---
                if depth == 0:
                    og_title = (_extract_meta(html, "og:title")
                                or _extract_meta(html, "twitter:title"))
                    og_desc = (_extract_meta(html, "og:description")
                               or _extract_meta(html, "twitter:description")
                               or _extract_meta(html, "description"))
                    body_text = _strip_html(html)[:3500]
                    parts = []
                    if og_title:
                        parts.append(f"Заголовок статьи: {og_title}")
                    if og_desc:
                        parts.append(f"Описание (og): {og_desc}")
                    if body_text:
                        parts.append(f"Основной текст:\n{body_text}")
                    if parts:
                        article_text_parts.extend(parts)

                # --- Deep hop: if still no media, queue promising internal links ---
                if not media_urls_accum and depth + 1 < max_hops:
                    for candidate in _find_deep_link_candidates(html, final_url):
                        if candidate not in visited:
                            queue.append((candidate, depth + 1))
                            logger.info(f"Deep hop candidate: {candidate}")

                # Track visited pages as yt-dlp fallbacks (generic extractor can
                # grab HTML5 <video> / HLS streams from many product pages).
                if depth >= 1 and final_url not in page_urls_accum:
                    page_urls_accum.append(final_url)
            except Exception as e:
                logger.debug(f"fetch_linked_content hop failed for {url}: {e}")
                continue

        if article_text_parts or media_urls_accum or page_urls_accum:
            article_text = "\n\n".join(article_text_parts)[:4000]
            result["article_text"] = article_text
            result["media_urls"] = media_urls_accum
            result["page_urls"] = page_urls_accum
            result["source_page"] = first_page_url
            logger.info(
                f"fetch_linked_content: {first_page_url} → "
                f"{len(article_text)} chars, {len(media_urls_accum)} media, "
                f"{len(page_urls_accum)} fallback pages, visited {len(visited)}"
            )
            return result
    return result


def _native_source_url(url: str) -> str:
    """Rewrite a Nitter URL to the native x.com form that yt-dlp understands.

    Nitter URLs look like https://nitter.net/<user>/status/<id>#m — we
    strip the fragment and swap the host.  YouTube URLs pass through as-is.
    """
    if "nitter" in url:
        m = re.match(r"https?://[^/]*nitter[^/]*/([^/]+)/status/(\d+)", url)
        if m:
            return f"https://x.com/{m.group(1)}/status/{m.group(2)}"
    return url.split("#", 1)[0]


def download_source_media(source_url: str, dest_path: Path, timeout: int = 120) -> Path | None:
    """Download the media attached to an X/YouTube URL via yt-dlp.

    Returns the final path on success, None if nothing downloadable was
    found (e.g. text-only tweet).  dest_path is a target file WITHOUT an
    extension — yt-dlp picks the extension itself and we locate the result
    afterwards.

    This is called from bot.py after a launch is approved.  Blocking —
    wrap in asyncio.to_thread at the call site.
    """
    import subprocess
    import sys
    import shutil as _shutil
    import tempfile

    native = _native_source_url(source_url)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    # yt-dlp output template: <dest>.<ext>
    output_template = str(dest_path) + ".%(ext)s"

    # Prefer the yt-dlp that ships alongside the current Python interpreter
    # (i.e. the venv), then fall back to PATH.  On the production server
    # yt-dlp is installed via `venv/bin/pip install yt-dlp` so the binary
    # lives next to python3.
    venv_bin = Path(sys.executable).parent / ("yt-dlp.exe" if os.name == "nt" else "yt-dlp")
    yt_dlp_bin = str(venv_bin) if venv_bin.exists() else (_shutil.which("yt-dlp") or "yt-dlp")

    cmd = [
        yt_dlp_bin,
        "--quiet",
        "--no-warnings",
        "--no-playlist",
        "-f", "best[ext=mp4]/best",
        "-o", output_template,
    ]
    # Webshare residential proxy (when WEBSHARE_API_KEY is set).
    # YouTube blocks Hetzner datacenter IPs on popular videos; routing through
    # a residential IP (real ISP) bypasses that. Falls back to direct
    # connection if env var is empty — legacy behavior.
    try:
        from webshare_proxy import get_random_proxy
        proxy_url = get_random_proxy()
        if proxy_url:
            cmd += ["--proxy", proxy_url]
            _safe = proxy_url.split("@", 1)[-1] if "@" in proxy_url else proxy_url
            logger.info(f"[yt-dlp] using webshare proxy {_safe}")
    except Exception as e:
        logger.warning(f"[yt-dlp] webshare proxy unavailable: {e}")

    # YouTube-specific bypass: cookies file + EJS challenge solver from GitHub.
    # Hetzner datacenter IPs are blocked by "Sign in to confirm you're not a
    # bot"; cookies exported from Artem's Chrome + the GitHub-hosted JS solver
    # (executed via Deno, installed system-wide) let yt-dlp pull real formats.
    #
    # CRITICAL: yt-dlp REWRITES the cookies file on exit with a slimmed-down
    # version that strips most auth cookies.  Running twice destroys the file.
    # Workaround: copy to a throwaway temp path for each invocation, let yt-dlp
    # mutate the copy, discard afterwards.  The pristine master stays intact.
    cookies_master = paths.YOUTUBE_COOKIES
    cookies_tmp: Path | None = None
    if cookies_master.exists() and cookies_master.stat().st_size > 500:
        fd, tmp_path = tempfile.mkstemp(suffix=".txt", prefix="yt_cookies_")
        os.close(fd)
        cookies_tmp = Path(tmp_path)
        _shutil.copyfile(cookies_master, cookies_tmp)
        cmd += ["--cookies", str(cookies_tmp)]
    cmd += [
        "--remote-components", "ejs:github",
        native,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        logger.error("yt-dlp not installed on this host")
        return None
    except subprocess.TimeoutExpired:
        logger.warning(f"yt-dlp timed out on {native}")
        return None
    finally:
        if cookies_tmp and cookies_tmp.exists():
            try:
                cookies_tmp.unlink()
            except OSError:
                pass

    if result.returncode != 0:
        stderr = (result.stderr or "").strip().splitlines()[-1:] if result.stderr else []
        logger.warning(f"yt-dlp failed on {native}: {' | '.join(stderr)}")
        return None

    # Find whatever file yt-dlp produced at dest_path.*
    parent = dest_path.parent
    stem = dest_path.name
    candidates = sorted(parent.glob(f"{stem}.*"))
    if not candidates:
        logger.warning(f"yt-dlp succeeded but no file found at {dest_path}.*")
        return None
    return candidates[0]


# ---------------------------------------------------------------------------
# Digest — called once a day from bot.py job queue at 10:00 MSK
# ---------------------------------------------------------------------------

@dataclass
class DigestItem:
    id: str                 # full sha1 id of the *top-scoring* row in the group
    creator: str            # representative creator (first seen)
    url: str                # representative url (first seen)
    product: str
    company: str
    summary_ru: str
    score: int
    creators: list[str]     # all creators who mentioned this launch
    urls: list[str]         # all source URLs in this group
    group_key: str          # normalized "company|product" — used for cross-row ops
    mention_count: int      # how many rows were collapsed into this group
    published_at: str = ""  # ISO date of the source post (latest in group)


def _group_key(company: str, product: str) -> str:
    """Normalize a (company, product) pair into a dedup key.

    The primary key is *product* alone — because "Seedance 2.0" is the same
    news whether it's distributed by Freepik, Lovart, or Higgsfield.  All
    five mentions should collapse into one digest item.

    Lower-case + strip punctuation so that 'Seedance 2.0' / 'seedance-2.0'
    both map the same.  If the scorer didn't extract a product, we fall
    back to the company name so totally-product-less items don't collapse
    with each other indiscriminately.
    """
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9а-яё]+", "", (s or "").lower())

    p = norm(product)
    if p:
        return f"p|{p}"
    c = norm(company)
    if c:
        return f"c|{c}"
    return ""


def get_pending_digest(limit: int = MAX_DIGEST_ITEMS) -> list[DigestItem]:
    """Return grouped digest items: rows sharing the same (company, product)
    are collapsed into a single DigestItem with all sources attached.

    This is what makes a "Seedance 2.0 announced by 5 creators" show up once
    rather than five times.  Items without a recognizable product name
    (group_key == '') are kept ungrouped so we don't accidentally merge
    unrelated posts.
    """
    init_db()
    with _db() as conn:
        rows = conn.execute(
            """SELECT id, creator, url, product, company, summary_ru, score, published_at
               FROM seen
               WHERE status='queued'
               ORDER BY score DESC, fetched_at DESC"""
        ).fetchall()

    groups: dict[str, DigestItem] = {}
    singles: list[DigestItem] = []
    for r in rows:
        d = dict(r)
        key = _group_key(d["company"], d["product"])
        item = DigestItem(
            id=d["id"],
            creator=d["creator"],
            url=d["url"],
            product=d["product"],
            company=d["company"],
            summary_ru=d["summary_ru"],
            score=d["score"],
            creators=[d["creator"]],
            urls=[d["url"]],
            group_key=key,
            mention_count=1,
            published_at=d.get("published_at") or "",
        )
        if not key:
            singles.append(item)
            continue
        if key in groups:
            g = groups[key]
            if d["creator"] not in g.creators:
                g.creators.append(d["creator"])
            g.urls.append(d["url"])
            g.mention_count += 1
            # Prefer the longest summary — usually the most informative one.
            if len(d["summary_ru"] or "") > len(g.summary_ru or ""):
                g.summary_ru = d["summary_ru"]
            # Keep the highest score.
            if d["score"] > g.score:
                g.score = d["score"]
            # Keep the most recent published_at.
            if (d.get("published_at") or "") > (g.published_at or ""):
                g.published_at = d.get("published_at") or g.published_at
        else:
            groups[key] = item

    combined = list(groups.values()) + singles
    combined.sort(key=lambda x: (-x.score, -x.mention_count))
    return combined[:limit]


def mark_status(item_id: str, status: str) -> None:
    """status ∈ {approved, skipped, drafted}.  Marks only the single row."""
    with _db() as conn:
        conn.execute("UPDATE seen SET status=? WHERE id=?", (status, item_id))
        conn.commit()


def mark_group_status(item_id: str, status: str) -> int:
    """Mark every row sharing (company, product) with the given item.

    Returns the number of rows touched.  Used when Artem approves or skips
    a *grouped* digest item — all five Seedance mentions should flip
    together, not one at a time.
    """
    with _db() as conn:
        row = conn.execute(
            "SELECT company, product FROM seen WHERE id=?", (item_id,)
        ).fetchone()
        if not row:
            return 0
        key = _group_key(row["company"], row["product"])
        if not key:
            # No usable group key — fall back to updating just this row.
            conn.execute("UPDATE seen SET status=? WHERE id=?", (status, item_id))
            conn.commit()
            return 1
        # Gather all matching ids by re-computing the key per row.  We keep
        # this in Python because SQLite can't run our normalization easily.
        all_rows = conn.execute(
            "SELECT id, company, product FROM seen WHERE status='queued'"
        ).fetchall()
        ids = [r["id"] for r in all_rows if _group_key(r["company"], r["product"]) == key]
        if not ids:
            ids = [item_id]
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE seen SET status=? WHERE id IN ({placeholders})",
            [status, *ids],
        )
        conn.commit()
        return len(ids)


def get_item(item_id: str) -> dict | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM seen WHERE id=?", (item_id,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# CLI helpers (manual testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Lightweight manual test — poll one creator and print results.
    import sys
    handle = sys.argv[1] if len(sys.argv) > 1 else "minchoi"
    posts = fetch_creator(handle)
    print(f"Fetched {len(posts)} posts from @{handle}")
    for p in posts[:5]:
        print(f"  - {p.published_at} | {p.title[:80]}")
