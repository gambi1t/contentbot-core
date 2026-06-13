"""Тест materialize-слоя + валидации загрузок Pipeline 2 (13 июня).

CTO-ревью Critical 2: UI обещает «фото+видео», а ассемблер ест только видео →
сборка падает в конце длинного флоу. Решение: единый materialize-слой
ПЕРЕД assemble — любой BrollItem → mp4 (video/hf passthrough, image → Ken
Burns mp4 переиспользованием video_assembler). Битый item — пропуск, не падение.
+ валидация загрузок (ffprobe): размер, длительность, битость.

Запуск: python tests/test_broll_materialize.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

from broll.draft import BrollItem  # noqa: E402
from broll.materialize import (  # noqa: E402
    materialize_items, validate_upload_media,
    MAX_UPLOAD_BYTES, MIN_VIDEO_SEC,
)


def _assert(cond, msg, errors):
    if not cond:
        errors.append(msg); print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def main():
    errors = []
    work = Path(tempfile.mkdtemp(prefix="broll_mat_"))

    print("\n[materialize_items — роутинг по kind]")
    # фейковый конвертер фото: возвращает .mp4 рядом, не трогая ffmpeg
    def fake_kb(photo_path, out_dir, seg_len):
        p = Path(out_dir) / (Path(photo_path).stem + "_kb.mp4")
        p.write_bytes(b"fake")
        return p

    items = [
        BrollItem(kind="video", origin="library", path="/arch/clip1.mp4"),
        BrollItem(kind="hf_scene", origin="hf", path="/hf/scene_01.mp4"),
        BrollItem(kind="image", origin="upload", path="/up/photo.jpg"),
    ]
    out = materialize_items(items, work, seg_len=5.0, image_converter=fake_kb)
    names = [Path(p).name for p in out]
    _assert(out[0] == Path("/arch/clip1.mp4"), "video — passthrough как есть", errors)
    _assert(out[1] == Path("/hf/scene_01.mp4"), "hf_scene — passthrough", errors)
    _assert(names[2] == "photo_kb.mp4", "image → Ken Burns mp4 через конвертер", errors)
    _assert(len(out) == 3, "все 3 материализованы по порядку", errors)

    print("\n[materialize — битый item пропускается, не падает]")
    def boom_kb(*a, **k):
        raise RuntimeError("ffmpeg сдох на фото")
    items2 = [
        BrollItem(kind="video", origin="library", path="/arch/ok.mp4"),
        BrollItem(kind="image", origin="upload", path="/up/bad.jpg"),
    ]
    out2 = materialize_items(items2, work, seg_len=5.0, image_converter=boom_kb)
    _assert(out2 == [Path("/arch/ok.mp4")],
            "битое фото выброшено, видео осталось", errors)

    print("\n[validate_upload_media — через инъекцию probe]")
    big = work / "big.mp4"; big.write_bytes(b"x" * 10)

    def probe_ok(path):  # 8 сек видео
        return {"duration": 8.0, "codec": "h264"}
    ok, reason = validate_upload_media(big, "video", probe_fn=probe_ok)
    _assert(ok, f"валидное видео проходит ({reason})", errors)

    def probe_short(path):
        return {"duration": 0.4, "codec": "h264"}
    ok, reason = validate_upload_media(big, "video", probe_fn=probe_short)
    _assert(not ok and "коротк" in reason.lower(),
            f"видео <1с отклонено ({reason})", errors)

    def probe_fail(path):
        raise RuntimeError("not a media file")
    ok, reason = validate_upload_media(big, "video", probe_fn=probe_fail)
    _assert(not ok and ("битый" in reason.lower() or "не удалось" in reason.lower()),
            f"битый файл отклонён ({reason})", errors)

    print("\n[validate — размер]")
    huge = work / "huge.mp4"
    huge.write_bytes(b"x" * 16)
    # подменяем размер через stat недоступно — проверяем порог явной функцией
    ok, reason = validate_upload_media(
        huge, "video", probe_fn=probe_ok, size_override=MAX_UPLOAD_BYTES + 1)
    _assert(not ok and ("велик" in reason.lower() or "размер" in reason.lower()),
            f"слишком большой отклонён ({reason})", errors)

    print("\n[validate — image без duration]")
    img = work / "p.jpg"; img.write_bytes(b"x" * 10)
    def probe_img(path):
        return {"width": 1080, "height": 1920}
    ok, reason = validate_upload_media(img, "image", probe_fn=probe_img)
    _assert(ok, f"картинка с размерами проходит ({reason})", errors)

    print("\n[константы заданы]")
    _assert(MAX_UPLOAD_BYTES > 0 and MIN_VIDEO_SEC >= 1.0, "лимиты заданы", errors)

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
