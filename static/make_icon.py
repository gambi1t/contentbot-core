"""Generate a 1024x1024 PNG icon for the Meta app.

Clean black background with a big white "P" in the center — simple, readable,
meets Meta's 512-1024 icon requirements.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

SIZE = 1024
BG = (15, 15, 20)          # near-black
FG = (255, 255, 255)       # white
ACCENT = (255, 140, 0)     # orange accent dot

OUT = Path(__file__).parent / "app_icon_1024.png"

img = Image.new("RGB", (SIZE, SIZE), BG)
draw = ImageDraw.Draw(img)

# Try several fonts in order — use whatever is on the system.
font = None
for candidate in [
    "C:/Windows/Fonts/segoeuib.ttf",    # Segoe UI Bold
    "C:/Windows/Fonts/arialbd.ttf",     # Arial Bold
    "C:/Windows/Fonts/calibrib.ttf",    # Calibri Bold
]:
    try:
        font = ImageFont.truetype(candidate, 760)
        break
    except Exception:
        continue
if font is None:
    font = ImageFont.load_default()

text = "P"
# Measure with textbbox for accurate centering
bbox = draw.textbbox((0, 0), text, font=font)
tw = bbox[2] - bbox[0]
th = bbox[3] - bbox[1]
x = (SIZE - tw) // 2 - bbox[0]
y = (SIZE - th) // 2 - bbox[1] - 30  # slight optical lift

draw.text((x, y), text, font=font, fill=FG)

# Small orange accent dot — bottom right of the P, signature touch
dot_r = 60
cx = SIZE - 220
cy = SIZE - 260
draw.ellipse((cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r), fill=ACCENT)

img.save(OUT, "PNG", optimize=True)
print(f"Saved: {OUT}  size={OUT.stat().st_size} bytes  dims={img.size}")
