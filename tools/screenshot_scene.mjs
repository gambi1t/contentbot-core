/**
 * screenshot_scene.mjs <scene.html> <time_seconds> [outpath]
 * Снимок сцены 1080×1920 в указанный момент timeline.
 */
import puppeteer from "puppeteer";
import { pathToFileURL } from "url";
import path from "path";

const scene = process.argv[2];
const time = parseFloat(process.argv[3] || "2.5");
const out = process.argv[4] || `D:/AI/hf_local_diag/_shot_${path.basename(scene, '.html')}_${time}s.png`;

const browser = await puppeteer.launch({
  headless: "new",
  args: ["--no-sandbox", "--disable-gpu"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1080, height: 1920, deviceScaleFactor: 1 });
await page.goto(pathToFileURL(path.resolve(scene)).href, { waitUntil: "networkidle0", timeout: 30000 });
await page.evaluate(async () => { if (document.fonts?.ready) await document.fonts.ready; });
const compId = await page.evaluate(() => {
  const el = document.querySelector("[data-composition-id]");
  return el ? el.getAttribute("data-composition-id") : null;
});
await page.evaluate((id, t) => {
  const tls = window.__timelines || {};
  const tl = (id && tls[id]) ? tls[id] : Object.values(tls)[0];
  if (!tl) return;
  tl.pause();
  const dur = tl.duration() || 1;
  tl.progress(Math.min(t / dur, 1));  // progress() триггерит onUpdate, seek() — нет
}, compId, time);
await new Promise(r => setTimeout(r, 200));
await page.screenshot({ path: out, fullPage: false });
console.log(out);
await browser.close();
