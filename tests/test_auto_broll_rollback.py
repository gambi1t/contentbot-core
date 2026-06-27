"""TDD: откат лишних правок Claude в auto_broll — через снимок, БЕЗ git.

После генерации AutoBroll.tsx Claude мог нашалить (изменить другие файлы / добавить
свои). `_restore_stray` возвращает проект к снимку КРОМЕ AutoBroll.tsx. Заменяет
git-механизм, который был опасен, если проект — подпапка монорепо ядра
(`git checkout` бил по КОРНЮ репозитория и затирал несохранённые правки ядра).

Запуск: python -m pytest tests/test_auto_broll_rollback.py -v
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import auto_broll  # noqa: E402


def _mk_project(tmp: Path) -> None:
    (tmp / "src" / "scenes").mkdir(parents=True)
    (tmp / "src" / "scenes" / "AutoBroll.tsx").write_text("BASELINE auto", encoding="utf-8")
    (tmp / "src" / "scenes" / "MaksimInserts2.tsx").write_text("REF original", encoding="utf-8")
    (tmp / "package.json").write_text('{"name":"broll"}', encoding="utf-8")
    # тяжёлая папка — не должна попасть в снимок и не должна трогаться restore
    (tmp / "node_modules").mkdir()
    (tmp / "node_modules" / "big.txt").write_text("HUGE", encoding="utf-8")


def test_restore_keeps_autobroll_reverts_stray_removes_added(tmp_path, monkeypatch):
    proj = tmp_path / "broll"
    proj.mkdir()
    _mk_project(proj)
    monkeypatch.setattr(auto_broll, "BROLL_PROJECT", proj)

    snap = auto_broll._snapshot_project()
    # снимок не должен тянуть node_modules
    assert not (snap / "node_modules").exists(), "node_modules попал в снимок"
    try:
        # Симулируем Claude: легит-результат в AutoBroll.tsx + шалости вокруг
        (proj / "src" / "scenes" / "AutoBroll.tsx").write_text("NEW generated", encoding="utf-8")
        (proj / "src" / "scenes" / "MaksimInserts2.tsx").write_text("STRAY edit", encoding="utf-8")
        (proj / "src" / "scenes" / "Evil.tsx").write_text("added by claude", encoding="utf-8")
        (proj / "package.json").write_text('{"name":"HACKED"}', encoding="utf-8")

        auto_broll._restore_stray(snap)

        # AutoBroll.tsx — СОХРАНЁН (результат генерации)
        assert (proj / "src" / "scenes" / "AutoBroll.tsx").read_text(encoding="utf-8") == "NEW generated"
        # стрэй-правки ОТКАТАНЫ к снимку
        assert (proj / "src" / "scenes" / "MaksimInserts2.tsx").read_text(encoding="utf-8") == "REF original"
        assert (proj / "package.json").read_text(encoding="utf-8") == '{"name":"broll"}'
        # добавленный Claude файл УДАЛЁН
        assert not (proj / "src" / "scenes" / "Evil.tsx").exists()
        # node_modules не тронут
        assert (proj / "node_modules" / "big.txt").read_text(encoding="utf-8") == "HUGE"
    finally:
        shutil.rmtree(snap, ignore_errors=True)


def test_generate_restores_autobroll_to_committed(tmp_path, monkeypatch):
    """После генерации AutoBroll.tsx возвращается к закоммиченной версии (из
    снимка) → дерево монорепо чистое (гейт деплоя), стрэй-правки откатаны."""
    import claude_gen_lock

    proj = tmp_path / "remotion"
    proj.mkdir()
    _mk_project(proj)  # AutoBroll.tsx="BASELINE auto", MaksimInserts2="REF original"
    monkeypatch.setattr(auto_broll, "BROLL_PROJECT", proj)

    def fake_claude(prompt):
        # Claude переписывает AutoBroll.tsx (результат) + шалит в эталоне (стрэй)
        (proj / "src" / "scenes" / "AutoBroll.tsx").write_text("GENERATED tsx", encoding="utf-8")
        (proj / "src" / "scenes" / "MaksimInserts2.tsx").write_text("STRAY edit", encoding="utf-8")
        return 0.0

    monkeypatch.setattr(auto_broll, "_run_claude", fake_claude)
    monkeypatch.setattr(
        auto_broll, "_render_all",
        lambda out_dir: ([out_dir / "autobroll" / "auto_01.mp4"], []),
    )
    monkeypatch.setattr(claude_gen_lock, "acquire_gen_flock", lambda name: "dummy")
    monkeypatch.setattr(claude_gen_lock, "release_gen_flock", lambda f: None)

    auto_broll.generate_auto_broll(
        "Сценарий длиннее тридцати символов для теста точно.", tmp_path / "out"
    )

    # AutoBroll.tsx вернулся к закоммиченной версии → дерево чистое
    assert (proj / "src" / "scenes" / "AutoBroll.tsx").read_text(encoding="utf-8") == "BASELINE auto"
    # стрэй-правка эталона откатана
    assert (proj / "src" / "scenes" / "MaksimInserts2.tsx").read_text(encoding="utf-8") == "REF original"


def test_no_git_used_for_rollback():
    """auto_broll больше НЕ использует git для отката (безопасно как подпапка ядра)."""
    code = (ROOT / "auto_broll.py").read_text(encoding="utf-8")
    # Проверяем по ОПРЕДЕЛЕНИЯМ функций (упоминание старых имён в комментах-
    # объяснениях «что заменили» допустимо — это не использование git).
    assert "def ensure_git_baseline" not in code, "git-baseline функция не удалена"
    assert "def _revert_stray" not in code, "git _revert_stray функция не удалена"
    assert "def _git(" not in code, "git-обёртка не удалена"
    # Новый механизм отката на месте.
    assert "def _snapshot_project" in code and "def _restore_stray" in code, "нет снимок-отката"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
