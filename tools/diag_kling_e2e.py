"""End-to-end: новый AI-видео B-roll (обобщённый director → Kling 3.0 Pro).

Гонит весь путь generate_ai_broll на картинг-сценарии (3 клипа × 5с = ~$1.68),
печатает пути + фактическую цену, выдёргивает кадры для визуальной проверки
фиделити Kling (карт vs generic, и насколько лучше старого Seedance).

Запуск: как остальные diag_*, под env maksim-bot (нужен FAL_KEY + OAuth).
"""
import logging
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ai_video_broll as A  # noqa: E402

SCRIPT = """Быстрее — не значит сильнее давить газ. Именно здесь теряют секунды круг за кругом.
Первое: тормоз в повороте убивает темп. Тормози до входа — внутри только газ. Второе: смотришь в бампер впереди — едешь по чужой линии. Смотри на апекс. Третье: резкий газ на выходе — задок срывается. Нажимай плавно. Приезжай — разберём на трассе."""

OUT = Path("/tmp/kling_e2e_test")
OUT.mkdir(parents=True, exist_ok=True)
DUR = int(sys.argv[1]) if len(sys.argv) > 1 else 5
N = int(sys.argv[2]) if len(sys.argv) > 2 else 3

print(f"Гоню generate_ai_broll: target_clips={N}, duration={DUR}с (Kling 3.0 Pro)…\n")
paths, cost = A.generate_ai_broll(SCRIPT, OUT, duration=DUR, target_clips=N)

print("\n" + "=" * 60)
print(f"ГОТОВО: {len(paths)} клипов, фактическая цена ${cost:.2f}")
for p in paths:
    sz = Path(p).stat().st_size if Path(p).exists() else 0
    frame = OUT / (Path(p).stem + "_frame.png")
    subprocess.run(["ffmpeg", "-y", "-ss", str(min(2, DUR - 1)), "-i", str(p),
                    "-frames:v", "1", str(frame)], capture_output=True)
    print(f"  {Path(p).name}: {sz} bytes → кадр {frame.name}")
print(f"\nКадры в {OUT}/")
