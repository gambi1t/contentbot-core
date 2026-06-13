"""Меню источников видеоряда + фоллбэк-логика Pipeline 2 (13 июня).

CTO-ревью:
- Q1: плоское меню с time-labels (решение Артёма) — честно про время режима.
- Callback несёт draft_id → stale-guard по status (не полный CAS, ужато под
  1 клиента).
- Q7: матрица фоллбэков. HF-only fail НЕ собирать молча из живых.
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .draft import SourceMode

# Подписи кнопок с честным временем (CTO-ревью 4.6).
_MODE_LABELS = {
    SourceMode.AUTO:    "🤖 Авто из библиотеки — быстро (3-6 мин)",
    SourceMode.MANUAL:  "👆 Выбрать вручную — точнее",
    SourceMode.UPLOAD:  "📤 Загрузить свои фото/видео",
    SourceMode.HF_ONLY: "🎨 Только графика — дольше (10-25 мин)",
    SourceMode.AUTO_HF: "🔀 Авто + графика — баланс (8-20 мин)",
}
_MODE_ORDER = (SourceMode.AUTO, SourceMode.MANUAL, SourceMode.UPLOAD,
               SourceMode.HF_ONLY, SourceMode.AUTO_HF)

_CB_PREFIX = "b2src"


def source_menu_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    """Плоское меню источников. Каждый callback несёт draft_id."""
    rows = [
        [InlineKeyboardButton(_MODE_LABELS[m], callback_data=f"{_CB_PREFIX}:{m}:{draft_id}")]
        for m in _MODE_ORDER
    ]
    rows.append([InlineKeyboardButton(
        "❌ Отмена", callback_data=f"{_CB_PREFIX}:cancel:{draft_id}")])
    return InlineKeyboardMarkup(rows)


def parse_source_cb(data: str) -> tuple[str | None, str | None]:
    """'b2src:<mode>:<draft_id>' → (mode, draft_id) или (None, None)."""
    parts = (data or "").split(":", 2)
    if len(parts) != 3 or parts[0] != _CB_PREFIX:
        return None, None
    mode, draft_id = parts[1], parts[2]
    if mode != "cancel" and mode not in SourceMode.ALL:
        return None, None
    return mode, draft_id


def hf_fallback_action(source_mode: str, hf_ok_count: int,
                       live_available: bool, min_partial: int = 3) -> str:
    """Что делать, если HF-генерация не дала полный результат (CTO-ревью Q7).

    Возвращает:
      - "proceed_partial" — собрать из того, что сгенерилось (≥min_partial);
      - "offer_choice"    — спросить юзера (hf_only fail: не молчать);
      - "live_only"       — тихо собрать из живых (auto_hf: графика опц.);
      - "fail"            — нечего собирать.
    """
    if hf_ok_count >= min_partial:
        return "proceed_partial"
    if source_mode == SourceMode.HF_ONLY:
        # юзер выбрал именно графику — не подменять молча
        return "offer_choice" if live_available else "fail"
    if source_mode == SourceMode.AUTO_HF:
        return "live_only" if live_available else "fail"
    return "fail"
