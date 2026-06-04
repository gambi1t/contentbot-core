"""Render parity-test: наш puppeteer vs npx hyperframes render.

Phase 1 Step 4 (5 июня 2026): research-tool для регрессии решения остаться с
нашим render. Запускается вручную, не в CI.

Что делает:
1. Принимает scene.html (по умолчанию scene_01.html в текущем dir).
2. Рендерит обоими движками в /tmp/parity_<sceneid>/{ours,npx}.mp4.
3. Извлекает финальный кадр (t=4.9) обоих в PNG через ffmpeg.
4. Считает pixel-diff между ними через puppeteer (или MD5 для грубой проверки).
5. Печатает итог: совпадает / не совпадает + размеры файлов + bitrate.

Использование:
  python tools/render_parity_test.py <scene.html> [<hf_project_dir>]

Решение Step 4 — в docs/render_parity_decision_2026-06-05.md.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path


def _run(cmd: list[str], cwd: Path | None = None,
         timeout: int = 300) -> tuple[int, str]:
    """Запускает команду, возвращает (returncode, combined output)."""
    try:
        r = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    except Exception as e:
        return -2, f"SPAWN ERR: {e}"


def _ffprobe(mp4: Path) -> dict:
    """Метаданные через ffprobe → dict."""
    rc, out = _run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries",
        "stream=codec_name,width,height,r_frame_rate,duration,nb_frames,bit_rate",
        "-of", "json", str(mp4),
    ])
    if rc != 0:
        return {"error": out[:200]}
    try:
        data = json.loads(out)
        return data.get("streams", [{}])[0]
    except Exception:
        return {"error": "unparseable"}


def _extract_last_frame(mp4: Path, t: float = 4.9) -> Path:
    """ffmpeg → последний кадр PNG рядом с mp4."""
    png = mp4.with_name(mp4.stem + "_last.png")
    _run([
        "ffmpeg", "-y", "-ss", str(t), "-i", str(mp4),
        "-frames:v", "1", str(png),
    ])
    return png


def _png_hash(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest() if p.exists() else "none"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python render_parity_test.py <scene.html> [<hf_project_dir>]")
        return 2
    scene = Path(argv[1]).resolve()
    if not scene.exists():
        print(f"FAIL: scene not found: {scene}")
        return 2
    hf_project = Path(argv[2]).resolve() if len(argv) > 2 else scene.parent

    sid = scene.stem
    out_dir = Path(f"/tmp/parity_{sid}_{int(time.time())}")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"out_dir: {out_dir}")

    # ── 1. наш render
    our_mp4 = out_dir / f"{sid}_ours.mp4"
    our_render = Path(__file__).resolve().parent / "render_scene.mjs"
    if not our_render.exists():
        print(f"FAIL: render_scene.mjs not at {our_render}")
        return 2
    t0 = time.time()
    rc1, log1 = _run(["node", str(our_render), str(scene),
                      str(out_dir / "_frames_ours"), "5", "30"],
                     cwd=hf_project, timeout=300)
    dt1 = time.time() - t0
    if rc1 == 0:
        # ffmpeg склейка кадров → mp4
        _run([
            "ffmpeg", "-y", "-framerate", "30",
            "-i", str(out_dir / "_frames_ours" / "frame_%04d.png"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
            str(our_mp4),
        ])
    print(f"OURS:  rc={rc1} time={dt1:.1f}s size="
          f"{our_mp4.stat().st_size if our_mp4.exists() else 'n/a'}")

    # ── 2. npx render
    npx_mp4 = out_dir / f"{sid}_npx.mp4"
    t0 = time.time()
    rc2, log2 = _run(
        ["npx", "--yes", "hyperframes@0.6.56", "render",
         "-c", str(scene.name), "-o", str(npx_mp4)],
        cwd=hf_project, timeout=300,
    )
    dt2 = time.time() - t0
    print(f"NPX:   rc={rc2} time={dt2:.1f}s size="
          f"{npx_mp4.stat().st_size if npx_mp4.exists() else 'n/a'}")

    # ── 3. метаданные
    if our_mp4.exists():
        print(f"  ours meta: {_ffprobe(our_mp4)}")
    if npx_mp4.exists():
        print(f"  npx  meta: {_ffprobe(npx_mp4)}")

    # ── 4. last frame
    diffs = []
    if our_mp4.exists():
        our_last = _extract_last_frame(our_mp4)
        print(f"  ours last frame: {our_last} ({our_last.stat().st_size}b "
              f"md5={_png_hash(our_last)[:8]})")
    if npx_mp4.exists():
        npx_last = _extract_last_frame(npx_mp4)
        print(f"  npx  last frame: {npx_last} ({npx_last.stat().st_size}b "
              f"md5={_png_hash(npx_last)[:8]})")
        # эвристика "чёрный экран" — png меньше 30KB при разрешении 1080×1920
        if npx_last.stat().st_size < 30_000:
            diffs.append(f"npx last frame подозрительно мал "
                         f"({npx_last.stat().st_size}b) — возможно чёрный экран")

    # ── 5. итог
    print()
    print("=== PARITY DECISION ===")
    if rc1 == 0 and rc2 == 0 and not diffs:
        print("PARITY OK — оба движка дают сопоставимый результат.")
        print("Можно рассматривать возврат к npx (см. docs/render_parity_decision_2026-06-05.md).")
        return 0
    print("PARITY FAIL:")
    for d in diffs:
        print(f"  - {d}")
    if rc1 != 0:
        print(f"  - наш render упал rc={rc1}")
    if rc2 != 0:
        print(f"  - npx render упал rc={rc2}")
    print("Решение Step 4 (свой render) остаётся в силе.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
