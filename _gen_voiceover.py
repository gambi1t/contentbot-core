"""Генерация озвучки сценария для теста монтажа (голос Артёма — голоса Максима ещё нет).

Запуск:  python _gen_voiceover.py
Выход:   _montage_test/voiceover.mp3
"""
from __future__ import annotations

import sys
from pathlib import Path

OUT = Path(__file__).parent / "_montage_test"
OUT.mkdir(exist_ok=True)

SCRIPT = (
    "Я уволил себя из роли надзирателя. Контроль стал качественнее, "
    "а у меня появилось время на то, чем реально должен заниматься собственник.\n\n"
    "Подключил ИИ-ассистента: он слушает планёрки через Плауд, сам ставит "
    "задачи в Битрикс. Я ничего не записываю и не догоняю людей в чатах. "
    "Вечером — отчёт: что сделано, что висит, кто провалил.\n\n"
    "Раньше операционка сидела в голове. Теперь — в системе. "
    "Голова свободна для решений.\n\n"
    "Как собрал — в Telegram-канале «Юмсунов про реальный бизнес»."
)


def main() -> int:
    print("импортирую bot.py…")
    from bot import generate_voiceover

    out = OUT / "voiceover.mp3"
    generate_voiceover(SCRIPT, str(out))
    if not out.exists() or out.stat().st_size < 1000:
        print("FAIL: озвучка не создалась")
        return 1

    import subprocess, json
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(out)],
        capture_output=True, text=True,
    )
    dur = float(json.loads(probe.stdout)["format"]["duration"])
    print(f"OK: {out} — {out.stat().st_size // 1024} KB, {dur:.1f} сек")
    return 0


if __name__ == "__main__":
    sys.exit(main())
