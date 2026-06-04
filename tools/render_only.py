"""Рендер 6 готовых scene_NN.html в /tmp/hf_render_<ts>/hyperframes/hf_NN.mp4.

Запускается ПОСЛЕ build-фазы (когда scene_01..06.html уже на диске). Не вызывает
Claude — только npx hyperframes render. Можно прогонять отдельно от генерации.

После рендера папка структурно совместима с `video_assembler.assemble_auto_montage`
(broll_mode="hf"): кладёшь сюда avatar_*.mp4 и зовёшь assembler — см.
docs/hf_assembler_recipe.md.

Run on server:
  cd /home/maksim-bot/maksim-bot && \\
    sudo -u maksim-bot env HOME=/home/maksim-bot \\
    venv/bin/python tools/render_only.py [out_dir]
"""
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    stream=sys.stdout)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hyperframes_broll as H  # noqa: E402

out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(f"/tmp/hf_render_{int(time.time())}")
out_dir.mkdir(parents=True, exist_ok=True)
print(f"=== RENDER ALL → {out_dir}/hyperframes/ ===")
print(f"source HTMLs in: {H.HF_PROJECT}")

# что вообще есть на входе
for sf in H.SCENE_FILES:
    p = H.HF_PROJECT / sf
    if p.exists():
        print(f"  {sf}: {p.stat().st_size}B")
    else:
        print(f"  {sf}: ОТСУТСТВУЕТ")

t0 = time.time()
clips, errors = H._render_all(out_dir)
dt = time.time() - t0

print(f"\n=== ИТОГ за {dt:.1f}s ({dt/60:.1f} мин) ===")
print(f"clips ({len(clips)}):")
for c in clips:
    sz = c.stat().st_size if c.exists() else 0
    print(f"  {c.name}: {sz} байт ({sz/1024:.0f} KB)")
if errors:
    print(f"errors ({len(errors)}):")
    for e in errors:
        print(f"  - {e[:200]}")
print(f"\nдля сборки финального ролика (split 50/50):")
print(f"  - положи avatar_*.mp4 в {out_dir}/")
print(f"  - python -c \"from video_assembler import assemble_auto_montage; "
      f"from pathlib import Path; "
      f"print(assemble_auto_montage(Path('{out_dir}'), layout='split', "
      f"broll_mode='hf', brand_name='maksim'))\"")
