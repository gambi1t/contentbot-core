"""Тест прогресс-уведомлений HF-генерации (10 июня).

Боевой прогон «Себестоимость»: 22 минуты без единого апдейта (2 fix-round'а)
— Артём решил «не сделался». Фикс: generate_hyperframes_broll(progress_cb)
шлёт фазовые апдейты (раскадровка → сцены → рендер → доводка N/2), бот
мостит их в edit_message_text через run_coroutine_threadsafe.

Запуск: python tests/test_hf_progress.py
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import hyperframes_broll as hb  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []

    print("\n[_notify — fire-and-forget]")
    got = []
    hb._notify(got.append, "тест")
    _assert(got == ["тест"], "вызывает cb с текстом", errors)
    hb._notify(None, "тест")  # не падает без cb
    _assert(True, "None-cb безопасен", errors)

    def boom(_):
        raise RuntimeError("у эдита истерика")
    try:
        hb._notify(boom, "тест")
        _assert(True, "сбой cb НЕ валит пайплайн", errors)
    except Exception:
        _assert(False, "сбой cb НЕ валит пайплайн", errors)

    print("\n[generate_hyperframes_broll — принимает progress_cb]")
    sig = inspect.signature(hb.generate_hyperframes_broll)
    _assert("progress_cb" in sig.parameters, "параметр progress_cb есть", errors)
    _assert(sig.parameters["progress_cb"].default is None,
            "progress_cb опционален (default None)", errors)

    print("\n[фазовые вызовы в оркестраторе]")
    src = inspect.getsource(hb.generate_hyperframes_broll)
    n_calls = src.count("_notify(progress_cb")
    _assert(n_calls >= 4,
            f"≥4 фазовых уведомлений (раскадровка/сцены/рендер/доводка), got {n_calls}",
            errors)
    _assert("Доводка" in src and "MAX_FIX_ROUNDS" in src.split("Доводка")[1][:200],
            "уведомление доводки содержит счётчик N/MAX", errors)

    print("\n[bot.py — мост в edit_message_text]")
    bot_src = Path(__file__).parent.parent.joinpath("bot.py").read_text(
        encoding="utf-8", errors="replace")
    _assert("run_coroutine_threadsafe" in bot_src,
            "thread-safe мост (run_coroutine_threadsafe)", errors)
    _assert("_hf_progress,\n" in bot_src or "_hf_progress)" in bot_src
            or "_hf_progress," in bot_src,
            "хэндлер передаёт _hf_progress в generate", errors)
    _assert("до ~25" in bot_src, "честная вилка времени в стартовом сообщении", errors)

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
