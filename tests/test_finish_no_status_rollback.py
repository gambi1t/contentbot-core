"""TDD: кнопка «✅ Готово» (finish) НЕ откатывает Notion-статус в «Подбор скринкаст».

Баг (canary 25 июня): готовый ролик /ready → finalize ставит «Готово к публикации»,
но нажатие «Готово» (finish, ~35 экранов) откатывало статус в середину канбана
«Подбор скринкаст» (легаси-хардкод). Правильные переходы уже есть: finalize →
«Готово к публикации», crosspost_go → «Опубликовано». Фикс: finish не трогает статус.

Запуск: python tests/test_finish_no_status_rollback.py
"""
from __future__ import annotations

import sys
from pathlib import Path


def _assert(cond: bool, msg: str, errors: list) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(f"FAIL {msg}")


def _finish_block(src: str) -> str:
    idx = src.find('if query.data == "finish":')
    nxt = src.find('if query.data ==', idx + 20)
    return src[idx: nxt if nxt != -1 else idx + 2500]


def main() -> int:
    errors: list = []
    src = (Path(__file__).parent.parent / "bot.py").read_text(encoding="utf-8")

    print("\n-- finish-хендлер больше не откатывает статус --")
    block = _finish_block(src)
    _assert(block, "блок finish найден", errors)
    _assert("Подбор скринкаст" not in block,
            "finish НЕ ставит «Подбор скринкаст» (откат убран)", errors)
    _assert("update_notion_status" not in block,
            "finish вообще не трогает статус Notion", errors)

    print("\n-- правильные переходы статуса на месте --")
    _assert('"Готово к публикации"' in src or "Готово к публикации" in src,
            "finalize ставит «Готово к публикации»", errors)
    _assert("Опубликовано" in src, "crosspost ставит «Опубликовано»", errors)

    print("\n" + (f"FAIL ({len(errors)})" if errors else "OK finish-status tests passed"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
