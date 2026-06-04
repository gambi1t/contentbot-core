/**
 * motion_smoketest.mjs <scene.html>
 *
 * Mountain test: timeline проигрывается реально или элементы статичны?
 * Снимает кадры на t=0.5 / 2.5 / 4.5, вычисляет MD5-хэш каждого PNG, сравнивает.
 * Если все 3 хэша одинаковые → анимация СЛОМАНА (timeline pause+set state не
 * меняется при seek/progress). Если хотя бы 2 разные — анимация играет.
 *
 * Exit 0 = ok (motion detected). Exit 1 = static (animation broken).
 * Exit 2 = launch error.
 *
 * Заранее ловит баги вроде «scene_04 counter застрял на 0%» — где детектор
 * layout-нарушений ничего не видит (всё внутри кадра), но кадры идентичны.
 */
import puppeteer from "puppeteer";
import { pathToFileURL } from "url";
import path from "path";
import crypto from "crypto";

const scene = process.argv[2];
if (!scene) {
  console.error("Usage: node motion_smoketest.mjs <scene.html>");
  process.exit(2);
}

const TIMES = [0.5, 2.5, 4.5];
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

const hashes = [];
for (const t of TIMES) {
  await page.evaluate((id, time) => {
    const tls = window.__timelines || {};
    const tl = id && tls[id] ? tls[id] : Object.values(tls)[0];
    if (!tl) return;
    tl.pause();
    const dur = tl.duration() || 1;
    tl.progress(Math.min(time / dur, 1));
  }, compId, t);
  await new Promise(r => setTimeout(r, 80));
  const buf = await page.screenshot({ type: "png" });
  const h = crypto.createHash("md5").update(buf).digest("hex");
  hashes.push({ t, hash: h, size: buf.length });
}
await browser.close();

const uniq = new Set(hashes.map(x => x.hash));
const result = {
  scene: path.basename(scene),
  samples: hashes,
  unique_frames: uniq.size,
  motion_detected: uniq.size > 1,
};
console.log(JSON.stringify(result, null, 2));
process.exit(uniq.size > 1 ? 0 : 1);
