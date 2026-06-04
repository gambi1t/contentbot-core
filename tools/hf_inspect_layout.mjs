/**
 * hf_inspect_layout.mjs — детектор layout-дефектов HyperFrames-сцен (v2).
 *
 * Зачем: официальный `hyperframes inspect` (даже 0.6.64 --strict) НЕ ловит
 * (проверено 1 июня 2026 на реальных сценах):
 *   • frame-overflow карточки за край (scene_05: «20%» обрезан)
 *   • text-on-text overlap (scene_01: «выручка по месяцам» налезла на «СЕЗОН»)
 * Поэтому свой детектор на puppeteer-core + chrome-headless-shell.
 *
 * Ловит 3 класса дефектов:
 *   • offscreen — текст ИЛИ карточка (div с фоном/рамкой) выходит за 1080×1920
 *   • overlap   — боксы двух ТЕКСТОВ частично пересекаются (НЕ вложенность:
 *                 подпись внутри карточки или span внутри заголовка — норма)
 *   • crowding  — два текста в одной колонке с вертикальным зазором < MIN_GAP
 *                 (визуально тесно — как «выручка по месяцам» под «СЕЗОН»)
 *
 * Анализ делается ВНУТРИ браузера (page.evaluate), где есть DOM-ancestry
 * (el.contains) — поэтому вложенные пары (заголовок div+span) не дают
 * ложных overlap.
 *
 * Сэмплы времени: «устоявшийся» кадр (вход анимации завершён ~1с, выход
 * начинается ~3с) — по умолчанию 1.5/2.0/2.5с. Дедуп статичных issue.
 *
 * Usage:
 *   HYPERFRAMES_BROWSER_PATH=/path/to/chrome-headless-shell \
 *     node hf_inspect_layout.mjs scene_01.html \
 *       [--samples 1.5,2,2.5] [--tol 2] [--min-overlap 25] [--min-gap 24]
 * Exit: 0 ok, 1 есть issues, 2 ошибка запуска.
 */
import puppeteer from "puppeteer-core";
import path from "path";
import { pathToFileURL } from "url";

const FRAME_W = 1080;
const FRAME_H = 1920;

function parseArgs(argv) {
  const a = { file: null, samples: [1.5, 2.0, 2.5], tol: 2, minOverlap: 25, minGap: 24 };
  const rest = argv.slice(2);
  for (let i = 0; i < rest.length; i++) {
    const t = rest[i];
    if (t === "--samples") a.samples = rest[++i].split(",").map(Number);
    else if (t === "--tol") a.tol = Number(rest[++i]);
    else if (t === "--min-overlap") a.minOverlap = Number(rest[++i]);
    else if (t === "--min-gap") a.minGap = Number(rest[++i]);
    else if (!a.file) a.file = t;
  }
  return a;
}

async function main() {
  const args = parseArgs(process.argv);
  if (!args.file) {
    console.error("Usage: node hf_inspect_layout.mjs <scene.html> [--samples a,b,c] [--tol N] [--min-gap N]");
    process.exit(2);
  }
  const browserPath = process.env.HYPERFRAMES_BROWSER_PATH;
  if (!browserPath) {
    console.error("HYPERFRAMES_BROWSER_PATH не задан");
    process.exit(2);
  }

  const browser = await puppeteer.launch({
    executablePath: browserPath,
    headless: "shell",
    args: ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
  });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: FRAME_W, height: FRAME_H, deviceScaleFactor: 1 });
    const url = pathToFileURL(path.resolve(args.file)).href;
    await page.goto(url, { waitUntil: "networkidle0", timeout: 30000 });
    await page.evaluate(async () => {
      if (document.fonts && document.fonts.ready) await document.fonts.ready;
    });

    const compId = await page.evaluate(() => {
      const el = document.querySelector("[data-composition-id]");
      return el ? el.getAttribute("data-composition-id") : null;
    });

    const perSample = [];
    for (const t of args.samples) {
      await page.evaluate((id, time) => {
        const tls = window.__timelines || {};
        const tl = id && tls[id] ? tls[id] : Object.values(tls)[0];
        if (tl) { tl.pause(); tl.seek(time); }
      }, compId, t);
      await new Promise((r) => setTimeout(r, 80));

      const issues = await page.evaluate(
        (FRAME_W, FRAME_H, TOL, MIN_OVERLAP, MIN_GAP) => {
          function isVisible(el) {
            const s = getComputedStyle(el);
            if (s.display === "none" || s.visibility === "hidden") return false;
            if (parseFloat(s.opacity) < 0.05) return false;
            const r = el.getBoundingClientRect();
            return r.width > 1 && r.height > 1;
          }
          function directText(el) {
            let txt = "";
            for (const n of el.childNodes) if (n.nodeType === 3) txt += n.textContent;
            return txt.trim();
          }
          function hasBgOrBorder(el) {
            const s = getComputedStyle(el);
            const bg = s.backgroundColor;
            const hasBg = bg && bg !== "transparent" && bg !== "rgba(0, 0, 0, 0)";
            const bw = parseFloat(s.borderTopWidth) + parseFloat(s.borderRightWidth)
              + parseFloat(s.borderBottomWidth) + parseFloat(s.borderLeftWidth);
            const hasBorder = bw > 0 && s.borderStyle !== "none";
            return hasBg || hasBorder;
          }
          // G1: эффективная opacity (произведение по предкам). Ghost-декор
          // (reference_pack рекомендует ghost-text 3-8% для глубины фона) —
          // НЕ контент, не должен давать ложный overlap/crowding.
          const GHOST_OPACITY = 0.2;
          function effectiveOpacity(el) {
            let o = 1, cur = el;
            while (cur && cur.nodeType === 1 && cur.tagName !== "HTML") {
              const v = parseFloat(getComputedStyle(cur).opacity);
              if (!isNaN(v)) o *= v;
              cur = cur.parentElement;
            }
            return o;
          }
          // alpha из color (ghost часто тусклый через rgba-цвет, а не opacity —
          // реальный кейс scene_01: color:rgba(255,87,34,0.05), opacity:1).
          function colorAlpha(el) {
            const c = getComputedStyle(el).color;
            const m = c.match(/rgba?\(\s*[\d.]+\s*,\s*[\d.]+\s*,\s*[\d.]+\s*,\s*([\d.]+)\s*\)/);
            return m ? parseFloat(m[1]) : 1;
          }
          function isGhost(el) {
            return effectiveOpacity(el) < GHOST_OPACITY || colorAlpha(el) < GHOST_OPACITY;
          }

          // H1: ближайший styled-предок (карточка/чарт-контейнер), не
          // full-bleed. Используется чтобы исключить crowding между метками
          // ВНУТРИ ОДНОЙ карточки (нормальная табличная вёрстка чарта).
          function closestStyledAncestor(el) {
            let cur = el.parentElement;
            while (cur && cur !== document.body && cur.tagName !== "HTML") {
              if (hasBgOrBorder(cur)) {
                const rr = cur.getBoundingClientRect();
                // карточка должна быть структурным контейнером (не фон-панель)
                if (rr.width > 1 && rr.width < FRAME_W * 0.95) return cur;
              }
              cur = cur.parentElement;
            }
            return null;
          }

          const all = Array.from(document.querySelectorAll("body *"));
          const texts = [];   // {el, r, text, ancestor}
          const cards = [];    // {el, r}  — карточки (фон/рамка, не full-bleed)
          for (const el of all) {
            if (!isVisible(el)) continue;
            const r = el.getBoundingClientRect();
            const txt = directText(el);
            if (txt) texts.push({
              el, r, text: txt.slice(0, 30),
              ancestor: closestStyledAncestor(el),
              ghost: isGhost(el),  // декоративный фон (opacity ИЛИ color-alpha)
            });
            else if (hasBgOrBorder(el)) {
              // карточка = фон/рамка И НЕ full-bleed (исключаем фон-панели и
              // декоративные свечения шириной во весь кадр)
              if (r.width < FRAME_W * 0.95) cards.push({ el, r });
            }
          }

          const issues = [];

          // 1) offscreen — текст И карточки
          const offCandidates = [
            ...texts.map((x) => ({ ...x, kind: "text" })),
            ...cards.map((x) => ({ ...x, kind: "card", text: "" })),
          ];
          for (const c of offCandidates) {
            const off = [];
            if (c.r.left < -TOL) off.push("left");
            if (c.r.right > FRAME_W + TOL) off.push("right");
            if (c.r.top < -TOL) off.push("top");
            if (c.r.bottom > FRAME_H + TOL) off.push("bottom");
            if (off.length) {
              issues.push({
                type: "offscreen", kind: c.kind, edge: off.join("+"),
                text: c.text,
                rect: [Math.round(c.r.left), Math.round(c.r.top), Math.round(c.r.right), Math.round(c.r.bottom)],
              });
            }
          }

          // 2) overlap + 3) crowding — только пары ТЕКСТОВ, не вложенные.
          // Логика С1-fix (signed vertical gap):
          //   g = max(a.top,b.top) - min(a.bottom,b.bottom)
          //     g < 0  → пересечение по Y глубиной |g|
          //     g >= 0 → зазор g между боксами
          //   ix > 0 → горизонтальное пересечение, ix > 20 → "одна колонка"
          //   • если g<0 (пересечение по Y) И ix>0 → overlap (любая ненулевая площадь)
          //   • если g>=0 И ix>20 И g<MIN_GAP → crowding
          for (let i = 0; i < texts.length; i++) {
            for (let j = i + 1; j < texts.length; j++) {
              const A = texts[i], B = texts[j];
              // G1: ghost-декор (тусклый фоновый текст) — не контент, пропускаем.
              if (A.ghost || B.ghost) continue;
              // вложенность по DOM (заголовок div + span, подпись в карточке) — норма
              if (A.el.contains(B.el) || B.el.contains(A.el)) continue;
              const a = A.r, b = B.r;

              const ix = Math.min(a.right, b.right) - Math.max(a.left, b.left);
              const g = Math.max(a.top, b.top) - Math.min(a.bottom, b.bottom);

              // H2: bbox в issue (для дедупа с координатами)
              const boxA = [Math.round(a.left), Math.round(a.top), Math.round(a.right), Math.round(a.bottom)];
              const boxB = [Math.round(b.left), Math.round(b.top), Math.round(b.right), Math.round(b.bottom)];

              if (g < 0 && ix > 0) {
                // overlap (любое реальное пересечение по обеим осям)
                const area = ix * (-g);
                issues.push({
                  type: "overlap", a: A.text, b: B.text,
                  overlapPx: Math.round(area), boxA, boxB,
                });
              } else if (g >= 0 && ix > 20 && g < MIN_GAP) {
                // H1: если оба текста сидят внутри ОДНОЙ карточки/чарта —
                // это структурированная вёрстка (метки таблицы/чарта/легенды),
                // crowding не флагаем.
                if (A.ancestor && A.ancestor === B.ancestor) continue;
                const topFirst = a.top <= b.top;
                issues.push({
                  type: "crowding",
                  a: (topFirst ? A : B).text,
                  b: (topFirst ? B : A).text,
                  gapPx: Math.round(g),
                  boxA: topFirst ? boxA : boxB,
                  boxB: topFirst ? boxB : boxA,
                });
              }
            }
          }
          return issues;
        },
        FRAME_W, FRAME_H, args.tol, args.minOverlap, args.minGap
      );

      perSample.push(issues);
    }

    // дедуп статичных issue между сэмплами. H2-fix: ключи overlap/crowding
    // включают округлённые bbox ОБЕИХ коробок — иначе разные пары с одинаковым
    // текстом (₽ФИКС / 100%) ошибочно схлопываются в одну.
    const seen = new Set();
    const deduped = [];
    function boxKey(is) {
      // упорядочиваем bbox по top-left, чтобы порядок (a,b)/(b,a) не давал разных ключей
      const a = (is.boxA || []).join(",");
      const b = (is.boxB || []).join(",");
      return a < b ? `${a}|${b}` : `${b}|${a}`;
    }
    for (const issues of perSample) {
      for (const is of issues) {
        let key;
        if (is.type === "offscreen") key = `off|${is.kind}|${is.text}|${is.edge}|${is.rect.join(",")}`;
        else if (is.type === "overlap") key = `ov|${[is.a, is.b].sort().join("|")}|${boxKey(is)}`;
        else key = `cr|${[is.a, is.b].sort().join("|")}|${boxKey(is)}`;
        if (seen.has(key)) continue;
        seen.add(key);
        deduped.push(is);
      }
    }

    const result = {
      ok: deduped.length === 0,
      count: deduped.length,
      file: path.basename(args.file),
      samples: args.samples,
      issues: deduped,
    };
    console.log(JSON.stringify(result, null, 2));
    await browser.close();
    process.exit(deduped.length === 0 ? 0 : 1);
  } catch (e) {
    try { await browser.close(); } catch {}
    console.error("INSPECT_ERROR: " + (e && e.message ? e.message : String(e)));
    process.exit(2);
  }
}

main();
