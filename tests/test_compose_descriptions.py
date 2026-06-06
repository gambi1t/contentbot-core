"""Регресс-тест экстракции генератора описания (10 июня).

gen_description вынес ядро в bot._compose_publication_descriptions(script_text).
Тут проверяем парсинг (CTA-строка + 3 варианта через ---, чистка «Вариант N:»)
с МОК-claude (без реального API — детерминированно, без токенов).

Запуск: python tests/test_compose_descriptions.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import bot  # noqa: E402


class _FakeContent:
    def __init__(self, text):
        self.text = text


class _FakeResp:
    def __init__(self, text):
        self.content = [_FakeContent(text)]


class _FakeClaude:
    def __init__(self, text):
        self._text = text
        self.last_system = None
        self.last_messages = None

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, model=None, max_tokens=None, system=None, messages=None):
            self._outer.last_system = system
            self._outer.last_messages = messages
            return _FakeResp(self._outer._text)

    @property
    def messages(self):
        return _FakeClaude._Messages(self)


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg)
        print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def test_parsing(errors):
    print("\n[_compose_publication_descriptions — парсинг 3 вариантов]")
    canned = (
        "CTA: Подпишись на канал @yumsunov_realbiz\n"
        "---\n"
        "Вариант 1: Хук про маму и ИИ.\nКонтекст полезный.\n\nПодпишись на канал @yumsunov_realbiz 👇\n"
        "---\n"
        "Другой угол — провокация.\nЧем полезно.\n\nПодпишись на канал @yumsunov_realbiz 👇\n"
        "---\n"
        "Третий угол — инсайт.\nРаскрытие.\n\nПодпишись на канал @yumsunov_realbiz 👇"
    )
    orig = bot.claude
    fake = _FakeClaude(canned)
    bot.claude = fake
    try:
        variants, cta = bot._compose_publication_descriptions("Тестовый сценарий про ИИ")
    finally:
        bot.claude = orig

    _assert(len(variants) == 3, f"3 варианта распарсены, got {len(variants)}", errors)
    _assert("Подпишись на канал" in cta, f"CTA извлечён, got {cta!r}", errors)
    _assert(not any(v.lower().startswith("вариант") for v in variants),
            "префикс «Вариант N:» убран", errors)
    _assert(all("Подпишись на канал" in v for v in variants),
            "каждый вариант содержит CTA", errors)
    # system prompt должен был содержать бренд-инструкции (no-hashtag rule)
    _assert(fake.last_system and "Хештеги" in fake.last_system,
            "system-промпт содержит бренд-правила (no-hashtag)", errors)
    # CTA для соцсетей — «ссылка в шапке профиля», не голый Telegram-хэндл
    _assert(fake.last_system and "шапке профиля" in fake.last_system,
            "system-промпт направляет CTA в шапку профиля (Telegram не кликабелен на IG)", errors)


def test_fallback_no_separators(errors):
    print("\n[fallback — ответ без --- → весь текст одним вариантом]")
    orig = bot.claude
    bot.claude = _FakeClaude("Просто текст без разделителей и CTA-строки.")
    try:
        variants, cta = bot._compose_publication_descriptions("сценарий")
    finally:
        bot.claude = orig
    _assert(len(variants) >= 1, f"≥1 вариант (fallback на raw), got {len(variants)}", errors)


def main():
    errors = []
    test_parsing(errors)
    test_fallback_no_separators(errors)
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
