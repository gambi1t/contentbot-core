"""TDD: интеграция layout-детектора в fix-rounds generate_hyperframes_broll.

Контракт (1 июня 2026):
  • После генерации сцен прогоняется `_inspect_all_scenes()` (node-детектор
    hf_inspect_layout.mjs). Если есть layout-issues → fix-round с координатами
    проблем в промпте Клоду.
  • Render-ошибки ФАТАЛЬНЫ (нет видео) → raise после MAX_FIX_ROUNDS.
  • Layout-issues — QUALITY: чиним в раундах, но если раунды исчерпаны, а
    рендер успешен → отдаём клипы с warning (лучше неидеальное видео, чем
    ничего). НЕ raise.
  • `_format_layout_issues` даёт человекочитаемый текст по каждому типу
    (offscreen / overlap / crowding).

Run: python tests/test_hf_layout_fixrounds.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

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


def _run_gen(inspect_seq, render_seq, tmp="/tmp/_hf_fr_test"):
    """Гоняет generate_hyperframes_broll с замоканными шагами.
    inspect_seq — список результатов _inspect_all_scenes по вызовам.
    render_seq — список (clips, errors) по вызовам _render_all.
    Возвращает (result_or_exc, claude_calls, fix_prompts)."""
    claude_calls = {"n": 0}
    fix_prompts = []

    def fake_claude(prompt, timeout=None):
        # Build вынесен в _run_build_phase (мокается отдельно), поэтому ВСЕ
        # вызовы _run_claude здесь — это fix-round'ы layout/render.
        claude_calls["n"] += 1
        fix_prompts.append(prompt)
        return 0.0

    insp = list(inspect_seq)
    rend = list(render_seq)

    def fake_inspect():
        return insp.pop(0) if insp else {}

    def fake_render(out_dir):
        return rend.pop(0) if rend else ([], [])

    # Фаза 1 (storyboard) и фаза 2 (build) ортогональны этому тесту про
    # layout/render fix-rounds — мокаем обе, чтобы generate сразу шёл к
    # inspect+render+fix-цикл.
    with patch.object(H, "ensure_git_baseline"), \
         patch.object(H, "_revert_stray"), \
         patch.object(H, "_run_storyboard_phase", return_value=({"version": "1.0", "scenes": []}, 0.0)), \
         patch.object(H, "_run_build_phase", return_value=0.0), \
         patch.object(H, "_run_claude", side_effect=fake_claude), \
         patch.object(H, "_inspect_all_scenes", side_effect=fake_inspect), \
         patch.object(H, "_render_all", side_effect=fake_render), \
         patch.object(H, "HF_PROJECT", Path("/tmp")):
        try:
            res = H.generate_hyperframes_broll("script", tmp)
            return ("ok", res), claude_calls["n"], fix_prompts
        except Exception as e:
            return ("exc", e), claude_calls["n"], fix_prompts


def test_helpers_exist(errors):
    print("\n-- хелперы существуют --")
    _assert(hasattr(H, "_inspect_all_scenes"), "_inspect_all_scenes есть", errors)
    _assert(hasattr(H, "_inspect_layout"), "_inspect_layout есть", errors)
    _assert(hasattr(H, "_format_layout_issues"), "_format_layout_issues есть", errors)


def test_inspect_timeout_budget(errors):
    print("\n-- LAYOUT_INSPECT_TIMEOUT в разумных пределах (MEDIUM-B) --")
    # Реальное время детектора ~5-20с/сцена. Старое значение 120 давало
    # бюджет 120×6=720с/раунд × 3 раунда = 36 минут только на инспекцию.
    # Снижаем до 30с (запас ×1.5 над реальным).
    _assert(
        hasattr(H, "LAYOUT_INSPECT_TIMEOUT"),
        "LAYOUT_INSPECT_TIMEOUT существует",
        errors,
    )
    if hasattr(H, "LAYOUT_INSPECT_TIMEOUT"):
        v = H.LAYOUT_INSPECT_TIMEOUT
        _assert(
            15 <= v <= 40,
            f"LAYOUT_INSPECT_TIMEOUT в [15,40] (got {v})",
            errors,
        )


def test_layout_issue_triggers_fixround(errors):
    print("\n-- layout-issue → fix-round, потом чисто → ok --")
    # 1й inspect: overlap; 2й: чисто. render всегда ok.
    issues1 = {"scene_01.html": [{"type": "overlap", "a": "X", "b": "Y", "overlapPx": 100}]}
    (kind, res), n_claude, fixp = _run_gen(
        inspect_seq=[issues1, {}],
        render_seq=[([Path("a.mp4")], []), ([Path("a.mp4")], [])],
    )
    _assert(kind == "ok", f"без исключения (got {kind}: {res if kind=='exc' else ''})", errors)
    _assert(n_claude == 1, f"1 fix-round вызов (build вынесен в build_phase), got {n_claude}", errors)
    _assert(fixp and "scene_01" in fixp[0], "fix-промпт упоминает проблемную сцену", errors)


def test_clean_first_no_fixround(errors):
    print("\n-- сразу чисто → без fix-round --")
    (kind, res), n_claude, fixp = _run_gen(
        inspect_seq=[{}],
        render_seq=[([Path("a.mp4")], [])],
    )
    _assert(kind == "ok", "без исключения", errors)
    _assert(n_claude == 0, f"0 fix-round вызовов (build вынесен), got {n_claude}", errors)


def test_render_error_fatal(errors):
    print("\n-- render-ошибка после раундов → raise --")
    (kind, res), n_claude, fixp = _run_gen(
        inspect_seq=[{}, {}, {}],
        render_seq=[([], ["scene_03: boom"])] * 3,
    )
    _assert(kind == "exc", "поднято исключение", errors)
    _assert(
        kind == "exc" and isinstance(res, H.HyperFramesBrollError),
        f"тип HyperFramesBrollError (got {type(res).__name__ if kind=='exc' else res})",
        errors,
    )


def test_layout_unfixable_ships_with_warning(errors):
    print("\n-- layout-issue не чинится за раунды, но рендер ОК → отдаём (НЕ raise) --")
    issues = {"scene_02.html": [{"type": "crowding", "a": "A", "b": "B", "gapPx": 8}]}
    (kind, res), n_claude, fixp = _run_gen(
        inspect_seq=[issues, issues, issues],   # всегда есть проблема
        render_seq=[([Path("a.mp4")], [])] * 3,  # рендер всегда ок
    )
    _assert(kind == "ok", f"отдаёт клипы без исключения (got {kind})", errors)
    # MAX_FIX_ROUNDS fix-раундов исчерпаны (build вынесен в build_phase)
    _assert(n_claude == H.MAX_FIX_ROUNDS, f"{H.MAX_FIX_ROUNDS} fix-раундов, got {n_claude}", errors)


def test_format_issues_text(errors):
    print("\n-- _format_layout_issues человекочитаем --")
    txt = H._format_layout_issues({
        "scene_01.html": [
            {"type": "offscreen", "kind": "card", "edge": "right", "text": "20%", "rect": [760, 800, 1160, 1100]},
            {"type": "overlap", "a": "Заголовок", "b": "подпись", "overlapPx": 500},
            {"type": "crowding", "a": "верх", "b": "низ", "gapPx": 8},
        ]
    })
    _assert("scene_01" in txt, "упомянута сцена", errors)
    _assert("20%" in txt and "right" in txt.lower(), "offscreen описан", errors)
    _assert("Заголовок" in txt and "подпись" in txt, "overlap описан", errors)
    _assert("8" in txt, "crowding gap описан", errors)


def main():
    print("=" * 60)
    print("test_hf_layout_fixrounds")
    print("=" * 60)
    errors = []
    test_helpers_exist(errors)
    test_inspect_timeout_budget(errors)
    if not (hasattr(H, "_inspect_all_scenes") and hasattr(H, "_format_layout_issues")):
        print("\nFAIL: хелперы не определены — остальное не гоняем")
        return 1
    test_layout_issue_triggers_fixround(errors)
    test_clean_first_no_fixround(errors)
    test_render_error_fatal(errors)
    test_layout_unfixable_ships_with_warning(errors)
    test_format_issues_text(errors)
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
