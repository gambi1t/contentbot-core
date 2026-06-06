"""TDD tests for publish_helpers — pure functions extracted from bot.py
publish-flow. Cover (1) description-guard before IG/YT/VK publish, and
(2) inputs for tg_post_writer.rewrite_for_telegram() from a card.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from publish_helpers import (
    needs_description,
    build_ig_caption,
    extract_script_text,
    extract_video_topic,
)

AI_DISCLOSURE_TEST = "\n\n—\nAI-tools used."


# ── needs_description ────────────────────────────────────────────────────────

def test_needs_description_when_missing_key():
    assert needs_description({}) is True


def test_needs_description_when_empty_string():
    assert needs_description({"description": ""}) is True


def test_needs_description_when_whitespace_only():
    assert needs_description({"description": "   \n\t  "}) is True


def test_needs_description_when_filled():
    assert needs_description({"description": "Короткое описание поста."}) is False


def test_needs_description_when_single_char():
    # Even a single non-whitespace char counts as present — author's choice.
    assert needs_description({"description": "x"}) is False


# ── build_ig_caption ─────────────────────────────────────────────────────────

def test_build_ig_caption_uses_description_when_present():
    out = build_ig_caption(
        card_title="Заголовок",
        description="Это нормальное описание.",
        script_text="Сценарий, который НЕ должен попасть.",
        ai_disclosure=AI_DISCLOSURE_TEST,
    )
    assert out.startswith("Заголовок\n\nЭто нормальное описание.")
    assert out.endswith(AI_DISCLOSURE_TEST)
    assert "Сценарий, который НЕ должен попасть" not in out


def test_build_ig_caption_falls_back_to_script_when_no_description():
    out = build_ig_caption(
        card_title="Заголовок",
        description="",
        script_text="Полный сценарий" + " AAA" * 200,  # > 500 chars
        ai_disclosure=AI_DISCLOSURE_TEST,
    )
    assert out.startswith("Заголовок\n\nПолный сценарий")
    # Script must be truncated at 500 chars.
    body_without_disclosure = out[: -len(AI_DISCLOSURE_TEST)]
    script_part = body_without_disclosure.split("\n\n", 1)[1]
    assert len(script_part) == 500


def test_build_ig_caption_title_only_when_nothing_else():
    out = build_ig_caption(
        card_title="Только заголовок",
        description="",
        script_text="",
        ai_disclosure=AI_DISCLOSURE_TEST,
    )
    assert out == "Только заголовок" + AI_DISCLOSURE_TEST


def test_build_ig_caption_treats_whitespace_description_as_missing():
    out = build_ig_caption(
        card_title="Title",
        description="   \n  ",
        script_text="Use this instead.",
        ai_disclosure=AI_DISCLOSURE_TEST,
    )
    assert "Use this instead" in out
    # Whitespace-only description must not leak in.
    assert "   \n  " not in out


# ── extract_script_text ──────────────────────────────────────────────────────

def test_extract_script_text_prefers_data_script():
    data = {"script": "Текст сценария.", "voice_parts": ["Часть 1.", "Часть 2."]}
    assert extract_script_text(data) == "Текст сценария."


def test_extract_script_text_falls_back_to_voice_parts():
    data = {"voice_parts": ["Часть 1.", "Часть 2.", "Часть 3."]}
    out = extract_script_text(data)
    assert "Часть 1." in out
    assert "Часть 2." in out
    assert "Часть 3." in out


def test_extract_script_text_empty_when_no_data():
    assert extract_script_text({}) == ""


def test_extract_script_text_empty_when_script_is_whitespace_and_no_voice_parts():
    assert extract_script_text({"script": "   "}) == ""


def test_extract_script_text_strips_leading_trailing_whitespace():
    assert extract_script_text({"script": "  hello  "}) == "hello"


# ── extract_video_topic ──────────────────────────────────────────────────────

def test_extract_video_topic_prefers_card_title():
    out = extract_video_topic({"script": "Длинный сценарий..."}, card_title="Лучший заголовок")
    assert out == "Лучший заголовок"


def test_extract_video_topic_falls_back_to_script_head_when_no_title():
    long_script = "Первая фраза сценария. " + "А дальше много текста. " * 50
    out = extract_video_topic({"script": long_script}, card_title="")
    assert out.startswith("Первая фраза сценария")
    assert len(out) <= 120  # bounded


def test_extract_video_topic_empty_when_nothing():
    assert extract_video_topic({}, card_title="") == ""


def test_extract_video_topic_strips_card_title():
    assert extract_video_topic({}, card_title="  Trimmed  ") == "Trimmed"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
