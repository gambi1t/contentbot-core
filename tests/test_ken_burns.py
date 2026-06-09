"""Тест Кен Бёрнса (Подход 1, 8 июня): easing + диагональ.

Было: линейный зум + пан по одной оси («просто приближение»). Стало:
smoothstep ease-in-out + диагональные варианты (оживление). Проверяем строку
фильтра + РЕАЛЬНЫЙ ffmpeg-рендер (валидность выражения).

Запуск: python tests/test_ken_burns.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import video_assembler as va  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []
    N = 90  # 3 сек @ 30fps

    print("\n[easing — smoothstep, кадр-based, не линейный]")
    f = va._ken_burns_filter(N, "zoom_in_br")
    _assert("3-2*" in f, "содержит smoothstep (3-2*t)", errors)
    _assert("on/" in f, "прогресс по номеру кадра (on/N)", errors)
    _assert("min(zoom+" not in f, "НЕ старый линейный зум (min(zoom+step))", errors)
    _assert("zoompan=" in f and "z='1+" in f, "eased zoom z=1+range*E", errors)

    print("\n[диагональ — x И y двигаются (zoom_in_br ↘)]")
    # для br: dx>0, dy>0 → в x и y есть множитель *E и сдвиг окна
    _assert("(iw-iw/zoom)" in f and "(ih-ih/zoom)" in f, "пан по обеим осям", errors)
    fx_center = va._ken_burns_filter(N, "zoom_in")
    _assert(f != fx_center, "диагональ ≠ центрированный зум", errors)

    print("\n[детерминизм]")
    _assert(va._ken_burns_filter(N, "zoom_in_br") == f, "одинаковый вход → одинаковая строка", errors)

    print("\n[shoes-варианты НЕ тронуты]")
    fs = va._ken_burns_filter(N, "zoom_in_shoes")
    _assert("min(zoom+" in fs, "zoom_in_shoes остался линейным (не задет)", errors)
    fsf = va._ken_burns_filter(N, "zoom_in_shoes_full")
    _assert("ih-ih/zoom" in fsf, "zoom_in_shoes_full — bottom-anchor (не задет)", errors)

    print("\n[список вариантов по умолчанию содержит диагонали]")
    import inspect
    src = inspect.getsource(va._build_ken_burns_clips)
    _assert("zoom_in_br" in src and "zoom_in_tl" in src, "default-ротация включает диагонали", errors)

    print("\n[РЕАЛЬНЫЙ ffmpeg-рендер — выражение валидно]")
    tmp = Path(tempfile.mkdtemp())
    img = tmp / "test.png"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=1:duration=1",
         "-frames:v", "1", str(img)], capture_output=True, timeout=30)
    if img.exists():
        for v in ("zoom_in_br", "zoom_in", "zoom_in_left"):
            out = tmp / f"kb_{v}.mp4"
            vf = va._ken_burns_filter(N, v)
            r = subprocess.run(
                ["ffmpeg", "-y", "-loop", "1", "-i", str(img), "-t", "3",
                 "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
                capture_output=True, text=True, timeout=60)
            ok = out.exists() and out.stat().st_size > 1000
            _assert(ok, f"ffmpeg отрендерил {v} (валидное выражение)"
                    + ("" if ok else f" — {r.stderr[-200:]}"), errors)
    else:
        print("  ⚠ нет тестовой картинки — пропуск рендера")

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
