"""Pipeline #2 — B-roll montage (ролик без аватара).

Собирает вертикальный ролик из B-roll клипов под закадровую озвучку
голосом Максима. Без говорящей головы / HeyGen-аватара.

Модули:
  assembler — avatar-free ffmpeg-монтаж (клипы + озвучка → MP4)
  selector  — metadata-aware выбор клипов из тегированного архива
  handlers  — 2-фазный Telegram-flow (сценарий → preview → сборка)
"""
