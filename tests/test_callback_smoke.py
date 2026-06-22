"""Callback-smoke харнес (v1: selfie-ветка) — CTO-рекомендация против «мёртвых
кнопок» (исключение ПОСЛЕ query.answer() = кнопка молча падает, как баг
bool.strip 22.06). Дёргает КАЖДЫЙ selfie-callback с типовым состоянием +
мокнутыми тяжёлыми внешними (генераторы/ffmpeg/Notion/LLM) и проверяет:
  • handler НЕ бросает необработанное исключение;
  • handler ответил пользователю (answer / edit / send).

Без денег, без реального Telegram, без faster-whisper — годен для CI на деплое.
НЕ покрывает (нужны отдельные fault-injection/E2E, см. CTO-ревью): реальную
сборку (asm), прожиг, реальные генерации, рестарт-recovery, частичные сбои.

Run: python tests/test_callback_smoke.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# Тяжёлые/внешние модули мокаем ДО импорта handlers. broll_picker/cover/music —
# реальные (чистые билдеры клавиатур/сообщений).
_HEAVY = ("telegram", "telegram.ext", "subtitle_burner", "hyperframes_broll",
          "ai_video_broll", "auto_broll", "music_mixer", "video_assembler",
          "bot", "selfie.transcribe", "selfie.edit", "selfie.punch_in")
for _m in _HEAVY:
    sys.modules.setdefault(_m, MagicMock())

sys.path.insert(0, str(Path(__file__).parent.parent))

from selfie import handlers as sh  # noqa: E402

# Генераторы возвращают (clips, cost) → пустой список → ветка «не удалось» отвечает.
sys.modules["auto_broll"].generate_auto_broll = MagicMock(return_value=([], 0.0))
sys.modules["hyperframes_broll"].generate_hyperframes_broll = MagicMock(return_value=([], 0.0))
sys.modules["ai_video_broll"].generate_ai_broll = MagicMock(return_value=([], 0.0))
sys.modules["ai_video_broll"].MIN_CLIPS = 1
sys.modules["ai_video_broll"].estimate_cost_range = MagicMock(return_value=(1.0, 2.0))


def _assert(cond: bool, msg: str, errors: list) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def _make(data: str, uid: int = 777, chat_id: int = 777):
    q = SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=uid),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
    )
    q.message = SimpleNamespace(chat_id=chat_id, reply_text=AsyncMock(), edit_text=AsyncMock())
    upd = SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=uid))
    ctx = SimpleNamespace(bot=AsyncMock())
    return upd, ctx, q


def _responded(q, ctx) -> bool:
    return (q.answer.called or q.edit_message_text.called
            or q.message.reply_text.called or ctx.bot.send_message.called)


def _tmpdir() -> str:
    d = Path(tempfile.mkdtemp(prefix="smoke_selfie_"))
    (d / "source.mp4").write_bytes(b"x")
    (d / "subtitled.mp4").write_bytes(b"x")
    return str(d)


def _base_state(state: str) -> dict:
    tmp = _tmpdir()
    return {
        "state": state,
        "selfie_tmp_dir": tmp,
        "selfie_source": str(Path(tmp) / "source.mp4"),
        "selfie_subtitled": str(Path(tmp) / "subtitled.mp4"),
        "selfie_final": str(Path(tmp) / "subtitled.mp4"),
        "selfie_transcript": "это тестовый сценарий про ИИ. второе предложение.",
        "selfie_orig_transcript": "это тестовый сценарий про ИИ. второе предложение.",
        "selfie_edited": True,  # bool — воспроизводит класс bug bool.strip
        "selfie_words": [{"word": "это", "start": 0.0, "end": 0.3}],
        "selfie_broll_items": [],
        "selfie_broll_shown_ids": {},
    }


# (handler, callback_data, state_for_that_handler). asm/реальная сборка исключены.
def _cases():
    return [
        (sh.handle_text_review_callback, "selfie_text:ok", "selfie_text_review"),
        (sh.handle_text_review_callback, "selfie_text:confirm", "selfie_text_review"),
        (sh.handle_text_review_callback, "selfie_text:edit", "selfie_text_review"),
        (sh.handle_text_review_callback, "selfie_text:cancel_edit", "selfie_text_review"),
        (sh.handle_broll_callback, "selfie_broll:add", "selfie_broll_offer"),
        (sh.handle_broll_callback, "selfie_broll:skip", "selfie_broll_offer"),
        (sh.handle_broll_callback, "selfie_broll:cancel", "selfie_broll_picking"),
        (sh.handle_broll_callback, "selfie_broll:lib_photo", "selfie_broll_picking"),
        (sh.handle_broll_callback, "selfie_broll:lib_clip", "selfie_broll_picking"),
        (sh.handle_broll_callback, "selfie_broll:upload_photo", "selfie_broll_picking"),
        (sh.handle_broll_callback, "selfie_broll:upload_video", "selfie_broll_picking"),
        (sh.handle_broll_callback, "selfie_broll:gen", "selfie_broll_picking"),
        (sh.handle_broll_callback, "selfie_broll:hf", "selfie_broll_picking"),
        (sh.handle_broll_callback, "selfie_broll:aivideo", "selfie_broll_picking"),
        (sh.handle_broll_callback, "selfie_broll:back", "selfie_broll_picking"),
        (sh.handle_broll_callback, "selfie_broll:done", "selfie_broll_picking"),
        (sh.handle_music_callback, "selfie_music:skip", "selfie_music_picking"),
        (sh.handle_music_callback, "selfie_music:back", "selfie_music_picking"),
        (sh.handle_cover_callback, "selfie_cover:library", "selfie_cover_picking"),
        (sh.handle_cover_callback, "selfie_cover:skip", "selfie_cover_picking"),
        # Устаревшая сессия (pending пуст) — НЕ должно крашить, должно ответить.
        (sh.handle_broll_callback, "selfie_broll:hf", None),
        (sh.handle_cover_callback, "selfie_cover:frame:start", None),
    ]


def main() -> int:
    print("=" * 60 + "\ncallback-smoke (selfie) — dead-button guard\n" + "=" * 60)
    errors: list = []
    pending: dict = {}
    sh.init(pending=pending, save_pending=lambda *_a, **_k: None,
            assets_dir=Path(tempfile.gettempdir()), logger=MagicMock(),
            selfie_finalize=AsyncMock(), title_picker=AsyncMock(),
            cover_text_step=AsyncMock(), draft_card=AsyncMock())

    for handler, data, state in _cases():
        uid = 777
        pending.clear()
        if state is not None:
            pending[uid] = _base_state(state)
        upd, ctx, q = _make(data, uid)
        label = f"{handler.__name__}({data}{' /stale' if state is None else ''})"
        try:
            asyncio.run(handler(upd, ctx))
            ok = _responded(q, ctx)
            _assert(ok, f"{label} — ответил без краша" if ok else f"{label} — НЕ ответил", errors)
        except Exception as e:
            import traceback
            traceback.print_exc()
            _assert(False, f"{label} — КРАШ: {type(e).__name__}: {e}", errors)

    print("\n" + "=" * 60)
    print(f"FAIL ({len(errors)}):\n  " + "\n  ".join(errors) if errors
          else "OK all callback-smoke cases passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
