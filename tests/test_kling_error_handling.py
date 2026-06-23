"""TDD: обработка сбоя Kling — 2 попытки рендера + кнопки восстановления по типу.

Закрепляет A+B (23.06): «all Kling clips failed» больше не глухой тупик.
  A. fal_media: рендер-вызов ретраится ТОЛЬКО на технических ошибках fal
     (downstream_service_error и т.п.) — БЕЗ двойного списания (успех не
     повторяем; сбой = рендер не завершён = не оплачен). content_policy и
     прочие — финал без повтора.
  B. handlers: при полном сбое — сообщение по типу ошибки + кнопки. Техническая
     → есть «Повторить». Контентная (модерация) → «Повторить» НЕТ, ведём на
     правку сценария.
Тип ошибки прокидывается: fal_media.errors_out → ai_video_broll.AiVideoError.category
→ broll.handlers._av_fail_text/_av_fail_keyboard.

Источник таксономии ошибок fal: fal.ai/docs/errors (проверено 23.06).
Сеть/LLM замоканы.
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── A1. классификация ошибок fal ─────────────────────────────────────────────
def test_classify_fal_error_buckets():
    import fal_media as F
    assert F._classify_fal_error(Exception("content_policy_violation: nope")) == "content"
    assert F._classify_fal_error(Exception("downstream_service_error: upstream")) == "technical"
    assert F._classify_fal_error(Exception("internal_server_error")) == "technical"
    assert F._classify_fal_error(Exception("generation_timeout after 600s")) == "technical"
    assert F._classify_fal_error(Exception("downstream_service_unavailable")) == "technical"
    assert F._classify_fal_error(Exception("validation: aspect_ratio invalid")) == "other"
    assert F._classify_fal_error(Exception("who knows")) == "other"


def _fake_fal(subscribe):
    return types.SimpleNamespace(subscribe=subscribe)


def _patch_common(monkeypatch, F, subscribe, *, download_ok=True):
    monkeypatch.setitem(sys.modules, "fal_client", _fake_fal(subscribe))
    monkeypatch.setattr(F, "_is_configured", lambda: True)
    monkeypatch.setattr(F.time, "sleep", lambda *a: None)   # без реального backoff
    if download_ok:
        monkeypatch.setattr(F, "_download_timeout",
                            lambda url, part: Path(part).write_bytes(b"x" * 60000))


# ── A2. технический сбой → повтор → успех (2 попытки), без лишних трат ────────
def test_render_retry_recovers_on_technical(monkeypatch, tmp_path):
    import fal_media as F
    calls = {"n": 0}

    def subscribe(endpoint, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception("downstream_service_error: upstream 500")
        return {"video": {"url": "http://x/v.mp4"}}

    _patch_common(monkeypatch, F, subscribe)
    errs = []
    out = F.generate_kling_video("p", tmp_path / "c.mp4", duration=5, errors_out=errs)
    assert out is not None
    assert calls["n"] == 2          # 1 технический сбой + успех на 2-й
    assert errs == []               # на итоговом успехе ошибку НЕ пишем


# ── A3. контентный сбой → БЕЗ повтора (повтор бесполезен), тип записан ─────────
def test_render_no_retry_on_content(monkeypatch, tmp_path):
    import fal_media as F
    calls = {"n": 0}

    def subscribe(endpoint, **kw):
        calls["n"] += 1
        raise Exception("content_policy_violation: prompt rejected")

    _patch_common(monkeypatch, F, subscribe)
    errs = []
    out = F.generate_kling_video("p", tmp_path / "c.mp4", duration=5, errors_out=errs)
    assert out is None
    assert calls["n"] == 1          # модерация — финал, НЕ повторяем (нет двойного отказа)
    assert errs == ["content"]


# ── A4. УСПЕХ никогда не повторяется — нет двойного списания ───────────────────
def test_render_success_charges_once(monkeypatch, tmp_path):
    import fal_media as F
    calls = {"n": 0}

    def subscribe(endpoint, **kw):
        calls["n"] += 1
        return {"video": {"url": "http://x/v.mp4"}}

    _patch_common(monkeypatch, F, subscribe)
    out = F.generate_kling_video("p", tmp_path / "c.mp4", duration=5)
    assert out is not None
    assert calls["n"] == 1          # ровно один оплачиваемый рендер, повтора нет


# ── A5. технический сбой исчерпал попытки → None + тип "technical" ────────────
def test_render_technical_exhausts_attempts(monkeypatch, tmp_path):
    import fal_media as F
    calls = {"n": 0}

    def subscribe(endpoint, **kw):
        calls["n"] += 1
        raise Exception("internal_server_error")

    _patch_common(monkeypatch, F, subscribe)
    errs = []
    out = F.generate_kling_video("p", tmp_path / "c.mp4", duration=5, errors_out=errs)
    assert out is None
    assert calls["n"] == F.KLING_RENDER_RETRIES     # ровно 2 попытки, не больше
    assert errs == ["technical"]


# ── A6. рендер ок, но скачивание сорвалось → тип "technical" (клип не потерян даром) ─
def test_render_ok_download_fail_is_technical(monkeypatch, tmp_path):
    import fal_media as F
    monkeypatch.setitem(sys.modules, "fal_client",
                        _fake_fal(lambda endpoint, **kw: {"video": {"url": "http://x/v.mp4"}}))
    monkeypatch.setattr(F, "_is_configured", lambda: True)
    monkeypatch.setattr(F.time, "sleep", lambda *a: None)
    monkeypatch.setattr(F, "_download_timeout",
                        lambda url, part: (_ for _ in ()).throw(Exception("HTTP 500")))
    errs = []
    out = F.generate_kling_video("p", tmp_path / "c.mp4", duration=5, errors_out=errs)
    assert out is None
    assert errs == ["technical"]


# ── A7. движок поднимает AiVideoError с категорией по агрегату клипов ──────────
def _patch_ai(monkeypatch, A, cat):
    monkeypatch.setattr(A.fal_media, "kling_ready", lambda: (True, "ok"))

    def fake_kling(prompt, dest, duration=5, aspect="9:16", negative_prompt=None, errors_out=None):
        if errors_out is not None:
            errors_out.append(cat)
        return None

    monkeypatch.setattr(A.fal_media, "generate_kling_video", fake_kling)
    monkeypatch.setattr(A, "plan_clips", lambda *a, **k: [
        {"beat": "x", "prompt": "Multiple shots. p1"},
        {"beat": "y", "prompt": "Multiple shots. p2"}])


def test_engine_raises_content_category(monkeypatch, tmp_path):
    import ai_video_broll as A
    _patch_ai(monkeypatch, A, "content")
    with pytest.raises(A.AiVideoError) as ei:
        A.generate_ai_broll("s", tmp_path, claude=object(), duration=10, target_clips=2)
    assert ei.value.category == "content"


def test_engine_raises_technical_category(monkeypatch, tmp_path):
    import ai_video_broll as A
    _patch_ai(monkeypatch, A, "technical")
    with pytest.raises(A.AiVideoError) as ei:
        A.generate_ai_broll("s", tmp_path, claude=object(), duration=10, target_clips=2)
    assert ei.value.category == "technical"


def test_engine_content_beats_technical_when_mixed(monkeypatch, tmp_path):
    """Если в пачке смешаны типы — content приоритетнее (один сценарий, повтор зря)."""
    import ai_video_broll as A
    monkeypatch.setattr(A.fal_media, "kling_ready", lambda: (True, "ok"))
    seq = ["technical", "content"]
    state = {"i": 0}

    def fake_kling(prompt, dest, duration=5, aspect="9:16", negative_prompt=None, errors_out=None):
        if errors_out is not None:
            errors_out.append(seq[state["i"]])
        state["i"] += 1
        return None

    monkeypatch.setattr(A.fal_media, "generate_kling_video", fake_kling)
    monkeypatch.setattr(A, "plan_clips", lambda *a, **k: [
        {"beat": "x", "prompt": "Multiple shots. p1"},
        {"beat": "y", "prompt": "Multiple shots. p2"}])
    with pytest.raises(A.AiVideoError) as ei:
        A.generate_ai_broll("s", tmp_path, claude=object(), duration=10, target_clips=2)
    assert ei.value.category == "content"


# ── B. кнопки/сообщения восстановления по типу ───────────────────────────────
pytest.importorskip("telegram")


def test_av_fail_keyboard_technical_has_retry():
    from broll import handlers as H
    from broll.draft import SourceMode
    kb = H._av_fail_keyboard("D1", "technical")
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert f"b2src:{SourceMode.AI_VIDEO_GO}:D1" in datas      # «Повторить» есть
    assert f"b2src:{SourceMode.HF_ONLY}:D1" in datas          # графика
    assert f"b2src:{SourceMode.AUTO}:D1" in datas             # библиотека
    assert f"b2src:{SourceMode.AI_VIDEO_MENU}:D1" in datas    # к источникам
    # все callback'и — существующие b2src/b2scr (parse_source_cb их принимает)
    from broll.source_menu import parse_source_cb
    for d in datas:
        if d.startswith("b2src:"):
            assert parse_source_cb(d) != (None, None)


def test_av_fail_keyboard_content_no_retry_edit_instead():
    from broll import handlers as H
    from broll.draft import SourceMode
    kb = H._av_fail_keyboard("D1", "content")
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert f"b2src:{SourceMode.AI_VIDEO_GO}:D1" not in datas   # повтор бесполезен → НЕ даём
    assert "b2scr:edit:D1" in datas                            # ведём на правку сценария
    assert f"b2src:{SourceMode.HF_ONLY}:D1" in datas           # альтернативы остаются


def test_av_fail_text_differs_by_category():
    from broll import handlers as H
    t_tech = H._av_fail_text("technical")
    t_cont = H._av_fail_text("content")
    assert t_tech != t_cont
    assert "модерац" in t_cont.lower()                         # контент → про модерацию
    assert "сторон" in t_tech.lower()                          # техн → «на стороне Kling»
    # техн-текст НЕ обвиняет сценарий юзера, контент-текст — наоборот, ведёт к правке
    assert "сценар" in t_cont.lower()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
