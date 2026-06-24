"""TDD: парсер гайда «Вставить свой текст» — код-блоки, ##/###, таблицы.

Кейс Артёма: гайд route-following (Seedance/Kling) с ПРОМПТАМИ в ```text … ```,
заголовками ##/### и таблицей. Старый create_guide_page_from_raw распознавал
только `# `, `-/•`, `1.` → промпты рвались по пустым строкам (split на блоки),
``` оставались текстом, ##/### и таблицы — абзацы с решётками/палками.
Подписчик не мог скопировать промпт.

Стало: _build_guide_blocks(raw) (чистая, без Notion) — код-блоки целиком
(rich_text-чанки ≤2000), ##/### → heading_3, # → heading_2, таблицы | a | b |
→ table block, списки/абзацы как были.

Запуск: python tests/test_guide_parser.py
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


def _assert(cond: bool, msg: str, errors: list) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(f"FAIL {msg}")


FENCE = "```"
SAMPLE = (
    "## Заголовок раздела\n\n"
    "Абзац текста.\n\n"
    "### Промпт для Seedance\n\n"
    f"{FENCE}text\n"
    "Промпт строка один\n\n"
    "Промпт строка два после пустой\n"
    f"{FENCE}\n\n"
    "- буллет один\n"
    "- буллет два\n\n"
    "| Событие | Маркер |\n"
    "|---|---|\n"
    "| Старт | у реки |\n"
    "| Финал | у храма |\n"
)


def _all_text(blocks) -> str:
    out = []
    for b in blocks:
        body = b.get(b["type"], {})
        for rt in body.get("rich_text", []):
            out.append(rt.get("text", {}).get("content", ""))
    return "\n".join(out)


def test_code_block_whole(errors):
    print("\n-- код-блок целиком (промпт не порван, без литеральных ```) --")
    blocks = bot._build_guide_blocks(SAMPLE)
    code = [b for b in blocks if b["type"] == "code"]
    _assert(len(code) == 1, f"ровно 1 code-блок (got {len(code)})", errors)
    if not code:
        return
    txt = "".join(rt["text"]["content"] for rt in code[0]["code"]["rich_text"])
    _assert("Промпт строка один" in txt and "Промпт строка два после пустой" in txt,
            "промпт целиком (пустая строка внутри сохранена)", errors)
    _assert(FENCE not in txt, "в код-блоке нет литеральных ```", errors)


def test_headings_h3(errors):
    print("\n-- ## и ### → heading_3, # → heading_2 --")
    blocks = bot._build_guide_blocks(SAMPLE)
    types = [b["type"] for b in blocks]
    _assert("heading_3" in types, "## / ### → heading_3", errors)


def test_table_block(errors):
    print("\n-- таблица | a | b | → table block (не абзац с палками) --")
    blocks = bot._build_guide_blocks(SAMPLE)
    _assert(any(b["type"] == "table" for b in blocks), "есть table-блок", errors)


def test_no_literal_markup_in_text(errors):
    print("\n-- ни ```, ни «## », ни «|---|» не утекли в текст абзацев --")
    blocks = bot._build_guide_blocks(SAMPLE)
    # текст НЕ-кодовых блоков
    txt = _all_text([b for b in blocks if b["type"] != "code"])
    _assert(FENCE not in txt, "нет ``` в тексте", errors)
    _assert("## " not in txt, "нет «## » в тексте", errors)
    _assert("|---|" not in txt, "нет разделителя таблицы в тексте", errors)


def main() -> int:
    errors: list = []
    for fn in (test_code_block_whole, test_headings_h3, test_table_block,
               test_no_literal_markup_in_text):
        fn(errors)
    print("\n" + (f"FAIL ({len(errors)})" if errors else "OK all guide-parser tests passed"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
