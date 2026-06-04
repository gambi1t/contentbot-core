/**
 * motion_smoketest.mjs <scene.html> [--strict|--lenient]
 *
 * Phase 1 Step 5 (5 июня 2026, по ревью ChatGPT 4 июня):
 * MD5-3-кадра → perceptual diff по safe-area на 5 точках, 3-уровневый verdict.
 *
 * Ловит сцены где timeline зарегистрирован, но визуально статичен —
 * например, scene_04 в нашем raw-render с seek/progress-bug выглядела
 * идентично от t=0 до t=4.5 (counter застрял на 0%).
 *
 * Алгоритм:
 *   1. Снимаем 5 PNG-кадров по timeline на t=0.2, 0.8, 1.5, 2.5, 4.5.
 *   2. Каждый кадр обрезаем в safe-area (x∈[40,1040], y∈[480,1440]).
 *   3. Для каждой пары (кадр[0], кадр[i]) считаем pixelmatch:
 *        процент пикселей с разницей цвета > threshold.
 *   4. motion = max(diff_from_frame0[1..4]).
 *
 * Verdict:
 *   ok       — motion ≥ STRONG_PCT (явное движение)
 *   warning  — STRONG_PCT > motion ≥ WEAK_PCT (мало, но не ноль —
 *              может быть ambient-hold/CTA по контракту)
 *   fail     — motion < WEAK_PCT (timeline есть, но визуально статично —
 *              почти наверняка onUpdate не срабатывает или ошибка в seek)
 *
 * Exit codes:
 *   0 = ok | warning   (warning не блокирует pipeline)
 *   1 = fail
 *   2 = ошибка запуска / нет timeline
 */
import puppeteer from "puppeteer";
import pixelmatch from "pixelmatch";
import { PNG } from "pngjs";
import { pathToFileURL } from "url";
import path from "path";

// Safe-area из style_contract (синхронизировано с reference_pack.md)
const FRAME_W = 1080;
const FRAME_H = 1920;
const SAFE_X = [40, 1040];
const SAFE_Y = [480, 1440];
const SAFE_W = SAFE_X[1] - SAFE_X[0];   // 1000
const SAFE_H = SAFE_Y[1] - SAFE_Y[0];   // 960
const SAFE_AREA_PIXELS = SAFE_W * SAFE_H;  // 960 000

// Пороги (по эмпирическим замерам на здоровых vs broken сценах 4-5 июня)
const SAMPLES = [0.2, 0.8, 1.5, 2.5, 4.5];
const PIXELMATCH_THRESHOLD = 0.1;   // sensitivity per-pixel (0..1)
const STRONG_PCT = 0.02;             // 2% safe-area = явное движение
const WEAK_PCT   = 0.001;            // 0.1% = почти ничего

function parseArgs(argv) {
  const a = { file: null, mode: "default" };
  const rest = argv.slice(2);
  for (let i = 0; i < rest.length; i++) {
    if (rest[i] === "--strict") a.mode = "strict";
    else if (rest[i] === "--lenient") a.mode = "lenient";
    else if (!a.file) a.file = rest[i];
  }
  return a;
}

function cropToSafeArea(pngBuffer) {
  const png = PNG.sync.read(pngBuffer);
  const out = new PNG({ width: SAFE_W, height: SAFE_H });
  for (let y = 0; y < SAFE_H; y++) {
    for (let x = 0; x < SAFE_W; x++) {
      const srcIdx = ((y + SAFE_Y[0]) * png.width + (x + SAFE_X[0])) * 4;
      const dstIdx = (y * SAFE_W + x) * 4;
      out.data[dstIdx + 0] = png.data[srcIdx + 0];
      out.data[dstIdx + 1] = png.data[srcIdx + 1];
      out.data[dstIdx + 2] = png.data[srcIdx + 2];
      out.data[dstIdx + 3] = png.data[srcIdx + 3];
    }
  }
  return out;
}

async function main() {
  const args = parseArgs(process.argv);
  if (!args.file) {
    console.error("Usage: node motion_smoketest.mjs <scene.html> [--strict|--lenient]");
    process.exit(2);
  }

  const browser = await puppeteer.launch({
    headless: "new",
    args: ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: FRAME_W, height: FRAME_H, deviceScaleFactor: 1 });
  await page.goto(pathToFileURL(path.resolve(args.file)).href,
                  { waitUntil: "networkidle0", timeout: 30000 });
  await page.evaluate(async () => {
    if (document.fonts && document.fonts.ready) await document.fonts.ready;
  });

  // проверяем что timeline вообще существует
  const tlInfo = await page.evaluate(() => {
    const tls = window.__timelines || {};
    const ids = Object.keys(tls);
    const el = document.querySelector("[data-composition-id]");
    const compId = el ? el.getAttribute("data-composition-id") : null;
    return { ids, compId, hasAny: ids.length > 0 };
  });
  if (!tlInfo.hasAny) {
    console.log(JSON.stringify({
      ok: false, verdict: "no_timeline",
      reason: "window.__timelines пуст — композиция не зарегистрировала timeline",
    }, null, 2));
    await browser.close();
    process.exit(2);
  }

  // снимаем 5 кадров
  const frames = [];
  for (const t of SAMPLES) {
    await page.evaluate((id, time) => {
      const tls = window.__timelines || {};
      const tl = id && tls[id] ? tls[id] : Object.values(tls)[0];
      if (!tl) return;
      tl.pause();
      const dur = tl.duration() || 1;
      tl.progress(Math.min(time / dur, 1));
    }, tlInfo.compId, t);
    await new Promise(r => setTimeout(r, 80));
    const buf = await page.screenshot({ type: "png" });
    frames.push({ t, buf, cropped: cropToSafeArea(buf) });
  }
  await browser.close();

  // diff каждого кадра против frame[0] (базовый)
  const base = frames[0].cropped;
  const diffs = [];
  for (let i = 1; i < frames.length; i++) {
    const target = frames[i].cropped;
    const diffPng = new PNG({ width: SAFE_W, height: SAFE_H });
    const changedPixels = pixelmatch(
      base.data, target.data, diffPng.data,
      SAFE_W, SAFE_H, { threshold: PIXELMATCH_THRESHOLD }
    );
    const pct = changedPixels / SAFE_AREA_PIXELS;
    diffs.push({ t: frames[i].t, changed_px: changedPixels, pct });
  }

  const maxPct = Math.max(...diffs.map(d => d.pct));

  // verdict
  let verdict, exitCode, ok;
  const strong = args.mode === "strict" ? STRONG_PCT * 2 : STRONG_PCT;
  const weak = args.mode === "lenient" ? WEAK_PCT / 2 : WEAK_PCT;

  if (maxPct >= strong) {
    verdict = "ok";
    exitCode = 0;
    ok = true;
  } else if (maxPct >= weak) {
    verdict = "warning";
    exitCode = 0;  // warning не блокирует pipeline
    ok = true;
  } else {
    verdict = "fail";
    exitCode = 1;
    ok = false;
  }

  console.log(JSON.stringify({
    ok, verdict,
    file: path.basename(args.file),
    timeline_id: tlInfo.compId,
    samples: SAMPLES,
    diffs,
    max_diff_pct: maxPct,
    thresholds: { strong, weak },
    mode: args.mode,
  }, null, 2));
  process.exit(exitCode);
}

main().catch(e => {
  console.error(`motion_smoketest crashed: ${e.message}`);
  process.exit(2);
});
