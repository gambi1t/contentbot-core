"""TDD: rate-limit-осведомлённость (3 июня, по итогу диагностики scene_02).

Диагностика scene_02 с hardened-промптом (без SKILL.md/Bash, 2765 симв) всё равно
дала таймаут: events=14, tools=[Read=3], НИ ОДНОГО Write, last=rate_limit_event.
Источник CLI (claude 2.1.144) подтверждает: `rate_limit_event` эмитится «when
rate limit info changes», payload:
  {status: allowed|allowed_warning|rejected, resetsAt: epoch, rateLimitType:
   five_hour|seven_day|..., utilization: 0..1}
Вывод: корень scene_02-фейла — НЕ lint-loop (его убрали), а троттлинг Max-
подписки. Нельзя «переинженерить» хард-лимит. Правильно — РАСПОЗНАВАТЬ его:
не retry-ить вслепую в стену 3×10мин, а сказать когда лимит сбросится.

Контракт:
  - `_parse_stream` дополнительно отдаёт `rate_limit` = последний rate_limit_info
    (dict) или None.
  - `_rate_limit_note(info) -> str|None` — человекочитаемо: тип лимита + время
    сброса (resetsAt → локальное HH:MM), либо None если не rejected/нет инфо.

Run: python tests/test_hyperframes_rate_limit.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
os.environ.setdefault("CLAUDE_CODE_OAUTH_TOKEN", "dummy_oauth")

sys.path.insert(0, str(Path(__file__).parent.parent))
import hyperframes_broll as H  # noqa: E402


def _assert(cond, msg, errors):
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


# реальная форма потока при троттлинге (по диагностике scene_02 + схеме CLI)
_THROTTLED = (
    '{"type":"system","subtype":"init"}\n'
    '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read"}]}}\n'
    '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read"}]}}\n'
    '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read"}]}}\n'
    '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed_warning",'
    '"resetsAt":1717430400,"rateLimitType":"five_hour","utilization":0.91}}\n'
    '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected",'
    '"resetsAt":1717434000,"rateLimitType":"five_hour","utilization":1.0}}\n'
)

_HEALTHY = (
    '{"type":"system","subtype":"init"}\n'
    '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Write"}]}}\n'
    '{"type":"result","total_cost_usd":0.5}\n'
)


def test_parse_stream_exposes_rate_limit(errors):
    print("\n-- _parse_stream отдаёт последний rate_limit_info --")
    d = H._parse_stream(_THROTTLED)
    rl = d.get("rate_limit")
    _assert(rl is not None, "rate_limit присутствует", errors)
    if rl:
        _assert(rl.get("status") == "rejected", f"status=rejected (последний; got {rl.get('status')})", errors)
        _assert(rl.get("rateLimitType") == "five_hour", "rateLimitType=five_hour", errors)
    _assert(d.get("tool_counts", {}).get("Read") == 3, "Read=3 (как в диагностике)", errors)


def test_parse_stream_no_rate_limit_when_healthy(errors):
    print("\n-- здоровый поток → rate_limit None --")
    d = H._parse_stream(_HEALTHY)
    _assert(d.get("rate_limit") is None, "rate_limit None когда событий нет", errors)


def test_rate_limit_note(errors):
    print("\n-- _rate_limit_note: rejected → текст с типом и временем сброса --")
    _assert(hasattr(H, "_rate_limit_note"), "_rate_limit_note есть", errors)
    if not hasattr(H, "_rate_limit_note"):
        return
    note = H._rate_limit_note({"status": "rejected", "resetsAt": 1717434000,
                               "rateLimitType": "five_hour", "utilization": 1.0})
    _assert(note is not None, "rejected → не None", errors)
    if note:
        _assert("five_hour" in note, f"упомянут тип лимита (got {note!r})", errors)
        _assert(any(c.isdigit() for c in note), "есть время сброса (цифры)", errors)
    # allowed → None (не троттлинг)
    note2 = H._rate_limit_note({"status": "allowed", "utilization": 0.2})
    _assert(note2 is None, "allowed → None", errors)
    # None/мусор → None (без краша)
    _assert(H._rate_limit_note(None) is None, "None-вход → None (без краша)", errors)


def main():
    print("=" * 60)
    print("test_hyperframes_rate_limit")
    print("=" * 60)
    errors = []
    test_parse_stream_exposes_rate_limit(errors)
    test_parse_stream_no_rate_limit_when_healthy(errors)
    test_rate_limit_note(errors)
    print()
    if errors:
        print(f"FAIL: {len(errors)} assertion(s)")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
