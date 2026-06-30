"""TDD (live-test 30.06): cover_pick:0 → «Ошибка: 'card_data'» (KeyError).

Трейс с сервера: cover_pick → `card_data = data["card_data"]` → KeyError('card_data').
Корень: карточка открыта через notion_card: (P2b гидрирует script+notion_page_id+
notion_edit_title, но НЕ card_data). avatar_confirm генерит тексты обложки (script
теперь есть), пользователь жмёт вариант → cover_pick читает data['card_data'] по
индексу → падает. Мой P2b ОТКРЫЛ этот путь (раньше падало раньше на «нет сценария»).

Фикс (surgical): cover_pick и cover_confirm читают card_data/script через .get с
фолбэком на notion_edit_title — без KeyError, без clobber-риска от установки
card_data в notion_card:.

Запуск: python tests/test_cover_card_data_keyerror.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
os.environ.setdefault("TELEGRAM_TOKEN", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import bot  # noqa: E402


def _assert(cond: bool, msg: str, errors: list) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(f"FAIL {msg}")


def test_cover_card_data_defensive(errors):
    print("\n-- cover_pick/cover_confirm: card_data через .get, без KeyError --")
    src = Path(bot.__file__).read_text(encoding="utf-8")
    _assert('card_data = data["card_data"]' not in src,
            "нет индексного `card_data = data[\"card_data\"]` (KeyError 'card_data' убран)", errors)
    _assert(src.count('data.get("card_data") or {"title": data.get("notion_edit_title"') >= 2,
            "оба cover-доступа card_data: .get + фолбэк на notion_edit_title", errors)
    # script в cover-ветках тоже через .get (рядом с card_data-фолбэком) —
    # проверяем по соседству, не глобально (есть легитимный data['script'] в TTS).
    cp = src.find('card_data = data.get("card_data") or {"title": data.get("notion_edit_title"')
    if cp != -1:
        region = src[cp: cp + 200]
        _assert('script_text = data.get("script"' in region,
                "script в cover читается через .get (рядом с card_data)", errors)


def main() -> int:
    errors: list = []
    test_cover_card_data_defensive(errors)
    print("\n" + (f"FAIL ({len(errors)})" if errors else "OK cover card_data KeyError test passed"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
