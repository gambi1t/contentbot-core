"""Межпроцессный замок на тяжёлую Claude-генерацию (Remotion/HyperFrames).

Зачем: один `CLAUDE_CODE_OAUTH_TOKEN` (Max-подписка) шарится между процессами
(бот, deep-research, Cursor, второй systemd-процесс). In-process
`threading.Lock` (`_GEN_LOCK` в auto_broll/hyperframes) координирует только
ОДИН процесс. `flock` на общий файл сериализует тяжёлые генерации между
нашими генераторами и несколькими процессами — дешёвая часть Critical 3 из
GPT-ревью (полный единый ClaudeRunner отложен).

Non-blocking: если замок занят другим процессом → `ClaudeGenBusy` (понятный
текст пользователю) вместо немого зависания. Вызывается из `asyncio.to_thread`,
event loop не блокирует. На не-POSIX (Windows-dev) — no-op (fcntl недоступен).
"""
from __future__ import annotations

import logging

logger = logging.getLogger("content_bot.claude_gen_lock")

_LOCK_PATH = "/tmp/maksim_claude_gen.lock"


class ClaudeGenBusy(RuntimeError):
    """Замок генерации занят другим процессом на том же токене."""


def acquire_gen_flock(label: str = "gen"):
    """Захватить межпроцессный замок. Возвращает handle (или None на не-POSIX).

    Raises ClaudeGenBusy, если замок уже держит другой процесс.
    """
    try:
        import fcntl
    except Exception:
        return None  # Windows/dev — координации нет, просто выполняем.
    f = open(_LOCK_PATH, "w")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            f.close()
        except Exception:
            pass
        raise ClaudeGenBusy(
            "Очередь генерации занята другим процессом — подожди пару минут и повтори."
        )
    logger.info(f"[claude_gen_lock] acquired ({label})")
    return f


def release_gen_flock(handle) -> None:
    """Освободить замок (no-op если handle None)."""
    if handle is None:
        return
    try:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        handle.close()
    except Exception:
        pass
    logger.info("[claude_gen_lock] released")
