"""Centralized pending storage — фикс F1 (26 May 2026, ChatGPT review C1).

Раньше `pending` объявлялся в `bot.py`, и `carousel/handlers.py` делал
late-import `bot` внутри функции `_pending_io`. Проблема в том что systemd
запускает сервис как `python bot.py` → главный модуль живёт под именем
`__main__`. Когда `carousel/handlers.py` вызывает `import bot`, Python
грузит файл `bot.py` ВТОРОЙ РАЗ под именем `bot` — со своим экземпляром
`pending` и всех top-level глобалов.

Симптом который реально наблюдался в проде: `logger.info` карусели
из `carousel.llm` НЕ попадали в `journalctl`, хотя другие логи шли —
потому что logger в втором instance модуля не сконфигурирован тем же
handler'ом. Это и был «второй pending», только с logger.

Решение: вынести pending в отдельный модуль `bot_state.py`, который
импортируется ОДИН РАЗ и в `bot.py` (как `from bot_state import …`), и в
`carousel/handlers.py`. Python кэширует модуль в `sys.modules`, повторных
загрузок нет.

Также: `save_pending` теперь атомарный (tempfile + os.replace) — раньше
прямой write_text мог потерять весь pending при crash во время записи.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

PENDING_FILE = Path(__file__).parent / "pending.json"
PROJECTS_DIR = Path(__file__).parent / "projects"
_TMP_SUFFIX = ".tmp"


def project_dir(data: dict) -> Path | None:
    """Папка проекта для карточки по pending-data (или dict с notion_page_id).

    Вынесено из bot.py чтобы carousel/handlers.py не делал `import bot` —
    та же причина что для pending (см. шапку модуля).

    Fallback на `notion_edit_card`/`notion_edit_title` (поля режима
    редактирования карточки): без них «Скачать материалы» в edit-режиме
    резолвил None и отдавал только обложку, хотя проект существует
    (порт B2 из legacy, грабли content-bot 18 июня).
    """
    notion_id = data.get("notion_page_id") or data.get("notion_edit_card")
    if not notion_id:
        return None
    title = (
        (data.get("card_data") or {}).get("title")
        or data.get("notion_edit_title")
        or "untitled"
    )
    safe_title = re.sub(r'[<>:"/\\|?*]', '', title)[:60].strip()
    folder_name = f"{notion_id[:8]}_{safe_title}"
    d = PROJECTS_DIR / folder_name
    d.mkdir(parents=True, exist_ok=True)
    return d


FINISHED_FLAG = "finished.flag"


def mark_finished(data: dict) -> None:
    """Пометить проект как ГОТОВЫЙ ролик (загружен извне через /ready).

    Кросспост не должен резать «CTA-хвост» (_trim_cta_from_video) у внешне
    смонтированного ролика — у него нет устного CTA. Маркер durable (переживает
    перезапуск бота и переоткрытие карточки на следующий день), чего in-memory
    флага `selfie_finished` не хватает. No-op если проект ещё не создан.
    """
    proj = project_dir(data)
    if proj:
        try:
            (proj / FINISHED_FLAG).write_text("1", encoding="utf-8")
        except OSError:
            pass


def is_finished_project(data: dict) -> bool:
    """True если ролик загружен как готовый: in-memory флаг ИЛИ маркер в проекте."""
    if data.get("selfie_finished"):
        return True
    proj = project_dir(data)
    return bool(proj and (proj / FINISHED_FLAG).exists())


def _load_pending() -> dict:
    if PENDING_FILE.exists():
        try:
            raw = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
            return {int(k): v for k, v in raw.items()}
        except Exception:
            return {}
    return {}


def save_pending(data: dict | None = None) -> None:
    """Atomic save: tempfile + os.replace.

    `data=None` → сохраняет глобальный `pending` (рекомендованный вызов).
    `data=<dict>` → принимает явный dict (обратная совместимость со старым
    `_save_pending(pending)` в bot.py).
    """
    payload = data if data is not None else pending
    tmp_path = PENDING_FILE.with_suffix(PENDING_FILE.suffix + _TMP_SUFFIX)
    # P0 #3: durable atomic — пишем во временный, flush+fsync (данные на диске),
    # затем os.replace (атомарен на всех ОС). Рестарт в момент записи больше не
    # оставит битый pending.json (CTO-ревью).
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(str(tmp_path), str(PENDING_FILE))


# Глобальный dict — мутируется in-place и bot.py, и carousel/handlers.py.
# Это безопасно т.к. PTB single-threaded async (без GIL race на dict mutation).
pending: dict[int, dict] = _load_pending()


__all__ = ["pending", "save_pending", "PENDING_FILE", "PROJECTS_DIR", "project_dir",
           "FINISHED_FLAG", "mark_finished", "is_finished_project"]
