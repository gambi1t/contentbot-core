"""Тест is_video_message (8 июня) — приём видео по video/mime/РАСШИРЕНИЮ.

Telegram Web шлёт .MOV как документ с ненадёжным mime → ловим по расширению,
иначе бот молча дропает видео после /selfie (баг на IMG_1566.MOV).

Запуск: python tests/test_selfie_video_detect.py
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

from selfie.handlers import is_video_message  # noqa: E402


def _msg(video=None, document=None):
    return SimpleNamespace(video=video, document=document)


def _doc(mime=None, name=None):
    return SimpleNamespace(mime_type=mime, file_name=name)


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []
    print("\n[is_video_message]")
    _assert(is_video_message(_msg(video=object())), "нативное video → True", errors)
    _assert(is_video_message(_msg(document=_doc("video/quicktime", "x.mov"))),
            "документ mime video/quicktime → True", errors)
    _assert(is_video_message(_msg(document=_doc("application/octet-stream", "IMG_1566.MOV"))),
            "документ octet-stream + .MOV (по расширению) → True", errors)
    _assert(is_video_message(_msg(document=_doc(None, "clip.mp4"))),
            "документ без mime + .mp4 → True", errors)
    _assert(is_video_message(_msg(document=_doc("video/x-matroska", "v.mkv"))),
            "документ mkv video-mime → True", errors)
    _assert(not is_video_message(_msg(document=_doc("application/pdf", "doc.pdf"))),
            "PDF-документ → False", errors)
    _assert(not is_video_message(_msg(document=_doc(None, "photo.jpg"))),
            "jpg-документ → False", errors)
    _assert(not is_video_message(_msg()), "ни video, ни document → False", errors)

    print()
    if errors:
        print(f"❌ FAIL — {len(errors)}:")
        for e in errors:
            print(f"   - {e}")
        return 1
    print("✅ ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
