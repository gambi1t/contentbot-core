"""TDD: ретрай VK-аплоада на транзиентные ошибки (Артём 28.06: error 10).

  * error 10 (Internal server error) → ретраим, при успехе возвращаем клип;
  * error 5 (auth failed) → НЕ ретраим (нужна переавторизация);
  * после MAX_ATTEMPTS транзиентных — сдаёмся (None).

Запуск: python -m pytest tests/test_vk_retry.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
os.environ.setdefault("TELEGRAM_TOKEN", "dummy")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import crosspost as cp  # noqa: E402


class _Resp:
    def __init__(self, status=200, js=None, text=""):
        self.status_code = status
        self._js = js or {}
        self.text = text

    def json(self):
        return self._js


def _setup(monkeypatch, tmp_path):
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"x")
    monkeypatch.setattr(cp, "_vk_get_valid_token", lambda: "tok")
    monkeypatch.setattr(cp.time, "sleep", lambda s: None)  # без реальных пауз
    return vid


def test_vk_retries_on_error10_then_succeeds(monkeypatch, tmp_path):
    vid = _setup(monkeypatch, tmp_path)
    calls = {"save": 0}

    def fake_get(url, **kw):
        calls["save"] += 1
        if calls["save"] < 3:  # первые 2 — транзиентная ошибка 10
            return _Resp(js={"error": {"error_code": 10, "error_msg": "Internal server error"}})
        return _Resp(js={"response": {"upload_url": "http://up", "video_id": 1, "owner_id": 2}})

    monkeypatch.setattr(cp.requests, "get", fake_get)
    monkeypatch.setattr(cp.requests, "post", lambda url, **kw: _Resp(status=200))
    res = cp.vk_upload_clip(str(vid), "desc")
    assert res is not None and res["platform"] == "vk", "error 10 не отретраен"
    assert calls["save"] == 3, f"ожидалось 3 попытки save, было {calls['save']}"


def test_vk_no_retry_on_auth_error5(monkeypatch, tmp_path):
    vid = _setup(monkeypatch, tmp_path)
    calls = {"save": 0}

    def fake_get(url, **kw):
        calls["save"] += 1
        return _Resp(js={"error": {"error_code": 5, "error_msg": "User authorization failed"}})

    monkeypatch.setattr(cp.requests, "get", fake_get)
    res = cp.vk_upload_clip(str(vid), "desc")
    assert res is None
    assert calls["save"] == 1, f"auth(5) нельзя ретраить, было {calls['save']} попыток"


def test_vk_gives_up_after_max_attempts(monkeypatch, tmp_path):
    vid = _setup(monkeypatch, tmp_path)
    calls = {"save": 0}

    def fake_get(url, **kw):
        calls["save"] += 1
        return _Resp(js={"error": {"error_code": 10, "error_msg": "ISE"}})

    monkeypatch.setattr(cp.requests, "get", fake_get)
    res = cp.vk_upload_clip(str(vid), "desc")
    assert res is None
    assert calls["save"] == 3, f"должно быть ровно MAX_ATTEMPTS=3, было {calls['save']}"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
