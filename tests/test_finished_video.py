"""Unit tests for the "finished video" feature (/ready) — ядро.

Артём загружает УЖЕ смонтированный ролик (с вшитыми субтитрами) и хочет сразу
название/описание/обложку/кросспостинг, пропустив всю продакшн-обработку selfie
(правка текста, монтаж, прожиг субтитров, музыка, b-roll).

Закрепляем чистую логику двух новых хелперов:
- selfie.handlers.build_finished_pending() — pending-состояние готового ролика:
  сразу к обложке (selfie_cover_picking), видео = финал (selfie_subtitled/final),
  транскрипт для названия/описания, НЕ в selfie_text_review.
- bot_state.is_finished_project() / mark_finished() — durable-маркер, чтобы
  кросспост НЕ резал «CTA-хвост» (_trim_cta_from_video) у внешне смонтированного
  ролика — у него нет устного CTA, иначе молча режется 4с контента. Маркер
  переживает перезапуск/переоткрытие карточки (in-memory флага мало).

telegram/selfie-сабмодули мокаем — тяжёлые импорты (faster_whisper, ffmpeg) в dev
могут отсутствовать. Логика хелперов чистая.
Run: python tests/test_finished_video.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

# Тяжёлые/опциональные импорты handlers.py мокаем ДО импорта (как test_selfie_intake).
for _m in (
    "telegram", "telegram.ext",
    "selfie.broll_picker", "selfie.cover", "selfie.music",
    "selfie.edit", "selfie.transcribe",
):
    sys.modules.setdefault(_m, MagicMock())

sys.path.insert(0, str(Path(__file__).parent.parent))

import bot_state  # noqa: E402
from selfie import handlers as sh  # noqa: E402


def _assert(cond: bool, msg: str, errors: list) -> None:
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(f"FAIL {msg}")


# ── build_finished_pending ───────────────────────────────────────────────────

def test_finished_pending_state_to_cover(errors):
    print("\n-- finished pending: state → selfie_cover_picking (НЕ text_review) --")
    p = sh.build_finished_pending(Path("/tmp/x"), Path("/tmp/x/source.mp4"),
                                  [{"word": "привет", "start": 0.0, "end": 0.5}], "привет мир")
    _assert(p["state"] == "selfie_cover_picking", f"state={p['state']!r}", errors)
    _assert(p["state"] != "selfie_text_review", "НЕ уходит в правку текста", errors)


def test_finished_pending_video_is_final(errors):
    print("\n-- finished pending: готовый ролик = финал (subtitled/final = source) --")
    src = Path("/tmp/x/source.mp4")
    p = sh.build_finished_pending(Path("/tmp/x"), src, [], "t")
    _assert(p["selfie_subtitled"] == str(src), "selfie_subtitled=source", errors)
    _assert(p["selfie_final"] == str(src), "selfie_final=source", errors)
    _assert(p["selfie_source"] == str(src), "selfie_source=source", errors)


def test_finished_pending_flag_and_transcript(errors):
    print("\n-- finished pending: флаг + транскрипт/слова для названия/описания --")
    words = [{"word": "раз", "start": 0.0, "end": 0.3}]
    p = sh.build_finished_pending(Path("/tmp/x"), Path("/tmp/x/source.mp4"), words, "раз два")
    _assert(p.get("selfie_finished") is True, "selfie_finished=True", errors)
    _assert(p["selfie_transcript"] == "раз два", "transcript carried", errors)
    _assert(p["selfie_orig_transcript"] == "раз два", "orig transcript carried", errors)
    _assert(p["selfie_words"] == words, "words carried", errors)


def test_finished_pending_cover_keys(errors):
    print("\n-- finished pending: ключи для cover-picker готовы --")
    tmp = Path("/tmp/abc")
    p = sh.build_finished_pending(tmp, tmp / "source.mp4", [], "t")
    _assert(isinstance(p.get("selfie_cover_shown_lib_ids"), list), "selfie_cover_shown_lib_ids=[]", errors)
    _assert("selfie_music_note" in p, "selfie_music_note задан (для финального сообщения)", errors)
    _assert(p["selfie_tmp_dir"] == str(tmp), "selfie_tmp_dir set", errors)


# ── bot_state: durable finished marker ───────────────────────────────────────

def test_is_finished_inmemory_flag(errors):
    print("\n-- is_finished_project: in-memory флаг → True (без проекта) --")
    _assert(bot_state.is_finished_project({"selfie_finished": True}) is True, "flag → True", errors)


def test_is_finished_empty_false(errors):
    print("\n-- is_finished_project: пусто → False --")
    _assert(bot_state.is_finished_project({}) is False, "{} → False", errors)


def test_mark_then_is_finished_marker(errors):
    print("\n-- mark_finished → is_finished_project видит маркер (durable) --")
    with tempfile.TemporaryDirectory() as td:
        orig = bot_state.PROJECTS_DIR
        bot_state.PROJECTS_DIR = Path(td)
        try:
            data = {"notion_page_id": "abcd1234ef", "card_data": {"title": "Готовый ролик"}}
            _assert(bot_state.is_finished_project(data) is False, "до маркера → False", errors)
            bot_state.mark_finished(data)
            _assert(bot_state.is_finished_project(data) is True, "после mark_finished → True", errors)
            proj = bot_state.project_dir(data)
            _assert((proj / bot_state.FINISHED_FLAG).exists(), "finished.flag записан в проект", errors)
        finally:
            bot_state.PROJECTS_DIR = orig


def test_mark_finished_no_project_safe(errors):
    print("\n-- mark_finished без notion_page_id: не падает --")
    try:
        bot_state.mark_finished({})  # project_dir → None
        _assert(True, "no-op без проекта", errors)
    except Exception as e:
        _assert(False, f"бросил {e}", errors)


def test_finalize_with_cover_preserves_flag(errors):
    print("\n-- _finalize_with_cover: проносит selfie_finished (rebuild его терял) --")
    import asyncio
    captured = {}

    async def _title_picker(mq, ctx, uid, transcript, first):
        captured["pending"] = dict(sh._PENDING[uid])

    pend = {}
    sh.init(pending=pend, save_pending=lambda *_a, **_k: None,
            assets_dir=Path("."), logger=MagicMock(),
            selfie_finalize=None, title_picker=_title_picker, cover_text_step=None)
    uid = 777
    pend[uid] = sh.build_finished_pending(Path("/tmp/x"), Path("/tmp/x/source.mp4"), [], "тема")
    asyncio.run(sh._finalize_with_cover(MagicMock(), MagicMock(), uid,
                                        Path("/tmp/x/cover.jpg"), "кадр"))
    cap = captured.get("pending", {})
    _assert(cap.get("selfie_finished") is True, "selfie_finished дошёл до waiting_title", errors)
    _assert(cap.get("state") == "selfie_waiting_title", "state=selfie_waiting_title", errors)


def main() -> int:
    print("=" * 60 + "\nfinished video (/ready): pending + durable marker\n" + "=" * 60)
    errors: list = []
    for fn in (
        test_finished_pending_state_to_cover, test_finished_pending_video_is_final,
        test_finished_pending_flag_and_transcript, test_finished_pending_cover_keys,
        test_is_finished_inmemory_flag, test_is_finished_empty_false,
        test_mark_then_is_finished_marker, test_mark_finished_no_project_safe,
        test_finalize_with_cover_preserves_flag,
    ):
        fn(errors)
    print("\n" + "=" * 60)
    print(f"FAIL ({len(errors)})" if errors else "OK all finished-video tests passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
