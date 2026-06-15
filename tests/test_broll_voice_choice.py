"""TDD: выбор голоса озвучки B-roll + дедуп HF-превью (15 июня).

Развилка озвучки: ИИ-клон Максима / свой записанный голос. HF-превью больше
не повторяет сценарий (он на шаге источника). Чистые куски тут; флоу — Telethon.

Запуск: python tests/test_broll_voice_choice.py
"""
from __future__ import annotations
import os, sys
from pathlib import Path
os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
sys.path.insert(0, str(Path(__file__).parent.parent))

from broll.handlers import _voice_choice_keyboard, _hf_preview_text  # noqa: E402


def _assert(cond, msg, errors):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        errors.append(msg)


def main():
    errors = []
    print("\n[_voice_choice_keyboard — две опции озвучки + отмена]")
    kb = _voice_choice_keyboard()
    flat = [b for row in kb.inline_keyboard for b in row]
    cbs = [b.callback_data for b in flat]
    texts = " ".join(b.text for b in flat)
    _assert("b2vc:ai" in cbs, "есть кнопка ИИ-клон (b2vc:ai)", errors)
    _assert("b2vc:own" in cbs, "есть кнопка свой голос (b2vc:own)", errors)
    _assert("broll_cancel" in cbs, "есть отмена", errors)
    _assert("ИИ" in texts and ("сам" in texts or "свой" in texts),
            "подписи понятны (ИИ-клон / свой)", errors)

    print("\n[_hf_preview_text — БЕЗ повтора сценария, с числом сцен]")
    long_script = "Спортзал снимает стресс? Не всегда. " * 10
    txt = _hf_preview_text(6)
    _assert("6" in txt, "указано число сцен", errors)
    _assert(long_script.strip() not in txt, "полный сценарий НЕ повторяется (дедуп)", errors)
    _assert("Собрать ролик" in txt and "Перегенерировать" in txt,
            "упомянуты обе кнопки", errors)

    print()
    if errors:
        print(f"❌ FAIL — {len(errors)}:")
        for e in errors: print("   -", e)
        return 1
    print("✅ ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
