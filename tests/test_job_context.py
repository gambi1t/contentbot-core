"""TDD: JobContext + workspace isolation (Phase 1, шаг 1 production-плана).

По ревью ChatGPT 4 июня: общий HF_PROJECT для 6 параллельных агентов = гонки
файлов, stray edits, параллельный git. Решение — каждая попытка сцены в свой
sandbox `runs/<job_id>/scenes/scene_NN/attempt_M/`, валидный результат
**atomic copy** в HF_PROJECT для совместимости с существующим `_render_all`.

Контракт:
  job = JobContext.create(script_text, runs_root)
    создаёт runs/<job_id>/ с job.json, scenes/, ID формат YYYYmmddTHHMMSS_HEX4
  job.write_storyboard(storyboard) → runs/<job_id>/storyboard.json
  job.attempt_dir(scene_id, attempt_n) → runs/.../scene_NN/attempt_M/ (создаёт)
  job.record_attempt(scene_id, attempt_n, result) → result.json в attempt_dir
  job.promote(scene_id, src_html, hf_project_root) → atomic copy в hf/scene_NN.html
  job.finalize_scene(scene_id, status, ...) → запись в scene_results.json
  JobContext.load(job_id, runs_root) → восстановить (для дебага прод-фейлов)

Run: python tests/test_job_context.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
os.environ.setdefault("CLAUDE_CODE_OAUTH_TOKEN", "dummy_oauth")

sys.path.insert(0, str(Path(__file__).parent.parent))
from job_context import JobContext  # noqa: E402


def _assert(cond, msg, errors):
    print(f"  {'OK' if cond else 'FAIL'} {msg}")
    if not cond:
        errors.append(msg)


def test_create_workspace(errors):
    print("\n-- JobContext.create — создаёт runs/<id>/, job.json, scenes/ --")
    with tempfile.TemporaryDirectory() as td:
        runs_root = Path(td) / "runs"
        job = JobContext.create("test script text", runs_root)
        _assert(job.id, "job.id присвоен", errors)
        # Формат: YYYYmmddTHHMMSS_HEX4
        import re
        _assert(re.match(r"^\d{8}T\d{6}_[0-9a-f]{4}$", job.id),
                f"id format YYYYmmddTHHMMSS_HEX4 (got {job.id})", errors)
        _assert(job.root.exists() and job.root.is_dir(), "корень job создан", errors)
        _assert(job.root.name == job.id, "имя папки = id", errors)
        _assert((job.root / "job.json").exists(), "job.json есть", errors)
        _assert((job.root / "scenes").is_dir(), "scenes/ есть", errors)
        # job.json содержит script_text
        meta = json.loads((job.root / "job.json").read_text(encoding="utf-8"))
        _assert(meta.get("script_text") == "test script text", "job.json.script_text", errors)
        _assert("created_at" in meta, "created_at записан", errors)
        _assert(meta.get("id") == job.id, "job.json.id совпадает", errors)


def test_storyboard_write(errors):
    print("\n-- write_storyboard кладёт storyboard.json --")
    with tempfile.TemporaryDirectory() as td:
        runs_root = Path(td) / "runs"
        job = JobContext.create("s", runs_root)
        sb = {"scenes": [{"id": "scene_01", "business_archetype": "hero_number"}]}
        out = job.write_storyboard(sb)
        _assert(out == job.root / "storyboard.json", f"путь верный (got {out})", errors)
        _assert(out.exists(), "файл записан", errors)
        loaded = json.loads(out.read_text(encoding="utf-8"))
        _assert(loaded == sb, "содержимое сериализовано корректно", errors)


def test_attempt_dir_isolation(errors):
    print("\n-- attempt_dir создаёт изолированную папку на каждую попытку --")
    with tempfile.TemporaryDirectory() as td:
        runs_root = Path(td) / "runs"
        job = JobContext.create("s", runs_root)
        a1 = job.attempt_dir("scene_02", 1)
        a2 = job.attempt_dir("scene_02", 2)
        _assert(a1.exists() and a1.is_dir(), "attempt_1 создан", errors)
        _assert(a2.exists() and a2.is_dir(), "attempt_2 создан", errors)
        _assert(a1 != a2, "разные попытки — разные папки", errors)
        _assert(a1.name == "attempt_1", f"имя attempt_1 (got {a1.name})", errors)
        _assert(a1.parent.name == "scene_02", "родитель — scene_02", errors)
        _assert("scenes" in a1.parts, "под scenes/", errors)


def test_record_attempt(errors):
    print("\n-- record_attempt пишет result.json в attempt_dir --")
    with tempfile.TemporaryDirectory() as td:
        runs_root = Path(td) / "runs"
        job = JobContext.create("s", runs_root)
        result = {"status": "ok", "turns": 4, "cost_usd": 0.42, "duration_s": 67.3}
        path = job.record_attempt("scene_01", 1, result)
        _assert(path.name == "result.json", "имя файла result.json", errors)
        _assert(path.exists(), "result.json создан", errors)
        loaded = json.loads(path.read_text(encoding="utf-8"))
        _assert(loaded.get("status") == "ok", "status восстановлен", errors)
        _assert(loaded.get("turns") == 4, "turns восстановлен", errors)


def test_promote_atomic(errors):
    print("\n-- promote: atomic copy валидной сцены в HF_PROJECT --")
    with tempfile.TemporaryDirectory() as td:
        runs_root = Path(td) / "runs"
        hf_root = Path(td) / "hf"
        hf_root.mkdir()
        job = JobContext.create("s", runs_root)
        # имитация attempt с валидным HTML
        a1 = job.attempt_dir("scene_03", 1)
        src_html = a1 / "scene_03.html"
        src_html.write_text("<html>valid scene</html>", encoding="utf-8")
        # promote
        dst = job.promote("scene_03", src_html, hf_root)
        _assert(dst == hf_root / "scene_03.html", "promote в HF_PROJECT/scene_03.html", errors)
        _assert(dst.exists(), "файл существует в hf_root", errors)
        _assert(dst.read_text(encoding="utf-8") == "<html>valid scene</html>",
                "содержимое скопировано", errors)
        # атомарность: повторный promote из другого attempt перезаписывает
        a2 = job.attempt_dir("scene_03", 2)
        src2 = a2 / "scene_03.html"
        src2.write_text("<html>v2</html>", encoding="utf-8")
        job.promote("scene_03", src2, hf_root)
        _assert(dst.read_text(encoding="utf-8") == "<html>v2</html>",
                "повторный promote перезаписал atomically", errors)


def test_finalize_scene_results(errors):
    print("\n-- finalize_scene + scene_results.json --")
    with tempfile.TemporaryDirectory() as td:
        runs_root = Path(td) / "runs"
        job = JobContext.create("s", runs_root)
        job.finalize_scene("scene_01", "ok", attempt_n=2, turns=4, cost_usd=0.5)
        job.finalize_scene("scene_02", "failed", attempt_n=3,
                           reason="timeout, 0 Write")
        results_path = job.root / "scene_results.json"
        _assert(results_path.exists(), "scene_results.json есть", errors)
        results = json.loads(results_path.read_text(encoding="utf-8"))
        _assert(results.get("scene_01", {}).get("status") == "ok",
                "scene_01 status=ok", errors)
        _assert(results.get("scene_01", {}).get("attempt_n") == 2,
                "attempt_n записан", errors)
        _assert(results.get("scene_02", {}).get("status") == "failed",
                "scene_02 status=failed", errors)
        _assert(results.get("scene_02", {}).get("reason"),
                "reason для failed записан", errors)


def test_load_existing(errors):
    print("\n-- JobContext.load восстанавливает уже созданный job --")
    with tempfile.TemporaryDirectory() as td:
        runs_root = Path(td) / "runs"
        job1 = JobContext.create("hello", runs_root)
        job1.finalize_scene("scene_01", "ok", attempt_n=1)
        # перезагрузка
        job2 = JobContext.load(job1.id, runs_root)
        _assert(job2.id == job1.id, "id совпадает", errors)
        _assert(job2.root == job1.root, "root совпадает", errors)
        # читаем results через загруженный объект
        results_path = job2.root / "scene_results.json"
        _assert(results_path.exists(), "результаты сохранились", errors)


def test_concurrent_finalize(errors):
    """Параллельный finalize_scene не должен ломать JSON (тест на гонку записи).

    По ревью GPT: scene_results.json пишется КОНКУРЕНТНО из нескольких сцен.
    Простой read-modify-write без lock = corrupt JSON.
    """
    print("\n-- concurrent finalize_scene из 6 потоков не ломает scene_results.json --")
    import threading
    with tempfile.TemporaryDirectory() as td:
        runs_root = Path(td) / "runs"
        job = JobContext.create("s", runs_root)

        def worker(i):
            job.finalize_scene(f"scene_{i:02d}", "ok", attempt_n=1,
                               turns=i, cost_usd=0.1 * i)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(1, 7)]
        for t in threads: t.start()
        for t in threads: t.join()

        results_path = job.root / "scene_results.json"
        try:
            results = json.loads(results_path.read_text(encoding="utf-8"))
            _assert(len(results) == 6, f"все 6 записаны (got {len(results)})", errors)
            for i in range(1, 7):
                _assert(f"scene_{i:02d}" in results,
                        f"scene_{i:02d} есть в результатах", errors)
        except json.JSONDecodeError as e:
            _assert(False, f"JSON corrupted: {e}", errors)


def main():
    print("=" * 60)
    print("test_job_context (Phase 1 step 1)")
    print("=" * 60)
    errors = []
    test_create_workspace(errors)
    test_storyboard_write(errors)
    test_attempt_dir_isolation(errors)
    test_record_attempt(errors)
    test_promote_atomic(errors)
    test_finalize_scene_results(errors)
    test_load_existing(errors)
    test_concurrent_finalize(errors)
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
