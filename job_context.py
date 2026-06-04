"""JobContext — workspace-изоляция для производственного B-roll-pipeline.

Цель (по ревью ChatGPT 4 июня 2026): шесть параллельных subagent-вызовов в одну
рабочую папку HF_PROJECT = гонки файлов, stray edits, конкурентный git revert.
Решение — каждая попытка сцены пишется в свой sandbox:

    runs/<job_id>/
    ├── job.json              — параметры запуска
    ├── storyboard.json       — фаза 1
    ├── scene_results.json    — итог по сценам (concurrent-safe запись)
    └── scenes/
        ├── scene_01/
        │   ├── attempt_1/
        │   │   ├── result.json    — turns, cost, status, reason
        │   │   ├── stream.jsonl   — стрим claude (пишет scheduler)
        │   │   └── scene_01.html  — кладёт сам субагент через Write
        │   └── attempt_2/...
        └── scene_02/...

После валидации `_scene_valid_minimal` → `job.promote(scene_id, src_html,
hf_project_root)` делает **atomic copy** в `HF_PROJECT/scene_NN.html`. Это
сохраняет совместимость с `_render_all`, `_inspect_all_scenes`, `assemble_*`,
которые ожидают сцены в корне HF_PROJECT.

Без этого модуля production-дебаг прод-фейлов невозможен (см. ChatGPT review
2026-06-04 §«job-level артефакты»).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JobContext:
    """Жизненный цикл одного B-roll-job'а. См. docstring модуля."""

    # глобальный lock для scene_results.json внутри одного процесса.
    # Сцены пишутся из разных asyncio-task или потоков — без lock JSON ломается.
    _results_lock = threading.Lock()

    def __init__(self, job_id: str, runs_root: Path):
        self.id = job_id
        self.runs_root = Path(runs_root)
        self.root = self.runs_root / job_id

    # ── создание / загрузка ──────────────────────────────────────────────
    @classmethod
    def create(cls, script_text: str, runs_root: Path | str) -> "JobContext":
        """Создаёт новую job. ID = `YYYYmmddTHHMMSS_HEX4` — отсортирован по времени
        и уникален при параллельных запусках в одну секунду (4 hex = 65536).
        """
        runs_root = Path(runs_root)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        rand = secrets.token_hex(2)  # 4 hex chars
        job_id = f"{ts}_{rand}"
        job = cls(job_id, runs_root)
        job.root.mkdir(parents=True, exist_ok=False)
        (job.root / "scenes").mkdir()
        meta = {
            "id": job_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "script_text": script_text,
            "script_sha1": hashlib.sha1(script_text.encode("utf-8")).hexdigest(),
        }
        (job.root / "job.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return job

    @classmethod
    def load(cls, job_id: str, runs_root: Path | str) -> "JobContext":
        """Восстанавливает существующий job (для дебага прод-фейлов)."""
        runs_root = Path(runs_root)
        job = cls(job_id, runs_root)
        if not job.root.exists():
            raise FileNotFoundError(f"Job не найден: {job.root}")
        return job

    # ── storyboard ───────────────────────────────────────────────────────
    def write_storyboard(self, storyboard: dict) -> Path:
        path = self.root / "storyboard.json"
        path.write_text(
            json.dumps(storyboard, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path

    # ── attempt-папки ────────────────────────────────────────────────────
    def attempt_dir(self, scene_id: str, attempt_n: int) -> Path:
        """Возвращает (и создаёт) папку для конкретной попытки."""
        path = self.root / "scenes" / scene_id / f"attempt_{attempt_n}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def record_attempt(self, scene_id: str, attempt_n: int, result: dict) -> Path:
        """Кладёт result.json в attempt-папку (turns, cost, status, reason и пр.)."""
        adir = self.attempt_dir(scene_id, attempt_n)
        path = adir / "result.json"
        # добавляем timestamp если не передан
        result = {**result, "recorded_at": result.get("recorded_at") or
                  datetime.now(timezone.utc).isoformat()}
        path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path

    # ── promote: atomic copy в HF_PROJECT ───────────────────────────────
    def promote(self, scene_id: str, src_html: Path,
                hf_project_root: Path | str) -> Path:
        """**Атомарно** копирует валидный scene_NN.html в HF_PROJECT.

        Реализация: пишем во временный файл в TARGETной папке (того же
        filesystem'а), затем os.replace — атомарная замена даже если читает
        конкурент. Так `_render_all` никогда не увидит полуфайл.
        """
        src_html = Path(src_html)
        if not src_html.exists():
            raise FileNotFoundError(f"источник promote не существует: {src_html}")
        hf_project_root = Path(hf_project_root)
        dst = hf_project_root / f"{scene_id}.html"
        hf_project_root.mkdir(parents=True, exist_ok=True)
        # temp в той же папке (важно — os.replace через partition не атомарен)
        fd, tmp_path = tempfile.mkstemp(
            suffix=".html.tmp", prefix=f".{scene_id}_", dir=str(hf_project_root)
        )
        os.close(fd)
        try:
            shutil.copyfile(src_html, tmp_path)
            os.replace(tmp_path, dst)  # atomic
        except Exception:
            # уборка temp если сломались
            try: os.unlink(tmp_path)
            except OSError: pass
            raise
        return dst

    # ── finalize_scene + scene_results.json (concurrent-safe) ───────────
    def finalize_scene(self, scene_id: str, status: str, *,
                       attempt_n: int | None = None,
                       turns: int | None = None,
                       cost_usd: float | None = None,
                       duration_s: float | None = None,
                       reason: str | None = None,
                       **extra: Any) -> None:
        """Записывает финальный итог сцены в scene_results.json.

        Concurrent-safe: внутрипроцессный lock (`_results_lock`) + read-modify-
        write через временный файл с os.replace. 6 параллельных сцен пишут
        одновременно — corruption невозможна. (Между процессами нужен fcntl /
        file-lock — это **TODO** для multi-tenant случая.)
        """
        entry = {
            "status": status,
            "attempt_n": attempt_n,
            "turns": turns,
            "cost_usd": cost_usd,
            "duration_s": duration_s,
            "reason": reason,
            "finalized_at": datetime.now(timezone.utc).isoformat(),
            **extra,
        }
        # отфильтровываем None для чистоты JSON
        entry = {k: v for k, v in entry.items() if v is not None}

        results_path = self.root / "scene_results.json"
        with self._results_lock:
            # read
            current: dict = {}
            if results_path.exists():
                try:
                    current = json.loads(results_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    # битый JSON — записываем рядом дамп для дебага
                    backup = results_path.with_suffix(".broken.json")
                    try:
                        shutil.copyfile(results_path, backup)
                    except OSError:
                        pass
                    current = {}
            # modify
            current[scene_id] = entry
            # write atomically
            fd, tmp_path = tempfile.mkstemp(
                suffix=".json.tmp", prefix=".scene_results_", dir=str(self.root)
            )
            os.close(fd)
            try:
                Path(tmp_path).write_text(
                    json.dumps(current, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
                os.replace(tmp_path, results_path)
            except Exception:
                try: os.unlink(tmp_path)
                except OSError: pass
                raise

    # ── удобные геттеры ──────────────────────────────────────────────────
    def read_results(self) -> dict:
        """Прочитать scene_results.json или вернуть {} если ещё нет."""
        path = self.root / "scene_results.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
