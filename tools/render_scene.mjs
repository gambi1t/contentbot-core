/**
 * render_scene.mjs <scene.html> <out_dir>
 * Рендерит сцену в PNG-кадры 1080×1920, 30fps × 5s = 150 frames.
 * Дальше ffmpeg склеит в mp4.
 */
import puppeteer from "puppeteer";
import { pathToFileURL } from "url";
import path from "path";
import fs from "fs";

const scene = process.argv[2];
const outDir = process.argv[3];
const duration = parseFloat(process.argv[4] || "5");
const fps = parseInt(process.argv[5] || "30");
const frames = Math.round(duration * fps);

if (!scene || !outDir) {
  console.error("Usage: node render_scene.mjs <scene.html> <out_dir> [duration=5] [fps=30]");
  process.exit(2);
}
if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });

const t0 = Date.now();
const browser = await puppeteer.launch({
  headless: "new",
  args: ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1080, height: 1920, deviceScaleFactor: 1 });
await page.goto(pathToFileURL(path.resolve(scene)).href, { waitUntil: "networkidle0", timeout: 30000 });
await page.evaluate(async () => { if (document.fonts?.ready) await document.fonts.ready; });
const compId = await page.evaluate(() => {
  const el = document.querySelector("[data-composition-id]");
  return el ? el.getAttribute("data-composition-id") : null;
});

for (let i = 0; i < frames; i++) {
  const t = i / fps;
  await page.evaluate((id, time) => {
    const tls = window.__timelines || {};
    const tl = id && tls[id] ? tls[id] : Object.values(tls)[0];
    if (!tl) return;
    tl.pause();
    // progress() вместо seek() — гарантированно триггерит onUpdate всех
    // child-tween'ов, включая tween-of-plain-object (scene_04 gauge fillObj).
    // seek() для plain-object tween может НЕ вызывать onUpdate, и кадр
    // выглядит как t=0 даже на t=4.5 (баг показался Артёму 4 июня).
    const dur = tl.duration() || 1;
    tl.progress(Math.min(time / dur, 1));
  }, compId, t);
  const fname = `frame_${String(i).padStart(4, "0")}.png`;
  await page.screenshot({ path: path.join(outDir, fname), type: "png" });
}
await browser.close();
const dt = ((Date.now() - t0) / 1000).toFixed(1);
console.log(`${frames} frames in ${dt}s → ${outDir}`);
