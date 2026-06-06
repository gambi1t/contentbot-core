"""Pure helpers extracted from bot.py publish-flow.

- needs_description / build_ig_caption — guard before IG/YT/VK publish so
  empty description doesn't fall through to a raw script_text[:500] caption.
- extract_script_text / extract_video_topic — inputs for
  tg_post_writer.rewrite_for_telegram() built from a card's data dict.

Kept dependency-free so tests/test_publish_helpers.py runs without importing
bot.py (which loads heavy modules).
"""
from __future__ import annotations


def needs_description(data: dict) -> bool:
    """True iff the card has no usable description for publish caption."""
    raw = (data or {}).get("description", "")
    return not str(raw).strip()


def build_ig_caption(
    card_title: str,
    description: str,
    script_text: str,
    ai_disclosure: str,
) -> str:
    """Build IG/YT/VK caption with priority: description → script[:500] → title only.

    Mirrors prior inline logic in bot.py:15400-15402, but treats
    whitespace-only description as missing.
    """
    desc = (description or "").strip()
    script = (script_text or "").strip()

    if desc:
        body = f"{card_title}\n\n{desc}"
    elif script:
        body = f"{card_title}\n\n{script[:500]}"
    else:
        body = card_title

    return body + (ai_disclosure or "")


def extract_script_text(data: dict) -> str:
    """Pull the most authoritative script text from a card's data dict.

    Order: data['script'] (single string) → data['voice_parts'] (list of
    strings joined). Whitespace-only → empty. Stripped.
    """
    if not data:
        return ""

    s = str(data.get("script", "") or "").strip()
    if s:
        return s

    parts = data.get("voice_parts") or []
    if isinstance(parts, (list, tuple)):
        joined = " ".join(str(p).strip() for p in parts if str(p).strip())
        if joined:
            return joined

    return ""


def extract_video_topic(data: dict, card_title: str) -> str:
    """Topic = card_title if present, else first ~120 chars of script."""
    title = (card_title or "").strip()
    if title:
        return title

    script = extract_script_text(data or {})
    if not script:
        return ""

    # First sentence-ish chunk, bounded at 120 chars.
    head = script[:120].strip()
    return head
