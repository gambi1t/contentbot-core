"""Тесты IG-карусели (10 июня): crosspost.convert_pngs_to_jpegs +
instagram_upload_carousel.

Покрывает:
- convert_pngs_to_jpegs — PNG (с альфой) → валидный JPEG RGB, белый фон,
  сохранение порядка, пропуск отсутствующих.
- instagram_upload_carousel — валидация 2-10; оркестрация child→parent→
  publish с мок-requests (children join, creation_id, возврат media_id);
  обработка ошибки контейнера.

Стиль: без pytest, main() → 0/1 (как test_carousel_surgical_helpers.py).
Запуск: python tests/test_instagram_carousel.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

import crosspost  # noqa: E402
from PIL import Image  # noqa: E402


def _assert(cond: bool, msg: str, errors: list) -> None:
    if not cond:
        errors.append(msg)
        print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


# ── Fake requests ────────────────────────────────────────────────────────
class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


class _FakeRequests:
    """Мокает crosspost.requests: считает child/parent/publish вызовы."""
    def __init__(self):
        self.posts = []
        self.child_counter = 0
        self.parent_id = "PARENT_123"
        self.published_id = "MEDIA_PUB_999"
        self.last_children = None
        self.last_caption = None
        self.fail_child_idx = None  # для теста ошибки

    def post(self, url, data=None, timeout=None):
        self.posts.append((url, data))
        d = data or {}
        if d.get("is_carousel_item") == "true":
            # child container
            if self.fail_child_idx is not None and self.child_counter == self.fail_child_idx:
                return _FakeResp(400, {"error": "bad image"})
            self.child_counter += 1
            return _FakeResp(200, {"id": f"CHILD_{self.child_counter}"})
        if d.get("media_type") == "CAROUSEL":
            self.last_children = d.get("children")
            self.last_caption = d.get("caption")
            return _FakeResp(200, {"id": self.parent_id})
        if url.endswith("/media_publish"):
            return _FakeResp(200, {"id": self.published_id})
        return _FakeResp(200, {"id": "UNKNOWN"})

    def get(self, url, params=None, timeout=None):
        # parent status poll → сразу FINISHED
        return _FakeResp(200, {"status_code": "FINISHED", "status": "ok"})


def test_convert_pngs_to_jpegs(errors: list) -> None:
    print("\n[convert_pngs_to_jpegs]")
    tmp = Path(tempfile.mkdtemp(prefix="jpegtest_"))
    # PNG c альфой (полупрозрачный) + обычный
    p1 = tmp / "slide_01.png"
    Image.new("RGBA", (200, 250), (255, 0, 0, 128)).save(str(p1))
    p2 = tmp / "slide_02.png"
    Image.new("RGB", (200, 250), (0, 128, 0)).save(str(p2))
    missing = tmp / "nope.png"

    out = tmp / "jpg"
    res = crosspost.convert_pngs_to_jpegs([p1, p2, missing], out, quality=90)

    _assert(len(res) == 2, f"2 JPEG из 2 существующих PNG (missing пропущен), got {len(res)}", errors)
    _assert(all(p.suffix == ".jpg" for p in res), "расширение .jpg", errors)
    _assert([p.stem for p in res] == ["slide_01", "slide_02"], "порядок и имена сохранены", errors)
    if res:
        im = Image.open(str(res[0]))
        _assert(im.mode == "RGB", f"JPEG в RGB (без альфы), got {im.mode}", errors)
        _assert(im.format == "JPEG", f"формат JPEG, got {im.format}", errors)


def test_carousel_validation(errors: list) -> None:
    print("\n[instagram_upload_carousel — валидация]")
    fake = _FakeRequests()
    orig_req = crosspost.requests
    orig_tok = crosspost._get_instagram_access_token
    orig_uid = crosspost._get_instagram_user_id
    crosspost.requests = fake
    crosspost._get_instagram_access_token = lambda: "TOKEN"
    crosspost._get_instagram_user_id = lambda: "IG_USER"
    try:
        _assert(crosspost.instagram_upload_carousel(["u1"], "c") is None,
                "1 изображение → None (нужно ≥2)", errors)
        _assert(crosspost.instagram_upload_carousel([f"u{i}" for i in range(11)], "c") is None,
                "11 изображений → None (макс 10)", errors)
        _assert(crosspost.instagram_upload_carousel([], "c") is None,
                "0 изображений → None", errors)
    finally:
        crosspost.requests = orig_req
        crosspost._get_instagram_access_token = orig_tok
        crosspost._get_instagram_user_id = orig_uid


def test_carousel_happy_path(errors: list) -> None:
    print("\n[instagram_upload_carousel — happy path 5 фото]")
    fake = _FakeRequests()
    orig_req = crosspost.requests
    orig_tok = crosspost._get_instagram_access_token
    orig_uid = crosspost._get_instagram_user_id
    crosspost.requests = fake
    crosspost._get_instagram_access_token = lambda: "TOKEN"
    crosspost._get_instagram_user_id = lambda: "IG_USER"
    try:
        urls = [f"https://m/slide_{i}.jpg" for i in range(5)]
        res = crosspost.instagram_upload_carousel(urls, "Подпись поста")
        _assert(res is not None and res.get("id") == "MEDIA_PUB_999",
                f"вернул media_id опубликованного поста, got {res}", errors)
        _assert(res and res.get("platform") == "instagram", "platform=instagram", errors)
        _assert(fake.child_counter == 5, f"создано 5 child-контейнеров, got {fake.child_counter}", errors)
        _assert(fake.last_children == "CHILD_1,CHILD_2,CHILD_3,CHILD_4,CHILD_5",
                f"children join по порядку, got {fake.last_children}", errors)
        _assert(fake.last_caption == "Подпись поста", "caption проброшен", errors)
    finally:
        crosspost.requests = orig_req
        crosspost._get_instagram_access_token = orig_tok
        crosspost._get_instagram_user_id = orig_uid


def test_carousel_child_failure(errors: list) -> None:
    print("\n[instagram_upload_carousel — ошибка child-контейнера]")
    fake = _FakeRequests()
    fake.fail_child_idx = 2  # третий child падает
    orig_req = crosspost.requests
    orig_tok = crosspost._get_instagram_access_token
    orig_uid = crosspost._get_instagram_user_id
    crosspost.requests = fake
    crosspost._get_instagram_access_token = lambda: "TOKEN"
    crosspost._get_instagram_user_id = lambda: "IG_USER"
    try:
        res = crosspost.instagram_upload_carousel([f"u{i}.jpg" for i in range(5)], "c")
        _assert(res is None, "ошибка child → None (не публикуем частично)", errors)
    finally:
        crosspost.requests = orig_req
        crosspost._get_instagram_access_token = orig_tok
        crosspost._get_instagram_user_id = orig_uid


def test_carousel_empty_media_id(errors: list) -> None:
    print("\n[instagram_upload_carousel — publish 200 без id → None]")
    fake = _FakeRequests()
    fake.published_id = ""  # 200, но id пустой
    orig_req = crosspost.requests
    orig_tok = crosspost._get_instagram_access_token
    orig_uid = crosspost._get_instagram_user_id
    crosspost.requests = fake
    crosspost._get_instagram_access_token = lambda: "TOKEN"
    crosspost._get_instagram_user_id = lambda: "IG_USER"
    try:
        res = crosspost.instagram_upload_carousel([f"u{i}.jpg" for i in range(3)], "c")
        _assert(res is None, f"пустой media_id → None (не {{'id': None}}), got {res}", errors)
    finally:
        crosspost.requests = orig_req
        crosspost._get_instagram_access_token = orig_tok
        crosspost._get_instagram_user_id = orig_uid


def main() -> int:
    errors: list = []
    test_convert_pngs_to_jpegs(errors)
    test_carousel_validation(errors)
    test_carousel_happy_path(errors)
    test_carousel_child_failure(errors)
    test_carousel_empty_media_id(errors)
    print()
    if errors:
        print(f"❌ FAIL — {len(errors)} ошибок:")
        for e in errors:
            print(f"   - {e}")
        return 1
    print("✅ ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
