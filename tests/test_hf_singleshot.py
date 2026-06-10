"""Тест single-shot генерации HF-сцен (10 июня).

Контекст: агентный headless-build (`claude -p` + Read/Write tools) на сервере
зависал 18/18 (Write вне workspace → permission-тупик в -p). CTO-ресерч
(Remotion AI docs, screenshot-to-code, v0): для одно-файловых выходов
прод-паттерн = ОДНА completion, файл пишет вызывающий код.

Новый build: промпт с ИНЛАЙН reference_pack.md + index.html + контракт сцены →
SubscriptionClient (без инструментов, проверенный путь бота) → ответ = HTML →
Python пишет файл → _scene_valid_minimal → promote. Невалидный ответ →
regenerate-попытка с описанием дефектов. Параллель: asyncio + semaphore.

Запуск: python tests/test_hf_singleshot.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")

# HF_PROJECT-фикстура ДО импорта модуля (модуль читает env при импорте)
_FIX = Path(tempfile.mkdtemp(prefix="hf_fix_"))
os.environ["HYPERFRAMES_PROJECT_DIR"] = str(_FIX)
(_FIX / "reference_pack.md").write_text(
    "# REF-PACK-MARKER\nПравила HF: safe-area x∈[40,1040] y∈[480,1440].",
    encoding="utf-8")
(_FIX / "index.html").write_text(
    "<!doctype html><html><head><style>@font-face{font-family:InterMarker}"
    "</style></head><body></body></html>", encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent.parent))

import hyperframes_broll as hb  # noqa: E402


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def _valid_scene_html(scene_id: str) -> str:
    """HTML, проходящий _scene_valid_minimal (>5000 байт, все маркеры)."""
    pad = "/* " + ("дизайн " * 700) + " */"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>{pad}</style></head>
<body><div data-composition-id="{scene_id}" data-width="1080" data-height="1920">
<h1>Текст</h1></div>
<script>
const tl = gsap.timeline({{paused: true}});
window.__timelines = window.__timelines || {{}};
window.__timelines["{scene_id}"] = tl;
</script></body></html>"""


_SB = {"scenes": [
    {"id": f"scene_{i:02d}", "business_archetype": f"arch_{i}",
     "hf_technique": "kinetic_typography", "visual_style": "Swiss Pulse",
     "motion_family": "slide", "primary_text": f"Текст {i}"}
    for i in range(1, 7)
]}


class _FakeResp:
    def __init__(self, text):
        class _C:  # mimic content block
            pass
        c = _C(); c.text = text
        self.content = [c]


class _FakeClient:
    """Мимикрирует SubscriptionClient.messages.create; пишет лог промптов."""
    def __init__(self, replies_by_scene):
        self.calls = []  # list[(scene_id_guess, prompt)]
        self._replies = replies_by_scene  # scene_id -> list[str] (по попыткам)

        class _M:
            def __init__(s, outer): s._o = outer
            def create(s, *, model, max_tokens, messages, **kw):
                prompt = messages[0]["content"]
                sid = next((x for x in _SB_IDS if x in prompt), "?")
                s._o.calls.append((sid, prompt))
                seq = s._o._replies[sid]
                idx = sum(1 for c in s._o.calls[:-1] if c[0] == sid)
                return _FakeResp(seq[min(idx, len(seq) - 1)])
        self.messages = _M(self)


_SB_IDS = [s["id"] for s in _SB["scenes"]]


def main():
    errors = []

    print("\n[_extract_html — чистка ответа модели]")
    clean = _valid_scene_html("scene_01")
    _assert(hb._extract_html(clean) == clean.strip(), "чистый HTML — как есть", errors)
    fenced = f"```html\n{clean}\n```"
    _assert(hb._extract_html(fenced) == clean.strip(), "```-фенс снимается", errors)
    prosed = f"Вот сцена:\n\n{clean}\n\nГотово!"
    got = hb._extract_html(prosed)
    _assert(got.startswith("<!doctype") and got.endswith("</html>"),
            "проза до/после отрезается", errors)
    try:
        hb._extract_html("<html><body>обрыв")
        _assert(False, "без </html> → ValueError", errors)
    except ValueError:
        _assert(True, "без </html> → ValueError", errors)

    print("\n[_build_scene_singleshot_prompt — самодостаточный, без инструментов]")
    p = hb._build_scene_singleshot_prompt(_SB, "scene_03")
    _assert("REF-PACK-MARKER" in p, "reference_pack ИНЛАЙН в промпте", errors)
    _assert("InterMarker" in p, "index.html (образец/@font-face) инлайн", errors)
    _assert('"scene_03"' in p or "scene_03" in p, "контракт сцены в промпте", errors)
    _assert("arch_3" in p, "поля контракта (archetype) в промпте", errors)
    low = p.lower()
    _assert("write" not in low and "bash" not in low and "прочитай" not in low,
            "нет инструментальных инструкций (Write/Bash/«прочитай файл»)", errors)
    _assert("<!doctype" in low and "</html>" in low,
            "явная инструкция формата вывода (от <!doctype до </html>)", errors)

    pf = hb._build_scene_singleshot_prompt(
        _SB, "scene_03", prev_html="<html>OLD-MARKER</html>",
        issues="тексты НАЛЕЗАЮТ (120px²)")
    _assert("OLD-MARKER" in pf and "НАЛЕЗАЮТ" in pf,
            "fix-режим: прошлый HTML + дефекты в промпте", errors)

    print("\n[модель — Opus 4.8 (решение Артёма 10 июня, A/B: 46с vs 74с, кадр богаче)]")
    _assert(hb.HF_SINGLESHOT_MODEL == "claude-opus-4-8",
            f"дефолт claude-opus-4-8, got {hb.HF_SINGLESHOT_MODEL}", errors)

    print("\n[_singleshot_llm_client — таймаут под тяжёлую генерацию, не 180с]")
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "dummy-token"
    try:
        cl = hb._singleshot_llm_client()
        _assert(getattr(cl, "_timeout_sec", 0) == 480,
                f"timeout_sec=480 (HF_SINGLESHOT_TIMEOUT_S), got {getattr(cl, '_timeout_sec', None)}",
                errors)
        _assert(getattr(cl, "_extra_env", {}).get("MAX_THINKING_TOKENS") == "0",
                "thinking отключён (MAX_THINKING_TOKENS=0) — иначе сцена не влезает в таймаут",
                errors)
    finally:
        os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)

    print("\n[_run_build_phase_singleshot — happy path, 6 сцен через fake client]")
    fake = _FakeClient({sid: [_valid_scene_html(sid)] for sid in _SB_IDS})
    hb._singleshot_llm_client = lambda: fake
    from job_context import JobContext
    runs = Path(tempfile.mkdtemp(prefix="hf_runs_"))
    job = JobContext.create("тестовый сценарий", runs)
    cost = asyncio.run(hb._run_build_phase_singleshot(_SB, job))
    promoted = [sf for sf in hb.SCENE_FILES if (hb.HF_PROJECT / sf).exists()]
    _assert(len(promoted) == 6, f"6/6 сцен promoted в HF_PROJECT, got {len(promoted)}", errors)
    _assert(len(fake.calls) == 6, f"ровно 6 вызовов LLM (без лишних), got {len(fake.calls)}", errors)
    _assert(isinstance(cost, float), "возвращает float cost", errors)

    print("\n[retry: невалидный ответ → regenerate с дефектами]")
    for sf in hb.SCENE_FILES:
        (hb.HF_PROJECT / sf).unlink(missing_ok=True)
    bad = "<!doctype html><html><body>коротыш</body></html>"
    replies = {sid: [_valid_scene_html(sid)] for sid in _SB_IDS}
    replies["scene_02"] = [bad, _valid_scene_html("scene_02")]
    fake2 = _FakeClient(replies)
    hb._singleshot_llm_client = lambda: fake2
    job2 = JobContext.create("тестовый сценарий 2", runs)
    asyncio.run(hb._run_build_phase_singleshot(_SB, job2))
    s2_calls = [pr for sid, pr in fake2.calls if sid == "scene_02"]
    _assert(len(s2_calls) == 2, f"scene_02: 2 попытки, got {len(s2_calls)}", errors)
    _assert("коротыш" in s2_calls[1] or "короткий" in s2_calls[1],
            "2-я попытка содержит прошлый HTML/дефекты (regenerate, не слепой retry)", errors)
    _assert((hb.HF_PROJECT / "scene_02.html").exists(), "scene_02 в итоге promoted", errors)

    print("\n[все попытки невалидны → HyperFramesBrollError]")
    for sf in hb.SCENE_FILES:
        (hb.HF_PROJECT / sf).unlink(missing_ok=True)
    fake3 = _FakeClient({sid: [bad] for sid in _SB_IDS})
    hb._singleshot_llm_client = lambda: fake3
    job3 = JobContext.create("тестовый сценарий 3", runs)
    raised = False
    try:
        asyncio.run(hb._run_build_phase_singleshot(_SB, job3))
    except hb.HyperFramesBrollError:
        raised = True
    _assert(raised, "все попытки плохие → HyperFramesBrollError", errors)

    print("\n[_problems_by_scene — маппинг дефектов на сцены для fix-round]")
    layout = {"scene_01.html": [{"type": "overlap", "a": "X", "b": "Y", "overlapPx": 10}]}
    rerrs = ["scene_04.html: ffmpeg rc=1: boom", "мусор без префикса"]
    mp = hb._problems_by_scene(layout, rerrs)
    _assert(set(mp) == {"scene_01.html", "scene_04.html"},
            f"сцены с проблемами найдены, got {set(mp)}", errors)
    _assert("НАЛЕЗАЮТ" in mp["scene_01.html"] or "overlap" in mp["scene_01.html"].lower(),
            "layout-дефект в тексте", errors)
    _assert("ffmpeg" in mp["scene_04.html"], "render-ошибка в тексте", errors)

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
