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
  // GPT-5 review 2026-06-23 Steps 2-3: тонкие thresholds для tiny_text + hard_overlap.
  // Дефолты — пол читаемости для 1080×1920 видео (можно тенант-override).
  const a = {
    file: null,
    samples: [1.5, 2.0, 2.5],
    tol: 2,
    minOverlap: 25,
    minGap: 24,
    // tiny_text floors (px) по ролям
    heroMin: 60,
    headingMin: 48,
    bodyMin: 28,
    captionMin: 18,
    ctaMin: 32,
    // hard_overlap: либо >= absPx, либо >= ratio × min(area_a, area_b)
    hardOverlapAbsPx: 300,
    hardOverlapRatio: 0.08,
  };
  const rest = argv.slice(2);
  for (let i = 0; i < rest.length; i++) {
    const t = rest[i];
    if (t === "--samples") a.samples = rest[++i].split(",").map(Number);
    else if (t === "--tol") a.tol = Number(rest[++i]);
    else if (t === "--min-overlap") a.minOverlap = Number(rest[++i]);
    else if (t === "--min-gap") a.minGap = Number(rest[++i]);
    else if (t === "--hero-min") a.heroMin = Number(rest[++i]);
    else if (t === "--heading-min") a.headingMin = Number(rest[++i]);
    else if (t === "--body-min") a.bodyMin = Number(rest[++i]);
    else if (t === "--caption-min") a.captionMin = Number(rest[++i]);
    else if (t === "--cta-min") a.ctaMin = Number(rest[++i]);
    else if (t === "--hard-overlap-abs") a.hardOverlapAbsPx = Number(rest[++i]);
    else if (t === "--hard-overlap-ratio") a.hardOverlapRatio = Number(rest[++i]);
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
        (FRAME_W, FRAME_H, TOL, MIN_OVERLAP, MIN_GAP, MINS, HARD) => {
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

          // ── GPT-5 Step 2-3: role resolver + effective font px + decor ────
          // Role каскад: 1) data-hf-role атрибут → 2) class имена → 3) tag fallback.
          // (4-й уровень length+prominence добавим позже если будет шум на (1-3).)
          const ROLE_CLASSES = {
            hero: ["hero", "headline", "h-hero"],
            heading: ["title", "subtitle", "h-title", "section-title", "kicker-title"],
            cta: ["cta", "button", "btn", "cta-button", "cta-text"],
            caption: ["caption", "kicker", "eyebrow", "label", "meta", "sub", "subtitle-text"],
            body: ["body", "lead", "text", "copy", "p-body"],
            decor: ["decor", "ghost", "ambient", "bg-text", "ghost-text"],
          };
          function resolveRole(el) {
            // 1. data-hf-role
            const explicit = el.getAttribute && el.getAttribute("data-hf-role");
            if (explicit) return explicit.toLowerCase();
            // 1b. ancestor data-hf-role (CTA-карточка содержит span с CTA-текстом)
            const anc = el.closest && el.closest("[data-hf-role]");
            if (anc) return anc.getAttribute("data-hf-role").toLowerCase();
            // 2. classes
            const cl = el.classList || [];
            for (const role in ROLE_CLASSES) {
              for (const cls of ROLE_CLASSES[role]) {
                if (cl.contains && cl.contains(cls)) return role;
              }
            }
            // 3. tag fallback
            const tag = (el.tagName || "").toLowerCase();
            if (tag === "h1") return "hero";
            if (tag === "h2") return "heading";
            if (tag === "h3" || tag === "h4") return "heading";
            if (tag === "p" || tag === "li") return "body";
            if (tag === "small") return "caption";
            return "body";  // безопасный дефолт
          }

          function transformScaleY(el) {
            // Кумулятивный scaleY по transform-цепочке предков (matrix или scale).
            let s = 1, cur = el;
            while (cur && cur.nodeType === 1 && cur.tagName !== "HTML") {
              const tr = getComputedStyle(cur).transform;
              if (tr && tr !== "none") {
                // matrix(a,b,c,d,e,f) — d = scaleY; matrix3d(...) — sy = m22 (idx 5)
                const m = tr.match(/^matrix\(([^)]+)\)$/);
                if (m) {
                  const v = m[1].split(",").map(parseFloat);
                  if (v.length >= 4 && !isNaN(v[3])) s *= Math.abs(v[3]);
                } else {
                  const m3 = tr.match(/^matrix3d\(([^)]+)\)$/);
                  if (m3) {
                    const v = m3[1].split(",").map(parseFloat);
                    if (v.length >= 6 && !isNaN(v[5])) s *= Math.abs(v[5]);
                  }
                }
              }
              cur = cur.parentElement;
            }
            return s;
          }

          function effectiveFontPx(el) {
            // computedFontSize × кумулятивный transform-scaleY (не rect.height —
            // у многострочного блока высота != размер шрифта; GPT-5 Critical 3).
            const fs = parseFloat(getComputedStyle(el).fontSize) || 0;
            return fs * transformScaleY(el);
          }

          function isDecor(el) {
            // явная разметка: data-hf-role="decor" / data-hf-allow-offscreen
            const r = el.getAttribute && el.getAttribute("data-hf-role");
            if (r && r.toLowerCase() === "decor") return true;
            if (el.getAttribute && el.getAttribute("data-hf-allow-offscreen") === "true") return true;
            // или классово декоративный
            const cl = el.classList || [];
            for (const cls of ROLE_CLASSES.decor) if (cl.contains && cl.contains(cls)) return true;
            return false;
          }

          // G2 (13 июня): фрагменты ОДНОГО текстового блока — слова/строки
          // заголовка, разбитого на span'ы для анимации появления — это НЕ
          // независимые тексты; их межсловный/межстрочный зазор не дефект.
          // КЛАСС-АГНОСТИЧНО: имена контейнеров (.hero/.cta/.title/div) LLM
          // варьирует, поэтому НЕ перечисляем их, а идём по split-цепочке
          // (.word/.w/.line/.char/.tk) вверх до первого НЕ-split предка =
          // контейнер всего блока. Это устойчиво к новым классам сцен.
          const SPLIT_CLASSES = ["word", "w", "line", "char", "letter",
                                 "tk", "token", "frag", "seg"];
          function isSplitPiece(el) {
            return !!(el && el.classList
              && SPLIT_CLASSES.some((c) => el.classList.contains(c)));
          }
          function textBlockRoot(el) {
            const explicit = el.closest("[data-hf-text-block]");
            if (explicit) return explicit;
            if (isSplitPiece(el)) {
              let cur = el;
              while (cur.parentElement && isSplitPiece(cur)) cur = cur.parentElement;
              return cur;  // первый не-split предок = текст-блок целиком
            }
            // не split-кусок — семантический контейнер по тегам/частым классам
            return el.closest("h1,h2,h3,p,.hero,.title,.headline,.caption,.cta,.label");
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
          const texts = [];   // {el, r, text, ancestor, role, fontPx, decor}
          const cards = [];    // {el, r, decor}
          for (const el of all) {
            if (!isVisible(el)) continue;
            const r = el.getBoundingClientRect();
            const txt = directText(el);
            if (txt) texts.push({
              el, r, text: txt.slice(0, 30),
              ancestor: closestStyledAncestor(el),
              blockRoot: textBlockRoot(el),  // общий текст-блок (G2)
              ghost: isGhost(el),  // декоративный фон (opacity ИЛИ color-alpha)
              role: resolveRole(el),         // GPT-5 Step 3: hero|heading|body|caption|cta|decor
              fontPx: effectiveFontPx(el),    // computedFontSize × transformScaleY
              decor: isDecor(el),             // explicit data-hf-role="decor" / allow-offscreen
            });
            else if (hasBgOrBorder(el)) {
              if (r.width < FRAME_W * 0.95) cards.push({ el, r, decor: isDecor(el) });
            }
          }

          const issues = [];

          // 1) offscreen — текст И карточки. severity: blocking для semantic,
          //    advisory для decor (явный data-hf-role="decor" / allow-offscreen).
          const offCandidates = [
            ...texts.map((x) => ({ ...x, kind: "text" })),
            ...cards.map((x) => ({ ...x, kind: "card", text: "", role: "decor" })),
          ];
          for (const c of offCandidates) {
            const off = [];
            if (c.r.left < -TOL) off.push("left");
            if (c.r.right > FRAME_W + TOL) off.push("right");
            if (c.r.top < -TOL) off.push("top");
            if (c.r.bottom > FRAME_H + TOL) off.push("bottom");
            if (off.length) {
              const isSemantic = (c.kind === "text" && !c.decor && c.role !== "decor")
                              || (c.kind === "card" && !c.decor);
              issues.push({
                type: "offscreen",
                severity: isSemantic ? "blocking" : "advisory",
                kind: c.kind, role: c.role || null, edge: off.join("+"),
                text: c.text,
                rect: [Math.round(c.r.left), Math.round(c.r.top), Math.round(c.r.right), Math.round(c.r.bottom)],
              });
            }
          }

          // 1b) tiny_text — GPT-5 Step 3: эффективный font-size ниже floor для роли.
          // Floor берётся из MINS (CLI flags / tenant style_contract). Decor пропускаем.
          for (const T of texts) {
            if (T.decor || T.role === "decor" || T.ghost) continue;
            const floor = MINS[T.role];
            if (!floor) continue;  // нет порога для роли — пропуск
            if (T.fontPx > 0 && T.fontPx < floor) {
              // GPT-5 review: hero/heading/body/cta → blocking; caption → advisory.
              const sev = (T.role === "caption") ? "advisory" : "blocking";
              issues.push({
                type: "tiny_text", severity: sev, role: T.role, text: T.text,
                px: Math.round(T.fontPx * 10) / 10, min: floor,
                rect: [Math.round(T.r.left), Math.round(T.r.top), Math.round(T.r.right), Math.round(T.r.bottom)],
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
              // G2: оба фрагмента — части одного текст-блока (split-span'ы
              // заголовка/строки): межсловный/межстрочный зазор не дефект.
              if (A.blockRoot && A.blockRoot === B.blockRoot) continue;
              const a = A.r, b = B.r;

              const ix = Math.min(a.right, b.right) - Math.max(a.left, b.left);
              const g = Math.max(a.top, b.top) - Math.min(a.bottom, b.bottom);

              // H2: bbox в issue (для дедупа с координатами)
              const boxA = [Math.round(a.left), Math.round(a.top), Math.round(a.right), Math.round(a.bottom)];
              const boxB = [Math.round(b.left), Math.round(b.top), Math.round(b.right), Math.round(b.bottom)];

              if (g < 0 && ix > 0) {
                // overlap (любое реальное пересечение по обеим осям). GPT-5 Step 3:
                // severity hard, если area >= max(HARD.absPx, HARD.ratio × min(area_a, area_b)).
                const area = ix * (-g);
                const areaA = Math.max(1, a.width * a.height);
                const areaB = Math.max(1, b.width * b.height);
                const hardThreshold = Math.max(HARD.absPx, HARD.ratio * Math.min(areaA, areaB));
                const isHard = area >= hardThreshold;
                // blocking — только если ОБА текста — semantic role (не decor/caption)
                const semA = A.role && !["decor", "caption"].includes(A.role);
                const semB = B.role && !["decor", "caption"].includes(B.role);
                issues.push({
                  type: "overlap",
                  severity: (isHard && semA && semB) ? "blocking" : "advisory",
                  a: A.text, b: B.text, roleA: A.role || null, roleB: B.role || null,
                  overlapPx: Math.round(area),
                  hardThresholdPx: Math.round(hardThreshold),
                  boxA, boxB,
                });
              } else if (g >= 0 && ix > 20 && g < MIN_GAP) {
                // H1: если оба текста сидят внутри ОДНОЙ карточки/чарта —
                // это структурированная вёрстка (метки таблицы/чарта/легенды),
                // crowding не флагаем.
                if (A.ancestor && A.ancestor === B.ancestor) continue;
                const topFirst = a.top <= b.top;
                issues.push({
                  type: "crowding",
                  severity: "advisory",  // GPT-5 review: crowding оставить advisory (самый шумный)
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
        FRAME_W, FRAME_H, args.tol, args.minOverlap, args.minGap,
        { hero: args.heroMin, heading: args.headingMin, body: args.bodyMin,
          caption: args.captionMin, cta: args.ctaMin },
        { absPx: args.hardOverlapAbsPx, ratio: args.hardOverlapRatio }
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
        else if (is.type === "crowding") key = `cr|${[is.a, is.b].sort().join("|")}|${boxKey(is)}`;
        else if (is.type === "tiny_text") key = `tt|${is.role}|${is.text}|${is.rect.join(",")}`;
        else key = `${is.type}|${JSON.stringify(is)}`;
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
